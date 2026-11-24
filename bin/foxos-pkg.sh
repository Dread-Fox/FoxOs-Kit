# shellcheck shell=bash
# FoxOS package helpers: distro detection and cross-distro package names.
# Sourced by install.sh and foxos-tools. Supports apt (Kali/Debian/Ubuntu/Mint/Parrot/Pop),
# dnf (Fedora/RHEL/Rocky/Alma), pacman (Arch/Manjaro/EndeavourOS) and zypper (openSUSE).
#
# Test hooks (no effect unless set):
#   FOXOS_OS_RELEASE=file   read this instead of /etc/os-release
#   FOXOS_PM=apt|dnf|pacman|zypper   force the package manager
#   FOXOS_DRYRUN=1          print every changing command instead of running it

foxos_detect() {
  local rel="${FOXOS_OS_RELEASE:-/etc/os-release}" ID="" ID_LIKE="" PRETTY_NAME=""
  # shellcheck disable=SC1090
  [ -r "$rel" ] && . "$rel"
  FOXOS_ID="${ID:-unknown}"; FOXOS_LIKE="${ID_LIKE:-}"; FOXOS_NAME="${PRETTY_NAME:-$FOXOS_ID}"
  if [ -n "${FOXOS_PM:-}" ]; then :
  elif command -v apt-get >/dev/null; then FOXOS_PM=apt
  elif command -v dnf >/dev/null; then FOXOS_PM=dnf
  elif command -v pacman >/dev/null; then FOXOS_PM=pacman
  elif command -v zypper >/dev/null; then FOXOS_PM=zypper
  else FOXOS_PM=unknown; fi
  FOXOS_KALI=0; [ "$FOXOS_ID" = kali ] && FOXOS_KALI=1
  case "$(uname -m)" in x86_64|amd64) FOXOS_ARCH=amd64 ;; aarch64|arm64) FOXOS_ARCH=arm64 ;; *) FOXOS_ARCH="$(uname -m)" ;; esac
  export FOXOS_ID FOXOS_LIKE FOXOS_NAME FOXOS_PM FOXOS_KALI FOXOS_ARCH
}

# Run a command that changes the system (or just print it in dry-run mode).
run() { if [ "${FOXOS_DRYRUN:-0}" = 1 ]; then printf '  [dry-run] %s\n' "$*"; else "$@"; fi; }

pm_update() {
  case "$FOXOS_PM" in
    apt) run sudo apt-get update -qq ;;
    dnf) run sudo dnf -q makecache ;;
    pacman) run sudo pacman -Sy --noconfirm ;;
    zypper) run sudo zypper -q -n refresh ;;
  esac
}

# Is a package name available from the repos (or installed)? In dry-run on a simulated distro whose
# package manager isn't present, assume yes so the plan can be reviewed.
pm_has() {
  local p=$1
  if [ "${FOXOS_DRYRUN:-0}" = 1 ] && ! command -v "${FOXOS_PM/apt/apt-cache}" >/dev/null 2>&1; then return 0; fi
  case "$FOXOS_PM" in
    apt) apt-cache show "$p" >/dev/null 2>&1 ;;
    dnf) dnf -q info "$p" >/dev/null 2>&1 ;;
    pacman) pacman -Si "$p" >/dev/null 2>&1 || pacman -Qi "$p" >/dev/null 2>&1 ;;
    zypper) zypper -q -n info "$p" 2>/dev/null | grep -q '^Name' ;;
    *) return 1 ;;
  esac
}

pm_install() {
  [ $# -eq 0 ] && return 0
  case "$FOXOS_PM" in
    apt) run sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@" ;;
    dnf) run sudo dnf install -y -q "$@" ;;
    pacman) run sudo pacman -S --needed --noconfirm "$@" ;;
    zypper) run sudo zypper -q -n install "$@" ;;
    *) echo "  no supported package manager; install manually: $*"; return 1 ;;
  esac
}

# Candidate package names for a logical package on this distro (first available wins).
# Empty output = not packaged here.
pkgnames() {
  local l=$1 pm=$FOXOS_PM
  case "$l:$pm" in
    python3:pacman) echo python ;;  python3:*) echo python3 ;;
    python3-tk:apt) echo python3-tk ;; python3-tk:dnf) echo python3-tkinter ;; python3-tk:pacman) echo tk ;; python3-tk:zypper) echo python3-tk ;;
    python3-yaml:apt) echo python3-yaml ;; python3-yaml:dnf) echo python3-pyyaml ;; python3-yaml:pacman) echo python-yaml ;; python3-yaml:zypper) echo python3-PyYAML ;;
    python3-pil:apt) echo python3-pil ;; python3-pil:dnf) echo python3-pillow ;; python3-pil:pacman) echo python-pillow ;; python3-pil:zypper) echo python3-Pillow ;;
    python3-venv:apt) echo python3-venv ;; python3-venv:pacman) echo python ;; python3-venv:*) echo python3 ;;  # venv is part of Python there
    pipx:pacman) echo python-pipx ;; pipx:zypper) echo python3-pipx ;; pipx:*) echo pipx ;;
    dig:apt) echo "bind9-dnsutils dnsutils" ;; dig:dnf|dig:zypper) echo bind-utils ;; dig:pacman) echo bind ;;
    libnotify:apt) echo libnotify-bin ;; libnotify:zypper) echo libnotify-tools ;; libnotify:*) echo libnotify ;;
    imagemagick:dnf|imagemagick:zypper) echo ImageMagick ;; imagemagick:*) echo imagemagick ;;
    font-dejavu:apt) echo fonts-dejavu ;; font-dejavu:dnf) echo dejavu-sans-fonts ;; font-dejavu:pacman) echo ttf-dejavu ;; font-dejavu:zypper) echo dejavu-fonts ;;
    font-fira:apt) echo fonts-firacode ;; font-fira:dnf|font-fira:zypper) echo fira-code-fonts ;; font-fira:pacman) echo ttf-fira-code ;;
    libfuse2:apt) echo "libfuse2t64 libfuse2" ;; libfuse2:dnf) echo fuse-libs ;; libfuse2:pacman) echo fuse2 ;; libfuse2:zypper) echo libfuse2 ;;
    tshark:apt) echo tshark ;; tshark:dnf|tshark:pacman) echo wireshark-cli ;; tshark:zypper) echo wireshark ;;
    exiftool:apt) echo libimage-exiftool-perl ;; exiftool:dnf) echo perl-Image-ExifTool ;; exiftool:pacman) echo perl-image-exiftool ;; exiftool:zypper) echo exiftool ;;
    msfconsole:apt) echo metasploit-framework ;; msfconsole:pacman) echo metasploit ;; msfconsole:*) ;;
    searchsploit:apt) echo exploitdb ;; searchsploit:*) ;;
    httpx-toolkit:apt) echo httpx-toolkit ;; httpx-toolkit:*) ;;
    impacket-secretsdump:apt) echo impacket-scripts ;; impacket-secretsdump:*) ;;
    theHarvester:apt) echo theharvester ;; theHarvester:*) ;;
    netexec:apt|netexec:pacman) echo netexec ;; netexec:*) ;;
    *) echo "$l" ;;
  esac
}

# Install logical packages; report (don't fail on) the ones this distro doesn't carry.
pkg_ensure() {
  local want=() l c found
  for l in "$@"; do
    found=""
    for c in $(pkgnames "$l"); do if pm_has "$c"; then found=$c; break; fi; done
    if [ -n "$found" ]; then want+=("$found"); else FOXOS_SKIPPED="${FOXOS_SKIPPED:-} $l"; fi
  done
  pm_install "${want[@]}"
}
