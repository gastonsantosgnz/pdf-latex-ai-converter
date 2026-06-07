"""Compile a standalone .tex into PDF using a local LaTeX engine (pdflatex)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


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
