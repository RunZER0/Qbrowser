"""Minimal local .env support without adding runtime dependencies."""

import os
from pathlib import Path


def load_local_env() -> None:
    override = os.getenv("QBROWSER_ENV_FILE")
    if override:
        file = Path(override).expanduser()
    else:
        file = Path.cwd() / ".env"
        if not file.is_file():
            file = Path(__file__).resolve().parent.parent / ".env"
    if not file.is_file():
        return
    for line in file.read_text(encoding="utf-8").splitlines():
        item = line.strip()
        if not item or item.startswith("#") or "=" not in item:
            continue
        key, _, val = item.partition("=")
        key = key.strip()
        if key.isidentifier():
            os.environ.setdefault(key, val.strip().strip('"').strip("'"))
