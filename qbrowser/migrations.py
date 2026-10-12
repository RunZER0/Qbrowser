"""Versioned, transactional SQLite schema evolution.

Qbrowser's original schema is created by JobStore.initialize(). Migration 1
adopts that existing schema without destroying data; migration 2 introduces
durable execution boundaries. Inspired by Temporal's event history and
LangGraph's explicit thread checkpoints, without requiring a server.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

MIGRATIONS = {
    1: (
        "Adopt original Qbrowser schema",
        (
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY, name TEXT NOT NULL,
                checksum TEXT NOT NULL, applied_at TEXT NOT NULL
            )""",
        ),
    ),
    2: (
        "Durable GUI action journal and supervisor snapshots",
        (
            """CREATE TABLE IF NOT EXISTS gui_actions (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(id),
                attempt INTEGER NOT NULL,
                ordinal INTEGER NOT NULL,
                action TEXT NOT NULL,
                impact TEXT NOT NULL,
                purpose TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                phase TEXT NOT NULL CHECK(phase IN
                   ('prepared','dispatched','confirmed','uncertain')),
                created_at TEXT NOT NULL,
                dispatched_at TEXT,
                resolved_at TEXT,
                result TEXT,
                UNIQUE(job_id, attempt, ordinal)
            )""",
            """CREATE INDEX IF NOT EXISTS idx_gui_actions_job
               ON gui_actions(job_id, attempt, ordinal)""",
            """CREATE TABLE IF NOT EXISTS supervisor_snapshots (
                job_id TEXT NOT NULL REFERENCES jobs(id),
                revision INTEGER NOT NULL,
                attempt INTEGER NOT NULL,
                state_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(job_id, revision)
            )""",
            """CREATE TABLE IF NOT EXISTS subtask_history (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES jobs(id),
                attempt INTEGER NOT NULL,
                key TEXT NOT NULL,
                from_state TEXT,
                to_state TEXT NOT NULL,
                detail TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_subtask_history
               ON subtask_history(job_id, seq)""",
        ),
    ),
}
LATEST_SCHEMA_VERSION = max(MIGRATIONS)


def migrate(db):
    """Apply pending versions atomically, reject unknown future schemas."""
    current = int(db.execute("PRAGMA user_version").fetchone()[0])
    if current > LATEST_SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema {current} is newer than Qbrowser {LATEST_SCHEMA_VERSION}; upgrade software"
        )
    for version, (name, statements) in MIGRATIONS.items():
        if version <= current:
            continue
        checksum = hashlib.sha256("\n".join(statements).encode()).hexdigest()
        db.execute("BEGIN IMMEDIATE")
        try:
            for statement in statements:
                db.execute(statement)
            db.execute(
                """INSERT OR REPLACE INTO schema_migrations
                   (version,name,checksum,applied_at) VALUES(?,?,?,?)""",
                (version, name, checksum, datetime.now(timezone.utc).isoformat()),
            )
            db.execute(f"PRAGMA user_version = {version}")
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    return int(db.execute("PRAGMA user_version").fetchone()[0])
