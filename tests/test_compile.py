"""Tests for the pdflatex driver with the subprocess mocked (no LaTeX needed)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from pdf2latex import compile as compile_mod
from pdf2latex.compile import compile_pdf


def _tex(tmp_path: Path) -> Path:
    tex = tmp_path / "doc-standalone.tex"
    tex.write_text("\\documentclass{article}\\begin{document}x\\end{document}", encoding="utf-8")
    return tex


def test_compile_missing_engine_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compile_mod.shutil, "which", lambda _engine: None)
    with pytest.raises(SystemExit, match="not found on PATH"):
        compile_pdf(_tex(tmp_path))


def test_compile_missing_file_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compile_mod.shutil, "which", lambda _engine: "/usr/bin/pdflatex")
    with pytest.raises(SystemExit, match="File not found"):
        compile_pdf(tmp_path / "missing.tex")


def test_compile_runs_engine_n_times_and_returns_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(compile_mod.shutil, "which", lambda _engine: "/usr/bin/pdflatex")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(compile_mod.subprocess, "run", fake_run)
    tex = _tex(tmp_path)
    pdf = compile_pdf(tex, runs=2)

    assert pdf == tex.with_suffix(".pdf")
    assert len(calls) == 2  # two passes so the TOC resolves


def test_compile_failure_raises_with_log_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(compile_mod.shutil, "which", lambda _engine: "/usr/bin/pdflatex")
    monkeypatch.setattr(
        compile_mod.subprocess,
        "run",
        lambda cmd, **kwargs: SimpleNamespace(returncode=1, stdout="! LaTeX Error\n", stderr=""),
    )
    with pytest.raises(SystemExit, match="failed"):
        compile_pdf(_tex(tmp_path), runs=2)


def test_compile_lenient_returns_pdf_despite_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(compile_mod.shutil, "which", lambda _engine: "/usr/bin/pdflatex")
    tex = _tex(tmp_path)
    tex.with_suffix(".pdf").write_bytes(b"%PDF-1.4")  # pretend a PDF was produced
    monkeypatch.setattr(
        compile_mod.subprocess,
        "run",
        lambda cmd, **kwargs: SimpleNamespace(returncode=1, stdout="! LaTeX Error", stderr=""),
    )
    # halt_on_error=False: a PDF exists, so it is returned despite the error.
    assert compile_pdf(tex, runs=1, halt_on_error=False) == tex.with_suffix(".pdf")


def test_compile_lenient_raises_when_no_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(compile_mod.shutil, "which", lambda _engine: "/usr/bin/pdflatex")
    monkeypatch.setattr(
        compile_mod.subprocess,
        "run",
        lambda cmd, **kwargs: SimpleNamespace(returncode=1, stdout="! fatal", stderr=""),
    )
    with pytest.raises(SystemExit, match="did not produce a PDF"):
        compile_pdf(_tex(tmp_path), runs=1, halt_on_error=False)
