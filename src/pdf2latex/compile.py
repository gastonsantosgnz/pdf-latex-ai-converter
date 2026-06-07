"""Compile a standalone .tex into PDF using a local LaTeX engine (pdflatex)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def compile_pdf(standalone_tex: Path, *, engine: str = "pdflatex", runs: int = 2) -> Path:
    """Run the LaTeX engine ``runs`` times so the table of contents resolves.

    Requires a TeX distribution (TeX Live, MiKTeX, ...) on PATH.
    """
    if shutil.which(engine) is None:
        raise SystemExit(
            f"'{engine}' not found on PATH. Install a LaTeX distribution "
            "(TeX Live / MiKTeX) or pass a different --engine."
        )
    if not standalone_tex.is_file():
        raise SystemExit(f"File not found: {standalone_tex}")

    workdir = standalone_tex.parent
    for i in range(runs):
        print(f"[{engine}] run {i + 1}/{runs} on {standalone_tex.name}", flush=True)
        proc = subprocess.run(
            [engine, "-interaction=nonstopmode", "-halt-on-error", standalone_tex.name],
            cwd=workdir,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            tail = "\n".join((proc.stdout or "").splitlines()[-25:])
            raise SystemExit(
                f"{engine} failed (run {i + 1}). Last log lines:\n{tail}\n"
                f"Full log: {standalone_tex.with_suffix('.log')}"
            )

    pdf = standalone_tex.with_suffix(".pdf")
    print(f"PDF ready: {pdf}")
    return pdf
