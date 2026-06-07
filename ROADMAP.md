# Roadmap

Planned improvements for **pdf-latex-ai-converter**. Each item lists the goal,
the concrete deliverables and the acceptance criteria so contributors know when
it is "done".

Status legend: `done` · `in progress` · `planned`

---

## 1. Continuous Integration (GitHub Actions) — `done`

**Goal.** Every push and pull request is automatically linted and smoke-tested,
so broken code never reaches `main` and the repo shows a green build badge.

**Deliverables.**
- `.github/workflows/ci.yml` running on `push` and `pull_request`.
- A **lint** job using [Ruff](https://docs.astral.sh/ruff/) (`ruff check`).
- A **smoke** job across Python 3.10–3.12 that installs the package
  (`pip install -e .[dev]`) and verifies the CLI loads (`pdf2latex --help`) and
  the package imports.
- Ruff configuration in `pyproject.toml` and a `dev` optional-dependency group.

**Acceptance criteria.**
- CI passes on a clean checkout with no network/API calls.
- Lint is clean (or violations are explicitly ignored with justification).

---

## 2. Test suite with `pytest` (LLM mocked) — `done`

**Goal.** Lock down the deterministic parts of the pipeline so refactors are
safe, without spending a cent on the OpenAI API.

**Deliverables.**
- `tests/` package with unit tests for the pure logic:
  - `layout.slugify` (accents, spaces, collisions).
  - `converter.resolve_range` (batches, explicit ranges, bounds).
  - `assemble.strip_code_fences` and `assemble.iter_page_files` (ordering).
  - `splitter.split_from_config` against a tiny synthetic monolith.
- The OpenAI client is **mocked** (e.g. `unittest.mock` / `monkeypatch`) so
  `convert_image_b64` is exercised without real network calls.
- Coverage target ≥ 80% on `src/pdf2latex` (excluding thin CLI glue).
- `pytest` wired into the CI workflow from item 1.

**Acceptance criteria.**
- `pytest` runs offline and deterministically.
- A representative bad model output (mismatched `tabular`, unclosed `itemize`,
  unwrapped `$`) has at least one regression test.

---

## 3. Open-source community files & metadata — `done`

**Goal.** Make the project welcoming and credible to outside contributors.

**Deliverables.**
- `CONTRIBUTING.md` (dev setup, lint/test commands, commit conventions).
- `CODE_OF_CONDUCT.md` (Contributor Covenant).
- `CHANGELOG.md` (Keep a Changelog format, semver).
- `.github/ISSUE_TEMPLATE/` (bug report + feature request) and
  `.github/PULL_REQUEST_TEMPLATE.md`.
- Richer `pyproject.toml` metadata: `authors`, `keywords`, `classifiers`,
  `project.urls` (Homepage, Issues, Repository).
- README badges (CI status, license, Python versions).

**Acceptance criteria.**
- GitHub recognizes the community health files (Insights → Community Standards).
- Package metadata renders correctly with `python -m build` / `twine check`.

---

## 4. Cost preflight, `--dry-run` and live progress — `done`

**Goal.** No surprises on the OpenAI bill. Let users preview scope and cost
before committing to a long conversion, and follow progress while it runs.

**Deliverables.**
- A pre-flight summary for `convert`: page count in range, model, profile and an
  **approximate** cost estimate (configurable per-model price table, with a clear
  "estimate only" disclaimer).
- A `--dry-run` flag that renders/validates pages and prints the plan **without**
  calling the API.
- A `--yes/-y` flag to skip the confirmation prompt in automated runs.
- A live progress bar with a running token/cost tally (the data is already captured
  per page in the `*.usage.txt` files), plus a final summary on completion.
- Running token/cost accounting written to `log.txt` and printed on completion
  (already partially captured via `*.usage.txt`).

**Acceptance criteria.**
- `convert --dry-run` performs zero API calls and exits 0.
- The estimate is documented as approximate and easy to update when prices change.
- The progress output degrades gracefully in non-interactive runs (CI, log files).

---

## 5. Parallel page conversion with rate-limit awareness — `done`

**Goal.** Cut wall-clock time for large books from hours to minutes by converting
independent pages concurrently, without tripping the provider's rate limits.

**Deliverables.**
- A bounded `ThreadPoolExecutor` in `convert_pdf` (`--workers N`, sensible default)
  that converts pages concurrently; the job stays resumable (already-converted
  pages are still skipped).
- A token-bucket / rate limiter replacing the fixed `time.sleep(1.0)`, tuned to the
  account tier (configurable RPM/TPM) and reusing the existing exponential backoff
  in `worker._create_with_backoff`.
- Thread-safe logging (`_log` guarded by a lock) and correctly aggregated OK/FAIL
  counters.
- `--workers 1` preserves today's strictly sequential behaviour as a fallback.

**Acceptance criteria.**
- A multi-page conversion (LLM mocked) yields the same output as the sequential
  path, with no interleaved or corrupted log lines.
- `--workers 1` reproduces the current behaviour byte-for-byte.

---

## 6. LaTeX validation & auto-repair pass — `done`

**Goal.** Reduce manual cleanup to near zero by catching and fixing broken LaTeX
per page, and by telling the user exactly which pages still need a human.

**Deliverables.**
- A cheap, offline validator run on every page: balanced braces, matched
  `\begin{...}`/`\end{...}`, and unescaped `$` outside math — no API or LaTeX
  installation required.
- An optional deep check: compile each page in isolation (or run `chktex`), gated
  behind a flag so the default stays dependency-light.
- An auto-repair round-trip: on a validation/compile error, resend the page LaTeX
  plus the error message to the model asking for a minimal fix (bounded retries).
- A `needs-review.txt` report listing the pages that still fail after repair, with
  the specific reason — instead of surfacing errors only at final compile time.

**Acceptance criteria.**
- A known-bad page (mismatched `tabular`, unclosed `itemize`, unescaped `$`) is
  flagged by the offline validator (covered by the existing regression fixtures).
- The auto-repair step performs zero API calls when every page already validates.

---

## Ideas parking lot (unscheduled)

- Multi-provider backends (OpenAI-compatible `base_url`, Anthropic, Gemini).
- Structured `logging` with `--verbose` instead of `print`.
