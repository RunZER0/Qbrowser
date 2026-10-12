"""Immutable, versioned, job-scoped file vault; safe imports, exports and retention."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO

from qbrowser.artifacts import workspace
from qbrowser.document_engine import SUPPORTED, create_document, convert_document, inspect_document
from qbrowser.store import JobStore, home, utcnow

MAX_BYTES = 100 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 300 * 1024 * 1024
MAX_MEMBERS = 3000
CONTENT_TYPES = {
    ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".pdf": "application/pdf", ".png": "image/png",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".flac": "audio/flac", ".m4a": "audio/mp4", ".mp3": "audio/mpeg",
    ".mp4": "audio/mp4", ".mpeg": "audio/mpeg", ".mpga": "audio/mpeg",
    ".ogg": "audio/ogg", ".oga": "audio/ogg", ".wav": "audio/wav",
    ".webm": "audio/webm",
}
OOXML_REQUIRED = {
    ".docx": "word/document.xml",
    ".xlsx": "xl/workbook.xml",
    ".pptx": "ppt/presentation.xml",
}
VALID_ID = re.compile(r"^[0-9a-f]{12}$")


def safe_name(name: str) -> str:
    """Allow only a safe *single* filename; prevent path traversal and alternate streams."""
    if not isinstance(name, str) or not 1 <= len(name) <= 160:
        raise ValueError("Filename must have 1-160 characters")
    if name in (".", "..") or name != Path(name).name or any(c in name for c in "\\/:") \
            or name.startswith(".") or any(ord(c) < 32 for c in name):
        raise ValueError("Unsafe filename")
    if any(ch in '<>|?*"\\' or ord(ch) == 127 for ch in name) or name.endswith((" ", ".")):
        raise ValueError("Unsafe filename")
    if Path(name).suffix.lower() not in SUPPORTED:
        raise ValueError("Unsupported file extension")
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", name, re.IGNORECASE):
        raise ValueError("Reserved filename")
    return name


def _schema(store: JobStore):
    with store._connection() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS document_revisions (
                job_id TEXT NOT NULL REFERENCES jobs(id),
                role TEXT NOT NULL CHECK(role IN ('input','output')),
                filename TEXT NOT NULL,
                version INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                bytes INTEGER NOT NULL,
                mime TEXT NOT NULL,
                created_at TEXT NOT NULL,
                parent_sha256 TEXT,
                metadata TEXT NOT NULL DEFAULT '{}',
                qa TEXT NOT NULL DEFAULT '[]',
                deleted_at TEXT,
                PRIMARY KEY(job_id,role,filename,version)
            );
            CREATE INDEX IF NOT EXISTS doc_revisions_job ON
                document_revisions(job_id,role,filename,version);
        """)
        db.execute("INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) VALUES(2,?,?)",
                   ("immutable-document-vault", utcnow()))


def _vault_path(job_id: str, digest: str) -> Path:
    if not VALID_ID.fullmatch(job_id) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid artifact identifier")
    return home() / "vault" / job_id / digest[:2] / digest


def validate_file(path: Path, filename: str) -> dict:
    """Reject executable extensions, disguised archives, corrupt containers and zip bombs."""
    filename = safe_name(filename)
    extension = Path(filename).suffix.lower()
    if path.is_symlink() or not path.is_file():
        raise ValueError("Not a regular document file")
    size = path.stat().st_size
    if not 0 < size <= MAX_BYTES:
        raise ValueError("File empty or exceeds 100 MB limit")
    with path.open("rb") as fp:
        header = fp.read(32)
    if extension == ".pdf":
        if not header.startswith(b"%PDF-"):
            raise ValueError("File is not a valid PDF")
    elif extension == ".png":
        if not header.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("File is not a valid PNG")
    elif extension in (".jpg", ".jpeg"):
        if not header.startswith(b"\xff\xd8\xff"):
            raise ValueError("File is not a valid JPEG")
    elif extension == ".wav":
        if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise ValueError("File is not a valid WAV audio container")
    elif extension == ".flac":
        if not header.startswith(b"fLaC"):
            raise ValueError("File is not a valid FLAC stream")
    elif extension in {".m4a", ".mp4"}:
        if len(header) < 12 or header[4:8] != b"ftyp":
            raise ValueError("File is not a valid MP4 audio container")
    elif extension in {".ogg", ".oga"}:
        if not header.startswith(b"OggS"):
            raise ValueError("File is not a valid Ogg audio container")
    elif extension == ".webm":
        if not header.startswith(b"\x1aE\xdf\xa3"):
            raise ValueError("File is not a valid WebM container")
    elif extension in {".mp3", ".mpeg", ".mpga"}:
        frame_sync = len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0
        if not header.startswith(b"ID3") and not frame_sync:
            raise ValueError("File is not a valid MPEG audio stream")
    elif extension in OOXML_REQUIRED:
        if not zipfile.is_zipfile(path):
            raise ValueError("Office document must be a ZIP package")
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_MEMBERS:
                raise ValueError("Too many Office package members")
            unpacked = 0
            for info in infos:
                unpacked += info.file_size
                if unpacked > MAX_TOTAL_UNCOMPRESSED or (
                    info.compress_size and info.file_size / info.compress_size > 1000
                ):
                    raise ValueError("Unsafe document compression ratio")
                parts = Path(info.filename).parts
                if info.filename.startswith("/") or ".." in parts or "\\" in info.filename:
                    raise ValueError("Unsafe internal Office path")
                if info.filename.lower().endswith((".exe", ".dll", ".js", ".vbs", "vbaproject.bin")) or "/activex/" in info.filename.lower() or "/embeddings/" in info.filename.lower():
                    raise ValueError("Executable content in document package")
            names = set(zf.namelist())
            if "[Content_Types].xml" not in names or OOXML_REQUIRED[extension] not in names:
                raise ValueError("Office package does not match extension")
            if zf.testzip() is not None:
                raise ValueError("Office package has corrupted contents")
    elif extension in (".txt", ".md", ".csv"):
        if b"\x00" in header:
            raise ValueError("Binary file submitted as text")
        with path.open("r", encoding="utf-8-sig", errors="strict") as fp:
            for _ in iter(lambda: fp.read(64 * 1024), ""):
                pass
    # Inspect with the mature format parser as a second structural check.
    try:
        details = inspect_document(path)
    except Exception as exc:
        raise ValueError(f"Document cannot be opened: {type(exc).__name__}") from exc
    return details


def _stream_to_temp(stream: BinaryIO, target_dir: Path, *, suffix: str = "") -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".incoming-", suffix=suffix, dir=target_dir)
    total = 0
    try:
        with os.fdopen(fd, "wb") as dest:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError("File exceeds 100 MB limit")
                dest.write(chunk)
            dest.flush()
            os.fsync(dest.fileno())
        if not total:
            raise ValueError("Empty file")
        return Path(tmp)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def _hash(path: Path) -> tuple[str, int]:
    sha = hashlib.sha256()
    size = 0
    with path.open("rb") as fp:
        for block in iter(lambda: fp.read(1024 * 1024), b""):
            size += len(block)
            sha.update(block)
    return sha.hexdigest(), size


def record_file(store: JobStore, job_id: str, filename: str, role: str,
                stream: BinaryIO, *, preserve_working_copy: bool = True) -> dict:
    """Capture a versioned immutable snapshot; never silently overwrite prior revisions."""
    store.get(job_id)
    filename = safe_name(filename)
    if role not in {"input", "output"}:
        raise ValueError("Role must be input or output")
    root = workspace(job_id)
    temp = _stream_to_temp(stream, root, suffix=Path(filename).suffix.lower())
    try:
        info = validate_file(temp, filename)
        digest, size = _hash(temp)
        destination = _vault_path(job_id, digest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _schema(store)
        with store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                """SELECT version,sha256 FROM document_revisions
                   WHERE job_id=? AND role=? AND filename=?
                   ORDER BY version DESC LIMIT 1""",
                (job_id, role, filename),
            ).fetchone()
            if previous and previous["sha256"] == digest:
                row = db.execute(
                    """SELECT * FROM document_revisions WHERE
                       job_id=? AND role=? AND filename=? AND version=?""",
                    (job_id, role, filename, previous["version"]),
                ).fetchone()
                if row["deleted_at"] is None:
                    db.execute("COMMIT")
                    return _decode(row)
            # Atomic snapshot. Hash filenames are immutable; identical content is deduplicated.
            if destination.is_symlink():
                raise ValueError("Unsafe snapshot destination")
            if not destination.exists():
                staged = destination.with_name("." + uuid.uuid4().hex)
                shutil.copyfile(temp, staged)
                if os.name != "nt":
                    os.chmod(staged, 0o600)
                os.replace(staged, destination)
            revision = (previous["version"] + 1) if previous else 1
            stamp = utcnow()
            qa = info.pop("issues", [])
            db.execute(
                """INSERT INTO document_revisions
                   (job_id,role,filename,version,sha256,bytes,mime,created_at,parent_sha256,metadata,qa)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (job_id, role, filename, revision, digest, size,
                 CONTENT_TYPES[Path(filename).suffix.lower()], stamp,
                 previous["sha256"] if previous else None,
                 json.dumps(info, ensure_ascii=False), json.dumps(qa, ensure_ascii=False)),
            )
            JobStore._event(db, job_id, "artifact_version", role=role,
                            filename=filename, version=revision, sha256=digest)
            db.execute("COMMIT")
        if preserve_working_copy:
            directory = root / "inputs" if role == "input" else root
            directory.mkdir(parents=True, exist_ok=True)
            copy = directory / filename
            if copy.is_symlink() or (copy.exists() and not copy.is_file()):
                raise ValueError("Unsafe working copy destination")
            staged = directory / ("." + uuid.uuid4().hex + ".part")
            shutil.copyfile(temp, staged)
            os.replace(staged, copy)
        return get_revision(store, job_id, role, filename, revision)
    finally:
        temp.unlink(missing_ok=True)


def _decode(row: sqlite3.Row) -> dict:
    result = dict(row)
    result["metadata"] = json.loads(result["metadata"])
    result["qa"] = json.loads(result["qa"])
    return result


def list_revisions(store: JobStore, job_id: str, *, role: str | None = None) -> list[dict]:
    store.get(job_id)
    _schema(store)
    if role not in (None, "input", "output"):
        raise ValueError("Invalid role")
    with store._connection() as db:
        if role is None:
            rows = db.execute(
                "SELECT * FROM document_revisions WHERE job_id=? ORDER BY created_at,role,filename,version",
                (job_id,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM document_revisions WHERE job_id=? AND role=? ORDER BY created_at,filename,version",
                (job_id, role),
            ).fetchall()
    return [_decode(row) for row in rows]


def get_revision(store: JobStore, job_id: str, role: str, filename: str,
                 version: int | None = None) -> dict:
    store.get(job_id)
    safe_name(filename)
    if role not in {"input", "output"}:
        raise ValueError("Invalid role")
    _schema(store)
    with store._connection() as db:
        if version is None:
            row = db.execute(
                """SELECT * FROM document_revisions WHERE job_id=? AND role=? AND filename=?
                   ORDER BY version DESC LIMIT 1""",
                (job_id, role, filename),
            ).fetchone()
        else:
            row = db.execute(
                """SELECT * FROM document_revisions
                   WHERE job_id=? AND role=? AND filename=? AND version=?""",
                (job_id, role, filename, version),
            ).fetchone()
    if row is None:
        raise KeyError("Document version not found")
    return _decode(row)


def open_snapshot(store: JobStore, job_id: str, role: str,
                  filename: str, version: int | None = None) -> tuple[dict, Path]:
    item = get_revision(store, job_id, role, filename, version)
    if item["deleted_at"]:
        raise KeyError("Document content expired")
    path = _vault_path(job_id, item["sha256"])
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError("Vault content unavailable")
    digest, size = _hash(path)
    if digest != item["sha256"] or size != item["bytes"]:
        raise ValueError("Vault integrity check failed")
    return item, path


def restore_revision(store: JobStore, job_id: str, role: str,
                     filename: str, version: int) -> dict:
    """Restore an immutable revision as a new latest version and working copy."""
    job = store.get(job_id)
    if job["status"] in {"queued", "running", "waiting"}:
        raise ValueError("Pause or finish the task before restoring a document")
    source_item, source = open_snapshot(store, job_id, role, filename, version)
    with source.open("rb") as stream:
        restored = record_file(store, job_id, filename, role, stream)
    with store._connection() as db:
        JobStore._event(db, job_id, "artifact_restored", role=role,
                        filename=filename, source_version=version,
                        restored_version=restored["version"], sha256=source_item["sha256"])
    restored["restored_from_version"] = version
    return restored


def create_output(store: JobStore, job_id: str, filename: str, data: dict) -> dict:
    filename = safe_name(filename)
    if Path(filename).suffix.lower() not in {".docx", ".pdf", ".xlsx", ".pptx", ".txt", ".md"}:
        raise ValueError("Format not supported for document generation")
    with tempfile.TemporaryDirectory(dir=workspace(job_id)) as tmp:
        path = Path(tmp) / filename
        create_document(path, data)
        with path.open("rb") as fp:
            return record_file(store, job_id, filename, "output", fp)


def convert_output(store: JobStore, job_id: str, source_role: str, source_name: str,
                   dest_name: str, *, version: int | None = None) -> dict:
    dest_name = safe_name(dest_name)
    _, source = open_snapshot(store, job_id, source_role, source_name, version)
    with tempfile.TemporaryDirectory(dir=workspace(job_id)) as tmp:
        target = Path(tmp) / dest_name
        # Snapshot filenames are hashes; parsers need the actual format extension.
        local = Path(tmp) / safe_name(source_name)
        shutil.copyfile(source, local)
        convert_document(local, target)
        with target.open("rb") as fp:
            return record_file(store, job_id, dest_name, "output", fp)


def sync_outputs(store: JobStore, job_id: str) -> list[dict]:
    """Version regular output files created manually through the GUI."""
    root = workspace(job_id)
    saved = []
    for file in sorted(root.iterdir()):
        if file.name.startswith(".") or file.name == "inputs" or file.is_symlink() or not file.is_file():
            continue
        if file.suffix.lower() not in SUPPORTED:
            continue
        if not 0 < file.stat().st_size <= MAX_BYTES:
            continue
        try:
            with file.open("rb") as fp:
                saved.append(record_file(store, job_id, file.name, "output", fp,
                                         preserve_working_copy=False))
        except (ValueError, OSError):
            # Invalid files are left unindexed for explicit review.
            continue
    return saved


def purge_expired(store: JobStore, *, days: int, dry_run: bool = True) -> dict:
    """Delete old finished-job blob contents, preserving manifests for lineage."""
    if not 7 <= days <= 36500:
        raise ValueError("Retention must be 7-36500 days")
    _schema(store)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with store._connection() as db:
        rows = db.execute(
            """SELECT r.* FROM document_revisions r JOIN jobs j ON j.id=r.job_id
               WHERE j.status IN ('completed','cancelled','failed')
               AND r.created_at < ? AND r.deleted_at IS NULL""", (cutoff,),
        ).fetchall()
    selected = [_decode(row) for row in rows]
    if dry_run:
        return {"eligible_versions": len(selected), "deleted_versions": 0, "dry_run": True}
    deleted = 0
    for item in selected:
        job_id, digest = item["job_id"], item["sha256"]
        with store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE document_revisions SET deleted_at=? WHERE
                   job_id=? AND role=? AND filename=? AND version=? AND deleted_at IS NULL""",
                (utcnow(), job_id, item["role"], item["filename"], item["version"]),
            )
            still_used = db.execute(
                """SELECT 1 FROM document_revisions WHERE job_id=? AND sha256=?
                   AND deleted_at IS NULL LIMIT 1""", (job_id, digest),
            ).fetchone()
            db.execute("COMMIT")
        if not still_used:
            _vault_path(job_id, digest).unlink(missing_ok=True)
            working = workspace(job_id) / (
                "inputs" if item["role"] == "input" else ""
            ) / item["filename"]
            if working.is_file() and not working.is_symlink():
                if _hash(working)[0] == digest:
                    working.unlink()
            if item["role"] == "output":
                with store._connection() as db:
                    try:
                        db.execute("DELETE FROM artifacts WHERE job_id=? AND name=? AND sha256=?",
                                   (job_id, item["filename"], digest))
                    except sqlite3.OperationalError:
                        pass
        deleted += 1
    return {"eligible_versions": len(selected), "deleted_versions": deleted, "dry_run": False}
