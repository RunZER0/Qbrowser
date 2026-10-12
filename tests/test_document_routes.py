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
            unauthenticated = client.post(root + "/upload/input",
                files={"file": ("assignment.txt", io.BytesIO(b"private"), "text/plain")})
            assert unauthenticated.status_code == 401
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
            diff = client.get(url + "/output/analysis.docx/diff?older=1&newer=2",
                              headers=headers)
            assert diff.status_code == 200, diff.text
            assert "Final version." in diff.json()["unified_diff"]
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



def test_native_agent_produces_versioned_word_file_without_gui_clicks():
    """Full fake-model agent loop → document_action → persisted output → review state."""
    from qbrowser.native import run_native, ModelConfig
    from qbrowser.artifact_vault import list_revisions

    class FakeDesktop:
        def observe(self):
            return "data:image/jpeg;base64,ZHVtbXk=", (1280, 720)

    class FakeModel:
        turns = 0
        def ask(self, messages):
            self.turns += 1
            if self.turns == 1:
                op, args = "document_action", {"operation": "create",
                    "filename": "final.docx",
                    "data": {"title": "Result", "blocks": [
                        {"type": "paragraph", "text": "A real Word file produced by the agent."}
                    ]}}
            else:
                op, args = "finish_task", {"result": "Word file produced",
                                           "evidence": "Saved via managed document tool"}
            import json
            return {"calls": [{"id": "call" + str(self.turns), "name": op, "args": args}],
                    "assistant": {"role": "assistant", "content": None,
                                  "tool_calls": [{"id": "call" + str(self.turns),
                                    "type": "function", "function": {
                                        "name": op, "arguments": json.dumps(args)}}]},
                    "usage": {"input": 100, "output": 30}}

    with TemporaryDirectory() as directory, patch.dict(os.environ, {"QBROWSER_HOME": directory}):
        store = JobStore(f"{directory}/state.db")
        task = store.create("Generate Word document")
        claimed = store.claim()
        config = ModelConfig("zai", "glm-5.3-flash", "fake", "https://example.com", 5, 120, 1280)
        state = run_native(claimed, store, model=FakeModel(), desktop=FakeDesktop(),
                           config=config)
        assert state["status"] == "verify"
        documents = list_revisions(store, task["id"], role="output")
        assert len(documents) == 1
        assert documents[0]["filename"] == "final.docx"
        assert documents[0]["metadata"]["title"] == "Result"



def test_upload_request_content_length_guard():
    with TemporaryDirectory() as directory, patch.dict(os.environ, {
        "QBROWSER_HOME": directory, "QBROWSER_API_TOKEN": "secret",
    }):
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer secret"}
            job = client.post("/api/jobs", json={"instruction": "Test large upload"},
                              headers=headers).json()
            url = f"/api/jobs/{job['id']}/documents/upload/input"
            oversized = client.post(url, files={
                "file": ("notes.txt", io.BytesIO(b"short"), "text/plain"),
            }, headers={**headers, "Content-Length": str(102 * 1024 * 1024)})
            assert oversized.status_code == 413



def test_semantic_office_preview_escapes_untrusted_html():
    with TemporaryDirectory() as directory, patch.dict(os.environ, {
        "QBROWSER_HOME": directory, "QBROWSER_API_TOKEN": "secret",
    }):
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer secret"}
            job = client.post("/api/jobs", json={"instruction": "Preview safe HTML"},
                              headers=headers).json()
            base = f"/api/jobs/{job['id']}/documents"
            generated = client.post(base + "/generate", headers=headers, json={
                "filename": "safe.docx", "data": {"title": "Safe Preview", "blocks": [
                    {"type": "paragraph", "text": "<script>alert('XSS')</script>"}
                ]}})
            assert generated.status_code == 201, generated.text
            html_preview = client.get(base + "/output/safe.docx/preview", headers=headers)
            assert html_preview.status_code == 200
            assert "<script>" not in html_preview.text
            assert "&lt;script&gt;" in html_preview.text
            assert "default-src 'none'" in html_preview.headers["content-security-policy"]
