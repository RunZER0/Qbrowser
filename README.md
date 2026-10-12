# Qbrowser

Local-first AI computer operator using **one multimodal model** for screenshot understanding, reasoning and GUI actions. Includes persistent SQLite jobs, enforced task plans, structured working memory, an action journal, a conversational loopback dashboard and optional Agent-S backend.

**Native default:** Z.ai `glm-5.3-flash`. **Alternative:** DeepSeek `deepseek-flash`. Both use official OpenAI-compatible vision and tool-calling interfaces; website interactions occur through the visible computer interface.

## Install

```bash
git clone https://github.com/RunZER0/Qbrowser.git
cd Qbrowser
python bootstrap.py
```

Edit `.env` with `ZAI_API_KEY` (or set `QBROWSER_PROVIDER=deepseek` and `DEEPSEEK_API_KEY`). Keep that file private. The bootstrap generates a private `QBROWSER_API_TOKEN`; enter that value into the workboard token field.

In two different terminals (activate `.venv` or invoke the executable by its full path):

```bash
qbrowser serve
qbrowser worker
```

Before starting the worker on a new machine, validate your installation:

```bash
qbrowser doctor
qbrowser smoke-model
qbrowser smoke-desktop
```

The model smoke test sends a synthetic image to the configured inference endpoint; the desktop smoke test only reads a screenshot. Neither executes GUI clicks.

## Optional capabilities

- Install Playwright browser tools with `pip install -e '.[browser]'` and `playwright install chromium`. The native agent gets role-based DOM/accessibility navigation, tab management, scrolling, managed downloads and approval-gated uploads alongside desktop screenshots. Navigation checks DNS results for private targets; set `QBROWSER_BROWSER_ALLOWED_HOSTS` to restrict destinations further. Profiles are stored per desktop identity.
- Audio uploads are accepted as input artifacts. Transcription runs only after an explicit document read or conversion and requires `QBROWSER_TRANSCRIPTION_BASE_URL` plus `QBROWSER_TRANSCRIPTION_API_KEY`. The configured service receives the audio bytes.
- Curated preferences and procedures can be kept between tasks with `qbrowser memory-add`, searched with `qbrowser memory-search`, and removed with `qbrowser memory-delete`. `qbrowser memory-add --supersedes ID` records an explicit replacement while keeping history. SQLite FTS5 is the default; optional local vector retrieval uses `pip install -e '.[memory]'`, `QBROWSER_MEMORY_EMBEDDINGS=1` and `qbrowser memory-reindex`. FastEmbed runs inference locally; its model is downloaded on first use. Add only non-secret notes; memory remains unencrypted and is treated as untrusted context.
- On Linux, `QBROWSER_OS_SANDBOX=bwrap` enables an optional bubblewrap wrapper for Agent-S. The job workspace is writable; the host tree is read-only and network remains enabled. This is disabled by default. For genuinely sensitive work, use a dedicated OS account or VM.
- Separate isolated displays can run workers with distinct `qbrowser worker --desktop-id DISPLAY_ID` values. Never give two workers the same physical desktop identity.
- `QBROWSER_MAX_CONTEXT_TOKENS` bounds the retained conversation using a local token estimate; task-wide usage is separately capped by `QBROWSER_MAX_TOTAL_TOKENS`.

Browse to http://127.0.0.1:8765, enter a natural-language task and queue it. The worker controls your visible desktop. Leave that session unlocked while working. Each job has a conversation panel for answers and follow-up instructions while it is running or waiting for input.

Examples:

```bash
qbrowser add "Open the browser, find the latest report, and summarize it"
qbrowser list
qbrowser plan JOB_ID
qbrowser message JOB_ID "Use the short format and save a PDF copy"
qbrowser actions JOB_ID
qbrowser checkpoints JOB_ID
qbrowser events JOB_ID
```

## Engines

- **`QBROWSER_ENGINE=native` (default):** one vision model, screenshot-and-action loop, built on OpenAI-compatible Chat Completions, PyAutoGUI, Pillow and Pyperclip. No separate 7B grounding model.
- **`QBROWSER_ENGINE=agent_s`:** mature [Agent-S](https://github.com/simular-ai/Agent-S) as an alternative, installed with `python bootstrap.py --agent-s`. Requires its own reasoning model and grounding endpoint.

The model connects via API or a local OpenAI-compatible inference endpoint. No website-specific APIs are required.

OpenRouter works through the custom OpenAI-compatible endpoint. Set `QBROWSER_PROVIDER=custom`, `QBROWSER_BASE_URL=https://openrouter.ai/api/v1`, `QBROWSER_MODEL` to an OpenRouter model ID that supports image input and tool calls, and `QBROWSER_MODEL_API_KEY` in the private `.env` file. OpenRouter uses the OpenAI-compatible request format; image and tool support still depends on the selected model. See [OpenRouter tool calling](https://openrouter.ai/docs/guides/features/tool-calling) and [image input](https://openrouter.ai/blog/tutorials/send-image-to-llm/).

## Delivery and backup

After manually reviewing a completed task, the owner may send verified files through a paired OpenClaw WhatsApp channel with `qbrowser deliver JOB_ID --confirm`. Set `QBROWSER_OWNER_WHATSAPP` to the owner's number first. To back up the job database, use `qbrowser backup /path/to/backup.db`. See the [installation guide](docs/INSTALL.md) for recovery procedures.

## Document and artifact workbench

Every job now has a versioned document vault. Upload instructions or source documents through **Documents → Upload file**, produce local DOCX/PDF/XLSX/PPTX files using the managed document tools, convert supported text/data formats, review QA results, download an immutable revision, or restore an older revision as a new latest version from the authenticated dashboard.

Examples:

```bash
qbrowser upload JOB_ID ./assignment.pdf --role input
qbrowser generate JOB_ID answer.docx ./document-payload.json
qbrowser convert JOB_ID output answer.docx answer.txt
qbrowser versions JOB_ID
qbrowser restore JOB_ID output answer.docx --version 1
qbrowser document-qa JOB_ID output answer.docx
```

Automated retention is disabled by default. See [document and artifact documentation](docs/DOCUMENTS.md) for the supported conversion matrix, limitations, security and lifecycle operations.

## Important behavior

Plans, subtask acceptance conditions, dependency state, working memory, conversation messages, and action intents/results survive restarts. Qbrowser serializes mouse/keyboard access, bounds execution by actions/time, detects repeated unchanged clicks, and flags uncertain outcomes rather than replaying unknown GUI effects. A task cannot finish until each plan item has evidence; model-reported completion then remains `verify` pending operator confirmation. Model-classified external actions pause for a one-time approval bound to that proposed action and current screen; the task form also offers an explicit whole-job pre-approval. Destructive actions are blocked. A model can still misclassify an unsafe click, so use a sandbox or dedicated computer and supervision. GUI waits can be persisted and reobserved on wake, and token limits bound model spending. Token totals and inference latency are visible per job. Endpoints without token usage return a rough estimate; optional dollar totals need both current per-million-token rates configured in `.env`.

Ordinary screenshots remain in memory. To retain encrypted local evidence at plan milestones, before and after approved external actions, on errors, and at completion, install `.[evidence]`, run `qbrowser evidence-key`, place the printed key in `.env`, and set `QBROWSER_CAPTURE_EVIDENCE=1`. Evidence files are stored under `~/.qbrowser/evidence/`, outside deliverable folders, and expire after 30 days by default. Qbrowser cannot recover a lost evidence key.

**Operational status:** the local core, document lifecycle, independent reviewer code, keyring option and CI matrix are implemented. The current Qbrowser environment has no provider key and lacks the OpenAI-compatible SDK and PyAutoGUI, so live inference and mouse/keyboard acceptance remain open. WhatsApp inbound is reserved for the final phase; multi-day unattended testing and signed releases are also open.

See [installation](docs/INSTALL.md), [security boundaries](SECURITY.md), [product checklist](docs/PRODUCT_CHECKLIST.md), and [OpenClaw handoff](examples/openclaw/SKILL.md).
