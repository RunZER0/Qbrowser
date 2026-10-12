"""Transactional SQLite job state, an append-only journal, and deliberate recovery."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import timedelta
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VALID = {
    "queued", "running", "needs_review", "verify", "completed",
    "failed", "cancelled", "paused", "waiting_approval", "waiting",
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
                CREATE TABLE IF NOT EXISTS checkpoints (
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    key TEXT NOT NULL,
                    state TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(job_id, key)
                );
            """)
            existing = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            for field in ("engine", "provider", "model"):
                if field not in existing:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {field} TEXT")
            if "stop_requested" not in existing:
                db.execute("ALTER TABLE jobs ADD COLUMN stop_requested INTEGER NOT NULL DEFAULT 0")
            for field in ("wake_at", "pending_approval", "last_plan"):
                if field not in existing:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {field} TEXT")
            for field in ("input_tokens", "output_tokens"):
                if field not in existing:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {field} INTEGER NOT NULL DEFAULT 0")
            from qbrowser.migrations import migrate
            migrate(db)

    @staticmethod
    def _job(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["external_approved"] = bool(item["external_approved"])
        item["stop_requested"] = bool(item.get("stop_requested", 0))
        return item

    @staticmethod
    def _event(db, job_id: str, kind: str, **payload):
        db.execute(
            "INSERT INTO events(job_id, occurred_at, type, data) VALUES(?,?,?,?)",
            (job_id, utcnow(), kind, json.dumps(payload, ensure_ascii=False)),
        )

    def create(self, instruction: str, *, priority: int = 0,
               external_approved: bool = False, engine: str | None = None,
               provider: str | None = None, model: str | None = None) -> dict:
        instruction = instruction.strip()
        if not instruction or len(instruction) > 50000:
            raise ValueError("Instruction must contain 1–50000 characters")
        if engine not in (None, "native", "agent_s"):
            raise ValueError("Unsupported execution engine")
        if provider not in (None, "zai", "deepseek", "custom"):
            raise ValueError("Unsupported provider")
        if model is not None and (not model.strip() or len(model) > 120):
            raise ValueError("Invalid model identifier")
        ident = uuid.uuid4().hex[:12]
        stamp = utcnow()
        with self._connection() as db:
            db.execute(
                """INSERT INTO jobs(id,instruction,priority,external_approved,created_at,updated_at,
                                    engine,provider,model)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (ident, instruction, int(priority), int(external_approved), stamp, stamp,
                 engine, provider, model),
            )
            self._event(db, ident, "created", priority=int(priority),
                        external_approved=bool(external_approved), engine=engine,
                        provider=provider, model=model)
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
                   updated_at=?, started_at=?, finished_at=NULL, last_error=NULL, stop_requested=0
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

    def record_usage(self, job_id: str, input_tokens: int, output_tokens: int) -> dict:
        """Persist model token accounting before further GUI actions."""
        if any(type(n) is not int or n < 0 for n in (input_tokens, output_tokens)):
            raise ValueError("Token usage must be nonnegative integers")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] != "running":
                raise ValueError("Usage requires a running job")
            db.execute(
                "UPDATE jobs SET input_tokens=input_tokens+?,output_tokens=output_tokens+?,"
                "updated_at=? WHERE id=?",
                (input_tokens, output_tokens, utcnow(), job_id),
            )
            self._event(db, job_id, "model_usage",
                        input_tokens=input_tokens, output_tokens=output_tokens)
            db.execute("COMMIT")
        return self.get(job_id)

    def wait_for(self, job_id: str, seconds: int, reason: str) -> dict:
        """Persistent, restart-safe sleep. The worker re-observes the live screen on wake."""
        if type(seconds) is not int or not 10 <= seconds <= 86400:
            raise ValueError("Wait must be between 10 seconds and 24 hours")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Waiting reason required")
        due = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] != "running":
                raise ValueError("Only a running job may schedule a wait")
            db.execute(
                "UPDATE jobs SET status='waiting',wake_at=?,last_error=?,updated_at=? WHERE id=?",
                (due, reason[:1000], utcnow(), job_id),
            )
            self._event(db, job_id, "waiting", due_at=due, reason=reason[:1000])
            db.execute("COMMIT")
        return self.get(job_id)

    def wake_due(self) -> int:
        """Release due waiting jobs for fresh-screen reinspection; no saved clicks are replayed."""
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT id FROM jobs WHERE status='waiting' AND wake_at<=?",
                (utcnow(),),
            ).fetchall()
            for row in rows:
                db.execute(
                    "UPDATE jobs SET status='queued',wake_at=NULL,updated_at=? WHERE id=?",
                    (utcnow(), row["id"]),
                )
                self._event(db, row["id"], "woke", reason="Scheduled wait elapsed")
            db.execute("COMMIT")
        return len(rows)

    def require_approval(self, job_id: str, reason: str) -> dict:
        """Reject the proposed external operation; never persist executable click replay."""
        if not reason or not isinstance(reason, str):
            raise ValueError("Approval reason required")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] != "running":
                raise ValueError("Approval gate requires running job")
            db.execute(
                "UPDATE jobs SET status='waiting_approval',pending_approval=?,updated_at=? WHERE id=?",
                (reason[:1000], utcnow(), job_id),
            )
            self._event(db, job_id, "approval_required", reason=reason[:1000])
            db.execute("COMMIT")
        return self.get(job_id)

    def request_stop(self, job_id: str) -> dict:
        """Cooperative stop; the active worker handles the in-flight GUI action."""
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,stop_requested FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] in FINAL:
                raise ValueError("Job already finished")
            if row["status"] == "running":
                db.execute("UPDATE jobs SET stop_requested=1,updated_at=? WHERE id=?",
                           (utcnow(), job_id))
                self._event(db, job_id, "stop_requested")
            else:
                db.execute("UPDATE jobs SET status='cancelled',updated_at=?,finished_at=? WHERE id=?",
                           (utcnow(), utcnow(), job_id))
                self._event(db, job_id, "status", previous=row["status"], current="cancelled")
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
            if row["status"] in FINAL:
                raise ValueError("Cannot authorize a finished job")
            db.execute(
                "UPDATE jobs SET external_approved=1,pending_approval=NULL,updated_at=?, "
                "status=CASE WHEN status='waiting_approval' THEN 'queued' ELSE status END "
                "WHERE id=?", (utcnow(), job_id))
            self._event(db, job_id, "external_approved",
                        resumed=row["status"] == "waiting_approval")
            db.execute("COMMIT")
        return self.get(job_id)

    def checkpoint(self, job_id: str, key: str, state: str, detail: str = "") -> dict:
        """Durable progress marker for plans of any shape, created at runtime."""
        if not key or len(key) > 120 or not all(ch.isalnum() or ch in "-_." for ch in key):
            raise ValueError("Checkpoint key must contain 1-120 letters, digits, dots, dashes or underscores")
        if state not in {"pending", "in_progress", "done", "blocked", "skipped"}:
            raise ValueError("Invalid checkpoint state")
        if len(detail) > 10000:
            raise ValueError("Checkpoint detail exceeds 10000 characters")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] in FINAL:
                raise ValueError("Cannot change checkpoints on a finished job")
            stamp = utcnow()
            previous = db.execute(
                "SELECT state FROM checkpoints WHERE job_id=? AND key=?",
                (job_id, key),
            ).fetchone()
            db.execute("""
                INSERT INTO checkpoints(job_id,key,state,detail,updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(job_id,key) DO UPDATE SET state=excluded.state,
                    detail=excluded.detail,updated_at=excluded.updated_at
                """, (job_id, key, state, detail, stamp))
            db.execute(
                """INSERT INTO subtask_history
                   (job_id,attempt,key,from_state,to_state,detail,occurred_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (job_id, row["attempt"] if "attempt" in row.keys() else
                 db.execute("SELECT attempt FROM jobs WHERE id=?", (job_id,)).fetchone()[0],
                 key, previous["state"] if previous else None, state, detail, stamp),
            )
            self._event(db, job_id, "checkpoint", key=key, state=state, detail=detail)
            db.execute("COMMIT")
        return {"job_id": job_id, "key": key, "state": state,
                "detail": detail, "updated_at": stamp}

    def checkpoints(self, job_id: str) -> list[dict]:
        self.get(job_id)
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM checkpoints WHERE job_id=? ORDER BY updated_at, key",
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def heartbeat(self, job_id: str, *, log_bytes: int = 0) -> None:
        """Persist liveness, without claiming GUI task progress or success."""
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE jobs SET updated_at=? WHERE id=? AND status='running'",
                (utcnow(), job_id),
            )
            if changed.rowcount == 1:
                self._event(db, job_id, "heartbeat", log_bytes=log_bytes)
            db.execute("COMMIT")

    def recover_interrupted(self) -> int:
        """Requeue only observational attempts; ambiguous GUI side effects need review.

        This is an explicitly safe subset of Temporal-like workflow recovery:
        the current screen is re-inspected, with no recorded input ever replayed.
        """
        from qbrowser.durable import ActionJournal

        decisions = ActionJournal(self)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """SELECT id,attempt,engine,external_approved,stop_requested
                   FROM jobs WHERE status='running'"""
            ).fetchall()
            for row in rows:
                # Agent-S remains opaque to Qbrowser; never assume its side effects.
                backend = row["engine"] or os.getenv("QBROWSER_ENGINE", "native")
                safe, explanation = decisions.recovery_decision(
                    row["id"], row["attempt"]
                )
                can_resume = (
                    backend == "native" and safe
                    and not row["external_approved"] and not row["stop_requested"]
                )
                target = "queued" if can_resume else "needs_review"
                description = (
                    "Recovered safely: " + explanation
                    if can_resume else "Interrupted GUI work requires owner review: " + explanation
                )
                db.execute(
                    """UPDATE jobs SET status=?,updated_at=?,last_error=? WHERE id=?""",
                    (target, utcnow(), description, row["id"]),
                )
                self._event(
                    db, row["id"], "interrupted",
                    reason=description, auto_queued=can_resume,
                )
            db.execute("COMMIT")
        return len(rows)
