---
name: foxos-rice
description: "FoxOS rebrand/rice of this Kali Xfce box — crimson+steel theme, assets, how to revert"
metadata:
  node_type: memory
  type: project
  originSessionId: 92e46c4e-b5a5-538d-91a5-cf8bfe5873cc
  modified: 2026-11-19T00:59:20.174Z
---

The user runs this Kali box headlessly via Claude Code and is "ricing" its GUI login experience into **FoxOS** (DreadFox edition, crimson + steel palette). Applied 2026-11-13.

Identity: hex-badge logo (user's own SVG design) — steel bezel, crimson `#E11D2A` ring + ears, `#FF2E4D` glowing eyes, two-tone faceted wolf-fox head. Source: `@HOME@/foxos/assets/foxos-badge.svg`. Wordmark "FoxOS" + tagline "DREADFOX · BUILT ON KALI".

Workspace `@HOME@/foxos/`: `assets/` (svg+png), `wallpaper/` (wallpaper/login/boot/terminal mockups), `plymouth/`, `backups/` (ALL originals), `revert-foxos.sh` (one-command restore to stock Kali).

Applied surfaces (all 5):
1. Wallpaper — xfconf backdrop → /usr/share/backgrounds/foxos/foxos-wallpaper.png (live).
2. Terminal — ~/.config/xfce4/terminal/terminalrc crimson palette (FiraCode 11) + FoxOS figlet banner appended to ~/.zshrc (guarded interactive).
3. Branding — /etc/os-release (PRETTY_NAME/NAME=FoxOS, kept ID=kali for tooling, LOGO=foxos), /etc/issue + issue.net (figlet), /etc/motd.
4. Login — LightDM greeter: bg=foxos-login.png, theme Kali-Red-Dark, icons Flat-Remix-Red-Dark, default-user-image=fox badge.
5. Boot splash — custom Plymouth theme `/usr/share/plymouth/themes/foxos/` (badge+wordmark+crimson progress bar), set default, initramfs rebuilt (both Pi kernels).

2026-11-18 tagline refresh: user does NOT want "Kali" referenced in any cosmetics; "DreadFox · built on Kali" replaced by "BY FOX WROX" + "FIND THE EDGE ··· SHARPEN IT!" on wallpaper/login/plymouth wordmark/banners (os-release PRETTY_NAME too; ID=kali + URLs kept for tooling). Keep the fox badge design exactly — user only wants text changes, never a redesign. Regenerate via `~/foxos/build-tagline.py`; v1 copies in `~/foxos/backups/v1-tagline/`. Whiskermenu button-icon = /usr/share/foxos/badge-48.png. Login bg no longer has the baked-in fake login form.

OPEN: Plymouth splash won't display at boot until `quiet splash` is added to /boot/firmware/cmdline.txt (boot-critical file, not yet edited — needs user OK). Assets are aarch64 Pi. Could not screenshot live result — physical display was powered off during the session. User wants to snapshot a "final OS version" when satisfied. See [[kali-tool-catalog]].
