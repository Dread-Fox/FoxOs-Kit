"""Dens through the real HTTP API: two full FOXWORX apps form a den and collaborate (direct sockets, no Tor)."""
import socket
import time
from dataclasses import replace

from fastapi.testclient import TestClient

from foxworx.app import create_app


def free_port() -> int:
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def node_cfg(cfg, tmp_path, name):
    port = free_port()
    return replace(cfg, state=tmp_path / name, den_enabled=True, den_tor=False, den_listen_port=port,
                   den_address=f'direct:127.0.0.1:{port}', den_label=name)


def signed_in(app, cfg):
    c = TestClient(app, base_url=cfg.origin, client=('127.0.0.1', 50000))
    c.__enter__()
    assert c.post('/api/login', json={'token': cfg.local_token}, headers={'Origin': cfg.origin}).status_code == 200
    c.headers.update({'Origin': cfg.origin, 'X-Foxworx-CSRF': c.get('/api/session').json()['csrf']})
    return c


def wait(fn, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError('timed out')


def test_two_workbenches_share_a_den(cfg, tmp_path):
    ca, cb = node_cfg(cfg, tmp_path, 'Alpha'), node_cfg(cfg, tmp_path, 'Bravo')
    a, b = signed_in(create_app(ca, _allow_root_for_tests=True), ca), signed_in(create_app(cb, _allow_root_for_tests=True), cb)
    try:
        info = a.get('/api/dens').json()
        assert info['enabled'] and info['node']['label'] == 'Alpha' and info['node']['tor'] == 'direct'
        den = a.post('/api/dens', json={'name': 'Recon pack'}).json()['id']
        code = a.post(f'/api/dens/{den}/invite', json={'hours': 2}).json()['code']
        joined = b.post('/api/dens/join', json={'code': code})
        assert joined.status_code == 200, joined.text
        a.post(f'/api/dens/{den}/post', json={'kind': 'chat', 'text': 'welcome <script>alert(1)</script>'})
        items = wait(lambda: [i for i in b.get(f'/api/dens/{den}/feed').json()['items'] if i['kind'] == 'chat'])
        assert items[0]['text'] == 'welcome <script>alert(1)</script>'  # stored as text; the UI renders it as text
        assert items[0]['author_label'] == 'Alpha'
        # Only the allowed post kinds go through the API.
        assert b.post(f'/api/dens/{den}/post', json={'kind': 'agent_answer', 'reply_to': '0' * 64}).status_code == 400
        # File round trip with the upload size rules of the workbench.
        up = b.post(f'/api/dens/{den}/files', content=b'evidence-bytes',
                    headers={'X-Filename': 'notes.txt', 'Content-Type': 'application/octet-stream'})
        assert up.status_code == 200, up.text
        fid = up.json()['file_id']
        got = wait(lambda: (r := a.get(f'/api/dens/{den}/files/{fid}')).status_code == 200 and r)
        assert got.content == b'evidence-bytes' and 'attachment' in got.headers['content-disposition']
        # Partner request lands in Alpha's inbox; Alpha drafts from the workspace (0 tokens) and answers.
        a.post(f'/api/dens/{den}/post', json={'kind': 'note', 'title': 'Lab gateway', 'body': 'gateway runs ssh on 2222'})
        q = b.post(f'/api/dens/{den}/request', json={'qtype': 'recall', 'text': 'which port is ssh on the gateway?'}).json()
        inbox = wait(lambda: [i for i in a.get(f'/api/dens/{den}/feed').json()['items'] if i['id'] == q['id'] and i['for_me']])
        assert inbox[0]['status'] == 'pending'
        draft = a.post(f"/api/dens/{den}/inbox/{q['id']}/draft", json={'source': 'workspace'}).json()
        assert '2222' in draft['text'] and draft['tokens'] == 0
        a.post(f"/api/dens/{den}/inbox/{q['id']}/answer", json={'text': draft['text'], 'source': 'workspace'})
        wait(lambda: [i for i in b.get(f'/api/dens/{den}/feed').json()['items'] if i['kind'] == 'agent_answer'])
        # No model configured: an AI draft is refused cleanly instead of spending anything.
        q2 = b.post(f'/api/dens/{den}/request', json={'qtype': 'ask', 'text': 'quick tip?'}).json()
        wait(lambda: [i for i in a.get(f'/api/dens/{den}/feed').json()['items'] if i['id'] == q2['id']])
        assert a.post(f"/api/dens/{den}/inbox/{q2['id']}/draft", json={'source': 'model'}).status_code >= 400
        # Membership view and safety words agree on both sides.
        ma = {m['id']: m for m in a.get(f'/api/dens/{den}').json()['members']}
        mb = {m['id']: m for m in b.get(f'/api/dens/{den}').json()['members']}
        a_id, b_id = a.get('/api/dens').json()['node']['id'], b.get('/api/dens').json()['node']['id']
        assert ma[b_id]['safety_words'] == mb[a_id]['safety_words'] and ma[b_id]['online']
        # Mutations need the CSRF header.
        assert TestClient(a.app, base_url=ca.origin).post('/api/dens', json={'name': 'x'}).status_code in (401, 403)
    finally:
        a.__exit__(None, None, None)
        b.__exit__(None, None, None)


def test_dens_off_by_setting(cfg):
    app = create_app(cfg, _allow_root_for_tests=True)
    with TestClient(app, base_url=cfg.origin, client=('127.0.0.1', 50000)) as c:
        c.post('/api/login', json={'token': cfg.local_token}, headers={'Origin': cfg.origin})
        assert c.get('/api/dens').json()['enabled'] is False
