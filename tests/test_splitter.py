"""Tests for chapter splitting against a synthetic monolith (pure, no network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf2latex.layout import BookPaths
from pdf2latex.splitter import split_auto, split_from_config

MONOLITH = """\
% ===== Page 1 =====
\\section*{Intro}
content-1
% ===== Page 2 =====
content-2
% ===== Page 3 =====
content-3
% ===== Page 4 =====
\\section*{Main}
content-4
% ===== Page 5 =====
content-5
% ===== Page 6 =====
content-6
"""


def _paths_with_monolith(tmp_path: Path, text: str) -> BookPaths:
    paths = BookPaths.for_source(tmp_path / "book.pdf", output_root=tmp_path / "out")
    paths.ensure_dirs()
    paths.monolith_tex.write_text(text, encoding="utf-8")
    return paths


def test_split_from_config_creates_chapter_files(tmp_path: Path) -> None:
    paths = _paths_with_monolith(tmp_path, MONOLITH)
    config = {
        "title": "Synthetic Book",
        "subtitle": "Edition",
        "chapters": [
            {"start_page": 1, "slug": "intro", "title": "1. Intro"},
            {"start_page": 4, "slug": "main", "title": "2. Main"},
        ],
    }
    config_path = tmp_path / "chapters.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    written = split_from_config(paths, config_path)

    assert written == [
        ("chapter_01_intro.tex", "1. Intro"),
        ("chapter_02_main.tex", "2. Main"),
    ]

    ch1 = (paths.chapters_dir / "chapter_01_intro.tex").read_text(encoding="utf-8")
    ch2 = (paths.chapters_dir / "chapter_02_main.tex").read_text(encoding="utf-8")

    # Chapter 1 owns pages 1-3, chapter 2 owns pages 4-6 (no leakage).
    assert "content-1" in ch1 and "content-3" in ch1
    assert "content-4" not in ch1
    assert "content-4" in ch2 and "content-6" in ch2
    assert "content-1" not in ch2

    # Standalone is rewritten to input the chapter files in order.
    standalone = paths.standalone_tex.read_text(encoding="utf-8")
    assert "\\input{chapters/chapter_01_intro.tex}" in standalone
    assert "\\input{chapters/chapter_02_main.tex}" in standalone
    assert "Synthetic Book" in standalone


def test_split_from_config_without_markers_raises(tmp_path: Path) -> None:
    paths = _paths_with_monolith(tmp_path, "no markers here\njust text\n")
    config_path = tmp_path / "chapters.json"
    config_path.write_text(
        json.dumps({"chapters": [{"start_page": 1, "title": "Only"}]}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        split_from_config(paths, config_path)


def test_split_from_config_empty_chapters_raises(tmp_path: Path) -> None:
    paths = _paths_with_monolith(tmp_path, MONOLITH)
    config_path = tmp_path / "chapters.json"
    config_path.write_text(json.dumps({"chapters": []}), encoding="utf-8")
    with pytest.raises(SystemExit):
        split_from_config(paths, config_path)


def test_split_auto_uses_blank_pages_as_separators(tmp_path: Path) -> None:
    monolith = """\
% ===== Page 1 =====
content-1
% ===== Page 2 =====
% blank-page
% ===== Page 3 =====
content-3
"""
    paths = _paths_with_monolith(tmp_path, monolith)
    written = split_auto(paths)

    # Page 1 starts chapter 1; the blank page 2 makes page 3 start chapter 2.
    assert [fname for fname, _ in written] == [
        "chapter_01.tex",
        "chapter_02.tex",
    ]
    ch2 = (paths.chapters_dir / "chapter_02.tex").read_text(encoding="utf-8")
    assert "content-3" in ch2
