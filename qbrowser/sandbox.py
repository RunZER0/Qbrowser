"""Opt-in Linux bubblewrap wrapper for the legacy Agent-S subprocess."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from qbrowser.store import home


def sandbox_command(command: list[str], workspace: Path | str, *,
                    mode: str | None = None, platform: str | None = None,
                    bwrap_path: str | None = None) -> list[str]:
    """Run a child with a read-only host tree and one writable task workspace.

    Network is intentionally shared so the configured model endpoint and requested
    websites remain reachable. Linux host-policy and desktop integration still
    need local acceptance; unsupported platforms fail closed when requested.
    """
    if not isinstance(command, list) or not command or any(not isinstance(item, str) for item in command):
        raise ValueError("Sandboxed execution requires an argv list")
    selected = (mode if mode is not None else os.getenv("QBROWSER_OS_SANDBOX", "off")).strip().lower()
    if selected in {"", "0", "false", "off", "no"}:
        return list(command)
    if selected != "bwrap":
        raise ValueError("QBROWSER_OS_SANDBOX must be off or bwrap")
    if (platform or sys.platform) != "linux":
        raise RuntimeError("The bubblewrap sandbox is available only on Linux")
    executable = bwrap_path or shutil.which("bwrap")
    if not executable:
        raise RuntimeError("bubblewrap is unavailable; install bwrap or disable QBROWSER_OS_SANDBOX")
    task_dir = Path(workspace)
    if task_dir.is_symlink() or not task_dir.is_dir():
        raise ValueError("Sandbox workspace must be an existing regular directory")
    task_dir = task_dir.resolve(strict=True)
    allowed_root = (home() / "workspaces").resolve()
    try:
        task_dir.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError("Sandbox workspace must be within QBROWSER_HOME/workspaces") from exc

    argv = [executable, "--die-with-parent", "--new-session", "--unshare-all", "--share-net",
            "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]
    # Preserve only the X11 socket directory across the private temporary filesystem.
    x11 = Path("/tmp/.X11-unix")
    if x11.is_dir():
        staged = Path(f"/run/qbrowser-x11-{os.getpid()}")
        argv.extend(["--dir", str(staged), "--bind", str(x11), str(staged)])
    argv.extend(["--tmpfs", "/tmp"])
    if x11.is_dir():
        argv.extend(["--dir", str(x11), "--bind", str(staged), str(x11)])
    argv.extend(["--bind", str(task_dir), str(task_dir), "--chdir", str(task_dir), "--", *command])
    return argv
