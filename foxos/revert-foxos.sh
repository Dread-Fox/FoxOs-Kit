#!/usr/bin/env bash
# Revert the FoxOS look to this machine's own originals (saved by apply-foxos.sh in ~/foxos/backups).
set -uo pipefail
B="$HOME/foxos/backups"
r() { [ -f "$B/$1.orig" ] && sudo cp "$B/$1.orig" "$2" && echo "  restored $2"; }
echo "[*] branding + login + grub"
r issue /etc/issue; r issue.net /etc/issue.net; r motd /etc/motd; r os-release /etc/os-release
r lightdm-gtk-greeter.conf /etc/lightdm/lightdm-gtk-greeter.conf
r grub /etc/default/grub && sudo update-grub >/dev/null 2>&1
echo "[*] terminal + zsh banner"
[ -f "$B/terminalrc.orig" ] && cp "$B/terminalrc.orig" "$HOME/.config/xfce4/terminal/terminalrc" || rm -f "$HOME/.config/xfce4/terminal/terminalrc"
sed -i '/# >>> FoxOS banner >>>/,/# <<< FoxOS banner <<</d' "$HOME/.zshrc"
echo "[*] wallpaper + menu icon"
export DISPLAY="${DISPLAY:-:0}"
for p in $(xfconf-query -c xfce4-desktop -l 2>/dev/null | grep last-image); do
  xfconf-query -c xfce4-desktop -p "$p" -s /usr/share/backgrounds/kali-16x9/default 2>/dev/null || true; done
for p in $(xfconf-query -c xfce4-panel -l 2>/dev/null | grep -E '/button-icon$'); do xfconf-query -c xfce4-panel -p "$p" -r; done
echo "[*] plymouth → kali"
sudo plymouth-set-default-theme kali 2>/dev/null && sudo update-initramfs -u >/dev/null
echo "[✓] reverted. Log out/in (and reboot for the boot splash)."
