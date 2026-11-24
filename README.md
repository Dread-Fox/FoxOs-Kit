# foxos-kit — DreadFox AI toolkit, portable

Sets up an **independent** DreadFox on Kali (x86_64 or arm64), and best-effort on other major Linux distros. It gets its own HackerOne database, timers,
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

**Dens:** in FoxWorx's **Dens** tab, connect your DreadFox with friends' installs: encrypted group chat, shared notes and
findings, file sharing and light agent-to-agent help, peer-to-peer over Tor with no server. See
`FoxWorx-DreadFoxHacker/docs/DENS.md`.

## Supported systems

| System | Status |
|---|---|
| **Kali** (x86-64, ARM64) | Tested. Everything, including the FoxOS look. |
| **Debian, Ubuntu, Mint, Parrot, Pop!_OS** (apt) | Best-effort, untested. |
| **Fedora, RHEL, Rocky, Alma** (dnf) | Best-effort, untested. |
| **Arch, Manjaro, EndeavourOS** (pacman) | Best-effort, untested. |
| **openSUSE** (zypper) | Best-effort, untested. |

Needs a 64-bit CPU, a desktop login with systemd and Python 3.11+. On anything that isn't Kali the FoxOS look
(system branding, login screen, boot splash, GRUB) is skipped unless you pass `--rice`.

### Security tools on any distro: `foxos-tools`
By default the installer makes sure a **security toolset** is present. It first looks for one it recognises and,
if found, installs nothing:

| Recognised toolset | Detected by | Weekly update |
|---|---|---|
| Kali (`kali-linux-headless/default/large/everything`) | installed metapackage | re-applies the metapackage (new tools) |
| Kali tool categories (`kali-tools-web`, `-passwords`, … any of the 27) | installed category sets, with installed/listed counts | re-applies every installed category set (new tools) |
| Parrot tool categories (`parrot-tools-*`) | installed category sets | re-applies them |
| Parrot Security (`parrot-tools-full`, `parrot-tools*`) | installed metapackage | re-applies the metapackage (new tools) |
| BlackArch (also Athena OS) | `blackarch` repo + 20+ tools from its group | `pacman -Syu` |
| Fedora Security Lab | `security-lab` group installed | `dnf group upgrade` |
| BackBox, Pentoo, REMnux, Exegol | their packages / markers / command | reminder with the toolset's own update command |
| Any hand-built collection | 60%+ of a list of ~74 widely used tools | reminder |

Only if none is found does it install `kali-linux-headless`, Kali's full command-line toolset (~12 GB). On Kali it's installed with apt; on any other distro it lives in an official
Kali container (distrobox + podman) and every tool it adds that your system lacks becomes a normal command in
`~/.local/bin`. Your system's own packages are never replaced. A weekly update re-applies the toolset, so **new
tools Kali adds show up automatically**, and FoxWorx's agent discovers them on its own.
```bash
foxos-tools inventory                  # everything installed: toolsets + categories, tool commands, tool folders
                                       # outside the package manager, /opt and ~/tools projects, what FoxWorx sees
foxos-tools expose                     # let FoxWorx's agent use tools in ~/go/bin, ~/.cargo/bin, /snap/bin, pdtm…
foxos-tools check                      # the DreadFox tools: present or missing
foxos-tools toolkit                    # detect a toolset; install Kali's headless set only if none
foxos-tools toolkit --set default      # make sure a specific Kali set is present: headless, default, large, everything
foxos-tools update                     # pull in new Kali tools now (the weekly timer does this for you)
foxos-tools install --all              # individual tools: distro packages → verified releases → pipx
foxos-tools timer --off                # stop the weekly update
```
Install flags: `--toolkit auto` (default: detect first), `--toolkit headless|default|large|everything` to make sure a Kali set is present, `--toolkit none` to skip. On a Kali host
the weekly job can't type your sudo password, so it reminds you to run `foxos-tools update` instead; in the
container it updates unattended. Release binaries are checked against the release's SHA-256 checksums.
Tools that need raw packet or Wi-Fi access may still need host privileges when they run from the container.

## What's inside
| path | installs to | what |
|---|---|---|
| `h1/` | `~/h1`, `/usr/local/bin/h1` | HackerOne intel: sync, ranking, Bounty Radar widget (with live sync progress bar), `recon` (passive + light probe), `hunt` (deeper, with Claude triage), report drafting/submission |
| `h1/dreadfox_widget.py`, `h1/fox_art.py`, `h1/assets/` | `~/h1` | **DreadFox Companion**: desktop kitsune in the Bounty Radar crimson/steel style, drawn from the DreadFox redesign (9 stages, 8 moods), live mood from FoxWorx, click-to-boop, TALK window (Fox / Chat / Copilot / Rabbit Hole). Self-test: `python3 -I ~/h1/dreadfox_widget.py --shot 9 out.png` |
| `desktop/` | `~/.local/share/applications`, `~/Desktop` | launchers: Bounty Radar, DreadFox Companion, FoxWorx |
| `bin/` | `~/.local/bin` | `foxos-setup` (guided setup), `foxos-tools` (security tools on any distro), `foxworx-launch` (starts FoxWorx and opens it), `foxworx-api` (helper the setup uses); `foxos-pkg.sh` (distro helpers) |
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
