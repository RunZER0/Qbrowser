# Qbrowser

Local-first AI computer operator using **one multimodal model** for screenshot understanding, reasoning and GUI actions. Includes persistent SQLite jobs, an event journal, agent-defined checkpoints, a loopback dashboard and optional Agent-S backend.

**Native default:** Z.ai `glm-5.3-flash`. **Alternative:** DeepSeek `deepseek-flash`. Both use official OpenAI-compatible vision and tool-calling interfaces; website interactions occur through the visible computer interface.

## Install

```bash
git clone https://github.com/RunZER0/Qbrowser.git
cd Qbrowser
python bootstrap.py
```

Edit `.env` with `ZAI_API_KEY` (or set `QBROWSER_PROVIDER=deepseek` and `DEEPSEEK_API_KEY`). Keep that file private.

In two different terminals (activate `.venv` or invoke the executable by its full path):

```bash
qbrowser serve
qbrowser worker
```

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

## Important behavior

Tasks and milestones survive restarts. Qbrowser serializes mouse/keyboard access, bounds execution by actions/time, and flags uncertain outcomes rather than replaying unknown GUI effects. Model-reported completion is marked `verify` pending operator confirmation. External actions need explicit owner authorization; pixel-level GUI control cannot perfectly classify consequences, so a sandbox or dedicated computer and supervision are still necessary.

**Operational status:** unit/API tests run in GitHub Actions; model and OS GUI end-to-end tests require an authorized local machine with credentials. WhatsApp messaging, automatic delivery and long-lived external-event waiting are subsequent integrations, documented in the [product checklist](docs/PRODUCT_CHECKLIST.md).

See [installation and security details](docs/INSTALL.md), [product checklist](docs/PRODUCT_CHECKLIST.md), and [OpenClaw handoff](examples/openclaw/SKILL.md).
