# Qbrowser native installation

## Requirements

- Python 3.11 or 3.12 recommended, Git, an unlocked physical Windows/macOS/Linux desktop.
- API key for either Z.ai GLM-5.3-Flash or DeepSeek Flash, or a compatible local model serving vision + tool calls.
- Active graphical session and desktop permissions for screenshots and keyboard/mouse events.
- Strongly recommended: separate browser profile or isolated VM, especially with authenticated accounts.
- When using the optional Agent-S backend, follow its platform-specific dependencies; Agent-S currently requires Python <=3.12 and a separate grounding endpoint.

## Local bootstrap

    git clone https://github.com/RunZER0/Qbrowser.git
    cd Qbrowser
    python bootstrap.py

This creates .venv, installs Qbrowser, FastAPI and the native GUI executor, copies .env.example to .env, and initializes the database. It does NOT start desktop control or connect to accounts.

For server-only development: python bootstrap.py --minimal
For native + Agent-S: python bootstrap.py --agent-s

Windows (PowerShell):

    .\.venv\Scripts\qbrowser.exe doctor
    .\.venv\Scripts\qbrowser.exe serve

macOS/Linux:

    .venv/bin/qbrowser doctor
    .venv/bin/qbrowser serve

In a second terminal start .venv/bin/qbrowser worker (PowerShell: .\.venv\Scripts\qbrowser.exe worker). Use Ctrl+C to exit; the current operation may need a manual review.

Open http://127.0.0.1:8765 to queue tasks, inspect milestones, request stop, review files and verify outcomes. A fresh bootstrap writes a randomized QBROWSER_API_TOKEN to .env. Enter that token into the dashboard's token field before using the API.

## Model setup

Edit .env (never commit it):

Z.ai:

    QBROWSER_ENGINE=native
    QBROWSER_PROVIDER=zai
    QBROWSER_MODEL=glm-5.3-flash
    ZAI_API_KEY=<YOUR_KEY>

DeepSeek:

    QBROWSER_ENGINE=native
    QBROWSER_PROVIDER=deepseek
    QBROWSER_MODEL=deepseek-flash
    DEEPSEEK_API_KEY=<YOUR_KEY>

For a local OpenAI-compatible multimodal model:

    QBROWSER_ENGINE=native
    QBROWSER_PROVIDER=custom
    QBROWSER_MODEL=<YOUR_MODEL_ID>
    QBROWSER_BASE_URL=http://127.0.0.1:PORT/v1
    QBROWSER_MODEL_API_KEY=<LOCAL_SERVER_KEY_OR_DUMMY>

Custom endpoints must serve image-bearing Chat Completions and function tool calls. The endpoint is not automatically started by Qbrowser. Model support and function-call quality vary.

You can override the engine, provider and model per job in the web workboard or:

    qbrowser add "Open a browser and create a local report" --provider deepseek --model deepseek-flash

The user-interface operating loop does not use any ChatGPT, Gemini, Grammarly or other website API. Those sites are navigated by GUI. The AI inference API is separate and optional when running an appropriate local model.

## Local acceptance checks

After filling your provider credentials in .env, run:

    qbrowser doctor
    qbrowser smoke-model
    qbrowser smoke-desktop

smoke-model sends a synthetic screenshot to your configured model, asks it to locate a colored target, and validates its structured tool action without clicking anything. This incurs a small inference charge. smoke-desktop captures a screenshot and verifies coordinate mapping without moving the mouse. Neither test establishes long-running GUI reliability. Before any real task, use a dedicated test browser profile and perform a supervised two-app workflow.

## Native interaction protocol

The model receives a downsized screenshot of the current desktop (default width 1280). It returns one structured function call: computer_action, record_progress, finish_task, or request_assistance. Qbrowser validates and executes the action through PyAutoGUI, records a durable event, captures the next screenshot and repeats.

Available actions: screenshot, click, double_click, move, scroll, type (clipboard paste), hotkey, keypress and wait. Desktop fail-safe is enabled (PyAutoGUI upper-left corner). On macOS, grant Screen Recording and Accessibility permissions. On Linux, X11 input support is recommended; Wayland often restricts synthetic input and screenshots. Clipboard paste on Linux may require xclip/xsel.

Coordinate scaling maps image pixels to OS input coordinates, including high-DPI displays. A single monitor is recommended. Multiple monitors and unusual scaling combinations require explicit testing.

Set these limits in .env:

    QBROWSER_MAX_STEPS=80
    QBROWSER_MAX_SECONDS=3600
    QBROWSER_SCREEN_WIDTH=1280

No API keys are written into SQLite, event messages or Git-tracked files. Screenshots are transmitted to your chosen model provider for vision inference; choose providers and privacy settings accordingly. The GUI executor may place typed text temporarily on the OS clipboard.

## Persistence and failure semantics

- queued: accepted, waiting for a local worker.
- running: desktop worker holds exclusive OS lock; only one concurrent desktop agent.
- verify: model reports visually verified task completion, but operator review remains required.
- needs_review: model failure, blocker, timeout, interrupted run, or stop request. GUI state is uncertain.
- completed: owner/operator explicitly marks result verified.
- paused/cancelled: nonrunning task stopped.
- checkpoints: model-defined pending / in_progress / done / blocked / skipped milestones, persisted across restarts.

To stop a running job: qbrowser stop JOB_ID or press Request stop on the workboard. Stop is cooperative, checked between model/tool actions. A blocked provider call may take time to return. A stop does not roll back clicks already performed.

To rerun after a crash: inspect target app(s), use qbrowser requeue JOB_ID, then start/continue the worker. Native requeues receive earlier milestones but begin from the **current screenshot**, not an old click sequence.

Generated outputs should be saved under ~/.qbrowser/workspaces/JOB_ID (or QBROWSER_HOME/workspaces/JOB_ID). Files found there after a run are hashed and listed in the workboard. No automatic external delivery is implemented.

## Local HTTP API

- GET /api/jobs; POST /api/jobs with instruction, engine, provider, model, priority and external_approved
- GET /api/jobs/{id}; GET /api/jobs/{id}/events
- GET /api/jobs/{id}/checkpoints; PUT /api/jobs/{id}/checkpoints
- GET /api/jobs/{id}/artifacts
- POST /api/jobs/{id}/stop, /pause, /cancel, /requeue, /approve-external, /verified

The API listens on 127.0.0.1:8765. Optionally set QBROWSER_API_TOKEN for bearer authentication. Do not expose the HTTP server to the public internet. This service controls a real logged-in desktop.

## Output delivery and database backup

The owner may send a verified job's indexed files through an already paired OpenClaw WhatsApp channel:

    qbrowser verified JOB_ID
    qbrowser deliver JOB_ID --confirm

Set QBROWSER_OWNER_WHATSAPP to the *owner's own* E.164 number (example: +2547...). The delivery command checks the job is completed, validates every file hash, and reserves a delivery record BEFORE sending. Uncertain send outcomes never retry automatically: inspect WhatsApp before attempting anything else. Qbrowser does not deliver automatically or receive arbitrary WhatsApp messages by itself.

Back up the live job database (SQLite online backup API is safe with WAL):

    qbrowser backup /path/to/qbrowser-backup.db

A backup includes job state and metadata, not external workspaces or local API keys. Preserve workspaces separately. Restoring requires stopping the worker and server and taking an offline copy after integrity checking; automated restore is not yet implemented.

## Optional Agent-S backend

    python bootstrap.py --agent-s

Set QBROWSER_ENGINE=agent_s and configure its model/API credentials plus AGENT_S_GROUND_URL, AGENT_S_GROUND_MODEL and resolution. The existing upstream Agent-S CLI implements its screenshot/action loop. The native engine does NOT require the grounding endpoint.

For OpenClaw/WhatsApp, install OpenClaw separately, pair your account using the official OpenClaw WhatsApp setup, then run `qbrowser install-openclaw` inside the Qbrowser environment. This writes an owner-gated local skill to `~/.openclaw/workspace/skills/qbrowser/SKILL.md` pointing to the current Python interpreter. Restart or reload OpenClaw to discover the skill. This repository does not itself receive WhatsApp messages; the messaging channel and allowlisted owner are still separate setup tasks.

## Acceptance testing

1. Run python -m pytest -q on the installed environment.
2. Queue a benign task: "Open a text editor, type Qbrowser test and save a file in my job workspace."
3. Confirm the model identified coordinates and the worker actually clicked and saved.
4. Check a file appears in the workboard and matches its hash.
5. Test a task that uses two websites but submits no externally sensitive data.
6. Stop a job during execution, restart the worker, and verify needs_review, with no automatic replay.
7. Explicitly approve a harmless external submission only in a test account.
8. Test authenticated workflows, file dialogs, Unicode, high-DPI, and 30+ action contexts.
9. Run a hostile webpage prompt-injection test and verify the agent refuses unapproved external changes.

Do not run financial transfers, sensitive submissions, or destructive tasks unattended until runtime guards and OS isolation have undergone a dedicated security audit.

## Product readiness

docs/PRODUCT_CHECKLIST.md tracks the wider flagship requirements. Automated tests cover offline state, API and simulated native model interactions. Real provider/desktop integration, messaging, notification delivery, long-lived scheduler, production security audit and multi-day reliability are still required before an unattended release.
