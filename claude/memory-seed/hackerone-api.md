---
name: hackerone-api
description: "HackerOne toolkit at ~/h1 — `h1` CLI, Bounty Radar desktop widget, sync timer, intel DB, creds location"
metadata:
  node_type: memory
  type: reference
  originSessionId: 8bda96c2-ec81-57aa-81a1-91545f238b5f
  modified: 2026-11-19T03:08:40.206Z
---

HackerOne Hacker API uses this user's own account: creds in `~/.config/hackerone/creds` (H1_USER/H1_TOKEN, chmod 600), entered at install. Check with `h1 me`. To rotate the token, only the creds file needs updating. H1_USER also tags all research traffic (the program's required header or `X-Bug-Bounty: HackerOne-<H1_USER>`).

Toolkit `~/h1/` (built 2026-11-17, README.md inside):
- `h1.py` → `/usr/local/bin/h1`: sync, top, show, new, targets, weaknesses, report new/list/submit, me, timer, widget.
- `h1lib.py`: API client, SQLite DB `data/h1.db` (programs, scopes, payouts, changes, scores), scoring, target export.
- `h1_widget.py`: "Bounty Radar" Tk window in the FoxOS palette. Launch with the desktop icon, the app-menu entry (`~/.local/share/applications/bounty-radar.desktop`) or `h1 widget`. It live-reloads after each sync.
- systemd user timer `h1-sync.timer` (OnCalendar :00/:30, Persistent, linger on) syncs on its own and runs notify-send when new programs or assets appear. The widget shows "next auto-sync HH:MM" in its status line and only syncs itself if the timer is off.
- `report submit` posts as the H1_USER account. It always shows a preview and needs typed `submit`. Never submit without the user's explicit OK.

- `recon.py` / `h1 recon` (added 2026-11-18): daily `h1-recon.timer` 01:43 on the top 10. Passive sources plus a LIGHT probe (1 GET / per host, ≤2 rps, policy header, skipped when the policy bans automation). Wildcard-DNS catch-all filtering, diff vs the previous run → change feed "⌖" plus a Radar "Recon" button. The user OK'd active work "as long as it's low impact". Heavier scanning (nuclei) stays in `h1 hunt`, whose timer is NOT enabled.

API quirk: the hacktivity `next` link stops after page 2. Walk `page[number]` explicitly at size 10 (already done in `_hacktivity_pages`).
Screenshot the widget with `import -window $(xdotool search --name 'Bounty Radar')`, because a root-window capture comes out black. Don't `pkill -f h1_widget.py` loosely, because the pattern also matches and kills the calling shell.
Related: [[websec-framework]], [[foxos-rice]], [[kali-tool-catalog]].
