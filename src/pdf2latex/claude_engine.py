"""Convert a page image to LaTeX with Claude Code (the user's subscription).

This is an alternative to the OpenAI engine. Instead of calling the OpenAI API,
it shells out to the local ``claude`` CLI in headless print mode (``claude -p``),
which reads the rendered page image with its Read tool and returns the LaTeX.

Authentication uses the user's Claude Code login - no API key required (unless
``ANTHROPIC_API_KEY`` is set in the environment, which the CLI would prefer).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .assemble import strip_code_fences
from .worker import PageResult


def claude_available() -> bool:
    """True if the ``claude`` CLI is on PATH."""
    return shutil.which("claude") is not None


def _error_detail(stdout: str) -> str | None:
    """If ``claude -p`` reported an error in its JSON result, return it, else None."""
    try:
        data = json.loads(stdout.strip())
    except (json.JSONDecodeError, ValueError):
        return None
    if data.get("is_error"):
        status = data.get("api_error_status")
        msg = data.get("result") or "unknown error"
        return f"{msg} (HTTP {status})" if status else str(msg)
    return None


def _parse_output(stdout: str) -> tuple[str, int, int]:
    """Return (latex, prompt_tokens, completion_tokens) from ``claude -p`` JSON.

    Falls back to treating the whole output as LaTeX if it is not JSON.
    """
    stdout = stdout.strip()
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return stdout, 0, 0
    latex = data.get("result")
    if not isinstance(latex, str):
        latex = stdout
    usage = data.get("usage") or {}
    pt = int(usage.get("input_tokens", 0) or 0)
    ct = int(usage.get("output_tokens", 0) or 0)
    return latex, pt, ct


def convert_image_with_claude(
    png_path: Path,
    *,
    system_prompt: str,
    user_text: str,
    model: str | None = None,
    timeout: int = 600,
) -> PageResult:
    """Convert one page PNG to LaTeX via ``claude -p`` and return a PageResult."""
    if shutil.which("claude") is None:
        raise SystemExit(
            "'claude' CLI not found on PATH. Install Claude Code and sign in "
            "(claude.com/product/claude-code), or use the OpenAI engine."
        )
    prompt = (
        f"{system_prompt}\n\n{user_text}\n\n"
        f"Read the image file at {png_path} and convert THAT page to LaTeX. "
        "Output ONLY the LaTeX for the page: no explanation, no preamble, no "
        "Markdown code fences."
    )
    cmd = [
        "claude",
        "-p",
        prompt,
        "--allowedTools",
        "Read",
        "--output-format",
        "json",
    ]
    if model:
        cmd += ["--model", model]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace", timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"claude -p timed out after {timeout}s") from exc
    err = _error_detail(proc.stdout)
    if err is not None or proc.returncode != 0:
        low = (err or "").lower()
        if "authenticat" in low or "401" in low or "invalid authentication" in low:
            raise RuntimeError(
                "Claude CLI is not authenticated for headless use (HTTP 401). In a "
                "normal terminal run `claude setup-token` and export the resulting "
                "CLAUDE_CODE_OAUTH_TOKEN (or `claude auth login`), then retry — or use "
                "--engine openai."
            )
        detail = (err or proc.stderr or proc.stdout or "").strip()[:300]
        raise RuntimeError(f"claude -p failed (exit {proc.returncode}): {detail}")
    latex, pt, ct = _parse_output(proc.stdout)
    return PageResult(
        latex=strip_code_fences(latex),
        prompt_tokens=pt,
        completion_tokens=ct,
        total_tokens=pt + ct,
        finish_reason="stop",
    )


def claude_model() -> str | None:
    """Optional model override for the Claude engine (else the plan default)."""
    return os.environ.get("PDF2LATEX_CLAUDE_MODEL") or None
