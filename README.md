# foxos-kit — DreadFox AI toolkit, portable

Sets up an **independent** DreadFox on any Kali box (x86_64 or arm64). It gets its own HackerOne database, timers,
recon history and Claude memory. Nothing is shared with, or synced back to, the machine the kit came from.

## Install (on the new Kali box, as your normal user, in a desktop terminal)
```bash
git clone https://github.com/Dread-Fox/foxos-kit.git ~/foxos-kit
cd ~/foxos-kit && ./install.sh            # --no-rice / --no-timers / --no-rc to skip parts
```
It asks for your HackerOne API username and token (stored at `~/.config/hackerone/creds`, chmod 600). Leave them blank
to add later. Then:
```bash
claude                                          # log in once
systemctl --user enable --now claude-rc.service # Remote Control (start sessions from the Claude app)
h1 top · h1 recon --plan · h1 widget
```

## What's inside
| path | installs to | what |
|---|---|---|
| `h1/` | `~/h1`, `/usr/local/bin/h1` | HackerOne intel: sync, ranking, Bounty Radar widget, `recon` (passive + light probe), `hunt` (deeper, with Claude triage), report drafting/submission |
| `websec/` | `~/websec`, `websec` | headless authz-testing framework (profiles/loot start empty) |
| `toolkit/` | `~/toolkit`, `toolkit` | rolling Kali CLI tool catalog |
| `claude/skills/` | `~/.claude/skills` | 14 `kali-*` pentest workflow skills |
| `claude/memory-seed/` | `~/.claude/projects/<home>/memory` | starter memory notes (only if none exist yet) |
| `systemd/claude-rc.service` | `~/.config/systemd/user` | Claude Remote Control service |
| `foxos/` | `~/foxos`, `/usr/share/...`, `/etc/...` | FoxOS look: wallpaper, login, Plymouth splash (GRUB `quiet splash` on x86), hex-badge menu icon, terminal theme and banner |

Timers on the new box: H1 sync every 30 min, recon daily at **04:13** (the Pi runs at 01:43, so the two don't overlap).
Revert the look: `~/foxos/revert-foxos.sh` (restores this machine's own originals from `~/foxos/backups`).
Change the tagline: edit `LINE1`/`LINE2` in `~/foxos/build-tagline.py`, run it, then re-run `foxos/apply-foxos.sh`.

## Never in this repo
API tokens, `~/.config/hackerone/creds`, FoxWorx `settings.env`, Claude login, `h1.db`, recon/hunt output, reports,
websec engagement profiles/loot, or client-engagement memory notes. FoxWorx Workbench has its own repo
(`Dread-Fox/FoxWorx-DreadFoxHacker`).
