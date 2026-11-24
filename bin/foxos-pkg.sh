# shellcheck shell=bash
# FoxOS package helpers: distro detection and cross-distro package names.
# Sourced by install.sh and foxos-tools. Supports apt (Kali/Debian/Ubuntu/Mint/Parrot/Pop),
# dnf (Fedora/RHEL/Rocky/Alma), pacman (Arch/Manjaro/EndeavourOS), zypper (openSUSE), apk (Alpine) and
# xbps (Void). Uses doas automatically where there is no sudo.
#
# Test hooks (no effect unless set):
#   FOXOS_OS_RELEASE=file   read this instead of /etc/os-release
#   FOXOS_PM=apt|dnf|pacman|zypper   force the package manager
#   FOXOS_DRYRUN=1          print every changing command instead of running it

foxos_detect() {
  # Package managers live in sbin on some distros (Alpine's apk is /sbin/apk), which a normal user's PATH may lack.
  case ":$PATH:" in *:/sbin:*) ;; *) PATH="$PATH:/usr/local/sbin:/usr/sbin:/sbin"; export PATH ;; esac
  local rel="${FOXOS_OS_RELEASE:-/etc/os-release}" ID="" ID_LIKE="" PRETTY_NAME=""
  # shellcheck disable=SC1090
  [ -r "$rel" ] && . "$rel"
  FOXOS_ID="${ID:-unknown}"; FOXOS_LIKE="${ID_LIKE:-}"; FOXOS_NAME="${PRETTY_NAME:-$FOXOS_ID}"
  if [ -n "${FOXOS_PM:-}" ]; then :
  elif command -v apt-get >/dev/null; then FOXOS_PM=apt
  elif command -v dnf >/dev/null; then FOXOS_PM=dnf
  elif command -v pacman >/dev/null; then FOXOS_PM=pacman
  elif command -v zypper >/dev/null; then FOXOS_PM=zypper
  elif command -v apk >/dev/null; then FOXOS_PM=apk
  elif command -v xbps-install >/dev/null; then FOXOS_PM=xbps
  else FOXOS_PM=unknown; fi
  FOXOS_KALI=0; [ "$FOXOS_ID" = kali ] && FOXOS_KALI=1
  case "$(uname -m)" in x86_64|amd64) FOXOS_ARCH=amd64 ;; aarch64|arm64) FOXOS_ARCH=arm64 ;; *) FOXOS_ARCH="$(uname -m)" ;; esac
  export FOXOS_ID FOXOS_LIKE FOXOS_NAME FOXOS_PM FOXOS_KALI FOXOS_ARCH
}

# Systems without sudo (Alpine, Void, some Arch setups) often use doas: make 'sudo' mean doas there.
if ! command -v sudo >/dev/null 2>&1 && command -v doas >/dev/null 2>&1; then sudo() { doas "$@"; }; fi

# Run a command that changes the system (or just print it in dry-run mode).
run() { if [ "${FOXOS_DRYRUN:-0}" = 1 ]; then printf '  [dry-run] %s\n' "$*"; else "$@"; fi; }

pm_update() {
  case "$FOXOS_PM" in
    apt) run sudo apt-get update -qq ;;
    dnf) run sudo dnf -q makecache ;;
    pacman) run sudo pacman -Sy --noconfirm ;;
    zypper) run sudo zypper -q -n refresh ;;
    apk) run sudo apk update -q ;;
    xbps) run sudo xbps-install -Suy xbps >/dev/null 2>&1; run sudo xbps-install -S ;;  # Void wants xbps itself current first
  esac
}

# Is a package name available from the repos (or installed)? In dry-run on a simulated distro whose
# package manager isn't present, assume yes so the plan can be reviewed.
pm_has() {
  local p=$1
  local probe=$FOXOS_PM; case "$probe" in apt) probe=apt-cache ;; xbps) probe=xbps-query ;; esac
  if [ "${FOXOS_DRYRUN:-0}" = 1 ] && ! command -v "$probe" >/dev/null 2>&1; then return 0; fi
  # No 'cmd | grep -q' here: grep -q exits early, the producer gets SIGPIPE, and under 'set -o pipefail'
  # (install.sh, foxos-tools) the check would wrongly fail. Capture the output first.
  local out
  case "$FOXOS_PM" in
    # an installable version must exist (renamed packages like libfuse2 -> libfuse2t64 report "(none)")
    apt) out=$(apt-cache policy "$p" 2>/dev/null); [[ $out =~ Candidate:\ [^\(] ]] ;;
    dnf) dnf -q info "$p" >/dev/null 2>&1 || [ -n "$(dnf -q repoquery --whatprovides "$p" 2>/dev/null)" ] ;;
    pacman) pacman -Si "$p" >/dev/null 2>&1 || pacman -Qi "$p" >/dev/null 2>&1 ;;
    # openSUSE installs by provided name too (python3-tk comes from python313-tk)
    zypper) zypper -q -n search --match-exact --provides "$p" >/dev/null 2>&1 ;;
    apk) out=$(apk search -q -x "$p" 2>/dev/null); [[ $'\n'$out$'\n' == *$'\n'$p$'\n'* ]] || apk info -e "$p" >/dev/null 2>&1 ;;
    xbps) xbps-query -R "$p" >/dev/null 2>&1 || xbps-query "$p" >/dev/null 2>&1 ;;
    *) return 1 ;;
  esac
}

pm_install_raw() {
  case "$FOXOS_PM" in
    apt) run sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@" ;;
    dnf) run sudo dnf install -y -q "$@" ;;
    pacman) run sudo pacman -S --needed --noconfirm "$@" ;;
    zypper) run sudo zypper -q -n install "$@" ;;
    apk) run sudo apk add -q "$@" ;;
    xbps) local miss=() q; for q in "$@"; do xbps-query "$q" >/dev/null 2>&1 || miss+=("$q"); done  # xbps errors on installed ones
          [ ${#miss[@]} -eq 0 ] || run sudo xbps-install -y "${miss[@]}" ;;
    *) echo "  no supported package manager; install manually: $*"; return 1 ;;
  esac
}

# Install packages; if the batch fails, retry one by one so a single bad package can't block the rest.
pm_install() {
  [ $# -eq 0 ] && return 0
  pm_install_raw "$@" && return 0
  [ $# -eq 1 ] && return 1
  local p failed=""
  for p in "$@"; do pm_install_raw "$p" >/dev/null 2>&1 || failed="$failed $p"; done
  [ -n "$failed" ] && { echo "  could not install:$failed"; FOXOS_SKIPPED="${FOXOS_SKIPPED:-}$failed"; }
  return 0
}

# Candidate package names for a logical package on this distro (first available wins).
# Empty output = not packaged here.
pkgnames() {
  local l=$1 pm=$FOXOS_PM
  case "$pm" in
    apk) case "$l" in
      python3-tk) echo py3-tkinter ;; python3-yaml) echo py3-yaml ;; python3-pil) echo py3-pillow ;; python3-venv) echo python3 ;;
      dig) echo bind-tools ;; font-dejavu) echo font-dejavu ;; font-fira) echo font-fira-code ;;
      libfuse2) echo fuse ;; exiftool) echo exiftool ;; *) echo "$l" ;; esac; return ;;
    xbps) case "$l" in
      python3-tk) echo python3-tkinter ;; python3-yaml) echo python3-yaml ;; python3-pil) echo python3-Pillow ;; python3-venv) echo python3 ;;
      dig) echo bind-utils ;; imagemagick) echo ImageMagick ;; font-dejavu) echo dejavu-fonts-ttf ;; font-fira) echo font-firacode ;;
      libfuse2) echo fuse ;; tshark) echo wireshark ;; exiftool) echo perl-Image-ExifTool ;; pipx) echo python3-pipx ;; *) echo "$l" ;; esac; return ;;
  esac
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
