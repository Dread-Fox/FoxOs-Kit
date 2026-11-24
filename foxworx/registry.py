"""Inventory executables and metadata without blindly invoking --help/--version."""
from pathlib import Path
import configparser
import json
import os
import re
import shlex
import subprocess
import time
import threading
from .config import Settings
from .toolsets import ToolsetIndex

NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.+-]{0,149}$')
CATALOG = json.loads(Path(__file__).with_name('catalog.json').read_text())

class Registry:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.items: dict[str, dict] = {}
        self.scanned = 0.0
        self._scan_lock=threading.RLock()
        # Toolkit knowledge (package owner, toolset + category, origin) from the package database; optional.
        self.toolsets: ToolsetIndex | None = ToolsetIndex()

    def scan(self) -> list[dict]:
        with self._scan_lock:
            return self._scan()

    def _scan(self) -> list[dict]:
        found = {}
        for directory in self.cfg.tool_path.split(':'):
            p = Path(directory)
            if not p.is_dir():
                continue
            try:
                for f in p.iterdir():
                    if NAME.fullmatch(f.name) and f.name not in found and f.is_file() and os.access(f, os.X_OK):
                        found[f.name] = str(f.absolute())
            except PermissionError:
                continue
        versions = {}
        try:
            r = subprocess.run(['/usr/bin/dpkg-query', '-W', '-f=${binary:Package}\t${Version}\n'], capture_output=True, text=True, timeout=5, check=False)
            for line in r.stdout.splitlines():
                if '\t' in line:
                    p, v = line.split('\t', 1)
                    versions[p.split(':')[0]] = v
        except (OSError, subprocess.TimeoutExpired):
            pass
        items = {}
        for name in set(CATALOG) | set(found):
            meta = CATALOG.get(name, {'category':'System / additional', 'mode':'unknown', 'package':'',
                                      'description':'Discovered executable; no specialized adapter',
                                      'note':'Verify invocation and dependencies. Interactive/GUI requirements have not been classified.'})
            items[name] = {'name':name, **meta, 'path':found.get(name), 'installed':name in found,
                           'curated':name in CATALOG, 'version':versions.get(meta['package'], '')}
        # Read launch metadata, never execute .desktop command strings.
        for directory in ('/usr/share/applications', '/usr/share/kali-menu/applications'):
            for file in list(Path(directory).glob('*.desktop'))[:3000]:
                try:
                    if file.stat().st_size > 65536:
                        continue
                    ini = configparser.ConfigParser(interpolation=None, strict=False)
                    ini.read(file)
                    entry = ini['Desktop Entry']
                    command = shlex.split(entry.get('Exec', ''))
                    if command:
                        name = Path(command[0]).name
                        if name in items and name not in CATALOG:
                            items[name]['mode'] = 'interactive' if entry.get('Terminal', 'false').lower() == 'true' else 'gui'
                            items[name]['description'] = entry.get('Comment', entry.get('Name', name))[:240]
                            items[name]['note'] = 'Mode inferred from desktop entry; verify any headless alternative.'
                except (OSError, ValueError, KeyError, configparser.Error):
                    pass
        self._annotate(items)
        self.items = items
        self.scanned = time.time()
        return self.list()

    def _annotate(self, items: dict) -> None:
        """Add toolset/category, origin, owning package and a description to every discovered tool."""
        index = self.toolsets
        if index is None:
            return
        try:
            index.data()
        except Exception:  # nosec B110 - package metadata is best-effort; the inventory still works without it
            return
        placeholder = 'Discovered executable; no specialized adapter'
        for item in items.values():
            if not item.get('installed') or not item.get('path'):
                continue
            try:
                info = index.describe(item['path'], item.get('package', ''))
            except Exception:  # nosec B112 - one odd file must not break the scan
                continue
            item['origin'] = info['origin']
            item['toolsets'] = info['toolsets']
            if not item.get('package'):
                item['package'] = info['package']
            if not item.get('version'):
                item['version'] = info['version']
            if not item['curated']:
                store_entry = store_catalog().get(item['name'])
                if info['toolset_category']:
                    item['category'] = info['toolset_category']
                elif store_entry and info['origin'] != 'package':
                    item['category'] = store_entry['category']
                    if item.get('description') == placeholder:
                        item['description'] = store_entry['description']
                elif info['origin'] in ('go', 'cargo', 'pdtm', 'pipx', 'snap', 'kali-container'):
                    item['category'] = {'kali-container': 'Kali container'}.get(info['origin'], 'User-installed · ' + info['origin'])
                if item.get('description') == placeholder and info['summary']:
                    item['description'] = info['summary']

    def list(self) -> list[dict]:
        if not self.scanned:
            return self.scan()
        return sorted(self.items.values(), key=lambda x: (not x['curated'], x['name'].lower()))

    def resolve(self, name: str) -> dict:
        if not NAME.fullmatch(name):
            raise ValueError('Select a discovered executable name, not a path or command line')
        if not self.scanned:
            self.scan()
        item = self.items.get(name)
        if not item or not item['installed']:
            raise ValueError(f'{name} is not installed/discovered. Install it separately, then refresh the toolbox.')
        path = Path(item['path'])
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError(f'{name} is no longer executable; refresh the toolbox')
        return dict(item)

    @staticmethod
    def fingerprint(path: str) -> dict:
        p = Path(path)
        s = p.stat()
        return {'realpath':str(p.resolve()), 'inode':s.st_ino, 'size':s.st_size, 'mtime_ns':s.st_mtime_ns}

def store_catalog() -> dict:
    from .tool_store import BY_COMMAND  # local import: tool_store imports registry-independent modules only
    return BY_COMMAND


# Public discovery facade; implementation has no dependency on Registry.
from .tool_discovery import ToolDiscovery  # noqa: E402,F401  (re-exported public API)
