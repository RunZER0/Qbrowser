"""Consistent online SQLite backup (WAL-safe), without exporting credentials or files."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from qbrowser.store import JobStore


def backup_database(store: JobStore, destination: str | Path) -> Path:
    target = Path(destination).expanduser().resolve()
    if target == store.path.resolve():
        raise ValueError("Backup destination must differ from live database")
    if target.exists():
        raise FileExistsError("Refusing to replace an existing backup")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(store.path) as source, sqlite3.connect(target) as result:
            source.backup(result)
            result.execute("PRAGMA quick_check")
            validation = result.execute("PRAGMA quick_check").fetchone()[0]
            if validation != "ok":
                raise RuntimeError(f"Backup integrity check failed: {validation}")
        if os.name != "nt":
            os.chmod(target, 0o600)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target
