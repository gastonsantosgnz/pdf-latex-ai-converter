"""Offline LaTeX validation plus an optional external deep check.

The offline checks are cheap, dependency-free and run on every converted page:

* balanced ``{`` / ``}`` (ignoring escaped ``\\{`` ``\\}`` and comments),
* matched and correctly nested ``\\begin{env}`` / ``\\end{env}``,
* an even number of unescaped ``$`` (an odd count breaks math mode).

They are heuristics, not a LaTeX parser, but they catch the mistakes a vision
model actually makes. The optional :func:`deep_check` shells out to ``chktex``
for a deeper pass when the user asks for it and the tool is installed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .assemble import iter_page_files
from .layout import BookPaths

_ENV_RE = re.compile(r"\\(begin|end)\s*\{([^{}]*)\}")


@dataclass(frozen=True)
class Issue:
    """A single problem found on a page."""

    kind: str  # "braces" | "environments" | "math" | "deep"
    message: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.message}"


def _strip_comments(text: str) -> str:
    """Drop LaTeX comments (``%`` to end of line) while keeping escaped ``\\%``."""
    cleaned: list[str] = []
    for line in text.splitlines():
        out: list[str] = []
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "\\" and i + 1 < len(line):
                out.append(line[i : i + 2])  # keep escaped pair verbatim
                i += 2
                continue
            if ch == "%":
                break  # rest of the line is a comment
            out.append(ch)
            i += 1
        cleaned.append("".join(out))
    return "\n".join(cleaned)


def _brace_issue(text: str) -> Issue | None:
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            i += 2  # skip escaped char (e.g. \{ \} \$)
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return Issue("braces", "unmatched closing brace '}'")
        i += 1
    if depth > 0:
        return Issue("braces", f"{depth} unclosed brace(s) '{{'")
    return None


def _math_issue(text: str) -> Issue | None:
    count = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            i += 2
            continue
        if ch == "$":
            count += 1
        i += 1
    if count % 2 == 1:
        return Issue("math", f"odd number of unescaped '$' ({count}); use \\( \\) or escape as \\$")
    return None


def _environment_issue(text: str) -> Issue | None:
    stack: list[str] = []
    for match in _ENV_RE.finditer(text):
        kind, name = match.group(1), match.group(2).strip()
        if kind == "begin":
            stack.append(name)
        elif not stack:
            return Issue("environments", f"\\end{{{name}}} without a matching \\begin")
        else:
            opened = stack.pop()
            if opened != name:
                return Issue("environments", f"\\begin{{{opened}}} closed by \\end{{{name}}}")
    if stack:
        return Issue("environments", f"unclosed environment(s): {', '.join(reversed(stack))}")
    return None


def validate_text(text: str) -> list[Issue]:
    """Run all offline checks on a page's LaTeX. Empty list means it looks fine."""
    normalized = _strip_comments(text)
    issues: list[Issue] = []
    for check in (_brace_issue, _math_issue, _environment_issue):
        issue = check(normalized)
        if issue is not None:
            issues.append(issue)
    return issues


def validate_pages(paths: BookPaths, page_numbers: Iterable[int]) -> dict[int, list[Issue]]:
    """Validate the existing ``.tex`` for each given page number."""
    review: dict[int, list[Issue]] = {}
    for page_num in page_numbers:
        tex = paths.page_tex(page_num)
        if tex.exists():
            review[page_num] = validate_text(tex.read_text(encoding="utf-8"))
    return review


def validate_existing(paths: BookPaths) -> dict[int, list[Issue]]:
    """Validate every converted page found under ``pages/``."""
    return {num: validate_text(tex.read_text(encoding="utf-8")) for num, tex in iter_page_files(paths)}


def deep_check(tex_path: Path, *, engine: str = "chktex") -> list[Issue]:
    """Run an external linter (``chktex``) on a page. Caller ensures it exists."""
    proc = subprocess.run(
        [engine, "-q", "-n", "all", str(tex_path)],
        capture_output=True,
        text=True,
    )
    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    return [Issue("deep", ln) for ln in lines]


def deep_check_available(engine: str = "chktex") -> bool:
    return shutil.which(engine) is not None


def write_needs_review(paths: BookPaths, review: dict[int, list[Issue]]) -> Path | None:
    """Write ``needs-review.txt`` for pages with issues; remove it if all clean."""
    failing = {page: issues for page, issues in review.items() if issues}
    report = paths.out_dir / "needs-review.txt"
    if not failing:
        if report.exists():
            report.unlink()
        return None
    lines = [f"Pages needing review: {len(failing)}", ""]
    for page in sorted(failing):
        lines.append(f"page_{page:04d}.tex")
        lines.extend(f"  - {issue}" for issue in failing[page])
        lines.append("")
    report.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return report
