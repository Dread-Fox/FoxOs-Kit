---
name: websec-framework
description: The headless web-app authorization testing framework (Burp alternative) installed at @HOME@/websec on this Kali box
metadata:
  node_type: memory
  type: reference
  originSessionId: 92e46c4e-b5a5-538d-91a5-cf8bfe5873cc
  modified: 2026-11-14T02:21:21.575Z
---

Headless Burp-equivalent framework for web-app authz testing on this GUI-less Kali Pi (aarch64). Built 2026-11-13.

Location: `@HOME@/websec/` — `websec.py` (main CLI, symlinked to `/usr/local/bin/websec`), `profiles/*.json`, `loot/`, `README.md`.

Commands (Burp component -> backing tool): `authz` (built-in Autorize-equivalent engine), `req` (Repeater), `proxy` (mitmdump), `fuzz` (ffuf/Intruder), `scan` (nuclei/Scanner), `crawl` (katana), `recon` (subfinder+dnsx), `params` (arjun), `oob` (interactsh-client/Collaborator), `jwt` (Decoder).

Gap tools installed this session: arjun, paramspider, subfinder, katana, dnsx (all apt/Kali), interactsh-client (GitHub release v1.4.1 arm64, sha256 verified, in /usr/local/bin). No Go/Cargo on box; Burp itself NOT installed (GUI-only, won't run headless — don't try).

`websec authz <profile>`: safe methods only by default; `--allow-writes` needed for PATCH/POST/DELETE. Profile = base, login_path, roles{login|token|{}}, endpoints[{method,path,allow[]}]. Verdict VIOL = deny-role got 2xx. See [[fms-facilitydesk-audit]].

facilitydesk profile re-confirmed the two broken-access-control findings (/pm-schedules, /vendors reachable by technician) automatically. Profile holds the rotated tech cred, chmod 600.
