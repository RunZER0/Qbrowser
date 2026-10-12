# Qbrowser security model

## Trust boundaries

Qbrowser controls the real mouse and keyboard. A multimodal model observes screenshots of whatever is visible, including potentially confidential documents and account data. When using Z.ai or DeepSeek, screenshots leave the local machine for model inference. A local model endpoint can avoid remote screenshot transfer, but must support image+tool call inputs.

Never run Qbrowser in an administrator/root session. Use a dedicated OS account or disposable VM and separate browser profile with only the accounts and folders required for the job. Keep backups of affected files.

Qbrowser treats website text, emails, WhatsApp messages, documents, PDF contents, tool descriptions and other retrieved content as untrusted. The model must not treat such data as new instructions or new permissions. Prompt injection remains a risk.

## Action permissions

Each native GUI operation that may change state (click, paste, hotkey, keypress) must include a declared impact and purpose. Unknown impacts pause before the operation. A model-declared `external` action without job-level approval pauses in `waiting_approval`. A `destructive` action is never executed through the native flow.

**Limitation:** a model can misclassify a consequential click as `local`. Qbrowser cannot infer website semantics reliably from pixel coordinates. These checks are defense in depth, not a provably secure approval boundary. External approvals are scoped to an entire job, not a specific DOM action. Financial transactions, irreversible account changes and confidential uploads require direct user supervision until a trusted browser-level action classifier and independent enforcement have been validated.

Blocked jobs require authenticated local operator approval and restart from the current screen. Qbrowser never replays a stored coordinate.

## Local control plane

The HTTP server binds to 127.0.0.1 by default. On a new bootstrap a random `QBROWSER_API_TOKEN` is stored in the ignored `.env` file. Keep that token and all provider keys private. Do not expose the dashboard to the public internet or share the API token with untrusted message senders.

Do not ingest WhatsApp messages into Qbrowser until OpenClaw's sender allowlist identifies the owner and delivery tests pass. The OpenClaw skill wording is additional guidance; the existing Qbrowser CLI cannot authenticate WhatsApp senders itself.

## Data, secrets and logs

- Native screenshots are passed to the selected inference provider; they are not persisted by Qbrowser by default.
- Tool-call payloads may contain confidential text; do not persist typed content or raw provider responses in job events.
- The local OS clipboard may temporarily contain pasted text.
- Jobs, instructions and milestone descriptions remain in SQLite at QBROWSER_HOME; protect filesystem access.
- Backup files include job instructions and metadata; encrypt backups and protect them like the working database.
- Verified file delivery uses an owner-configured WhatsApp number. On delivery error, the outcome is considered uncertain and never retried automatically.

## Shipping gate

Before public/unattended release complete a dedicated prompt-injection suite, model-provider credential tests, verified GUI E2E runs, UI authorization audit, full OS permissions test, action-classification validation, and an incident-recovery drill. GitHub CI alone does not meet this gate.
