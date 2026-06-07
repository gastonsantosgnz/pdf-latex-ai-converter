# pdf-latex-ai-converter

[![CI](https://github.com/gastonsantosgnz/pdf-latex-ai-converter/actions/workflows/ci.yml/badge.svg)](https://github.com/gastonsantosgnz/pdf-latex-ai-converter/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

Convert **math-heavy PDFs into clean, compilable LaTeX** — page by page — using a
vision LLM (OpenAI GPT-4o by default).

Unlike a plain OCR/text dump, this tool is tuned to **detect and reconstruct
mathematical structure**: inline and display equations, vertically aligned
operations, `tabular` tables, `tikz` diagrams, number lines, geometric figures
and didactic layouts. It preserves the document's **original language** and
heading hierarchy.

> Built originally to digitize Spanish-language math textbooks, but works for any
> language and any structured PDF.

---

## Features

- **Page-by-page conversion** with a vision model (`detail: high`).
- **Math-first prompt**: correct `\(...\)` / `\[...\]`, `array`/`aligned`,
  `\phantom` alignment, escaped currency (`\$`), safe exponents, no nested
  display-math bugs.
- **Tables & diagrams**: faithful `tabular`; diagrams rebuilt with `tikz`.
- **Profiles**: `default` and `dense` (textbooks full of tables/photos — skips
  decorative photos, keeps academic figures, drops decorative colors).
- **Resumable & batched**: already-converted pages are skipped; convert in
  batches to stay under rate limits.
- **Assembling**: stitches pages into one monolithic `.tex` plus a ready-to-build
  `standalone` document (title page + table of contents).
- **Chapter splitting**: by an explicit JSON config or auto (blank-page markers).
- **One-command compile** to PDF (`pdflatex`).

## Project layout

```
pdf-latex-ai-converter/
├── sources/                 # drop your PDFs here
├── output/                  # everything generated lands here (git-ignored)
│   └── <Your-PDF-slug>/
│       ├── pages/           # page_0001.tex, page_0001.usage.txt, ...
│       ├── <slug>.tex             # monolithic assembly
│       ├── <slug>-standalone.tex  # compilable document
│       ├── chapters/        # chapter_01_*.tex, ... (after split)
│       └── log.txt
├── src/pdf2latex/           # the Python package
├── examples/                # example chapter config
├── requirements.txt
├── pyproject.toml
└── .env.example
```

## Quick start

### 1. Install

```bash
git clone <your-fork-url> pdf-latex-ai-converter
cd pdf-latex-ai-converter

python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

pip install -e .          # or: pip install -r requirements.txt
```

### 2. Add your API key

```bash
cp .env.example .env       # Windows: copy .env.example .env
# edit .env and set OPENAI_API_KEY=sk-...
```

### 3. Drop a PDF in `sources/` and convert

```bash
# See what's available
pdf2latex list

# Preview scope and approximate cost first (renders pages, makes zero API calls)
pdf2latex convert "My Book.pdf" --profile dense --dry-run

# Convert the whole document (small PDFs)
pdf2latex convert "My Book.pdf" --profile dense

# Or convert in batches of 100 pages (recommended for large books)
pdf2latex convert "My Book.pdf" --batch 1 --profile dense
pdf2latex convert "My Book.pdf" --batch 2 --profile dense
# ...repeat until done (the tool tells you the next batch)
```

> If you didn't `pip install`, run it as a module:
> `python -m pdf2latex convert "My Book.pdf"`

### 4. (Optional) Split into chapters

```bash
# Explicit chapters from a JSON config (see examples/chapters.example.json)
pdf2latex split "My Book" --config examples/chapters.example.json

# Or automatically, using blank pages as chapter separators
pdf2latex split "My Book" --auto
```

### 5. Compile to PDF

Requires a LaTeX distribution (TeX Live / MiKTeX) on your `PATH`.

```bash
pdf2latex compile "My Book"
# -> output/My-Book/My-Book-standalone.pdf
```

## Command reference

| Command | What it does |
|---|---|
| `pdf2latex list` | List PDFs in `sources/`. |
| `pdf2latex convert <pdf>` | Convert pages → `.tex`, then assemble. Flags: `--profile`, `--batch`, `--batch-size`, `--start`, `--end`, `--model`, `--max-tokens`, `--scale`, `--workers`, `--rpm`, `--tpm`, `--title`, `--subtitle`, `--dry-run`, `--yes/-y`. |
| `pdf2latex assemble <pdf\|slug>` | Rebuild the monolith + standalone from existing pages. |
| `pdf2latex split <pdf\|slug>` | Split into chapters: `--config <file.json>` or `--auto`. |
| `pdf2latex compile <pdf\|slug>` | Compile the standalone `.tex` to PDF: `--engine`, `--runs`. |

`<slug>` is the folder name created under `output/` (e.g. `My-Book`).

## Chapter config format

```json
{
  "title": "My Mathematics Book",
  "subtitle": "LaTeX edition",
  "chapters": [
    { "start_page": 1,  "slug": "intro",     "title": "1. Introduction" },
    { "start_page": 25, "slug": "equations", "title": "2. Equations" }
  ]
}
```

`start_page` is the **PDF page number** where the chapter begins; the chapter ends
right before the next chapter's start.

## How it works

```
sources/Book.pdf
      │  render each page → PNG (pypdfium2)
      ▼
vision LLM (OpenAI) with a math-aware system prompt
      │  → LaTeX per page
      ▼
output/Book/pages/page_NNNN.tex
      │  concatenate (assemble)
      ▼
output/Book/Book.tex  ──split──▶  output/Book/chapters/*.tex
      │
      ▼
output/Book/Book-standalone.tex  ──pdflatex──▶  Book-standalone.pdf
```

## Tips & cost control

- Use `--profile dense` for textbooks with many tables, diagrams and photos.
- Use `--batch N` to convert large books in chunks and resume safely.
- Pages convert concurrently (default `--workers 4`), which cuts wall-clock time
  on large books. Use `--workers 1` for strictly sequential behaviour. If your
  account hits provider rate limits, cap throughput with `--rpm` (requests per
  minute) and/or `--tpm` (tokens per minute); 429s are also retried with
  exponential backoff automatically.
- Preview before you spend: `pdf2latex convert <pdf> --dry-run` prints a
  pre-flight summary (pages in range, model, profile and an **approximate** cost
  range), render-validates the pages and makes **zero API calls**.
- Every `convert` shows that pre-flight summary and asks for confirmation before
  spending. Pass `--yes` (or `-y`) to skip the prompt in scripts and CI.
- The cost figure is an estimate. Override the per-model price table without
  editing code by pointing `PDF2LATEX_PRICES` at a JSON file, where each value is
  `[input_usd_per_1M_tokens, output_usd_per_1M_tokens]`:

  ```bash
  echo '{ "gpt-4o": [2.5, 10.0], "my-model": [1.0, 3.0] }' > prices.json
  PDF2LATEX_PRICES=prices.json pdf2latex convert "My Book.pdf" --dry-run
  ```

- On an interactive terminal a live progress bar shows a running token/cost
  tally; in non-interactive runs (CI, redirected logs) it degrades to the plain
  per-page log lines so nothing is garbled.
- The model can occasionally produce a `tabular` whose column count doesn't match
  a row, or a list that needs closing. Check `output/<slug>/log.txt` and the
  `pdflatex` log; fix the few flagged pages by editing the page `.tex`.
- The standalone preamble loads missing images in `draft` mode (boxes instead of
  errors). Remove `\setkeys{Gin}{draft}` once you add real image files.

## Notes

- Costs depend on your model and page count; each page is one vision request.
- Output is **machine-generated** and may need light manual cleanup, especially
  on complex diagrams.
- The system prompt lives in `src/pdf2latex/prompts.py` — tweak it for your domain.

## License

MIT — see [LICENSE](LICENSE).
