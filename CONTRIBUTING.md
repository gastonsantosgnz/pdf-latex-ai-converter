# Contributing to pdf-latex-ai-converter

Thanks for taking the time to contribute. This project converts math-heavy PDFs
to LaTeX page by page with a vision LLM, and it aims to stay small, readable and
well tested. The notes below should get you productive quickly.

## Development setup

```bash
git clone https://github.com/gastonsantosgnz/pdf-latex-ai-converter
cd pdf-latex-ai-converter

python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

pip install -e ".[dev]"
```

This installs the package in editable mode together with the development tools
(`ruff`, `pytest`, `pytest-cov`).

## Before you open a pull request

Run the same checks CI runs:

```bash
ruff check .          # lint
pytest                # tests + coverage (must stay at or above 80%)
```

The test suite is fully offline: the OpenAI client is mocked, so `pytest` never
makes a network call and never spends API credit. New behaviour should come with
tests; bug fixes should come with a regression test.

If you change packaging metadata, validate the distribution too:

```bash
pip install build twine
python -m build
twine check dist/*
```

## Coding conventions

- Keep the README and user-facing docs **free of emojis**.
- Match the existing style: type hints, `from __future__ import annotations`,
  small focused functions, and docstrings that explain *why*.
- Do not break existing design decisions without discussion: the output slug
  (`slugify`), the `% ===== Page N =====` page markers, the chapters begin/end
  block, and the `default` / `dense` profiles.
- Keep pure logic in importable functions (easy to unit test); side effects and
  CLI wiring stay thin.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add cost estimation to the convert command
fix: handle empty page range in resolve_range
docs: clarify chapter config format
test: cover the rate-limit backoff path
```

Keep the subject in the imperative mood and under ~72 characters; add a body when
the change needs context.

## Reporting bugs and requesting features

Open an issue using the templates under
[`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE). For bugs, include the exact
command you ran, the model/profile, and the relevant lines from
`output/<slug>/log.txt`.

## Code of conduct

Participation in this project is governed by the
[Code of Conduct](CODE_OF_CONDUCT.md).
