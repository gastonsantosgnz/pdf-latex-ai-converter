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
