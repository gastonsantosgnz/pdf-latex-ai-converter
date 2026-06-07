---
name: pdf-to-latex-claude
description: Convert a math-heavy PDF to compilable LaTeX using Claude's own vision in this Claude Code session (the user's Claude subscription) instead of the OpenAI API or `claude -p`. Use when the user wants to convert a PDF (e.g. a textbook in sources/) to LaTeX "with Claude", "from the chat", "using my subscription", or "without OpenAI cost". Reuses the pdf2latex machinery (render, assemble, compile, validate) and is resumable.
---

# Convert a PDF to LaTeX with Claude, in-session

You convert the PDF yourself: you **see** each page image (Read) and **write** its
LaTeX. There is NO OpenAI API call and NO `claude -p` subprocess — the vision is
you, in this session, billed to the user's Claude subscription. The deterministic
parts (render, assemble, compile, validate, resume) come from the `pdf2latex` CLI.

Run all commands from the repo root with the venv. Set `PDF2LATEX_OUTPUT_DIR` if the
user keeps output outside the repo (ask if unsure; otherwise the default `./output`).

## Steps

1. **Scope.** Confirm the PDF (a file in `sources/`) and an optional page range.
   For a large book, work in **batches of ~20-40 pages** and tell the user it is
   resumable (already-done pages are skipped). Do not silently commit to hundreds of
   pages in one go.

2. **Load the conversion rules — the single source of truth.** Read them and follow
   them EXACTLY when writing LaTeX:
   ```bash
   .venv/bin/python -c "from pdf2latex.prompts import build_system_prompt; print(build_system_prompt())"
   ```
   Key points: wrap every `_`/`^` in `\( \)`; tabular/array column count = max `&` in a
   row + 1; never put a whole page of figures in ONE unbreakable tabular/minipage (use
   a separate minipage pair per item so pages break naturally); reproduce diagrams with
   `tikzpicture`; escape `_ # % &` in text; output ONLY the page body (no preamble,
   no `\documentclass`, no code fences).

3. **Render the pending pages to images:**
   ```bash
   .venv/bin/pdf2latex render-pages "<PDF>" [--start N --end M]
   ```
   It prints, per pending page, the PNG path and the target `.tex` path, e.g.
   `page 58: .../_render/page_0058.png  ->  .../pages/page_0058.tex`.

4. **Transcribe each page.** For every rendered page, in order:
   - **Read** the PNG. If the page is dense or the text is tiny, render a higher-scale
     crop with pdfium and Read that to get details right (angles, subscripts, table cells).
   - **Write** the target `page_NNNN.tex` with a faithful, complete LaTeX transcription
     of that page (all text, math, tables, diagrams — never summarize), following the
     rules from step 2.
   - Delete the page's PNG when done (optional; assemble ignores non-`.tex` files).

5. **Assemble and compile** (compile assembles first):
   ```bash
   .venv/bin/pdf2latex compile "<PDF>"
   ```
   If some pages fail, open those `.tex`, fix the LaTeX, and recompile. The pages you
   already wrote are kept, so you never redo good work.

6. **Report**: pages converted this batch, whether the PDF compiled, and anything in
   `needs-review.txt`. Offer the next batch.

## Notes
- Resumable: a page with a non-empty `page_NNNN.tex` is skipped, so stop/continue freely.
- This spends the Claude subscription (this session), not OpenAI credit.
- For a fully automated run with the OpenAI API instead, use `pdf2latex convert "<PDF>"`
  (or `--engine claude-code` to drive a headless `claude -p`, which needs the `claude`
  CLI authenticated for headless use).
