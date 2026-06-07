"""Command-line interface for pdf2latex.

Subcommands:
    list       List PDFs found in sources/.
    convert    Convert a PDF to LaTeX page by page (and assemble).
    assemble   Rebuild the monolith and standalone from existing pages.
    split      Split the monolith into chapter files (config or --auto).
    compile    Compile the standalone .tex into PDF (needs pdflatex).
    fix        Compile and auto-repair the pages that break (vision + the error).
    validate   Check converted pages for broken LaTeX (offline).
    serve      Launch the local web UI (needs the [web] extra).
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .layout import OUTPUT_DIR, SOURCES_DIR, BookPaths, resolve_source


def _default_model() -> str:
    return os.environ.get("PDF2LATEX_MODEL", "gpt-5")


def _cmd_list(_args: argparse.Namespace) -> int:
    pdfs = sorted(SOURCES_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs in {SOURCES_DIR}. Drop your files there.")
        return 0
    print(f"PDFs in {SOURCES_DIR}:")
    for p in pdfs:
        print(f"  - {p.name}")
    return 0


def _cmd_convert(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    from .converter import convert_pdf

    load_dotenv()
    source = resolve_source(args.source)
    convert_pdf(
        source,
        model=args.model,
        max_tokens=args.max_tokens,
        batch=args.batch,
        start=args.start,
        end=args.end,
        batch_size=args.batch_size,
        scale=args.scale,
        workers=args.workers,
        rpm=args.rpm,
        tpm=args.tpm,
        title=args.title,
        subtitle=args.subtitle,
        dry_run=args.dry_run,
        assume_yes=args.yes,
        repair=args.repair,
        repair_retries=args.repair_retries,
        output_root=args.out,
    )
    return 0


def _cmd_assemble(args: argparse.Namespace) -> int:
    from .assemble import assemble_monolith, write_standalone

    paths = _paths_for(args.source, args.out)
    assemble_monolith(paths)
    write_standalone(paths, title=args.title, subtitle=args.subtitle)
    print(f"Assembled: {paths.monolith_tex.name} and {paths.standalone_tex.name}")
    return 0


def _cmd_split(args: argparse.Namespace) -> int:
    from .splitter import split_auto, split_from_config

    paths = _paths_for(args.source, args.out)
    if args.auto:
        split_auto(paths)
    elif args.config:
        split_from_config(paths, Path(args.config))
    else:
        raise SystemExit("Provide --config <file.json> or --auto.")
    print(f"Chapters in: {paths.chapters_dir}")
    return 0


def _cmd_compile(args: argparse.Namespace) -> int:
    from .compile import compile_pdf

    paths = _paths_for(args.source, args.out)
    compile_pdf(paths.standalone_tex, engine=args.engine, runs=args.runs)
    return 0


def _cmd_fix(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    from .autofix import autofix_book

    load_dotenv()
    source = resolve_source(args.source)

    def report(event: dict) -> None:
        kind = event.get("kind")
        if kind == "compile":
            print(f"Round {event['round']}: compiling...", flush=True)
        elif kind == "broken":
            print(f"  broken pages: {event['pages'] or 'none attributable'}", flush=True)
        elif kind == "page_fixed":
            print(f"  page {event['page']}: fixed ({event['tokens']} tokens)", flush=True)
        elif kind == "page_skipped":
            print(f"  page {event['page']}: skipped — unsafe fix ({event['reason']})", flush=True)
        elif kind == "page_error":
            print(f"  page {event['page']}: error — {event['error']}", flush=True)
        elif kind == "built":
            print(f"PDF built: {event['pdf']}", flush=True)

    result = autofix_book(
        source,
        model=args.model,
        output_root=args.out,
        max_rounds=args.max_rounds,
        max_tries_per_page=args.max_tries,
        on_event=report,
    )
    print(
        f"\nDone in {result.rounds} round(s). "
        f"fixed={result.fixed or '[]'} stuck={result.stuck or '[]'} "
        f"skipped={result.skipped or '[]'} tokens={result.total_tokens:,}"
    )
    if result.ok:
        print(f"PDF: {result.pdf}")
        return 0
    if result.stuck:
        print("Some pages still need a manual edit (see the page .tex files).")
    return 1


def _cmd_validate(args: argparse.Namespace) -> int:
    from .assemble import iter_page_files
    from .validate import (
        deep_check,
        deep_check_available,
        validate_existing,
        write_needs_review,
    )

    paths = _paths_for(args.source, args.out)
    review = validate_existing(paths)
    if not review:
        print(f"No converted pages found in {paths.pages_dir}.")
        return 0

    if args.deep_check:
        if deep_check_available(args.engine):
            for page_num, tex in iter_page_files(paths):
                review[page_num] = review.get(page_num, []) + deep_check(tex, engine=args.engine)
        else:
            print(f"Deep check skipped: '{args.engine}' not found on PATH.")

    report = write_needs_review(paths, review)
    n_review = sum(1 for issues in review.values() if issues)
    print(f"Validated {len(review)} page(s); {n_review} need review.")
    if report is not None:
        print(f"See {report}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    try:
        from .web import run
    except ImportError as exc:
        raise SystemExit(
            'The web UI needs extra dependencies. Install them with:\n'
            '  pip install -e ".[web]"'
        ) from exc
    run(host=args.host, port=args.port)
    return 0


def _paths_for(source: str, output_root: Path | None = None) -> BookPaths:
    """Accept either a source PDF reference or an existing output slug."""
    root = output_root or OUTPUT_DIR
    slug_dir = root / source
    if slug_dir.is_dir():
        # Reconstruct from an existing output folder name.
        fake_pdf = SOURCES_DIR / f"{source}.pdf"
        return BookPaths.for_source(fake_pdf, output_root=root)
    return BookPaths.for_source(resolve_source(source), output_root=root)


def _add_out_arg(p: argparse.ArgumentParser) -> None:
    """Add a shared ``--out`` flag to redirect generated output off the repo."""
    p.add_argument(
        "--out",
        "--output-dir",
        dest="out",
        type=Path,
        default=None,
        metavar="DIR",
        help="Output directory (default: ./output, or $PDF2LATEX_OUTPUT_DIR if set).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf2latex",
        description="Convert math-heavy PDFs to LaTeX with a vision LLM.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List PDFs in sources/.").set_defaults(func=_cmd_list)

    pc = sub.add_parser("convert", help="Convert a PDF to LaTeX page by page.")
    pc.add_argument("source", help="PDF filename in sources/ or a full path.")
    pc.add_argument("--model", default=_default_model())
    pc.add_argument("--max-tokens", type=int, default=16384)
    pc.add_argument("--batch", type=int, default=None, help="Process batch N (size --batch-size).")
    pc.add_argument("--batch-size", type=int, default=100)
    pc.add_argument("--start", type=int, default=None, help="First page (1-based).")
    pc.add_argument("--end", type=int, default=None, help="Last page (inclusive).")
    pc.add_argument("--scale", type=float, default=2.0, help="Render scale (resolution).")
    # Default mirrors converter.DEFAULT_WORKERS (kept literal to avoid importing the
    # heavy converter module just to build the parser).
    pc.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Pages converted concurrently (default 4; use 1 for sequential).",
    )
    pc.add_argument("--rpm", type=float, default=None, help="Optional requests-per-minute cap.")
    pc.add_argument("--tpm", type=float, default=None, help="Optional tokens-per-minute cap.")
    pc.add_argument("--title", default=None)
    pc.add_argument("--subtitle", default=None)
    pc.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the plan and cost estimate, render-validate pages; no API calls.",
    )
    pc.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt (for non-interactive/automated runs).",
    )
    pc.add_argument(
        "--repair",
        action="store_true",
        help="Send pages that fail validation back to the model for a minimal fix (uses API).",
    )
    pc.add_argument(
        "--repair-retries",
        type=int,
        default=1,
        help="Max repair round-trips per failing page (default 1).",
    )
    _add_out_arg(pc)
    pc.set_defaults(func=_cmd_convert)

    pa = sub.add_parser("assemble", help="Rebuild monolith + standalone from pages.")
    pa.add_argument("source", help="Source PDF reference or existing output slug.")
    pa.add_argument("--title", default=None)
    pa.add_argument("--subtitle", default=None)
    _add_out_arg(pa)
    pa.set_defaults(func=_cmd_assemble)

    ps = sub.add_parser("split", help="Split monolith into chapters.")
    ps.add_argument("source", help="Source PDF reference or existing output slug.")
    ps.add_argument("--config", default=None, help="JSON chapter config.")
    ps.add_argument("--auto", action="store_true", help="Split on blank-page separators.")
    _add_out_arg(ps)
    ps.set_defaults(func=_cmd_split)

    pp = sub.add_parser("compile", help="Compile the standalone .tex to PDF.")
    pp.add_argument("source", help="Source PDF reference or existing output slug.")
    pp.add_argument("--engine", default="pdflatex")
    pp.add_argument("--runs", type=int, default=2)
    _add_out_arg(pp)
    pp.set_defaults(func=_cmd_compile)

    pf = sub.add_parser(
        "fix", help="Compile and auto-repair the pages that break, until the PDF builds."
    )
    pf.add_argument("source", help="Source PDF reference or existing output slug.")
    pf.add_argument("--model", default=_default_model(), help="Vision model for repairs.")
    pf.add_argument("--max-rounds", type=int, default=5, help="Max compile/fix rounds.")
    pf.add_argument(
        "--max-tries", type=int, default=3, help="Max repair attempts per page."
    )
    _add_out_arg(pf)
    pf.set_defaults(func=_cmd_fix)

    pvd = sub.add_parser("validate", help="Check converted pages for broken LaTeX (offline).")
    pvd.add_argument("source", help="Source PDF reference or existing output slug.")
    pvd.add_argument(
        "--deep-check", action="store_true", help="Also run an external linter (chktex)."
    )
    pvd.add_argument("--engine", default="chktex", help="Deep-check linter (default chktex).")
    _add_out_arg(pvd)
    pvd.set_defaults(func=_cmd_validate)

    psv = sub.add_parser("serve", help="Launch the local web UI (needs the [web] extra).")
    psv.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1).")
    psv.add_argument("--port", type=int, default=8000, help="Bind port (default 8000).")
    psv.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
