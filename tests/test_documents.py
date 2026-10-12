"""Section 14: real document generation, immutable versioning and safety tests."""
from __future__ import annotations

import io
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from qbrowser.artifact_vault import (
    create_output, convert_output, get_revision, list_revisions, open_snapshot,
    purge_expired, record_file, restore_revision, safe_name, sync_outputs,
)
from qbrowser.document_engine import create_document, extract_text, inspect_document, preview_png
from qbrowser.store import JobStore


@pytest.fixture
def setup_job():
    with tempfile.TemporaryDirectory() as directory:
        with patch.dict(os.environ, {"QBROWSER_HOME": directory}):
            store = JobStore(Path(directory) / "db.sqlite")
            job = store.create("Prepare an accessible document")
            yield store, job["id"], Path(directory)


@pytest.mark.parametrize("filename", [
    "../secret.docx", "/root/report.pdf", r"C:\safe.docx", ".hidden.pdf",
    "bad.exe", "CON.pdf", "report.pdf:stream", "x\x00.docx",
])
def test_unsafe_filename_rejected(filename):
    with pytest.raises(ValueError):
        safe_name(filename)


def test_docx_create_and_version_lineage(setup_job):
    store, job, root = setup_job
    first = create_output(store, job, "report.docx", {
        "title": "Corporate Governance", "blocks": [
            {"type": "heading", "text": "Introduction"},
            {"type": "paragraph", "text": "Corporate governance is an accountability framework."},
            {"type": "bullet", "text": "Audit obligations"},
            {"type": "table", "rows": [["Area", "Finding"], ["Risk", "High"]]},
        ],
    })
    assert first["version"] == 1
    assert first["metadata"]["title"] == "Corporate Governance"
    assert first["metadata"]["tables"] == 1
    assert first["bytes"] > 100
    old_blob = open_snapshot(store, job, "output", "report.docx", 1)[1]
    original_hash = first["sha256"]
    second = create_output(store, job, "report.docx", {
        "title": "Corporate Governance Revised",
        "blocks": [{"type": "paragraph", "text": "The second, reviewed version."}],
    })
    assert second["version"] == 2
    assert second["parent_sha256"] == original_hash
    assert old_blob.exists()
    assert open_snapshot(store, job, "output", "report.docx", 1)[0]["sha256"] == original_hash
    assert get_revision(store, job, "output", "report.docx")["version"] == 2
    assert create_output(store, job, "report.docx", {
        "title": "Corporate Governance Revised",
        "blocks": [{"type": "paragraph", "text": "The second, reviewed version."}],
    })["version"] == 2


def test_restore_historical_revision_creates_new_version(setup_job):
    store, job, root = setup_job
    first = create_output(store, job, "restore.txt", {"text": "Original draft"})
    second = create_output(store, job, "restore.txt", {"text": "Revised draft"})
    with pytest.raises(ValueError, match="Pause or finish"):
        restore_revision(store, job, "output", "restore.txt", 1)
    with store._connection() as db:
        db.execute("UPDATE jobs SET status='verify' WHERE id=?", (job,))
    restored = restore_revision(store, job, "output", "restore.txt", 1)
    assert restored["version"] == 3
    assert restored["sha256"] == first["sha256"]
    assert restored["parent_sha256"] == second["sha256"]
    assert restored["restored_from_version"] == 1
    assert (root / "workspaces" / job / "restore.txt").read_text(encoding="utf-8") == "Original draft"
    assert open_snapshot(store, job, "output", "restore.txt", 1)[1].read_text(encoding="utf-8") == "Original draft"
    assert open_snapshot(store, job, "output", "restore.txt", 2)[1].read_text(encoding="utf-8") == "Revised draft"


def test_uploaded_image_is_resized_and_sent_as_untrusted_vision_input(setup_job):
    from PIL import Image
    from qbrowser.document_tools import perform_document_action
    store, job, root = setup_job
    image_path = root / "photo.png"
    Image.new("RGB", (2000, 1000), (20, 80, 140)).save(image_path)
    with image_path.open("rb") as image_file:
        record_file(store, job, "photo.png", "input", image_file)
    result = perform_document_action(store, job, {
        "operation": "view_image", "filename": "photo.png", "role": "input"})
    assert result["filename"] == "photo.png"
    assert result["image_uri"].startswith("data:image/jpeg;base64,")
    encoded = result["image_uri"].split(",", 1)[1]
    assert len(encoded) < 300_000


def test_pdf_generation_preview_and_qa(setup_job):
    store, job, _ = setup_job
    item = create_output(store, job, "analysis.pdf", {
        "title": "Research Report",
        "blocks": [{"type": "paragraph",
                    "text": "The results were consistent (Smith, 2021)."}],
    })
    assert item["metadata"]["pages"] >= 1
    assert any(issue["code"] == "missing_references_heading" for issue in item["qa"])
    _, blob = open_snapshot(store, job, "output", "analysis.pdf")
    # Stored blobs are immutable and have hash names; copy for parser access.
    with tempfile.TemporaryDirectory() as directory:
        original = Path(directory) / "analysis.pdf"
        shutil.copyfile(blob, original)
        assert preview_png(original).startswith(b"\x89PNG")
        assert "Research Report" in extract_text(original)


def test_spreadsheet_generation_and_csv_conversion(setup_job):
    store, job, _ = setup_job
    item = create_output(store, job, "budget.xlsx", {"sheets": [
        {"name": "Actuals", "rows": [
            ["Category", "Amount"], ["Fees", 300], ["Risk", "=2+2"]
        ]}
    ]})
    assert item["metadata"]["sheets"] == ["Actuals"]
    csv_version = convert_output(store, job, "output", "budget.xlsx", "budget.csv")
    _, blob = open_snapshot(store, job, "output", "budget.csv", csv_version["version"])
    with tempfile.TemporaryDirectory() as directory:
        local = Path(directory) / "budget.csv"
        shutil.copyfile(blob, local)
        assert "Category" in extract_text(local)
        assert "=2+2" in extract_text(local)


def test_slides_generation_and_text_conversion(setup_job):
    store, job, _ = setup_job
    result = create_output(store, job, "briefing.pptx", {
        "slides": [{"title": "Overview", "bullets": ["First point", "Second point"]},
                   {"title": "Conclusion", "bullets": ["Next steps"]}]
    })
    assert result["metadata"]["slides"] == 2
    converted = convert_output(store, job, "output", "briefing.pptx", "briefing.txt")
    assert converted["version"] == 1
    assert "Conclusion" in converted["metadata"]["preview"]


def test_stream_input_and_output_role_isolation(setup_job):
    store, job, _ = setup_job
    inp = record_file(store, job, "instructions.txt", "input",
                      io.BytesIO(b"Write 1000 words about contract law."))
    assert inp["role"] == "input"
    with pytest.raises(KeyError):
        open_snapshot(store, job, "output", "instructions.txt")
    assert "contract law" in inp["metadata"]["preview"]
    assert len(list_revisions(store, job, role="input")) == 1
    assert sync_outputs(store, job) == []


def test_import_rejects_fake_pdf_and_executable_zip(setup_job):
    store, job, _ = setup_job
    with pytest.raises(ValueError, match="valid PDF"):
        record_file(store, job, "fake.pdf", "input", io.BytesIO(b"not pdf data"))
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "unsafe.docx"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("[Content_Types].xml", "x")
            z.writestr("word/document.xml", "x")
            z.writestr("word/vbaProject.bin", b"MALICIOUS")
        with path.open("rb") as fp:
            with pytest.raises(ValueError, match="Executable"):
                record_file(store, job, "unsafe.docx", "input", fp)


def test_conversion_refuses_layout_loss(setup_job):
    store, job, _ = setup_job
    create_output(store, job, "paper.docx", {"blocks": [
        {"type": "paragraph", "text": "A formal legal paper."}]})
    with pytest.raises(ValueError, match="Unsupported conversion"):
        convert_output(store, job, "output", "paper.docx", "paper.pdf")


def test_output_gui_file_snapshot_versioning(setup_job):
    store, job, root = setup_job
    working = root / "workspaces" / job
    working.mkdir(parents=True, exist_ok=True)
    working.joinpath("draft.txt").write_text("Draft one", encoding="utf-8")
    assert sync_outputs(store, job)[0]["version"] == 1
    working.joinpath("draft.txt").write_text("Draft two", encoding="utf-8")
    assert sync_outputs(store, job)[0]["version"] == 2
    assert open_snapshot(store, job, "output", "draft.txt", 1)[1].read_text() == "Draft one"
    assert open_snapshot(store, job, "output", "draft.txt", 2)[1].read_text() == "Draft two"


def test_expired_versions_retain_metadata(setup_job):
    store, job, root = setup_job
    create_output(store, job, "old.txt", {"text": "Confidential archived text"})
    with store._connection() as db:
        db.execute("UPDATE document_revisions SET created_at=? WHERE job_id=?",
                   ("2000-01-01T00:00:00+00:00", job))
        db.execute("UPDATE jobs SET status='completed' WHERE id=?", (job,))
    assert purge_expired(store, days=7, dry_run=True)["eligible_versions"] == 1
    out = purge_expired(store, days=7, dry_run=False)
    assert out["deleted_versions"] == 1
    assert get_revision(store, job, "output", "old.txt")["deleted_at"]
    with pytest.raises(KeyError):
        open_snapshot(store, job, "output", "old.txt")


def test_reference_checks_detect_unmatched_authors(setup_job):
    store, job, _ = setup_job
    item = create_output(store, job, "references.docx", {
        "blocks": [
            {"type": "paragraph", "text": "The claim appears (Smith, 2020) and (Brown, 2021)."},
            {"type": "heading", "text": "References"},
            {"type": "paragraph", "text": "Smith, J. (2020). A relevant article."},
        ]})
    codes = {issue["code"] for issue in item["qa"]}
    assert "unmatched_citations" in codes
    assert item["metadata"]["unmatched_citations"] == ["Brown (2021)"]
    assert item["metadata"]["source_verification"].startswith("internal_consistency")


def test_retention_removes_local_working_copy(setup_job):
    store, job, root = setup_job
    create_output(store, job, "outdated.txt", {"text": "Old working copy"})
    working = root / "workspaces" / job / "outdated.txt"
    assert working.exists()
    with store._connection() as db:
        db.execute("UPDATE document_revisions SET created_at=? WHERE job_id=?",
                   ("2000-01-01T00:00:00+00:00", job))
        db.execute("UPDATE jobs SET status='completed' WHERE id=?", (job,))
    purge_expired(store, days=7, dry_run=False)
    assert not working.exists()
    assert get_revision(store, job, "output", "outdated.txt")["deleted_at"] is not None


def test_wrong_format_upload_rejected(setup_job):
    store, job, _ = setup_job
    with pytest.raises(ValueError):
        record_file(store, job, "document.docx", "input", io.BytesIO(b"plain text disguised"))


def test_internal_reference_check_does_not_claim_external_verification(setup_job):
    store, job, _ = setup_job
    item = create_output(store, job, "sources.txt", {"text": (
        "Discussion cites (Adams, 2018).\n\nReferences\nAdams, P. (2018). "
        "A publication. https://doi.org/10.1234/example"
    )})
    assert item["metadata"]["unmatched_citations"] == []
    assert item["metadata"]["source_verification"] != "verified"
    assert item["metadata"]["reference_urls"]
