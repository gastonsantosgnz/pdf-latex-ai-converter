"""Orchestrator: convert a whole PDF page by page, then assemble a monolith.

Runs in-process (no subprocess per page). Pages already converted are skipped,
so the job is resumable and can be done in batches to stay under rate limits.
"""

from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from .assemble import assemble_monolith, write_standalone
from .layout import BookPaths
from .pricing import (
    DEFAULT_INPUT_TOKENS_PER_PAGE,
    DEFAULT_OUTPUT_TOKENS_PER_PAGE,
    DISCLAIMER,
    FALLBACK_MODEL,
    cost_for_tokens,
    estimate_cost,
    load_prices,
)
from .progress import ProgressBar
from .ratelimit import RateLimiter
from .validate import validate_pages, validate_text, write_needs_review
from .worker import convert_image_b64, make_client, render_page_to_base64, repair_latex

DEFAULT_BATCH_SIZE = 100
DEFAULT_WORKERS = 4

# Guards the shared log so concurrent worker threads never interleave a line.
_LOG_LOCK = threading.Lock()


@dataclass(frozen=True)
class _Outcome:
    """Result of converting (and optionally repairing) a single page."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    finish_reason: str | None
    repaired: bool


def _log(paths: BookPaths, msg: str, *, console: bool = True) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {msg}"
    with _LOG_LOCK:
        if console:
            print(line, flush=True)
        with paths.log_file.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def _stdin_isatty() -> bool:
    """Whether we can prompt the user (false in CI, pipes and test runs)."""
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _pending_pages(paths: BookPaths, start_page: int, end_page: int) -> list[int]:
    """Pages in the range that still need converting (resumable: skip done ones)."""
    pending: list[int] = []
    for page_num in range(start_page, end_page + 1):
        tex = paths.page_tex(page_num)
        if tex.exists() and tex.stat().st_size > 0:
            continue
        pending.append(page_num)
    return pending


def _log_preflight(
    paths: BookPaths,
    *,
    source_pdf: Path,
    total: int,
    start_page: int,
    end_page: int,
    pending: list[int],
    model: str,
    profile: str,
    scale: float,
    workers: int,
    rpm: float | None,
    tpm: float | None,
    prices: dict[str, tuple[float, float]],
) -> None:
    """Print and log a pre-flight summary, including an approximate cost range."""
    in_range = end_page - start_page + 1
    already = in_range - len(pending)
    limits = [f"{name}={val:g}" for name, val in (("rpm", rpm), ("tpm", tpm)) if val]
    rate = f"   Rate limit: {', '.join(limits)}" if limits else ""
    _log(paths, "Pre-flight summary:")
    _log(paths, f"  PDF      : {source_pdf.name} ({total} pages total)")
    _log(
        paths,
        f"  Range    : {start_page}-{end_page} "
        f"({in_range} in range, {already} already done, {len(pending)} to convert)",
    )
    _log(paths, f"  Model    : {model}   Profile: {profile}   Scale: {scale}")
    _log(paths, f"  Workers  : {max(1, workers)}{rate}")
    if pending:
        est = estimate_cost(model, len(pending), prices=prices)
        warn = "" if est.known_model else f"  [unknown model, priced as {FALLBACK_MODEL}]"
        _log(paths, f"  Est. cost: ${est.usd_low:,.2f} - ${est.usd_high:,.2f}{warn}")
        _log(paths, f"  Note     : {DISCLAIMER}")


def resolve_range(
    total: int,
    *,
    batch: int | None,
    start: int | None,
    end: int | None,
    batch_size: int,
) -> tuple[int, int]:
    """Compute the 1-based inclusive page range to process."""
    if batch is not None:
        if batch < 1:
            raise SystemExit("--batch must be >= 1")
        s = (batch - 1) * batch_size + 1
        e = min(batch * batch_size, total)
        return s, e
    s = start or 1
    e = end or total
    if s < 1 or s > e:
        raise SystemExit(f"Invalid range: {s}-{e}")
    return s, min(e, total)


def convert_pdf(
    source_pdf: Path,
    *,
    model: str,
    profile: str = "default",
    max_tokens: int = 16384,
    batch: int | None = None,
    start: int | None = None,
    end: int | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    scale: float = 2.0,
    workers: int = DEFAULT_WORKERS,
    rpm: float | None = None,
    tpm: float | None = None,
    title: str | None = None,
    subtitle: str | None = None,
    dry_run: bool = False,
    assume_yes: bool = False,
    repair: bool = False,
    repair_retries: int = 1,
) -> BookPaths:
    """Convert ``source_pdf`` to per-page .tex files and (re)build the monolith.

    A pre-flight summary (page count, model, profile and an approximate cost) is
    always shown first. With ``dry_run`` the pending pages are render-validated
    and the plan is printed without any API call. Without ``assume_yes`` an
    interactive confirmation is required before spending on the API.

    Pages are converted concurrently with up to ``workers`` threads (the job
    stays resumable, so already-converted pages are skipped). An optional
    :class:`~pdf2latex.ratelimit.RateLimiter` (``rpm``/``tpm``) paces the API
    calls; ``workers=1`` reproduces the strictly sequential behaviour.

    Every converted page is checked by the offline LaTeX validator. With
    ``repair`` enabled, a failing page is sent back to the model for a minimal
    fix (up to ``repair_retries`` times). A ``needs-review.txt`` report lists any
    pages that still fail after the run.

    Returns the resolved :class:`BookPaths` so callers can keep working with it.
    """
    paths = BookPaths.for_source(source_pdf)
    paths.ensure_dirs()
    prices = load_prices()

    reader = PdfReader(str(source_pdf))
    total = len(reader.pages)
    start_page, end_page = resolve_range(
        total, batch=batch, start=start, end=end, batch_size=batch_size
    )

    _log(
        paths,
        f"--- session | PDF: {source_pdf.name} | model: {model} | profile: {profile} "
        f"| pages {start_page}-{end_page} of {total} ---",
    )

    pending = _pending_pages(paths, start_page, end_page)
    _log_preflight(
        paths,
        source_pdf=source_pdf,
        total=total,
        start_page=start_page,
        end_page=end_page,
        pending=pending,
        model=model,
        profile=profile,
        scale=scale,
        workers=workers,
        rpm=rpm,
        tpm=tpm,
        prices=prices,
    )

    if dry_run:
        rendered = render_failed = 0
        for page_num in pending:
            try:
                render_page_to_base64(str(source_pdf), page_index=page_num - 1, scale=scale)
                rendered += 1
            except Exception as exc:  # noqa: BLE001 - report and continue
                render_failed += 1
                _log(paths, f"DRY  page {page_num} render FAILED -> {exc!r}")
        suffix = f", {render_failed} failed" if render_failed else ""
        _log(
            paths,
            f"DRY RUN complete: {rendered}/{len(pending)} pending page(s) render OK"
            f"{suffix}. No API calls were made.",
        )
        return paths

    if pending and not assume_yes:
        if not _stdin_isatty():
            _log(
                paths,
                "Refusing to start: not an interactive terminal and --yes not set. "
                "No API calls made.",
            )
            return paths
        answer = input(f"Proceed converting {len(pending)} page(s) with {model}? [y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            _log(paths, "Aborted by user. No API calls made.")
            return paths

    client = make_client()
    limiter = RateLimiter(rpm=rpm, tpm=tpm)
    est_tokens = DEFAULT_INPUT_TOKENS_PER_PAGE + DEFAULT_OUTPUT_TOKENS_PER_PAGE

    ok = failed = 0
    tot_prompt = tot_completion = tot_tokens = 0
    bar = ProgressBar(end_page - start_page + 1)
    quiet = bar.enabled  # when the live bar is on, keep per-page lines out of stdout

    def _tally_note() -> str:
        usd = cost_for_tokens(model, tot_prompt, tot_completion, prices=prices)
        return f"{tot_tokens:,} tok | ~${usd:,.2f}"

    def _convert_one(page_num: int) -> _Outcome:
        """Convert (and optionally auto-repair) one page in a worker thread."""
        limiter.acquire(est_tokens)
        img_b64 = render_page_to_base64(str(source_pdf), page_index=page_num - 1, scale=scale)
        result = convert_image_b64(
            client, img_b64, model=model, max_tokens=max_tokens, profile=profile
        )
        latex = result.latex
        pt, ct, tt = result.prompt_tokens, result.completion_tokens, result.total_tokens

        issues = validate_text(latex)
        attempts = 0
        while repair and issues and attempts < repair_retries:
            limiter.acquire(est_tokens)
            fix = repair_latex(
                client, latex, [str(i) for i in issues], model=model, max_tokens=max_tokens
            )
            latex = fix.latex
            pt += fix.prompt_tokens
            ct += fix.completion_tokens
            tt += fix.total_tokens
            issues = validate_text(latex)
            attempts += 1

        page_tex = paths.page_tex(page_num)
        page_tex.write_text(latex, encoding="utf-8")
        if tt:
            page_tex.with_suffix(".usage.txt").write_text(
                f"model={model} prompt_tokens={pt} completion_tokens={ct} total_tokens={tt}\n",
                encoding="utf-8",
            )
        return _Outcome(pt, ct, tt, result.finish_reason, repaired=attempts > 0)

    pending_set = set(pending)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {page_num: pool.submit(_convert_one, page_num) for page_num in pending}
        # Consume in page order: counters/logs stay deterministic even when
        # workers > 1, while the conversions themselves run concurrently.
        for page_num in range(start_page, end_page + 1):
            if page_num not in pending_set:
                _log(paths, f"SKIP page {page_num} (already converted)", console=not quiet)
                ok += 1
                bar.update(advance=1, note=_tally_note())
                continue
            try:
                outcome = futures[page_num].result()
                tot_prompt += outcome.prompt_tokens
                tot_completion += outcome.completion_tokens
                tot_tokens += outcome.total_tokens
                ok += 1
                extra = "" if outcome.finish_reason in (None, "stop") else f" [{outcome.finish_reason}]"
                if outcome.repaired:
                    extra += " [repaired]"
                _log(paths, f"OK   page {page_num}{extra}", console=not quiet)
            except Exception as exc:  # noqa: BLE001 - record and continue
                failed += 1
                paths.pages_dir.joinpath(f"page_{page_num:04d}.err.txt").write_text(
                    repr(exc), encoding="utf-8"
                )
                _log(paths, f"ERR  page {page_num} -> {exc!r}", console=not quiet)
            bar.update(advance=1, note=_tally_note())

    bar.close()

    assemble_monolith(paths)
    write_standalone(paths, title=title, subtitle=subtitle)

    review = validate_pages(paths, range(start_page, end_page + 1))
    report = write_needs_review(paths, review)
    n_review = sum(1 for issues in review.values() if issues)

    actual = cost_for_tokens(model, tot_prompt, tot_completion, prices=prices)
    _log(
        paths,
        f"Done. OK={ok} FAIL={failed} | tokens={tot_tokens:,} "
        f"(prompt={tot_prompt:,}, completion={tot_completion:,}) | "
        f"actual cost ~${actual:,.2f} | needs-review={n_review} | "
        f"assembled -> {paths.monolith_tex.name}",
    )
    if report is not None:
        _log(paths, f"Pages needing manual review are listed in {report.name}")

    if end_page < total:
        nxt = end_page + 1
        nbatch = (nxt - 1) // batch_size + 1
        print(
            f"\nNext batch: --batch {nbatch} "
            f"(or --start {nxt} --end {min(nxt + batch_size - 1, total)})",
            flush=True,
        )
    return paths
