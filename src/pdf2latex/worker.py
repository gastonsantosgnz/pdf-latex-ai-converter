"""Single-page conversion: render a PDF page to an image and ask the LLM for LaTeX.

Reusable functions (used by :mod:`pdf2latex.converter`) plus a small CLI so a
single page can be converted on its own for debugging.
"""

from __future__ import annotations

import argparse
import base64
import io
import os
import threading
import time
from dataclasses import dataclass

from .assemble import strip_code_fences
from .prompts import build_system_prompt, build_user_text

# PDFium is not thread-safe; serialize all rendering so concurrent workers cannot
# corrupt its state (which surfaced as random "Failed to load page/document").
_RENDER_LOCK = threading.Lock()


@dataclass
class PageResult:
    """Outcome of converting one page."""

    latex: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    finish_reason: str | None = None
    rejected: str | None = None  # set by repair_latex when the fix is unsafe to apply


def render_page_to_base64(pdf_path: str, page_index: int = 0, scale: float = 2.0) -> str:
    """Render one page of a PDF to a PNG and return it base64-encoded.

    ``page_index`` is 0-based. ``scale`` ~2.0 gives the model enough resolution
    to read small subscripts/exponents without huge payloads.
    """
    import pypdfium2 as pdfium

    with _RENDER_LOCK:
        pdf = pdfium.PdfDocument(pdf_path)
        try:
            page = pdf[page_index]
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil()
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("utf-8")
        finally:
            pdf.close()


def render_page_to_file(pdf_path: str, page_index: int, dest, scale: float = 2.0) -> None:
    """Render one page of a PDF to a PNG file (for engines that read from disk)."""
    import pypdfium2 as pdfium

    with _RENDER_LOCK:
        pdf = pdfium.PdfDocument(pdf_path)
        try:
            pdf[page_index].render(scale=scale).to_pil().save(str(dest))
        finally:
            pdf.close()


def _looks_blank(text: str) -> bool:
    t = (text or "").strip()
    if t.startswith("%"):
        t = t[1:].strip()
    return t.lower() in {"blank-page", "pagina-decorativa", "página-decorativa"}


def _is_photo_omitted(text: str) -> bool:
    t = (text or "").strip().lstrip("%").strip().lower()
    return "photo-omitted" in t or "fotografia-omitida" in t


def convert_image_b64(
    client,
    img_b64: str,
    *,
    model: str,
    max_tokens: int = 16384,
    max_blank_retries: int = 2,
    rate_limit_retries: int = 5,
) -> PageResult:
    """Convert a base64 PNG page image to LaTeX using an OpenAI vision model.

    Retries on rate limits (exponential backoff) and re-prompts a couple of
    times if the model wrongly claims the page is blank.
    """
    system_prompt = build_system_prompt()
    user_text = build_user_text()

    messages: list = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{img_b64}",
                        "detail": "high",
                    },
                },
                {"type": "text", "text": user_text},
            ],
        },
    ]

    for blank_round in range(max_blank_retries + 1):
        response = _create_with_backoff(
            client,
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            retries=rate_limit_retries,
        )
        choice = response.choices[0]
        latex = choice.message.content or ""
        finish_reason = getattr(choice, "finish_reason", None)

        if (
            _looks_blank(latex)
            and not _is_photo_omitted(latex)
            and blank_round < max_blank_retries
        ):
            messages.append({"role": "assistant", "content": latex})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "The image DOES contain text or math from a textbook. "
                        "Return the full LaTeX for everything legible. "
                        "Do not answer with only % blank-page."
                    ),
                }
            )
            continue

        usage = response.usage
        return PageResult(
            latex=latex,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            total_tokens=getattr(usage, "total_tokens", 0) or 0,
            finish_reason=finish_reason,
        )

    raise RuntimeError("worker: conversion loop produced no output")


def _max_token_run(text: str) -> int:
    """Length of the longest run of identical whitespace-separated tokens.

    A healthy page never repeats the same token dozens of times in a row; a
    runaway model output does (e.g. ``\\textbullet \\textbullet \\textbullet`` ...).
    """
    best = run = 0
    prev: str | None = None
    for tok in text.split():
        run = run + 1 if tok == prev else 1
        prev = tok
        if run > best:
            best = run
    return best


def assess_repair(original: str, repaired: str) -> str | None:
    """Return a reason the repair is UNSAFE to apply, or ``None`` when it is safe.

    The model-based repair occasionally returns structurally-valid but nonsense
    LaTeX that destroys the page (observed in the wild: a heading padded with
    thousands of ``\\textbullet``, or a ~2000-row empty ``array``). Those pass the
    brace/environment checks yet break the build worse than before and erase the
    real content. These cheap, deterministic guards catch the pathological cases
    while leaving genuine minimal fixes untouched.
    """
    from .validate import validate_text

    repaired = repaired.strip()
    if not repaired:
        return "the repair returned an empty page"
    before, after = len(original.strip()), len(repaired)
    # 1. Runaway growth — a real fix is small; a 1.5x (or +400 char) blow-up is not.
    if after > max(int(before * 1.5), before + 400):
        return (
            f"the repair ballooned the page ({before} to {after} characters); "
            "this looks like runaway output"
        )
    # 2. Content loss — dropping half a substantial page means material was erased.
    if before >= 200 and after < before * 0.5:
        return f"the repair dropped too much content ({before} to {after} characters)"
    # 3. Pathological repetition the repair introduced.
    run_after = _max_token_run(repaired)
    if run_after >= 40 and run_after > _max_token_run(original) * 3:
        return f"the repair introduced runaway repetition ({run_after}x a single token)"
    # 4. The repair must not add NEW structural breakage.
    if len(validate_text(repaired)) > len(validate_text(original)):
        return "the repair introduced new unbalanced braces or environments"
    return None


def repair_latex(
    client,
    latex: str,
    problems: list[str],
    *,
    model: str,
    max_tokens: int = 4096,
    rate_limit_retries: int = 5,
    guard: bool = True,
) -> PageResult:
    """Ask the model to minimally fix a page's LaTeX given the detected problems.

    Returns a :class:`PageResult` with the corrected LaTeX and token usage so the
    caller can account for the extra cost of the repair round-trip.
    """
    system_prompt = (
        "You fix LaTeX so it compiles with pdflatex, with MINIMAL edits. Return ONLY the "
        "corrected LaTeX for the page: no preamble, no \\documentclass, no Markdown code "
        "fences. Preserve ALL content and the original language; do not add or remove "
        "material. Common fixes: wrap any subscript/superscript (_ ^) in \\( \\); never nest "
        "align*/aligned/equation/array inside \\[ \\]; make the tabular/array column spec "
        "match the widest row (max & in a row + 1); put \\hline on its own line between rows; "
        "balance \\left with \\right; close every environment and balance every { }."
    )
    problem_list = "\n".join(f"- {p}" for p in problems) or "- (unspecified)"
    user_text = (
        "The following LaTeX page has these problems:\n"
        f"{problem_list}\n\n"
        "Return the corrected LaTeX for the whole page:\n\n"
        f"{latex}"
    )
    response = _create_with_backoff(
        client,
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        max_tokens=max_tokens,
        retries=rate_limit_retries,
    )
    choice = response.choices[0]
    usage = response.usage
    fixed = strip_code_fences(choice.message.content or "")
    rejected = assess_repair(latex, fixed) if guard else None
    return PageResult(
        latex=fixed,
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        total_tokens=getattr(usage, "total_tokens", 0) or 0,
        finish_reason=getattr(choice, "finish_reason", None),
        rejected=rejected,
    )


def repair_with_image(
    client,
    img_b64: str,
    latex: str,
    problems: list[str],
    *,
    model: str,
    max_tokens: int = 8192,
    rate_limit_retries: int = 5,
    guard: bool = True,
) -> PageResult:
    """Fix a page using BOTH its source image and the compile error.

    Stronger than the text-only :func:`repair_latex`: with the original page in
    view the model can reconstruct a broken table or rebuild math it could only
    guess at from the text. The same :func:`assess_repair` guard applies, so a
    runaway or destructive fix is flagged via ``PageResult.rejected`` instead of
    overwriting good content.
    """
    system_prompt = (
        "You are correcting one page of LaTeX transcribed from the textbook image "
        "shown. The current LaTeX fails to compile with pdflatex. Return ONLY the "
        "corrected LaTeX for this page: no preamble, no \\documentclass, no Markdown "
        "fences. Match the image faithfully and keep the original language. Make the "
        "tabular/array column spec equal the widest row (max & in a row + 1); never "
        "nest align*/aligned/array inside \\[ \\]; wrap every subscript/superscript "
        "(_ ^) in \\( \\); put \\hline on its own line; balance \\left/\\right; close "
        "every environment and balance every { }."
    )
    problem_list = "\n".join(f"- {p}" for p in problems) or "- (unspecified)"
    user_text = (
        "The current LaTeX for the page in the image has these problems:\n"
        f"{problem_list}\n\n"
        "Here is the current (broken) LaTeX. Return the corrected version of the "
        "whole page:\n\n"
        f"{latex}"
    )
    messages: list = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{img_b64}",
                        "detail": "high",
                    },
                },
                {"type": "text", "text": user_text},
            ],
        },
    ]
    response = _create_with_backoff(
        client,
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        retries=rate_limit_retries,
    )
    choice = response.choices[0]
    usage = response.usage
    fixed = strip_code_fences(choice.message.content or "")
    rejected = assess_repair(latex, fixed) if guard else None
    return PageResult(
        latex=fixed,
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        total_tokens=getattr(usage, "total_tokens", 0) or 0,
        finish_reason=getattr(choice, "finish_reason", None),
        rejected=rejected,
    )


def _create_with_backoff(client, *, model, messages, max_tokens, retries):
    # Newer models (gpt-5, o-series) use ``max_completion_tokens`` and reject the
    # old ``max_tokens``; some also reject a custom ``temperature``. Start with the
    # modern parameters and drop whatever a model rejects, then retry immediately.
    kwargs = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": max_tokens,
        "temperature": 0,
    }
    attempt = 0
    while True:
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            # Out of credit/quota is a 429 but waiting never helps: fail fast.
            if "insufficient_quota" in msg or "exceeded your current quota" in msg:
                raise RuntimeError(
                    "OpenAI quota exceeded: your account is out of credit. Add credits "
                    "at platform.openai.com (Billing), then try again."
                ) from exc
            if "unsupported" in msg or "not supported" in msg:
                if "temperature" in msg and "temperature" in kwargs:
                    del kwargs["temperature"]
                    continue
                if "max_completion_tokens" in msg and "max_completion_tokens" in kwargs:
                    del kwargs["max_completion_tokens"]
                    kwargs["max_tokens"] = max_tokens  # very old model
                    continue
            if "429" in msg or "rate_limit" in msg or "rate limit" in msg:
                if attempt >= retries - 1:
                    break
                wait = 10 * (2 ** attempt)
                print(f"Rate limit, waiting {wait}s (attempt {attempt + 1}/{retries})…", flush=True)
                time.sleep(wait)
                attempt += 1
                continue
            raise
    raise RuntimeError("worker: exceeded rate-limit retries")


def make_client(api_key: str | None = None):
    """Create an OpenAI client, loading the key from the environment if needed."""
    from openai import OpenAI

    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("OPENAI_API_KEY is not set (put it in .env or the environment).")
    return OpenAI(api_key=key)


def _cli() -> int:  # pragma: no cover - thin CLI glue, exercised by smoke job
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description="Convert a single PDF page to LaTeX.")
    parser.add_argument("--input", required=True, help="PDF file (page 1 is used unless --page).")
    parser.add_argument("--output", required=True, help="Destination .tex file.")
    parser.add_argument("--page", type=int, default=1, help="1-based page number (default 1).")
    parser.add_argument("--model", default=os.environ.get("PDF2LATEX_MODEL", "gpt-5"))
    parser.add_argument("--max-tokens", type=int, default=16384)
    args = parser.parse_args()

    client = make_client()
    img_b64 = render_page_to_base64(args.input, page_index=args.page - 1)
    result = convert_image_b64(client, img_b64, model=args.model, max_tokens=args.max_tokens)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result.latex, encoding="utf-8")
    if result.total_tokens:
        out.with_suffix(".usage.txt").write_text(
            f"model={args.model} prompt_tokens={result.prompt_tokens} "
            f"completion_tokens={result.completion_tokens} total_tokens={result.total_tokens}\n",
            encoding="utf-8",
        )
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
