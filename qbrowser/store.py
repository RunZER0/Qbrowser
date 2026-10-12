"""Transactional SQLite job state, an append-only journal, and deliberate recovery."""

from __future__ import annotations

import json
import hashlib
import math
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

VALID = {
    "queued", "running", "needs_review", "verify", "completed",
    "failed", "cancelled", "paused", "waiting_approval", "waiting", "waiting_user",
}
FINAL = {"completed", "failed", "cancelled"}
SCHEMA_VERSION = 8


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
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
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
                CREATE TABLE IF NOT EXISTS plan_versions (
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    version INTEGER NOT NULL,
                    items TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(job_id, version)
                );
                CREATE TABLE IF NOT EXISTS plan_items (
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    key TEXT NOT NULL,
                    parent_key TEXT,
                    title TEXT NOT NULL,
                    acceptance TEXT NOT NULL,
                    dependencies TEXT NOT NULL DEFAULT '[]',
                    state TEXT NOT NULL DEFAULT 'pending',
                    evidence TEXT NOT NULL DEFAULT '',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(job_id, key)
                );
                CREATE TABLE IF NOT EXISTS action_journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    action_number INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    impact TEXT NOT NULL,
                    purpose TEXT NOT NULL DEFAULT '',
                    details TEXT NOT NULL DEFAULT '{}',
                    state TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    error_type TEXT,
                    UNIQUE(job_id, action_number)
                );
                CREATE INDEX IF NOT EXISTS action_job ON action_journal(job_id, action_number);
                CREATE TABLE IF NOT EXISTS job_messages (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    role TEXT NOT NULL CHECK(role IN ('user','assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS message_job ON job_messages(job_id, seq);
                CREATE TABLE IF NOT EXISTS evidence (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    relative_path TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    bytes INTEGER NOT NULL,
                    captured_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS evidence_job ON evidence(job_id, captured_at);
                CREATE TABLE IF NOT EXISTS task_assessments (
                    job_id TEXT PRIMARY KEY REFERENCES jobs(id),
                    version INTEGER NOT NULL,
                    feasibility TEXT NOT NULL,
                    complexity TEXT NOT NULL,
                    requirements TEXT NOT NULL,
                    success_criteria TEXT NOT NULL,
                    ambiguities TEXT NOT NULL,
                    assumptions TEXT NOT NULL,
                    estimated_steps INTEGER NOT NULL,
                    estimated_minutes INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    id TEXT PRIMARY KEY,
                    instruction TEXT NOT NULL,
                    interval_seconds INTEGER NOT NULL,
                    next_run_at TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    priority INTEGER NOT NULL DEFAULT 0,
                    engine TEXT,
                    provider TEXT,
                    model TEXT,
                    last_job_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS scheduled_due
                    ON scheduled_tasks(enabled,next_run_at);
                CREATE TABLE IF NOT EXISTS verification_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    outcome TEXT NOT NULL CHECK(outcome IN ('pass','fail','uncertain')),
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    report TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS verification_job
                    ON verification_reviews(job_id,id);
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
            for field, definition in (
                ("plan_version", "INTEGER NOT NULL DEFAULT 0"),
                ("plan_reconciled_action", "INTEGER NOT NULL DEFAULT 0"),
                ("action_count", "INTEGER NOT NULL DEFAULT 0"),
                ("memory_updated_action", "INTEGER NOT NULL DEFAULT 0"),
                ("memory_version", "INTEGER NOT NULL DEFAULT 0"),
                ("working_memory", "TEXT NOT NULL DEFAULT '{}'"),
                ("pending_question", "TEXT"),
                ("estimated_tokens", "INTEGER NOT NULL DEFAULT 0"),
                ("model_latency_ms", "INTEGER NOT NULL DEFAULT 0"),
                ("estimated_cost_usd", "REAL"),
                ("cost_configured", "INTEGER NOT NULL DEFAULT 0"),
                ("pending_action_approval", "TEXT"),
                ("approved_action_approval", "TEXT"),
            ):
                if field not in existing:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {field} {definition}")
            latest = db.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0]
            if latest > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema version {latest} is newer than this Qbrowser build ({SCHEMA_VERSION})")
            if latest < 1:
                # Version 1 records the fully idempotent legacy-to-current bootstrap above.
                db.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES(1,?,?)",
                           ("durable-jobs-plans-artifacts", utcnow()))
        from qbrowser.artifact_vault import _schema as migrate_artifacts
        migrate_artifacts(self)
        with self._connection() as db:
            db.execute("INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) VALUES(3,?,?)",
                       ("structured-task-assessment", utcnow()))
            db.execute("INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) VALUES(4,?,?)",
                       ("persistent-interval-scheduler", utcnow()))
            db.execute("INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) VALUES(5,?,?)",
                       ("independent-outcome-verification", utcnow()))
            db.executescript("""
                CREATE TABLE IF NOT EXISTS long_term_memory (
                    id TEXT PRIMARY KEY,
                    category TEXT NOT NULL CHECK(category IN ('preference','procedure','note')),
                    content TEXT NOT NULL,
                    tags TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    embedding BLOB,
                    embedding_model TEXT,
                    embedding_dimensions INTEGER,
                    superseded_by TEXT
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS long_term_memory_fts USING fts5(
                    content, category, tags,
                    content='long_term_memory', content_rowid='rowid'
                );
                CREATE TRIGGER IF NOT EXISTS long_term_memory_ai AFTER INSERT ON long_term_memory BEGIN
                    INSERT INTO long_term_memory_fts(rowid,content,category,tags)
                    VALUES (new.rowid,new.content,new.category,new.tags);
                END;
                CREATE TRIGGER IF NOT EXISTS long_term_memory_ad AFTER DELETE ON long_term_memory BEGIN
                    INSERT INTO long_term_memory_fts(long_term_memory_fts,rowid,content,category,tags)
                    VALUES ('delete',old.rowid,old.content,old.category,old.tags);
                END;
                CREATE TRIGGER IF NOT EXISTS long_term_memory_au AFTER UPDATE ON long_term_memory BEGIN
                    INSERT INTO long_term_memory_fts(long_term_memory_fts,rowid,content,category,tags)
                    VALUES ('delete',old.rowid,old.content,old.category,old.tags);
                    INSERT INTO long_term_memory_fts(rowid,content,category,tags)
                    VALUES (new.rowid,new.content,new.category,new.tags);
                END;
            """)
            db.execute("INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) VALUES(6,?,?)",
                       ("owner-curated-cross-task-memory", utcnow()))
            memory_columns = {row["name"] for row in db.execute(
                "PRAGMA table_info(long_term_memory)")}
            for field, definition in (("embedding", "BLOB"),
                                      ("embedding_model", "TEXT"),
                                      ("embedding_dimensions", "INTEGER")):
                if field not in memory_columns:
                    db.execute(f"ALTER TABLE long_term_memory ADD COLUMN {field} {definition}")
            db.execute("INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) VALUES(7,?,?)",
                       ("optional-local-vector-memory-search", utcnow()))
            if "superseded_by" not in memory_columns:
                db.execute("ALTER TABLE long_term_memory ADD COLUMN superseded_by TEXT")
            db.execute("INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) VALUES(8,?,?)",
                       ("explicit-memory-supersession", utcnow()))

    def schema_version(self) -> int:
        with self._connection() as db:
            return int(db.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0])

    def add_long_term_memory(self, content: str, *, category: str = "note",
                             tags: list[str] | None = None,
                             expires_days: int | None = None,
                             supersedes_id: str | None = None,
                             embedder: Any | None = None) -> dict:
        """Save owner-curated, non-secret knowledge for retrieval in later tasks."""
        if category not in {"preference", "procedure", "note"}:
            raise ValueError("Memory category must be preference, procedure, or note")
        if not isinstance(content, str) or not content.strip() or len(content) > 5000:
            raise ValueError("Memory content must contain 1-5000 characters")
        content = content.strip()
        if re.search(r"(?i)\b(?:bearer\s+[a-z0-9._~+/-]{12,}|(?:api[_ -]?key|password|access[_ -]?token)\s*[:=])", content):
            raise ValueError("Credentials cannot be stored in cross-task memory")
        if tags is None:
            tags = []
        if (not isinstance(tags, list) or len(tags) > 20 or
                any(not isinstance(tag, str) or not tag.strip() or len(tag) > 80 for tag in tags)):
            raise ValueError("Memory tags must be up to 20 short strings")
        if expires_days is not None and (type(expires_days) is not int or not 1 <= expires_days <= 3650):
            raise ValueError("Memory expiration must be 1-3650 days")
        if supersedes_id is not None and (not isinstance(supersedes_id, str) or
                                          not re.fullmatch(r"[0-9a-f]{32}", supersedes_id)):
            raise ValueError("Superseded memory ID must be a 32-character memory ID")
        tags_text = ", ".join(dict.fromkeys(tag.strip() for tag in tags))
        digest = hashlib.sha256(
            (category + "\0" + content + "\0" + tags_text).encode("utf-8")).hexdigest()
        stamp = datetime.now(timezone.utc)
        expires_at = (stamp + timedelta(days=expires_days)).isoformat() if expires_days else None
        memory_id = uuid.uuid4().hex
        selected_fields = """id,category,content,tags,content_hash,created_at,expires_at,
            embedding_model,embedding_dimensions,superseded_by"""
        with self._connection() as db:
            duplicate = db.execute(
                f"SELECT {selected_fields} FROM long_term_memory WHERE content_hash=?", (digest,)
            ).fetchone()
        if duplicate is not None and supersedes_id is None:
            return dict(duplicate)
        vector = None
        vector_model = None
        if embedder is not None:
            from qbrowser.semantic_memory import embed_texts, model_name
            vector_model = model_name()
            vector = embed_texts([self._memory_embedding_text(category, content, tags_text)],
                                 embedder=embedder, name=vector_model)[0]
        else:
            from qbrowser.semantic_memory import enabled
            if enabled():
                from qbrowser.semantic_memory import embed_texts, model_name
                vector_model = model_name()
                vector = embed_texts([self._memory_embedding_text(category, content, tags_text)],
                                     name=vector_model)[0]
        with self._connection() as db:
            values = None
            if vector is not None:
                from qbrowser.semantic_memory import pack_vector
                values = (pack_vector(vector), vector_model, len(vector))
            db.execute("BEGIN IMMEDIATE")
            try:
                old = None
                if supersedes_id is not None:
                    old = db.execute("SELECT id,superseded_by FROM long_term_memory WHERE id=?",
                                     (supersedes_id,)).fetchone()
                    if old is None or old["superseded_by"] is not None:
                        raise ValueError("Superseded memory must exist and still be active")
                db.execute("""INSERT OR IGNORE INTO long_term_memory
                    (id,category,content,tags,content_hash,created_at,expires_at,
                     embedding,embedding_model,embedding_dimensions)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (memory_id, category, content, tags_text, digest, stamp.isoformat(), expires_at,
                     *(values or (None, None, None))))
                row = db.execute(
                    f"SELECT {selected_fields} FROM long_term_memory WHERE content_hash=?", (digest,)
                ).fetchone()
                if old is not None:
                    if row["id"] == old["id"]:
                        raise ValueError("A memory cannot supersede itself")
                    db.execute("UPDATE long_term_memory SET superseded_by=? WHERE id=?",
                               (row["id"], old["id"]))
                    row = db.execute(
                        f"SELECT {selected_fields} FROM long_term_memory WHERE id=?", (row["id"],)
                    ).fetchone()
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        return dict(row)

    @staticmethod
    def _memory_embedding_text(category: str, content: str, tags: str) -> str:
        return f"{category}: {content}\nTags: {tags}" if tags else f"{category}: {content}"

    def search_long_term_memory(self, query: str, *, limit: int = 8,
                                 embedder: Any | None = None) -> list[dict]:
        """Retrieve prior notes by FTS5 or optional local semantic/hybrid ranking."""
        if not isinstance(query, str):
            raise ValueError("Memory search query must be text")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Memory search limit must be between 1 and 50")
        terms = list(dict.fromkeys(re.findall(r"[\w-]{2,}", query, flags=re.UNICODE)))[:12]
        match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
        semantic_enabled = embedder is not None
        if embedder is None:
            from qbrowser.semantic_memory import enabled
            semantic_enabled = enabled()
        if not terms and not semantic_enabled:
            return []
        lexical: list[dict] = []
        with self._connection() as db:
            if terms:
                rows = db.execute("""SELECT m.id,m.category,m.content,m.tags,m.created_at,m.expires_at,
                        bm25(long_term_memory_fts) AS rank
                    FROM long_term_memory_fts f JOIN long_term_memory m ON m.rowid=f.rowid
                    WHERE long_term_memory_fts MATCH ? AND m.superseded_by IS NULL
                        AND (m.expires_at IS NULL OR m.expires_at>?)
                    ORDER BY rank,m.created_at DESC LIMIT 500""", (match, utcnow())).fetchall()
                lexical = [dict(row) for row in rows]
            if not semantic_enabled:
                return lexical[:limit]

            from qbrowser.semantic_memory import (
                cosine, embed_texts, model_name, unpack_vector,
            )
            selected_model = model_name()
            query_vector = embed_texts([query], embedder=embedder, name=selected_model)[0]
            rows = db.execute("""SELECT id,category,content,tags,created_at,expires_at,
                    embedding,embedding_dimensions FROM long_term_memory
                WHERE embedding_model=? AND embedding IS NOT NULL
                    AND superseded_by IS NULL
                    AND (expires_at IS NULL OR expires_at>?) ORDER BY created_at DESC LIMIT 5000""",
                (selected_model, utcnow())).fetchall()

        lexical_rank = {item["id"]: rank for rank, item in enumerate(lexical, 1)}
        semantic_ranked = []
        for row in rows:
            try:
                vector = unpack_vector(row["embedding"], row["embedding_dimensions"])
            except (TypeError, ValueError):
                continue
            score = cosine(query_vector, vector)
            item = dict(row)
            item.pop("embedding", None)
            item.pop("embedding_dimensions", None)
            semantic_ranked.append((score, item))
        semantic_ranked.sort(key=lambda value: value[0], reverse=True)
        semantic = [item for _score, item in semantic_ranked[:500]]
        semantic_rank = {item["id"]: rank for rank, item in enumerate(semantic, 1)}
        combined: dict[str, dict] = {item["id"]: item for item in lexical}
        combined.update({item["id"]: item for item in semantic})
        scored = []
        for ident, item in combined.items():
            lex = lexical_rank.get(ident)
            sem = semantic_rank.get(ident)
            rrf = (1 / (60 + lex) if lex else 0) + (1 / (60 + sem) if sem else 0)
            item["lexical_rank"] = lex
            item["semantic_rank"] = sem
            item["relevance"] = round(rrf, 6)
            item["retrieval_method"] = ("hybrid" if lex and sem else
                                       "semantic" if sem else "lexical")
            scored.append(item)
        scored.sort(key=lambda item: (item["relevance"], item["created_at"]), reverse=True)
        return scored[:limit]

    def reindex_long_term_memory(self, *, embedder: Any | None = None,
                                 limit: int = 5000, batch_size: int = 32) -> dict:
        """Explicitly embed active curated notes locally for semantic retrieval."""
        if type(limit) is not int or not 1 <= limit <= 5000:
            raise ValueError("Memory reindex limit must be between 1 and 5000")
        if type(batch_size) is not int or not 1 <= batch_size <= 128:
            raise ValueError("Memory embedding batch size must be between 1 and 128")
        from qbrowser.semantic_memory import embed_texts, model_name, pack_vector
        selected_model = model_name()
        with self._connection() as db:
            rows = [dict(row) for row in db.execute("""SELECT id,category,content,tags
                FROM long_term_memory WHERE superseded_by IS NULL
                    AND (expires_at IS NULL OR expires_at>?)
                ORDER BY created_at DESC LIMIT ?""", (utcnow(), limit))]
        indexed = 0
        for start in range(0, len(rows), batch_size):
            batch = rows[start:start + batch_size]
            texts = [self._memory_embedding_text(row["category"], row["content"], row["tags"])
                     for row in batch]
            vectors = embed_texts(texts, embedder=embedder, name=selected_model)
            with self._connection() as db:
                db.executemany("""UPDATE long_term_memory
                    SET embedding=?,embedding_model=?,embedding_dimensions=? WHERE id=?""",
                    [(pack_vector(vector), selected_model, len(vector), row["id"])
                     for row, vector in zip(batch, vectors)])
            indexed += len(batch)
        return {"indexed": indexed, "model": selected_model}

    def list_long_term_memory(self, *, limit: int = 100,
                              include_superseded: bool = False) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Memory list limit must be between 1 and 500")
        if not isinstance(include_superseded, bool):
            raise ValueError("include_superseded must be a boolean")
        with self._connection() as db:
            rows = db.execute("""SELECT id,category,content,tags,created_at,expires_at,superseded_by
                FROM long_term_memory WHERE (expires_at IS NULL OR expires_at>?)
                    AND (? OR superseded_by IS NULL)
                ORDER BY created_at DESC LIMIT ?""",
                (utcnow(), int(include_superseded), limit)).fetchall()
        return [dict(row) for row in rows]

    def delete_long_term_memory(self, memory_id: str) -> bool:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            exists = db.execute("SELECT 1 FROM long_term_memory WHERE id=?", (memory_id,)).fetchone()
            if exists is None:
                db.execute("ROLLBACK")
                return False
            cur = db.execute("""WITH RECURSIVE lineage(id) AS (
                    SELECT id FROM long_term_memory WHERE id=?
                    UNION ALL
                    SELECT m.id FROM long_term_memory m JOIN lineage l ON m.superseded_by=l.id
                ) DELETE FROM long_term_memory WHERE id IN (SELECT id FROM lineage)""",
                (memory_id,))
            del cur
            db.execute("COMMIT")
        return True

    def migration_history(self) -> list[dict]:
        with self._connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT version,name,applied_at FROM schema_migrations ORDER BY version")]

    def assessment(self, job_id: str) -> dict | None:
        self.get(job_id)
        with self._connection() as db:
            row = db.execute("SELECT * FROM task_assessments WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        for key in ("requirements", "success_criteria", "ambiguities", "assumptions"):
            item[key] = json.loads(item[key])
        return item

    def assess_task(self, job_id: str, *, feasibility: str, complexity: str,
                    requirements: list[str], success_criteria: list[str],
                    ambiguities: list[str] | None = None,
                    assumptions: list[str] | None = None,
                    estimated_steps: int = 1, estimated_minutes: int = 1) -> dict:
        if feasibility not in {"ready", "needs_clarification", "infeasible"}:
            raise ValueError("Invalid task feasibility")
        if complexity not in {"low", "medium", "high"}:
            raise ValueError("Invalid task complexity")
        def text_list(name: str, values: list[str] | None, *, required: bool = False) -> list[str]:
            values = [] if values is None else values
            if (not isinstance(values, list) or len(values) > 40 or
                    (required and not values) or
                    any(not isinstance(value, str) or not value.strip() or len(value) > 1000
                        for value in values)):
                raise ValueError(f"{name} must contain up to 40 short text entries")
            return [value.strip() for value in values]
        normalized = {
            "requirements": text_list("requirements", requirements, required=True),
            "success_criteria": text_list("success_criteria", success_criteria, required=True),
            "ambiguities": text_list("ambiguities", ambiguities),
            "assumptions": text_list("assumptions", assumptions),
        }
        if feasibility == "ready" and normalized["ambiguities"]:
            raise ValueError("Resolve task ambiguities before marking the task ready")
        if feasibility == "needs_clarification" and not normalized["ambiguities"]:
            raise ValueError("List the missing information that requires clarification")
        if type(estimated_steps) is not int or not 1 <= estimated_steps <= 1000:
            raise ValueError("Estimated steps must be between 1 and 1000")
        if type(estimated_minutes) is not int or not 1 <= estimated_minutes <= 1440:
            raise ValueError("Estimated minutes must be between 1 and 1440")
        stamp = utcnow()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None:
                db.execute("ROLLBACK")
                raise KeyError(job_id)
            if job["status"] != "running":
                db.execute("ROLLBACK")
                raise ValueError("Task assessment requires an active worker")
            old = db.execute("SELECT version FROM task_assessments WHERE job_id=?", (job_id,)).fetchone()
            version = old["version"] + 1 if old else 1
            db.execute("""INSERT INTO task_assessments
                (job_id,version,feasibility,complexity,requirements,success_criteria,
                 ambiguities,assumptions,estimated_steps,estimated_minutes,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET
                version=excluded.version,feasibility=excluded.feasibility,
                complexity=excluded.complexity,requirements=excluded.requirements,
                success_criteria=excluded.success_criteria,ambiguities=excluded.ambiguities,
                assumptions=excluded.assumptions,estimated_steps=excluded.estimated_steps,
                estimated_minutes=excluded.estimated_minutes,updated_at=excluded.updated_at""",
                (job_id, version, feasibility, complexity,
                 json.dumps(normalized["requirements"], ensure_ascii=False),
                 json.dumps(normalized["success_criteria"], ensure_ascii=False),
                 json.dumps(normalized["ambiguities"], ensure_ascii=False),
                 json.dumps(normalized["assumptions"], ensure_ascii=False),
                 estimated_steps, estimated_minutes, stamp))
            self._event(db, job_id, "task_assessed", version=version,
                        feasibility=feasibility, complexity=complexity,
                        requirement_count=len(normalized["requirements"]),
                        criteria_count=len(normalized["success_criteria"]),
                        estimated_steps=estimated_steps, estimated_minutes=estimated_minutes)
            db.execute("COMMIT")
        return self.assessment(job_id)

    def create_schedule(self, instruction: str, *, interval_seconds: int,
                        first_run_seconds: int | None = None, priority: int = 0,
                        engine: str | None = None, provider: str | None = None,
                        model: str | None = None) -> dict:
        instruction = instruction.strip()
        if not instruction or len(instruction) > 50000:
            raise ValueError("Instruction must contain 1–50000 characters")
        if type(interval_seconds) is not int or not 60 <= interval_seconds <= 31536000:
            raise ValueError("Schedule interval must be between 60 seconds and one year")
        delay = interval_seconds if first_run_seconds is None else first_run_seconds
        if type(delay) is not int or not 0 <= delay <= interval_seconds:
            raise ValueError("First run delay must be between zero and the interval")
        if type(priority) is not int or not -100 <= priority <= 100:
            raise ValueError("Priority must be between -100 and 100")
        if engine is not None and not re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", engine):
            raise ValueError("Unsupported execution engine")
        if provider not in (None, "zai", "deepseek", "custom"):
            raise ValueError("Unsupported provider")
        if model is not None and (not model.strip() or len(model) > 120):
            raise ValueError("Invalid model identifier")
        ident, stamp = uuid.uuid4().hex[:12], datetime.now(timezone.utc)
        due = (stamp + timedelta(seconds=delay)).isoformat()
        with self._connection() as db:
            db.execute("""INSERT INTO scheduled_tasks
                (id,instruction,interval_seconds,next_run_at,priority,engine,provider,model,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (ident, instruction, interval_seconds, due, priority, engine, provider, model,
                 stamp.isoformat(), stamp.isoformat()))
        return self.get_schedule(ident)

    def get_schedule(self, schedule_id: str) -> dict:
        with self._connection() as db:
            row = db.execute("SELECT * FROM scheduled_tasks WHERE id=?", (schedule_id,)).fetchone()
        if row is None:
            raise KeyError(schedule_id)
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        return item

    def list_schedules(self) -> list[dict]:
        with self._connection() as db:
            rows = db.execute("SELECT * FROM scheduled_tasks ORDER BY next_run_at,id").fetchall()
        return [{**dict(row), "enabled": bool(row["enabled"])} for row in rows]

    def cancel_schedule(self, schedule_id: str) -> dict:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE scheduled_tasks SET enabled=0,updated_at=? WHERE id=?",
                                 (utcnow(), schedule_id)).rowcount
            if not changed:
                db.execute("ROLLBACK")
                raise KeyError(schedule_id)
            db.execute("COMMIT")
        return self.get_schedule(schedule_id)

    def record_verification(self, job_id: str, *, outcome: str, provider: str,
                            model: str, report: dict, input_tokens: int = 0,
                            output_tokens: int = 0, latency_ms: int = 0) -> dict:
        if outcome not in {"pass", "fail", "uncertain"}:
            raise ValueError("Invalid verification outcome")
        if not provider or len(provider) > 80 or not model or len(model) > 160:
            raise ValueError("Verification provider and model are required")
        if not isinstance(report, dict) or len(json.dumps(report, ensure_ascii=False)) > 50000:
            raise ValueError("Verification report is invalid or too large")
        if any(type(value) is not int or value < 0
               for value in (input_tokens, output_tokens, latency_ms)):
            raise ValueError("Verification metrics must be nonnegative integers")
        self.get(job_id)
        created = utcnow()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""INSERT INTO verification_reviews
                (job_id,outcome,provider,model,report,input_tokens,output_tokens,latency_ms,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (job_id, outcome, provider, model,
                 json.dumps(report, ensure_ascii=False), input_tokens, output_tokens,
                 latency_ms, created))
            ident = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            self._event(db, job_id, "verification_reviewed", review_id=ident,
                        outcome=outcome, provider=provider, model=model,
                        input_tokens=input_tokens, output_tokens=output_tokens,
                        latency_ms=latency_ms)
            db.execute("COMMIT")
        return self.verification_review(job_id, int(ident))

    def verification_review(self, job_id: str, review_id: int) -> dict:
        with self._connection() as db:
            row = db.execute("SELECT * FROM verification_reviews WHERE job_id=? AND id=?",
                             (job_id, review_id)).fetchone()
        if row is None:
            raise KeyError(review_id)
        item = dict(row)
        item["report"] = json.loads(item["report"])
        return item

    def verification_reviews(self, job_id: str) -> list[dict]:
        self.get(job_id)
        with self._connection() as db:
            rows = db.execute("SELECT * FROM verification_reviews WHERE job_id=? ORDER BY id",
                              (job_id,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["report"] = json.loads(item["report"])
            result.append(item)
        return result

    def enqueue_due_schedules(self, *, now: datetime | None = None, limit: int = 100) -> int:
        """Atomically enqueue one task per due interval and advance each schedule."""
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("Scheduler time must be timezone-aware")
        stamp = current.astimezone(timezone.utc).isoformat()
        created = 0
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT * FROM scheduled_tasks WHERE enabled=1 AND next_run_at<=? "
                              "ORDER BY next_run_at LIMIT ?", (stamp, max(1, min(limit, 500)))).fetchall()
            for row in rows:
                ident = uuid.uuid4().hex[:12]
                db.execute("""INSERT INTO jobs
                    (id,instruction,status,priority,external_approved,attempt,created_at,updated_at,
                     engine,provider,model)
                    VALUES(?,?,'queued',?,0,0,?,?,?,?,?)""",
                    (ident, row["instruction"], row["priority"], stamp, stamp,
                     row["engine"], row["provider"], row["model"]))
                self._event(db, ident, "created", priority=row["priority"],
                            external_approved=False, engine=row["engine"], provider=row["provider"],
                            model=row["model"], schedule_id=row["id"])
                previous_due = datetime.fromisoformat(row["next_run_at"])
                if previous_due.tzinfo is None:
                    previous_due = previous_due.replace(tzinfo=timezone.utc)
                interval = int(row["interval_seconds"])
                missed = max(1, int((current - previous_due).total_seconds() // interval) + 1)
                next_due = previous_due + timedelta(seconds=missed * interval)
                db.execute("UPDATE scheduled_tasks SET next_run_at=?,last_job_id=?,updated_at=? WHERE id=?",
                           (next_due.astimezone(timezone.utc).isoformat(), ident, stamp, row["id"]))
                created += 1
            db.execute("COMMIT")
        return created

    @staticmethod
    def _job(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["external_approved"] = bool(item["external_approved"])
        item["stop_requested"] = bool(item.get("stop_requested", 0))
        if item.get("created_at"):
            try:
                end = datetime.fromisoformat(item.get("finished_at") or utcnow())
                item["elapsed_ms"] = max(0, round((end - datetime.fromisoformat(
                    item["created_at"])).total_seconds() * 1000))
            except (TypeError, ValueError):
                item["elapsed_ms"] = None
        else:
            item["elapsed_ms"] = None
        if item.get("started_at"):
            try:
                end = datetime.fromisoformat(item.get("finished_at") or utcnow())
                item["execution_ms"] = max(0, round((end - datetime.fromisoformat(
                    item["started_at"])).total_seconds() * 1000))
            except (TypeError, ValueError):
                item["execution_ms"] = None
        else:
            item["execution_ms"] = None
        return item

    @staticmethod
    def public_job(item: dict[str, Any]) -> dict[str, Any]:
        """Remove internal one-time approval material from operator/API responses."""
        return {key: value for key, value in item.items()
                if key not in {"pending_action_approval", "approved_action_approval"}}

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
        if engine is not None and not re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", engine):
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
        return [self.public_job(self._job(row)) for row in rows]

    def metrics_summary(self) -> dict:
        with self._connection() as db:
            totals = db.execute("""SELECT COUNT(*) AS jobs,
                COALESCE(SUM(input_tokens),0) AS input_tokens,
                COALESCE(SUM(output_tokens),0) AS output_tokens,
                COALESCE(SUM(estimated_tokens),0) AS estimated_tokens,
                COALESCE(SUM(estimated_cost_usd),0) AS estimated_cost_usd,
                COALESCE(SUM(cost_configured),0) AS cost_configured_jobs,
                COALESCE(AVG(model_latency_ms),0) AS average_model_latency_ms,
                COALESCE(AVG((julianday(finished_at)-julianday(started_at))*86400000),0)
                    AS average_execution_ms
                FROM jobs""").fetchone()
            statuses = {row["status"]: row["count"] for row in db.execute(
                "SELECT status,COUNT(*) AS count FROM jobs GROUP BY status")}
            providers = [dict(row) for row in db.execute("""SELECT
                COALESCE(provider,'default') AS provider,COALESCE(model,'default') AS model,
                COUNT(*) AS jobs,COALESCE(SUM(input_tokens),0) AS input_tokens,
                COALESCE(SUM(output_tokens),0) AS output_tokens,
                COALESCE(SUM(estimated_cost_usd),0) AS estimated_cost_usd
                FROM jobs GROUP BY provider,model ORDER BY jobs DESC LIMIT 50""")]
            recent = [dict(row) for row in db.execute("""SELECT substr(created_at,1,10) AS day,
                COUNT(*) AS jobs FROM jobs WHERE created_at>=? GROUP BY day ORDER BY day""",
                ((datetime.now(timezone.utc)-timedelta(days=30)).isoformat(),))]
        return {"jobs": int(totals["jobs"]), "jobs_by_status": statuses,
                "input_tokens": int(totals["input_tokens"]),
                "output_tokens": int(totals["output_tokens"]),
                "estimated_tokens": int(totals["estimated_tokens"]),
                "estimated_cost_usd": float(totals["estimated_cost_usd"]),
                "cost_configured_jobs": int(totals["cost_configured_jobs"]),
                "average_model_latency_ms": round(float(totals["average_model_latency_ms"])),
                "average_execution_ms": round(float(totals["average_execution_ms"])),
                "providers": providers, "jobs_created_last_30_days": recent}

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
            if to in FINAL | {"verify", "needs_review", "paused", "waiting", "waiting_user"}:
                db.execute("UPDATE jobs SET approved_action_approval=NULL WHERE id=?", (job_id,))
            self._event(db, job_id, "status", previous=row["status"], current=to,
                        error=error, result=result, log_path=log_path)
            db.execute("COMMIT")
        return self.get(job_id)

    def record_usage(self, job_id: str, input_tokens: int, output_tokens: int, *,
                     estimated: bool = False, cost_usd: float | None = None) -> dict:
        """Persist model token accounting before further GUI actions."""
        if any(type(n) is not int or n < 0 for n in (input_tokens, output_tokens)):
            raise ValueError("Token usage must be nonnegative integers")
        if not isinstance(estimated, bool):
            raise ValueError("estimated must be a boolean")
        if cost_usd is not None and (type(cost_usd) not in (int, float) or
                                     not math.isfinite(cost_usd) or cost_usd < 0):
            raise ValueError("Estimated cost must be a nonnegative number")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] != "running":
                raise ValueError("Usage requires a running job")
            stamp = utcnow()
            if cost_usd is None:
                db.execute("UPDATE jobs SET input_tokens=input_tokens+?,output_tokens=output_tokens+?,"
                           "estimated_tokens=estimated_tokens+?,updated_at=? WHERE id=?",
                           (input_tokens, output_tokens,
                            input_tokens + output_tokens if estimated else 0, stamp, job_id))
            else:
                db.execute("UPDATE jobs SET input_tokens=input_tokens+?,output_tokens=output_tokens+?,"
                           "estimated_tokens=estimated_tokens+?,estimated_cost_usd=COALESCE(estimated_cost_usd,0)+?,"
                           "cost_configured=1,updated_at=? WHERE id=?",
                           (input_tokens, output_tokens,
                            input_tokens + output_tokens if estimated else 0, float(cost_usd), stamp, job_id))
            self._event(db, job_id, "model_usage",
                        input_tokens=input_tokens, output_tokens=output_tokens,
                        estimated=estimated, estimated_cost_usd=cost_usd)
            db.execute("COMMIT")
        return self.get(job_id)

    def record_model_latency(self, job_id: str, latency_ms: int) -> None:
        if type(latency_ms) is not int or latency_ms < 0:
            raise ValueError("Model latency must be a nonnegative integer")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] != "running":
                raise ValueError("Latency requires a running job")
            db.execute("UPDATE jobs SET model_latency_ms=model_latency_ms+?,updated_at=? WHERE id=?",
                       (latency_ms, utcnow(), job_id))
            self._event(db, job_id, "model_latency", latency_ms=latency_ms)
            db.execute("COMMIT")

    def record_action_guard_review(self, job_id: str, action: str, impact: str,
                                   confidence: float) -> None:
        if (not isinstance(action, str) or len(action) > 40 or
                impact not in {"local", "external", "destructive", "uncertain"} or
                type(confidence) not in (int, float) or not 0 <= confidence <= 1):
            raise ValueError("Invalid action-guard result")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] != "running":
                raise ValueError("Action-guard results require a running job")
            self._event(db, job_id, "action_guard_review", action=action,
                        impact=impact, confidence=round(float(confidence), 3))
            db.execute("COMMIT")

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
                "UPDATE jobs SET status='waiting',wake_at=?,last_error=?,approved_action_approval=NULL,updated_at=? WHERE id=?",
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

    def wake(self, job_id: str, reason: str = "External event received") -> dict:
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise ValueError("Wake reason must contain 1-500 characters")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                db.execute("ROLLBACK")
                raise KeyError(job_id)
            if row["status"] != "waiting":
                db.execute("ROLLBACK")
                raise ValueError("Only a task waiting for an external event can be woken")
            db.execute("UPDATE jobs SET status='queued',wake_at=NULL,updated_at=? WHERE id=?",
                       (utcnow(), job_id))
            self._event(db, job_id, "woke", reason=reason.strip())
            db.execute("COMMIT")
        return self.get(job_id)

    def require_approval(self, job_id: str, reason: str,
                         proposal_token: str | None = None) -> dict:
        """Reject the proposed external operation; never persist executable click replay."""
        if not reason or not isinstance(reason, str):
            raise ValueError("Approval reason required")
        if not isinstance(proposal_token, str) or not 1 <= len(proposal_token) <= 500:
            raise ValueError("A one-time approval must be bound to a proposed action")
        try:
            proposal = json.loads(proposal_token)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid proposed-action approval token") from exc
        if (not isinstance(proposal, dict) or
                not re.fullmatch(r"[0-9a-f]{64}", str(proposal.get("intent", ""))) or
                not re.fullmatch(r"[0-9a-f]{64}", str(proposal.get("screen", "")))):
            raise ValueError("Invalid proposed-action approval token")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] != "running":
                raise ValueError("Approval gate requires running job")
            db.execute(
                "UPDATE jobs SET status='waiting_approval',pending_approval=?,"
                "pending_action_approval=?,approved_action_approval=NULL,updated_at=? WHERE id=?",
                (reason[:1000], proposal_token, utcnow(), job_id),
            )
            self._event(db, job_id, "approval_required", reason=reason[:1000], scope="one_action")
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
            stamp = utcnow()
            if row["status"] == "waiting_approval":
                if not row["pending_action_approval"]:
                    raise ValueError("No proposed action is available for one-time approval")
                db.execute("""UPDATE jobs SET approved_action_approval=pending_action_approval,
                    pending_action_approval=NULL,pending_approval=NULL,status='queued',updated_at=?
                    WHERE id=?""", (stamp, job_id))
                self._event(db, job_id, "external_action_approved", scope="one_action")
            elif row["status"] == "queued":
                db.execute("UPDATE jobs SET external_approved=1,updated_at=? WHERE id=?",
                           (stamp, job_id))
                self._event(db, job_id, "external_approved", scope="whole_job")
            else:
                raise ValueError("Approve an action while waiting for approval, or pre-approve a queued job")
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
            db.execute("""
                INSERT INTO checkpoints(job_id,key,state,detail,updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(job_id,key) DO UPDATE SET state=excluded.state,
                    detail=excluded.detail,updated_at=excluded.updated_at
                """, (job_id, key, state, detail, stamp))
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

    @staticmethod
    def _validate_plan(items: list[dict]) -> list[dict]:
        if not isinstance(items, list) or not 1 <= len(items) <= 40:
            raise ValueError("A plan must contain between 1 and 40 items")
        normalized = []
        keys = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Every plan item must be an object")
            key = item.get("key")
            title = item.get("title")
            acceptance = item.get("acceptance")
            parent = item.get("parent_key")
            dependencies = item.get("depends_on", [])
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", key):
                raise ValueError("Plan keys must contain 1-64 letters, digits, dots, dashes or underscores")
            if key in keys:
                raise ValueError("Plan keys must be unique")
            if not isinstance(title, str) or not title.strip() or len(title) > 180:
                raise ValueError("Plan titles must contain 1-180 characters")
            if not isinstance(acceptance, str) or not acceptance.strip() or len(acceptance) > 1000:
                raise ValueError("Every plan item needs an acceptance condition of at most 1000 characters")
            if parent is not None and (not isinstance(parent, str) or not parent):
                raise ValueError("parent_key must be a plan key or null")
            if not isinstance(dependencies, list) or len(dependencies) > 20 or not all(
                    isinstance(dep, str) for dep in dependencies):
                raise ValueError("depends_on must be a list of at most 20 plan keys")
            if len(set(dependencies)) != len(dependencies):
                raise ValueError("Plan dependencies cannot repeat")
            keys.add(key)
            normalized.append({
                "key": key, "title": title.strip(), "acceptance": acceptance.strip(),
                "parent_key": parent, "depends_on": dependencies,
            })
        by_key = {item["key"]: item for item in normalized}
        graph: dict[str, list[str]] = {}
        for item in normalized:
            parent = item["parent_key"]
            deps = item["depends_on"]
            if parent == item["key"] or any(ref == item["key"] for ref in deps):
                raise ValueError("A plan item cannot depend on itself")
            if parent is not None and parent not in by_key:
                raise ValueError(f"Unknown parent plan item: {parent}")
            if any(ref not in by_key for ref in deps):
                raise ValueError(f"Unknown dependency for plan item {item['key']}")
            graph[item["key"]] = deps + ([parent] if parent else [])
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(key: str) -> None:
            if key in visiting:
                raise ValueError("Plan dependencies and parent links must not contain cycles")
            if key in visited:
                return
            visiting.add(key)
            for dependency in graph[key]:
                visit(dependency)
            visiting.remove(key)
            visited.add(key)

        for key in graph:
            visit(key)
        return normalized

    def set_plan(self, job_id: str, items: list[dict]) -> dict:
        """Create or revise a persisted task plan while preserving completed work."""
        normalized = self._validate_plan(items)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT status,plan_version,action_count FROM jobs WHERE id=?",
                             (job_id,)).fetchone()
            if job is None:
                raise KeyError(job_id)
            if job["status"] != "running":
                raise ValueError("A plan may only be created or revised by the active worker")
            current = {row["key"]: dict(row) for row in db.execute(
                "SELECT * FROM plan_items WHERE job_id=?", (job_id,)).fetchall()}
            incoming = {item["key"] for item in normalized}
            protected = {key for key, row in current.items()
                         if row["state"] in {"done", "in_progress"}}
            if not protected.issubset(incoming):
                raise ValueError("A plan revision cannot remove completed or active items")
            for item in normalized:
                old = current.get(item["key"])
                if old and old["state"] == "done" and (
                        old["title"] != item["title"] or old["acceptance"] != item["acceptance"]):
                    raise ValueError("A completed item's title and acceptance condition cannot be changed")
            version = int(job["plan_version"]) + 1
            stamp = utcnow()
            db.execute("DELETE FROM plan_items WHERE job_id=?", (job_id,))
            for item in normalized:
                old = current.get(item["key"], {})
                state = old.get("state", "pending")
                evidence = old.get("evidence", "")
                attempts = old.get("attempts", 0)
                db.execute("""INSERT INTO plan_items
                    (job_id,key,parent_key,title,acceptance,dependencies,state,evidence,attempts,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""", (
                    job_id, item["key"], item["parent_key"], item["title"],
                    item["acceptance"], json.dumps(item["depends_on"]), state,
                    evidence, attempts, stamp))
            snapshot = [{**item, "state": current.get(item["key"], {}).get("state", "pending"),
                         "evidence": current.get(item["key"], {}).get("evidence", "")}
                        for item in normalized]
            db.execute("INSERT INTO plan_versions(job_id,version,items,created_at) VALUES(?,?,?,?)",
                       (job_id, version, json.dumps(snapshot, ensure_ascii=False), stamp))
            db.execute("UPDATE jobs SET plan_version=?,plan_reconciled_action=?,updated_at=? WHERE id=?",
                       (version, job["action_count"], stamp, job_id))
            self._event(db, job_id, "plan_revised" if version > 1 else "plan_created",
                        version=version, item_count=len(normalized))
            db.execute("COMMIT")
        return self.plan(job_id)

    def plan(self, job_id: str) -> dict:
        job = self.get(job_id)
        with self._connection() as db:
            rows = db.execute("SELECT * FROM plan_items WHERE job_id=? ORDER BY rowid",
                              (job_id,)).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["depends_on"] = json.loads(item.pop("dependencies"))
            items.append(item)
        finished = sum(item["state"] in {"done", "skipped"} for item in items)
        return {"job_id": job_id, "version": job["plan_version"], "items": items,
                "completed": finished, "total": len(items),
                "percent": round(finished * 100 / len(items)) if items else 0}

    def update_plan_item(self, job_id: str, key: str, state: str, evidence: str = "") -> dict:
        allowed = {"pending", "in_progress", "done", "blocked", "skipped"}
        if state not in allowed:
            raise ValueError("Invalid plan-item state")
        if not isinstance(evidence, str) or len(evidence) > 2000:
            raise ValueError("Plan evidence must be at most 2000 characters")
        if state in {"done", "skipped"} and len(evidence.strip()) < 8:
            raise ValueError("Completed and skipped plan items require a brief evidence note")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT status,action_count FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None:
                raise KeyError(job_id)
            if job["status"] != "running":
                raise ValueError("Plan updates require a running job")
            row = db.execute("SELECT * FROM plan_items WHERE job_id=? AND key=?",
                             (job_id, key)).fetchone()
            if row is None:
                raise KeyError(key)
            if state == "in_progress":
                for dependency in json.loads(row["dependencies"]):
                    dep = db.execute("SELECT state FROM plan_items WHERE job_id=? AND key=?",
                                     (job_id, dependency)).fetchone()
                    if not dep or dep["state"] not in {"done", "skipped"}:
                        raise ValueError(f"Plan prerequisite is incomplete: {dependency}")
            if state == "done":
                children = db.execute("SELECT key,state FROM plan_items WHERE job_id=? AND parent_key=?",
                                      (job_id, key)).fetchall()
                if any(child["state"] not in {"done", "skipped"} for child in children):
                    raise ValueError("A parent plan item cannot finish before its children")
            attempts = row["attempts"] + int(state == "in_progress" and row["state"] != "in_progress")
            stamp = utcnow()
            db.execute("""UPDATE plan_items SET state=?,evidence=?,attempts=?,updated_at=?
                WHERE job_id=? AND key=?""",
                (state, evidence.strip(), attempts, stamp, job_id, key))
            db.execute("UPDATE jobs SET plan_reconciled_action=?,updated_at=? WHERE id=?",
                       (job["action_count"], stamp, job_id))
            self._event(db, job_id, "plan_item", key=key, state=state,
                        evidence=evidence.strip()[:500], attempts=attempts)
            db.execute("COMMIT")
        return next(item for item in self.plan(job_id)["items"] if item["key"] == key)

    def assert_plan_ready(self, job_id: str, *, require_active: bool = True,
                          reconciliation_interval: int = 12,
                          memory_interval: int = 8) -> None:
        job = self.get(job_id)
        plan = self.plan(job_id)
        if not plan["items"]:
            raise ValueError("Create the mandatory task plan before performing GUI actions")
        if require_active and not any(item["state"] == "in_progress" for item in plan["items"]):
            raise ValueError("Mark one plan item in progress before performing GUI actions")
        if job["action_count"] - job["plan_reconciled_action"] >= reconciliation_interval:
            raise ValueError("Reconcile the plan and current work before the next GUI action")
        if job["action_count"] - job["memory_updated_action"] >= memory_interval:
            raise ValueError("Update working memory before the next GUI action")
        if any(item["state"] == "blocked" for item in plan["items"]):
            raise ValueError("Resolve or explicitly skip the blocked plan item before continuing")

    def assert_plan_complete(self, job_id: str) -> None:
        plan = self.plan(job_id)
        if not plan["items"]:
            raise ValueError("A task cannot finish without a persisted plan")
        incomplete = [item["key"] for item in plan["items"]
                      if item["state"] not in {"done", "skipped"}]
        if incomplete:
            raise ValueError("Task plan has unfinished items: " + ", ".join(incomplete[:10]))

    def working_memory(self, job_id: str) -> dict:
        job = self.get(job_id)
        try:
            memory = json.loads(job["working_memory"] or "{}")
        except (TypeError, json.JSONDecodeError):
            memory = {}
        return {"version": job.get("memory_version", 0), "updated_action": job["memory_updated_action"],
                "summary": memory.get("summary", ""),
                "findings": memory.get("findings", []),
                "decisions": memory.get("decisions", []),
                "open_questions": memory.get("open_questions", [])}

    def update_working_memory(self, job_id: str, *, summary: str,
                              findings: list[str] | None = None,
                              decisions: list[str] | None = None,
                              open_questions: list[str] | None = None) -> dict:
        def clean(name: str, values: list[str] | None) -> list[str]:
            if values is None:
                return []
            if not isinstance(values, list) or len(values) > 25 or not all(
                    isinstance(value, str) and value.strip() and len(value) <= 1000
                    for value in values):
                raise ValueError(f"{name} must contain at most 25 short text items")
            return [value.strip() for value in values]
        if not isinstance(summary, str) or len(summary) > 5000:
            raise ValueError("Working-memory summary must be at most 5000 characters")
        data = {"summary": summary.strip(), "findings": clean("findings", findings),
                "decisions": clean("decisions", decisions),
                "open_questions": clean("open_questions", open_questions)}
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT status,action_count,memory_version FROM jobs WHERE id=?",
                             (job_id,)).fetchone()
            if job is None:
                raise KeyError(job_id)
            if job["status"] != "running":
                raise ValueError("Working memory updates require a running job")
            version = job["memory_version"] + 1
            stamp = utcnow()
            db.execute("UPDATE jobs SET working_memory=?,memory_version=?,memory_updated_action=?,updated_at=? WHERE id=?",
                       (json.dumps(data, ensure_ascii=False), version, job["action_count"], stamp, job_id))
            self._event(db, job_id, "working_memory", version=version,
                        findings=len(data["findings"]), decisions=len(data["decisions"]),
                        open_questions=len(data["open_questions"]))
            db.execute("COMMIT")
        return self.working_memory(job_id)

    def begin_action(self, job_id: str, action: str, impact: str, purpose: str = "",
                     details: dict | None = None,
                     approval_token: str | None = None) -> int:
        if impact == "destructive":
            raise ValueError("Destructive GUI actions are not supported")
        if impact not in {"local", "external", "unknown"}:
            raise ValueError("Invalid action impact")
        safe_details = {key: value for key, value in (details or {}).items()
                        if key in {"x", "y", "end_x", "end_y", "button", "amount", "seconds", "keys", "character_count"}}
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT status,action_count,plan_reconciled_action,memory_updated_action,"
                             "external_approved,approved_action_approval FROM jobs WHERE id=?",
                             (job_id,)).fetchone()
            if job is None:
                raise KeyError(job_id)
            if job["status"] != "running":
                raise ValueError("Actions require a running job")
            consume_approval = False
            if impact == "external" and not job["external_approved"]:
                if (not approval_token or
                        approval_token != job["approved_action_approval"]):
                    raise ValueError("A matching one-time external-action approval is required")
                consume_approval = True
            if action not in {"screenshot", "wait"}:
                rows = db.execute("SELECT state FROM plan_items WHERE job_id=?", (job_id,)).fetchall()
                if not rows:
                    raise ValueError("Create the mandatory task plan before performing GUI actions")
                if not any(row["state"] == "in_progress" for row in rows):
                    raise ValueError("Mark one plan item in progress before performing GUI actions")
                if any(row["state"] == "blocked" for row in rows):
                    raise ValueError("Resolve or explicitly skip the blocked plan item before continuing")
                if job["action_count"] - job["plan_reconciled_action"] >= 12:
                    raise ValueError("Reconcile the plan and current work before the next GUI action")
                if job["action_count"] - job["memory_updated_action"] >= 8:
                    raise ValueError("Update working memory before the next GUI action")
            action_number = job["action_count"] + 1
            stamp = utcnow()
            cur = db.execute("""INSERT INTO action_journal
                (job_id,action_number,action,impact,purpose,details,state,started_at)
                VALUES(?,?,?,?,?,?,?,?)""", (
                job_id, action_number, action, impact, purpose[:300],
                json.dumps(safe_details, ensure_ascii=False), "started", stamp))
            db.execute("UPDATE jobs SET action_count=?,approved_action_approval=CASE WHEN ? "
                       "THEN NULL ELSE approved_action_approval END,updated_at=? WHERE id=?",
                       (action_number, int(consume_approval), stamp, job_id))
            self._event(db, job_id, "action_started", action_number=action_number,
                        action=action, impact=impact, purpose=purpose[:300], details=safe_details)
            db.execute("COMMIT")
        return int(cur.lastrowid)

    def finish_action(self, action_id: int, *, error_type: str | None = None) -> None:
        state = "failed" if error_type else "completed"
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT job_id,action_number,state FROM action_journal WHERE id=?",
                             (action_id,)).fetchone()
            if row is None:
                raise KeyError(action_id)
            if row["state"] != "started":
                raise ValueError("Action has already been finalized")
            stamp = utcnow()
            db.execute("UPDATE action_journal SET state=?,finished_at=?,error_type=? WHERE id=?",
                       (state, stamp, error_type, action_id))
            self._event(db, row["job_id"], "action_result", action_number=row["action_number"],
                        state=state, error_type=error_type)
            db.execute("COMMIT")

    def action_history(self, job_id: str, *, limit: int = 100) -> list[dict]:
        self.get(job_id)
        with self._connection() as db:
            rows = db.execute("SELECT * FROM action_journal WHERE job_id=? ORDER BY action_number DESC LIMIT ?",
                              (job_id, max(1, min(limit, 500)))).fetchall()
        history = []
        for row in reversed(rows):
            item = {**dict(row), "details": json.loads(row["details"])}
            try:
                end = datetime.fromisoformat(item["finished_at"]) if item["finished_at"] else datetime.now(timezone.utc)
                item["duration_ms"] = max(0, round((end - datetime.fromisoformat(
                    item["started_at"])).total_seconds() * 1000))
            except (TypeError, ValueError):
                item["duration_ms"] = None
            history.append(item)
        return history

    def add_message(self, job_id: str, role: str, content: str) -> dict:
        if role not in {"user", "assistant"} or not isinstance(content, str) or not content.strip():
            raise ValueError("Message role and content are required")
        content = content.strip()
        if len(content) > 8000:
            raise ValueError("Message must be at most 8000 characters")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None:
                raise KeyError(job_id)
            if job["status"] in FINAL or job["status"] == "verify":
                raise ValueError("Cannot add a message to a finished task")
            stamp = utcnow()
            cur = db.execute("INSERT INTO job_messages(job_id,role,content,created_at) VALUES(?,?,?,?)",
                             (job_id, role, content, stamp))
            if role == "user" and job["status"] in {"waiting_user", "waiting"}:
                db.execute("UPDATE jobs SET status='queued',pending_question=NULL,wake_at=NULL,"
                           "approved_action_approval=NULL,updated_at=? WHERE id=?",
                           (stamp, job_id))
                self._event(db, job_id, "status", previous=job["status"], current="queued")
            self._event(db, job_id, "message", role=role, seq=cur.lastrowid, length=len(content))
            db.execute("COMMIT")
        return {"seq": int(cur.lastrowid), "job_id": job_id, "role": role,
                "content": content, "created_at": stamp}

    def ask_user(self, job_id: str, question: str) -> dict:
        question = question.strip() if isinstance(question, str) else ""
        if not question or len(question) > 2000:
            raise ValueError("Question must contain 1-2000 characters")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] != "running":
                raise ValueError("Only a running job can ask the owner a question")
            stamp = utcnow()
            db.execute("INSERT INTO job_messages(job_id,role,content,created_at) VALUES(?,?,?,?)",
                       (job_id, "assistant", question, stamp))
            db.execute("UPDATE jobs SET status='waiting_user',pending_question=?,"
                       "approved_action_approval=NULL,updated_at=? WHERE id=?",
                       (question, stamp, job_id))
            self._event(db, job_id, "question", question=question)
            self._event(db, job_id, "status", previous="running", current="waiting_user")
            db.execute("COMMIT")
        return self.get(job_id)

    def messages(self, job_id: str, *, after: int = 0, limit: int = 100) -> list[dict]:
        self.get(job_id)
        with self._connection() as db:
            rows = db.execute("SELECT * FROM job_messages WHERE job_id=? AND seq>? ORDER BY seq LIMIT ?",
                              (job_id, max(0, after), max(1, min(limit, 500)))).fetchall()
        return [dict(row) for row in rows]

    def capture_evidence(self, job_id: str, relative_path: str, kind: str,
                         sha256: str, byte_count: int) -> dict:
        path = Path(relative_path)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("Evidence path must be relative to Qbrowser's data directory")
        if kind not in {"before_external_action", "after_external_action", "milestone", "final", "error"}:
            raise ValueError("Unsupported evidence type")
        if not re.fullmatch(r"[0-9a-f]{64}", sha256) or byte_count < 1:
            raise ValueError("Invalid evidence hash or size")
        ident = uuid.uuid4().hex
        stamp = utcnow()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone() is None:
                db.execute("ROLLBACK")
                raise KeyError(job_id)
            db.execute("INSERT INTO evidence(id,job_id,relative_path,kind,sha256,bytes,captured_at) VALUES(?,?,?,?,?,?,?)",
                       (ident, job_id, path.as_posix(), kind, sha256, byte_count, stamp))
            self._event(db, job_id, "evidence_saved", evidence_id=ident, evidence_kind=kind,
                        sha256=sha256, bytes=byte_count)
            db.execute("COMMIT")
        return {"id": ident, "job_id": job_id, "relative_path": path.as_posix(),
                "kind": kind, "sha256": sha256, "bytes": byte_count, "captured_at": stamp}

    def evidence(self, job_id: str) -> list[dict]:
        self.get(job_id)
        with self._connection() as db:
            rows = db.execute("SELECT id,job_id,kind,sha256,bytes,captured_at FROM evidence WHERE job_id=? ORDER BY captured_at",
                              (job_id,)).fetchall()
        return [dict(row) for row in rows]

    def evidence_record(self, job_id: str, evidence_id: str) -> dict:
        with self._connection() as db:
            row = db.execute("SELECT * FROM evidence WHERE job_id=? AND id=?",
                             (job_id, evidence_id)).fetchone()
        if row is None:
            raise KeyError(evidence_id)
        return dict(row)

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
        """Do not replay interrupted GUI actions automatically."""
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT id FROM jobs WHERE status='running'").fetchall()
            for row in rows:
                db.execute(
                    "UPDATE jobs SET status='needs_review', updated_at=?, last_error=?,"
                    "approved_action_approval=NULL WHERE id=?",
                    (utcnow(), "Worker stopped before outcome verification", row["id"]),
                )
                pending = db.execute("SELECT action_number FROM action_journal WHERE job_id=? AND state='started'",
                                     (row["id"],)).fetchall()
                for action in pending:
                    db.execute("UPDATE action_journal SET state='unknown',finished_at=? WHERE job_id=? AND action_number=?",
                               (utcnow(), row["id"], action["action_number"]))
                    self._event(db, row["id"], "action_outcome_unknown",
                                action_number=action["action_number"])
                self._event(db, row["id"], "interrupted",
                            reason="Desktop state unknown; operator review required")
            db.execute("COMMIT")
        return len(rows)
