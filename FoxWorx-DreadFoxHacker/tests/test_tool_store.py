"""Tool store: keyword search, validated install paths, verified downloads, recognition after install."""
import hashlib
import io
import json
import shutil
import time
import zipfile
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from foxworx.app import create_app
from foxworx.registry import Registry
from foxworx.tool_store import ToolStore


class FakeIndex:
    def __init__(self, installed=(), tags=None):
        self._installed = {p: '' for p in installed}
        self._tags = tags or {}

    def manager(self):
        return 'dpkg'

    def data(self):
        return {'installed': self._installed, 'tags': self._tags}


APT_SEARCH = ('knockpy - subdomain scanner\nlibsubdomain-dev - development files for subdomain parsing\n'
              'subjack - subdomain takeover tool\nsubfinder - passive subdomain discovery\n')


def runner(argv, timeout=20):
    if argv[:2] == ['/usr/bin/apt-cache', 'search']:
        return APT_SEARCH
    if argv[:2] == ['/usr/bin/apt-cache', 'show']:
        return 'Package: subjack\nDescription: Subdomain takeover checker\n'
    return ''


@pytest.fixture
def store(cfg, tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    reg = Registry(replace(cfg, extra_paths=(str(tmp_path / 'bin'),)))
    (tmp_path / 'bin').mkdir()
    index = FakeIndex(installed=['subfinder'], tags={'subjack': [('Kali', 'Information gathering')]})
    st = ToolStore(reg.cfg, reg, index=index, run=runner)
    # Hermetic: only the test's own folder holds pipx/go, never the machine's real ones.
    st._which = lambda name: shutil.which(name, path=str(tmp_path / 'bin')) or ''
    def no_network(url):
        raise OSError('tests never touch the network')
    st.resolve = no_network  # exercise the API fallback with the fake fetch below
    return st


def test_search_ranks_tools_and_drops_libraries(store):
    out = store.search('subdomain')
    names = [r['name'] for r in out['results']]
    assert 'libsubdomain-dev' not in names                      # libraries/headers are not tools
    assert names.index('subjack') < names.index('knockpy')      # security toolset packages first
    assert {'subfinder', 'alterx', 'assetfinder'} <= set(names)  # vetted catalog entries (PD, Go)
    pkg = next(r for r in out['results'] if r['id'] == 'pkg:subjack')
    assert pkg['category'] == 'Kali · Information gathering' and pkg['terminal_command'] == 'sudo apt install subjack'
    assert next(r for r in out['results'] if r['id'] == 'pkg:subfinder')['installed'] is True


def test_input_is_validated(store):
    for bad in ('', 'x' * 81, 'nmap; rm -rf /', '$(id)'):
        with pytest.raises(ValueError):
            store.search(bad)
    with pytest.raises(ValueError):
        store.install('pkg:nmap; rm -rf /')
    with pytest.raises(ValueError):
        store.install('pipx:not-in-catalog')
    with pytest.raises(ValueError):
        store.install('go:subfinder')  # catalog source must match
    assert store.install('pkg:subjack') == {'mode': 'terminal', 'command': 'sudo apt install subjack',
                                            'message': 'System packages need your password: review the command in the terminal and press Enter.'}


def pd_release(binary=b'\x7fELF fake dnsx', good=True):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('dnsx', binary)
    blob = buf.getvalue()
    digest = hashlib.sha256(blob).hexdigest() if good else '0' * 64
    arch = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(__import__('os').uname().machine, 'amd64')
    files = {'https://api.github.com/repos/projectdiscovery/dnsx/releases/latest': json.dumps({'tag_name': 'v1.3.1'}).encode(),
             f'https://github.com/projectdiscovery/dnsx/releases/download/v1.3.1/dnsx_1.3.1_linux_{arch}.zip': blob,
             'https://github.com/projectdiscovery/dnsx/releases/download/v1.3.1/dnsx_1.3.1_checksums.txt':
                 f'{digest}  dnsx_1.3.1_linux_{arch}.zip\n'.encode()}
    return lambda url: files[url]


def wait_task(store, task_id):
    for _ in range(200):
        t = next(t for t in store.list_tasks() if t['id'] == task_id)
        if t['status'] != 'running':
            return t
        time.sleep(0.02)
    raise AssertionError('task did not finish')


def test_projectdiscovery_install_is_verified_and_recognised(store, tmp_path):
    seen = []
    store.on_installed = seen.append
    store.fetch = pd_release()
    t = wait_task(store, store.install('pd:dnsx')['task']['id'])
    dest = tmp_path / 'home/.local/bin/dnsx'
    assert t['status'] == 'installed' and dest.read_bytes() == b'\x7fELF fake dnsx' and dest.stat().st_mode & 0o111
    assert 'Checksum verified' in t['log'] and seen == ['dnsx']


def test_tampered_download_is_refused(store, tmp_path):
    store.fetch = pd_release(good=False)
    t = wait_task(store, store.install('pd:dnsx')['task']['id'])
    assert t['status'] == 'failed' and 'checksum mismatch' in t['log']
    assert not (tmp_path / 'home/.local/bin/dnsx').exists()


def test_pipx_runs_exact_command_or_asks_for_pipx(store, tmp_path):
    assert store.install('pipx:wafw00f')['mode'] == 'terminal'   # no pipx yet -> install it first
    log = tmp_path / 'pipx-args.txt'
    pipx = tmp_path / 'bin/pipx'
    pipx.write_text(f'#!/bin/sh\necho "$@" > {log}\necho installed\n')
    pipx.chmod(0o755)
    t = wait_task(store, store.install('pipx:wafw00f')['task']['id'])
    assert t['status'] == 'installed' and log.read_text().strip() == 'install --force wafw00f'


def test_store_api_and_agent_isolation(cfg, tmp_path):
    app = create_app(cfg, _allow_root_for_tests=True)
    app.state.tool_store.index = FakeIndex()
    app.state.tool_store.run = runner
    with TestClient(app, base_url=cfg.origin, client=('127.0.0.1', 50000)) as c:
        c.post('/api/login', json={'token': cfg.local_token}, headers={'Origin': cfg.origin})
        csrf = c.get('/api/session').json()['csrf']
        h = {'Origin': cfg.origin, 'X-Foxworx-CSRF': csrf}
        assert any(r['name'] == 'knockpy' for r in c.get('/api/store/search', params={'q': 'subdomain'}).json()['results'])
        assert c.get('/api/store/search', params={'q': '$(id)'}).status_code == 400
        assert c.post('/api/store/install', json={'id': 'pkg:knockpy'}).status_code in (401, 403)  # CSRF required
        r = c.post('/api/store/install', json={'id': 'pkg:knockpy'}, headers=h).json()
        assert r == {'mode': 'terminal', 'command': 'sudo apt install knockpy',
                     'message': 'System packages need your password: review the command in the terminal and press Enter.'}
        assert c.get('/api/store/tasks').json() == {'tasks': []}
        assert {x['id'] for x in c.get('/api/store/repos').json()['repos']} == {'kali', 'blackarch', 'wazuh'}
        assert any(e['action'] == 'tool-store-install' for e in app.state.store.events('local-operator'))
    from foxworx.ai import TOOLS
    assert not any('store' in t['name'] or 'install' in t['name'] for t in TOOLS)  # the agent can't install tools


def test_latest_version_prefers_redirect(store):
    store.resolve = lambda url: 'https://github.com/projectdiscovery/dnsx/releases/tag/v1.9.9'
    store.fetch = lambda url: (_ for _ in ()).throw(AssertionError('API must not be called'))
    assert store._latest_version('dnsx') == '1.9.9'
