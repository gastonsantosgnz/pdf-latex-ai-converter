"""A dependency-free progress bar that degrades gracefully off a TTY.

When attached to an interactive terminal it renders a single self-updating line
on ``stderr`` (carriage-return based). When the stream is not a TTY -- CI logs,
a redirected file, a pipe -- it stays silent so it never pollutes logs; callers
keep their normal per-line logging in that case.
"""

from __future__ import annotations

import sys
from typing import TextIO


class ProgressBar:
    """Minimal single-line progress bar with an optional running annotation."""

    def __init__(
        self,
        total: int,
        *,
        stream: TextIO | None = None,
        enabled: bool | None = None,
        width: int = 28,
    ) -> None:
        self.total = max(total, 0)
        self.stream = stream if stream is not None else sys.stderr
        if enabled is None:
            enabled = bool(getattr(self.stream, "isatty", lambda: False)()) and self.total > 0
        self.enabled = enabled
        self.width = width
        self.done = 0
        self._last_len = 0

    def update(self, *, advance: int = 0, done: int | None = None, note: str = "") -> None:
        """Advance the bar and redraw. ``note`` is free-text shown after the bar."""
        self.done = done if done is not None else self.done + advance
        if not self.enabled:
            return
        frac = self.done / self.total if self.total else 1.0
        frac = min(max(frac, 0.0), 1.0)
        filled = int(frac * self.width)
        bar = "#" * filled + "-" * (self.width - filled)
        line = f"[{bar}] {self.done}/{self.total} {frac * 100:3.0f}%"
        if note:
            line += f" | {note}"
        # Pad so a shorter line fully overwrites a previous longer one.
        pad = max(self._last_len - len(line), 0)
        self.stream.write("\r" + line + " " * pad)
        self.stream.flush()
        self._last_len = len(line)

    def close(self) -> None:
        """Finish the line so subsequent output starts cleanly."""
        if self.enabled:
            self.stream.write("\n")
            self.stream.flush()
