"""Local command-line task control."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from qbrowser.config import load_local_env
from qbrowser.store import JobStore, home


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="qbrowser", description="Durable local GUI agent manager")
    s = p.add_subparsers(dest="command", required=True)
    s.add_parser("init", help="Initialize the local job database")
    s.add_parser("doctor", help="Show local runtime prerequisites")
    add = s.add_parser("add", help="Queue a natural-language desktop job")
    add.add_argument("instruction", help="Task text, or '-' to read stdin")
    add.add_argument("--priority", type=int, default=0)
    add.add_argument("--engine", choices=["native", "agent_s"])
    add.add_argument("--provider", choices=["zai", "deepseek", "custom"])
    add.add_argument("--model", help="Override the provider's default model")
    add.add_argument("--approve-external", action="store_true",
                     help="Authorize external submissions requested in this job")
    s.add_parser("list", help="List jobs")
    for name in ("show", "events", "artifacts", "requeue", "verified", "cancel", "stop", "pause",
                 "approve-external"):
        cmd = s.add_parser(name)
        cmd.add_argument("id", help="Job ID")
    ck = s.add_parser("checkpoint", help="Record a durable dynamic task milestone")
    ck.add_argument("id")
    ck.add_argument("key")
    ck.add_argument("state", choices=["pending", "in_progress", "done", "blocked", "skipped"])
    ck.add_argument("detail", nargs="?", default="")
    cks = s.add_parser("checkpoints", help="Show milestone state for a job")
    cks.add_argument("id")
    w = s.add_parser("worker", help="Start the exclusive local desktop operator")
    w.add_argument("--once", action="store_true", help="Execute at most one queued job")
    web = s.add_parser("serve", help="Start local dashboard and HTTP API")
    web.add_argument("--host", default=None)
    web.add_argument("--port", type=int, default=None)
    return p


def doctor() -> int:
    engine = os.getenv("QBROWSER_ENGINE", "native")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Database folder: {home()}")
    print(f"Selected engine: {engine}")
    if engine == "native":
        provider = os.getenv("QBROWSER_PROVIDER", "zai")
        model = os.getenv("QBROWSER_MODEL") or {
            "zai": "glm-5.3-flash", "deepseek": "deepseek-flash",
        }.get(provider, "(not configured)")
        keyname = {"zai": "ZAI_API_KEY", "deepseek": "DEEPSEEK_API_KEY",
                   "custom": "QBROWSER_MODEL_API_KEY"}.get(provider)
        print(f"Model: {provider} / {model}")
        print(f"{keyname or 'Provider key'}: {'SET' if keyname and os.getenv(keyname) else 'NOT SET'}")
        try:
            import pyautogui
            print(f"PyAutoGUI: installed ({getattr(pyautogui, '__version__', 'unknown')})")
        except Exception as exc:
            print(f"PyAutoGUI: unavailable ({type(exc).__name__})")
        try:
            import openai
            print(f"OpenAI-compatible SDK: installed ({openai.__version__})")
        except ImportError:
            print("OpenAI-compatible SDK: NOT INSTALLED")
    else:
        print(f"Agent-S: {os.getenv('AGENT_S_PATH') or shutil.which('agent_s') or 'NOT INSTALLED'}")
        print(f"Grounding URL: {os.getenv('AGENT_S_GROUND_URL') or 'NOT CONFIGURED'}")
    print("Desktop control starts ONLY when qbrowser worker is launched.")
    return 0

def main(argv: list[str] | None = None) -> int:
    load_local_env()
    args = parser().parse_args(argv)
    if args.command == "doctor":
        return doctor()
    if args.command == "serve":
        try:
            import uvicorn
        except ImportError:
            print("Install dashboard dependencies: pip install -e '.[server]'", file=sys.stderr)
            return 2
        host = args.host or os.getenv("QBROWSER_HOST", "127.0.0.1")
        if host not in ("127.0.0.1", "localhost", "::1") and not os.getenv("QBROWSER_API_TOKEN"):
            print("Refusing non-loopback bind without QBROWSER_API_TOKEN", file=sys.stderr)
            return 2
        uvicorn.run("qbrowser.web:app", host=host,
                    port=args.port or int(os.getenv("QBROWSER_PORT", "8765")))
        return 0
    store = JobStore()
    if args.command == "init":
        print(f"Initialized {store.path}")
        return 0
    if args.command == "worker":
        from qbrowser.runner import worker
        try:
            worker(store, once=args.once)
        except (RuntimeError, KeyboardInterrupt) as exc:
            print(str(exc), file=sys.stderr)
            return 2 if isinstance(exc, RuntimeError) else 130
        return 0
    if args.command == "add":
        instruction = sys.stdin.read() if args.instruction == "-" else args.instruction
        job = store.create(instruction, priority=args.priority,
                           external_approved=args.approve_external,
                           engine=args.engine, provider=args.provider, model=args.model)
    elif args.command == "list":
        print(json.dumps(store.list(), indent=2, ensure_ascii=False))
        return 0
    elif args.command == "show":
        job = store.get(args.id)
    elif args.command == "events":
        print(json.dumps(store.events(args.id), indent=2, ensure_ascii=False))
        return 0
    elif args.command == "artifacts":
        from qbrowser.artifacts import artifacts
        print(json.dumps(artifacts(store, args.id), indent=2, ensure_ascii=False))
        return 0
    elif args.command == "checkpoints":
        print(json.dumps(store.checkpoints(args.id), indent=2, ensure_ascii=False))
        return 0
    elif args.command == "checkpoint":
        print(json.dumps(store.checkpoint(args.id, args.key, args.state, args.detail),
                         indent=2, ensure_ascii=False))
        return 0
    elif args.command == "requeue":
        job = store.transition(args.id, "queued",
              allowed={"needs_review", "paused", "waiting_approval", "failed"})
    elif args.command == "verified":
        job = store.transition(args.id, "completed", allowed={"verify"},
                               result="Operator verified the final outcome")
    elif args.command == "stop":
        job = store.request_stop(args.id)
    elif args.command == "cancel":
        job = store.transition(args.id, "cancelled",
              allowed={"queued", "paused", "needs_review", "waiting_approval", "verify"})
    elif args.command == "pause":
        job = store.transition(args.id, "paused", allowed={"queued"})
    elif args.command == "approve-external":
        job = store.approve_external(args.id)
    else:
        raise AssertionError(args.command)
    print(json.dumps(job, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
