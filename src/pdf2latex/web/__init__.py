"""Optional local web UI for pdf2latex (install with the ``[web]`` extra)."""

from __future__ import annotations

from .app import create_app, run

__all__ = ["create_app", "run"]
