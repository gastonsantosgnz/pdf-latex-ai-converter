"""Compile a standalone .tex into PDF using a local LaTeX engine (pdflatex)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .layout import BookPaths


def compile_pdf(
    standalone_tex: Path,
    *,
    engine: str = "pdflatex",
    runs: int = 2,
    halt_on_error: bool = True,
) -> Path:
    """Run the LaTeX engine ``runs`` times so the table of contents resolves.

    Requires a TeX distribution (TeX Live, MiKTeX, ...) on PATH. With
    ``halt_on_error=False`` the engine keeps going past errors and a PDF is
    returned as long as one was produced (best-effort, useful for
    machine-generated LaTeX that may have a few bad pages).
    """
    if shutil.which(engine) is None:
        raise SystemExit(
            f"'{engine}' not found on PATH. Install a LaTeX distribution "
            "(TeX Live / MiKTeX) or pass a different --engine."
        )
    if not standalone_tex.is_file():
        raise SystemExit(f"File not found: {standalone_tex}")

    workdir = standalone_tex.parent
    args = [engine, "-interaction=nonstopmode"]
    if halt_on_error:
        args.append("-halt-on-error")
    args.append(standalone_tex.name)

    last = None
    for i in range(runs):
        print(f"[{engine}] run {i + 1}/{runs} on {standalone_tex.name}", flush=True)
        # pdflatex output is not always valid UTF-8 (accents, fonts), so decode leniently.
        last = subprocess.run(
            args, cwd=workdir, capture_output=True, text=True, errors="replace"
        )
        if last.returncode != 0 and halt_on_error:
            tail = "\n".join((last.stdout or "").splitlines()[-25:])
            raise SystemExit(
                f"{engine} failed (run {i + 1}). Last log lines:\n{tail}\n"
                f"Full log: {standalone_tex.with_suffix('.log')}"
            )

    pdf = standalone_tex.with_suffix(".pdf")
    if not halt_on_error and not pdf.exists():
        tail = "\n".join((last.stdout or "").splitlines()[-25:]) if last else ""
        raise SystemExit(
            f"{engine} did not produce a PDF. Last log lines:\n{tail}\n"
            f"Full log: {standalone_tex.with_suffix('.log')}"
        )
    print(f"PDF ready: {pdf}")
    return pdf


def compile_errors_by_page(paths: BookPaths) -> list[dict]:
    """Parse a failed pdflatex log into ``[{page, error, snippet}]`` (one per page).

    Maps each ``l.<n>`` line number in the log back to the ``% ===== Page N =====``
    marker it falls under in the monolith, so a compile failure can be attributed
    to the page that caused it.
    """
    log = paths.standalone_tex.with_suffix(".log")
    if not log.exists() or not paths.monolith_tex.exists():
        return []
    log_lines = log.read_text("utf-8", errors="replace").splitlines()
    markers: list[tuple[int, int]] = []  # (1-based line in monolith, page number)
    for i, line in enumerate(
        paths.monolith_tex.read_text("utf-8", errors="replace").splitlines(), 1
    ):
        m = re.match(r"% ===== Page (\d+) =====", line.strip())
        if m:
            markers.append((i, int(m.group(1))))

    def page_of(line_no: int) -> int | None:
        return next((pg for mi, pg in reversed(markers) if mi <= line_no), None)

    by_page: dict[int, dict] = {}
    for i, line in enumerate(log_lines):
        if not line.startswith("! "):
            continue
        message = line[2:].strip()
        for j in range(i + 1, min(i + 8, len(log_lines))):
            m = re.match(r"l\.(\d+)(.*)", log_lines[j])
            if not m:
                continue
            page = page_of(int(m.group(1)))
            if page is None or page in by_page:
                break
            tail = log_lines[j + 1].strip() if j + 1 < len(log_lines) else ""
            by_page[page] = {
                "page": page,
                "error": message,
                "snippet": (m.group(2) + " " + tail).strip()[:120],
            }
            break
    return [by_page[p] for p in sorted(by_page)]
