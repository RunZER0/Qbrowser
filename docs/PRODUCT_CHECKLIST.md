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

## 24-domain progress checkpoint

This is the running checkpoint for implementation work. The table above preserves the original 25 workstreams; this rollup combines security and production operations into domain 24. `[x]` means the local implementation exists and has a relevant test or inspection; `[~]` means implemented in part or still awaiting environment validation; `[ ]` means outstanding. These are not public-release certifications.

| # | Domain | Current checkpoint | Next acceptance gate |
|---|---|---|---|
| 01 | Agent architecture | [x] Native screenshot/action/observe loop. [x] Agent-S retained as an optional backend. | [ ] Supervised end-to-end task on a real desktop using each selected engine. |
| 02 | Multimodal providers | [x] Z.ai, DeepSeek and compatible endpoint adapters; synthetic image/tool smoke probe. [~] Token usage and rough fallback estimates are recorded. | [ ] Authenticated vision/tool-call runs on each intended provider and model. |
| 03 | Desktop actions | [x] Validated PyAutoGUI input, coordinate scaling, fail-safe, bounded waits and key names. | [ ] Validate screenshots, Unicode, paste, multi-monitor and high-DPI behavior per OS/session. |
| 04 | Browser and applications | [x] Native path operates via visible GUI, without site-specific API scripts. | [ ] Complete supervised multi-app workflows with authenticated and file-dialog states. |
| 05 | Planning and progress | [x] Versioned dependency plan, acceptance conditions, progress evidence and finish gate. [x] 100-action simulated run. | [ ] Benchmark incomplete-result detection and plan revisions on real tasks. |
| 06 | Long contexts and memory | [x] Bounded complete-turn compaction and persisted findings/decisions/questions; runtime cadence gates. | [ ] Validate on long real tasks and provider context limits. |
| 07 | Durable persistence | [x] SQLite WAL jobs, events, plans, messages, working memory, action journal, evidence metadata and migrations. | [ ] Rehearse upgrades from every released schema and inspect backup/restore integrity. |
| 08 | Crash recovery | [x] Started GUI actions become unknown after interruption; jobs go to review and are not replayed. | [ ] Kill/restart during a real submission and verify no duplicate side effect. |
| 09 | Concurrency | [x] Atomic SQLite claim and exclusive cross-process desktop lock. | [ ] Exercise multiple queue producers and worker restarts on Windows, macOS and Linux. |
| 10 | Approval and scope | [x] Unapproved model-declared external actions pause; destructive native actions are blocked. [~] Approval remains job-scoped and classification remains model-declared. | [ ] Independent action classification and approval scoped to the proposed action. |
| 11 | Secrets and private data | [x] Environment-based provider keys, local API token, redacted typed text, optional AES-GCM evidence. [~] Job text and memory remain in local SQLite. | [ ] Review permissions, backups, clipboard handling, retention and key rotation on host OSs. |
| 12 | Dashboard and API | [x] Loopback job queue, plan, messages, action history, artifacts, evidence and stop/review controls. | [ ] Manual desktop/mobile usability, auth/error handling and accessibility review. |
| 13 | Messaging | [x] OpenClaw handoff skill and manually confirmed verified-file delivery. [~] No authenticated inbound WhatsApp receiver in Qbrowser. | [ ] Owner allowlist, inbound task auth, acknowledgements and end-to-end paired account test. |
| 14 | Files and artifacts | [x] Per-job workspace, output indexing and hashes. | [ ] Versioned revisions, cleanup/retention policy and tested restore for outputs. |
| 15 | Verification and evidence | [x] Plan completion evidence, `verify` status and operator-confirmed completion. [~] Encrypted screenshot evidence is opt-in. | [ ] Independent outcome verifier and benchmarked evidence quality. |
| 16 | Observability | [x] Redacted action intent/result journal, events, errors, token totals and model latency. [~] Dollar totals require user-configured rates; missing usage is estimated. | [ ] Confirm actionable diagnostics without exposing provider payloads or private screen data. |
| 17 | Cancellation | [x] Cooperative native stop checks; Agent-S timeout and process termination. | [ ] Test stop during slow inference, desktop action, OS prompt and worker shutdown on all OSs. |
| 18 | Installation and diagnostics | [x] Bootstrap, doctor, provider smoke and screenshot-only desktop smoke commands. | [ ] Fresh setup and permissions checks on physical Windows/macOS/Linux machines. |
| 19 | Tests and CI | [x] Local Linux run: 130 tests and 5 subtests, compileall, dependency check and CI lint selection pass. [x] CI matrix configured for Python 3.11/3.12 on three OSs. | [ ] Confirm hosted matrix result and execute actual desktop/provider integration tests. |
| 20 | Packaging and upgrades | [x] Python package metadata, optional dependency groups and schema migration. [~] No signed native installer/update path. | [ ] License/dependency notice review, reproducible release, upgrade test and signed artifacts. |
| 21 | Extensible executors | [x] Native and Agent-S execution remain behind the existing job/worker selection. | [ ] Add another mature executor through a stable adapter and contract test. |
| 22 | Waiting and scheduling | [x] Persisted waits wake on restart and reobserve the live screen. [~] No external event scheduler or periodic task service. | [ ] Add/test event-driven resume only if required; validate wake timing under restart. |
| 23 | Budgets and reliability | [x] Per-job action/step/time/token ceilings, usage records, optional configured price estimate, bounded provider retries. | [ ] Validate provider-specific budgets, failure backoff and multi-day reliability without replay. |
| 24 | Security and production operations | [x] Threat model, loopback default, SQLite backup and review gates documented. [~] Model labels are not a security boundary. | [ ] Hostile-page audit, independent security review, restore drill, log rotation and multi-day soak. |

### Work log

- 2026-10-12: Imported upstream main snapshot `73dccbe` into a local `codex/local-finish` branch. No remote changes were sent.
- 2026-10-12: Added durable plans, working memory, conversation steering, action intent/result records, encrypted opt-in screenshot evidence, UI/API inspection paths and a 100-action synthetic regression test.
- 2026-10-12: Added token/latency recording, rough usage fallback when an endpoint omits usage, and optional dollar estimates from user-configured per-million-token rates.
- 2026-10-12: Full local suite passed: 130 tests and 5 subtests. `compileall`, `pip check`, `git diff --check`, and the CI-selected Ruff rules passed. Document routes are mounted behind API authentication and enforce the request-size limit. Real provider and GUI validation remain open.

## Current implementation evidence

**Implemented and locally tested:** native multimodal action adapter; dynamic plans with completion evidence; SQLite WAL state and action journal; persisted working memory and owner messages; encrypted opt-in evidence; exclusive desktop lease; bounded context, steps, wall-clock and token totals; cancellation; per-job output hashes; persisted waiting and wake-up; explicit operator verification; optional owner-confirmed OpenClaw delivery; SQLite online backup; local API token support; synthetic provider and screenshot smoke probes; model usage and latency records.

**Cross-platform validation:** CI is configured for Python 3.11/3.12 on Windows, macOS and Linux, plus native dependency installation on all three. This local run executed on Linux only. Tests are headless and do not demonstrate functional mouse/keyboard use on a logged-in desktop.

**Still open for public unattended release:** live inference-provider vision/tool-call validation; logged-in Windows/macOS/Linux GUI smoke; safer action-scoped approval and independent checks for consequential actions; authenticated WhatsApp intake with paired owner account; independent result verification; output revision preservation and restore tooling; signed installers/updates; multi-day soak and security review. The current release is a supervised local beta candidate, not production certification.

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
- LangGraph persistence and memory concepts: https://docs.langchain.com/oss/python/langgraph/persistence — adapted concepts for checkpoints and memory; Qbrowser does not add LangGraph as a runtime dependency.
- Temporal workflow execution: https://docs.temporal.io/workflow-execution — durable event-history reference; Qbrowser keeps its smaller local SQLite state machine rather than adding a remote workflow service.
