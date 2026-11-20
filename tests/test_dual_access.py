"""v3.3.3+: one protected app, independent localhost and HTTPS tunnel origins."""
from dataclasses import replace
from pathlib import Path
import time
import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from foxworx.app import create_app
from foxworx.cli import initialize, main
from foxworx.config import Settings

PUBLIC='https://foxworxhacker1.dreadfoxtrader.com'


def test_explicit_public_origin_remains_opt_in(cfg):
    assert cfg.public_origin == ''
    assert PUBLIC not in cfg.origins
    with TestClient(create_app(cfg,_allow_root_for_tests=True),base_url=PUBLIC, client=('127.0.0.1',50000)) as c:
        assert c.get('/').status_code == 403


def test_two_origins_host_restriction_and_forwarded_headers(cfg):
    both=replace(cfg,public_origin=PUBLIC)
    both.validate()
    for base,cloud in [(cfg.origin,False),(PUBLIC,True)]:
        with TestClient(create_app(both,_allow_root_for_tests=True),base_url=base,client=('127.0.0.1',50000)) as c:
            hdr={'Cf-Ray':'test-id','X-Forwarded-For':'203.0.113.4'} if cloud else {}
            assert c.get('/',headers=hdr).status_code==200
            assert c.get('/api/auth-mode',headers=hdr).json()['mode']=='local'
            assert c.get('/api/auth-mode',headers=hdr).json()['public_origin']==PUBLIC
            assert c.get('/api/session',headers=hdr).status_code==401
            assert c.get('/api/session',headers={'Host':'attacker.test'}).status_code==403
            assert c.get('/api/auth-mode',headers={'Host':'127.0.0.1:8765','Cf-Ray':'proxy'}).status_code==403
            r=c.post('/api/login',json={'token':both.local_token},headers={'Origin':base,**hdr})
            assert r.status_code==200
            assert ('secure' in r.headers['set-cookie'].lower()) is cloud
            assert 'httponly' in r.headers['set-cookie'].lower()
            assert 'samesite=strict' in r.headers['set-cookie'].lower()
            assert c.get('/api/session',headers=hdr).status_code==200
            csrf=c.get('/api/session',headers=hdr).json()['csrf']
            # A valid cookie and CSRF do not authorize a cross-origin mutation.
            assert c.post('/api/logout',json={},headers={'Origin':cfg.origin if cloud else PUBLIC,'X-Foxworx-CSRF':csrf,**hdr}).status_code==403
            assert c.post('/api/logout',json={},headers={'Origin':base,'X-Foxworx-CSRF':csrf,**hdr}).status_code==200
    with TestClient(create_app(both,_allow_root_for_tests=True),base_url=PUBLIC,client=('10.0.0.9',60000)) as c:
        assert c.get('/').status_code==403


def test_https_host_is_never_automatically_trusted(cfg):
    for bad in ('http://foxworxhacker1.dreadfoxtrader.com','https://foxworxhacker1.dreadfoxtrader.com/route',
                'https://127.0.0.1','https://operator:pass@foxworxhacker1.dreadfoxtrader.com',
                'https://foxworxhacker1.dreadfoxtrader.com?x=1'):
        with pytest.raises(ValueError):
            replace(cfg,public_origin=bad).validate()


def test_remember_checkbox_keeps_server_side_session_for_seven_days(cfg):
    with TestClient(create_app(cfg,_allow_root_for_tests=True),base_url=cfg.origin,client=('127.0.0.1',50000)) as c:
        now=time.time()
        response=c.post('/api/login',json={'token':cfg.local_token,'remember':True},headers={'Origin':cfg.origin})
        assert response.status_code==200
        assert 'max-age=604800' in response.headers['set-cookie'].lower()
        expires=c.get('/api/session').json()['session_expires']
        assert now+6*86400 <= expires <= now+8*86400
        cookie=c.cookies['foxworx_session']
    # Sessions are stored server-side, so they survive an app restart.
    with TestClient(create_app(cfg,_allow_root_for_tests=True),base_url=cfg.origin,client=('127.0.0.1',50000)) as c:
        c.cookies.set('foxworx_session',cookie)
        assert c.get('/api/session').status_code==200
    with TestClient(create_app(cfg,_allow_root_for_tests=True),base_url=cfg.origin,client=('127.0.0.1',50000)) as c:
        response=c.post('/api/login',json={'token':cfg.local_token},headers={'Origin':cfg.origin})
        assert 'max-age=28800' in response.headers['set-cookie'].lower()


def test_cli_public_origin_round_trip_preserves_secrets(tmp_path,monkeypatch,capsys):
    for key in ('FOXWORX_PORT','FOXWORX_AUTH_MODE','FOXWORX_ORIGIN','FOXWORX_PUBLIC_ORIGIN'):
        monkeypatch.delenv(key,raising=False)
    path=tmp_path/'settings.env'
    initialize(path,profile='pi')
    initial=dict(dotenv_values(path,interpolate=False))
    assert main(['configure','--config',str(path),'--public-origin',PUBLIC])==0
    after=dict(dotenv_values(path,interpolate=False))
    assert after['FOXWORX_PUBLIC_ORIGIN']==PUBLIC
    assert after['FOXWORX_LOCAL_TOKEN_HASH']==initial['FOXWORX_LOCAL_TOKEN_HASH']
    assert after['FOXWORX_SESSION_SECRET']==initial['FOXWORX_SESSION_SECRET']
    assert after['FOXWORX_AUTH_MODE']=='local'
    assert after['FOXWORX_ORIGIN']=='http://127.0.0.1:8765'
    assert Settings.load(path).public_origin==PUBLIC
    assert (path.parent/'settings.env.before-v3.4.0').is_file()
    assert main(['configure','--config',str(path),'--clear-public-origin'])==0
    assert Settings.load(path).public_origin==''
    assert main(['configure','--config',str(path),'--public-origin','http://bad.example'])==1
    assert Settings.load(path).public_origin==''
    assert 'FOXWORX_SESSION_SECRET' not in capsys.readouterr().out
