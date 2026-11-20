> **v3.4.0:** Sign-in is required by default. See [LOCAL-SERVICE.md](LOCAL-SERVICE.md) for operator accounts, sessions and remote access.

# Configuration reference

Default file: `~/.config/foxworx-workbench/settings.env`. Use `bash scripts/configure.sh`, which creates secure permissions and preserves existing values. Environment variables override file values; dotenv interpolation is disabled. Restart the service after changes. `--init` creates only a missing config (and prints its setup token once). `--upgrade` is what the installer runs; it never weakens existing authentication. `--rotate-token` issues a new setup token and revokes all sessions. `--show-token` only works for legacy plaintext tokens. `--insecure-open-mode` / `--require-login` switch the explicit no-login opt-in off and on. Operator accounts are managed with `bash scripts/operator.sh`.

| Key | Default / meaning |
|---|---|
| FOXWORX_AUTH_MODE | `local` (default: sign-in required), `proxy` (sign-in behind an HTTPS reverse proxy), legacy `cloudflare` (Access JWT), or `open` (no login; requires `FOXWORX_OPEN_MODE_ACK`). |
| FOXWORX_LOCAL_TOKEN_HASH | `sha256:<hex>` of the setup token. Written by the installer; the plaintext is shown once. Legacy `FOXWORX_LOCAL_TOKEN` (plaintext) is still accepted. |
| FOXWORX_BIND_HOST | Listening address. Must be loopback (`127.0.0.1` default, or `::1`). Non-loopback values are refused. |
| FOXWORX_OPEN_MODE_ACK | Must be exactly `I_UNDERSTAND_NO_LOGIN` for `open` mode to start. Set by `configure.sh --insecure-open-mode`. |
| FOXWORX_PORT | `8765`; supported 1024–65535; listener remains 127.0.0.1. |
| FOXWORX_ORIGIN | `http://127.0.0.1:8765` locally; exact HTTPS origin in optional `proxy` mode, no path. |
| FOXWORX_SESSION_SECRET | Generated random secret; at least 32 characters. Keep private. |
| FOXWORX_LOCAL_TOKEN | Empty on a new open-mode installation. An old token remains stored but unused after migration; only legacy local/proxy login uses it. |
| FOXWORX_STATE | `~/.local/share/foxworx-workbench-v2` (expanded absolute default). |
| CF_TEAM_DOMAIN | `https://YOURTEAM.cloudflareaccess.com`; not the workbench hostname. |
| CF_ACCESS_AUD | Access application's audience identifier. |
| FOXWORX_ALLOWED_EMAILS | Comma-separated explicit emails; mandatory in Cloudflare mode. |
| OPENAI_API_KEY | Optional shared Pi-level credential or legacy v2 key; operators can instead save encrypted keys in the browser. |
| OPENAI_MODEL | Optional legacy OpenAI model ID for a v2 configuration. Per-operator selections override it. |
| FOXWORX_AI_ENCRYPTION_KEY | Separate secret, generated on new installs and automatically added to existing v2 configs by `configure --init`. Must be backed up with per-operator saved keys. Independent from session-token rotation. |
| FOXWORX_AI_ENDPOINTS | JSON map of named `ollama`, `lmstudio`, `vllm` and `custom` server addresses approved by the local administrator. Defaults to `{}`; loopback Ollama/LM Studio are built in. See AI-PROVIDERS.md. |
| ANTHROPIC_API_KEY, GEMINI_API_KEY, XAI_API_KEY, DEEPSEEK_API_KEY, MISTRAL_API_KEY | Optional shared Pi-level credentials. Operator-specific keys override these. |
| FOXWORX_MAX_JOBS | New ARM64 default `2`; new x86-64 default `4`; supported 1–8 active managed jobs. No background task queue beyond those slots. |
| FOXWORX_JOB_TIMEOUT | `900` seconds; supported 5–86400. |
| FOXWORX_LOG_MIB | `5`; supported 1–100 MiB captured bytes per job. Reaching the limit can stop the job. |
| FOXWORX_MAX_TERMINALS | New ARM64 default `4`; new x86-64 default `8`; supported 1–12 node-wide sessions. |
| FOXWORX_RESERVE_MIB | New ARM64 default `256`; new x86-64 default `1024`; minimum free space before a managed job starts. Not a live disk quota. |
| FOXWORX_EXTRA_TOOL_PATHS | Colon-separated additional absolute executable directories, e.g. `/home/foxoperator/go/bin`. Restart then Refresh. |
| FOXWORX_SHELL | `/bin/bash`; an installed executable interactive shell. Its startup files may affect behavior. |

Session limits: server-side sessions of 8 hours (optional 7-day remembered device), revocable on sign-out, password change, operator removal and token rotation; at most 20 concurrent sessions per operator; sign-in throttled at 8 failures per client or name per 15 minutes; pending proposal 10 minutes; ticket 30 seconds/one use; socket reauthentication at most 15 minutes; detached terminal expiry 1 hour; PTY replay 256 KiB; up to 128 output messages per connected browser; paste/input at most 16 KiB per frame; upload 10 MiB; export 64 MiB uncompressed; 1,000 listed workspace files; 10,000 exported jobs; 200 recent jobs/proposals per view; 60 displayed chat messages; 12 prior messages used for AI history. AI context may include the explicitly opted-in last 12 KB of a selected job.

The installer creates a **user** systemd unit at `~/.config/systemd/user/foxworx-workbench.service`. No application-level password reset email, shared admin role, secret-vault integration, service-token login or unattended API approval is provided.

AI requests currently have a fixed 2,400-output-token budget per provider response, up to three internal response rounds, and a 80-second total backstop with provider calls capped at 48 seconds, and a 95-second browser timeout. Reasoning-heavy requests may need a different reviewed budget/model; incomplete responses are errors, not executed work. No live model tuning or token-cost meter is provided in v2.

For operator-specific AI model selection/keys, use the browser **Model & keys** dialog; never place secrets in engagement notes or chat. Saved keys are encrypted at rest, but trusted same-UID shell users can read the encryption key. The vendor SDK is an optional dependency (`requirements-ai.txt`, installed by default). See [AI-PROVIDERS.md](AI-PROVIDERS.md).

**Cross-platform config:** architecture defaults apply only when config is first created. Existing settings/data remain intact on upgrade. The GPU runtime is not configured through FOXWORX: manage Ollama, NVIDIA drivers and `CUDA_VISIBLE_DEVICES` in their own host services. See [PLATFORMS.md](PLATFORMS.md).

## v3.4.0: authentication by default

`FOXWORX_AUTH_MODE=local` is the default for new installs, and `scripts/install.sh` (`configure --upgrade`) never weakens an existing `local`, `proxy` or `cloudflare` configuration. The v3.3.x no-login default is migrated to `local` with a backup. `FOXWORX_PUBLIC_ORIGIN` registers one HTTPS tunnel hostname; sign-in is enforced through it. `open` mode requires `FOXWORX_OPEN_MODE_ACK=I_UNDERSTAND_NO_LOGIN`, a loopback `FOXWORX_BIND_HOST`, and no public origin, and it rejects proxied requests. See [LOCAL-SERVICE.md](LOCAL-SERVICE.md).
