# foxos-kit — DreadFox AI toolkit, portable

Sets up an **independent** DreadFox on any Kali box (x86_64 or arm64). It gets its own HackerOne database, timers,
recon history and Claude memory. Nothing is shared with, or synced back to, the machine the kit came from.

## Install (on the new Kali box, as your normal user, in a desktop terminal)
```bash
git clone https://github.com/Dread-Fox/FoxOs-Kit.git ~/foxos-kit
cd ~/foxos-kit && ./install.sh            # --no-rice / --no-timers / --no-rc / --no-foxworx to skip parts
```
Run it from a terminal inside your Kali desktop session, as your normal user (it asks for sudo when needed).
It installs everything, then offers the **guided setup** (`foxos-setup`), which asks before each step:

1. FoxWorx running at http://127.0.0.1:8765 with **no login** (local-only: it listens on this machine and refuses tunnels/proxies)
2. LM Studio download and install
3. Zenko models (main ≈ 21 GB, everything ≈ 49 GB), plus GPU settings worked out from your cards
4. Load `zenko-main` and connect FoxWorx's copilot, Rabbit Hole and pet chat to it
5. Hatch and name your DreadFox
6. HackerOne API username/token and sync timers, Claude Code login and Remote Control

Skip anything and come back later with `foxos-setup`; `foxos-setup --check` shows what's done.
Then try: `h1 top · h1 recon --plan · h1 widget`, and the FoxWorx / DreadFox Companion / Bounty Radar desktop icons.

## What's inside
| path | installs to | what |
|---|---|---|
| `h1/` | `~/h1`, `/usr/local/bin/h1` | HackerOne intel: sync, ranking, Bounty Radar widget (with live sync progress bar), `recon` (passive + light probe), `hunt` (deeper, with Claude triage), report drafting/submission |
| `h1/dreadfox_widget.py`, `h1/fox_art.py`, `h1/assets/` | `~/h1` | **DreadFox Companion**: desktop kitsune in the Bounty Radar crimson/steel style, drawn from the DreadFox redesign (9 stages, 8 moods), live mood from FoxWorx, click-to-boop, TALK window (Fox / Chat / Copilot / Rabbit Hole). Self-test: `python3 -I ~/h1/dreadfox_widget.py --shot 9 out.png` |
| `desktop/` | `~/.local/share/applications`, `~/Desktop` | launchers: Bounty Radar, DreadFox Companion, FoxWorx |
| `bin/` | `~/.local/bin` | `foxos-setup` (guided setup), `foxworx-launch` (starts FoxWorx and opens it), `foxworx-api` (helper the setup uses) |
| `zenko/` | `~/zenko-scripts` | Zenko local-model kit: `fetch_models.sh [main\|all]` (resumable, size-verified; abliterated Qwen3.6-35B-A3B, Qwen3.8-27B, gpt-oss-20b, nomic-embed), `load_zenko.sh` (reads `~/.config/zenko/zenko.env`), `talk.py` |
| `FoxWorx-DreadFoxHacker/` | `~/FoxWorx-DreadFoxHacker`, `/etc/foxworx/adapter-policy.json` | the full FoxWorx workbench source (bundled, with its history) — the workbench (v3.5.0+: redesigned sanctuary, Zenko memory, companion export), its Rabbit Hole admin policy (installed CLI + all built-in workflows, only if none exists) the companion link in `settings.env`, and no-login local mode |
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
FoxWorx Workbench is bundled in `FoxWorx-DreadFoxHacker/` (a git subtree of `Dread-Fox/FoxWorx-DreadFoxHacker`).

## Updating the bundled FoxWorx
Develop FoxWorx in its own checkout, then pull it into the kit:
```bash
cd ~/foxos-kit
git subtree pull --prefix=FoxWorx-DreadFoxHacker https://github.com/Dread-Fox/FoxWorx-DreadFoxHacker.git main
```
Changes made directly in the kit's copy can be sent back with `git subtree push --prefix=FoxWorx-DreadFoxHacker <repo> <branch>`.
