"""Tool store: find installable security tools by keyword and install them; FOXWORX recognises them at once.

Sources searched (read-only queries with fixed arguments; nothing is executed to search):
  * the distro's package repositories (apt / pacman / dnf), with security toolset categories from
    ToolsetIndex (Kali, Parrot, BlackArch, Fedora Security Lab)
  * a vetted catalog of user-level tools: ProjectDiscovery releases, pipx Python tools and Go tools

Installing:
  * user-level tools install in the background as this Linux user, with no password: ProjectDiscovery
    binaries are SHA-256 checked against the release's checksums file; pipx and Go use their own tooling.
    Only catalog entries can be installed this way (never free-form text).
  * system packages need root, so FOXWORX never runs them: it returns the exact, validated command for the
    operator to review and run in a terminal with sudo.
  * the agent has no access to the store; installing tools is an operator action.
After an install the registry is rescanned, so new tools appear in the catalog and the agent's tool search.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from .tool_discovery import tokens
from .toolsets import ToolsetIndex, run_text

PKG = re.compile(r'^[a-z0-9][a-z0-9.+_-]{0,100}$')
# Repository packages that are libraries, headers, debug symbols, docs or firmware blobs, not tools.
NOT_TOOL = re.compile(r'(^lib|^golang-|^librust-|^libghc-|^python3?-|^ruby-|^node-|^php-|^perl-|^r-cran-|^texlive-|'
                      r'-dev$|-dbg$|-dbgsym$|-doc$|-docs$|-data$|-common$|-dkms$|-headers$|^firmware-|-firmware$)')
# Multi-word phrases searched as one idea (so "active directory" doesn't match "directory brute force").
PHRASES = {'active directory': 'ad', 'cross site scripting': 'xss', 'sql injection': 'sqli', 'man in the middle': 'mitm',
           'brute force': 'bruteforce', 'reverse engineering': 'reversing', 'privilege escalation': 'privesc'}
# A few cyber synonyms so everyday words find tools described differently.
SYNONYMS = {'wifi': {'wireless', '802', 'wlan', 'wpa', 'aircrack'}, 'wireless': {'wifi', '802', 'wlan'},
            'ad': {'ldap', 'kerberos', 'smb', 'ntlm'}, 'xss': {'cross', 'scripting'},
            'subdomain': {'subdomains'}, 'osint': {'reconnaissance', 'recon', 'information'},
            'sniff': {'sniffing', 'capture', 'packet'}, 'password': {'passwords', 'crack', 'hash'},
            'firmware': {'binwalk', 'embedded'}, 'jwt': {'token', 'json'}, 'sqli': {'sql', 'injection'},
            'mitm': {'spoof', 'spoofing', 'intercept'}, 'bruteforce': {'brute', 'force', 'cracker'},
            'reversing': {'reverse', 'disassembler', 'debugger'}, 'privesc': {'privilege', 'escalation'}}
TERM = re.compile(r'^[A-Za-z0-9 ._+-]{1,80}$')

# Vetted user-level tools. 'command' is what lands on the PATH.
CATALOG = [
    # ProjectDiscovery release binaries (verified against each release's checksums file)
    {'name': 'subfinder', 'source': 'pd', 'spec': 'subfinder', 'command': 'subfinder', 'category': 'Recon',
     'description': 'Passive subdomain discovery from many sources', 'keywords': 'subdomain dns recon osint enumerate'},
    {'name': 'dnsx', 'source': 'pd', 'spec': 'dnsx', 'command': 'dnsx', 'category': 'Recon',
     'description': 'Fast multi-purpose DNS toolkit (resolve, brute, wildcard filtering)', 'keywords': 'dns resolve recon'},
    {'name': 'httpx', 'source': 'pd', 'spec': 'httpx', 'command': 'httpx-toolkit', 'category': 'Web',
     'description': 'HTTP probing: status, title, tech, TLS (installed as httpx-toolkit)', 'keywords': 'http probe web live hosts'},
    {'name': 'nuclei', 'source': 'pd', 'spec': 'nuclei', 'command': 'nuclei', 'category': 'Vulnerability analysis',
     'description': 'Template-based vulnerability scanner', 'keywords': 'vulnerability scanner templates cve'},
    {'name': 'katana', 'source': 'pd', 'spec': 'katana', 'command': 'katana', 'category': 'Web',
     'description': 'Next-generation web crawler', 'keywords': 'crawl spider web urls'},
    {'name': 'tlsx', 'source': 'pd', 'spec': 'tlsx', 'command': 'tlsx', 'category': 'Recon',
     'description': 'TLS grabber: certificates, SANs, versions, ciphers', 'keywords': 'tls ssl certificate san'},
    {'name': 'uncover', 'source': 'pd', 'spec': 'uncover', 'command': 'uncover', 'category': 'Recon',
     'description': 'Query Shodan, Censys, FOFA and others for exposed hosts', 'keywords': 'shodan censys osint exposed'},
    {'name': 'alterx', 'source': 'pd', 'spec': 'alterx', 'command': 'alterx', 'category': 'Recon',
     'description': 'Subdomain wordlist generator from patterns', 'keywords': 'subdomain permutation wordlist'},
    {'name': 'asnmap', 'source': 'pd', 'spec': 'asnmap', 'command': 'asnmap', 'category': 'Recon',
     'description': 'Map organisations to ASNs and IP ranges', 'keywords': 'asn ip range network osint'},
    {'name': 'mapcidr', 'source': 'pd', 'spec': 'mapcidr', 'command': 'mapcidr', 'category': 'Recon',
     'description': 'CIDR range manipulation', 'keywords': 'cidr ip range subnet'},
    {'name': 'cdncheck', 'source': 'pd', 'spec': 'cdncheck', 'command': 'cdncheck', 'category': 'Recon',
     'description': 'Detect CDN, cloud and WAF providers for IPs/hosts', 'keywords': 'cdn waf cloud detect'},
    {'name': 'notify', 'source': 'pd', 'spec': 'notify', 'command': 'notify', 'category': 'Reporting',
     'description': 'Send tool output to Slack, Discord, Telegram and more', 'keywords': 'notification alert discord slack'},
    # pipx (isolated Python apps)
    {'name': 'sqlmap', 'source': 'pipx', 'spec': 'sqlmap', 'command': 'sqlmap', 'category': 'Web',
     'description': 'Automatic SQL injection and database takeover', 'keywords': 'sql injection sqli database web'},
    {'name': 'impacket', 'source': 'pipx', 'spec': 'impacket', 'command': 'secretsdump.py', 'category': 'Active Directory',
     'description': 'Windows/AD protocol tools (secretsdump, psexec, GetUserSPNs...)', 'keywords': 'windows active directory smb kerberos ad credentials'},
    {'name': 'netexec', 'source': 'pipx', 'spec': 'git+https://github.com/Pennyw0rth/NetExec', 'command': 'netexec', 'category': 'Active Directory',
     'description': 'Network execution: SMB/LDAP/WinRM/MSSQL assessment (nxc)', 'keywords': 'smb ldap winrm active directory windows network'},
    {'name': 'certipy-ad', 'source': 'pipx', 'spec': 'certipy-ad', 'command': 'certipy', 'category': 'Active Directory',
     'description': 'Active Directory Certificate Services enumeration and abuse', 'keywords': 'adcs certificate active directory'},
    {'name': 'bloodhound-python', 'source': 'pipx', 'spec': 'bloodhound', 'command': 'bloodhound-python', 'category': 'Active Directory',
     'description': 'BloodHound data collector for Active Directory', 'keywords': 'bloodhound active directory graph collector'},
    {'name': 'mitm6', 'source': 'pipx', 'spec': 'mitm6', 'command': 'mitm6', 'category': 'Sniffing & spoofing',
     'description': 'IPv6 DNS takeover via DHCPv6 (Windows networks)', 'keywords': 'ipv6 dhcpv6 dns spoof mitm windows'},
    {'name': 'wafw00f', 'source': 'pipx', 'spec': 'wafw00f', 'command': 'wafw00f', 'category': 'Web',
     'description': 'Identify web application firewalls', 'keywords': 'waf firewall web fingerprint'},
    {'name': 'sslyze', 'source': 'pipx', 'spec': 'sslyze', 'command': 'sslyze', 'category': 'Recon',
     'description': 'TLS/SSL configuration scanner', 'keywords': 'tls ssl cipher certificate scan'},
    {'name': 'arjun', 'source': 'pipx', 'spec': 'arjun', 'command': 'arjun', 'category': 'Web',
     'description': 'HTTP parameter discovery', 'keywords': 'http parameter fuzz web api'},
    {'name': 'dirsearch', 'source': 'pipx', 'spec': 'dirsearch', 'command': 'dirsearch', 'category': 'Web',
     'description': 'Web path/directory brute forcer', 'keywords': 'directory brute force web content discovery'},
    {'name': 'pwntools', 'source': 'pipx', 'spec': 'pwntools', 'command': 'pwn', 'category': 'Exploitation',
     'description': 'CTF and exploit development framework', 'keywords': 'exploit ctf binary pwn rop'},
    {'name': 'ropper', 'source': 'pipx', 'spec': 'ropper', 'command': 'ropper', 'category': 'Reverse engineering',
     'description': 'ROP gadget finder for binaries', 'keywords': 'rop gadget binary exploit reverse'},
    {'name': 'name-that-hash', 'source': 'pipx', 'spec': 'name-that-hash', 'command': 'nth', 'category': 'Password attacks',
     'description': 'Identify hash types (with hashcat/john modes)', 'keywords': 'hash identify password crack'},
    {'name': 'semgrep', 'source': 'pipx', 'spec': 'semgrep', 'command': 'semgrep', 'category': 'Code analysis',
     'description': 'Static analysis for code security issues', 'keywords': 'sast code static analysis secure review'},
    {'name': 'shodan', 'source': 'pipx', 'spec': 'shodan', 'command': 'shodan', 'category': 'Recon',
     'description': 'Shodan command-line client (needs an API key)', 'keywords': 'shodan osint exposed internet search'},
    # Go tools (go install; needs Go)
    {'name': 'waybackurls', 'source': 'go', 'spec': 'github.com/tomnomnom/waybackurls@latest', 'command': 'waybackurls', 'category': 'Recon',
     'description': 'Fetch URLs the Wayback Machine knows for a domain', 'keywords': 'wayback archive urls recon osint'},
    {'name': 'gau', 'source': 'go', 'spec': 'github.com/lc/gau/v2/cmd/gau@latest', 'command': 'gau', 'category': 'Recon',
     'description': 'Get all known URLs (Wayback, Common Crawl, OTX, URLScan)', 'keywords': 'urls archive crawl recon'},
    {'name': 'assetfinder', 'source': 'go', 'spec': 'github.com/tomnomnom/assetfinder@latest', 'command': 'assetfinder', 'category': 'Recon',
     'description': 'Find domains and subdomains related to a domain', 'keywords': 'subdomain domain recon'},
    {'name': 'httprobe', 'source': 'go', 'spec': 'github.com/tomnomnom/httprobe@latest', 'command': 'httprobe', 'category': 'Web',
     'description': 'Find working HTTP/HTTPS servers from a host list', 'keywords': 'http probe live hosts'},
    {'name': 'ffuf', 'source': 'go', 'spec': 'github.com/ffuf/ffuf/v2@latest', 'command': 'ffuf', 'category': 'Web',
     'description': 'Fast web fuzzer (content, parameters, vhosts)', 'keywords': 'fuzz web directory parameter vhost brute'},
    {'name': 'gobuster', 'source': 'go', 'spec': 'github.com/OJ/gobuster/v3@latest', 'command': 'gobuster', 'category': 'Web',
     'description': 'Directory, DNS and vhost brute forcing', 'keywords': 'directory dns vhost brute force'},
    {'name': 'hakrawler', 'source': 'go', 'spec': 'github.com/hakluke/hakrawler@latest', 'command': 'hakrawler', 'category': 'Web',
     'description': 'Simple fast web crawler for endpoints and assets', 'keywords': 'crawl spider web endpoints'},
    {'name': 'kerbrute', 'source': 'go', 'spec': 'github.com/ropnop/kerbrute@latest', 'command': 'kerbrute', 'category': 'Active Directory',
     'description': 'Kerberos user enumeration and password spraying', 'keywords': 'kerberos active directory users spray brute'},
    {'name': 'dalfox', 'source': 'go', 'spec': 'github.com/hahwul/dalfox/v2@latest', 'command': 'dalfox', 'category': 'Web',
     'description': 'XSS scanner and parameter analysis', 'keywords': 'xss cross site scripting web scanner'},
    {'name': 'anew', 'source': 'go', 'spec': 'github.com/tomnomnom/anew@latest', 'command': 'anew', 'category': 'Utilities',
     'description': 'Append only new lines to a file (recon pipelines)', 'keywords': 'dedupe lines pipeline utility'},
    {'name': 'unfurl', 'source': 'go', 'spec': 'github.com/tomnomnom/unfurl@latest', 'command': 'unfurl', 'category': 'Utilities',
     'description': 'Pull out bits of URLs (domains, paths, params)', 'keywords': 'url parse parameters utility'},
]
BY_NAME = {c['name']: c for c in CATALOG}
BY_COMMAND = {c['command']: c for c in CATALOG}


def _arch() -> str:
    m = os.uname().machine
    return {'x86_64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(m, m)


class ToolStore:
    def __init__(self, cfg, registry, index: ToolsetIndex | None = None, run: Callable[[list[str]], str] = run_text,
                 on_installed: Callable[[str], None] | None = None, fetch: Callable[[str], bytes] | None = None,
                 resolve: Callable[[str], str] | None = None):
        self.cfg, self.registry = cfg, registry
        self.index = index if index is not None else getattr(registry, 'toolsets', None)
        self.run = run
        self.on_installed = on_installed
        self.fetch = fetch or self._fetch
        self.resolve = resolve or self._resolve
        self.tasks: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._desc_cache: dict[str, str] = {}

    # -- helpers --------------------------------------------------------------------------------
    def _which(self, name: str) -> str:
        return shutil.which(name, path=self.cfg.tool_path) or ''

    def _installed_commands(self) -> set[str]:
        return {i['name'] for i in self.registry.list() if i.get('installed')}

    def manager(self) -> str:
        return self.index.manager() if self.index is not None else ''

    def _installed_pkgs(self) -> dict:
        try:
            return self.index.data()['installed'] if self.index is not None else {}
        except Exception:  # nosec B110 - package data is optional
            return {}

    def _tags(self) -> dict:
        try:
            return self.index.data()['tags'] if self.index is not None else {}
        except Exception:  # nosec B110
            return {}

    def install_command(self, pkg: str) -> str:
        if not PKG.fullmatch(pkg):
            raise ValueError('Invalid package name')
        return {'dpkg': f'sudo apt install {pkg}', 'pacman': f'sudo pacman -S --needed {pkg}',
                'rpm': f'sudo dnf install {pkg}'}.get(self.manager(), '')

    # -- search ---------------------------------------------------------------------------------
    def search(self, query: str, limit: int = 40) -> dict:
        query = (query or '').strip()
        if not TERM.fullmatch(query):
            raise ValueError('Search with words, letters and numbers (up to 80 characters)')
        phrased = query.lower()
        for phrase, word in PHRASES.items():
            phrased = phrased.replace(phrase, word)
        terms = tokens(phrased)
        if not terms:
            raise ValueError('Search with a cyber keyword, e.g. "subdomain", "wifi", "jwt", "active directory"')
        extra = set().union(*(tokens(' '.join(SYNONYMS.get(t, ()))) for t in terms)) if terms else set()
        installed_cmds, installed_pkgs, tags = self._installed_commands(), self._installed_pkgs(), self._tags()
        results: dict[str, dict] = {}

        def phrase(text: str) -> str:
            text = text.lower()
            for ph, word in PHRASES.items():
                text = text.replace(ph, word)
            return text

        def score(name: str, text: str) -> int:
            nt, dt = tokens(name.replace('-', ' ')) | tokens(name), tokens(phrase(text))
            direct = sum(3 for t in terms if t in nt) + sum(1 for t in terms if t in dt)
            return direct + sum(1 for t in extra - terms if t in nt or t in dt)

        for c in CATALOG:
            s = score(c['name'] + ' ' + c['command'], c['description'] + ' ' + c['keywords'] + ' ' + c['category'])
            if s:
                s += 3  # vetted, installable without a password
                results[f"{c['source']}:{c['name']}"] = {
                    'id': f"{c['source']}:{c['name']}", 'name': c['name'], 'source': c['source'], 'command': c['command'],
                    'description': c['description'], 'category': c['category'], 'installed': c['command'] in installed_cmds,
                    'install': 'direct', 'score': s + 1}
        for pkg, desc in self._repo_search(query, terms, extra).items():
            if not PKG.fullmatch(pkg):
                continue
            ptags = tags.get(pkg, [])
            if not ptags and NOT_TOOL.search(pkg):
                continue
            s = score(pkg, desc + ' ' + ' '.join(c for _, c in ptags))
            if not s:
                continue
            category = f'{ptags[0][0]} · {ptags[0][1]}' if ptags else ''
            results[f'pkg:{pkg}'] = {'id': f'pkg:{pkg}', 'name': pkg, 'source': self.manager() or 'system', 'command': '',
                                     'description': desc[:240], 'category': category, 'installed': pkg in installed_pkgs,
                                     'install': 'terminal', 'terminal_command': self.install_command(pkg),
                                     'score': s + (6 if ptags else 0)}  # security toolset packages first
        ranked = sorted(results.values(), key=lambda r: (-r['score'], r['installed'], r['name']))[:limit]
        return {'query': query, 'manager': self.manager(), 'results': ranked,
                'have': {'pipx': bool(self._which('pipx')), 'go': bool(self._which('go'))}}

    def _repo_search(self, query: str, terms: set[str], extra: set[str] = frozenset()) -> dict[str, str]:
        """Package -> description from the distro's repositories, plus toolset packages matching the terms."""
        out: dict[str, str] = {}
        mgr = self.manager()
        words = [w for w in re.findall(r'[A-Za-z0-9+._-]+', query) if len(w) >= 2][:4]
        if mgr == 'dpkg':
            text = self.run(['/usr/bin/apt-cache', 'search', '--', *words])
            for line in text.splitlines()[:400]:
                pkg, _, desc = line.partition(' - ')
                out[pkg.strip()] = desc.strip()
            # toolset packages (e.g. Kali category members) whose category matches, with descriptions
            want = terms | extra
            tagged = [p for p, t in self._tags().items() if any(tokens(c) & want for _, c in t)][:150]
            missing = [p for p in tagged if p not in out and p not in self._desc_cache]
            if missing:
                block = self.run(['/usr/bin/apt-cache', 'show', '--no-all-versions', *missing], )
                for para in block.split('\n\n'):
                    m = re.search(r'^Package: (\S+)', para, re.M)
                    d = re.search(r'^Description(?:-en)?: (.*)$', para, re.M)
                    if m:
                        self._desc_cache[m.group(1)] = d.group(1) if d else ''
            for p in tagged:
                out.setdefault(p, self._desc_cache.get(p, ''))
        elif mgr == 'pacman':
            text = self.run(['/usr/bin/pacman', '-Ss', *words])
            lines = text.splitlines()
            for i, line in enumerate(lines[:800]):
                m = re.match(r'^\S+/(\S+) \S+', line)
                if m:
                    out[m.group(1)] = lines[i + 1].strip() if i + 1 < len(lines) else ''
        elif mgr == 'rpm':
            text = self.run(['/usr/bin/dnf', '-q', 'search', *words], )
            for line in text.splitlines()[:400]:
                m = re.match(r'^\s*([A-Za-z0-9._+-]+)\.[a-z0-9_]+\s+:\s+(.*)$', line)
                if m:
                    out[m.group(1)] = m.group(2)
        return out

    # -- install --------------------------------------------------------------------------------
    def install(self, item_id: str, owner: str = '') -> dict:
        source, _, name = item_id.partition(':')
        if source == 'pkg':
            cmd = self.install_command(name)
            if not cmd:
                raise ValueError('Unsupported package manager on this host')
            return {'mode': 'terminal', 'command': cmd,
                    'message': 'System packages need your password: review the command in the terminal and press Enter.'}
        entry = BY_NAME.get(name)
        if not entry or entry['source'] != source:
            raise ValueError('Unknown tool')
        if source == 'pipx' and not self._which('pipx'):
            return {'mode': 'terminal', 'command': self.install_command({'pacman': 'python-pipx'}.get(self.manager(), 'pipx')),
                    'message': 'pipx is needed first: install it, then try again.'}
        if source == 'go' and not self._which('go'):
            return {'mode': 'terminal', 'command': self.install_command({'dpkg': 'golang-go', 'rpm': 'golang'}.get(self.manager(), 'go')),
                    'message': 'Go is needed first: install it, then try again.'}
        task = {'id': secrets.token_hex(8), 'name': name, 'source': source, 'status': 'running', 'log': '',
                'started': time.time(), 'owner': owner}
        with self._lock:
            if any(t['status'] == 'running' and t['name'] == name for t in self.tasks.values()):
                raise ValueError(f'{name} is already being installed')
            self.tasks[task['id']] = task
        threading.Thread(target=self._run_install, args=(task, entry), name='foxworx-store', daemon=True).start()
        return {'mode': 'task', 'task': self._public(task)}

    def _public(self, task: dict) -> dict:
        return {k: task[k] for k in ('id', 'name', 'source', 'status', 'log', 'started')}

    def list_tasks(self) -> list[dict]:
        with self._lock:
            return [self._public(t) for t in sorted(self.tasks.values(), key=lambda t: -t['started'])[:20]]

    def _log(self, task: dict, text: str) -> None:
        task['log'] = (task['log'] + text.rstrip() + '\n')[-6000:]

    def _run_install(self, task: dict, entry: dict) -> None:
        try:
            if entry['source'] == 'pd':
                self._install_pd(task, entry)
            else:
                argv = ([self._which('pipx'), 'install', '--force', entry['spec']] if entry['source'] == 'pipx'
                        else [self._which('go'), 'install', entry['spec']])
                env = {'PATH': self.cfg.tool_path, 'HOME': str(Path.home()), 'LANG': 'C.UTF-8'}
                proc = subprocess.run(argv, capture_output=True, text=True, timeout=1800, env=env, check=False)
                self._log(task, (proc.stdout + proc.stderr)[-4000:])
                if proc.returncode != 0:
                    raise RuntimeError(f'{entry["source"]} exited with {proc.returncode}')
            task['status'] = 'installed'
            self._log(task, f'{entry["command"]} installed.')
            if self.on_installed:
                self.on_installed(entry['name'])
        except Exception as exc:  # report to the operator; never crash the workbench
            task['status'] = 'failed'
            self._log(task, f'Install failed: {exc}')

    @staticmethod
    def _fetch(url: str) -> bytes:
        if not url.startswith(('https://api.github.com/repos/projectdiscovery/', 'https://github.com/projectdiscovery/')):
            raise ValueError('Unexpected download location')
        req = urllib.request.Request(url, headers={'User-Agent': 'foxworx-tool-store', 'Accept': 'application/octet-stream, application/json'})
        with urllib.request.urlopen(req, timeout=120) as r:  # nosec B310 - fixed https GitHub URLs only
            return r.read(200 * 1024 * 1024)

    @staticmethod
    def _resolve(url: str) -> str:
        """Final URL after redirects (github.com/<repo>/releases/latest -> .../releases/tag/vX)."""
        if not url.startswith('https://github.com/projectdiscovery/'):
            raise ValueError('Unexpected location')
        req = urllib.request.Request(url, method='HEAD', headers={'User-Agent': 'foxworx-tool-store'})
        with urllib.request.urlopen(req, timeout=60) as r:  # nosec B310 - fixed https GitHub URL
            return r.geturl()

    def _latest_version(self, repo: str) -> str:
        # The releases/latest redirect isn't rate-limited like the GitHub API (60 calls/hour per IP); API is the fallback.
        try:
            final = self.resolve(f'https://github.com/projectdiscovery/{repo}/releases/latest')
            m = re.search(r'/releases/tag/v?([^/?#]+)$', final)
            if m:
                return m.group(1)
        except Exception:  # nosec B110 - fall back to the API below
            pass
        release = json.loads(self.fetch(f'https://api.github.com/repos/projectdiscovery/{repo}/releases/latest'))
        return str(release['tag_name']).lstrip('v')

    def _install_pd(self, task: dict, entry: dict) -> None:
        repo = entry['spec']
        ver = self._latest_version(repo)
        if not re.fullmatch(r'[0-9][0-9A-Za-z.+-]{0,40}', ver):
            raise ValueError('Unexpected release version')
        asset = f'{repo}_{ver}_linux_{_arch()}.zip'
        base = f'https://github.com/projectdiscovery/{repo}/releases/download/v{ver}/'
        self._log(task, f'Downloading {asset}…')
        blob = self.fetch(base + asset)
        sums = self.fetch(base + f'{repo}_{ver}_checksums.txt').decode('utf-8', 'replace')
        want = next((line.split()[0] for line in sums.splitlines() if line.strip().endswith(asset)), '')
        got = hashlib.sha256(blob).hexdigest()
        if not want or want != got:
            raise ValueError(f'checksum mismatch for {asset}; not installed')
        self._log(task, f'Checksum verified ({got[:12]}…)')
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            data = z.read(repo)
        dest_dir = Path.home() / '.local/bin'
        dest_dir.mkdir(parents=True, exist_ok=True)
        tmp = dest_dir / f'.{entry["command"]}.foxworx-tmp'
        tmp.write_bytes(data)
        tmp.chmod(0o755)
        os.replace(tmp, dest_dir / entry['command'])
        self._log(task, f'Installed {entry["command"]} {ver} to {dest_dir}')

    # -- repositories ---------------------------------------------------------------------------
    def repos(self) -> list[dict]:
        mgr = self.manager()
        kali_host = Path('/etc/os-release').exists() and 'ID=kali' in Path('/etc/os-release').read_text(errors='replace')

        def grep(paths: list[str], pattern: str) -> bool:
            for p in paths:
                for f in ([Path(p)] if Path(p).is_file() else sorted(Path(p).glob('*')) if Path(p).is_dir() else []):
                    try:
                        if re.search(pattern, f.read_text(errors='replace')):
                            return True
                    except OSError:
                        continue
            return False
        apt = ['/etc/apt/sources.list', '/etc/apt/sources.list.d']
        tool = bool(self._which('foxos-tools'))
        out = [
            {'id': 'kali', 'name': 'Kali tools (Debian/Ubuntu)', 'applies': mgr == 'dpkg' and not kali_host,
             'configured': kali_host or grep(apt, r'kali\.(download|org)'),
             'description': 'Kali repository pinned so it never replaces system packages. Safer alternative: the Kali container.'},
            {'id': 'blackarch', 'name': 'BlackArch (Arch)', 'applies': mgr == 'pacman',
             'configured': grep(['/etc/pacman.conf'], r'^\[blackarch\]'),
             'description': '2,800+ tools via the official strap.sh, checksum-verified.'},
            {'id': 'wazuh', 'name': 'Wazuh security platform', 'applies': mgr in ('dpkg', 'rpm'),
             'configured': grep(apt + ['/etc/yum.repos.d'], r'packages\.wazuh\.com'),
             'description': 'XDR/SIEM agent and manager packages, signing key fingerprint-pinned.'},
        ]
        for r in out:
            r['command'] = f'foxos-tools repo add {r["id"]}' if tool else ''
        return out
