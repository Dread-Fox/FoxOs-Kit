#!/usr/bin/env bash
# FoxOS kit installer — sets up an independent DreadFox on a fresh Kali box.
#   ./install.sh                 everything (tools, h1/recon, websec, toolkit, Claude skills+memory seed,
#                                timers, Remote Control service, FoxOS look)
#   ./install.sh --no-rice       skip the FoxOS cosmetics
#   ./install.sh --no-timers     don't enable the H1 sync/recon timers
#   ./install.sh --no-rc         don't install the Claude Remote Control service
# Run as your normal user (not root) from a terminal inside the desktop session. Uses sudo when needed.
# Safe to re-run: existing creds, memory and backups are never overwritten.
set -euo pipefail
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RICE=1 TIMERS=1 RC=1
for a in "$@"; do case "$a" in
  --no-rice) RICE=0 ;; --no-timers) TIMERS=0 ;; --no-rc) RC=0 ;;
  -h|--help) sed -n 2,10p "$0"; exit 0 ;; *) echo "unknown option $a"; exit 1 ;; esac; done
[ "$(id -u)" -eq 0 ] && { echo "run as your normal user, not root"; exit 1; }
say() { printf '\033[38;5;160m[foxos]\033[0m %s\n' "$*"; }
tmpl() { sed -e "s#@HOME@#$HOME#g" -e "s#@UID@#$(id -u)#g" "$1"; }
grep -qi kali /etc/os-release || say "warning: this doesn't look like Kali — continuing anyway"
say "machine: $(uname -m) · user: $USER · home: $HOME"

# ------------------------------------------------------------------ packages
say "installing packages (apt)…"
sudo apt-get update -qq
PKGS="python3 python3-tk python3-yaml python3-pil git curl jq whois dnsutils libnotify-bin xdotool xdg-utils
      subfinder dnsx httpx-toolkit nuclei katana amass theharvester figlet imagemagick fonts-dejavu fonts-firacode"
[ $RICE = 1 ] && PKGS="$PKGS plymouth plymouth-themes"
avail=""; for p in $PKGS; do apt-cache show "$p" >/dev/null 2>&1 && avail="$avail $p" || say "  (not in apt: $p — skipped)"; done
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $avail

# ------------------------------------------------------------------ Claude Code
if ! command -v claude >/dev/null && [ ! -x "$HOME/.local/bin/claude" ]; then
  say "installing Claude Code (official installer)…"
  curl -fsSL https://claude.ai/install.sh | bash
fi
export PATH="$HOME/.local/bin:$PATH"

# ------------------------------------------------------------------ code
say "installing h1 / websec / toolkit into $HOME…"
for d in h1 websec toolkit; do
  mkdir -p "$HOME/$d"
  for f in "$KIT/$d"/*; do tmpl "$f" > "$HOME/$d/$(basename "$f")"; done
done
chmod +x "$HOME/h1/h1.py" "$HOME/websec/websec.py" "$HOME/toolkit/refresh.py"
mkdir -p "$HOME/websec/profiles" "$HOME/websec/loot" && chmod 700 "$HOME/websec/profiles"
sudo ln -sf "$HOME/h1/h1.py" /usr/local/bin/h1
sudo ln -sf "$HOME/websec/websec.py" /usr/local/bin/websec
sudo ln -sf "$HOME/toolkit/refresh.py" /usr/local/bin/toolkit

# HackerOne API creds — prompted, never shipped
C="$HOME/.config/hackerone/creds"
if [ ! -s "$C" ]; then
  mkdir -p "$(dirname "$C")"
  read -rp "HackerOne API username [dread_fox] (blank = skip for now): " U; U="${U:-}"
  if [ -n "$U" ]; then
    read -rsp "HackerOne API token (hidden): " T; echo
    umask 077; printf 'H1_USER=%s\nH1_TOKEN=%s\n' "$U" "$T" > "$C"; chmod 600 "$C"
  else say "  skipped — add $C later (H1_USER=… / H1_TOKEN=…)"; fi
fi

# desktop entry
mkdir -p "$HOME/.local/share/applications" "$HOME/Desktop"
tmpl "$KIT/desktop/bounty-radar.desktop" > "$HOME/.local/share/applications/bounty-radar.desktop"
cp "$HOME/.local/share/applications/bounty-radar.desktop" "$HOME/Desktop/" && chmod +x "$HOME/Desktop/bounty-radar.desktop"

# ------------------------------------------------------------------ Claude skills + memory seed
say "installing Claude skills + memory seed…"
mkdir -p "$HOME/.claude/skills"
cp -rn "$KIT/claude/skills/"* "$HOME/.claude/skills/"
SLUG="$(printf '%s' "$HOME" | sed 's#/#-#g')"
MEM="$HOME/.claude/projects/$SLUG/memory"
if [ ! -f "$MEM/MEMORY.md" ]; then
  mkdir -p "$MEM"
  for f in "$KIT/claude/memory-seed/"*.md; do tmpl "$f" > "$MEM/$(basename "$f")"; done
  cat > "$MEM/this-machine.md" <<EOF
---
name: this-machine
description: "Which DreadFox this is — independent from the Raspberry Pi DreadFox; seeded from its kit $(date +%F)"
metadata:
  type: project
---
This box ($(hostname), $(uname -m)) is an INDEPENDENT DreadFox, set up from foxos-kit on $(date +%F).
It has its own HackerOne DB, timers, recon history and memory; the Raspberry Pi DreadFox is separate and
keeps its own. Seeded notes describe the Pi's original setup — verify paths/state here before relying on them.
Related: [[foxos-rice]], [[hackerone-api]].
EOF
  { echo "- [This machine](this-machine.md) — independent DreadFox on $(hostname), seeded $(date +%F)"
    echo "- [FoxOS rice](foxos-rice.md) — crimson+steel FoxOS look; revert via ~/foxos/revert-foxos.sh"
    echo "- [websec framework](websec-framework.md) — headless authz testing toolkit at ~/websec"
    echo "- [Kali tool catalog](kali-tool-catalog.md) — rolling CLI tool catalog, run \`toolkit\` to refresh"
    echo "- [HackerOne toolkit](hackerone-api.md) — \`h1\` CLI, Bounty Radar, sync + recon timers at ~/h1"; } > "$MEM/MEMORY.md"
else say "  memory already exists at $MEM — left untouched"; fi

# ------------------------------------------------------------------ timers (this box's own schedule)
sudo loginctl enable-linger "$USER" || true
if [ $TIMERS = 1 ]; then
  if [ -s "$C" ]; then
    say "enabling H1 sync (every 30 min) + daily recon (04:13)…"
    h1 timer --every 30 >/dev/null
    h1 recon --timer -n 10 --at 04:13 >/dev/null
    say "  first sync running in background (builds this box's own baseline)…"
    systemctl --user start --no-block h1-sync.service || true
  else say "  no H1 creds yet — run later:  h1 timer --every 30 && h1 recon --timer --at 04:13"; fi
fi

# ------------------------------------------------------------------ Remote Control
if [ $RC = 1 ]; then
  mkdir -p "$HOME/Zenko_Agent" "$HOME/.config/systemd/user"
  tmpl "$KIT/systemd/claude-rc.service" > "$HOME/.config/systemd/user/claude-rc.service"
  systemctl --user daemon-reload
  if [ -f "$HOME/.claude/.credentials.json" ]; then
    systemctl --user enable --now claude-rc.service && say "Remote Control service running"
  else say "Remote Control installed but not started: run \`claude\` once to log in, then
         systemctl --user enable --now claude-rc.service"; fi
fi

# ------------------------------------------------------------------ FoxOS look
[ $RICE = 1 ] && bash "$KIT/foxos/apply-foxos.sh" "$KIT"

say "toolkit catalog…"; toolkit >/dev/null 2>&1 || true
say "done. Open a new terminal. Try:  h1 top · h1 recon --plan · h1 widget"
