# Qbrowser security model

## Trust boundaries

Qbrowser controls the real mouse and keyboard. A multimodal model observes screenshots of whatever is visible, including potentially confidential documents and account data. When using Z.ai or DeepSeek, screenshots leave the local machine for model inference. A local model endpoint can avoid remote screenshot transfer, but must support image+tool call inputs.

Never run Qbrowser in an administrator/root session. Use a dedicated OS account or disposable VM and separate browser profile with only the accounts and folders required for the job. Keep backups of affected files.

Qbrowser treats website text, emails, WhatsApp messages, documents, PDF contents, tool descriptions and other retrieved content as untrusted. The model must not treat such data as new instructions or new permissions. Prompt injection remains a risk.

## Action permissions

Each native GUI operation that may change state (click, paste, hotkey, keypress) must include a declared impact and purpose. An unclassified action stops for review. A model-declared `external` action pauses in `waiting_approval` unless the owner explicitly pre-approved external actions for the whole job. Approval from that pause is bound to a digest of the exact action proposal and the current perceptual screen signature; it authorizes one matching action and is consumed when the action is journaled. A `destructive` action is never executed through the native flow.

**Limitation:** a model can misclassify a consequential click as `local`. Qbrowser cannot infer website semantics reliably from pixel coordinates. These checks are defense in depth, not a provably secure approval boundary. External approvals are scoped to an entire job, not a specific DOM action. Financial transactions, irreversible account changes and confidential uploads require direct user supervision until a trusted browser-level action classifier and independent enforcement have been validated.

When `QBROWSER_ENABLE_ACTION_GUARD=1`, a separately configured verifier model reviews each proposed state-changing action against the current screenshot. External or uncertain actions require owner approval; destructive actions stop. This model review is a defense-in-depth signal, not a security proof. Typed text is not sent to the guard; the guard receives only its character count.

Blocked jobs require local operator approval and restart from a fresh screen. Qbrowser never replays a stored coordinate. The dashboard's explicit whole-job pre-approval remains broader than the one-time proposal flow, and model-declared purposes and impact labels still cannot serve as an independent security boundary.

## Local control plane

The HTTP server binds to 127.0.0.1 by default. On a new bootstrap a random `QBROWSER_API_TOKEN` is stored in the ignored `.env` file. Keep that token and all provider keys private. Do not expose the dashboard to the public internet or share the API token with untrusted message senders.

Do not ingest WhatsApp messages into Qbrowser until OpenClaw's sender allowlist identifies the owner and delivery tests pass. The OpenClaw skill wording is additional guidance; the existing Qbrowser CLI cannot authenticate WhatsApp senders itself.

## Data, secrets and logs

- Native screenshots are passed to the selected inference provider for vision; choose a provider and account privacy settings that fit the material on screen. Ordinary screenshots are memory-only.
- If `QBROWSER_CAPTURE_EVIDENCE=1`, selected milestone screenshots are encrypted with AES-GCM under a user-managed key and stored in `QBROWSER_HOME/evidence/`. The key is not stored in SQLite. Keep a protected backup of it; losing it makes evidence unreadable. Retention defaults to 30 days. Encrypted screenshots still contain the original sensitive screen data.
- Tool-call payloads may contain confidential text; do not persist typed content or raw provider responses in job events.
- The local OS clipboard may temporarily contain pasted text.
- Jobs, instructions, conversation messages, plan details, working memory, action purposes and usage summaries remain in SQLite at QBROWSER_HOME; protect filesystem access. The action journal omits typed text and screenshot data, but coordinates and declared purpose remain visible to local users.
- Backup files include job instructions and metadata; encrypt backups and protect them like the working database.
- API and provider credentials can be resolved from environment variables first, with optional OS keyring lookup (`QBROWSER_USE_KEYRING=1`). Qbrowser never stores credentials in the job database; the actual keyring security depends on the active OS backend and user session.
- Verified file delivery uses an owner-configured WhatsApp number. On delivery error, the outcome is considered uncertain and never retried automatically.

Model token totals and latency are recorded per job. When a compatible endpoint omits token usage, Qbrowser uses a rough estimate that counts each image at a fixed fallback value; this is not provider billing data. Dollar totals appear only when both user-configured input and output token rates are set, and are estimates that must be updated when model pricing changes.

## Shipping gate

Before public/unattended release complete a dedicated prompt-injection suite, model-provider credential tests, verified GUI E2E runs, UI authorization audit, full OS permissions test, action-classification validation, and an incident-recovery drill. GitHub CI alone does not meet this gate.

## Optional browser and transcription adapters

- Playwright uses a persistent profile per desktop identity under `QBROWSER_HOME/browser_profiles`. It blocks non-web URL schemes and local/private IP targets unless explicitly host-allowlisted, and checks DNS answers before top-level navigation to reduce rebinding/redirect exposure. Set `QBROWSER_BROWSER_ALLOWED_HOSTS` to constrain destinations further. Explicitly allowlisted private hosts bypass that check by design. Page text and accessibility snapshots remain untrusted. Managed uploads use the same external approval gate as native GUI actions; downloads are container-validated before entering the artifact vault. Arbitrary JavaScript is not exposed.
- Audio files are only sent for transcription after an explicit `document_action` read or text conversion. Configure `QBROWSER_TRANSCRIPTION_BASE_URL` and `QBROWSER_TRANSCRIPTION_API_KEY`; transcription is disabled otherwise. Use a trusted HTTPS endpoint. The transcript is untrusted source content and may contain prompt injection.
- Cross-task memory is owner-curated through the local CLI, uses SQLite FTS5 full-text relevance and optional local FastEmbed vectors, and supports expiry, explicit supersession and deletion. The selected embedding model is downloaded on first use; inference then runs locally. Do not store credentials, personal secrets or confidential account data. Memory is not encrypted at rest and retrieved notes are treated as untrusted data.

## Optional Linux subprocess sandbox

Set `QBROWSER_OS_SANDBOX=bwrap` to run the legacy Agent-S child inside bubblewrap with a read-only host tree, private temporary directory, process namespaces and the job workspace as its only writable host path. Network remains shared so the model endpoint and task websites can work. The GUI socket is available to permit desktop control. This is a write-isolation layer, not a confidentiality boundary: host files can still be read, and the desktop can still be controlled. The feature is Linux-only, disabled by default and requires local desktop acceptance. If host namespace policy prevents bubblewrap startup, the worker fails closed before launching Agent-S. Use a dedicated OS account or VM for sensitive tasks.
