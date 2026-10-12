# Installation and operation

Qbrowser contains the orchestration, progress state, CLI, HTTP API and GUI worker adapter. The mature GUI controller is installed as Agent-S on the user's machine.

## Install on Windows

1. Install Python 3.11+ and Git. Enable the option to add Python to PATH.
2. Clone the repository and open PowerShell inside it.
3. Execute:

       py -3 -m venv .venv
       .\.venv\Scripts\python.exe -m pip install --upgrade pip
       .\.venv\Scripts\python.exe -m pip install -e ".[server,desktop]"
       Copy-Item .env.example .env
       .\.venv\Scripts\qbrowser.exe init
       .\.venv\Scripts\qbrowser.exe doctor

4. Edit .env with the reasoning model API key and grounding endpoint. Do not commit .env.
5. In terminal A, run .\.venv\Scripts\qbrowser.exe serve. Browse to http://127.0.0.1:8765.
6. In terminal B, run .\.venv\Scripts\qbrowser.exe worker.

Give the Python process permission to control the desktop if Windows prompts. Keep your desktop unlocked for GUI tasks.

## Install on macOS/Linux

    python3 -m venv .venv
    . .venv/bin/activate
    python -m pip install --upgrade pip
    python -m pip install -e ".[server,desktop]"
    cp .env.example .env
    qbrowser init
    qbrowser doctor

Install Tesseract OCR as recommended by Agent-S: brew install tesseract (macOS), or your system package manager on Linux. Permit screen recording and accessibility control on macOS. A physical logged-in desktop session is required.

In separate terminals start qbrowser serve and qbrowser worker.

## Required Agent-S configuration

Agent-S depends on a reasoning model and a separate grounding model to locate UI targets. See https://github.com/simular-ai/Agent-S for the latest compatible model configurations.

Edit the local .env file:
- AGENT_S_PROVIDER, AGENT_S_MODEL, and the corresponding provider key (such as OPENAI_API_KEY).
- AGENT_S_GROUND_URL must be reachable from the computer. You can run a compatible grounding model locally or use a provider endpoint. The .env.example value is only a placeholder.
- AGENT_S_GROUND_MODEL and the grounding dimensions must match the endpoint.
- AGENT_S_TIMEOUT_SECONDS governs how long one GUI run may last.

The controller runs ordinary GUI tasks without website-specific API keys; however, its reasoning model and grounding endpoint may require their own keys or model servers. The interface also requires a running graphical session.

## Existing-agent integration

Qbrowser calls the installed Agent-S CLI with a natural-language task. Agent-S implements the screenshot/observe/reason/act loop; Qbrowser does not duplicate that machinery. You may fork upstream Agent-S locally if you want to modify the GUI controller itself:

    git clone https://github.com/simular-ai/Agent-S.git vendor/Agent-S
    python -m pip install -e vendor/Agent-S

Do not run the optional Agent-S local coding environment on untrusted tasks. This project deliberately leaves it disabled.

For OpenClaw, create a skill that calls either:

    qbrowser add "A natural-language task..."
    qbrowser show <id>

or the loopback HTTP endpoints below. Do not enable an untrusted WhatsApp sender to authorize external actions. The agent's existing OpenClaw integration is documented in https://github.com/simular-ai/Agent-S/tree/main/integrations/openclaw.

## JSON HTTP interface

- GET /api/jobs
- POST /api/jobs with {"instruction":"...", "priority":0, "external_approved":false}
- GET /api/jobs/{id}
- GET /api/jobs/{id}/events
- GET /api/jobs/{id}/checkpoints
- PUT /api/jobs/{id}/checkpoints with {"key":"research","state":"done","detail":"Sources checked"}
- POST /api/jobs/{id}/requeue, /verified, /cancel, /pause or /approve-external

The API listens on 127.0.0.1:8765 by default. If QBROWSER_API_TOKEN is set, clients must send Authorization: Bearer <token>. Binding beyond loopback is refused unless a token is set; consider the local API a privileged control surface.

## Recovery semantics

- queued: waiting for the worker.
- running: Agent-S currently controls the computer.
- verify: GUI process exited successfully; a human must check the actual outcome.
- needs_review: interruption, timeout or failed GUI execution; inspect the screen before requeueing.
- completed: operator confirmed outcome.
- paused/cancelled: no process running for this job.

A planning agent can create arbitrary durable milestones using qbrowser checkpoint <id> <key> <state> "<detail>" and query them with qbrowser checkpoints <id>. Checkpoint states: pending, in_progress, done, blocked, skipped. These are generic agent-defined milestones rather than predefined website workflows.

A task is never blindly retried after unknown GUI side effects. The worker obtains an OS-level exclusive lock and moves interrupted jobs to needs_review upon restart. An event journal is persisted to SQLite. This first version tracks job-level execution and outcomes; it does not resume an individual Agent-S click sequence mid-run.

## Boundaries

This initial release does not ingest WhatsApp messages, run arbitrary remote desktop sessions, execute concurrent desktop operators or guarantee that Agent-S obeys its natural-language safety restrictions. Manual operator verification remains necessary before treating a task as completed.
