# Qbrowser

A local-first agent workbench for arbitrary computer tasks. Qbrowser accepts natural-language jobs, stores durable execution state in SQLite, runs the existing Agent-S GUI operator through a controlled desktop worker, and exposes a local dashboard and HTTP API.

**Current scope:** one desktop worker; arbitrary task instructions; persistent jobs, event logs, progress, operator approval, crash recovery and local installation. No predefined website-specific workflows. WhatsApp intake and distributed workers are later integrations.

The desktop operator uses normal GUI interactions (screenshots, clicks, keyboard and scrolling), with model inference and visual grounding supplied by Agent-S. It needs a local desktop session and separately configured model credentials and grounding endpoint.

## Quick start

The cross-platform setup script prepares a local virtual environment and installs the existing GUI agent:

```bash
python bootstrap.py
```

The script creates an editable `.env` file and does not start desktop control. Run `qbrowser doctor` after configuring the model and grounding endpoint.

Alternatively, install components manually:

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[server]"
qbrowser init
qbrowser doctor
qbrowser serve
```

In another terminal, after installing the optional GUI engine (`pip install -e ".[desktop]"`) and setting configuration:

```bash
qbrowser worker
```

Open **http://127.0.0.1:8765**. Jobs are created and managed through the local dashboard or CLI: `qbrowser add "Open a browser and ..."`; `qbrowser list`.

See `docs/INSTALL.md` for model, grounding, desktop permissions and safety configuration.

## Technology adopted

- [Agent-S](https://github.com/simular-ai/Agent-S), Apache-2.0: full-screen GUI agent. Installed locally as the existing `gui-agents` package, not copied or misrepresented as a GitHub fork.
- [OpenClaw](https://github.com/openclaw/openclaw): optional intake and orchestration integration. Qbrowser provides a documented local CLI and HTTP bridge, so OpenClaw can hand tasks to the local worker without rewriting the GUI agent.
- Python 3.11+, SQLite and optional FastAPI for the dashboard.

## Safety and progress behavior

Qbrowser never starts a desktop-control task until `qbrowser worker` is deliberately started. Only one job can hold the desktop lease. A failed or abruptly interrupted GUI task moves to **needs_review** because replaying clicks may duplicate external actions. Operators can explicitly requeue it after checking the target apps. The agent is instructed to pause before publishing, payments, destructive operations or sharing private data unless approved for that job.

Data is stored in `~/.qbrowser/qbrowser.db` by default. The local API binds to loopback by default and supports an optional bearer token. No web or desktop account credentials are stored in SQLite.
