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

5. **Validate per page, then assemble and compile.** `compile` does NOT re-assemble —
   always run `assemble` first so the monolith includes every current page:
   ```bash
   .venv/bin/pdf2latex assemble "<PDF>"
   .venv/bin/pdf2latex compile  "<PDF>"
   ```
   A single full compile halts at the FIRST bad page and hides the rest. To pinpoint
   every broken page at once, compile each page alone (in parallel) against the book's
   standalone preamble — see **Per-page validation** below. Fix each failing `.tex` and
   recompile; already-good pages are untouched, so you never redo good work.

6. **Report**: pages converted this batch, whether the PDF compiled, and anything in
   `needs-review.txt`. Offer the next batch.

## Fleet mode (large books, many pages)

For a big batch (tens to hundreds of pages) you do NOT have to transcribe serially.
Fan out with the **Workflow** tool: one agent per small batch (~5 pages). Each agent
loads the rules, renders its pages, reads them (hi-res crops when dense) and writes
each `page_NNNN.tex`. Disjoint page sets mean no write conflicts (no worktrees needed).
A 255-page dense geometry batch converted this way in ~35 min with 51 agents, and only
2 of those pages had a compile error. Tips:
- Write a tiny shared render helper that, given a page number, emits the full-page PNG
  plus hi-res top/bottom crops (cap width at ~1900 px so `Read` accepts them).
- Generate the page list **inside** the workflow script (a `for` loop), not via `args`
  — `args` can arrive empty (then the whole run no-ops in milliseconds).
- Give each agent the exact rules, the page-style template, the available packages, and
  the MUST-COMPILE constraints; have it return `{written:[...], flagged:[...]}` where
  `flagged` lists pages whose diagrams it could only approximate (for a later visual pass).

## Per-page validation (find every broken page at once)

Compile each page alone against the standalone preamble, in parallel (e.g. a
`ThreadPoolExecutor`), with `-halt-on-error`, and report the first `! ...` line per
failing page. This isolates syntax errors far faster than one giant compile that stops
at the first one.

## Common compile errors -> quick fixes

- `Something's wrong--perhaps a missing \item`: `\begin{enumerate}[a)]` needs the
  `enumerate`/`enumitem` shorthand. Use plain `\begin{enumerate}` with `\item[a)]`, or
  load `\usepackage[shortlabels]{enumitem}`.
- `Misplaced \noalign`: `\centering` inside a `p{}` cell redefines `\\`. Drop it, or use
  a `>{\centering\arraybackslash}p{...}` column.
- `I do not know the key '/tikz/name path'`: add `\usetikzlibrary{intersections}` to the
  standalone preamble (`write_standalone` preserves manual preamble tweaks).
- `No shape named 'O' is known`: a tikz coordinate is used before it is defined. Add
  `\coordinate (O) at (0,0);` before the first `(O)` reference.
- `Undefined control sequence` inside tikz: usually a broken `let ... \p1` / `\x{...}`
  expression. Replace with an explicit `\coordinate (D) at (x,y);`.
- `Dimension too large` in a `plot`: `sin(... r)` with large radian arguments (or `exp`)
  overflows pgfmath. Use degrees and bounded arguments, e.g. `sin(\x*150)`.

## Notes
- Resumable: a page with a non-empty `page_NNNN.tex` is skipped, so stop/continue freely.
- This spends the Claude subscription (this session), not OpenAI credit.
- For a fully automated run with the OpenAI API instead, use `pdf2latex convert "<PDF>"`
  (or `--engine claude-code` to drive a headless `claude -p`, which needs the `claude`
  CLI authenticated for headless use).
