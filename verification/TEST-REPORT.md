# FOXWORX DreadFoxHacker v3.4.0 — Release verification

**Date:** 2026-11-19. **Scope:** authentication by default, upgrade preservation of authenticated settings, Rabbit Hole guardrails, dependency upgrades, CI and static analysis, with regression coverage of the existing terminal, jobs, AI, Discord, hardware, graph and DreadFox features. Tests ran on a Linux x86-64 Kali development host as an unprivileged user, **not** on a Raspberry Pi or a production deployment.

## Results

| Check | Command | Result |
|---|---|---|
| Unit/integration tests, Python 3.14 | `PYTHON=.venv/bin/python bash scripts/test.sh` | **384 passed, 1 skipped**; compile, shell-syntax and JS-syntax checks pass |
| Unit/integration tests, Python 3.13 | `python -m pytest -q` in a fresh 3.13 venv | **384 passed, 1 skipped** |
| Lint | `ruff check foxworx tests` (`ruff.toml`) | Clean |
| Static security analysis | `bandit -q -r foxworx -c bandit.yaml` | Clean; every skip and `nosec` is documented |
| Dependency audit | `pip-audit -r requirements.txt`, `-r requirements-ai.txt`, `-r requirements-dev.txt` | No known vulnerabilities |
| Secrets in history | regex scan of `git log --all -p` for common token and key formats | None found |
| Live server smoke | `python tests/live_local_smoke.py` | **PASS** ([live-local-smoke.json](live-local-smoke.json)) |
| Browser smoke | `python tests/browser_smoke.py` (headless Chromium, fixture API) | **PASS** ([browser-smoke.json](browser-smoke.json)); screenshots regenerated |
| Manual browser flow | Playwright against a real server | `/` redirects to `/login` → wrong token rejected → setup-token sign-in → workbench → sign out → back at sign-in; no page errors |

The skipped test (`test_adapters.py`) needs to create a root-owned policy directory, which a non-root test process cannot do by design. CI (`.github/workflows/ci.yml`) repeats the test suite on Python 3.11 and 3.13 and runs Ruff, Bandit and pip-audit on every push.

## New coverage in v3.4.0

**`tests/test_auth.py` (31 tests)**

- A fresh install defaults to `local` mode with only a hashed setup token; the plaintext is printed once and never written.
- Anonymous requests: `/` redirects to `/login`; **every** `/api/*` route (enumerated from the router, 60+ method/path pairs) returns 401; terminal creation returns 401; the terminal WebSocket is refused, including with a valid ticket but no session.
- Sessions: `HttpOnly` + `SameSite=Strict` cookies, `Secure` over HTTPS, 8 h / 7 d lifetimes, server-side expiry and revocation on sign-out (a copied cookie stops working), CSRF bound to the session, same-origin login only, throttling to 429 after 8 failures, audited failures.
- Operator accounts: scrypt hashing, reserved names, password policy, per-operator data isolation, session revocation on removal, CLI add/remove.
- Open mode: refused without the explicit acknowledgement; refused on `0.0.0.0`, LAN IPs, `::` and hostnames; `serve` exits with an error; refused with a public origin; rejects DNS-rebinding `Host` values, tunnel/proxy headers and non-loopback clients.
- Upgrades: `local`, `proxy` and `cloudflare` configurations are preserved byte for byte; unacknowledged v3.3.x open mode moves to `local` with a 0600 backup and a one-time token, idempotently; acknowledged open mode is preserved; `--insecure-open-mode` / `--require-login` round-trip; `--rotate-token` revokes sessions.
- Security headers, with HSTS only on HTTPS origins.

**`tests/test_rabbit_guard.py` (51 tests)**

- In-scope commands pass (nmap, curl, gobuster, hydra, whatweb, scheme-less in-scope URLs, numeric option values).
- Blocked: out-of-scope and excluded IPs, CIDRs not fully contained, IP ranges, scope-bypass and code-execution options, `file://`, credential/config/state paths, the workbench listener (including `0.0.0.0` and IPv4-mapped forms), out-of-scope resolvers and proxies, denied executables, scheme-less `host/path`, and decimal/hex/octal/short IPv4 encodings.
- End to end: an out-of-scope `run_tool` request is blocked, audited as `rabbit-command-blocked`, never creates a job, and the model is told why; the next in-scope command runs and is audited with its command line; a denied executable never launches.
- Defence in depth: `JobManager.delegate` rejects an out-of-scope argv even when the runner's own check is skipped; human-reviewed proposals carry `Guardrail:` warnings.

Existing suites (`test_local_service.py`, `test_dual_access.py`, `test_api.py`, `test_cli.py`, `test_release_v28.py`, `live_local_smoke.py`, `browser_smoke.py`) were updated for the new defaults. `test_token_free.py` was removed because it asserted the unauthenticated behaviour this release removes.

## Not verified here

Raspberry Pi ARM64 installation, systemd startup after reboot, a real Cloudflare Tunnel or reverse proxy, live Codex/ChatGPT sign-in, real Discord API traffic, cloud or local inference providers, and real assessment traffic against targets. These need acceptance testing on each deployment (see [docs/INSTALL.md](../docs/INSTALL.md#acceptance-gate-before-remote-team-use)). Rabbit Hole guardrails are argument-level checks, not an OS or network sandbox; see [SECURITY-REVIEW.md](SECURITY-REVIEW.md).
