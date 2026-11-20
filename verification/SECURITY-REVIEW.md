# Security review · v3.4.0

**Date:** 2026-11-19 · **Baseline:** v3.3.3 (`48d8a62`) · **Reviewer:** internal review (FoxWorx LLC, AI-assisted). This is not an independent third-party audit.

## Scope and method

- Manual review of authentication, session, host/origin and CSRF handling (`auth.py`, `app.py`, `config.py`, `cli.py`), terminal WebSocket and PTY handling, managed jobs and argv construction, Rabbit Hole autonomous execution, evidence upload/download/export, provider credential storage, Discord and documentation fetchers, the installer/upgrade path, and the frontend's DOM sinks.
- Tooling: **Bandit** 1.9.4, **Ruff** 0.17.6 (pyflakes + bugbear), **pip-audit** 2.10.1 against all three requirements files, a regex scan of the full git history for credentials (OpenAI/GitHub/AWS/Slack/Discord token formats, private keys, webhook URLs), live tests against a real `uvicorn` process, and headless Chromium.
- Severity uses CVSS 3.1 qualitative ratings in the context of the intended deployment (a single host running a real shell as the service account).

## Summary

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | No authentication by default; any visitor reaching the service (including through a tunnel) gets a shell as the service account | **Critical** | Fixed |
| 2 | DNS rebinding against the open-mode listener: `Host` was not validated, so a malicious web page could read the CSRF token and open a terminal | **High** | Fixed |
| 3 | Installer upgrade migrated authenticated deployments (`local`/`proxy`/`cloudflare`) to the unauthenticated mode | **High** | Fixed |
| 4 | Rabbit Hole installed-CLI mode executed model-built argv with no check of destinations or programs: out-of-scope targets, the workbench's own API, and reading `settings.env` into a cloud model prompt were all possible | **High** | Mitigated (guardrails; not a sandbox) |
| 5 | Guard bypass found during this review: scheme-less `host/path` and decimal/hex/octal/short IPv4 encodings (`2130706433:8765`) evaded the new destination checks | High (pre-release) | Fixed before release |
| 6 | Known-vulnerable dependencies: PyJWT 2.13.0 (5 advisories), Starlette 0.50.0 via FastAPI 0.128.2 (several advisories), filelock 3.32.7 via LiteLLM (13), pytest 9.0.2 (dev) | Medium | Fixed |
| 7 | Setup token stored in plaintext and re-displayable; stateless JWT sessions could not be revoked by logout; 30-day remember-me | Medium | Fixed |
| 8 | Login throttling was a single global bucket, cleared on any success: a lockout lever for attackers and resettable by a legitimate login | Medium | Fixed |
| 9 | Authentication and CSRF enforced only inside each handler (a forgotten check would expose a route; unauthenticated requests reached body validation first) | Low | Fixed (central middleware + route-enumeration test) |
| 10 | Failed sign-ins not logged or audited; autonomous commands audited by job ID only | Low | Fixed |
| 11 | Cookie `SameSite=Lax`; no HSTS, COOP or CORP headers | Low | Fixed |
| 12 | `.gitignore` did not cover `settings.env` or its backups if copied into the checkout | Low | Fixed |
| 13 | Real tunnel hostname in tests; stale screenshots and docs advertising "token-free" mode | Info | Fixed |
| 14 | No CI; 42 unused imports, 3 unused variables, untriaged Bandit output | Info | Fixed |

No credentials were found in the working tree or git history. No HTML injection sinks (`innerHTML`, `insertAdjacentHTML`, `eval`) exist in the frontend; all dynamic `href` values are allowlisted server-side and the CSP (`script-src 'self'`) blocks `javascript:` URLs. File upload, download and export use no-follow, component-wise opens and reject traversal. All subprocesses use argv lists; there is no `shell=True`.

## Details of fixed findings

### 1–3. Authentication (Critical/High)

- `local` mode with sign-in is the default. Every `/api/*` route is authenticated and every API mutation is Origin- and CSRF-checked in middleware before routing; `tests/test_auth.py` enumerates all routes and asserts anonymous requests get 401. `/` redirects anonymous browsers to `/login`; terminal WebSockets need a session plus a one-use ticket.
- Per-operator accounts with scrypt hashes (`foxworx/accounts.py`, `scripts/operator.sh`). The setup token is printed once and stored as SHA-256 only.
- Server-side sessions: random 256-bit cookie, digest stored, 8 h / 7 d expiry, revoked on sign-out, password change, operator removal and token rotation. `HttpOnly`, `SameSite=Strict`, `Secure` over HTTPS. CSRF token bound to the session.
- The `Host` allowlist is enforced in every mode (fixes rebinding). Open mode requires `FOXWORX_OPEN_MODE_ACK`, refuses non-loopback binds and public origins, and rejects proxied requests.
- `configure --upgrade` preserves authenticated settings exactly and moves only the unacknowledged v3.3.x open default to `local`, with a backup and a new one-time token.

### 4–5. Rabbit Hole guardrails (High)

`foxworx/rabbit_guard.py` runs inside `JobManager._check_delegation`, so it is enforced at delegation, approval and process launch. It requires every IP, CIDR, host, `host:port`, `user@host`, scheme-less `host/path` and URL argument to be in scope (exclusions win, CIDRs fully contained), rejects IP range syntax and non-canonical numeric addresses, always blocks the workbench's own listener (including `0.0.0.0` and IPv4-mapped forms), and denies interpreters, launchers, destructive, privilege, system, package-manager and remote-shell executables, scope-bypassing or code-executing options (`nmap -iL/-iR`, `sqlmap --os-shell/--eval`, `curl -K/-T` …), `file://` URLs and credential/config/state paths. Each autonomous command and each blocked attempt is audited.

Finding 5 was found by probing the first version of this guard. It was fixed and regression-tested before release.

### 6. Dependencies (Medium)

`fastapi 0.128.2 → 0.165.0` (Starlette `0.50.0 → 1.10.0`, now pinned explicitly), `PyJWT 2.13.0 → 2.15.1`, `uvicorn 0.48.0 → 0.54.0`, `litellm 1.104.0 → 1.110.1` with `filelock` pinned to `4.2.12`, `pytest 9.0.2 → 9.0.3`. `pip-audit` now reports no known vulnerabilities for all three requirements files, and the full test suite passes on Python 3.13 and 3.14 with the new versions.

### Bandit triage

All remaining Bandit results were reviewed. B404/B603/B606 (subprocess with fixed argv; terminal shell exec) are skipped in `bandit.yaml` with the rationale recorded there. Eight low/medium false positives (empty-string defaults flagged as passwords, a dataclass field named `shell`, `/tmp/` prefixes in a deny list, an intentional `except: pass` in an optional observer) carry inline `# nosec <id>` with the reason on the line above. One `assert` used for control flow was replaced with an explicit check. CI fails on any new finding.

## Recommendations (not changed in this release)

Ordered by risk reduction. These are larger changes or behaviour changes that need a product decision.

1. **OS-level containment for autonomous runs (High).** Run Rabbit Hole jobs as a separate unprivileged UID inside a network namespace with an nftables egress allowlist derived from the engagement scope (or under bubblewrap/systemd-run with `IPAddressAllow=`), with read-only mounts and a per-run workspace. This is the only way to enforce destinations after DNS resolution and redirects.
2. **Privilege separation between the web service and operator shells (High).** Today every terminal can read the SQLite database, `settings.env`, the session secret and the AI key-encryption key. Run PTYs as per-operator Unix accounts (or at least a different UID from the service) so a shell cannot read other operators' sessions, keys or audit records.
3. **MFA and roles (Medium).** Add TOTP or WebAuthn for operator accounts, an admin role for account management in the UI, and an option to disable setup-token sign-in once a named administrator exists.
4. **Audit integrity and SOC integration (Medium).** Ship audit events (sign-ins, failures, approvals, autonomous commands, guard blocks) to journald/syslog in a structured format for SIEM ingestion, and make the local audit trail append-only or hash-chained. Surface `system:auth` events to administrators in the UI.
5. **Persistent throttling (Low).** Login throttling is in memory and resets on restart; persist it in SQLite or rely on fail2ban reading the new `foxworx.security` log lines.
6. **Credential encryption key handling (Low).** Derive the provider-key encryption key with HKDF and a stored random salt, version ciphertexts, and stop falling back to the session secret when `FOXWORX_AI_ENCRYPTION_KEY` is missing, so rotating the session secret can never orphan stored keys.
7. **systemd hardening (Low).** Evaluate `PrivateTmp`, `ProtectSystem=strict` with explicit `ReadWritePaths`, `RestrictSUIDSGID`, `LockPersonality` and a `SystemCallFilter` for the user unit. These were not added because they could not be validated against Kali user-service sessions in this review.
8. **Code structure (Info).** `create_app()` is an ~800-line closure; split it into FastAPI routers per feature. Older modules use dense one-line style without type hints or docstrings; adopt `ruff format` and `mypy --strict` gradually, starting with `jobs.py`, `rabbit.py` and `scope.py`.
9. **Independent assessment (Info).** Commission an external penetration test before any client-facing or multi-team deployment.

## Verification

See [TEST-REPORT.md](TEST-REPORT.md) for commands and results. Key evidence: 384 automated tests passing (including `tests/test_auth.py` and `tests/test_rabbit_guard.py`), Ruff/Bandit/pip-audit clean, `tests/live_local_smoke.py` against a real server, and `tests/browser_smoke.py` in headless Chromium.
