#!/usr/bin/env python3
"""Install Qbrowser into an isolated environment on the current machine.

Does not launch GUI control or configure model secrets.
"""
from __future__ import annotations

import argparse
import os
import shutil
import secrets
import re
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    p = argparse.ArgumentParser(description="Set up Qbrowser local dependencies")
    p.add_argument("--minimal", action="store_true",
                   help="Install the workboard only (skip the Agent-S GUI dependency)")
    p.add_argument("--agent-s", action="store_true",
                   help="Install legacy Agent-S backend with its grounding-model requirement")
    args = p.parse_args()
    directory = ROOT / ".venv"
    if not directory.exists():
        venv.EnvBuilder(with_pip=True).create(directory)
    python = directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run([str(python), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    extra = "server" if args.minimal else ("server,native,desktop" if args.agent_s else "server,native")
    subprocess.run([str(python), "-m", "pip", "install", "-e", f".[{extra}]"],
                   check=True, cwd=ROOT)
    env = ROOT / ".env"
    if not env.exists():
        template = (ROOT / ".env.example").read_text(encoding="utf-8")
        template = template.replace("QBROWSER_API_TOKEN=\n",
                                    "QBROWSER_API_TOKEN=" + secrets.token_urlsafe(32) + "\n")
        env.write_text(template, encoding="utf-8")
        if os.name != "nt":
            os.chmod(env, 0o600)
    else:
        existing = env.read_text(encoding="utf-8")
        replaced, count = re.subn(r"(?m)^QBROWSER_API_TOKEN=[ \\t]*$",
                                  lambda _: "QBROWSER_API_TOKEN=" + secrets.token_urlsafe(32),
                                  existing)
        if count:
            env.write_text(replaced, encoding="utf-8")
            if os.name != "nt":
                os.chmod(env, 0o600)
    cli = directory / ("Scripts/qbrowser.exe" if os.name == "nt" else "bin/qbrowser")
    subprocess.run([str(cli), "init"], check=True, cwd=ROOT)
    subprocess.run([str(cli), "doctor"], check=True, cwd=ROOT)
    print("\nEdit .env for your model, grounding configuration and local API token.")
    print("The workboard API token is stored under QBROWSER_API_TOKEN in .env.")
    print(f"Dashboard: {cli} serve")
    print(f"Desktop worker: {cli} worker")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"Installation command failed with exit code {exc.returncode}", file=sys.stderr)
        raise SystemExit(exc.returncode)
