"""Transactional SQLite job state, an append-only journal, and deliberate recovery."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VALID = {
    "queued", "running", "needs_review", "verify", "completed",
    "failed", "cancelled", "paused", "waiting_approval",
}
FINAL = {"completed", "failed", "cancelled"}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def home() -> Path:
    return Path(os.getenv("QBROWSER_HOME", "~/.qbrowser")).expanduser().resolve()


class JobStore:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else home() / "qbrowser.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 30000")
        try:
            yield db
        finally:
            db.close()

    def initialize(self):
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    instruction TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    priority INTEGER NOT NULL DEFAULT 0,
                    external_approved INTEGER NOT NULL DEFAULT 0,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    last_error TEXT,
                    result TEXT,
                    log_path TEXT
                );
                CREATE INDEX IF NOT EXISTS job_queue
                    ON jobs(status, priority DESC, created_at ASC);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    occurred_at TEXT NOT NULL,
                    type TEXT NOT NULL,
                    data TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS event_job ON events(job_id, seq);
            """)

    @staticmethod
    def _job(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["external_approved"] = bool(item["external_approved"])
        return item

    @staticmethod
    def _event(db, job_id: str, kind: str, **payload):
        db.execute(
            "INSERT INTO events(job_id, occurred_at, type, data) VALUES(?,?,?,?)",
            (job_id, utcnow(), kind, json.dumps(payload, ensure_ascii=False)),
        )

    def create(self, instruction: str, *, priority: int = 0, external_approved: bool = False) -> dict:
        instruction = instruction.strip()
        if not instruction or len(instruction) > 50000:
            raise ValueError("Instruction must contain 1–50000 characters")
        ident = uuid.uuid4().hex[:12]
        stamp = utcnow()
        with self._connection() as db:
            db.execute(
                """INSERT INTO jobs(id,instruction,priority,external_approved,created_at,updated_at)
                   VALUES(?,?,?,?,?,?)""",
                (ident, instruction, int(priority), int(external_approved), stamp, stamp),
            )
            self._event(db, ident, "created", priority=int(priority),
                        external_approved=bool(external_approved))
        return self.get(ident)

    def get(self, job_id: str) -> dict:
        with self._connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._job(row)

    def list(self, limit: int = 100) -> list[dict]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [self._job(row) for row in rows]

    def events(self, job_id: str, *, since: int = 0) -> list[dict]:
        self.get(job_id)
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM events WHERE job_id = ? AND seq > ? ORDER BY seq ASC LIMIT 1000",
                (job_id, since),
            ).fetchall()
        return [{**dict(row), "data": json.loads(row["data"])} for row in rows]

    def claim(self) -> dict | None:
        """Atomically take one job; the desktop process lock prevents parallel operators."""
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT * FROM jobs WHERE status='queued'
                   ORDER BY priority DESC, created_at ASC LIMIT 1"""
            ).fetchone()
            if row is None:
                db.execute("COMMIT")
                return None
            stamp = utcnow()
            db.execute(
                """UPDATE jobs SET status='running', attempt=attempt+1,
                   updated_at=?, started_at=?, finished_at=NULL, last_error=NULL
                   WHERE id=? AND status='queued'""",
                (stamp, stamp, row["id"]),
            )
            self._event(db, row["id"], "started", attempt=row["attempt"] + 1)
            db.execute("COMMIT")
        return self.get(row["id"])

    def transition(self, job_id: str, to: str, *, allowed: set[str],
                   error: str | None = None, result: str | None = None,
                   log_path: str | None = None):
        if to not in VALID:
            raise ValueError("Unknown status")
        if not allowed:
            raise ValueError("Allowed source statuses required")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] not in allowed:
                raise ValueError(f"Cannot change {row['status']} to {to}")
            now = utcnow()
            db.execute(
                """UPDATE jobs SET status=?, updated_at=?, finished_at=?,
                   last_error=COALESCE(?,last_error), result=COALESCE(?,result),
                   log_path=COALESCE(?,log_path) WHERE id=?""",
                (to, now, now if to in FINAL | {"verify", "needs_review"} else None,
                 error, result, log_path, job_id),
            )
            self._event(db, job_id, "status", previous=row["status"], current=to,
                        error=error, result=result, log_path=log_path)
            db.execute("COMMIT")
        return self.get(job_id)

    def approve_external(self, job_id: str) -> dict:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] == "running":
                raise ValueError("Cannot change approval during a running desktop job")
            db.execute("UPDATE jobs SET external_approved=1, updated_at=? WHERE id=?",
                       (utcnow(), job_id))
            self._event(db, job_id, "external_approved")
            db.execute("COMMIT")
        return self.get(job_id)

    def recover_interrupted(self) -> int:
        """Do not replay interrupted GUI actions automatically."""
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT id FROM jobs WHERE status='running'").fetchall()
            for row in rows:
                db.execute(
                    "UPDATE jobs SET status='needs_review', updated_at=?, last_error=? WHERE id=?",
                    (utcnow(), "Worker stopped before outcome verification", row["id"]),
                )
                self._event(db, row["id"], "interrupted",
                            reason="Desktop state unknown; operator review required")
            db.execute("COMMIT")
        return len(rows)
