"""Authenticated document API, verified downloads, previews and conversion."""
from __future__ import annotations

import io
import os
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from qbrowser.store import JobStore
from qbrowser.web import app


def test_uploaded_file_lifecycle_and_security():
    with TemporaryDirectory() as directory, patch.dict(os.environ, {
        "QBROWSER_HOME": directory, "QBROWSER_API_TOKEN": "private-token",
    }):
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer private-token"}
            job = client.post("/api/jobs", json={"instruction": "Write paper"},
                              headers=headers).json()
            root = f"/api/jobs/{job['id']}/documents"
            assert client.get(root).status_code == 401
            upload = client.post(root + "/upload/input", headers=headers,
                files={"file": ("assignment.txt", io.BytesIO(b"APA citations required."), "text/plain")})
            assert upload.status_code == 201, upload.text
            assert upload.json()["role"] == "input"
            assert len(client.get(root, headers=headers).json()) == 1
            assert client.get(root + "/input/assignment.txt/versions", headers=headers).json()[0]["version"] == 1
            url = root + "/input/assignment.txt/download?version=1"
            assert client.get(url).status_code == 401
            result = client.get(url, headers=headers)
            assert result.status_code == 200
            assert result.content == b"APA citations required."
            assert result.headers["x-content-type-options"] == "nosniff"
            bad = client.post(root + "/upload/input", headers=headers,
                files={"file": ("malware.exe", io.BytesIO(b"evil"), "application/octet-stream")})
            assert bad.status_code == 422


def test_generated_file_preview_review_versions():
    with TemporaryDirectory() as directory, patch.dict(os.environ, {
        "QBROWSER_HOME": directory, "QBROWSER_API_TOKEN": "secret",
    }):
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer secret"}
            job = client.post("/api/jobs", json={"instruction": "Create report"},
                              headers=headers).json()
            url = f"/api/jobs/{job['id']}/documents"
            body = {"filename": "analysis.docx", "data": {
                "title": "Academic Analysis", "blocks": [
                    {"type": "paragraph", "text": "The claim is supported (Smith, 2021)."}
                ]}}
            gen = client.post(url + "/generate", json=body, headers=headers)
            assert gen.status_code == 201, gen.text
            assert gen.json()["version"] == 1
            qa = client.get(url + "/output/analysis.docx/review", headers=headers)
            assert qa.status_code == 200
            assert qa.json()["source_verification"] == "requires independent external research"
            assert qa.json()["analysis"]["citation_signals"] == 1
            preview = client.get(url + "/output/analysis.docx/preview", headers=headers)
            assert "Academic Analysis" in preview.text
            assert preview.headers["content-security-policy"].startswith("default-src 'none'")
            again = client.post(url + "/generate", json={
                "filename": "analysis.docx",
                "data": {"title": "Second Version", "blocks": [
                    {"type": "paragraph", "text": "Final version."}
                ]}}, headers=headers)
            assert again.json()["version"] == 2
            first = client.get(url + "/output/analysis.docx/download?version=1", headers=headers)
            assert first.status_code == 200
            assert first.content != client.get(url + "/output/analysis.docx/download?version=2",
                                               headers=headers).content
            converter = client.post(url + "/convert", headers=headers, json={
                "source_role": "output", "source_filename": "analysis.docx",
                "destination_filename": "analysis.txt", "version": 2})
            assert converter.status_code == 201, converter.text
            txt = client.get(url + "/output/analysis.txt/download", headers=headers)
            assert "Final version." in txt.text


def test_pdf_preview_returns_png_and_requires_auth():
    with TemporaryDirectory() as directory, patch.dict(os.environ, {
        "QBROWSER_HOME": directory, "QBROWSER_API_TOKEN": "secret",
    }):
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer secret"}
            job = client.post("/api/jobs", json={"instruction": "Create PDF"},
                              headers=headers).json()
            base = f"/api/jobs/{job['id']}/documents"
            r = client.post(base + "/generate", headers=headers, json={
                "filename": "paper.pdf", "data": {
                    "title": "Test PDF", "blocks": [{"type": "paragraph", "text": "Example content."}]
                }})
            assert r.status_code == 201, r.text
            assert client.get(base + "/output/paper.pdf/preview").status_code == 401
            preview = client.get(base + "/output/paper.pdf/preview", headers=headers)
            assert preview.status_code == 200
            assert preview.headers["content-type"] == "image/png"
            assert preview.content.startswith(b"\x89PNG")
            assert client.get(base + "/output/paper.pdf/preview?page=500",
                              headers=headers).status_code == 422


def test_native_document_tool_creates_and_reads_without_gui():
    from qbrowser.document_tools import perform_document_action
    with TemporaryDirectory() as directory, patch.dict(os.environ, {"QBROWSER_HOME": directory}):
        store = JobStore(f"{directory}/state.db")
        job = store.create("Review instructions and write report")["id"]
        record = perform_document_action(store, job, {
            "operation": "create", "filename": "answer.docx",
            "data": {"title": "Answer", "blocks": [{"type": "paragraph", "text": "A reviewable answer."}]}
        })
        assert "answer.docx" in record
        listed = perform_document_action(store, job, {"operation": "list"})
        assert "answer.docx" in listed
        read = perform_document_action(store, job, {
            "operation": "read", "role": "output", "filename": "answer.docx",
            "offset": 0, "length": 200,
        })
        assert "reviewable" in read
