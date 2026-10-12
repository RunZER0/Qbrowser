"""Authenticated document intake, creation, conversion, previews and immutable downloads."""
from __future__ import annotations

import html
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from qbrowser.artifact_vault import (
    create_output, convert_output, get_revision, list_revisions,
    open_snapshot, purge_expired, record_file, restore_revision, safe_name,
)
from qbrowser.document_engine import inspect_document, preview_png, preview_html
from qbrowser.store import JobStore

router = APIRouter(prefix="/api/jobs/{job_id}/documents", tags=["Documents"])
MAX_UPLOAD_REQUEST_BYTES = 101 * 1024 * 1024


def _safe(fn):
    try:
        return fn()
    except (KeyError, FileNotFoundError):
        raise HTTPException(status_code=404, detail="Document or job not found")
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


class CreateDocument(BaseModel):
    filename: str = Field(min_length=1, max_length=160)
    data: dict


class ConvertDocument(BaseModel):
    source_role: str
    source_filename: str
    destination_filename: str
    version: int | None = None


class RetentionRequest(BaseModel):
    days: int = Field(ge=7, le=36500)
    confirm: bool = False


class RestoreDocument(BaseModel):
    version: int = Field(ge=1)


@router.get("")
def versions(job_id: str, role: str | None = None):
    return _safe(lambda: list_revisions(JobStore(), job_id, role=role))


@router.post("/upload/{role}", status_code=201)
def upload(job_id: str, role: str, request: Request, file: UploadFile = File(...)):
    try:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc
            if declared_size < 0:
                raise HTTPException(status_code=400, detail="Invalid Content-Length")
            if declared_size > MAX_UPLOAD_REQUEST_BYTES:
                raise HTTPException(status_code=413, detail="Upload exceeds the 100 MB file limit")
        return _safe(lambda: record_file(JobStore(), job_id, file.filename or "", role, file.file))
    finally:
        file.file.close()


@router.post("/generate", status_code=201)
def generate(job_id: str, request: CreateDocument):
    return _safe(lambda: create_output(JobStore(), job_id, request.filename, request.data))


@router.post("/convert", status_code=201)
def convert(job_id: str, request: ConvertDocument):
    return _safe(lambda: convert_output(
        JobStore(), job_id, request.source_role, request.source_filename,
        request.destination_filename, version=request.version,
    ))


@router.post("/retention")
def retention(job_id: str, request: RetentionRequest):
    JobStore().get(job_id)
    if request.confirm:
        # Retention policy is global, never hidden inside one job request.
        raise HTTPException(status_code=400, detail="Use local CLI for confirmed global deletion")
    return _safe(lambda: purge_expired(JobStore(), days=request.days, dry_run=True))


@router.get("/{role}/{filename}/versions")
def file_versions(job_id: str, role: str, filename: str):
    return _safe(lambda: [x for x in list_revisions(JobStore(), job_id, role=role)
                          if x["filename"] == safe_name(filename)])


@router.post("/{role}/{filename}/restore")
def restore(job_id: str, role: str, filename: str, request: RestoreDocument):
    return _safe(lambda: restore_revision(
        JobStore(), job_id, role, filename, request.version,
    ))


from contextlib import contextmanager
import tempfile
import shutil


@contextmanager
def typed_source(path: Path, filename: str):
    with tempfile.TemporaryDirectory() as temp:
        local = Path(temp) / safe_name(filename)
        shutil.copyfile(path, local)
        yield local


@router.get("/{role}/{filename}/diff")
def diff(job_id: str, role: str, filename: str, older: int, newer: int):
    def run():
        import difflib
        from qbrowser.document_engine import extract_text
        _, left = open_snapshot(JobStore(), job_id, role, filename, older)
        _, right = open_snapshot(JobStore(), job_id, role, filename, newer)
        with typed_source(left, filename) as old, typed_source(right, filename) as new:
            before = extract_text(old, limit=200000)
            after = extract_text(new, limit=200000)
        changes = "\n".join(difflib.unified_diff(
            before.splitlines(), after.splitlines(),
            fromfile=f"{filename}@v{older}", tofile=f"{filename}@v{newer}",
            lineterm="",
        ))
        return {"filename": filename, "older": older, "newer": newer,
                "unified_diff": changes[:50000], "truncated": len(changes) > 50000}
    return _safe(run)


@router.get("/{role}/{filename}/review")
def review(job_id: str, role: str, filename: str, version: int | None = None):
    def run():
        item, path = open_snapshot(JobStore(), job_id, role, filename, version)
        with typed_source(path, filename) as local:
            analysis = inspect_document(local)
        return {"revision": item, "analysis": analysis,
                "source_verification": "requires independent external research"}
    return _safe(run)


@router.get("/{role}/{filename}/preview")
def preview(job_id: str, role: str, filename: str, version: int | None = None,
            page: int = 0):
    def run():
        item, path = open_snapshot(JobStore(), job_id, role, filename, version)
        with typed_source(path, filename) as local:
            if local.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg"}:
                return Response(preview_png(local, page=page), media_type="image/png",
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
            content = preview_html(local)
            markup = (
                "<!doctype html><html><head><meta charset='utf-8'><title>Document Preview</title>"
                "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                "<style>body{font:16px system-ui;max-width:980px;margin:30px auto;padding:0 18px;}"
                "table{border-collapse:collapse;margin:16px 0;}td{padding:5px 9px;}"
                "section{border-bottom:1px solid #bbb;padding:12px 0;}"
                "pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>"
                "</head><body><h2>" + html.escape(item["filename"]) + "</h2>"
                + content + "<p>Semantic preview. Exact Office page layout not rendered.</p></body></html>"
            )
            return HTMLResponse(markup, headers={
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
                "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            })
    return _safe(run)


@router.get("/{role}/{filename}/download")
def download(job_id: str, role: str, filename: str, version: int | None = None):
    def run():
        item, path = open_snapshot(JobStore(), job_id, role, filename, version)
        return FileResponse(path, media_type=item["mime"], filename=item["filename"],
            content_disposition_type="attachment", headers={
                "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            })
    return _safe(run)
