---
name: kali-tool-catalog
description: "Rolling catalog of useful CLI tools on this Kali box; regenerate with the `toolkit` command"
metadata:
  node_type: memory
  type: reference
  originSessionId: 92e46c4e-b5a5-538d-91a5-cf8bfe5873cc
  modified: 2026-11-14T02:40:21.353Z
---

This Kali Pi is operated headlessly through Claude Code — the user does NOT use the Kali GUI. I am their direct interface to all CLI tools.

Rolling catalog lives at `@HOME@/toolkit/`:
- `refresh.py` -> symlinked to `/usr/local/bin/toolkit`. Run `toolkit` anytime to regenerate.
- `CATALOG.md` — generated: curated tools by Kali-menu category, with ✓ presence, purpose, version.
- `.snapshot.json` — previous run; refresh reports NEW/REMOVED tools since last time (that's the "rolling" part).

Baseline 2026-11-13: 106/121 curated tools present. Absent then: bettercap, crackmapexec (netexec supersedes it), dalfox, ddrescue, docker, enum4linux-ng, gdb, go, joomscan, kerbrute, ltrace, ncat, podman, rustscan, steghide, strace, termshark, tor, volatility3, zsteg. Installable on request via apt / release binary.

Burp Suite deliberately NOT installed (GUI-only, can't run headless) — use the [[websec-framework]] instead for authz/web testing. To add a tool: `sudo apt install <t>`, verify, then run `toolkit` to refresh the catalog.
