"""A small optional FastAPI app that drives conversions from the browser.

Installed via the ``[web]`` extra and launched with ``pdf2latex serve``. It wraps
the existing pipeline: pick or upload a PDF, preview the cost with a dry run,
convert with live progress over Server-Sent Events, and download the results.

The app is deliberately single-user and in-memory: it is a local companion to the
CLI, not a hosted service.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse

from ..layout import OUTPUT_DIR, SOURCES_DIR, BookPaths, resolve_source

_STATIC = Path(__file__).resolve().parent / "static"
_END = {"type": "_end"}


@dataclass
class Job:
    """In-memory state for one conversion started from the UI."""

    id: str
    status: str = "running"  # running | done | error | aborted
    events: list[dict] = field(default_factory=list)
    queue: queue.Queue = field(default_factory=queue.Queue)
    result: dict | None = None
    error: str | None = None
    paths: BookPaths | None = None
    stop: threading.Event = field(default_factory=threading.Event)


def _output_targets(paths: BookPaths) -> dict:
    return {
        "pdf": paths.standalone_tex.with_suffix(".pdf"),
        "monolith": paths.monolith_tex,
        "standalone": paths.standalone_tex,
        "needs-review": paths.out_dir / "needs-review.txt",
        "log": paths.log_file,
    }


def _existing_files(paths: BookPaths) -> list[dict]:
    return [{"kind": kind, "name": p.name} for kind, p in _output_targets(paths).items() if p.exists()]


def _needs_review_count(paths: BookPaths) -> int:
    report = paths.out_dir / "needs-review.txt"
    if not report.exists():
        return 0
    head = report.read_text(encoding="utf-8").splitlines()[:1]
    if head and head[0].startswith("Pages needing review:"):
        try:
            return int(head[0].split(":", 1)[1])
        except ValueError:
            return 0
    return 0


def _compile_errors(paths: BookPaths) -> list[dict]:
    """Parse a failed pdflatex log into ``[{page, error, snippet}]`` (one per page)."""
    from ..compile import compile_errors_by_page

    return compile_errors_by_page(paths)


def _file_opener() -> list[str]:
    """Prefer a code editor (Cursor/VS Code), else the OS default app."""
    for editor in ("cursor", "code"):
        if shutil.which(editor):
            return [editor]
    if sys.platform == "darwin":
        return ["open"]
    if sys.platform.startswith("win"):
        return ["cmd", "/c", "start", ""]
    return ["xdg-open"]


def _result_payload(paths: BookPaths, *, dry_run: bool) -> dict:
    if dry_run:
        return {"dry_run": True, "slug": paths.slug}
    return {
        "dry_run": False,
        "slug": paths.slug,
        "out_dir": str(paths.out_dir),
        "files": _existing_files(paths),
    }


class JobManager:
    """Track conversion jobs, each running on its own daemon thread."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        return job

    def cancel(self, job_id: str) -> None:
        """Ask a running job to stop after its in-flight pages finish."""
        self.get(job_id).stop.set()

    def start(self, params: dict) -> Job:
        job = Job(id=uuid.uuid4().hex[:12])
        with self._lock:
            self._jobs[job.id] = job
        threading.Thread(target=self._run, args=(job, params), daemon=True).start()
        return job

    def _run(self, job: Job, params: dict) -> None:
        from ..converter import convert_pdf  # lazy import keeps the web layer light

        def on_event(ev: dict) -> None:
            job.events.append(ev)
            job.queue.put(ev)
            if ev.get("type") == "aborted":
                job.status = "aborted"

        try:
            paths = convert_pdf(
                resolve_source(params["source"], sources_dir=SOURCES_DIR),
                model=params["model"],
                workers=params["workers"],
                start=params.get("start"),
                end=params.get("end"),
                engine=params.get("engine", "openai"),
                dry_run=params["dry_run"],
                repair=params["repair"],
                assume_yes=True,
                on_event=on_event,
                should_stop=job.stop.is_set,
            )
            job.paths = paths
            # Publish the result before flipping status to "done" so a client
            # that keys off status never observes a finished job with no result.
            job.result = _result_payload(paths, dry_run=params["dry_run"])
            if job.status == "running":
                job.status = "done"
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - surfaced to the client
            job.status = "error"
            job.error = str(exc)
            job.queue.put({"type": "error", "message": str(exc)})
        finally:
            job.queue.put(_END)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def create_app() -> FastAPI:
    from dotenv import load_dotenv

    load_dotenv()  # pick up OPENAI_API_KEY from .env like the CLI does
    app = FastAPI(title="pdf2latex", docs_url=None, redoc_url=None)
    manager = JobManager()

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(_STATIC / "index.html")

    @app.get("/api/info")
    def info() -> dict:
        from ..pricing import load_prices

        default_model = os.environ.get("PDF2LATEX_MODEL", "gpt-5")
        models = sorted(set(load_prices()) | {default_model})
        from ..claude_engine import claude_available

        return {
            "api_key_set": bool(os.environ.get("OPENAI_API_KEY")),
            "default_model": default_model,
            "models": models,
            "claude_available": claude_available(),
            "output_dir": str(OUTPUT_DIR),
            "sources_dir": str(SOURCES_DIR),
        }

    @app.get("/api/estimate")
    def estimate(source: str) -> dict:
        from pypdf import PdfReader

        from ..pricing import estimate_cost, load_prices

        try:
            pdf = resolve_source(source, sources_dir=SOURCES_DIR)
        except SystemExit as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        paths = BookPaths.for_source(pdf, output_root=OUTPUT_DIR)
        try:
            total = len(PdfReader(str(pdf)).pages)
        except Exception:  # noqa: BLE001 - unreadable PDF -> 0 pages
            total = 0
        done = (
            sum(1 for p in paths.pages_dir.glob("page_*.tex") if p.stat().st_size > 0)
            if paths.pages_dir.exists()
            else 0
        )
        pending = max(total - done, 0)
        prices = load_prices()
        models = [
            {
                "model": m,
                "usd_low": est.usd_low,
                "usd_high": est.usd_high,
                "known_model": est.known_model,
            }
            for m in sorted(prices)
            for est in [estimate_cost(m, pending, prices=prices)]
        ]
        return {
            "name": pdf.name,
            "slug": paths.slug,
            "total": total,
            "done": done,
            "pending": pending,
            "models": models,
        }

    @app.get("/api/page/{slug}/{number}")
    def page_tex(slug: str, number: int) -> dict:
        paths = BookPaths.for_source(SOURCES_DIR / f"{slug}.pdf", output_root=OUTPUT_DIR)
        tex = paths.page_tex(number)
        if not tex.exists():
            raise HTTPException(status_code=404, detail="page not converted yet")
        return {"page": number, "slug": slug, "latex": tex.read_text(encoding="utf-8")}

    @app.get("/api/render")
    def render(source: str, page: int, scale: float = 1.5) -> Response:
        import base64

        from ..worker import render_page_to_base64

        try:
            pdf = resolve_source(source, sources_dir=SOURCES_DIR)
        except SystemExit as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            b64 = render_page_to_base64(str(pdf), page_index=page - 1, scale=scale)
        except Exception as exc:  # noqa: BLE001 - bad page index or unreadable PDF
            raise HTTPException(status_code=404, detail=f"could not render page {page}") from exc
        return Response(content=base64.b64decode(b64), media_type="image/png")

    @app.get("/api/pdfs")
    def list_pdfs() -> dict:
        SOURCES_DIR.mkdir(parents=True, exist_ok=True)
        return {"pdfs": sorted(p.name for p in SOURCES_DIR.glob("*.pdf"))}

    @app.post("/api/reveal")
    def reveal(slug: str | None = None) -> dict:
        """Open the output folder (or a book's subfolder) in the OS file manager."""
        root = OUTPUT_DIR.resolve()
        target = (root / slug).resolve() if slug else root
        if target != root and root not in target.parents:
            raise HTTPException(status_code=400, detail="invalid path")
        if not target.exists():
            target = root
        root.mkdir(parents=True, exist_ok=True)
        if sys.platform == "darwin":
            cmd = ["open", str(target)]
        elif sys.platform.startswith("win"):
            cmd = ["explorer", str(target)]
        else:
            cmd = ["xdg-open", str(target)]
        subprocess.Popen(cmd)  # noqa: S603 - local single-user tool, path is sandboxed
        return {"opened": str(target)}

    @app.post("/api/compile")
    def compile_to_pdf(slug: str) -> dict:
        from ..compile import compile_pdf

        paths = BookPaths.for_source(SOURCES_DIR / f"{slug}.pdf", output_root=OUTPUT_DIR)
        if not paths.standalone_tex.exists():
            raise HTTPException(status_code=404, detail="Nothing to compile yet — convert first.")
        try:
            # Lenient: produce a PDF even if a few machine-generated pages have errors.
            pdf = compile_pdf(paths.standalone_tex, halt_on_error=False)
        except SystemExit as exc:
            return {
                "success": False,
                "slug": slug,
                "errors": _compile_errors(paths),
                "message": str(exc),
            }
        return {"success": True, "slug": slug, "pdf": pdf.name}

    @app.post("/api/open-file")
    def open_file(slug: str, page: int) -> dict:
        paths = BookPaths.for_source(SOURCES_DIR / f"{slug}.pdf", output_root=OUTPUT_DIR)
        tex = paths.page_tex(page).resolve()
        if OUTPUT_DIR.resolve() not in tex.parents:
            raise HTTPException(status_code=400, detail="invalid path")
        if not tex.exists():
            raise HTTPException(status_code=404, detail="page not found")
        subprocess.Popen([*_file_opener(), str(tex)])  # noqa: S603 - sandboxed local path
        return {"opened": str(tex)}

    @app.post("/api/repair")
    def repair_page(slug: str, page: int, model: str = "gpt-4o") -> dict:
        from ..assemble import assemble_monolith, write_standalone
        from ..worker import make_client, render_page_to_base64, repair_latex, repair_with_image

        source_pdf = SOURCES_DIR / f"{slug}.pdf"
        paths = BookPaths.for_source(source_pdf, output_root=OUTPUT_DIR)
        tex = paths.page_tex(page)
        if not tex.exists():
            raise HTTPException(status_code=404, detail="page not found")
        latex = tex.read_text("utf-8", errors="replace")
        errors = {e["page"]: e["error"] for e in _compile_errors(paths)}
        problem = errors.get(page, "This page fails to compile in LaTeX.")
        problems = [
            f"LaTeX compile error: {problem}. Fix the page so it compiles "
            "(common causes: a malformed table/array, math outside math mode, "
            "or an unbalanced \\left/\\right)."
        ]
        try:
            client = make_client()
            # Prefer a vision repair (image + error): it can rebuild a broken table
            # from the original page. Fall back to text-only if rendering fails.
            try:
                img = render_page_to_base64(str(source_pdf), page_index=page - 1)
                result = repair_with_image(client, img, latex, problems, model=model)
            except Exception:  # noqa: BLE001 - missing/unrenderable source -> text repair
                result = repair_latex(client, latex, problems, model=model)
        except SystemExit as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - surface API errors to the client
            raise HTTPException(status_code=502, detail=f"AI repair failed: {exc}") from exc
        if result.rejected:
            # The fix would have damaged the page: leave the original untouched.
            return {
                "slug": slug,
                "page": page,
                "tokens": result.total_tokens,
                "applied": False,
                "rejected": result.rejected,
            }
        backup = tex.with_suffix(".tex.bak")
        backup.write_text(latex, encoding="utf-8")  # reversible: keep the pre-repair page
        tex.write_text(result.latex, encoding="utf-8")
        assemble_monolith(paths)
        write_standalone(paths)
        return {"slug": slug, "page": page, "tokens": result.total_tokens, "applied": True}

    @app.post("/api/restore")
    def restore_page(slug: str, page: int) -> dict:
        """Revert a page to the backup saved before the last applied AI repair."""
        from ..assemble import assemble_monolith, write_standalone

        paths = BookPaths.for_source(SOURCES_DIR / f"{slug}.pdf", output_root=OUTPUT_DIR)
        tex = paths.page_tex(page)
        backup = tex.with_suffix(".tex.bak")
        if not backup.exists():
            raise HTTPException(status_code=404, detail="no backup to restore for this page")
        tex.write_text(backup.read_text("utf-8", errors="replace"), encoding="utf-8")
        backup.unlink(missing_ok=True)
        assemble_monolith(paths)
        write_standalone(paths)
        return {"slug": slug, "page": page, "restored": True}

    @app.post("/api/reset")
    def reset_book(slug: str) -> dict:
        """Delete a book's converted output so it can be reconverted from scratch.

        Removes the whole output folder for the slug (pages, monolith, standalone,
        PDF, logs). The source PDF in sources/ is never touched.
        """
        paths = BookPaths.for_source(SOURCES_DIR / f"{slug}.pdf", output_root=OUTPUT_DIR)
        if paths.out_dir.exists():
            shutil.rmtree(paths.out_dir, ignore_errors=True)
        return {"slug": slug, "reset": True}

    @app.get("/api/library")
    def library() -> dict:
        from pypdf import PdfReader

        SOURCES_DIR.mkdir(parents=True, exist_ok=True)
        items: list[dict] = []
        summary = {"total": 0, "done": 0, "in_progress": 0, "pending": 0}
        for pdf in sorted(SOURCES_DIR.glob("*.pdf")):
            paths = BookPaths.for_source(pdf, output_root=OUTPUT_DIR)
            try:
                total = len(PdfReader(str(pdf)).pages)
            except Exception:  # noqa: BLE001 - an unreadable PDF just shows 0 pages
                total = 0
            done = (
                sum(1 for p in paths.pages_dir.glob("page_*.tex") if p.stat().st_size > 0)
                if paths.pages_dir.exists()
                else 0
            )
            status = "pending" if done == 0 else "done" if total and done >= total else "in_progress"
            summary[status] += 1
            summary["total"] += 1
            items.append(
                {
                    "name": pdf.name,
                    "slug": paths.slug,
                    "total": total,
                    "done": done,
                    "status": status,
                    "needs_review": _needs_review_count(paths),
                    "files": _existing_files(paths),
                }
            )
        return {"items": items, "summary": summary}

    @app.get("/api/output/{slug}/{kind}")
    def output_file(slug: str, kind: str) -> FileResponse:
        paths = BookPaths.for_source(SOURCES_DIR / f"{slug}.pdf", output_root=OUTPUT_DIR)
        target = _output_targets(paths).get(kind)
        if target is None or not target.exists():
            raise HTTPException(status_code=404, detail="file not found")
        return FileResponse(target, filename=target.name)

    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...)) -> dict:
        name = Path(file.filename or "upload.pdf").name
        if not name.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Only .pdf files are accepted")
        SOURCES_DIR.mkdir(parents=True, exist_ok=True)
        (SOURCES_DIR / name).write_bytes(await file.read())
        return {"name": name}

    @app.post("/api/convert")
    def start_convert(
        source: str = Form(...),
        model: str = Form("gpt-5"),
        engine: str = Form("openai"),
        workers: int = Form(4),
        dry_run: bool = Form(False),
        repair: bool = Form(False),
        start: int | None = Form(None),
        end: int | None = Form(None),
    ) -> dict:
        try:
            resolve_source(source, sources_dir=SOURCES_DIR)
        except SystemExit as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        job = manager.start(
            {
                "source": source,
                "model": model,
                "engine": engine,
                "workers": workers,
                "dry_run": dry_run,
                "repair": repair,
                "start": start,
                "end": end,
            }
        )
        return {"job_id": job.id}

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> dict:
        job = manager.get(job_id)
        return {
            "id": job.id,
            "status": job.status,
            "error": job.error,
            "events": job.events,
            "result": job.result,
        }

    @app.post("/api/jobs/{job_id}/stop")
    def job_stop(job_id: str) -> dict:
        """Ask a running conversion to stop; pages already done are kept."""
        manager.cancel(job_id)
        return {"id": job_id, "stopping": True}

    @app.get("/api/jobs/{job_id}/events")
    def job_events(job_id: str) -> StreamingResponse:
        job = manager.get(job_id)

        def stream():
            while True:
                ev = job.queue.get()
                if ev.get("type") == "_end":
                    yield _sse({"type": "_end", "status": job.status})
                    break
                yield _sse(ev)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/download/{job_id}/{kind}")
    def download(job_id: str, kind: str) -> FileResponse:
        job = manager.get(job_id)
        paths = job.paths or BookPaths.for_source(
            SOURCES_DIR / f"{job_id}.pdf", output_root=OUTPUT_DIR
        )
        target = _output_targets(paths).get(kind)
        if target is None or not target.exists():
            raise HTTPException(status_code=404, detail="file not found")
        return FileResponse(target, filename=target.name)

    return app


def run(host: str = "127.0.0.1", port: int = 8000) -> None:  # pragma: no cover - server entry
    import uvicorn

    print(f"pdf2latex web UI on http://{host}:{port}  (Ctrl+C to stop)")
    uvicorn.run(create_app(), host=host, port=port)
