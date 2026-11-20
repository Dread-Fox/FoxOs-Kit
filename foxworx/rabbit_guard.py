"""Guardrails for model-constructed command lines (Rabbit Hole installed-CLI mode).

This is a *policy check on the argument vector*, applied before a delegated
command is approved and again immediately before it is launched. It blocks:

* executables that are interpreters, command launchers, destructive, privilege
  or system-configuration tools, package managers, or remote-shell utilities;
* known option patterns that execute code, read target lists from files, write
  arbitrary files or turn a scanner into a shell;
* references to the workbench's own secrets (config file, state directory) and
  common credential stores;
* any host name, IP address, CIDR block or URL in the arguments that is outside
  the engagement scope, plus any reference to the workbench's own listener.

It is NOT a sandbox. A permitted tool can still resolve names to other
addresses, follow redirects, load plugins or spawn children, and the process
keeps the service account's file and network access. See docs/RABBIT-HOLE.md.
"""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from .scope import allowed, validate_rule

# Interpreters and generic command launchers turn "one tool" into "any program".
_LAUNCHERS = {
    'sh', 'bash', 'dash', 'zsh', 'ksh', 'mksh', 'csh', 'tcsh', 'fish', 'busybox', 'toybox',
    'python', 'python2', 'python3', 'pypy', 'pypy3', 'perl', 'ruby', 'irb', 'php', 'node', 'nodejs', 'deno', 'bun',
    'lua', 'luajit', 'tclsh', 'wish', 'expect', 'awk', 'gawk', 'mawk', 'nawk', 'sed', 'jshell', 'java', 'R', 'Rscript',
    'env', 'xargs', 'nohup', 'setsid', 'nice', 'ionice', 'timeout', 'stdbuf', 'chroot', 'unshare', 'nsenter',
    'script', 'screen', 'tmux', 'watch', 'at', 'batch', 'crontab', 'find', 'parallel', 'make', 'gdb', 'strace',
    'vi', 'vim', 'nvim', 'nano', 'emacs', 'ed', 'less', 'more', 'man',
}
_DESTRUCTIVE = {
    'rm', 'rmdir', 'unlink', 'shred', 'wipe', 'srm', 'dd', 'wipefs', 'fdisk', 'sfdisk', 'gdisk', 'parted',
    'truncate', 'mv', 'cp', 'ln', 'install', 'tee', 'chmod', 'chown', 'chgrp', 'chattr', 'setfacl',
    'kill', 'killall', 'pkill', 'mkswap', 'swapoff', 'swapon', 'losetup', 'cryptsetup',
}
_PRIVILEGE_AND_SYSTEM = {
    'sudo', 'su', 'doas', 'pkexec', 'runuser', 'setpriv', 'capsh', 'setcap', 'passwd', 'chpasswd', 'useradd',
    'usermod', 'userdel', 'groupadd', 'groupmod', 'visudo', 'systemctl', 'service', 'journalctl', 'loginctl',
    'reboot', 'shutdown', 'halt', 'poweroff', 'init', 'telinit', 'iptables', 'ip6tables', 'nft', 'ufw',
    'firewall-cmd', 'ip', 'ifconfig', 'route', 'iw', 'iwconfig', 'nmcli', 'airmon-ng', 'macchanger', 'modprobe',
    'insmod', 'rmmod', 'sysctl', 'mount', 'umount', 'docker', 'podman', 'kubectl',
}
_SOFTWARE_AND_REMOTE = {
    'apt', 'apt-get', 'aptitude', 'dpkg', 'snap', 'flatpak', 'pip', 'pip3', 'pipx', 'npm', 'npx', 'yarn', 'pnpm',
    'gem', 'cargo', 'go', 'git', 'ssh', 'scp', 'sftp', 'rsync', 'sshpass', 'telnet', 'ftp', 'nc', 'ncat', 'netcat',
    'socat', 'msfconsole', 'msfvenom', 'msfdb', 'codex', 'claude',
}
DENIED_EXECUTABLES = frozenset(_LAUNCHERS | _DESTRUCTIVE | _PRIVILEGE_AND_SYSTEM | _SOFTWARE_AND_REMOTE)
_DENIED_PATTERNS = (re.compile(r'(python|pypy|perl|ruby|php|lua|node)[0-9.]*'), re.compile(r'mkfs(\..+)?'))

# Per-tool options that read target lists from files (scope bypass), execute
# code, upload/write files, or open shells. Compared on the option name.
DENIED_OPTIONS: dict[str, frozenset[str]] = {
    'nmap': frozenset({'-iL', '-iR', '--resume', '--script-args-file', '--datadir', '--servicedb', '--versiondb'}),
    'masscan': frozenset({'-iL', '--includefile', '-c', '--conf', '--resume'}),
    'rustscan': frozenset({'-a', '--addresses-file'}),
    'sqlmap': frozenset({'--os-shell', '--os-cmd', '--os-pwn', '--os-smbrelay', '--os-bof', '--priv-esc',
                         '--file-write', '--file-dest', '--sql-shell', '--eval', '--shell', '--wizard',
                         '-m', '--answers', '--tamper', '--preprocess', '--postprocess'}),
    'curl': frozenset({'-K', '--config', '-T', '--upload-file', '--libcurl', '--unix-socket', '--abstract-unix-socket'}),
    'wget': frozenset({'-e', '--execute', '-i', '--input-file', '--post-file', '--body-file', '--use-askpass'}),
    'nuclei': frozenset({'-l', '-list', '-im', '-input-mode', '-code', '-headless', '-sb', '-tl'}),
    'httpx': frozenset({'-l', '-list', '-r', '-resolvers'}),
    'naabu': frozenset({'-l', '-list'}),
    'dnsx': frozenset({'-l', '-list'}),
    'katana': frozenset({'-l', '-list'}),
    'ffuf': frozenset({'-request', '-input-cmd', '-input-shell'}),
    'hydra': frozenset({'-M'}),
    'medusa': frozenset({'-H'}),
    'nikto': frozenset({'-config', '-Plugins'}),
    'whatweb': frozenset({'-i', '--input-file', '--plugins', '-p'}),
    'wpscan': frozenset({'--url-file'}),
    'theHarvester': frozenset({'-f'}),
}
# Option values that are destructive regardless of the tool.
_NMAP_DESTRUCTIVE_SCRIPTS = re.compile(r'(^|[,+ ])(dos|exploit|malware)($|[,+ ])', re.I)

_HOSTNAME = re.compile(r'(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,23}', re.I)
_IPV4_RANGE = re.compile(r'\d{1,3}(?:-\d{1,3})?(?:\.(?:\d{1,3}(?:-\d{1,3})?|\*)){3}')
# Last labels that almost always mean "file", not "domain".
_FILE_SUFFIXES = frozenset({
    'txt', 'lst', 'list', 'xml', 'json', 'jsonl', 'html', 'htm', 'csv', 'tsv', 'log', 'out', 'gnmap', 'nmap',
    'conf', 'cfg', 'ini', 'yaml', 'yml', 'toml', 'py', 'sh', 'pl', 'rb', 'js', 'php', 'asp', 'aspx', 'jsp', 'gz',
    'tgz', 'zip', 'tar', 'bz2', 'xz', 'pcap', 'pcapng', 'cap', 'md', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'svg',
    'nse', 'lua', 'db', 'sqlite', 'sql', 'bak', 'old', 'tmp', 'key', 'pem', 'crt', 'cer', 'req', 'rules', 'dic',
    'hash', 'hashes', 'pot', 'potfile', 'hccapx', 'hc22000', 'so', 'bin', 'exe', 'dll', 'jar', 'war', 'apk',
})
_SENSITIVE_FRAGMENTS = ('/.ssh', '/.gnupg', '/.aws', '/.kube', '/.docker', '/.codex', '/.config/foxworx',
                        '/.local/share/foxworx', '/etc/shadow', '/etc/gshadow', '/etc/sudoers', '/proc/self/environ',
                        '/.bash_history', '/.zsh_history', '/.netrc', '/.git-credentials', 'settings.env')


def _executable_denied(name: str) -> bool:
    base = os.path.basename(name)
    return base in DENIED_EXECUTABLES or any(p.fullmatch(base) for p in _DENIED_PATTERNS)


def _option_name(arg: str) -> str:
    return arg.split('=', 1)[0] if arg.startswith('-') else ''


def _candidates(arg: str) -> list[str]:
    """Split one argv element into pieces that might name a network destination."""
    value = arg.split('=', 1)[1] if arg.startswith('-') and '=' in arg else arg
    if value.startswith('-'):
        return []
    pieces = []
    for piece in re.split(r'[,\s]+', value):
        piece = piece.strip().strip('\'"')
        if piece:
            pieces.append(piece)
    return pieces


def _strip_endpoint(piece: str) -> tuple[str, int | None]:
    """Turn ``user@host:port`` / ``[v6]:port`` into ``(host, port)``."""
    if '@' in piece:
        piece = piece.rsplit('@', 1)[1]
    if piece.startswith('['):
        host, _, rest = piece[1:].partition(']')
        port = rest[1:] if rest.startswith(':') else ''
        return host, int(port) if port.isdigit() else None
    if piece.count(':') == 1:
        host, port = piece.split(':')
        return host, int(port) if port.isdigit() else None
    return piece, None


def _is_loopback(host: str) -> bool:
    host = host.lower().rstrip('.')
    if host in ('localhost', 'localhost.localdomain', 'ip6-localhost'):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _network_in_scope(network: ipaddress._BaseNetwork, includes: list[str], excludes: list[str]) -> bool:
    def networks(rules):
        out = []
        for rule in rules:
            try:
                rule = validate_rule(rule)
                out.append(ipaddress.ip_network(rule, strict=False))
            except ValueError:
                continue
        return out
    inside = any(network.version == n.version and network.subnet_of(n) for n in networks(includes))
    clash = any(network.version == n.version and network.overlaps(n) for n in networks(excludes))
    return inside and not clash


def check_command(tool: str, args: list[str], includes: list[str], excludes: list[str], *,
                  workbench_port: int | None = None, protected_paths: tuple[str, ...] = ()) -> list[str]:
    """Return human-readable reasons to block ``tool args``; an empty list means allowed."""
    problems: list[str] = []
    base = os.path.basename(tool)
    if _executable_denied(base):
        problems.append(f'{base} is on the autonomous-mode deny list (interpreter, launcher, destructive, '
                        'privilege, system or remote-shell tool)')
    denied_options = DENIED_OPTIONS.get(base, frozenset())
    protected = tuple(p for p in protected_paths if p)
    for index, arg in enumerate(args):
        option = _option_name(arg)
        if option and option in denied_options:
            problems.append(f'{base} option {option} is not permitted autonomously (reads target files, executes code or writes files)')
        script_value = (base == 'nmap' and
                        ((index > 0 and args[index - 1] == '--script') or arg.startswith('--script=')))
        if script_value:
            script = arg.split('=', 1)[1] if arg.startswith('--script=') else arg
            if _NMAP_DESTRUCTIVE_SCRIPTS.search(script) or '/' in script or script.endswith('.nse'):
                problems.append('nmap dos/exploit/malware categories and custom script paths are not permitted autonomously')
        lowered = arg.lower()
        if 'file://' in lowered:
            problems.append('file:// URLs read local files and are not permitted')
        if any(fragment in lowered for fragment in _SENSITIVE_FRAGMENTS) or any(p in arg for p in protected):
            problems.append('arguments may not reference credential stores or the workbench configuration/state')
        for piece in _candidates(arg):
            problems.extend(_destination_problems(piece, includes, excludes, workbench_port))
    return list(dict.fromkeys(problems))


def _destination_problems(piece: str, includes, excludes, workbench_port) -> list[str]:
    if '://' in piece:
        parts = urlsplit(piece)
        if parts.scheme.lower() == 'file':
            return []  # reported separately
        try:
            host, port = parts.hostname or '', parts.port
        except ValueError:
            return [f'malformed URL {piece[:80]!r}']
        if not host:
            return []
        if _is_loopback(host) and workbench_port and port == workbench_port:
            return ['the workbench listener itself is never a permitted target']
        target = piece if parts.scheme.lower() in ('http', 'https') else host
        if not (allowed(target, includes, excludes) or allowed(host, includes, excludes)):
            return [f'{host} is outside the engagement scope']
        return []
    if piece.startswith(('/', './', '../', '~')):
        return []
    if '/' in piece:
        try:
            network = ipaddress.ip_network(piece, strict=False)
        except ValueError:
            return []
        if not _network_in_scope(network, includes, excludes):
            return [f'network {network} is not entirely inside the engagement scope']
        return []
    host, port = _strip_endpoint(piece)
    if _is_loopback(host) and workbench_port and port == workbench_port:
        return ['the workbench listener itself is never a permitted target']
    if _IPV4_RANGE.fullmatch(host) and ('-' in host or '*' in host):
        return [f'IP range syntax {host!r} is not permitted autonomously; list in-scope addresses individually']
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is None:
        if not _HOSTNAME.fullmatch(host) or host.rsplit('.', 1)[-1].lower() in _FILE_SUFFIXES:
            return []
    if not allowed(host, includes, excludes):
        return [f'{host} is outside the engagement scope']
    return []


def protected_paths(cfg) -> tuple[str, ...]:
    """Absolute paths of this workbench's own secrets and state."""
    values = []
    for path in (getattr(cfg, 'config_path', None), getattr(cfg, 'state', None)):
        if path:
            try:
                values.append(str(Path(path).expanduser().resolve()))
            except OSError:
                values.append(str(path))
    return tuple(values)
