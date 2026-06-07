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
    profile: str = "default",
    max_blank_retries: int = 2,
    rate_limit_retries: int = 5,
) -> PageResult:
    """Convert a base64 PNG page image to LaTeX using an OpenAI vision model.

    Retries on rate limits (exponential backoff) and re-prompts a couple of
    times if the model wrongly claims the page is blank.
    """
    system_prompt = build_system_prompt(profile)
    user_text = build_user_text(profile)

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


def repair_latex(
    client,
    latex: str,
    problems: list[str],
    *,
    model: str,
    max_tokens: int = 4096,
    rate_limit_retries: int = 5,
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
    return PageResult(
        latex=strip_code_fences(choice.message.content or ""),
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        total_tokens=getattr(usage, "total_tokens", 0) or 0,
        finish_reason=getattr(choice, "finish_reason", None),
    )


def _create_with_backoff(client, *, model, messages, max_tokens, retries):
    for attempt in range(retries):
        try:
            return client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0,
            )
        except Exception as exc:  # noqa: BLE001 - retry only on rate limits
            msg = str(exc).lower()
            if "429" in msg or "rate_limit" in msg or "rate limit" in msg:
                wait = 10 * (2 ** attempt)
                print(f"Rate limit, waiting {wait}s (attempt {attempt + 1}/{retries})…", flush=True)
                time.sleep(wait)
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
    parser.add_argument("--model", default=os.environ.get("PDF2LATEX_MODEL", "gpt-4o"))
    parser.add_argument("--max-tokens", type=int, default=16384)
    parser.add_argument("--profile", choices=("default", "dense"), default="default")
    args = parser.parse_args()

    client = make_client()
    img_b64 = render_page_to_base64(args.input, page_index=args.page - 1)
    result = convert_image_b64(
        client, img_b64, model=args.model, max_tokens=args.max_tokens, profile=args.profile
    )

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
