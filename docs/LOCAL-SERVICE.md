# FOXWORX v3.4.0 — authenticated local service and remote access

FOXWORX runs as a **non-root systemd user service** listening on `127.0.0.1:8765`. Since v3.4.0, **sign-in is required by default** for the web UI, every API route, and every terminal WebSocket. The terminal is a real shell running as the service's Linux account, so the login is what stands between a browser and that account.

## Install or upgrade

```bash
cd ~/FoxWorx-DreadFoxHacker
git pull                       # or extract a new release into its own directory
systemctl --user stop foxworx-workbench 2>/dev/null || true   # finish work first
bash scripts/install.sh --preflight
bash scripts/install.sh
bash scripts/doctor.sh
```

What the installer does with your configuration (`~/.config/foxworx-workbench/settings.env`, mode 600):

| Existing configuration | Result |
|---|---|
| None (fresh install) | Creates `FOXWORX_AUTH_MODE=local` and generates a **setup token**, printed **once**. Only its SHA-256 hash is stored. |
| `local`, `proxy` or `cloudflare` | **Preserved exactly**: mode, port, origins, public origin, tokens and secrets are not touched. |
| `open` set by v3.3.x (no acknowledgement) | Moved to `local` (login required) and a new setup token is printed once. The previous file is kept as `settings.env.before-v3.4.0` (mode 600). Your public origin, provider keys, Discord settings and state are preserved. |
| `open` with `FOXWORX_OPEN_MODE_ACK=I_UNDERSTAND_NO_LOGIN` | Preserved (you opted in explicitly; see below). |

State in `~/.local/share/foxworx-workbench-v2/` is always preserved.

## Signing in

1. Open **http://127.0.0.1:8765**. You are redirected to the sign-in page.
2. First sign-in: leave **Operator name** empty and paste the setup token the installer printed. This signs you in as the built-in `local-operator` identity, which owns all records created by earlier versions.
3. Create a named account for each person:

   ```bash
   bash scripts/operator.sh add alice      # prompts for a password (12+ characters)
   bash scripts/operator.sh list
   bash scripts/operator.sh passwd alice   # also revokes alice's sessions
   bash scripts/operator.sh logout alice   # revoke all of alice's sessions
   bash scripts/operator.sh remove alice   # delete the account and revoke its sessions
   ```

   Each operator then signs in with their name and password. Their engagements, jobs, chat, provider keys and audit history are a separate namespace inside the app, and audit records are attributed to them.

**Lost or leaked setup token:** `bash scripts/configure.sh --rotate-token` prints a new token, rotates the session secret, and revokes every existing browser session. Restart the service afterwards.

### Session behaviour

- Sessions are stored server-side (only a SHA-256 digest of the cookie is kept). Signing out, changing a password, removing an operator or rotating the token revokes them immediately.
- Lifetime: 8 hours, or 7 days with **Remember this browser**. Expiry is enforced on the server.
- Cookie: `foxworx_session`, `HttpOnly`, `SameSite=Strict`, and `Secure` whenever the browser reaches the app over HTTPS (public origin or `proxy` mode).
- State-changing requests need an exact same-origin `Origin` header and a CSRF token bound to the session.
- Terminal WebSockets need a valid session, an exact `Origin`, and a 30-second single-use ticket bound to that operator and terminal.
- Failed sign-ins are rate limited: 8 failures per client address or operator name in 15 minutes, and 60 in total. Failures are written to the service log and the `system:auth` audit stream. Passwords and tokens are never logged.

## Remote access

The listener only binds a loopback address and only accepts loopback peers. Remote browsers reach it through one of these paths, and **sign-in remains enforced on all of them**.

**SSH port forward (simplest):**

```bash
ssh -N -L 127.0.0.1:8765:127.0.0.1:8765 operator@HOST_ADDRESS
```

**HTTPS tunnel or reverse proxy (for example Cloudflare Tunnel):** register the exact public hostname so the app accepts its `Host` header and issues `Secure` cookies:

```bash
bash scripts/configure.sh --public-origin https://workbench.example.com
systemctl --user restart foxworx-workbench
```

Point the tunnel at `http://127.0.0.1:8765`, keep WebSocket support enabled, and preserve the public `Host` header. Requests carrying tunnel headers under the plain localhost hostname are rejected. Adding Cloudflare Access/MFA in front is still a good idea (defence in depth), but it is no longer the only control.

**Binding a LAN address is not supported.** `FOXWORX_BIND_HOST` must be a loopback address in every mode. Use SSH or an HTTPS proxy instead, so that credentials and session cookies are never sent over plain HTTP on a network.

## Open (no-login) mode — explicit opt-in only

For a single-user lab VM where nobody else has a login or network path to the host, you can turn sign-in off:

```bash
bash scripts/configure.sh --insecure-open-mode   # sets FOXWORX_OPEN_MODE_ACK=I_UNDERSTAND_NO_LOGIN
systemctl --user restart foxworx-workbench
```

Open mode is constrained so it cannot be exposed by accident:

- The service **refuses to start** in open mode unless `FOXWORX_OPEN_MODE_ACK=I_UNDERSTAND_NO_LOGIN` is set, and refuses to bind any non-loopback address.
- It cannot be combined with `FOXWORX_PUBLIC_ORIGIN`.
- Requests that carry tunnel or proxy headers (`CF-Ray`, `CF-Connecting-IP`, `X-Forwarded-*`, `Forwarded`, `X-Real-IP`) are rejected with 403.
- The `Host` header must be `127.0.0.1:<port>`, `localhost:<port>` or `[::1]:<port>`, which blocks DNS-rebinding attacks from web pages.
- **What it does not protect against:** any local user or process on the same machine that can connect to 127.0.0.1 can operate the service account. Do not use open mode on shared hosts.

Return to login with `bash scripts/configure.sh --require-login`.

## `scripts/local-service.sh`

Points the deployment at `http://127.0.0.1:8765` (port, origin and bind address). It keeps login on: a `proxy` or `cloudflare` deployment becomes `local` login mode, and an unacknowledged `open` deployment becomes `local` with a new setup token. It only keeps open mode if you previously opted in explicitly.

## Troubleshooting

```bash
systemctl --user status foxworx-workbench --no-pager
journalctl --user -u foxworx-workbench -n 80 --no-pager
curl -fsS http://127.0.0.1:8765/api/auth-mode
# {"mode":"local","version":"3.4.0","public_origin":"","login":true}
```

- **`Unexpected hostname`:** the browser or tunnel used a hostname that is not configured. Use `127.0.0.1:8765`, or run `--public-origin` for your tunnel hostname.
- **`Tunnel must preserve its configured public Host header`:** the tunnel rewrote `Host` to localhost. Configure it to pass the public hostname through.
- **`Too many failed sign-in attempts`:** wait 15 minutes, or restart the service if you are the administrator.
- **Service fails to start after editing settings:** `bash scripts/doctor.sh` prints the validation error, for example an open-mode bind or a missing setup token.
