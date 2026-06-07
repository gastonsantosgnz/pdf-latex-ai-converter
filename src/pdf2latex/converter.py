"""Orchestrator: convert a whole PDF page by page, then assemble a monolith.

Runs in-process (no subprocess per page). Pages already converted are skipped,
so the job is resumable and can be done in batches to stay under rate limits.
"""

from __future__ import annotations

import time
from pathlib import Path

from pypdf import PdfReader

from .assemble import assemble_monolith, write_standalone
from .layout import BookPaths
from .worker import convert_image_b64, make_client, render_page_to_base64

DEFAULT_BATCH_SIZE = 100


def _log(paths: BookPaths, msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {msg}"
    print(line, flush=True)
    with paths.log_file.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


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
    sleep_s: float = 1.0,
    title: str | None = None,
    subtitle: str | None = None,
) -> BookPaths:
    """Convert ``source_pdf`` to per-page .tex files and (re)build the monolith.

    Returns the resolved :class:`BookPaths` so callers can keep working with it.
    """
    paths = BookPaths.for_source(source_pdf)
    paths.ensure_dirs()

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

    client = make_client()
    ok = failed = 0

    for page_num in range(start_page, end_page + 1):
        page_tex = paths.page_tex(page_num)
        if page_tex.exists() and page_tex.stat().st_size > 0:
            _log(paths, f"SKIP page {page_num} (already converted)")
            ok += 1
            continue

        try:
            time.sleep(sleep_s)
            img_b64 = render_page_to_base64(
                str(source_pdf), page_index=page_num - 1, scale=scale
            )
            result = convert_image_b64(
                client, img_b64, model=model, max_tokens=max_tokens, profile=profile
            )
            page_tex.write_text(result.latex, encoding="utf-8")
            if result.total_tokens:
                page_tex.with_suffix(".usage.txt").write_text(
                    f"model={model} prompt_tokens={result.prompt_tokens} "
                    f"completion_tokens={result.completion_tokens} "
                    f"total_tokens={result.total_tokens}\n",
                    encoding="utf-8",
                )
            ok += 1
            extra = "" if result.finish_reason in (None, "stop") else f" [{result.finish_reason}]"
            _log(paths, f"OK   page {page_num}{extra}")
        except Exception as exc:  # noqa: BLE001 - record and continue
            failed += 1
            paths.pages_dir.joinpath(f"page_{page_num:04d}.err.txt").write_text(
                repr(exc), encoding="utf-8"
            )
            _log(paths, f"ERR  page {page_num} -> {exc!r}")

    assemble_monolith(paths)
    write_standalone(paths, title=title, subtitle=subtitle)
    _log(paths, f"Done. OK={ok} FAIL={failed} | assembled -> {paths.monolith_tex.name}")

    if end_page < total:
        nxt = end_page + 1
        nbatch = (nxt - 1) // batch_size + 1
        print(
            f"\nNext batch: --batch {nbatch} "
            f"(or --start {nxt} --end {min(nxt + batch_size - 1, total)})",
            flush=True,
        )
    return paths
