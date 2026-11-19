#!/usr/bin/env bash
# Apply the FoxOS look (by Fox Wrox). Backs up THIS machine's originals to ~/foxos/backups first.
# Boot splash: GRUB on x86 (adds "quiet splash"), cmdline.txt left alone on a Raspberry Pi.
set -euo pipefail
KIT="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
F="$HOME/foxos"; B="$F/backups"
say() { printf '\033[38;5;160m[foxos]\033[0m %s\n' "$*"; }
mkdir -p "$B"
bk() { [ -e "$1" ] && [ ! -e "$B/$(basename "$1").orig" ] && sudo cp "$1" "$B/$(basename "$1").orig" && sudo chown "$USER" "$B/$(basename "$1").orig" || true; }

say "copying FoxOS assets → $F"
cp -r "$KIT/foxos/assets" "$KIT/foxos/wallpaper" "$KIT/foxos/plymouth" "$KIT/foxos/src" "$F/"
cp "$KIT/foxos/build-tagline.py" "$KIT/foxos/revert-foxos.sh" "$F/" && chmod +x "$F/revert-foxos.sh"

say "system images (wallpaper, login, badges)"
sudo install -d /usr/share/backgrounds/foxos /usr/share/foxos
sudo install -m644 "$KIT/foxos/wallpaper/"foxos-{wallpaper,login,boot}.png /usr/share/backgrounds/foxos/
sudo install -m644 "$KIT/foxos/badges/"* /usr/share/foxos/

say "branding text (/etc/issue, issue.net, motd, os-release)"
for f in /etc/issue /etc/issue.net /etc/motd /etc/os-release; do bk "$f"; done
sudo install -m644 "$KIT/etc/issue" /etc/issue
sudo install -m644 "$KIT/etc/issue.net" /etc/issue.net
sudo install -m644 "$KIT/etc/motd" /etc/motd
# keep ID=kali + URLs (apt/tooling depend on them); change only what's displayed
sudo sed -i -e 's/^PRETTY_NAME=.*/PRETTY_NAME="FoxOS · by Fox Wrox"/' -e 's/^NAME=.*/NAME="FoxOS"/' /etc/os-release
grep -q '^LOGO=' /etc/os-release && sudo sed -i 's/^LOGO=.*/LOGO=foxos/' /etc/os-release || echo 'LOGO=foxos' | sudo tee -a /etc/os-release >/dev/null
grep -q '^FOXOS_EDITION=' /etc/os-release || echo 'FOXOS_EDITION="Fox Wrox"' | sudo tee -a /etc/os-release >/dev/null

say "login screen (LightDM GTK greeter)"
G=/etc/lightdm/lightdm-gtk-greeter.conf
if [ -f "$G" ]; then
  bk "$G"
  setkey() { if grep -q "^\s*$1\s*=" "$G"; then sudo sed -i "s#^\s*$1\s*=.*#$1 = $2#" "$G";
             else sudo sed -i "/^\[greeter\]/a $1 = $2" "$G"; fi; }
  grep -q '^\[greeter\]' "$G" || echo '[greeter]' | sudo tee -a "$G" >/dev/null
  setkey background /usr/share/backgrounds/foxos/foxos-login.png
  setkey default-user-image /usr/share/foxos/badge-128.png
  [ -d /usr/share/themes/Kali-Red-Dark ] && setkey theme-name Kali-Red-Dark
  [ -d /usr/share/icons/Flat-Remix-Red-Dark ] && setkey icon-theme-name Flat-Remix-Red-Dark
else say "  no LightDM GTK greeter config — skipped"; fi

say "terminal theme + FoxOS banner"
mkdir -p "$HOME/.config/xfce4/terminal"
[ -f "$HOME/.config/xfce4/terminal/terminalrc" ] && cp -n "$HOME/.config/xfce4/terminal/terminalrc" "$B/terminalrc.orig"
cp "$KIT/etc/terminalrc" "$HOME/.config/xfce4/terminal/terminalrc"
[ -f "$HOME/.zshrc" ] && cp -n "$HOME/.zshrc" "$B/zshrc.orig"
if ! grep -q '>>> FoxOS banner >>>' "$HOME/.zshrc" 2>/dev/null; then cat >> "$HOME/.zshrc" <<'EOF'

# >>> FoxOS banner >>>
if [[ -o interactive ]]; then
  if command -v figlet >/dev/null 2>&1; then
    print -P "%F{red}"
    figlet -f slant "FoxOS" 2>/dev/null
    print -P "%f%F{red}  by Fox Wrox  —  Find the Edge … Sharpen it!%f\n"
  fi
fi
# <<< FoxOS banner <<<
EOF
fi

say "desktop wallpaper + Applications-menu icon (needs the Xfce session)"
cat > "$F/apply-desktop.sh" <<'EOF'
#!/usr/bin/env bash
# Per-session bits: wallpaper on every monitor/workspace + hex badge on the Whisker menu button.
export DISPLAY="${DISPLAY:-:0}"
command -v xfconf-query >/dev/null || exit 0
mkdir -p "$HOME/foxos/backups"
xfconf-query -c xfce4-desktop -lv > "$HOME/foxos/backups/xfconf-xfce4-desktop.orig" 2>/dev/null || true
props=$(xfconf-query -c xfce4-desktop -l 2>/dev/null | grep last-image)
if [ -z "$props" ]; then
  for m in $(xrandr --listmonitors 2>/dev/null | awk 'NR>1{print $NF}'); do
    props="$props /backdrop/screen0/monitor$m/workspace0/last-image"; done
fi
for p in $props; do xfconf-query -c xfce4-desktop -p "$p" -n -t string -s /usr/share/backgrounds/foxos/foxos-wallpaper.png; done
for p in $(xfconf-query -c xfce4-panel -l 2>/dev/null | grep -E '^/plugins/plugin-[0-9]+$'); do
  [ "$(xfconf-query -c xfce4-panel -p "$p")" = whiskermenu ] && \
    xfconf-query -c xfce4-panel -p "$p/button-icon" -n -t string -s /usr/share/foxos/badge-48.png
done
xfdesktop --reload >/dev/null 2>&1 || true
rm -f "$HOME/.config/autostart/foxos-apply-desktop.desktop"
EOF
chmod +x "$F/apply-desktop.sh"
if [ -n "${DISPLAY:-}" ] && xfconf-query -c xfce4-desktop -l >/dev/null 2>&1; then "$F/apply-desktop.sh"
else  # not in a desktop session: run once at next login
  mkdir -p "$HOME/.config/autostart"
  printf '[Desktop Entry]\nType=Application\nName=FoxOS desktop apply\nExec=%s\nX-GNOME-Autostart-enabled=true\n' \
    "$F/apply-desktop.sh" > "$HOME/.config/autostart/foxos-apply-desktop.desktop"
  say "  will apply at next desktop login"
fi

say "boot splash (Plymouth)"
if command -v plymouth-set-default-theme >/dev/null; then
  sudo install -d /usr/share/plymouth/themes/foxos
  sudo install -m644 "$KIT/foxos/plymouth/"* /usr/share/plymouth/themes/foxos/
  sudo plymouth-set-default-theme foxos
  if [ -f /etc/default/grub ]; then
    bk /etc/default/grub
    if ! grep -q '^GRUB_CMDLINE_LINUX_DEFAULT=.*splash' /etc/default/grub; then
      sudo sed -i -E '/^GRUB_CMDLINE_LINUX_DEFAULT=/{s/\bquiet\b//g; s/^(GRUB_CMDLINE_LINUX_DEFAULT=")([^"]*)"/\1\2 quiet splash"/; s/=" +/="/; s/  +/ /g}' /etc/default/grub
    fi
    grep -q '^GRUB_CMDLINE_LINUX_DEFAULT=' /etc/default/grub || echo 'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"' | sudo tee -a /etc/default/grub >/dev/null
    sudo update-grub >/dev/null 2>&1 || true
    lspci 2>/dev/null | grep -qi nvidia && say "  NVIDIA GPU: if the splash doesn't show, add nvidia-drm.modeset=1 to GRUB_CMDLINE_LINUX_DEFAULT"
  else say "  no GRUB (Pi?) — add 'quiet splash' to /boot/firmware/cmdline.txt yourself if wanted"; fi
  sudo update-initramfs -u >/dev/null
else say "  plymouth not installed — skipped"; fi
say "FoxOS look applied. Log out/in for the login screen; reboot for the boot splash."
