"""Tests for the offline LaTeX validator and the needs-review report."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from pdf2latex import validate as validate_mod
from pdf2latex.layout import BookPaths
from pdf2latex.validate import (
    Issue,
    deep_check,
    deep_check_available,
    validate_existing,
    validate_pages,
    validate_text,
    write_needs_review,
)

# The same messy output used as a regression fixture for the worker.
KNOWN_BAD = (
    "\\begin{tabular}{cc}\n"
    "a & b & c \\\\\n"
    "\\end{tabular}\n"
    "\\begin{itemize}\n"
    "\\item x\n"
    "it cost $5"
)


def test_clean_page_has_no_issues() -> None:
    good = "\\section*{Hi}\n\\(x^2\\) costs \\$5 at 50\\% off.\n% a comment with { and $"
    assert validate_text(good) == []


def test_known_bad_page_is_flagged() -> None:
    issues = validate_text(KNOWN_BAD)
    kinds = {i.kind for i in issues}
    # Unclosed itemize and an odd, unescaped '$' are both detected offline.
    assert "environments" in kinds
    assert "math" in kinds


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("\\section*{Hi", "braces"),  # unclosed brace
        ("a } b", "braces"),  # stray closing brace
        ("\\begin{a}\\end{b}", "environments"),  # mismatched env
        ("\\begin{itemize}\\item x", "environments"),  # unclosed env
        ("\\end{itemize}", "environments"),  # end without begin
        ("it cost $5", "math"),  # odd unescaped $
    ],
)
def test_each_check_fires(text: str, kind: str) -> None:
    assert kind in {i.kind for i in validate_text(text)}


def test_even_dollars_and_escapes_are_allowed() -> None:
    assert validate_text("$a$ and $b$") == []  # balanced (even) is fine
    assert validate_text("price is \\$5") == []  # escaped currency is fine


def test_comments_are_ignored() -> None:
    assert validate_text("% $ unbalanced { brace \\begin{x}") == []


def _paths(tmp_path: Path) -> BookPaths:
    paths = BookPaths.for_source(tmp_path / "b.pdf", output_root=tmp_path / "out")
    paths.ensure_dirs()
    return paths


def test_validate_pages_and_existing(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.page_tex(1).write_text("\\section*{ok}", encoding="utf-8")
    paths.page_tex(2).write_text("\\begin{itemize}\\item x", encoding="utf-8")

    review = validate_pages(paths, [1, 2, 3])  # page 3 does not exist
    assert review[1] == []
    assert review[2]
    assert 3 not in review

    assert set(validate_existing(paths)) == {1, 2}


def test_write_needs_review_lists_only_failing_pages(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    review = {
        1: [],
        2: [Issue("math", "odd '$'")],
        5: [Issue("environments", "unclosed: itemize")],
    }
    report = write_needs_review(paths, review)

    assert report is not None and report.name == "needs-review.txt"
    text = report.read_text(encoding="utf-8")
    assert "Pages needing review: 2" in text
    assert "page_0002.tex" in text and "page_0005.tex" in text
    assert "page_0001.tex" not in text  # clean page omitted


def test_write_needs_review_removes_stale_report_when_clean(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    stale = paths.out_dir / "needs-review.txt"
    stale.write_text("old", encoding="utf-8")

    assert write_needs_review(paths, {1: [], 2: []}) is None
    assert not stale.exists()


def test_deep_check_parses_linter_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tex = tmp_path / "page_0001.tex"
    tex.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        validate_mod.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout="Warning 1\nWarning 2\n", stderr="", returncode=1),
    )
    issues = deep_check(tex)
    assert [i.message for i in issues] == ["Warning 1", "Warning 2"]
    assert all(i.kind == "deep" for i in issues)


def test_deep_check_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(validate_mod.shutil, "which", lambda _e: None)
    assert deep_check_available("chktex") is False
    monkeypatch.setattr(validate_mod.shutil, "which", lambda _e: "/usr/bin/chktex")
    assert deep_check_available("chktex") is True
