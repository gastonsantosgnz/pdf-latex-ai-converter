"""Tests for path resolution and slug naming (pure, no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pdf2latex.layout import BookPaths, resolve_source, slugify


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("My Book", "My-Book"),
        ("Álgebra Básica", "Algebra-Basica"),  # accents are folded to ASCII
        ("a__b  c", "a-b-c"),  # underscores and runs of spaces collapse
        ("a---b", "a-b"),  # repeated hyphens collapse
        ("  spaced  ", "spaced"),  # leading/trailing whitespace stripped
        ("---", "document"),  # nothing left -> fallback
        ("", "document"),  # empty -> fallback
        ("Niño/Año*2", "NinoAno2"),  # punctuation dropped, letters kept
    ],
)
def test_slugify(name: str, expected: str) -> None:
    assert slugify(name) == expected


def test_bookpaths_for_source_layout(tmp_path: Path) -> None:
    pdf = tmp_path / "Mi Libro.pdf"
    paths = BookPaths.for_source(pdf, output_root=tmp_path / "out")

    assert paths.slug == "Mi-Libro"
    assert paths.out_dir == tmp_path / "out" / "Mi-Libro"
    assert paths.pages_dir == paths.out_dir / "pages"
    assert paths.chapters_dir == paths.out_dir / "chapters"
    assert paths.monolith_tex == paths.out_dir / "Mi-Libro.tex"
    assert paths.standalone_tex == paths.out_dir / "Mi-Libro-standalone.tex"
    assert paths.log_file == paths.out_dir / "log.txt"


def test_bookpaths_ensure_dirs_and_page_tex(tmp_path: Path) -> None:
    paths = BookPaths.for_source(tmp_path / "book.pdf", output_root=tmp_path / "out")
    paths.ensure_dirs()

    assert paths.pages_dir.is_dir()
    assert paths.chapters_dir.is_dir()
    assert paths.page_tex(7).name == "page_0007.tex"


def test_resolve_source_finds_in_sources_dir(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "doc.pdf").write_bytes(b"%PDF-1.4")

    # Bare name without extension resolves to <name>.pdf in sources/.
    assert resolve_source("doc", sources_dir=sources) == (sources / "doc.pdf").resolve()
    # Name with extension also resolves.
    assert resolve_source("doc.pdf", sources_dir=sources) == (sources / "doc.pdf").resolve()


def test_resolve_source_accepts_full_path(tmp_path: Path) -> None:
    pdf = tmp_path / "elsewhere" / "x.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(b"%PDF-1.4")
    assert resolve_source(str(pdf), sources_dir=tmp_path) == pdf.resolve()


def test_resolve_source_missing_raises_systemexit(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        resolve_source("nope", sources_dir=tmp_path)
