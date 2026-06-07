"""Tests for the dependency-free progress bar."""

from __future__ import annotations

import io

from pdf2latex.progress import ProgressBar


def test_disabled_off_tty_writes_nothing() -> None:
    buf = io.StringIO()  # StringIO.isatty() is False -> bar auto-disables
    bar = ProgressBar(10, stream=buf)
    assert bar.enabled is False

    bar.update(advance=5, note="anything")
    bar.close()
    assert buf.getvalue() == ""  # logs are not polluted in non-interactive runs


def test_enabled_renders_bar_and_running_note() -> None:
    buf = io.StringIO()
    bar = ProgressBar(4, stream=buf, enabled=True)

    bar.update(advance=1, note="100 tok | ~$0.01")
    out = buf.getvalue()
    assert "1/4" in out
    assert "25%" in out
    assert "$0.01" in out

    bar.update(done=4)
    assert "4/4" in buf.getvalue()

    bar.close()
    assert buf.getvalue().endswith("\n")


def test_zero_total_does_not_divide_by_zero() -> None:
    buf = io.StringIO()
    bar = ProgressBar(0, stream=buf, enabled=True)
    bar.update(advance=1)
    bar.close()
    assert "100%" in buf.getvalue()
