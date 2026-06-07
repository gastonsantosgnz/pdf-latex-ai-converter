# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Continuous Integration (GitHub Actions): Ruff lint plus a multi-version smoke
  job on Python 3.10-3.12.
- Offline `pytest` suite with the OpenAI client fully mocked; coverage enforced
  at 80% in CI (local coverage ~98%).
- Open-source community files and richer package metadata (issue/PR templates,
  Code of Conduct, Contributing guide, `authors`/`keywords`/`classifiers`/`project.urls`).
- Cost controls for `convert`: a pre-flight summary with an approximate cost
  estimate, a `--dry-run` flag (renders/validates pages, zero API calls), a
  `--yes/-y` flag to skip confirmation, and a live progress bar with a running
  token/cost tally. The per-model price table is configurable via the
  `PDF2LATEX_PRICES` environment variable.

## [0.1.0] - 2026-06-06

### Added
- Initial public release.
- Page-by-page PDF to LaTeX conversion with a vision LLM (OpenAI GPT-4o by default).
- CLI with `list`, `convert`, `assemble`, `split`, and `compile` subcommands.
- `default` and `dense` conversion profiles.
- Resumable, batched conversion; monolith plus standalone assembly.
- Chapter splitting by JSON config or automatic blank-page detection.
- One-command compile to PDF via `pdflatex`.

[Unreleased]: https://github.com/gastonsantosgnz/pdf-latex-ai-converter/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/gastonsantosgnz/pdf-latex-ai-converter/releases/tag/v0.1.0
