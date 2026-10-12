# Qbrowser product release checklist

**Status date:** 2026-10-12. **Release goal:** dependable local-first general-purpose AI computer operator with a native single-model loop, persistent job management, and optional Agent-S backend. A successful unit test is not proof of successful desktop operation.

Legend: **REUSE** = adopt maintained implementation; **ADAPT** = integrate or tailor; **BUILD** = Qbrowser-specific logic; **VALIDATE LOCAL** = cannot verify on cloud CI.

| # | Workstream | Implementation / variables | Origin | Completion gate |
|---|---|---|---|---|
| 01 | Agent architecture | Native screenshot → tool call → execute → observe; configurable provider; optional Agent-S | ADAPT OpenAI-compatible chat-completions / Agent-S | Real browser task on host succeeds |
| 02 | Native multimodal providers | Z.ai GLM-5.3-Flash and DeepSeek `deepseek-flash`; image payloads, tool calls, API URL, key, timeouts | REUSE provider APIs; BUILD small adapter | Test genuine authenticated provider requests, errors and image understanding |
| 03 | Desktop actions | Screenshot, click, double-click, move, scroll, type/paste, hotkeys and keypress, wait | REUSE PyAutoGUI / Pillow | Validate DPI, coordinate mapping, keyboard, Unicode, OS permissions |
| 04 | Browser and applications | Logged-in accounts, window switching, file dialogs, uploads and downloads | REUSE Agent-S and OS UI; BUILD no website-specific scripts | Task covering multiple real applications |
| 05 | Agent supervision | Dynamic plan, progress checkpoints, observe/replan, completion judgment | BUILD task loop around mature model tool calling | Multi-step test, incomplete result detection |
| 06 | Long contexts | Screenshot truncation, capped turns, bounded history, model cost protection | BUILD compacted observations | Complex task works beyond 25 actions without context blowup |
| 07 | Persistence | SQLite WAL jobs, immutable events, checkpoints and state transitions | REUSE SQLite; existing Qbrowser | Simulated crash preserves records |
| 08 | Crash recovery | Unknown GUI side effects go to needs_review, never silent replay | Existing Qbrowser, ADAPT | Kill worker and restart without duplicating submission |
| 09 | Concurrency | One desktop controller; queue, priority and bounded timeouts | Existing Qbrowser | Concurrent enqueue and claim tested |
| 10 | Approval and scope | Trusted owner, explicit external-action permission, redaction, action restrictions | BUILD | Dangerous actions require review; no false security guarantees |
| 11 | Secrets | Key env vars / OS credential store; avoid logging tokens or screenshots by default | REUSE OS environment/keyring later | No keys in repo, logs, database |
| 12 | Dashboard | Queue, live stage, event history, model choice, approval, recovery | ADAPT existing FastAPI | Browser usability test on desktop and mobile |
| 13 | Messaging intake | WhatsApp owner gating, attachments, delivery and acknowledgments | ADAPT existing OpenClaw integration | Authenticated end-to-end WhatsApp task |
| 14 | File/artifact versions | Per-job workspace, file discovery, output records and hashes | BUILD using pathlib/SQLite | Revisions and output files traceable |
| 15 | Verification | Screenshots, task-completion criteria, human review for uncertainty | BUILD with model observations; preserve manual check | Measurable outcome verification on benchmark tasks |
| 16 | Observability | Action log without secrets, errors, state and model usage, resume diagnostics | BUILD | Failure has actionable diagnostic |
| 17 | Cancellation | Cooperative stop between actions; timeout/interrupt subprocess cleanup | BUILD | Stop while running, no orphaned controls |
| 18 | Installation | Local bootstrap for Windows/macOS/Linux, doctor and .env | Existing Qbrowser, ADAPT | Fresh install on all three OSs |
| 19 | Tests | Unit / API / simulated multimodal tool calls / CI; GUI smoke and E2E | REUSE pytest/GitHub Actions | CI passes + OS-specific manual test |
| 20 | Packaging/release | Versioning, license notices, security notice, upgrade migrations | BUILD | Reproducible tagged release and upgrade |
| 21 | Extensible tools | Future browser-use, Playwright, Word and code operators without rewriting core | ADAPT mature libraries | Additional executor works behind stable adapter |
| 22 | Scheduling/awaiting | Persist waiting for external reports/events, polling and wake-up | REUSE Temporal or OpenClaw after evaluation | Restart-safe external-event wait |
| 23 | Cost / reliability | Per-job model and budget, retry/backoff with no redoing side-effects | REUSE SDK retries; BUILD budgets | Prevent runaway usage |
| 24 | Security threat model | Prompt injection, malicious pages, unsafe clicks, exfiltration, isolate user profiles | REUSE published computer-use guidance; BUILD policy | Security review with hostile website test |
| 25 | Production operations | Backup/restore, log rotation, diagnostics, hardware/network failure, upgrades | BUILD | Multi-day unattended reliability trial |

## Current implementation evidence

**Implemented in repository (cloud-tested):** workstreams 02 (OpenAI-compatible screenshot+tool protocol), 03 (PyAutoGUI action adapter, screenshot resizing and high-DPI mapping), 05 (dynamic progress records and model observe/act loop), 06 (bounded action history and duration), 07–09 (SQLite, crash review, exclusive worker), 12 (workboard with per-job model choice), 14 (workspace + hashed outputs), 16 (events and heartbeats), 17 (cooperative stop), and automated portions of 18–19.

**Available as local adapters:** 01 (native engine and Agent-S selection), 04 (GUI through existing toolkit), 13 (OpenClaw skill installer). Live provider responses, actual OS GUI control and WhatsApp account authentication are NOT certified by cloud tests.

**Not release-complete:** high-assurance consequential-action interception, independently verified task outcomes, provider budget metering, automatic external wait/wake scheduling, reliable customer delivery, backup/restore, security assessment, multi-OS E2E smoke matrix and multi-day endurance testing. These remain acceptance gates, despite the code being ready for local installation.

## Release phases

**Phase A — foundation:** durable store, state machine, events, CLI, dashboard, exclusive worker, bootstrap and CI. Previously implemented. Must inspect and retain.

**Phase B — native multimodal engine:** implemented and cloud-tested at protocol/simulator level; real-provider and desktop usability tests remain.

**Phase C — hardened local operator:** reliable cancellation, evidence and artifacts, interruptions, per-job constraints, dashboard control, installation documentation. Target for this implementation as time permits.

**Phase D — complete connected product:** authenticated WhatsApp intake, delivery, long waits and scheduled resumptions, real OS/account E2E tests, installer signing, multi-day validation, security review. Requires access to a real desktop, credentials, provider endpoints, and owner-controlled accounts; cannot be certified by cloud CI alone.

## Dependencies and assumptions

- Cloud model keys or compatible local endpoints supplied by owner; no invented credentials.
- A physical unlocked logged-in desktop or isolated VM where clicks, typing and screenshots are permitted.
- Native models may generate imprecise screen coordinates; expose actual screenshot dimensions and enforce bounds.
- Provider API compatibility must be validated in a live test, particularly tool calls with image messages.
- Any action against a website is executed through the GUI. Model inference may use an API.
- Qbrowser cannot safely guarantee that a pixel-level click is harmless. Default settings should prefer visible supervision, dry runs and review gates for external actions.
- Do not claim a finished flagship release without passing Phase D acceptance tests.

## Mature reference implementations

- Agent-S: https://github.com/simular-ai/Agent-S — GUI agent, optional backend
- OpenClaw: https://github.com/openclaw/openclaw — messaging/agent handoff
- PyAutoGUI: https://pyautogui.readthedocs.io/ — local cross-platform input and screenshots
- OpenAI computer use guide: https://developers.openai.com/api/docs/guides/tools-computer-use — tool/action feedback protocol, safety guidance
- Z.ai quickstart: https://docs.z.ai/guides/overview/quick-start — multimodal provider compatibility
- DeepSeek vision guide: https://api-docs.deepseek.com/guides/vision/ — image input and valid model ID
- DeepSeek tool calls: https://api-docs.deepseek.com/guides/tool_calls/
