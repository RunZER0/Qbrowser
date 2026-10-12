"""Durable GUI effect boundaries and semantic supervisor state.

Never replay an arbitrary click on restart: a crash between input execution and
journal commit makes its outcome uncertain. Keep only action metadata/hashes,
never pasted content, raw screenshots or passwords.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from qbrowser.store import JobStore, utcnow

# Genuine observation or no external side effects, even if the worker dies in-flight.
RESTART_SAFE_ACTIONS = frozenset({"screenshot", "wait", "move"})
MAX_STATE_BYTES = 24_000
MAX_FACTS = 50


def fingerprint_action(arguments: dict) -> str:
    """Hash the *complete* action payload without persisting sensitive values."""
    value = json.dumps(arguments, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, default=str)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_metadata(arguments: dict) -> tuple[str, str, str]:
    action = str(arguments.get("action", ""))
    if action not in {
        "screenshot", "click", "double_click", "move",
        "scroll", "type", "hotkey", "keypress", "wait",
    }:
        raise ValueError("Unrecognized GUI action")
    impact = str(arguments.get("impact") or "unknown")
    if impact not in {"local", "external", "destructive", "unknown"}:
        raise ValueError("Invalid action impact")
    purpose = str(arguments.get("purpose") or "").strip()
    # Purposes can contain secrets; store a brief generic description instead.
    return action, impact, ("private-paste" if action == "type" else purpose[:160])


class ActionJournal:
    def __init__(self, store: JobStore):
        self.store = store

    def prepare(self, job_id: str, *, attempt: int, ordinal: int,
                arguments: dict) -> dict:
        if not isinstance(arguments, dict) or ordinal < 1 or attempt < 1:
            raise ValueError("Invalid action metadata")
        action, impact, purpose = safe_metadata(arguments)
        digest = fingerprint_action(arguments)
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT status,attempt FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None:
                raise KeyError(job_id)
            if job["status"] != "running" or job["attempt"] != attempt:
                raise ValueError("Only the current running attempt can plan a GUI action")
            existing = db.execute(
                """SELECT * FROM gui_actions
                   WHERE job_id=? AND attempt=? AND ordinal=?""",
                (job_id, attempt, ordinal),
            ).fetchone()
            if existing is not None:
                if existing["fingerprint"] != digest:
                    raise ValueError("Action ordinal already belongs to a different payload")
                db.execute("COMMIT")
                return dict(existing)
            action_id = uuid.uuid4().hex
            timestamp = utcnow()
            db.execute(
                """INSERT INTO gui_actions
                   (id,job_id,attempt,ordinal,action,impact,purpose,fingerprint,
                    phase,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (action_id, job_id, attempt, ordinal, action, impact, purpose,
                 digest, "prepared", timestamp),
            )
            self.store._event(db, job_id, "gui_action_prepared",
                              action_id=action_id, attempt=attempt,
                              ordinal=ordinal, action=action, impact=impact)
            db.execute("COMMIT")
        return self.get(action_id)

    def get(self, action_id: str) -> dict:
        with self.store._connection() as db:
            row = db.execute("SELECT * FROM gui_actions WHERE id=?", (action_id,)).fetchone()
        if row is None:
            raise KeyError(action_id)
        return dict(row)

    def dispatch(self, action_id: str) -> dict:
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM gui_actions WHERE id=?", (action_id,)).fetchone()
            if row is None:
                raise KeyError(action_id)
            if row["phase"] != "prepared":
                raise ValueError("A GUI action cannot be dispatched twice")
            db.execute("UPDATE gui_actions SET phase='dispatched',dispatched_at=? WHERE id=?",
                       (utcnow(), action_id))
            self.store._event(db, row["job_id"], "gui_action_dispatched",
                              action_id=action_id, action=row["action"],
                              ordinal=row["ordinal"], attempt=row["attempt"])
            db.execute("COMMIT")
        return self.get(action_id)

    def resolve(self, action_id: str, *, confirmed: bool, result: str = "") -> dict:
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM gui_actions WHERE id=?", (action_id,)).fetchone()
            if row is None:
                raise KeyError(action_id)
            if row["phase"] != "dispatched":
                raise ValueError("Action result can only follow dispatch")
            phase = "confirmed" if confirmed else "uncertain"
            # Never write pasted strings or raw exceptions here.
            message = str(result)[:200] if row["action"] != "type" else "Paste outcome recorded"
            db.execute(
                "UPDATE gui_actions SET phase=?, resolved_at=?,result=? WHERE id=?",
                (phase, utcnow(), message, action_id),
            )
            self.store._event(db, row["job_id"], "gui_action_resolved",
                              action_id=action_id, phase=phase)
            db.execute("COMMIT")
        return self.get(action_id)

    def list(self, job_id: str, *, attempt: int | None = None) -> list[dict]:
        self.store.get(job_id)
        with self.store._connection() as db:
            if attempt is None:
                rows = db.execute(
                    "SELECT * FROM gui_actions WHERE job_id=? ORDER BY attempt,ordinal",
                    (job_id,),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM gui_actions WHERE job_id=? AND attempt=? ORDER BY ordinal",
                    (job_id, attempt),
                ).fetchall()
        return [dict(row) for row in rows]

    def recovery_decision(self, job_id: str, attempt: int) -> tuple[bool, str]:
        """Safe fresh-observation continuation; never reexecute a recorded action."""
        records = self.list(job_id, attempt=attempt)
        # Empty journal means we may have crashed during a model call, before GUI input.
        if not records:
            return True, "No GUI action was dispatched during this attempt"
        for record in records:
            if record["action"] not in RESTART_SAFE_ACTIONS:
                return False, "Potentially consequential GUI action requires inspection"
            if record["impact"] in {"external", "destructive"}:
                return False, "High-impact GUI action requires review"
        return True, "Only observation/movement/wait actions were recorded; reobserve screen"


def _validate_working_state(state: dict) -> dict:
    """Persist safe structured *summary*, not the complete model transcript."""
    if not isinstance(state, dict):
        raise ValueError("Supervisor state must be an object")
    permitted = {"current_goal", "phase", "summary", "verified_facts",
                 "next_steps", "blockers", "last_observation"}
    if set(state) - permitted:
        raise ValueError("Unsupported supervisor fields")
    output = {}
    for key, value in state.items():
        if key in {"verified_facts", "next_steps", "blockers"}:
            if not isinstance(value, list) or len(value) > MAX_FACTS:
                raise ValueError(f"{key} must be a list of up to {MAX_FACTS} strings")
            if not all(isinstance(v, str) and len(v) <= 400 for v in value):
                raise ValueError(f"{key} has invalid entries")
        elif not isinstance(value, str) or len(value) > 6000:
            raise ValueError(f"{key} must be a string of at most 6000 characters")
        output[key] = value
    data = json.dumps(output, ensure_ascii=False, separators=(",", ":"))
    if len(data.encode("utf-8")) > MAX_STATE_BYTES:
        raise ValueError("Supervisor state is too large")
    return output


def save_working_state(store: JobStore, job_id: str, state: dict) -> dict:
    clean = _validate_working_state(state)
    with store._connection() as db:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute("SELECT status,attempt FROM jobs WHERE id=?", (job_id,)).fetchone()
        if job is None:
            raise KeyError(job_id)
        if job["status"] not in {"running", "paused", "needs_review", "waiting"}:
            raise ValueError("Cannot update supervisor state in current job status")
        row = db.execute(
            "SELECT COALESCE(MAX(revision),0) AS v FROM supervisor_snapshots WHERE job_id=?",
            (job_id,),
        ).fetchone()
        revision = row["v"] + 1
        stamp = utcnow()
        db.execute(
            """INSERT INTO supervisor_snapshots(job_id,revision,attempt,state_json,created_at)
               VALUES(?,?,?,?,?)""",
            (job_id, revision, job["attempt"],
             json.dumps(clean, ensure_ascii=False), stamp),
        )
        store._event(db, job_id, "supervisor_snapshot",
                     revision=revision, attempt=job["attempt"],
                     field_names=list(clean))
        db.execute("COMMIT")
    return {"job_id": job_id, "revision": revision, "attempt": job["attempt"],
            "state": clean, "created_at": stamp}


def load_working_state(store: JobStore, job_id: str) -> dict | None:
    store.get(job_id)
    with store._connection() as db:
        row = db.execute(
            """SELECT revision,attempt,state_json,created_at FROM supervisor_snapshots
               WHERE job_id=? ORDER BY revision DESC LIMIT 1""",
            (job_id,),
        ).fetchone()
    if row is None:
        return None
    return {"job_id": job_id, "revision": row["revision"],
            "attempt": row["attempt"], "state": json.loads(row["state_json"]),
            "created_at": row["created_at"]}


def subtask_timeline(store: JobStore, job_id: str) -> list[dict]:
    store.get(job_id)
    with store._connection() as db:
        rows = db.execute(
            "SELECT * FROM subtask_history WHERE job_id=? ORDER BY seq",
            (job_id,),
        ).fetchall()
    return [dict(row) for row in rows]
