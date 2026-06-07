"""Project paths and naming conventions.

Output layout for a source PDF named e.g. ``My Book.pdf``::

    output/My-Book/
    ├── pages/            page_0001.pdf, page_0001.tex, page_0001.usage.txt, ...
    ├── My-Book.tex             (monolithic assembly of all pages)
    ├── My-Book-standalone.tex  (compilable: preamble + title + chapters)
    ├── chapters/         chapter_01_*.tex, ...
    └── log.txt
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# Repo root = two levels up from this file (src/pdf2latex/layout.py).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCES_DIR = PROJECT_ROOT / "sources"
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _default_output_dir() -> Path:
    """Resolve where generated output should go.

    Defaults to ``<repo>/output`` but can be redirected anywhere on the machine
    by setting ``PDF2LATEX_OUTPUT_DIR`` (so test conversions never have to live
    inside the repository). ``~`` is expanded.
    """
    env = os.environ.get("PDF2LATEX_OUTPUT_DIR")
    return Path(env).expanduser() if env else PROJECT_ROOT / "output"


OUTPUT_DIR = _default_output_dir()


def slugify(name: str) -> str:
    """Turn a title/filename stem into a filesystem- and URL-friendly slug."""
    text = unicodedata.normalize("NFKD", name)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip()
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-") or "document"


@dataclass
class BookPaths:
    """Resolved paths for one source document."""

    slug: str
    source_pdf: Path
    out_dir: Path
    pages_dir: Path
    chapters_dir: Path
    monolith_tex: Path
    standalone_tex: Path
    log_file: Path

    @classmethod
    def for_source(cls, source_pdf: Path, output_root: Path = OUTPUT_DIR) -> BookPaths:
        slug = slugify(source_pdf.stem)
        out_dir = output_root / slug
        return cls(
            slug=slug,
            source_pdf=source_pdf,
            out_dir=out_dir,
            pages_dir=out_dir / "pages",
            chapters_dir=out_dir / "chapters",
            monolith_tex=out_dir / f"{slug}.tex",
            standalone_tex=out_dir / f"{slug}-standalone.tex",
            log_file=out_dir / "log.txt",
        )

    def ensure_dirs(self) -> None:
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        self.chapters_dir.mkdir(parents=True, exist_ok=True)

    def page_tex(self, page_num: int) -> Path:
        return self.pages_dir / f"page_{page_num:04d}.tex"


def resolve_source(name_or_path: str, sources_dir: Path = SOURCES_DIR) -> Path:
    """Resolve a PDF given a bare filename, a name in ``sources/`` or a full path."""
    p = Path(name_or_path)
    candidates = [p, sources_dir / name_or_path]
    if not p.suffix:
        candidates.append(sources_dir / f"{name_or_path}.pdf")
    for c in candidates:
        if c.is_file():
            return c.resolve()
    raise SystemExit(
        f"PDF not found: {name_or_path!r}. Place it in '{sources_dir}' or pass a full path."
    )
