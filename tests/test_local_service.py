"""Local service regression: no Cloudflare, no public bind, login required by default."""
from dataclasses import replace
from dotenv import dotenv_values
import pytest
from fastapi.testclient import TestClient
from foxworx.cli import initialize, main
from foxworx.config import Settings
from foxworx.app import create_app


def test_port_defaults_and_local_login_no_cloudflare(cfg):
    assert cfg.port == 8765
    assert cfg.origin == 'http://127.0.0.1:8765'
    assert cfg.bind_host == '127.0.0.1'
    cfg.validate()
    with TestClient(create_app(cfg,_allow_root_for_tests=True),base_url=cfg.origin,client=('127.0.0.1',45678)) as c:
        assert c.get('/api/auth-mode').json()=={'mode':'local','version':'3.6.0','public_origin':'','login':True}
        assert c.get('/api/session').status_code==401
        assert c.post('/api/login',json={'token':cfg.local_token},headers={'Origin':cfg.origin}).status_code==200
        assert c.get('/api/session').json()['actor']=='local-operator'
        assert c.get('/api/session',headers={'Host':'any-approved-by-operator.example'}).status_code==403
        assert c.get('/api/auth-mode',headers={'CF-Connecting-IP':'203.0.113.4'}).status_code==403


def test_new_local_config_port_8765(tmp_path,monkeypatch):
    monkeypatch.delenv('FOXWORX_PORT',raising=False)
    monkeypatch.delenv('FOXWORX_AUTH_MODE',raising=False)
    path=tmp_path/'settings.env'
    assert initialize(path,profile='pi') is not None
    values=dotenv_values(path,interpolate=False)
    assert values['FOXWORX_PORT']=='8765'
    assert values['FOXWORX_AUTH_MODE']=='local'
    assert values['FOXWORX_ORIGIN']=='http://127.0.0.1:8765'
    assert Settings.load(path).port==8765


def test_local_service_migrates_previous_config_without_losing_credentials(tmp_path, monkeypatch):
    for key in ('FOXWORX_PORT','FOXWORX_ORIGIN','FOXWORX_AUTH_MODE'):
        monkeypatch.delenv(key,raising=False)
    path=tmp_path/'settings.env'
    initialize(path,profile='pi')
    from foxworx.cli import write_settings
    original=dict(dotenv_values(path,interpolate=False))
    original.update({'FOXWORX_PORT':'8000','FOXWORX_ORIGIN':'https://old.example.test',
                     'FOXWORX_AUTH_MODE':'cloudflare','CF_TEAM_DOMAIN':'https://fox.cloudflareaccess.com',
                     'CF_ACCESS_AUD':'old-aud','FOXWORX_ALLOWED_EMAILS':'operator@example.test',
                     'OPENAI_API_KEY':'example-private-secret','FOXWORX_DISCORD_BOT_TOKEN':'example-discord-private',
                     'FOXWORX_DISCORD_CHANNEL_ID':'123456789012345678'})
    write_settings(path,original)
    # Explicit local-service switch: stays authenticated (local login), never open.
    assert main(['configure','--config',str(path),'--local-service']) == 0
    migrated=dict(dotenv_values(path,interpolate=False))
    assert migrated['FOXWORX_PORT']=='8765' and migrated['FOXWORX_AUTH_MODE']=='local'
    assert migrated['FOXWORX_ORIGIN']=='http://127.0.0.1:8765'
    for k in ('FOXWORX_SESSION_SECRET','FOXWORX_LOCAL_TOKEN_HASH','FOXWORX_STATE',
              'OPENAI_API_KEY','FOXWORX_DISCORD_BOT_TOKEN','FOXWORX_DISCORD_CHANNEL_ID'):
        assert migrated[k]==original[k]
    backup=path.with_name('settings.env.before-v3.4.0')
    assert backup.is_file() and backup.stat().st_mode & 0o777 == 0o600
    assert dotenv_values(backup,interpolate=False)['FOXWORX_AUTH_MODE']=='cloudflare'
    assert main(['configure','--config',str(path),'--local-service']) == 0
    assert dict(dotenv_values(path,interpolate=False))==migrated


def test_proxy_auth_requires_exact_https_origin_and_local_origin_service(cfg):
    with pytest.raises(ValueError):
        replace(cfg,mode='proxy',origin='http://tools.example.test').validate()
    with pytest.raises(ValueError):
        replace(cfg,mode='proxy',origin='https://tools.example.test/path').validate()
    proxy=replace(cfg,mode='proxy',origin='https://tools.example.test')
    proxy.validate()
    with TestClient(create_app(proxy,_allow_root_for_tests=True),base_url=proxy.origin,client=('127.0.0.1',45678)) as c:
        assert c.get('/api/session').status_code==401
        r=c.post('/api/login',json={'token':proxy.local_token},headers={'Origin':proxy.origin})
        assert r.status_code==200 and 'Secure' in r.headers['set-cookie']
        assert c.get('/api/session').status_code==200
        assert c.get('/api/session',headers={'Host':'evil.test'}).status_code==403
        assert c.post('/api/logout',json={},headers={'Origin':'https://evil.test'}).status_code==403
    with TestClient(create_app(proxy,_allow_root_for_tests=True),base_url=proxy.origin,client=('10.0.0.7',45000)) as c:
        assert c.get('/api/auth-mode').status_code==403


def test_new_version_printed_without_network_config(capsys):
    assert main(['version'])==0
    assert capsys.readouterr().out.strip()=='3.6.0'


def test_local_service_issues_token_when_login_secret_missing(tmp_path, monkeypatch, capsys):
    for key in ('FOXWORX_PORT','FOXWORX_AUTH_MODE','FOXWORX_ORIGIN','FOXWORX_LOCAL_TOKEN','FOXWORX_LOCAL_TOKEN_HASH'):
        monkeypatch.delenv(key,raising=False)
    path=tmp_path/'settings.env'
    initialize(path,profile='pi')
    from foxworx.cli import write_settings
    old=dict(dotenv_values(path,interpolate=False))
    old.update(FOXWORX_LOCAL_TOKEN='',FOXWORX_LOCAL_TOKEN_HASH='',FOXWORX_AUTH_MODE='open',FOXWORX_PORT='8000')
    write_settings(path,old)
    capsys.readouterr()
    assert main(['configure','--config',str(path),'--local-service'])==0
    new=dict(dotenv_values(path,interpolate=False))
    assert new['FOXWORX_AUTH_MODE']=='local' and new['FOXWORX_LOCAL_TOKEN_HASH'].startswith('sha256:')
    assert new['FOXWORX_SESSION_SECRET']==old['FOXWORX_SESSION_SECRET']
    assert new['FOXWORX_PORT']=='8765'
    assert 'setup token' in capsys.readouterr().out.lower()
