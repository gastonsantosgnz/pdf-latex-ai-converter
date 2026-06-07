"""Assemble per-page .tex into a monolith and a compilable standalone document."""

from __future__ import annotations

import re
from pathlib import Path

from .layout import TEMPLATE_DIR, BookPaths

CHAPTERS_BEGIN = "% --- chapters begin (pdf2latex) ---"
CHAPTERS_END = "% --- chapters end (pdf2latex) ---"

_PAGE_RE = re.compile(r"page_(\d+)\.tex$")


def strip_code_fences(text: str) -> str:
    """Remove Markdown ``` fences the model sometimes wraps around output."""
    lines = text.splitlines()
    cleaned = [ln for ln in lines if not ln.strip().startswith("```")]
    return "\n".join(cleaned)


def iter_page_files(paths: BookPaths) -> list[tuple[int, Path]]:
    """Return (page_number, path) for every converted page, in order."""
    found: list[tuple[int, Path]] = []
    for tex in paths.pages_dir.glob("page_*.tex"):
        m = _PAGE_RE.search(tex.name)
        if m:
            found.append((int(m.group(1)), tex))
    found.sort(key=lambda t: t[0])
    return found


def assemble_monolith(paths: BookPaths) -> Path:
    """Concatenate all converted pages into ``<slug>.tex`` with page markers."""
    parts: list[str] = []
    for page_num, tex in iter_page_files(paths):
        body = strip_code_fences(tex.read_text(encoding="utf-8"))
        parts.append(f"% ===== Page {page_num} =====\n{body}")
    paths.monolith_tex.write_text("\n\n".join(parts), encoding="utf-8")
    return paths.monolith_tex


def _load_template() -> str:
    return (TEMPLATE_DIR / "standalone.tex").read_text(encoding="utf-8")


def _build_body(paths: BookPaths, chapters: list[tuple[str, str]] | None) -> str:
    """Body block: either chapter inputs or a single input of the monolith."""
    if chapters:
        lines = [CHAPTERS_BEGIN]
        for fname, title in chapters:
            lines.append(f"\\capitulo{{{title}}}")
            lines.append(f"\\input{{chapters/{fname}}}")
            lines.append("")
        lines.append(CHAPTERS_END)
        return "\n".join(lines)
    return f"{CHAPTERS_BEGIN}\n\\input{{{paths.monolith_tex.name}}}\n{CHAPTERS_END}"


def write_standalone(
    paths: BookPaths,
    *,
    title: str | None = None,
    subtitle: str | None = None,
    chapters: list[tuple[str, str]] | None = None,
) -> Path:
    """Write/refresh ``<slug>-standalone.tex``.

    If the file already exists, only the body (chapter list) is replaced so any
    manual preamble tweaks are preserved.
    """
    title = title or paths.slug.replace("-", " ")
    subtitle = subtitle or "LaTeX edition"
    body = _build_body(paths, chapters)

    if paths.standalone_tex.exists():
        content = paths.standalone_tex.read_text(encoding="utf-8")
        pattern = re.compile(
            re.escape(CHAPTERS_BEGIN) + r".*?" + re.escape(CHAPTERS_END),
            re.DOTALL,
        )
        if pattern.search(content):
            content = pattern.sub(lambda _m: body, content)
        else:
            content = content.replace("\\end{document}", body + "\n\n\\end{document}")
    else:
        content = (
            _load_template()
            .replace("@@TITLE@@", title)
            .replace("@@SUBTITLE@@", subtitle)
            .replace("@@BODY@@", body)
        )

    paths.standalone_tex.write_text(content, encoding="utf-8")
    return paths.standalone_tex
