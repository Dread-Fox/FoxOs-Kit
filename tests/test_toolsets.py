"""Toolkit-aware catalog: Kali/Parrot (dpkg), BlackArch (pacman), Fedora Security Lab (rpm), origins, inventory."""
import os
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from foxworx.app import create_app
from foxworx.toolsets import ToolsetIndex, parse_apt_depends, parse_dnf_group, parse_groups

APT_DEPENDS = """kali-tools-passwords
  Depends: kali-tools-gpu
  Depends: hashcat
  Depends: john
 |Depends: hydra
    hydra-alt
kali-tools-sniffing-spoofing
  Depends: wireshark
  Depends: tcpdump
kali-tools-web
  Depends: wireshark
  Depends: sqlmap
kali-tools-top10
  Depends: hydra
"""


def fake_runner(outputs):
    def run(argv, timeout=20):
        key = ' '.join(argv[:3])
        for prefix, text in outputs.items():
            if key.startswith(prefix):
                return text
        return ''
    return run


def write(path: Path, text: str = '', mode: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mode is not None:
        path.chmod(mode)
    return path


def test_parsers():
    deps = parse_apt_depends(APT_DEPENDS)
    assert deps['kali-tools-passwords'] == ['kali-tools-gpu', 'hashcat', 'john', 'hydra', 'hydra-alt']
    assert parse_groups('blackarch-scanner nmap\nblackarch-webapp sqlmap\n') == [('blackarch-scanner', 'nmap'), ('blackarch-webapp', 'sqlmap')]
    info = 'Group: Security Lab\n Description: tools\n Mandatory packages:\n   nmap\n   wireshark-cli\n Optional packages:\n   hydra\n'
    assert parse_dnf_group(info) == ['nmap', 'wireshark-cli', 'hydra']


def dpkg_root(tmp_path):
    root = tmp_path / 'root'
    write(root / 'var/lib/dpkg/status', 'x')
    for pkg, files in {'hashcat': ['/usr/bin/hashcat'], 'wireshark': ['/usr/bin/wireshark'], 'sqlmap': ['/usr/bin/sqlmap'],
                       'metasploit-framework': ['/usr/share/metasploit-framework/msfconsole', '/usr/share/metasploit-framework/msfdb'],
                       'coreutils': ['/usr/bin/ls']}.items():
        write(root / f'var/lib/dpkg/info/{pkg}.list', '\n'.join(files) + '\n')
    for exe in ('hashcat', 'wireshark', 'sqlmap', 'ls'):
        write(root / 'usr/bin' / exe, '#!/bin/sh\n', 0o755)
    for exe in ('msfconsole', 'msfdb'):
        write(root / 'usr/share/metasploit-framework' / exe, '#!/bin/sh\n', 0o755)
        os.symlink(root / 'usr/share/metasploit-framework' / exe, root / 'usr/bin' / exe)
    menu = root / 'usr/share/kali-menu/applications'
    write(menu / 'org.wireshark.Wireshark.desktop', '[Desktop Entry]\nExec=/usr/share/kali-menu/helper-scripts/wireshark.sh %f\n'
          'Categories=kali-network-sniffing;kali-discovery;\n')
    write(menu / 'kali-msf.desktop', '[Desktop Entry]\nExec=/usr/share/kali-menu/exec-in-shell "msfdb run"\nCategories=kali-exploitation-tools;\n')
    runner = fake_runner({
        '/usr/bin/dpkg-query -W': 'hashcat\tii \t6.2\tworld fastest cracker\nwireshark\tii \t4.4\tnetwork analyzer\n'
                                  'sqlmap\tii \t1.8\tSQL injection tool\nmetasploit-framework\tii \t6.4\tpentest framework\n'
                                  'kali-tools-passwords\tii \t1\tmeta\ncoreutils\tii \t9\tbasic utils\n',
        '/usr/bin/apt-cache pkgnames kali-tools-': 'kali-tools-passwords\nkali-tools-sniffing-spoofing\nkali-tools-web\nkali-tools-top10\n',
        '/usr/bin/apt-cache depends': APT_DEPENDS})
    return root, runner


def test_kali_categories_menu_and_symlink_owners(tmp_path):
    root, runner = dpkg_root(tmp_path)
    ix = ToolsetIndex(run=runner, root=str(root), home=str(tmp_path / 'home'))
    data = ix.data()
    kali = next(t for t in data['toolsets'] if t['label'] == 'Kali')
    assert kali['categories']['Password attacks'] == {'listed': 4, 'installed': 1, 'set_installed': True}
    assert kali['sets_installed'] == ['kali-tools-passwords']
    # collection category (Kali menu order picks Password attacks over others)
    assert ix.describe('/usr/bin/hashcat')['toolset_category'] == 'Kali · Password attacks'
    # menu entry wins, even when it launches a helper script
    assert ix.describe('/usr/bin/wireshark')['toolset_category'] == 'Kali · Network sniffing'
    # symlinked command -> owner of the real file; the package's menu entry (msfdb) gives msfconsole its category
    msf = ix.describe('/usr/bin/msfconsole')
    assert msf['package'] == 'metasploit-framework' and msf['toolset_category'] == 'Kali · Exploitation tools'
    assert ix.describe('/usr/bin/ls') == {**ix.describe('/usr/bin/ls'), 'package': 'coreutils', 'toolset_category': '', 'toolsets': []}
    assert ix.data() is data  # cached until the package database changes


def test_origins_and_projects(tmp_path):
    home = tmp_path / 'home'
    write(home / '.local/bin/foo', '#!/bin/sh\n# foxos-kali-export: foo from container foxos-kali\n', 0o755)
    write(home / '.local/bin/mine', '#!/bin/sh\n', 0o755)
    write(home / 'go/bin/waybackurls', 'bin', 0o755)
    write(home / 'tools/jwt_tool/jwt_tool.py', '#!/usr/bin/env python3\n')
    write(tmp_path / 'root/opt/google/chrome/chrome', 'x', 0o755)
    ix = ToolsetIndex(run=fake_runner({}), root=str(tmp_path / 'root'), home=str(home))
    assert ix.origin(str(home / '.local/bin/foo')) == 'kali-container'
    assert ix.origin(str(home / '.local/bin/mine')) == 'user'
    assert ix.origin(str(home / 'go/bin/waybackurls')) == 'go'
    assert ix.origin('/snap/bin/autopsy') == 'snap'
    names = {p['name']: p['launchers'] for p in ix.projects()}
    assert names == {'jwt_tool': ['jwt_tool.py']}  # vendor apps in /opt are skipped


def test_blackarch_groups(tmp_path):
    root = tmp_path / 'root'
    local = root / 'var/lib/pacman/local'
    write(local / 'nmap-7.95-1/files', '%FILES%\nusr/\nusr/bin/\nusr/bin/nmap\n')
    write(local / 'sqlmap-1.8-1/files', '%FILES%\nusr/bin/sqlmap\n')
    runner = fake_runner({
        '/usr/bin/pacman -Qi': 'Name            : nmap\nVersion         : 7.95-1\nDescription     : Network scanner\n\n'
                               'Name            : sqlmap\nVersion         : 1.8-1\nDescription     : SQLi\n',
        '/usr/bin/pacman -Sg': 'blackarch-scanner nmap\nblackarch-webapp sqlmap\nblackarch-scanner masscan\n'})
    ix = ToolsetIndex(run=runner, root=str(root), home=str(tmp_path))
    ba = ix.data()['toolsets'][0]
    assert ba['label'] == 'BlackArch' and ba['categories']['Scanner'] == {'listed': 2, 'installed': 1, 'set_installed': False}
    d = ix.describe('/usr/bin/nmap')
    assert d['package'] == 'nmap' and d['toolset_category'] == 'BlackArch · Scanner' and d['summary'] == 'Network scanner'


def test_fedora_security_lab(tmp_path):
    root = tmp_path / 'root'
    write(root / 'var/lib/rpm/rpmdb.sqlite', 'x')
    runner = fake_runner({
        '/usr/bin/rpm -qa --qf': 'nmap\t7.92-1\tNetwork exploration tool\n',
        '/usr/bin/dnf -q group': 'Group: Security Lab\n Mandatory packages:\n   nmap\n   hydra\n'})
    # the second rpm query (file -> package) shares the prefix; give it its own answer
    def run(argv, timeout=20):
        if argv[:3] == ['/usr/bin/rpm', '-qa', '--qf'] and 'FILENAMES' in argv[3]:
            return '/usr/bin/nmap\tnmap\n/usr/share/doc/nmap/README\tnmap\n'
        return runner(argv, timeout)
    ix = ToolsetIndex(run=run, root=str(root), home=str(tmp_path))
    d = ix.describe('/usr/bin/nmap')
    assert d['package'] == 'nmap' and d['toolset_category'] == 'Fedora Security Lab · Security lab'
    assert ix.data()['toolsets'][0]['installed'] == 1


def test_registry_and_inventory_api(cfg, tmp_path):
    tools = tmp_path / 'tools'
    # names that can't exist on a real system, so the system folders (searched first) never shadow them
    write(tools / 'fxtest-cracker', '#!/bin/sh\n', 0o755)
    write(tools / 'fxtest-brandnew', '#!/bin/sh\n', 0o755)
    root, runner = dpkg_root(tmp_path)
    c = replace(cfg, extra_paths=(str(tools),))
    app = create_app(c, _allow_root_for_tests=True)
    index = ToolsetIndex(run=runner, root=str(root), home=str(tmp_path / 'home'))
    index.data()['owners'][str(tools / 'fxtest-cracker')] = 'hashcat'  # this test's tool belongs to the hashcat package
    app.state.registry.toolsets = index
    with TestClient(app, base_url=c.origin, client=('127.0.0.1', 50000)) as client:  # startup scans the registry
        reg = app.state.registry
        hc = reg.items['fxtest-cracker']
        assert hc['package'] == 'hashcat' and {'toolset': 'Kali', 'category': 'Password attacks'} in hc['toolsets']
        assert hc['category'] == 'Kali · Password attacks'
        assert reg.items['fxtest-brandnew']['category'] == 'System / additional'
        client.post('/api/login', json={'token': c.local_token}, headers={'Origin': c.origin})
        inv = client.get('/api/tools/inventory').json()
    assert inv['manager'] == 'dpkg' and inv['toolsets'][0]['label'] == 'Kali'
    assert any(d['dir'] == str(tools) and d['tools'] >= 2 and d['kind'] == 'configured' for d in inv['search_dirs'])
    assert inv['totals']['executables'] >= 2 and 'package' in inv['by_origin']


def test_auto_tool_paths_follow_system_folders(cfg, tmp_path, monkeypatch):
    home = tmp_path / 'home'
    (home / 'go/bin').mkdir(parents=True)
    monkeypatch.setenv('HOME', str(home))
    on = replace(cfg, auto_tool_paths=True).tool_path.split(':')
    assert on[:6] == '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'.split(':')
    assert str(home / 'go/bin') in on and str(home / '.cargo/bin') not in on  # only existing folders
    assert replace(cfg, auto_tool_paths=False).tool_path == '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
