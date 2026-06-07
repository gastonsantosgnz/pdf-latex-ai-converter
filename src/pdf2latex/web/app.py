"""A small optional FastAPI app that drives conversions from the browser.

Installed via the ``[web]`` extra and launched with ``pdf2latex serve``. It wraps
the existing pipeline: pick or upload a PDF, preview the cost with a dry run,
convert with live progress over Server-Sent Events, and download the results.

The app is deliberately single-user and in-memory: it is a local companion to the
CLI, not a hosted service.
"""

from __future__ import annotations

import json
import queue
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

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


def _output_targets(paths: BookPaths) -> dict:
    return {
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
                resolve_source(params["source"]),
                model=params["model"],
                profile=params["profile"],
                workers=params["workers"],
                dry_run=params["dry_run"],
                repair=params["repair"],
                assume_yes=True,
                on_event=on_event,
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

    @app.get("/api/pdfs")
    def list_pdfs() -> dict:
        SOURCES_DIR.mkdir(parents=True, exist_ok=True)
        return {"pdfs": sorted(p.name for p in SOURCES_DIR.glob("*.pdf"))}

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
        model: str = Form("gpt-4o"),
        profile: str = Form("default"),
        workers: int = Form(4),
        dry_run: bool = Form(False),
        repair: bool = Form(False),
    ) -> dict:
        try:
            resolve_source(source)
        except SystemExit as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        job = manager.start(
            {
                "source": source,
                "model": model,
                "profile": profile,
                "workers": workers,
                "dry_run": dry_run,
                "repair": repair,
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
        targets = {
            "monolith": paths.monolith_tex,
            "standalone": paths.standalone_tex,
            "needs-review": paths.out_dir / "needs-review.txt",
            "log": paths.log_file,
        }
        target = targets.get(kind)
        if target is None or not target.exists():
            raise HTTPException(status_code=404, detail="file not found")
        return FileResponse(target, filename=target.name)

    return app


def run(host: str = "127.0.0.1", port: int = 8000) -> None:  # pragma: no cover - server entry
    import uvicorn

    print(f"pdf2latex web UI on http://{host}:{port}  (Ctrl+C to stop)")
    uvicorn.run(create_app(), host=host, port=port)
