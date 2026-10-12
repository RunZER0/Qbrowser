"""Optional, owner-confirmed WhatsApp delivery through an installed OpenClaw channel.

Delivery is invoked manually after verified completion. No credentials, chat session
or WhatsApp automation are implemented here. An uncertain delivery is never retried
automatically to avoid duplicate sends.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from qbrowser.artifacts import artifacts, workspace
from qbrowser.store import JobStore, utcnow

PHONE = re.compile(r"^\+[1-9][0-9]{7,14}$")


def _initialize(store: JobStore) -> None:
    with store._connection() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS deliveries (
            job_id TEXT NOT NULL REFERENCES jobs(id),
            filename TEXT NOT NULL,
            status TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(job_id,filename)
        )""")


def deliver_verified(store: JobStore, job_id: str, *, confirm: bool = False) -> dict:
    if not confirm:
        raise ValueError("Delivery requires explicit --confirm")
    recipient = os.environ.get("QBROWSER_OWNER_WHATSAPP", "")
    if not PHONE.fullmatch(recipient):
        raise ValueError("Set QBROWSER_OWNER_WHATSAPP to your own E.164 WhatsApp number")
    executable = shutil.which("openclaw")
    if not executable:
        raise RuntimeError("OpenClaw command unavailable; install and pair WhatsApp first")
    job = store.get(job_id)
    if job["status"] != "completed":
        raise ValueError("Only operator-verified completed jobs may be delivered")
    files = artifacts(store, job_id)
    if not files:
        raise ValueError("No indexed deliverables found; check job workspace")
    _initialize(store)
    successes = []
    for item in files:
        name = item["name"]
        path = (workspace(job_id) / name).resolve()
        if not path.is_relative_to(workspace(job_id).resolve()) or not path.is_file():
            raise ValueError("Output file missing or unsafe")
        # Restrict to the exact indexed file; do not send changed artifacts.
        import hashlib
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != item["sha256"]:
            raise ValueError("File changed after indexing; re-index before sending")
        with store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            found = db.execute(
                "SELECT status FROM deliveries WHERE job_id=? AND filename=?",
                (job_id, name),
            ).fetchone()
            if found:
                raise ValueError(f"Delivery already attempted for {name}; inspect WhatsApp manually")
            db.execute(
                "INSERT INTO deliveries(job_id,filename,status,updated_at) VALUES(?,?,?,?)",
                (job_id, name, "uncertain", utcnow()),
            )
            db.execute("COMMIT")
        # Reserve delivery BEFORE launching the external side effect.
        command = [executable, "message", "send", "--channel", "whatsapp",
                   "--target", recipient, "--media", str(path),
                   "--message", f"Qbrowser verified job {job_id}: {name}"]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=120,
                                    check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(
                f"OpenClaw delivery outcome uncertain for {name}; inspect WhatsApp."
            ) from exc
        if result.returncode:
            raise RuntimeError(
                f"OpenClaw delivery outcome uncertain for {name}; inspect WhatsApp manually."
            )
        with store._connection() as db:
            db.execute("UPDATE deliveries SET status='sent', updated_at=? "
                       "WHERE job_id=? AND filename=?",
                       (utcnow(), job_id, name))
        successes.append(name)
    return {"job_id": job_id, "sent": successes, "target": "owner"}
