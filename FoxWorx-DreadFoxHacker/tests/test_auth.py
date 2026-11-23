"""v3.4.0: authentication is on by default; open mode is an explicit loopback-only opt-in."""
from dataclasses import replace
import re
import sqlite3
import time

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from foxworx.accounts import Accounts, hash_password, verify_password
from foxworx.app import create_app
from foxworx.auth import SESSION_COOKIE
from foxworx.cli import initialize, main, write_settings
from foxworx.config import OPEN_MODE_ACK, Settings, hash_setup_token
from foxworx.store import Store

PUBLIC = 'https://workbench.example.test'
LOOP = ('127.0.0.1', 50000)


def app_client(cfg, base=None, client=LOOP):
    return TestClient(create_app(cfg, _allow_root_for_tests=True), base_url=base or cfg.origin, client=client)


def sign_in(c, cfg, **body):
    body = body or {'token': cfg.local_token}
    r = c.post('/api/login', json=body, headers={'Origin': cfg.origin})
    assert r.status_code == 200, r.text
    csrf = c.get('/api/session').json()['csrf']
    return {'Origin': cfg.origin, 'X-Foxworx-CSRF': csrf}


def opened(cfg, **changes):
    return replace(cfg, mode='open', open_mode_ack=OPEN_MODE_ACK, local_token='', **changes)


# ---- defaults ---------------------------------------------------------------

def test_fresh_install_requires_login_and_stores_only_token_hash(tmp_path, monkeypatch, capsys):
    for k in ('FOXWORX_AUTH_MODE', 'FOXWORX_LOCAL_TOKEN', 'FOXWORX_LOCAL_TOKEN_HASH', 'FOXWORX_OPEN_MODE_ACK'):
        monkeypatch.delenv(k, raising=False)
    path = tmp_path / 'settings.env'
    assert main(['configure', '--config', str(path), '--upgrade', '--profile', 'pi']) == 0
    printed = capsys.readouterr().out
    values = dotenv_values(path, interpolate=False)
    assert values['FOXWORX_AUTH_MODE'] == 'local'
    assert values['FOXWORX_BIND_HOST'] == '127.0.0.1'
    assert values['FOXWORX_LOCAL_TOKEN'] == ''
    token = next(line.strip() for line in printed.splitlines()
                 if re.fullmatch(r'[A-Za-z0-9_-]{40,}', line.strip()) and set(line.strip()) != {'='})
    assert values['FOXWORX_LOCAL_TOKEN_HASH'] == hash_setup_token(token)
    assert token not in path.read_text()  # Shown once, never stored in plaintext.
    cfg = Settings.load(path)
    assert cfg.mode == 'local' and cfg.check_setup_token(token) and not cfg.check_setup_token('x' * 43)
    # The plaintext cannot be re-displayed later.
    assert main(['configure', '--config', str(path), '--show-token']) == 1


def test_settings_default_mode_is_local(tmp_path):
    assert Settings(state=tmp_path, secret='s' * 48, local_token='t' * 48).mode == 'local'
    with pytest.raises(ValueError):
        Settings(state=tmp_path, secret='s' * 48, local_token='').validate()  # login secret required


# ---- every surface rejects anonymous callers --------------------------------

def test_unauthenticated_ui_redirects_to_sign_in(cfg):
    with app_client(cfg) as c:
        r = c.get('/', follow_redirects=False)
        assert r.status_code == 303 and r.headers['location'] == '/login'
        assert c.get('/login').status_code == 200
        sign_in(c, cfg)
        assert c.get('/', follow_redirects=False).status_code == 200
        assert c.get('/login', follow_redirects=False).status_code == 303


def test_every_api_route_rejects_unauthenticated_requests(cfg):
    app = create_app(cfg, _allow_root_for_tests=True)
    checked = 0
    with TestClient(app, base_url=cfg.origin, client=LOOP) as c:
        for route in app.routes:
            path = getattr(route, 'path', '')
            if not path.startswith('/api/') or path in ('/api/auth-mode', '/api/login'):
                continue
            concrete = path.replace('{path:path}', 'x').replace('{', '').replace('}', '')
            for method in route.methods - {'HEAD', 'OPTIONS'}:
                r = c.request(method, concrete, json={} if method != 'GET' else None,
                              headers={'Origin': cfg.origin})
                assert r.status_code == 401, (method, path, r.status_code)
                checked += 1
    assert checked > 60


def test_unauthenticated_terminal_creation_and_websocket_rejected(cfg, engagement):
    with app_client(cfg) as c:
        assert c.post('/api/terminals', json={'engagement_id': engagement['id']},
                      headers={'Origin': cfg.origin}).status_code == 401
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect('/ws/terminals/abc?ticket=x', headers={'Origin': cfg.origin}) as ws:
                ws.receive_json()


@pytest.mark.skipif(__import__('os').geteuid() == 0, reason='PTY tests need a non-root account')
def test_websocket_requires_session_and_single_use_ticket(cfg, engagement):
    with app_client(cfg) as c:
        h = sign_in(c, cfg)
        term = c.post('/api/terminals', json={'engagement_id': engagement['id']}, headers=h).json()
        ticket = c.post(f"/api/terminals/{term['id']}/ticket", json={}, headers=h).json()['ticket']
        # A valid ticket is useless without the session cookie that requested it.
        c.cookies.clear()
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(f"/ws/terminals/{term['id']}?ticket={ticket}", headers={'Origin': cfg.origin}) as ws:
                ws.receive_json()


# ---- sessions, cookies, CSRF -------------------------------------------------

def test_session_cookie_flags_and_server_side_logout(cfg):
    with app_client(cfg) as c:
        r = c.post('/api/login', json={'token': cfg.local_token}, headers={'Origin': cfg.origin})
        cookie = r.headers['set-cookie'].lower()
        assert 'httponly' in cookie and 'samesite=strict' in cookie and 'max-age=28800' in cookie
        assert 'secure' not in cookie  # plain-HTTP loopback cannot carry Secure cookies
        stolen = c.cookies[SESSION_COOKIE]
        csrf = c.get('/api/session').json()['csrf']
        assert c.post('/api/logout', json={}, headers={'Origin': cfg.origin, 'X-Foxworx-CSRF': csrf}).status_code == 200
    with app_client(cfg) as c:
        c.cookies.set(SESSION_COOKIE, stolen)
        assert c.get('/api/session').status_code == 401  # revoked server-side, not just client-side


def test_secure_cookie_over_https_public_origin(cfg):
    both = replace(cfg, public_origin=PUBLIC)
    with app_client(both, base=PUBLIC) as c:
        r = c.post('/api/login', json={'token': cfg.local_token, 'remember': True},
                   headers={'Origin': PUBLIC, 'CF-Ray': 'x'})
        cookie = r.headers['set-cookie'].lower()
        assert 'secure' in cookie and 'httponly' in cookie and 'max-age=604800' in cookie


def test_sessions_expire(cfg):
    with app_client(cfg) as c:
        sign_in(c, cfg)
        assert c.get('/api/session').status_code == 200
        with sqlite3.connect(cfg.state / 'workbench-v2.sqlite3') as db:
            db.execute('UPDATE sessions SET expires=?', (time.time() - 1,))
        assert c.get('/api/session').status_code == 401


def test_csrf_is_bound_to_the_session(cfg):
    with app_client(cfg) as a, app_client(cfg) as b:
        sign_in(a, cfg)
        sign_in(b, cfg)
        csrf_a = a.get('/api/session').json()['csrf']
        csrf_b = b.get('/api/session').json()['csrf']
        assert csrf_a != csrf_b
        assert b.post('/api/tools/refresh', json={}, headers={'Origin': cfg.origin, 'X-Foxworx-CSRF': csrf_a}).status_code == 403
        assert b.post('/api/tools/refresh', json={}, headers={'Origin': cfg.origin}).status_code == 403
        assert b.post('/api/tools/refresh', json={}, headers={'Origin': 'https://evil.test', 'X-Foxworx-CSRF': csrf_b}).status_code == 403


def test_login_rate_limited(cfg):
    with app_client(cfg) as c:
        for _ in range(8):
            assert c.post('/api/login', json={'token': 'wrong' * 10}, headers={'Origin': cfg.origin}).status_code == 401
        r = c.post('/api/login', json={'token': cfg.local_token}, headers={'Origin': cfg.origin})
        assert r.status_code == 429  # even the right secret waits out the window
        events = Store(cfg.state).events('system:auth')
        assert sum(e['action'] == 'login-failed' for e in events) == 9


def test_login_requires_same_origin(cfg):
    with app_client(cfg) as c:
        assert c.post('/api/login', json={'token': cfg.local_token}, headers={'Origin': 'https://evil.test'}).status_code == 403
        assert c.post('/api/login', json={'token': cfg.local_token}).status_code == 403


# ---- per-operator accounts --------------------------------------------------

def test_password_hashing():
    encoded = hash_password('correct horse battery')
    assert encoded.startswith('scrypt$') and 'correct' not in encoded
    assert verify_password('correct horse battery', encoded)
    assert not verify_password('correct horse batterz', encoded)
    assert not verify_password('anything', 'garbage')


def test_operator_accounts_are_isolated_and_revocable(cfg):
    with app_client(cfg) as c:
        accounts: Accounts = c.app.state.accounts
        accounts.add('alice', 'alice-long-password')
        accounts.add('bob', 'bob-long-password!')
        with pytest.raises(ValueError):
            accounts.add('local-operator', 'whatever-password')  # reserved setup identity
        with pytest.raises(ValueError):
            accounts.add('carol', 'short')
        assert c.post('/api/login', json={'username': 'alice', 'password': 'nope-nope-nope'},
                      headers={'Origin': cfg.origin}).status_code == 401
        h = sign_in(c, cfg, username='alice', password='alice-long-password')
        assert c.get('/api/session').json()['actor'] == 'alice'
        eng = c.post('/api/engagements', json={'name': 'Alice lab', 'include': ['10.0.0.5']}, headers=h).json()
        audit = c.get('/api/audit').json()
        assert any(e['action'] == 'login' for e in audit) and any(e['action'] == 'engagement-created' for e in audit)
    with app_client(cfg) as c:
        sign_in(c, cfg, username='bob', password='bob-long-password!')
        assert eng['id'] not in [e['id'] for e in c.get('/api/engagements').json()]
        assert c.get(f"/api/engagements/{eng['id']}/files").status_code == 404
        accounts.remove('bob')
        assert c.get('/api/session').status_code == 401  # removal revokes live sessions


def test_operator_cli_round_trip(cfg, tmp_path, monkeypatch):
    path = tmp_path / 'settings.env'
    write_settings(path, {'FOXWORX_STATE': str(cfg.state), 'FOXWORX_SESSION_SECRET': cfg.secret,
                          'FOXWORX_LOCAL_TOKEN': cfg.local_token, 'FOXWORX_AUTH_MODE': 'local'})
    monkeypatch.setattr('sys.stdin', __import__('io').StringIO('dana-long-password\n'))
    assert main(['operator', '--config', str(path), 'add', 'dana', '--password-stdin']) == 0
    assert Accounts(Store(cfg.state)).authenticate('dana', 'dana-long-password') == 'dana'
    assert main(['operator', '--config', str(path), 'remove', 'dana']) == 0
    assert Accounts(Store(cfg.state)).authenticate('dana', 'dana-long-password') is None


# ---- open mode: explicit, loopback-only -------------------------------------

def test_open_mode_requires_explicit_acknowledgement(cfg):
    with pytest.raises(ValueError, match='explicit opt-in'):
        replace(cfg, mode='open').validate()
    opened(cfg).validate()


@pytest.mark.parametrize('bind', ['0.0.0.0', '192.168.1.20', '::', 'workbench.lan'])
def test_open_mode_refuses_non_loopback_bind(cfg, bind):
    with pytest.raises(ValueError, match='loopback'):
        opened(cfg, bind_host=bind).validate()


def test_serve_refuses_open_mode_on_non_loopback_bind(tmp_path, cfg, capsys):
    path = tmp_path / 'settings.env'
    write_settings(path, {'FOXWORX_STATE': str(cfg.state), 'FOXWORX_SESSION_SECRET': cfg.secret,
                          'FOXWORX_AUTH_MODE': 'open', 'FOXWORX_OPEN_MODE_ACK': OPEN_MODE_ACK,
                          'FOXWORX_BIND_HOST': '0.0.0.0'})
    assert main(['serve', '--config', str(path)]) == 1
    assert 'loopback' in capsys.readouterr().err


def test_open_mode_cannot_be_combined_with_public_origin(cfg):
    with pytest.raises(ValueError):
        opened(cfg, public_origin=PUBLIC).validate()


def test_open_mode_rejects_tunnels_rebinding_and_remote_clients(cfg):
    with app_client(opened(cfg)) as c:
        assert c.get('/api/session').json()['actor'] == 'local-operator'
        # DNS rebinding: attacker hostname resolving to 127.0.0.1.
        assert c.get('/api/session', headers={'Host': 'attacker.example'}).status_code == 403
        # Tunnel / reverse proxy in front of a no-login shell.
        for header in ('CF-Ray', 'X-Forwarded-For', 'Forwarded', 'CF-Connecting-IP'):
            assert c.get('/api/session', headers={header: '203.0.113.9'}).status_code == 403
        assert c.post('/api/login', json={'token': 'x'}, headers={'Origin': cfg.origin}).status_code == 404
        csrf = c.get('/api/session').json()['csrf']
        assert c.post('/api/tools/refresh', json={}, headers={'Origin': 'https://attacker.example', 'X-Foxworx-CSRF': csrf}).status_code == 403
    with app_client(opened(cfg), client=('192.0.2.10', 4000)) as c:
        assert c.get('/api/session').status_code == 403


# ---- upgrades never weaken authentication ----------------------------------

def _seed(path, **changes):
    initialize(path, profile='pi')
    values = dict(dotenv_values(path, interpolate=False))
    values.update(changes)
    write_settings(path, values)
    return values


@pytest.mark.parametrize('changes', [
    {'FOXWORX_AUTH_MODE': 'local', 'FOXWORX_LOCAL_TOKEN': 'x' * 48, 'FOXWORX_LOCAL_TOKEN_HASH': '',
     'FOXWORX_PORT': '8000', 'FOXWORX_PUBLIC_ORIGIN': PUBLIC},
    {'FOXWORX_AUTH_MODE': 'proxy', 'FOXWORX_ORIGIN': 'https://tools.example.test', 'FOXWORX_LOCAL_TOKEN': 'y' * 48},
    {'FOXWORX_AUTH_MODE': 'cloudflare', 'FOXWORX_ORIGIN': 'https://kali.example.test',
     'CF_TEAM_DOMAIN': 'https://fox.cloudflareaccess.com', 'CF_ACCESS_AUD': 'aud', 'FOXWORX_ALLOWED_EMAILS': 'a@example.test'},
])
def test_upgrade_preserves_authenticated_settings(tmp_path, monkeypatch, changes):
    for k in ('FOXWORX_AUTH_MODE', 'FOXWORX_PORT', 'FOXWORX_ORIGIN', 'FOXWORX_PUBLIC_ORIGIN', 'FOXWORX_LOCAL_TOKEN'):
        monkeypatch.delenv(k, raising=False)
    path = tmp_path / 'settings.env'
    before = _seed(path, OPENAI_API_KEY='keep-me', **changes)
    assert main(['configure', '--config', str(path), '--upgrade']) == 0
    assert dict(dotenv_values(path, interpolate=False)) == before
    assert not path.with_name('settings.env.before-v3.4.0').exists()


def test_upgrade_moves_unacknowledged_open_mode_to_login(tmp_path, monkeypatch, capsys):
    for k in ('FOXWORX_AUTH_MODE', 'FOXWORX_LOCAL_TOKEN', 'FOXWORX_LOCAL_TOKEN_HASH', 'FOXWORX_OPEN_MODE_ACK'):
        monkeypatch.delenv(k, raising=False)
    path = tmp_path / 'settings.env'
    before = _seed(path, FOXWORX_AUTH_MODE='open', FOXWORX_LOCAL_TOKEN='', FOXWORX_LOCAL_TOKEN_HASH='',
                   FOXWORX_PUBLIC_ORIGIN=PUBLIC, OPENAI_API_KEY='keep-me')
    capsys.readouterr()
    assert main(['configure', '--config', str(path), '--upgrade']) == 0
    after = dict(dotenv_values(path, interpolate=False))
    assert after['FOXWORX_AUTH_MODE'] == 'local' and after['FOXWORX_LOCAL_TOKEN_HASH'].startswith('sha256:')
    for k in ('OPENAI_API_KEY', 'FOXWORX_SESSION_SECRET', 'FOXWORX_STATE', 'FOXWORX_PUBLIC_ORIGIN', 'FOXWORX_PORT'):
        assert after[k] == before[k]
    assert 'setup token' in capsys.readouterr().out.lower()
    backup = path.with_name('settings.env.before-v3.4.0')
    assert backup.stat().st_mode & 0o777 == 0o600
    assert dotenv_values(backup, interpolate=False)['FOXWORX_AUTH_MODE'] == 'open'
    # Idempotent: a second upgrade changes nothing and issues no new token.
    assert main(['configure', '--config', str(path), '--upgrade']) == 0
    assert dict(dotenv_values(path, interpolate=False)) == after


def test_upgrade_keeps_explicitly_acknowledged_open_mode(tmp_path, monkeypatch):
    for k in ('FOXWORX_AUTH_MODE', 'FOXWORX_OPEN_MODE_ACK'):
        monkeypatch.delenv(k, raising=False)
    path = tmp_path / 'settings.env'
    before = _seed(path, FOXWORX_AUTH_MODE='open', FOXWORX_OPEN_MODE_ACK=OPEN_MODE_ACK)
    assert main(['configure', '--config', str(path), '--upgrade']) == 0
    assert dict(dotenv_values(path, interpolate=False)) == before


def test_open_mode_opt_in_and_revert_commands(tmp_path, monkeypatch, capsys):
    for k in ('FOXWORX_AUTH_MODE', 'FOXWORX_OPEN_MODE_ACK', 'FOXWORX_PUBLIC_ORIGIN'):
        monkeypatch.delenv(k, raising=False)
    path = tmp_path / 'settings.env'
    _seed(path, FOXWORX_PUBLIC_ORIGIN=PUBLIC)
    assert main(['configure', '--config', str(path), '--insecure-open-mode']) == 1  # public origin set
    assert main(['configure', '--config', str(path), '--clear-public-origin']) == 0
    assert main(['configure', '--config', str(path), '--insecure-open-mode']) == 0
    assert 'WARNING' in capsys.readouterr().out
    assert Settings.load(path).mode == 'open'
    assert main(['configure', '--config', str(path), '--require-login']) == 0
    assert Settings.load(path).mode == 'local'


def test_rotate_token_revokes_sessions(cfg, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv('FOXWORX_LOCAL_TOKEN', raising=False)
    path = tmp_path / 'settings.env'
    write_settings(path, {'FOXWORX_STATE': str(cfg.state), 'FOXWORX_SESSION_SECRET': cfg.secret,
                          'FOXWORX_LOCAL_TOKEN': cfg.local_token, 'FOXWORX_AUTH_MODE': 'local'})
    accounts = Accounts(Store(cfg.state))
    cookie, _ = accounts.create_session('local-operator')
    assert main(['configure', '--config', str(path), '--rotate-token']) == 0
    assert accounts.session(cookie) is None
    new = Settings.load(path)
    assert new.local_token == '' and new.has_token_hash and not new.check_setup_token(cfg.local_token)


def test_security_headers_and_hsts_only_over_https(cfg):
    with app_client(cfg) as c:
        r = c.get('/api/auth-mode')
        assert "frame-ancestors 'none'" in r.headers['content-security-policy']
        assert r.headers['x-frame-options'] == 'DENY' and 'strict-transport-security' not in r.headers
    with app_client(replace(cfg, public_origin=PUBLIC), base=PUBLIC) as c:
        assert c.get('/api/auth-mode', headers={'CF-Ray': 'x'}).headers['strict-transport-security'].startswith('max-age=')
