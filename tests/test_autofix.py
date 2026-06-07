"""Tests for the compile-driven auto-repair loop, fully offline.

The LaTeX engine, the page renderer and the model are all injected, so the loop
runs deterministically without pdflatex or any network call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pdf2latex import autofix as af
from pdf2latex.layout import BookPaths
from pdf2latex.worker import PageResult


def _book(tmp_path: Path, pages: dict[int, str]) -> tuple[Path, BookPaths]:
    pdf = tmp_path / "book.pdf"
    paths = BookPaths.for_source(pdf, output_root=tmp_path)
    paths.pages_dir.mkdir(parents=True, exist_ok=True)
    for n, text in pages.items():
        paths.page_tex(n).write_text(text, encoding="utf-8")
    return pdf, paths


def _wire(monkeypatch: pytest.MonkeyPatch, paths: BookPaths):
    """A page is 'broken' while its file still contains the token BROKEN."""

    def fake_errors(p: BookPaths) -> list[dict]:
        out = []
        for tex in sorted(p.pages_dir.glob("page_*.tex")):
            if "BROKEN" in tex.read_text("utf-8"):
                out.append({"page": int(tex.stem.split("_")[1]), "error": "boom", "snippet": ""})
        return out

    monkeypatch.setattr(af, "compile_errors_by_page", fake_errors)
    monkeypatch.setattr(af, "assemble_monolith", lambda _p: None)
    monkeypatch.setattr(af, "write_standalone", lambda _p: None)
    monkeypatch.setattr(af, "make_client", lambda: object())

    def fake_compile(standalone: Path, *, halt_on_error: bool = True) -> Path:
        if fake_errors(paths):
            raise SystemExit("still broken")
        pdf = standalone.with_suffix(".pdf")
        pdf.write_bytes(b"%PDF-1.4")
        return pdf

    return fake_errors, fake_compile


def test_autofix_book_fixes_until_it_compiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf, paths = _book(tmp_path, {1: "BROKEN uno", 2: "BROKEN dos"})
    _errors, fake_compile = _wire(monkeypatch, paths)

    def repair(client, img, latex, problems, *, model):
        return PageResult(latex=latex.replace("BROKEN", "fixed"), total_tokens=5)

    res = af.autofix_book(
        pdf,
        model="gpt-4o",
        output_root=tmp_path,
        compile_fn=fake_compile,
        render_fn=lambda *a, **k: "IMG",
        repair_fn=repair,
    )

    assert res.ok
    assert res.fixed == [1, 2]
    assert res.stuck == []
    assert res.skipped == []
    assert res.total_tokens == 10


def test_autofix_book_reports_skipped_and_stuck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf, paths = _book(tmp_path, {1: "BROKEN uno"})
    _errors, fake_compile = _wire(monkeypatch, paths)

    def repair(client, img, latex, problems, *, model):
        # The guard rejected the fix: page is left untouched, so it stays broken.
        return PageResult(latex="garbage", total_tokens=3, rejected="ballooned the page")

    res = af.autofix_book(
        pdf,
        model="gpt-4o",
        output_root=tmp_path,
        compile_fn=fake_compile,
        render_fn=lambda *a, **k: "IMG",
        repair_fn=repair,
    )

    assert not res.ok
    assert res.skipped == [1]
    assert res.stuck == [1]
    assert res.fixed == []
    # The original page was never overwritten by the rejected fix.
    assert paths.page_tex(1).read_text("utf-8") == "BROKEN uno"


def test_autofix_book_stops_after_max_tries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf, paths = _book(tmp_path, {1: "BROKEN uno"})
    _errors, fake_compile = _wire(monkeypatch, paths)
    calls = {"n": 0}

    def repair(client, img, latex, problems, *, model):
        calls["n"] += 1
        return PageResult(latex="BROKEN still", total_tokens=1)  # applied but never fixes it

    res = af.autofix_book(
        pdf,
        model="gpt-4o",
        output_root=tmp_path,
        max_rounds=10,
        max_tries_per_page=2,
        compile_fn=fake_compile,
        render_fn=lambda *a, **k: "IMG",
        repair_fn=repair,
    )

    assert not res.ok
    assert res.stuck == [1]
    assert calls["n"] == 2  # bounded by max_tries_per_page, not max_rounds
