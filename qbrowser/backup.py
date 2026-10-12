"""Consistent online SQLite backup (WAL-safe), without exporting credentials or files."""

from __future__ import annotations

import os
import hashlib
import json
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from qbrowser.store import JobStore, home


def backup_database(store: JobStore, destination: str | Path) -> Path:
    target = Path(destination).expanduser().resolve()
    if target == store.path.resolve():
        raise ValueError("Backup destination must differ from live database")
    if target.exists():
        raise FileExistsError("Refusing to replace an existing backup")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(sqlite3.connect(store.path)) as source, closing(sqlite3.connect(target)) as result:
            source.backup(result)
            validation = result.execute("PRAGMA quick_check").fetchone()[0]
            if validation != "ok":
                raise RuntimeError(f"Backup integrity check failed: {validation}")
        if os.name != "nt":
            os.chmod(target, 0o600)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _data_files(root: Path, prefix: str) -> list[tuple[Path, str]]:
    if not root.exists():
        return []
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"Backup source is not a regular directory: {prefix}")
    result = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Refusing to back up symlink: {path}")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            result.append((path, f"{prefix}/{relative}"))
        elif not path.is_dir():
            raise ValueError(f"Refusing unsupported filesystem entry: {path}")
    return result


def backup_full(store: JobStore, destination: str | Path) -> Path:
    """Create a hash-verified archive of the database and local Qbrowser files.

    Provider credentials and .env files are deliberately excluded. The archive
    contains task content and should be protected as private data.
    """
    target = Path(destination).expanduser().resolve()
    if target.exists() or target == store.path.resolve():
        raise FileExistsError("Refusing to replace an existing backup")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch(mode=0o600, exist_ok=False)
    try:
        with tempfile.TemporaryDirectory(prefix="qbrowser-backup-") as temporary:
            temp_root = Path(temporary)
            db_copy = temp_root / "qbrowser.db"
            with closing(sqlite3.connect(store.path)) as source, closing(sqlite3.connect(db_copy)) as result:
                source.backup(result)
                check = result.execute("PRAGMA quick_check").fetchone()[0]
                if check != "ok":
                    raise RuntimeError(f"Backup integrity check failed: {check}")
            items: list[tuple[Path, str]] = [(db_copy, "qbrowser.db")]
            data_root = home()
            for folder in ("vault", "workspaces", "evidence"):
                items.extend(_data_files(data_root / folder, folder))
            manifest = {
                "format": "qbrowser-full-backup",
                "version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "files": [{"path": archive_name, "sha256": _digest(path),
                           "bytes": path.stat().st_size}
                          for path, archive_name in items],
            }
            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED,
                                 allowZip64=True) as archive:
                archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
                for path, archive_name in items:
                    archive.write(path, archive_name)
        if os.name != "nt":
            os.chmod(target, 0o600)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def restore_full(archive_path: str | Path, destination: str | Path) -> Path:
    """Verify and restore an archive into a new QBROWSER_HOME directory."""
    source = Path(archive_path).expanduser().resolve()
    target = Path(destination).expanduser().resolve()
    if not source.is_file() or source.is_symlink():
        raise ValueError("Backup archive must be a regular file")
    if target.exists():
        raise FileExistsError("Restore destination must not already exist")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".qbrowser-restore-", dir=target.parent))
    try:
        with zipfile.ZipFile(source) as archive:
            names = archive.namelist()
            if len(names) > 100_000 or len(names) != len(set(names)):
                raise ValueError("Backup contains duplicate or excessive entries")
            if "manifest.json" not in names:
                raise ValueError("Backup manifest is missing")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != "qbrowser-full-backup" or manifest.get("version") != 1:
                raise ValueError("Unsupported Qbrowser backup format")
            records = manifest.get("files")
            if not isinstance(records, list) or len(records) != len(names) - 1:
                raise ValueError("Backup manifest does not match archive entries")
            expected = {item.get("path"): item for item in records
                        if isinstance(item, dict)}
            if len(expected) != len(records) or set(expected) != set(names) - {"manifest.json"}:
                raise ValueError("Backup manifest paths do not match archive")
            total = 0
            for name, metadata in expected.items():
                relative = Path(name)
                if relative.is_absolute() or ".." in relative.parts or "\\" in name:
                    raise ValueError("Backup contains an unsafe path")
                if not relative.parts or relative.parts[0] not in {
                        "qbrowser.db", "vault", "workspaces", "evidence"}:
                    raise ValueError("Backup contains an unsupported path")
                member = archive.getinfo(name)
                if member.is_dir() or member.file_size != metadata.get("bytes"):
                    raise ValueError("Backup member size does not match manifest")
                total += member.file_size
                if total > 20 * 1024 * 1024 * 1024:
                    raise ValueError("Backup exceeds the 20 GB restore limit")
                output = staging / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as input_stream, output.open("xb") as output_stream:
                    shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)
                if _digest(output) != metadata.get("sha256"):
                    raise ValueError(f"Backup file hash mismatch: {name}")
        db_path = staging / "qbrowser.db"
        if not db_path.is_file():
            raise ValueError("Backup does not contain qbrowser.db")
        with closing(sqlite3.connect(db_path)) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Restored database failed integrity check")
        os.replace(staging, target)
        return target
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
