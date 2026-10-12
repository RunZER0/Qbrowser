"""Typed, bounded local document tool adapter for the native Qbrowser agent."""
from __future__ import annotations

import json
import base64
import io
from pathlib import Path

from qbrowser.artifact_vault import (
    create_output, convert_output, list_revisions, open_snapshot,
)
from qbrowser.document_routes import typed_source
from qbrowser.document_engine import extract_text, inspect_document
from qbrowser.store import JobStore


def perform_document_action(store: JobStore, job_id: str, args: dict) -> str | dict:
    if not isinstance(args, dict):
        raise ValueError("Document action requires object")
    op = args.get("operation")
    role = args.get("role", "input")
    filename = args.get("filename", "")
    version = args.get("version")
    if version is not None and (type(version) is not int or version < 1):
        raise ValueError("Invalid document version")
    if op == "list":
        files = list_revisions(store, job_id)
        return json.dumps([{
            "role": item["role"], "filename": item["filename"],
            "version": item["version"], "mime": item["mime"],
            "deleted": bool(item["deleted_at"])
        } for item in files[-100:]], ensure_ascii=False)
    if op == "create":
        data = args.get("data")
        if not isinstance(data, dict):
            raise ValueError("create requires structured document data")
        item = create_output(store, job_id, filename, data)
        return json.dumps({"created": item["filename"], "version": item["version"],
                           "hash": item["sha256"], "quality_issues": item["qa"]})
    if op == "convert":
        destination = args.get("destination_filename", "")
        item = convert_output(store, job_id, role, filename, destination, version=version)
        return json.dumps({"converted": item["filename"], "version": item["version"],
                           "quality_issues": item["qa"]})
    if op == "view_image":
        if Path(filename).suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            raise ValueError("view_image supports PNG and JPEG uploads")
        _, path = open_snapshot(store, job_id, role, filename, version)
        from PIL import Image
        with Image.open(path) as image:
            if image.width * image.height > 40_000_000:
                raise ValueError("Image is too large to inspect safely")
            image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=82, optimize=True)
        uri = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        return {"filename": filename, "image_uri": uri}
    if op in ("inspect", "read"):
        _, path = open_snapshot(store, job_id, role, filename, version)
        with typed_source(path, filename) as original:
            if op == "inspect":
                metadata = inspect_document(original)
                metadata["preview"] = metadata.get("preview", "")[:1200]
                return json.dumps(metadata, ensure_ascii=False)
            offset = args.get("offset", 0)
            length = args.get("length", 4000)
            if type(offset) is not int or type(length) is not int or offset < 0 or not 1 <= length <= 8000:
                raise ValueError("Read offset or length invalid")
            text = extract_text(original, limit=min(2_000_000, offset + length + 1))
            return json.dumps({"start": offset, "end": min(len(text), offset + length),
                "content": text[offset:offset + length], "has_more": len(text) > offset + length},
                ensure_ascii=False)
    raise ValueError("Unknown document operation")
