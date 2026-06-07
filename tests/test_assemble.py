"""Tests for monolith/standalone assembly (pure, no network)."""

from __future__ import annotations

from pathlib import Path

from pdf2latex.assemble import (
    CHAPTERS_BEGIN,
    CHAPTERS_END,
    assemble_monolith,
    iter_page_files,
    strip_code_fences,
    write_standalone,
)
from pdf2latex.layout import BookPaths


def _paths(tmp_path: Path) -> BookPaths:
    paths = BookPaths.for_source(tmp_path / "book.pdf", output_root=tmp_path / "out")
    paths.ensure_dirs()
    return paths


def test_strip_code_fences_removes_backtick_lines() -> None:
    raw = "```latex\n\\section*{Hi}\ntext\n```"
    cleaned = strip_code_fences(raw)
    assert "```" not in cleaned
    assert "\\section*{Hi}" in cleaned
    assert "text" in cleaned


def test_iter_page_files_is_numeric_not_lexicographic(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    # Create pages out of order, including the classic 2-vs-10 trap.
    for n in (10, 2, 1):
        paths.page_tex(n).write_text(f"page {n}", encoding="utf-8")

    order = [num for num, _ in iter_page_files(paths)]
    assert order == [1, 2, 10]


def test_assemble_monolith_writes_page_markers(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.page_tex(1).write_text("```\nAAA\n```", encoding="utf-8")
    paths.page_tex(2).write_text("BBB", encoding="utf-8")

    out = assemble_monolith(paths)
    content = out.read_text(encoding="utf-8")

    assert "% ===== Page 1 =====" in content
    assert "% ===== Page 2 =====" in content
    assert "AAA" in content and "BBB" in content
    assert "```" not in content  # fences stripped during assembly


def test_write_standalone_from_template(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.page_tex(1).write_text("AAA", encoding="utf-8")
    assemble_monolith(paths)

    out = write_standalone(paths, title="My Title", subtitle="My Subtitle")
    content = out.read_text(encoding="utf-8")

    assert "My Title" in content
    assert "My Subtitle" in content
    assert CHAPTERS_BEGIN in content and CHAPTERS_END in content
    assert "\\input{book.tex}" in content


def test_write_standalone_replaces_only_chapter_block(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.page_tex(1).write_text("AAA", encoding="utf-8")
    assemble_monolith(paths)
    write_standalone(paths, title="Keep Me")

    # A manual preamble tweak must survive a second write_standalone call.
    content = paths.standalone_tex.read_text(encoding="utf-8")
    content = content.replace("\\begin{document}", "% MANUAL TWEAK\n\\begin{document}")
    paths.standalone_tex.write_text(content, encoding="utf-8")

    chapters = [("chapter_01_intro.tex", "1. Intro")]
    write_standalone(paths, chapters=chapters)
    updated = paths.standalone_tex.read_text(encoding="utf-8")

    assert "% MANUAL TWEAK" in updated  # preamble preserved
    assert "\\capitulo{1. Intro}" in updated
    assert "\\input{chapters/chapter_01_intro.tex}" in updated
    assert "Keep Me" in updated  # title block untouched
