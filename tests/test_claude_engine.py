"""Tests for the Claude Code engine with the `claude` CLI fully mocked."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from pdf2latex import claude_engine
from pdf2latex.claude_engine import _parse_output, convert_image_with_claude


def test_parse_output_json_with_usage() -> None:
    out = '{"result": "\\\\section*{Hi}", "usage": {"input_tokens": 12, "output_tokens": 5}}'
    latex, pt, ct = _parse_output(out)
    assert latex == "\\section*{Hi}"
    assert (pt, ct) == (12, 5)


def test_parse_output_non_json_falls_back() -> None:
    latex, pt, ct = _parse_output("just latex here")
    assert latex == "just latex here"
    assert (pt, ct) == (0, 0)


def test_convert_image_with_claude_invokes_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    png = tmp_path / "p.png"
    png.write_bytes(b"x")
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(
            returncode=0,
            stdout='{"result": "```latex\\nX\\n```", "usage": {"input_tokens": 1, "output_tokens": 2}}',
            stderr="",
        )

    monkeypatch.setattr(claude_engine.shutil, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(claude_engine.subprocess, "run", fake_run)
    res = convert_image_with_claude(png, system_prompt="SYS", user_text="USR")

    assert res.latex == "X"  # code fences stripped
    assert res.total_tokens == 3
    cmd = captured["cmd"]
    assert cmd[0] == "claude" and "-p" in cmd
    assert str(png) in cmd[2]  # the prompt points the Read tool at the image
    assert "--output-format" in cmd and "json" in cmd


def test_convert_image_with_claude_missing_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_engine.shutil, "which", lambda _name: None)
    with pytest.raises(SystemExit, match="claude"):
        convert_image_with_claude(tmp_path / "p.png", system_prompt="", user_text="")


def test_convert_image_with_claude_nonzero_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_engine.shutil, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(
        claude_engine.subprocess,
        "run",
        lambda cmd, **k: SimpleNamespace(returncode=1, stdout="", stderr="boom"),
    )
    with pytest.raises(RuntimeError, match="claude -p failed"):
        convert_image_with_claude(tmp_path / "p.png", system_prompt="", user_text="")


def test_convert_image_with_claude_auth_error_is_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(claude_engine.shutil, "which", lambda _name: "/usr/bin/claude")
    auth_json = (
        '{"is_error": true, "api_error_status": 401, '
        '"result": "Failed to authenticate. API Error: 401 Invalid authentication credentials"}'
    )
    monkeypatch.setattr(
        claude_engine.subprocess,
        "run",
        lambda cmd, **k: SimpleNamespace(returncode=1, stdout=auth_json, stderr=""),
    )
    with pytest.raises(RuntimeError, match="setup-token"):
        convert_image_with_claude(tmp_path / "p.png", system_prompt="", user_text="")


def test_claude_available_reflects_which(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_engine.shutil, "which", lambda _name: "/usr/bin/claude")
    assert claude_engine.claude_available() is True
    monkeypatch.setattr(claude_engine.shutil, "which", lambda _name: None)
    assert claude_engine.claude_available() is False
