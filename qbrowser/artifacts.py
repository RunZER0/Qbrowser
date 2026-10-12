"""Isolated per-job outputs and auditable file metadata, with no file upload."""

from __future__ import annotations

import hashlib
from pathlib import Path

from qbrowser.store import JobStore, home, utcnow

MAX_FILES = 250
MAX_FILE_SIZE = 100 * 1024 * 1024


def workspace(job_id: str) -> Path:
    if not (len(job_id) == 12 and all(c in "0123456789abcdef" for c in job_id)):
        raise ValueError("Invalid job ID")
    root = home() / "workspaces" / job_id
    root.mkdir(parents=True, exist_ok=True)
    return root


def index_outputs(store: JobStore, job_id: str) -> list[dict]:
    """Index ordinary files only. Symlinks are excluded to avoid indexing outside data."""
    store.get(job_id)
    root = workspace(job_id).resolve()
    files = sorted(root.rglob("*"))
    if len(files) > MAX_FILES * 5:
        raise ValueError("Workspace contains excessive entries")
    found: list[dict] = []
    for file in files:
        if not file.is_file() or file.is_symlink():
            continue
        resolved = file.resolve()
        if not resolved.is_relative_to(root):
            continue
        size = file.stat().st_size
        if size > MAX_FILE_SIZE:
            continue
        digest = hashlib.sha256()
        with file.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        found.append({
            "job_id": job_id,
            "name": str(file.relative_to(root)),
            "sha256": digest.hexdigest(),
            "bytes": size,
            "indexed_at": utcnow(),
        })
        if len(found) >= MAX_FILES:
            break
    with store._connection() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS artifacts(
            job_id TEXT NOT NULL REFERENCES jobs(id),
            name TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
            indexed_at TEXT NOT NULL, PRIMARY KEY(job_id,name)
        )""")
        db.execute("BEGIN IMMEDIATE")
        for item in found:
            db.execute("""INSERT INTO artifacts(job_id,name,sha256,bytes,indexed_at)
                VALUES(?,?,?,?,?) ON CONFLICT(job_id,name) DO UPDATE SET
                sha256=excluded.sha256, bytes=excluded.bytes,
                indexed_at=excluded.indexed_at""",
                (item["job_id"], item["name"], item["sha256"],
                 item["bytes"], item["indexed_at"]))
        db.execute("COMMIT")
    return found


def artifacts(store: JobStore, job_id: str) -> list[dict]:
    store.get(job_id)
    with store._connection() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS artifacts(
            job_id TEXT NOT NULL REFERENCES jobs(id),
            name TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
            indexed_at TEXT NOT NULL, PRIMARY KEY(job_id,name)
        )""")
        rows = db.execute("SELECT * FROM artifacts WHERE job_id=? ORDER BY name",
                          (job_id,)).fetchall()
    return [dict(r) for r in rows]
