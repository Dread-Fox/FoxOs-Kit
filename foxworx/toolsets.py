"""Toolkit-aware catalog: which security toolset and category each installed tool belongs to, on any distro.

FOXWORX's registry finds executables; this module explains them. It reads the package database only (no
tool is ever executed) and recognises the major security toolsets regardless of which one a host uses:

  * Kali       kali-tools-* category metapackages (installed or merely available in apt) and kali-linux-*
  * Parrot     parrot-tools-* category metapackages
  * BlackArch  blackarch-* pacman groups (blackarch-scanner, blackarch-webapp, ...)
  * Fedora Security Lab   the dnf 'security-lab' group
  * anything else: tools are still catalogued by owning package, origin (system, Go, Rust, pdtm, snap,
    user, Kali container) and description; they just carry no toolset category.

Results are cached until the package database changes, so frequent registry scans stay cheap.
"""
from __future__ import annotations

import configparser
import os
import re
import shlex
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable

KALI_CATEGORIES = {
    'information-gathering': 'Information gathering', 'vulnerability': 'Vulnerability analysis',
    'web': 'Web applications', 'database': 'Database assessment', 'passwords': 'Password attacks',
    'wireless': 'Wireless attacks', '802-11': 'Wi-Fi (802.11)', 'bluetooth': 'Bluetooth', 'rfid': 'RFID/NFC',
    'sdr': 'Software-defined radio', 'voip': 'VoIP', 'reverse-engineering': 'Reverse engineering',
    'exploitation': 'Exploitation', 'sniffing-spoofing': 'Sniffing & spoofing',
    'post-exploitation': 'Post exploitation', 'forensics': 'Forensics', 'reporting': 'Reporting',
    'social-engineering': 'Social engineering', 'crypto-stego': 'Crypto & stego', 'fuzzing': 'Fuzzing',
    'gpu': 'GPU', 'hardware': 'Hardware', 'identify': 'Identify (forensics)', 'protect': 'Protect',
    'detect': 'Detect', 'respond': 'Respond', 'recover': 'Recover', 'windows-resources': 'Windows resources',
    'top10': 'Top 10'}
# Primary-category preference: Kali's main menu order first (01-13), then the narrower sub-categories.
PRIORITY = ['Information gathering', 'Vulnerability analysis', 'Web applications', 'Database assessment',
            'Password attacks', 'Wireless attacks', 'Reverse engineering', 'Exploitation', 'Sniffing & spoofing',
            'Post exploitation', 'Forensics', 'Reporting', 'Social engineering']
# Category sets that are summaries of others: used for tagging, never as a tool's primary category.
SUMMARY_SETS = {'top10', 'default', 'headless', 'large', 'everything', 'full', 'core'}
BIN_DIRS = ('/usr/local/sbin', '/usr/local/bin', '/usr/sbin', '/usr/bin', '/sbin', '/bin', '/usr/games')
VENDOR_DIRS = {'google', 'microsoft', 'containerd', 'foxos', 'resolve', 'brave.com', 'zoom', 'teamviewer',
               'vivaldi', 'opera', 'cisco', 'nvidia', 'amd'}


def run_text(argv: list[str], timeout: float = 20) -> str:
    """Run a fixed read-only package query with a clean environment; '' if unavailable."""
    if not argv or not Path(argv[0]).is_file():
        return ''
    try:
        with tempfile.TemporaryFile() as out:
            subprocess.run(argv, stdout=out, stderr=subprocess.DEVNULL, timeout=timeout, check=False,
                           env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8'})
            out.seek(0)
            return out.read(32 * 1024 * 1024).decode('utf-8', 'replace')
    except (OSError, subprocess.TimeoutExpired):
        return ''


def nice(name: str) -> str:
    return KALI_CATEGORIES.get(name, name.replace('-', ' ').strip().capitalize())


def nice_menu(category: str) -> str:
    text = category[len('kali-'):] if category.startswith('kali-') else category
    fixed = {'ssl-tls': 'SSL/TLS', 'snmp': 'SNMP', 'smtp': 'SMTP', 'voip': 'VoIP', 'nfc': 'NFC', 'wifi': 'Wi-Fi',
             'wifi-credential-access': 'Wi-Fi credential access', 'network-information-dns': 'Network information (DNS)'}
    return fixed.get(text, text.replace('-', ' ').capitalize())


def parse_apt_depends(text: str) -> dict[str, list[str]]:
    """`apt-cache depends` output -> {metapackage: [package, ...]} (alternatives included)."""
    out: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if line and not line[0].isspace():
            current = line.strip()
            out.setdefault(current, [])
        elif current and ('Depends:' in line or (line.startswith('    ') and not line.strip().endswith(':'))):
            name = line.split('Depends:', 1)[-1].strip().strip('<>')
            if name and ' ' not in name:
                out[current].append(name)
    return out


def parse_groups(text: str) -> list[tuple[str, str]]:
    """`pacman -Sg` / `pacman -Qg` output -> [(group, package)]."""
    pairs = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            pairs.append((parts[0], parts[1]))
    return pairs


def parse_dnf_group(text: str) -> list[str]:
    """`dnf group info` output -> package names listed under its package sections."""
    pkgs, inside = [], False
    for line in text.splitlines():
        if re.match(r'^\s*(Mandatory|Default|Optional|Conditional) [Pp]ackages', line):
            inside = True
            continue
        if inside and line.strip() and line.startswith(' '):
            name = line.split(':')[-1].strip().split()[0]
            if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]*', name):
                pkgs.append(name)
        elif inside and line.strip() and not line.startswith(' '):
            inside = False
    return pkgs


class ToolsetIndex:
    """Package-database knowledge: owner, summary and toolset tags for executables. Thread-safe, cached."""

    def __init__(self, run: Callable[[list[str]], str] = run_text, root: str = '/', home: str | None = None):
        self.run = run
        self.root = Path(root)
        self.home = Path(home or Path.home())
        self._lock = threading.Lock()
        self._stamp = None
        self._data: dict = {}

    # -- cache key -------------------------------------------------------------------------------
    def _p(self, path: str) -> Path:
        return self.root / path.lstrip('/')

    def stamp(self) -> tuple:
        out = []
        for path in ('/var/lib/dpkg/status', '/var/lib/pacman/local', '/var/lib/rpm', '/usr/lib/sysimage/rpm'):
            try:
                s = self._p(path).stat()
                out.append((path, s.st_mtime_ns, s.st_size))
            except OSError:
                continue
        return tuple(out)

    def manager(self) -> str:
        if self._p('/var/lib/dpkg/status').exists():
            return 'dpkg'
        if self._p('/var/lib/pacman/local').is_dir():
            return 'pacman'
        if self._p('/var/lib/rpm').exists() or self._p('/usr/lib/sysimage/rpm').exists():
            return 'rpm'
        return ''

    def data(self) -> dict:
        with self._lock:
            stamp = self.stamp()
            if stamp != self._stamp or not self._data:
                self._data = self._build()
                self._stamp = stamp
            return self._data

    # -- build -----------------------------------------------------------------------------------
    def _build(self) -> dict:
        mgr = self.manager()
        installed: dict[str, str] = {}   # package -> summary
        versions: dict[str, str] = {}
        owners: dict[str, str] = {}      # executable path -> package
        tags: dict[str, list[tuple[str, str]]] = {}
        toolsets: list[dict] = []

        def tag(pkg: str, toolset: str, category: str) -> None:
            if (toolset, category) not in tags.setdefault(pkg, []):
                tags[pkg].append((toolset, category))

        def add_family(toolset: str, members: dict[str, list[str]], prefix: str, installed_metas: set[str]) -> None:
            cats = {}
            for meta, deps in members.items():
                key = meta[len(prefix):]
                pkgs = sorted({d for d in deps if not d.startswith(('kali-', 'parrot-'))})
                if not pkgs:
                    continue
                for pkg in pkgs:
                    if key not in SUMMARY_SETS:
                        tag(pkg, toolset, nice(key))
                have = sum(1 for p in pkgs if p in installed)
                cats[nice(key)] = {'listed': len(pkgs), 'installed': have, 'set_installed': meta in installed_metas}
            if any(c['installed'] for c in cats.values()):
                inst = {p for m, deps in members.items() for p in deps if p in installed and not p.startswith(('kali-', 'parrot-'))}
                listed = {p for deps in members.values() for p in deps if not p.startswith(('kali-', 'parrot-'))}
                toolsets.append({'id': toolset.lower(), 'label': toolset, 'installed': len(inst), 'listed': len(listed),
                                 'sets_installed': sorted(m for m in installed_metas if m.startswith(prefix)),
                                 'categories': dict(sorted(cats.items()))})

        if mgr == 'dpkg':
            for line in self.run(['/usr/bin/dpkg-query', '-W', '-f=${binary:Package}\t${db:Status-Abbrev}\t${Version}\t${binary:Summary}\n']).splitlines():
                parts = line.split('\t', 3)
                if len(parts) == 4 and parts[1].startswith('ii'):
                    name = parts[0].split(':')[0]
                    installed[name], versions[name] = parts[3], parts[2]
            targets = self._link_targets()  # real files behind bin/ symlinks (e.g. msfconsole -> /usr/share/...)
            info = self._p('/var/lib/dpkg/info')
            for listing in info.glob('*.list') if info.is_dir() else []:
                pkg = listing.name[:-5].split(':')[0]
                try:
                    with listing.open(errors='replace') as fh:
                        for path in fh:
                            path = path.rstrip('\n')
                            if (path.startswith(BIN_DIRS) and path.count('/') <= 3) or path in targets:
                                owners[path] = pkg
                except OSError:
                    continue
            for family, toolset in (('kali-tools-', 'Kali'), ('parrot-tools-', 'Parrot')):
                metas = set(self.run(['/usr/bin/apt-cache', 'pkgnames', family]).split())
                metas |= {p for p in installed if p.startswith(family)}
                if metas:
                    deps = parse_apt_depends(self.run(['/usr/bin/apt-cache', 'depends', '--no-recommends', '--no-suggests',
                                                       '--no-conflicts', '--no-breaks', '--no-replaces', '--no-enhances',
                                                       *sorted(metas)], timeout=60))
                    add_family(toolset, {m: d for m, d in deps.items() if m.startswith(family)}, family,
                               {m for m in metas if m in installed})
        elif mgr == 'pacman':
            for block in self.run(['/usr/bin/pacman', '-Qi']).split('\n\n'):
                fields = dict(re.findall(r'^(\w[\w ]*?)\s*:\s*(.*)$', block, re.M))
                if fields.get('Name'):
                    installed[fields['Name']], versions[fields['Name']] = fields.get('Description', ''), fields.get('Version', '')
            local = self._p('/var/lib/pacman/local')
            for d in local.iterdir() if local.is_dir() else []:
                pkg = d.name.rsplit('-', 2)[0]
                try:
                    for path in (d / 'files').read_text(errors='replace').splitlines():
                        path = '/' + path
                        if path.startswith(BIN_DIRS) and path.count('/') <= 3 and not path.endswith('/'):
                            owners[path] = pkg
                except OSError:
                    continue
            members: dict[str, list[str]] = {}
            for group, pkg in parse_groups(self.run(['/usr/bin/pacman', '-Sg'])) or parse_groups(self.run(['/usr/bin/pacman', '-Qg'])):
                if group.startswith('blackarch-'):
                    members.setdefault(group, []).append(pkg)
            if members:
                add_family('BlackArch', members, 'blackarch-', set())
        elif mgr == 'rpm':
            for line in self.run(['/usr/bin/rpm', '-qa', '--qf', '%{NAME}\t%{VERSION}-%{RELEASE}\t%{SUMMARY}\n']).splitlines():
                parts = line.split('\t', 2)
                if len(parts) == 3:
                    installed[parts[0]], versions[parts[0]] = parts[2], parts[1]
            for line in self.run(['/usr/bin/rpm', '-qa', '--qf', '[%{FILENAMES}\t%{NAME}\n]'], timeout=60).splitlines():
                path, _, pkg = line.partition('\t')
                if pkg and path.startswith(BIN_DIRS) and path.count('/') <= 3:
                    owners[path] = pkg
            lab = parse_dnf_group(self.run(['/usr/bin/dnf', '-q', 'group', 'info', 'security-lab'], timeout=60))
            if lab:
                add_family('Fedora Security Lab', {'fsl-security-lab': lab}, 'fsl-', set())
        menu = self._menu()
        # The menu entry's command may differ from the tool's main command (e.g. Metasploit's entry runs
        # msfdb): give every command of that package the entry's categories.
        menu_pkg: dict[str, list[str]] = {}
        for cmd, cats in menu.items():
            for d in BIN_DIRS:
                path = f'{d}/{cmd}'
                pkg = owners.get(path)
                if not pkg and self._p(path).exists():
                    try:
                        real = os.path.realpath(self._p(path))
                        pkg = owners.get(real if self.root == Path('/') else '/' + str(Path(real).relative_to(self.root)))
                    except (OSError, ValueError):
                        pkg = None
                if pkg:
                    menu_pkg.setdefault(pkg, cats)
                    break
        return {'manager': mgr, 'installed': installed, 'versions': versions, 'owners': owners, 'tags': tags,
                'toolsets': toolsets, 'menu': menu, 'menu_pkg': menu_pkg}

    def _link_targets(self) -> set[str]:
        out = set()
        for d in BIN_DIRS:
            base = self._p(d)
            try:
                entries = list(base.iterdir())
            except OSError:
                continue
            for f in entries:
                try:
                    if f.is_symlink():
                        real = os.path.realpath(f)
                        out.add(real if self.root == Path('/') else '/' + str(Path(real).relative_to(self.root)))
                except (OSError, ValueError):
                    continue
        return out

    def _menu(self) -> dict[str, list[str]]:
        """Kali menu categories per command (e.g. wireshark -> Network sniffing, Discovery, Respond)."""
        out: dict[str, list[str]] = {}
        base = self._p('/usr/share/kali-menu/applications')
        for f in sorted(base.glob('*.desktop'))[:3000] if base.is_dir() else []:
            try:
                if f.stat().st_size > 65536:
                    continue
                ini = configparser.ConfigParser(interpolation=None, strict=False)
                ini.read(f)
                entry = ini['Desktop Entry']
                argv = shlex.split(entry.get('Exec', ''))
                if not argv:
                    continue
                if argv[0].endswith('exec-in-shell') and len(argv) > 1:
                    argv = shlex.split(argv[1]) or ['']
                if argv[0] in ('sh', 'bash', 'sudo') and len(argv) > 1:
                    argv = [a for a in argv[1:] if not a.startswith('-')] or ['']
                cmd = Path(argv[0]).name
                if '/helper-scripts/' in argv[0] and cmd.endswith('.sh'):
                    cmd = cmd[:-3]  # e.g. /usr/share/kali-menu/helper-scripts/wireshark.sh -> wireshark
                cats = [nice_menu(c) for c in entry.get('Categories', '').split(';') if c.startswith('kali-') and c != 'kali-kali-offsec-links']
                if cmd and cats and cmd not in out:
                    out[cmd] = cats
            except (OSError, ValueError, KeyError, configparser.Error):
                continue
        return out

    # -- queries ---------------------------------------------------------------------------------
    def owner(self, path: str) -> str:
        d = self.data()
        owners = d['owners']
        if path in owners:
            return owners[path]
        try:
            real = os.path.realpath(self._p(path))
            real = real if self.root == Path('/') else '/' + str(Path(real).relative_to(self.root))
            if real in owners:
                return owners[real]
        except (OSError, ValueError):
            pass
        alt = path[4:] if path.startswith('/usr/') else '/usr' + path  # merged-/usr hosts
        if alt in owners:
            return owners[alt]
        return ''

    def origin(self, path: str) -> str:
        """Where a tool comes from: package, kali-container, go, cargo, pdtm, snap, pipx, user, local."""
        home = str(self.home)
        if path.startswith(home + '/.local/bin/'):
            try:
                head = Path(path).read_bytes()[:400]
            except OSError:
                head = b''
            if b'foxos-kali-export' in head or b'distrobox' in head:
                return 'kali-container'
            return 'user'
        for prefix, label in ((home + '/go/bin/', 'go'), ('/usr/local/go/bin/', 'go'), (home + '/.cargo/bin/', 'cargo'),
                              (home + '/.pdtm/go/bin/', 'pdtm'), ('/snap/bin/', 'snap'), ('/opt/foxos/pipx/', 'pipx')):
            if path.startswith(prefix):
                return label
        return 'local' if path.startswith('/usr/local/') else 'package'

    def describe(self, path: str, package: str = '') -> dict:
        """Owner package, version, summary, toolset tags and primary category for one executable."""
        d = self.data()
        pkg = package or self.owner(path)
        tags = d['tags'].get(pkg, [])
        menu = d.get('menu', {}).get(Path(path).name) or d.get('menu_pkg', {}).get(pkg, [])
        tags = tags + [('Kali menu', c) for c in menu]
        ranked = sorted((t for t in tags if t[0] != 'Kali menu' and t[1] != nice('top10')),
                        key=lambda t: (PRIORITY.index(t[1]) if t[1] in PRIORITY else len(PRIORITY), t[1]))
        if menu:  # Kali's own menu is the most specific signal for a tool's main purpose
            primary = ('Kali', menu[0])
        else:
            primary = ranked[0] if ranked else (tags[0] if tags else None)
        return {'package': pkg, 'version': d['versions'].get(pkg, ''), 'summary': d['installed'].get(pkg, '')[:240],
                'origin': self.origin(path),
                'toolsets': [{'toolset': t, 'category': c} for t, c in tags],
                'toolset_category': f'{primary[0]} · {primary[1]}' if primary else ''}

    def projects(self) -> list[dict]:
        """Tool folders in /opt and ~/tools that aren't single commands on the PATH."""
        out = []
        for base in (self._p('/opt'), self.home / 'tools', self.home / 'Tools'):
            if not base.is_dir():
                continue
            for d in sorted(base.iterdir()):
                if not d.is_dir() or d.name.lower() in VENDOR_DIRS or d.name.startswith('.'):
                    continue
                launchers = []
                for f in sorted(list(d.glob('*')) + list(d.glob('bin/*')))[:400]:
                    try:
                        if f.is_file() and (os.access(f, os.X_OK) or f.read_bytes()[:2] == b'#!'):
                            launchers.append(str(f.relative_to(d)))
                    except OSError:
                        continue
                out.append({'path': str(d), 'name': d.name, 'launchers': launchers[:5]})
        return out[:200]
