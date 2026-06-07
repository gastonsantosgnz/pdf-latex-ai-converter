"""Compile-driven auto-repair.

Instead of relying only on the offline validator (which misses errors that
*compile* badly but parse fine, like a column-count mismatch), this loop:

1. compiles the book leniently,
2. reads the real pdflatex error attributed to each page,
3. asks the model to fix that page using BOTH its source image and the error
   (:func:`pdf2latex.worker.repair_with_image`), guarded against destructive fixes,
4. reassembles and repeats until the PDF builds or the budget runs out.

It is the automation of the manual compile/fix loop. Every dependency that hits
the network or the LaTeX engine is injectable so the loop is testable offline.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .assemble import assemble_monolith, write_standalone
from .compile import compile_errors_by_page, compile_pdf
from .layout import BookPaths
from .worker import make_client, render_page_to_base64, repair_with_image


@dataclass
class AutofixResult:
    """Outcome of an :func:`autofix_book` run."""

    pdf: Path | None
    rounds: int
    fixed: list[int] = field(default_factory=list)
    stuck: list[int] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)  # repairs the guard rejected
    total_tokens: int = 0

    @property
    def ok(self) -> bool:
        return self.pdf is not None


def autofix_book(
    source_pdf: Path,
    *,
    model: str,
    output_root: Path | None = None,
    max_rounds: int = 5,
    max_tries_per_page: int = 3,
    on_event: Callable[[dict], None] | None = None,
    client=None,
    compile_fn: Callable = compile_pdf,
    render_fn: Callable = render_page_to_base64,
    repair_fn: Callable = repair_with_image,
) -> AutofixResult:
    """Compile, fix the pages that break, and repeat until the PDF builds.

    ``max_rounds`` bounds the whole loop and ``max_tries_per_page`` bounds how
    often any single page is sent to the model, so the cost is always capped.
    """
    paths = (
        BookPaths.for_source(source_pdf, output_root=output_root)
        if output_root is not None
        else BookPaths.for_source(source_pdf)
    )

    def emit(**event) -> None:
        if on_event is not None:
            on_event(event)

    tries: dict[int, int] = {}
    touched: set[int] = set()
    skipped: list[int] = []
    total_tokens = 0
    pdf: Path | None = None
    rounds = 0

    for rnd in range(1, max_rounds + 1):
        rounds = rnd
        emit(kind="compile", round=rnd)
        try:
            pdf = compile_fn(paths.standalone_tex, halt_on_error=False)
            emit(kind="built", pdf=str(pdf))
            break
        except SystemExit:
            pdf = None

        errors = compile_errors_by_page(paths)
        broken = [e["page"] for e in errors]
        emit(kind="broken", round=rnd, pages=broken)
        if not broken:
            emit(kind="unattributable")  # compile failed but no page could be blamed
            break

        err_by_page = {e["page"]: e["error"] for e in errors}
        todo = [p for p in broken if tries.get(p, 0) < max_tries_per_page]
        if not todo:
            break  # everything left has exhausted its tries

        if client is None:
            client = make_client()

        progressed = False
        for page in todo:
            tries[page] = tries.get(page, 0) + 1
            tex = paths.page_tex(page)
            if not tex.exists():
                continue
            latex = tex.read_text("utf-8", errors="replace")
            problem = err_by_page.get(page, "the page fails to compile")
            try:
                img = render_fn(str(source_pdf), page_index=page - 1)
                result = repair_fn(
                    client,
                    img,
                    latex,
                    [f"LaTeX compile error: {problem}. Fix the page so it compiles."],
                    model=model,
                )
            except Exception as exc:  # noqa: BLE001 - report and keep going
                emit(kind="page_error", page=page, error=str(exc))
                continue
            total_tokens += result.total_tokens
            if result.rejected:
                emit(kind="page_skipped", page=page, reason=result.rejected)
                if page not in skipped:
                    skipped.append(page)
                continue
            tex.with_suffix(".tex.bak").write_text(latex, encoding="utf-8")  # reversible
            tex.write_text(result.latex, encoding="utf-8")
            touched.add(page)
            progressed = True
            emit(kind="page_fixed", page=page, tokens=result.total_tokens)

        assemble_monolith(paths)
        write_standalone(paths)
        if not progressed:
            break  # nothing applied this round; further rounds would loop forever

    # Final classification: one last compile so the report reflects the last fixes.
    if pdf is None:
        try:
            pdf = compile_fn(paths.standalone_tex, halt_on_error=False)
            emit(kind="built", pdf=str(pdf))
        except SystemExit:
            pdf = None
    final_broken = [] if pdf is not None else [e["page"] for e in compile_errors_by_page(paths)]

    result = AutofixResult(
        pdf=pdf,
        rounds=rounds,
        fixed=sorted(touched - set(final_broken)),
        stuck=sorted(final_broken),
        skipped=sorted(skipped),
        total_tokens=total_tokens,
    )
    emit(
        kind="done",
        ok=result.ok,
        fixed=result.fixed,
        stuck=result.stuck,
        skipped=result.skipped,
        tokens=result.total_tokens,
    )
    return result
