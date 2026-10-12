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

For OpenRouter:

    QBROWSER_ENGINE=native
    QBROWSER_PROVIDER=custom
    QBROWSER_MODEL=<OPENROUTER_MODEL_ID_WITH_IMAGE_AND_TOOL_SUPPORT>
    QBROWSER_BASE_URL=https://openrouter.ai/api/v1
    QBROWSER_MODEL_API_KEY=<OPENROUTER_API_KEY>

Custom endpoints must serve image-bearing Chat Completions and function tool calls. The endpoint is not automatically started by Qbrowser. Model support and function-call quality vary. OpenRouter uses the OpenAI-compatible format, but image input and tool support must be confirmed for the selected model.

For an independent read-only completion review, set `QBROWSER_VERIFIER_MODEL` to a model different from the executor. The verifier defaults to the task's provider and endpoint; override them with `QBROWSER_VERIFIER_PROVIDER` and `QBROWSER_VERIFIER_BASE_URL` if needed. If it uses the same provider, Qbrowser reuses that provider's API key; otherwise set `QBROWSER_VERIFIER_API_KEY`. It receives the current screenshot, task criteria, plan evidence, and extracted output text. It never performs GUI actions and it never marks the task complete. Its result still requires operator confirmation.

For an independent review before each state-changing GUI action, set `QBROWSER_ENABLE_ACTION_GUARD=1`. It uses the same separately configured verifier model and API credentials. It receives the current screenshot, task instruction, action, purpose, and coordinates; pasted text is represented by its character count. Uncertain actions require owner approval, destructive actions are blocked, and guard errors stop the task before the action. This adds an inference call per action and still requires real hostile-page validation.

Credentials can be stored in the OS keyring instead of `.env`:

    python -m pip install -e '.[keyring]'
    qbrowser secret-set ZAI_API_KEY

Enable lookup by setting `QBROWSER_USE_KEYRING=1` in `.env`. `secret-set` reads the value without echoing it and accepts only Qbrowser's known credential names. Environment values take precedence. Use `qbrowser secret-delete NAME` to remove a keyring entry. The selected OS keyring must be unlocked and available to the account running the worker.

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

The model receives a downsized screenshot of the current desktop (default width 1280). It must assess requirements, criteria, ambiguity, feasibility and expected resources before creating a plan. Qbrowser blocks execution until the assessment and plan exist; ambiguity pauses for an owner answer. The model marks the active plan item, updates working memory at least every eight GUI actions, and reconciles the plan at least every twelve actions. Owner follow-ups require reassessment and a plan revision before more GUI work. Qbrowser records an action intent before using PyAutoGUI, records the outcome after it returns, then captures the next screenshot.

Available actions: screenshot, click, double_click, move, scroll, bounded drag, type (clipboard paste), hotkey, keypress and wait. The clipboard is restored after a paste when the OS clipboard API can read its prior contents. Desktop fail-safe is enabled (PyAutoGUI upper-left corner). On macOS, grant Screen Recording and Accessibility permissions. On Linux, X11 input support is recommended; Wayland often restricts synthetic input and screenshots. Clipboard paste on Linux may require xclip/xsel.

The runtime raises model-declared impact when the owner task or action text contains consequential intent. Destructive intent is always blocked. This deterministic text policy cannot identify every consequential pixel target, so keep external-action pre-approval off unless the task explicitly requires it.

Coordinate scaling maps image pixels to OS input coordinates, including high-DPI displays. A single monitor is recommended. Multiple monitors and unusual scaling combinations require explicit testing.

Set these limits in .env:

    QBROWSER_MAX_STEPS=80
    QBROWSER_MAX_SECONDS=3600
    QBROWSER_SCREEN_WIDTH=1280
    QBROWSER_MAX_TOTAL_TOKENS=250000

Native job usage and model latency are persisted and shown on the workboard. Providers that return token usage are recorded directly. Compatible endpoints that omit usage receive a rough estimate; image token costs vary by model, so estimated usage is labeled. To display an estimated dollar total, set both `QBROWSER_INPUT_COST_PER_MILLION_USD` and `QBROWSER_OUTPUT_COST_PER_MILLION_USD` to the rates for the configured model. Update those rates when provider pricing changes. Qbrowser does not treat the estimate as a billing statement.

No API keys are written into SQLite, event messages or Git-tracked files. Screenshots are transmitted to your chosen model provider for vision inference; choose providers and privacy settings accordingly. Routine screenshots stay in memory. To retain encrypted milestones locally, install the optional dependency and configure a key:

    python -m pip install -e '.[evidence]'
    qbrowser evidence-key

Put the generated value in `.env` as `QBROWSER_EVIDENCE_KEY` and set `QBROWSER_CAPTURE_EVIDENCE=1`. Qbrowser saves encrypted images for completed plan items, before and after approved external actions, errors, and final results under `~/.qbrowser/evidence/`. They expire after 30 days by default. Losing the key makes the evidence unreadable. The GUI executor may place typed text temporarily on the OS clipboard.

## Persistence and failure semantics

- queued: accepted, waiting for a local worker.
- running: desktop worker holds exclusive OS lock; only one concurrent desktop agent.
- verify: model reports visually verified task completion, but operator review remains required.
- needs_review: model failure, blocker, timeout, interrupted run, or stop request. GUI state is uncertain.
- waiting_user: the agent asked a question; submit an answer in the conversation panel to resume.
- waiting_approval: an external action is paused; approve that proposed action once or cancel the job. This does not pre-approve later external actions.
- completed: owner/operator explicitly marks result verified.
- paused/cancelled: nonrunning task stopped.
- plans: durable parent/child items with dependencies, acceptance conditions and evidence; the runtime prevents GUI actions without a plan and active item.
- working memory: persisted summary, findings, decisions and open questions, injected after context compaction.
- action journal: redacted action intent and result records; typed text and screenshot contents are not included.
- messages: owner follow-ups are inserted into the next model turn; answers resume a waiting task from the current screen.
- checkpoints: concise human-readable milestones, persisted across restarts.

To stop a running job: qbrowser stop JOB_ID or press Request stop on the workboard. Stop is cooperative, checked between model/tool actions. A blocked provider call may take time to return. A stop does not roll back clicks already performed.

To rerun after a crash: inspect target app(s), use qbrowser requeue JOB_ID, then start/continue the worker. Native requeues receive earlier milestones but begin from the **current screenshot**, not an old click sequence.

Generated outputs should be saved under ~/.qbrowser/workspaces/JOB_ID (or QBROWSER_HOME/workspaces/JOB_ID). Files found there after a run are hashed and listed in the workboard. Screenshot evidence is kept outside this folder so it is not mistaken for a deliverable.

## Local HTTP API

- GET /api/jobs; POST /api/jobs with instruction, engine, provider, model, priority and external_approved
- GET /api/jobs/{id}; GET /api/jobs/{id}/events
- GET /api/jobs/{id}/events/stream for authenticated server-sent events
- GET /api/jobs/{id}/assessment; GET /api/jobs/{id}/verification-reviews; POST /api/jobs/{id}/verification-review
- GET /api/metrics; GET /api/health/ready; GET /health/live
- GET /api/desktop/screenshot (requires `QBROWSER_API_TOKEN`)
- GET/POST /api/schedules; DELETE /api/schedules/{id}; POST /api/jobs/{id}/wake
- GET /api/jobs/{id}/checkpoints; PUT /api/jobs/{id}/checkpoints
- GET /api/jobs/{id}/plan, /memory, /actions and /messages; POST /api/jobs/{id}/messages
- GET /api/jobs/{id}/evidence and /evidence/{evidence_id}
- GET /api/jobs/{id}/artifacts
- POST /api/jobs/{id}/stop, /pause, /cancel, /requeue, /approve-external, /verified

The API listens on 127.0.0.1:8765. The API token generated by bootstrap is required for non-loopback binds and is used for bearer authentication. Do not expose the HTTP server to the public internet. This service controls a real logged-in desktop.

## Output delivery and database backup

The owner may send a verified job's indexed files through an already paired OpenClaw WhatsApp channel:

    qbrowser verified JOB_ID
    qbrowser deliver JOB_ID --confirm

Set QBROWSER_OWNER_WHATSAPP to the *owner's own* E.164 number (example: +2547...). The delivery command checks the job is completed, validates every file hash, and reserves a delivery record BEFORE sending. Uncertain send outcomes never retry automatically: inspect WhatsApp before attempting anything else. Qbrowser does not deliver automatically or receive arbitrary WhatsApp messages by itself.

Back up the live job database (SQLite online backup API is safe with WAL):

    qbrowser backup /path/to/qbrowser-backup.db

Create a full private-data archive with the database, immutable artifact vault, workspaces and encrypted screenshot evidence:

    qbrowser backup-full /path/to/qbrowser-full-backup.zip

Restore it into a new, empty data directory, then set `QBROWSER_HOME` to that path before starting Qbrowser:

    qbrowser restore-full /path/to/qbrowser-full-backup.zip /path/to/new-qbrowser-data

The archive excludes `.env` and provider keys. It contains task instructions and files; protect it as private data. Restore verifies the manifest, every file hash, and SQLite integrity before installing the directory. `qbrowser db-version` lists applied schema migrations.

The database-only backup includes job state and metadata. Use the full archive when workspaces and document revisions must also be preserved.

To schedule a recurring task from the workboard or CLI:

    qbrowser schedule-add "Check this dashboard" --interval-seconds 86400
    qbrowser schedules
    qbrowser schedule-cancel SCHEDULE_ID

The local worker enqueues at most one run per due schedule after downtime and advances to the next future interval. Recurring jobs never inherit whole-job external-action approval. Use `qbrowser wake JOB_ID` or the authenticated API when an awaited external event arrives.

## Optional Agent-S backend

    python bootstrap.py --agent-s

Set QBROWSER_ENGINE=agent_s and configure its model/API credentials plus AGENT_S_GROUND_URL, AGENT_S_GROUND_MODEL and resolution. The existing upstream Agent-S CLI implements its screenshot/action loop. The native engine does NOT require the grounding endpoint.

For OpenClaw/WhatsApp, install OpenClaw separately and pair your account using its official setup, then run `qbrowser install-openclaw` inside the Qbrowser environment. This writes an owner-gated local skill to `~/.openclaw/workspace/skills/qbrowser/SKILL.md` pointing to the current Python interpreter. Restart or reload OpenClaw to discover the skill. Qbrowser retains owner-confirmed file delivery; inbound WhatsApp intake remains the final integration phase.

Agent-S subprocess logs are capped by `QBROWSER_MAX_AGENT_LOG_BYTES` (10 MiB by default). The worker removes regular `.log` files older than `QBROWSER_LOG_RETENTION_DAYS` (30 days by default); set it to `0` to disable pruning.

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

`docs/COMPLETE_ARCHITECTURE_CHECKLIST.md` preserves the full 24-section source checklist and its live status. `docs/PRODUCT_CHECKLIST.md` summarizes the mapped implementation workstreams. Automated tests cover offline state, API and simulated native model interactions. Live provider, desktop, and cross-platform acceptance remain environment gates; inbound WhatsApp work is reserved for the final phase.
