# Qbrowser

Local-first AI computer operator using **one multimodal model** for screenshot understanding, reasoning and GUI actions. Includes persistent SQLite jobs, an event journal, agent-defined checkpoints, a loopback dashboard and optional Agent-S backend.

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

Browse to http://127.0.0.1:8765, enter any natural-language task and queue it. The worker controls your visible desktop. Leave that session unlocked while working.

Examples:

```bash
qbrowser add "Open the browser, find the latest report, and summarize it"
qbrowser list
qbrowser checkpoints JOB_ID
qbrowser events JOB_ID
```

## Engines

- **`QBROWSER_ENGINE=native` (default):** one vision model, screenshot-and-action loop, built on OpenAI-compatible Chat Completions, PyAutoGUI, Pillow and Pyperclip. No separate 7B grounding model.
- **`QBROWSER_ENGINE=agent_s`:** mature [Agent-S](https://github.com/simular-ai/Agent-S) as an alternative, installed with `python bootstrap.py --agent-s`. Requires its own reasoning model and grounding endpoint.

The model connects via API or a local OpenAI-compatible inference endpoint. No website-specific APIs are required.

## Delivery and backup

After manually reviewing a completed task, the owner may send verified files through a paired OpenClaw WhatsApp channel with `qbrowser deliver JOB_ID --confirm`. Set `QBROWSER_OWNER_WHATSAPP` to the owner's number first. To back up the job database, use `qbrowser backup /path/to/backup.db`. See the [installation guide](docs/INSTALL.md) for recovery procedures.

## Document and artifact workbench

Every job now has a versioned document vault. Upload instructions or source documents through **Documents → Upload file**, produce local DOCX/PDF/XLSX/PPTX files using the managed document tools, convert supported text/data formats, review QA results and download any immutable revision from the authenticated dashboard.

Examples:

```bash
qbrowser upload JOB_ID ./assignment.pdf --role input
qbrowser generate JOB_ID answer.docx ./document-payload.json
qbrowser convert JOB_ID output answer.docx answer.txt
qbrowser versions JOB_ID
qbrowser document-qa JOB_ID output answer.docx
```

Automated retention is disabled by default. See [document and artifact documentation](docs/DOCUMENTS.md) for the supported conversion matrix, limitations, security and lifecycle operations.

## Important behavior

Tasks and milestones survive restarts. Qbrowser serializes mouse/keyboard access, bounds execution by actions/time, and flags uncertain outcomes rather than replaying unknown GUI effects. Model-reported completion is marked `verify` pending operator confirmation. Model-classified external actions stop for approval; destructive actions are blocked. A model can still misclassify an unsafe click, so use a sandbox or dedicated computer and supervision. GUI waits can be persisted and reobserved on wake, and token limits bound model spending.

**Operational status:** automated tests run on Windows/macOS/Linux with Python 3.11 and 3.12, and native dependencies install on all three platforms. Real model and OS GUI end-to-end tests require an authorized local machine with credentials. Authenticated WhatsApp intake, independent outcome verification and multi-day unattended testing remain outstanding.

See [installation](docs/INSTALL.md), [security boundaries](SECURITY.md), [product checklist](docs/PRODUCT_CHECKLIST.md), and [OpenClaw handoff](examples/openclaw/SKILL.md).
