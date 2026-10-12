"""Environment-first secret access with optional operating-system keyring storage."""

from __future__ import annotations

import os

SECRET_NAMES = frozenset({
    "QBROWSER_API_TOKEN", "ZAI_API_KEY", "DEEPSEEK_API_KEY",
    "QBROWSER_MODEL_API_KEY", "QBROWSER_VERIFIER_API_KEY",
    "QBROWSER_TRANSCRIPTION_API_KEY",
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
    "AGENT_S_GROUND_API_KEY", "QBROWSER_EVIDENCE_KEY",
})


def _enabled() -> bool:
    return os.getenv("QBROWSER_USE_KEYRING", "0").strip().lower() in {"1", "true", "yes", "on"}


def get_secret(name: str) -> str:
    if name not in SECRET_NAMES:
        raise ValueError("Unsupported credential name")
    value = os.getenv(name, "")
    if value or not _enabled():
        return value
    try:
        import keyring
        return keyring.get_password("qbrowser", name) or ""
    except Exception:
        return ""


def set_secret(name: str, value: str) -> None:
    if name not in SECRET_NAMES:
        raise ValueError("Unsupported credential name")
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError("Credential must contain 1-4096 characters")
    try:
        import keyring
    except ImportError as exc:
        raise RuntimeError("Install Qbrowser's keyring extra first: pip install -e '.[keyring]'") from exc
    keyring.set_password("qbrowser", name, value)


def delete_secret(name: str) -> bool:
    if name not in SECRET_NAMES:
        raise ValueError("Unsupported credential name")
    try:
        import keyring
        from keyring.errors import PasswordDeleteError
        keyring.delete_password("qbrowser", name)
        return True
    except ImportError as exc:
        raise RuntimeError("Install Qbrowser's keyring extra first: pip install -e '.[keyring]'") from exc
    except PasswordDeleteError:
        return False
    except Exception as exc:
        raise RuntimeError("Could not delete credential from the operating-system keyring") from exc
