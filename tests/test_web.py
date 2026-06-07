"""Tests for the optional FastAPI web layer (skipped if the [web] extra is absent).

The conversion pipeline is stubbed, so these tests exercise the HTTP/SSE plumbing
without any rendering or API calls.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from pdf2latex.layout import BookPaths  # noqa: E402
from pdf2latex.web.app import create_app  # noqa: E402


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    sources = tmp_path / "sources"
    sources.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr("pdf2latex.web.app.SOURCES_DIR", sources)
    monkeypatch.setattr("pdf2latex.web.app.OUTPUT_DIR", out)
    return TestClient(create_app())


def _make_pdf(path: Path, pages: int) -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    with path.open("wb") as fh:
        writer.write(fh)


def _wait_done(client: TestClient, job_id: str, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish in time")


def _stub_convert(out_root: Path):
    def fake_convert(source_pdf, *, on_event, dry_run, model, profile, workers, **_kw):
        paths = BookPaths.for_source(source_pdf, output_root=out_root)
        paths.ensure_dirs()
        on_event(
            {
                "type": "preflight",
                "to_convert": 1,
                "in_range": 1,
                "already": 0,
                "model": model,
                "workers": workers,
                "est_low": 0.0,
                "est_high": 0.01,
                "known_model": True,
            }
        )
        if dry_run:
            on_event({"type": "dry_run", "rendered": 1, "failed": 0, "pending": 1})
            return paths
        paths.monolith_tex.write_text("% ===== Page 1 =====\nX", encoding="utf-8")
        paths.standalone_tex.write_text("doc", encoding="utf-8")
        on_event({"type": "page", "page": 1, "status": "ok", "done": 1, "total": 1, "tokens": 3, "usd": 0.0})
        on_event({"type": "done", "ok": 1, "failed": 0, "tokens": 3, "usd": 0.0, "needs_review": 0})
        return paths

    return fake_convert


def test_index_served(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "pdf2latex" in r.text


def test_list_and_upload_pdfs(client: TestClient) -> None:
    assert client.get("/api/pdfs").json() == {"pdfs": []}

    r = client.post("/api/upload", files={"file": ("book.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 200 and r.json()["name"] == "book.pdf"
    assert client.get("/api/pdfs").json() == {"pdfs": ["book.pdf"]}


def test_upload_rejects_non_pdf(client: TestClient) -> None:
    r = client.post("/api/upload", files={"file": ("notes.txt", b"hi", "text/plain")})
    assert r.status_code == 400


def test_convert_unknown_source_is_400(client: TestClient) -> None:
    r = client.post("/api/convert", data={"source": "does-not-exist.pdf"})
    assert r.status_code == 400


def test_dry_run_flow(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr("pdf2latex.converter.convert_pdf", _stub_convert(tmp_path / "out"))

    r = client.post("/api/convert", data={"source": str(pdf), "dry_run": "true"})
    job_id = r.json()["job_id"]
    job = _wait_done(client, job_id)

    assert job["status"] == "done"
    assert job["result"]["dry_run"] is True
    assert any(e["type"] == "dry_run" for e in job["events"])


def test_convert_flow_and_download(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr("pdf2latex.converter.convert_pdf", _stub_convert(tmp_path / "out"))

    job_id = client.post("/api/convert", data={"source": str(pdf)}).json()["job_id"]
    job = _wait_done(client, job_id)

    assert job["status"] == "done"
    kinds = {f["kind"] for f in job["result"]["files"]}
    assert {"monolith", "standalone"} <= kinds

    dl = client.get(f"/api/download/{job_id}/monolith")
    assert dl.status_code == 200
    assert "% ===== Page 1 =====" in dl.text


def test_library_lists_and_marks_pending(client: TestClient, tmp_path: Path) -> None:
    _make_pdf(tmp_path / "sources" / "book.pdf", 3)

    data = client.get("/api/library").json()
    assert data["summary"] == {"total": 1, "done": 0, "in_progress": 0, "pending": 1}
    item = data["items"][0]
    assert item["name"] == "book.pdf"
    assert item["total"] == 3
    assert item["done"] == 0
    assert item["status"] == "pending"


def test_library_marks_done(client: TestClient, tmp_path: Path) -> None:
    _make_pdf(tmp_path / "sources" / "book.pdf", 2)
    pages = tmp_path / "out" / "book" / "pages"
    pages.mkdir(parents=True)
    (pages / "page_0001.tex").write_text("x", encoding="utf-8")
    (pages / "page_0002.tex").write_text("y", encoding="utf-8")

    item = client.get("/api/library").json()["items"][0]
    assert item["done"] == 2
    assert item["status"] == "done"


def test_output_download_by_slug(client: TestClient, tmp_path: Path) -> None:
    out_dir = tmp_path / "out" / "book"
    out_dir.mkdir(parents=True)
    (out_dir / "book.tex").write_text("MONOLITH", encoding="utf-8")

    r = client.get("/api/output/book/monolith")
    assert r.status_code == 200
    assert "MONOLITH" in r.text
    assert client.get("/api/output/book/standalone").status_code == 404


def test_sse_stream_emits_events(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr("pdf2latex.converter.convert_pdf", _stub_convert(tmp_path / "out"))

    job_id = client.post("/api/convert", data={"source": str(pdf), "dry_run": "true"}).json()["job_id"]
    _wait_done(client, job_id)

    with client.stream("GET", f"/api/jobs/{job_id}/events") as r:
        body = "".join(r.iter_text())
    assert "preflight" in body
    assert "_end" in body
