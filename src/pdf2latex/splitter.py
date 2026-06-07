r"""Split the assembled monolith into chapter files.

Two modes:

* **config** — a JSON file lists the chapters and their start pages.
* **auto**   — start a new chapter whenever a page was detected as blank
  (``% blank-page``), which works for books that use a blank sheet between
  chapters. Page 1 always starts the first chapter.

Both modes rewrite the standalone so it ``\input``s the chapter files in order.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .assemble import write_standalone
from .layout import BookPaths, slugify

_PAGE_MARKER = re.compile(r"% ===== Page (\d+) =====")


def _page_line_map(lines: list[str]) -> dict[int, int]:
    """Map page number -> line index of its marker in the monolith."""
    mapping: dict[int, int] = {}
    for i, line in enumerate(lines):
        m = _PAGE_MARKER.match(line.strip())
        if m:
            mapping[int(m.group(1))] = i
    return mapping


def _nearest_line(page_line: dict[int, int], page: int, lookahead: int = 15) -> int | None:
    for p in range(page, page + lookahead):
        if p in page_line:
            return page_line[p]
    return None


def split_from_config(paths: BookPaths, config_path: Path) -> list[tuple[str, str]]:
    """Split using an explicit chapter config. Returns [(filename, title), ...]."""
    config = json.loads(config_path.read_text(encoding="utf-8"))
    chapters = config.get("chapters", [])
    if not chapters:
        raise SystemExit("Config has no 'chapters'.")

    lines = paths.monolith_tex.read_text(encoding="utf-8").splitlines()
    page_line = _page_line_map(lines)
    if not page_line:
        raise SystemExit("No page markers in monolith. Run 'assemble' first.")
    last_page = max(page_line)

    starts = [int(c["start_page"]) for c in chapters]
    written: list[tuple[str, str]] = []

    for idx, ch in enumerate(chapters):
        start_page = int(ch["start_page"])
        title = ch.get("title", f"Chapter {idx + 1}")
        slug = ch.get("slug") or slugify(title)

        start_line = _nearest_line(page_line, start_page)
        if idx + 1 < len(chapters):
            end_line = _nearest_line(page_line, starts[idx + 1])
        else:
            end_line = len(lines)
        if start_line is None or end_line is None:
            print(f"WARNING: could not locate chapter {idx + 1} ({title}); skipped")
            continue

        fname = f"chapter_{idx + 1:02d}_{slug}.tex"
        chunk = "\n".join(lines[start_line:end_line]).rstrip() + "\n"
        (paths.chapters_dir / fname).write_text(chunk, encoding="utf-8")
        written.append((fname, title))
        end_page = starts[idx + 1] - 1 if idx + 1 < len(chapters) else last_page
        print(f"Chapter {idx + 1:02d}: pages {start_page}-{end_page} -> {fname}")

    title = config.get("title")
    subtitle = config.get("subtitle")
    write_standalone(paths, title=title, subtitle=subtitle, chapters=written)
    return written


def split_auto(paths: BookPaths) -> list[tuple[str, str]]:
    """Split on blank-page separators. Returns [(filename, title), ...]."""
    lines = paths.monolith_tex.read_text(encoding="utf-8").splitlines()
    page_line = _page_line_map(lines)
    if not page_line:
        raise SystemExit("No page markers in monolith. Run 'assemble' first.")

    pages_sorted = sorted(page_line)
    blank_pages: set[int] = set()
    for page in pages_sorted:
        start = page_line[page]
        nxt = page + 1
        end = page_line.get(nxt, len(lines))
        body = "\n".join(lines[start + 1 : end]).lower()
        if "% blank-page" in body and len(body.strip()) < 40:
            blank_pages.add(page)

    # Chapter starts: page 1, plus the page right after each blank page.
    starts = [pages_sorted[0]]
    for page in pages_sorted:
        if page in blank_pages:
            nxt = page + 1
            if nxt in page_line:
                starts.append(nxt)
    starts = sorted(set(starts))

    written: list[tuple[str, str]] = []
    for idx, start_page in enumerate(starts):
        start_line = page_line[start_page]
        end_line = page_line[starts[idx + 1]] if idx + 1 < len(starts) else len(lines)
        title = f"Chapter {idx + 1}"
        fname = f"chapter_{idx + 1:02d}.tex"
        chunk = "\n".join(lines[start_line:end_line]).rstrip() + "\n"
        (paths.chapters_dir / fname).write_text(chunk, encoding="utf-8")
        written.append((fname, title))
        print(f"Chapter {idx + 1:02d}: from page {start_page} -> {fname}")

    write_standalone(paths, chapters=written)
    return written
