"""Tests for range math and the convert orchestrator (LLM fully mocked)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from pdf2latex import converter
from pdf2latex.converter import convert_pdf, resolve_range
from pdf2latex.layout import BookPaths
from pdf2latex.worker import PageResult


# --------------------------------------------------------------------------- #
# resolve_range                                                               #
# --------------------------------------------------------------------------- #
def test_resolve_range_by_batch() -> None:
    assert resolve_range(250, batch=1, start=None, end=None, batch_size=100) == (1, 100)
    assert resolve_range(250, batch=3, start=None, end=None, batch_size=100) == (201, 250)


def test_resolve_range_explicit_and_defaults() -> None:
    assert resolve_range(20, batch=None, start=5, end=10, batch_size=100) == (5, 10)
    assert resolve_range(7, batch=None, start=None, end=None, batch_size=100) == (1, 7)
    # End beyond the document is clamped to the page count.
    assert resolve_range(7, batch=None, start=1, end=100, batch_size=100) == (1, 7)


def test_resolve_range_invalid_inputs() -> None:
    with pytest.raises(SystemExit):
        resolve_range(10, batch=0, start=None, end=None, batch_size=100)
    with pytest.raises(SystemExit):
        resolve_range(10, batch=None, start=5, end=3, batch_size=100)


# --------------------------------------------------------------------------- #
# convert_pdf (orchestrator)                                                  #
# --------------------------------------------------------------------------- #
def _make_pdf(path: Path, pages: int) -> Path:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    with path.open("wb") as fh:
        writer.write(fh)
    return path


@pytest.fixture
def out_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect convert_pdf's output away from the real ``output/`` dir."""
    root = tmp_path / "out"
    real_for_source = BookPaths.for_source
    monkeypatch.setattr(
        converter,
        "BookPaths",
        SimpleNamespace(for_source=lambda src: real_for_source(src, output_root=root)),
    )
    return root


def _stub_pipeline(monkeypatch: pytest.MonkeyPatch, convert_fn) -> None:
    monkeypatch.setattr(
        converter, "render_page_to_base64", lambda pdf, page_index=0, scale=2.0: f"img-{page_index}"
    )
    monkeypatch.setattr(converter, "make_client", lambda: object())
    monkeypatch.setattr(converter, "convert_image_b64", convert_fn)


def test_convert_pdf_happy_path(
    tmp_path: Path, out_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = _make_pdf(tmp_path / "book.pdf", pages=3)

    def fake_convert(client, img_b64, *, model, max_tokens, profile):
        return PageResult(latex="LATEX", total_tokens=3, finish_reason="stop")

    _stub_pipeline(monkeypatch, fake_convert)
    paths = convert_pdf(pdf, model="gpt-4o", sleep_s=0)

    monolith = paths.monolith_tex.read_text(encoding="utf-8")
    assert monolith.count("% ===== Page") == 3
    assert paths.standalone_tex.exists()
    assert paths.page_tex(1).read_text(encoding="utf-8") == "LATEX"


def test_convert_pdf_skips_already_converted(
    tmp_path: Path, out_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = _make_pdf(tmp_path / "book.pdf", pages=3)
    pre = BookPaths.for_source(pdf, output_root=out_root)
    pre.ensure_dirs()
    pre.page_tex(1).write_text("ALREADY", encoding="utf-8")

    converted: list[str] = []

    def fake_convert(client, img_b64, *, model, max_tokens, profile):
        converted.append(img_b64)
        return PageResult(latex="NEW", total_tokens=1)

    _stub_pipeline(monkeypatch, fake_convert)
    convert_pdf(pdf, model="gpt-4o", sleep_s=0)

    # Page 1 (img-0) is skipped; only pages 2 and 3 hit the model.
    assert converted == ["img-1", "img-2"]
    assert pre.page_tex(1).read_text(encoding="utf-8") == "ALREADY"


def test_convert_pdf_records_errors_and_continues(
    tmp_path: Path, out_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = _make_pdf(tmp_path / "book.pdf", pages=3)

    def fake_convert(client, img_b64, *, model, max_tokens, profile):
        if img_b64 == "img-1":  # page 2 fails
            raise ValueError("bad page")
        return PageResult(latex="OK", total_tokens=1)

    _stub_pipeline(monkeypatch, fake_convert)
    paths = convert_pdf(pdf, model="gpt-4o", sleep_s=0)

    assert (paths.pages_dir / "page_0002.err.txt").exists()
    assert paths.page_tex(1).exists()
    assert paths.page_tex(3).exists()
    assert not paths.page_tex(2).exists()


def test_convert_pdf_prints_next_batch_hint(
    tmp_path: Path, out_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    pdf = _make_pdf(tmp_path / "book.pdf", pages=3)

    def fake_convert(client, img_b64, *, model, max_tokens, profile):
        return PageResult(latex="OK", total_tokens=1)

    _stub_pipeline(monkeypatch, fake_convert)
    convert_pdf(pdf, model="gpt-4o", batch=1, batch_size=2, sleep_s=0)

    out = capsys.readouterr().out
    assert "Next batch" in out
