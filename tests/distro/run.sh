#!/usr/bin/env bash
# Distro test: run the real FoxOS kit installer inside a clean container, then check the result.
#   tests/distro/run.sh IMAGE [IMAGE...]      e.g. ubuntu:22.04 fedora:41 archlinux:latest
# Uses podman (or docker). The installer runs as a normal user with passwordless sudo, like a desktop
# user; it skips the FoxOS look, timers, Remote Control, the big standard toolset and the Claude download.
set -uo pipefail
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENGINE=$(command -v podman || command -v docker) || { echo "need podman or docker"; exit 1; }
LOGS="${FOXOS_TEST_LOGS:-$KIT/tests/distro/logs}"; mkdir -p "$LOGS"

bootstrap='set -e
if command -v apt-get >/dev/null; then export DEBIAN_FRONTEND=noninteractive; apt-get update -qq; apt-get install -y -qq sudo bash curl ca-certificates >/dev/null
elif command -v dnf >/dev/null; then dnf install -y -q sudo bash curl which findutils util-linux shadow-utils >/dev/null
elif command -v pacman >/dev/null; then pacman -Sy --noconfirm --needed sudo bash curl >/dev/null
elif command -v zypper >/dev/null; then zypper -q -n install sudo bash curl >/dev/null
elif command -v apk >/dev/null; then apk add -q sudo bash curl shadow su-exec util-linux-login
elif command -v xbps-install >/dev/null; then xbps-install -Sy sudo bash curl >/dev/null
fi
id fox >/dev/null 2>&1 || useradd -m -s /bin/bash fox
echo "fox ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/fox; chmod 440 /etc/sudoers.d/fox
cp -r /kit /home/fox/foxos-kit; chown -R fox /home/fox/foxos-kit
su - fox -c "cd ~/foxos-kit && FOXOS_NO_CLAUDE=1 ./install.sh --no-rice --no-timers --no-rc --toolkit none < /dev/null" ; echo "INSTALL_EXIT=$?"
su - fox -c "bash ~/foxos-kit/tests/distro/verify.sh"'

status=0
for img in "$@"; do
  tag=$(echo "$img" | tr '/:' '__')
  echo "=== $img"
  start=$(date +%s)
  timeout 2400 "$ENGINE" run --rm -v "$KIT:/kit:ro,Z" "$img" sh -c "$bootstrap" > "$LOGS/$tag.log" 2>&1
  echo "    $(grep -E '^(PASS|FAIL)' "$LOGS/$tag.log" | tr '\n' ' ')($(( $(date +%s) - start ))s, log: $LOGS/$tag.log)"
  grep -q '^RESULT PASS' "$LOGS/$tag.log" || status=1
done
exit $status
