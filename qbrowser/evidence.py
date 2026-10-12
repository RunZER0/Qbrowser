"""Opt-in encrypted screenshot evidence stored outside user deliverable folders."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from pathlib import Path

from qbrowser.store import JobStore, home
from qbrowser.credentials import get_secret

MAX_EVIDENCE_BYTES = 20 * 1024 * 1024


def evidence_key() -> bytes:
    encoded = get_secret("QBROWSER_EVIDENCE_KEY").strip()
    if not encoded:
        raise RuntimeError("Set QBROWSER_EVIDENCE_KEY to a base64-encoded 32-byte key")
    try:
        key = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except Exception as exc:
        raise RuntimeError("QBROWSER_EVIDENCE_KEY is not valid base64") from exc
    if len(key) != 32:
        raise RuntimeError("QBROWSER_EVIDENCE_KEY must decode to exactly 32 bytes")
    return key


def new_evidence_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def enabled() -> bool:
    return os.getenv("QBROWSER_CAPTURE_EVIDENCE", "0").strip().lower() in {"1", "true", "yes", "on"}


def save_screenshot(store: JobStore, job_id: str, image_uri: str, kind: str) -> dict | None:
    """Encrypt and save an explicit milestone image; ordinary observation stays in memory."""
    if not enabled():
        return None
    prefix = "data:image/jpeg;base64,"
    if not isinstance(image_uri, str) or not image_uri.startswith(prefix):
        raise ValueError("Evidence must be a JPEG screenshot")
    try:
        raw = base64.b64decode(image_uri[len(prefix):], validate=True)
    except Exception as exc:
        raise ValueError("Screenshot evidence is malformed") from exc
    if not raw or len(raw) > MAX_EVIDENCE_BYTES:
        raise ValueError("Screenshot evidence is empty or exceeds the 20 MB limit")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise RuntimeError("Install the evidence extra to store encrypted screenshots") from exc
    key = evidence_key()
    nonce = secrets.token_bytes(12)
    ciphertext = nonce + AESGCM(key).encrypt(nonce, raw, f"{job_id}:{kind}".encode())
    ident = secrets.token_hex(16)
    relative = Path("evidence") / job_id / f"{ident}.jpg.enc"
    target = home() / relative
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        target.parent.chmod(0o700)
    except OSError:
        pass
    target.write_bytes(ciphertext)
    try:
        target.chmod(0o600)
    except OSError:
        pass
    digest = hashlib.sha256(ciphertext).hexdigest()
    try:
        record = store.capture_evidence(job_id, relative.as_posix(), kind, digest, len(ciphertext))
    except Exception:
        target.unlink(missing_ok=True)
        raise
    prune_expired(store)
    return record


def read_screenshot(store: JobStore, job_id: str, evidence_id: str) -> bytes:
    record = store.evidence_record(job_id, evidence_id)
    relative = Path(record["relative_path"])
    root = (home() / "evidence").resolve()
    candidate = home() / relative
    if candidate.is_symlink():
        raise ValueError("Evidence path is outside the evidence directory")
    path = candidate.resolve()
    if not path.is_relative_to(root):
        raise ValueError("Evidence path is outside the evidence directory")
    payload = path.read_bytes()
    if len(payload) != record["bytes"] or hashlib.sha256(payload).hexdigest() != record["sha256"]:
        raise ValueError("Evidence integrity check failed")
    if len(payload) < 29 or len(payload) > MAX_EVIDENCE_BYTES + 64:
        raise ValueError("Evidence file has an invalid size")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(evidence_key()).decrypt(
            payload[:12], payload[12:], f"{job_id}:{record['kind']}".encode())
    except ImportError as exc:
        raise RuntimeError("Install the evidence extra to read encrypted screenshots") from exc
    except Exception as exc:
        raise ValueError("Evidence decryption failed; check the configured evidence key") from exc


def prune_expired(store: JobStore, *, retention_days: int | None = None) -> int:
    """Delete expired encrypted images and their metadata, preserving a journal event."""
    days = retention_days
    if days is None:
        try:
            days = int(os.getenv("QBROWSER_EVIDENCE_RETENTION_DAYS", "30"))
        except ValueError as exc:
            raise ValueError("QBROWSER_EVIDENCE_RETENTION_DAYS must be an integer") from exc
    if not 1 <= days <= 3650:
        raise ValueError("Evidence retention must be between 1 and 3650 days")
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with store._connection() as db:
        rows = db.execute("SELECT id,job_id,relative_path FROM evidence WHERE captured_at<?", (cutoff,)).fetchall()
        if not rows:
            return 0
        db.execute("BEGIN IMMEDIATE")
        root = (home() / "evidence").resolve()
        removed = 0
        for row in rows:
            candidate = home() / row["relative_path"]
            target = candidate.resolve()
            if target.is_relative_to(root) and not candidate.is_symlink():
                target.unlink(missing_ok=True)
            db.execute("DELETE FROM evidence WHERE id=?", (row["id"],))
            store._event(db, row["job_id"], "evidence_expired", evidence_id=row["id"])
            removed += 1
        db.execute("COMMIT")
    return removed
