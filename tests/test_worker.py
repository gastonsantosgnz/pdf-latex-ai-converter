"""Tests for single-page conversion with a fully mocked OpenAI client.

No real network calls are made: a ``FakeClient`` stands in for the OpenAI SDK,
so we exercise the retry/usage logic deterministically and offline.
"""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from pdf2latex import worker
from pdf2latex.worker import (
    PageResult,
    _max_token_run,
    assess_repair,
    convert_image_b64,
    make_client,
    render_page_to_base64,
    repair_latex,
)


class _Usage:
    def __init__(self, prompt: int, completion: int, total: int) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = total


def _response(content: str, *, finish_reason: str = "stop", usage=(10, 20, 30)):
    choice = SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=_Usage(*usage))


class _FakeCompletions:
    def __init__(self, responses) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    """Minimal stand-in for ``openai.OpenAI`` returning scripted responses."""

    def __init__(self, responses) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(responses))

    @property
    def calls(self) -> list[dict]:
        return self.chat.completions.calls


def test_pageresult_defaults() -> None:
    res = PageResult(latex="x")
    assert res.prompt_tokens == 0
    assert res.completion_tokens == 0
    assert res.total_tokens == 0
    assert res.finish_reason is None


def test_convert_normal_output_returns_tokens_and_finish_reason() -> None:
    client = FakeClient([_response("\\section*{Hi}", usage=(5, 7, 12))])
    res = convert_image_b64(client, "imgb64", model="gpt-4o")
    assert res.latex == "\\section*{Hi}"
    assert (res.prompt_tokens, res.completion_tokens, res.total_tokens) == (5, 7, 12)
    assert res.finish_reason == "stop"
    assert len(client.calls) == 1


def test_convert_returns_bad_model_output_verbatim() -> None:
    """Regression: messy model output (mismatched tabular, unclosed itemize,
    unwrapped ``$``) is passed through untouched; sanitizing is the user's job."""
    bad = (
        "\\begin{tabular}{cc}\n"  # spec declares 2 cols but the row has 3
        "a & b & c \\\\\n"
        "\\end{tabular}\n"
        "\\begin{itemize}\n"  # never closed
        "\\item x\n"
        "it cost $5"  # currency not escaped
    )
    client = FakeClient([_response(bad)])
    res = convert_image_b64(client, "x", model="gpt-4o")
    assert res.latex == bad


def test_convert_retries_when_model_claims_blank() -> None:
    client = FakeClient([_response("% blank-page"), _response("real content", usage=(1, 2, 3))])
    res = convert_image_b64(client, "x", model="gpt-4o")
    assert res.latex == "real content"
    assert len(client.calls) == 2  # one retry happened


def test_convert_gives_up_after_max_blank_retries() -> None:
    client = FakeClient([_response("% blank-page"), _response("% blank-page")])
    res = convert_image_b64(client, "x", model="gpt-4o", max_blank_retries=1)
    assert res.latex.strip() == "% blank-page"
    assert len(client.calls) == 2


def test_convert_retries_on_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker.time, "sleep", lambda *_: None)
    client = FakeClient([Exception("Error code: 429 rate_limit_exceeded"), _response("ok")])
    res = convert_image_b64(client, "x", model="gpt-4o")
    assert res.latex == "ok"
    assert len(client.calls) == 2


def test_convert_reraises_non_rate_limit_errors() -> None:
    client = FakeClient([ValueError("boom")])
    with pytest.raises(ValueError, match="boom"):
        convert_image_b64(client, "x", model="gpt-4o")


def test_create_with_backoff_exhausts_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker.time, "sleep", lambda *_: None)
    client = FakeClient([Exception("429")] * 3)
    with pytest.raises(RuntimeError, match="rate-limit retries"):
        worker._create_with_backoff(
            client, model="m", messages=[], max_tokens=1, retries=3
        )


@pytest.mark.parametrize(
    ("text", "blank"),
    [
        ("% blank-page", True),
        ("%pagina-decorativa", True),
        ("página-decorativa", True),
        ("\\section*{Hi}", False),
        ("", False),
    ],
)
def test_looks_blank(text: str, blank: bool) -> None:
    assert worker._looks_blank(text) is blank


@pytest.mark.parametrize(
    ("text", "photo"),
    [
        ("% photo-omitted", True),
        ("fotografia-omitida", True),
        ("real content", False),
    ],
)
def test_is_photo_omitted(text: str, photo: bool) -> None:
    assert worker._is_photo_omitted(text) is photo


def test_make_client_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        make_client()


def test_make_client_with_explicit_key() -> None:
    client = make_client(api_key="sk-explicit-test")
    assert client is not None
    assert hasattr(client, "chat")


def test_repair_latex_fixes_and_strips_fences() -> None:
    fixed = "```latex\n\\begin{itemize}\\item x\\end{itemize}\n```"
    client = FakeClient([_response(fixed, usage=(3, 4, 7))])
    res = repair_latex(client, "\\begin{itemize}\\item x", ["unclosed itemize"], model="gpt-4o")

    assert "```" not in res.latex
    assert "\\end{itemize}" in res.latex
    assert res.total_tokens == 7
    # The detected problem is handed to the model in the prompt.
    sent = client.calls[0]["messages"][1]["content"]
    assert "unclosed itemize" in sent


def test_max_token_run_counts_longest_repeat() -> None:
    assert _max_token_run("") == 0
    assert _max_token_run("a b c") == 1
    assert _max_token_run("x x x y") == 3
    assert _max_token_run("\\textbullet " * 200) == 200


def test_assess_repair_accepts_a_minimal_fix() -> None:
    original = "\\begin{itemize}\\item uno \\item dos"
    repaired = "\\begin{itemize}\\item uno \\item dos\\end{itemize}"
    assert assess_repair(original, repaired) is None


def test_assess_repair_rejects_empty() -> None:
    assert "empty" in assess_repair("some real content here", "   ")


def test_assess_repair_rejects_runaway_growth() -> None:
    original = "\\section*{2} texto breve"
    repaired = "\\section*{2 " + "\\textbullet " * 4000 + "}"
    reason = assess_repair(original, repaired)
    assert reason and ("balloon" in reason or "repetition" in reason)


def test_assess_repair_rejects_content_loss() -> None:
    original = "x" * 600
    repaired = "x" * 100
    assert "dropped" in assess_repair(original, repaired)


def test_assess_repair_rejects_new_structural_breakage() -> None:
    original = "balanced \\textbf{ok} content padded out to a decent length here"
    repaired = "balanced \\textbf{ok content padded out to a decent length here"  # missing }
    assert "unbalanced" in assess_repair(original, repaired)


def test_repair_latex_flags_unsafe_fix_and_can_be_disabled() -> None:
    runaway = "\\section*{" + "\\textbullet " * 5000 + "}"
    client = FakeClient([_response(runaway, usage=(3, 4, 7))])
    res = repair_latex(client, "\\section*{2} corto", ["bad"], model="gpt-4o")
    assert res.rejected  # guard catches the runaway

    client2 = FakeClient([_response(runaway, usage=(3, 4, 7))])
    res2 = repair_latex(client2, "\\section*{2} corto", ["bad"], model="gpt-4o", guard=False)
    assert res2.rejected is None  # guard can be turned off


def test_render_page_to_base64_produces_png(tmp_path: Path) -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    pdf_path = tmp_path / "one.pdf"
    with pdf_path.open("wb") as fh:
        writer.write(fh)

    b64 = render_page_to_base64(str(pdf_path), page_index=0, scale=1.0)
    assert isinstance(b64, str) and b64

    raw = base64.b64decode(b64)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic number


def test_render_is_thread_safe(tmp_path: Path) -> None:
    """Concurrent rendering must not fail: PDFium is serialized behind a lock."""
    from concurrent.futures import ThreadPoolExecutor

    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(16):
        writer.add_blank_page(width=200, height=260)
    pdf_path = tmp_path / "multi.pdf"
    with pdf_path.open("wb") as fh:
        writer.write(fh)

    def _render(i: int) -> str:
        return render_page_to_base64(str(pdf_path), page_index=i, scale=1.0)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_render, range(16)))

    assert len(results) == 16
    assert all(base64.b64decode(r)[:8] == b"\x89PNG\r\n\x1a\n" for r in results)
