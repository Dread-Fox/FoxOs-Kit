# foxos-kit — DreadFox AI toolkit, portable

Sets up an **independent** DreadFox on any Kali box (x86_64 or arm64). It gets its own HackerOne database, timers,
recon history and Claude memory. Nothing is shared with, or synced back to, the machine the kit came from.

## Install (on the new Kali box, as your normal user, in a desktop terminal)
```bash
git clone https://github.com/Dread-Fox/foxos-kit.git ~/foxos-kit
cd ~/foxos-kit && ./install.sh            # --no-rice / --no-timers / --no-rc / --no-foxworx to skip parts
```
It asks for your HackerOne API username and token (stored at `~/.config/hackerone/creds`, chmod 600). Leave them blank
to add later. Then:
```bash
claude                                          # log in once
systemctl --user enable --now claude-rc.service # Remote Control (start sessions from the Claude app)
h1 top · h1 recon --plan · h1 widget
```
The FoxWorx installer prints a one-time setup token; save it to sign in at http://127.0.0.1:8765.
For local abliterated models (Zenko), follow `~/zenko-scripts/README.md` (LM Studio 0.4.x + models + `load_zenko.sh`).

## What's inside
| path | installs to | what |
|---|---|---|
| `h1/` | `~/h1`, `/usr/local/bin/h1` | HackerOne intel: sync, ranking, Bounty Radar widget (with live sync progress bar), `recon` (passive + light probe), `hunt` (deeper, with Claude triage), report drafting/submission |
| `h1/dreadfox_widget.py`, `h1/fox_art.py`, `h1/assets/` | `~/h1` | **DreadFox Companion**: desktop kitsune in the Bounty Radar crimson/steel style, drawn from the DreadFox redesign (9 stages, 8 moods), live mood from FoxWorx, click-to-boop, TALK window (Fox / Chat / Copilot / Rabbit Hole). Self-test: `python3 -I ~/h1/dreadfox_widget.py --shot 9 out.png` |
| `desktop/` | `~/.local/share/applications`, `~/Desktop` | launchers: Bounty Radar, DreadFox Companion, FoxWorx |
| `bin/foxworx-launch` | `~/.local/bin` | starts the FoxWorx workbench if needed and opens it in the browser |
| `zenko/` | `~/zenko-scripts` | Zenko local-model kit: LM Studio model downloads (abliterated Qwen3.6-35B-A3B, Qwen3.8-27B, gpt-oss-20b, nomic-embed), `load_zenko.sh`, `talk.py` |
| FoxWorx (cloned) | `~/FoxWorx-DreadFoxHacker`, `/etc/foxworx/adapter-policy.json` | the workbench (v3.5.0+: redesigned sanctuary, Zenko memory, companion export), its Rabbit Hole admin policy (installed CLI + all built-in workflows, only if none exists) and the companion link in `settings.env` |
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
websec engagement profiles/loot, or client-engagement memory notes, the pet state (`~/h1/data/pet.json`), or model weights.
FoxWorx Workbench has its own repo (`Dread-Fox/FoxWorx-DreadFoxHacker`); the installer clones it.
