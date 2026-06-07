"""Command-line interface for pdf2latex.

Subcommands:
    list       List PDFs found in sources/.
    convert    Convert a PDF to LaTeX page by page (and assemble).
    assemble   Rebuild the monolith and standalone from existing pages.
    split      Split the monolith into chapter files (config or --auto).
    compile    Compile the standalone .tex into PDF (needs pdflatex).
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .layout import OUTPUT_DIR, SOURCES_DIR, BookPaths, resolve_source


def _default_model() -> str:
    return os.environ.get("PDF2LATEX_MODEL", "gpt-4o")


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
        profile=args.profile,
        max_tokens=args.max_tokens,
        batch=args.batch,
        start=args.start,
        end=args.end,
        batch_size=args.batch_size,
        scale=args.scale,
        title=args.title,
        subtitle=args.subtitle,
    )
    return 0


def _cmd_assemble(args: argparse.Namespace) -> int:
    from .assemble import assemble_monolith, write_standalone

    paths = _paths_for(args.source)
    assemble_monolith(paths)
    write_standalone(paths, title=args.title, subtitle=args.subtitle)
    print(f"Assembled: {paths.monolith_tex.name} and {paths.standalone_tex.name}")
    return 0


def _cmd_split(args: argparse.Namespace) -> int:
    from .splitter import split_auto, split_from_config

    paths = _paths_for(args.source)
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

    paths = _paths_for(args.source)
    compile_pdf(paths.standalone_tex, engine=args.engine, runs=args.runs)
    return 0


def _paths_for(source: str) -> BookPaths:
    """Accept either a source PDF reference or an existing output slug."""
    slug_dir = OUTPUT_DIR / source
    if slug_dir.is_dir():
        # Reconstruct from an existing output folder name.
        fake_pdf = SOURCES_DIR / f"{source}.pdf"
        return BookPaths.for_source(fake_pdf)
    return BookPaths.for_source(resolve_source(source))


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
    pc.add_argument("--profile", choices=("default", "dense"), default="default")
    pc.add_argument("--max-tokens", type=int, default=16384)
    pc.add_argument("--batch", type=int, default=None, help="Process batch N (size --batch-size).")
    pc.add_argument("--batch-size", type=int, default=100)
    pc.add_argument("--start", type=int, default=None, help="First page (1-based).")
    pc.add_argument("--end", type=int, default=None, help="Last page (inclusive).")
    pc.add_argument("--scale", type=float, default=2.0, help="Render scale (resolution).")
    pc.add_argument("--title", default=None)
    pc.add_argument("--subtitle", default=None)
    pc.set_defaults(func=_cmd_convert)

    pa = sub.add_parser("assemble", help="Rebuild monolith + standalone from pages.")
    pa.add_argument("source", help="Source PDF reference or existing output slug.")
    pa.add_argument("--title", default=None)
    pa.add_argument("--subtitle", default=None)
    pa.set_defaults(func=_cmd_assemble)

    ps = sub.add_parser("split", help="Split monolith into chapters.")
    ps.add_argument("source", help="Source PDF reference or existing output slug.")
    ps.add_argument("--config", default=None, help="JSON chapter config.")
    ps.add_argument("--auto", action="store_true", help="Split on blank-page separators.")
    ps.set_defaults(func=_cmd_split)

    pp = sub.add_parser("compile", help="Compile the standalone .tex to PDF.")
    pp.add_argument("source", help="Source PDF reference or existing output slug.")
    pp.add_argument("--engine", default="pdflatex")
    pp.add_argument("--runs", type=int, default=2)
    pp.set_defaults(func=_cmd_compile)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
