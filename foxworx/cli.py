"""Local administration CLI: configuration, operator accounts, diagnostics and serving.

Nothing here is reachable over HTTP; it runs as the service account in a shell.
"""
from __future__ import annotations
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import stat
import sys
import tempfile
from dotenv import dotenv_values
from . import __version__
from .config import Settings, DEFAULT_CONFIG, ROOT, OPEN_MODE_ACK, hash_setup_token, is_loopback
from .platforms import host_snapshot, profile_defaults


def write_settings(path: Path, values: dict[str, str]) -> None:
    path = path.expanduser().absolute()
    if path.is_symlink():
        raise ValueError('Refusing a symbolic-link configuration file')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lines = [f'# FOXWORX Workbench v{__version__}. Private configuration; do not publish or send to chat.']
    for key, value in values.items():
        if not key.replace('_', '').isalnum():
            raise ValueError('Invalid configuration key')
        value = str(value or '')
        if '\n' in value or '\r' in value or '\x00' in value:
            raise ValueError(f'{key} must fit on one line')
        lines.append(key + '=' + json.dumps(value, ensure_ascii=True))
    fd, temporary = tempfile.mkstemp(prefix='.settings-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write('\n'.join(lines) + '\n')
            stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


BACKUP_SUFFIX = '.before-v3.4.0'


def _backup(path: Path) -> None:
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copyfile(path, backup)
        os.chmod(backup, 0o600)


def _validated_write(path: Path, values: dict[str, str]) -> None:
    """Validate a candidate configuration in a scratch file, then replace atomically."""
    fd, temporary = tempfile.mkstemp(prefix='.validate-', dir=path.parent)
    os.close(fd)
    try:
        write_settings(Path(temporary), values)
        Settings.load(Path(temporary))
    finally:
        os.unlink(temporary)
    write_settings(path, values)


def new_setup_token(values: dict[str, str]) -> str:
    """Generate a setup token, store only its hash, and return it for one-time display."""
    token = secrets.token_urlsafe(32)
    values['FOXWORX_LOCAL_TOKEN_HASH'] = hash_setup_token(token)
    # Bandit B105 false positive: clears the legacy plaintext token.
    values['FOXWORX_LOCAL_TOKEN'] = ''  # nosec B105
    return token


def announce_token(token: str) -> None:
    bar = '=' * 72
    print(bar)
    print('FOXWORX setup token (shown ONCE; only a hash is stored):')
    print()
    print('    ' + token)
    print()
    print('Sign in with it, then create named operator accounts:')
    print('    bash scripts/operator.sh add <name>')
    print('Lost it? Run: bash scripts/configure.sh --rotate-token')
    print(bar)


def _has_login_secret(values: dict[str, str]) -> bool:
    return len(values.get('FOXWORX_LOCAL_TOKEN') or '') >= 32 or bool(values.get('FOXWORX_LOCAL_TOKEN_HASH'))


def initialize(path: Path, *, profile: str = 'auto') -> str | None:
    """Create a new authenticated configuration exactly once.

    Returns the new setup token (for one-time display) when a file was created,
    or ``None`` when an existing configuration was preserved. An existing file
    only gains a missing model-credential encryption key; auth is untouched.
    """
    path = path.expanduser()
    if path.is_symlink(): raise ValueError('Configuration path must not be a symbolic link')
    if path.exists():
        current=dict(dotenv_values(path, interpolate=False))
        if not current.get('FOXWORX_AI_ENCRYPTION_KEY'):
            current['FOXWORX_AI_ENCRYPTION_KEY']=secrets.token_urlsafe(48)
            write_settings(path,current)
        return None
    if profile == 'auto':
        profile=host_snapshot()['profile']
    defaults=profile_defaults(profile)
    values={
        'FOXWORX_AUTH_MODE':'local', 'FOXWORX_PORT':'8765', 'FOXWORX_BIND_HOST':'127.0.0.1',
        'FOXWORX_ORIGIN':'http://127.0.0.1:8765', 'FOXWORX_PUBLIC_ORIGIN':'',
        'FOXWORX_SESSION_SECRET':secrets.token_urlsafe(48),
        # Bandit B105 false positive: empty placeholders; the token is generated below.
        'FOXWORX_LOCAL_TOKEN':'', 'FOXWORX_LOCAL_TOKEN_HASH':'', 'FOXWORX_OPEN_MODE_ACK':'',  # nosec B105
        'FOXWORX_AI_ENCRYPTION_KEY':secrets.token_urlsafe(48),
        'FOXWORX_AI_ENDPOINTS':'{}',
        'FOXWORX_STATE':str(Path.home()/'.local/share/foxworx-workbench-v2'),
        'CF_TEAM_DOMAIN':'', 'CF_ACCESS_AUD':'', 'FOXWORX_ALLOWED_EMAILS':'',
        'OPENAI_API_KEY':'', 'OPENAI_MODEL':'',
        'FOXWORX_MAX_JOBS':str(defaults['max_jobs']), 'FOXWORX_JOB_TIMEOUT':'900', 'FOXWORX_LOG_MIB':'5',
        'FOXWORX_MAX_TERMINALS':str(defaults['max_terminals']), 'FOXWORX_RESERVE_MIB':str(defaults['reserve_mib']),
        'FOXWORX_EXTRA_TOOL_PATHS':'', 'FOXWORX_SHELL':'/bin/bash',
    }
    token = new_setup_token(values)
    write_settings(path, values)
    return token


def upgrade(path: Path, *, profile: str = 'auto') -> str | None:
    """Installer entry point. Never weakens an existing deployment's authentication.

    * No config: create an authenticated one (see :func:`initialize`).
    * ``local``/``proxy``/``cloudflare``: mode, port, origins, tokens and
      secrets are preserved exactly.
    * ``open`` WITH the explicit acknowledgement: preserved (operator opted in).
    * ``open`` WITHOUT it (the v3.3.x default): moved to authenticated
      ``local`` mode with a new setup token. A backup is kept.

    Returns a newly generated setup token for one-time display, else ``None``.
    """
    created = initialize(path, profile=profile)
    if created is not None:
        return created
    values = dict(dotenv_values(path, interpolate=False))
    mode = values.get('FOXWORX_AUTH_MODE') or 'open'  # v3.3.x files without the key defaulted to open
    token = None
    changes: dict[str, str] = {}
    if len(values.get('FOXWORX_SESSION_SECRET') or '') < 32:
        changes['FOXWORX_SESSION_SECRET'] = secrets.token_urlsafe(48)
    if mode == 'open' and values.get('FOXWORX_OPEN_MODE_ACK') != OPEN_MODE_ACK:
        changes['FOXWORX_AUTH_MODE'] = 'local'
        # v3.3.x allowed open mode with a public origin; keep that hostname but now behind login.
    if (changes.get('FOXWORX_AUTH_MODE', mode) in ('local', 'proxy')) and not _has_login_secret(values):
        token = new_setup_token(changes)
    if 'FOXWORX_AUTH_MODE' not in values:
        changes.setdefault('FOXWORX_AUTH_MODE', mode)
    if changes:
        _backup(path)
        values.update(changes)
        _validated_write(path, values)
        if changes.get('FOXWORX_AUTH_MODE') == 'local' and mode == 'open':
            print('Security upgrade: token-free (open) mode is no longer the default. Login is now required; '
                  f'previous configuration backed up to {path.name}{BACKUP_SUFFIX}.')
    return token


def configure(path: Path, options) -> None:
    if getattr(options, 'upgrade', False):
        token = upgrade(path, profile=options.profile)
        if token: announce_token(token)
        else: print('Preserved existing authentication settings in ' + str(path))
        return
    created = initialize(path, profile=options.profile)
    if created is not None:
        print('Created ' + str(path))
        announce_token(created)
        if options.init: return
    if getattr(options, 'local_service', False):
        # Point the deployment at the loopback listener on port 8765. Never
        # selects or keeps open mode unless the administrator acknowledged it.
        values = dict(dotenv_values(path, interpolate=False))
        desired = {'FOXWORX_PORT':'8765', 'FOXWORX_ORIGIN':'http://127.0.0.1:8765', 'FOXWORX_BIND_HOST':'127.0.0.1'}
        mode = values.get('FOXWORX_AUTH_MODE') or 'open'
        keep_open = mode == 'open' and values.get('FOXWORX_OPEN_MODE_ACK') == OPEN_MODE_ACK
        desired['FOXWORX_AUTH_MODE'] = 'open' if keep_open else 'local'
        if len(values.get('FOXWORX_SESSION_SECRET') or '') < 32:
            desired['FOXWORX_SESSION_SECRET'] = secrets.token_urlsafe(48)
        token = None
        if not keep_open and not _has_login_secret(values):
            token = new_setup_token(desired)
        if any(values.get(k) != v for k, v in desired.items()):
            _backup(path)
            values.update(desired)
            _validated_write(path, values)
            print(f'Configured local service on http://127.0.0.1:8765 ({desired["FOXWORX_AUTH_MODE"]} mode); '
                  'original configuration backed up.')
        else:
            print('Local service is already configured for http://127.0.0.1:8765.')
        if token: announce_token(token)
        return
    if getattr(options, 'insecure_open_mode', False):
        values = dict(dotenv_values(path, interpolate=False))
        if values.get('FOXWORX_PUBLIC_ORIGIN'):
            raise ValueError('Clear the public origin first (--clear-public-origin); open mode is loopback-only.')
        if not is_loopback(values.get('FOXWORX_BIND_HOST') or '127.0.0.1'):
            raise ValueError('Open mode refuses non-loopback bind addresses.')
        _backup(path)
        values.update({'FOXWORX_AUTH_MODE':'open', 'FOXWORX_OPEN_MODE_ACK':OPEN_MODE_ACK,
                       'FOXWORX_ORIGIN':f'http://127.0.0.1:{values.get("FOXWORX_PORT") or 8765}'})
        _validated_write(path, values)
        print('WARNING: open mode disables application login. Any local user or process that can reach')
        print('127.0.0.1 can use the terminal as this Linux account. Proxied/tunnelled requests are refused.')
        print('Revert with: bash scripts/configure.sh --require-login')
        return
    if getattr(options, 'require_login', False):
        values = dict(dotenv_values(path, interpolate=False))
        token = None if _has_login_secret(values) else new_setup_token(values)
        values.update({'FOXWORX_AUTH_MODE':'local', 'FOXWORX_OPEN_MODE_ACK':''})
        _backup(path)
        _validated_write(path, values)
        print('Login is now required (local mode). Restart: systemctl --user restart foxworx-workbench')
        if token: announce_token(token)
        return
    if options.init:
        print('Preserved ' + str(path))
        return
    if getattr(options, 'public_origin', None) is not None or getattr(options, 'clear_public_origin', False):
        from dataclasses import replace
        values = dict(dotenv_values(path, interpolate=False))
        public = (options.public_origin or '').rstrip('/') if options.public_origin else ''
        # Validate the actual config before touching the file. Never print tokens.
        current = Settings.load(path)
        replace(current,public_origin=public).validate()
        old = values.get('FOXWORX_PUBLIC_ORIGIN','')
        if old != public:
            _backup(path)
            values['FOXWORX_PUBLIC_ORIGIN'] = public
            write_settings(path,values)
        print(('Allowed HTTPS browser origin: '+public) if public else 'Optional HTTPS browser origin disabled.')
        print('Local URL: http://127.0.0.1:8765')
        print('Apply changes: systemctl --user restart foxworx-workbench')
        return
    values = dict(dotenv_values(path, interpolate=False))
    if options.show_token:
        if values.get('FOXWORX_LOCAL_TOKEN'):
            print(values['FOXWORX_LOCAL_TOKEN']); return
        raise ValueError('The setup token is stored only as a hash and cannot be shown. '
                         'Run --rotate-token to issue a new one.')
    if options.rotate_token:
        token = new_setup_token(values)
        values['FOXWORX_SESSION_SECRET']=secrets.token_urlsafe(48)
        write_settings(path, values)
        try:
            from .store import Store
            from .accounts import Accounts
            revoked = Accounts(Store(Settings.load(path).state)).revoke_all()
            print(f'Revoked {revoked} active browser session(s).')
        except (OSError, ValueError) as exc:
            print('Could not revoke stored sessions: '+str(exc), file=sys.stderr)
        announce_token(token)
        print('Restart the service to apply: systemctl --user restart foxworx-workbench'); return
    if not sys.stdin.isatty():
        raise ValueError('Interactive configuration needs a terminal. Use --init for unattended local initialization.')
    print(f'Configure FOXWORX v{__version__}. Enter preserves the displayed value. Secrets are never echoed.')
    def ask(key, description, default=''):
        old=values.get(key) or default
        new=input(f'{description} [{old}]: ').strip()
        values[key]=new or old
    ask('FOXWORX_AUTH_MODE','Authentication: local (login, recommended) / proxy / legacy cloudflare','local')
    if values['FOXWORX_AUTH_MODE']=='open' and values.get('FOXWORX_OPEN_MODE_ACK')!=OPEN_MODE_ACK:
        raise ValueError('Open mode must be enabled explicitly with --insecure-open-mode')
    ask('FOXWORX_PORT','Local listening port','8765')
    if values['FOXWORX_AUTH_MODE']=='proxy':
        print('HTTPS reverse proxy: keep the 127.0.0.1 listener and terminate TLS upstream.')
        ask('FOXWORX_ORIGIN','Exact external HTTPS origin, no path')
    elif values['FOXWORX_AUTH_MODE']=='cloudflare':
        print('Legacy Cloudflare JWT mode is available for existing deployments; it is not required for local service.')
        ask('FOXWORX_ORIGIN','Public HTTPS origin, no trailing path')
        ask('CF_TEAM_DOMAIN','HTTPS team domain, e.g. https://TEAM.cloudflareaccess.com')
        ask('CF_ACCESS_AUD','Access application audience (AUD)')
        ask('FOXWORX_ALLOWED_EMAILS','Permitted operator email addresses, comma separated')
    else:
        values['FOXWORX_ORIGIN']=f'http://127.0.0.1:{values["FOXWORX_PORT"]}'
    token = None
    if values['FOXWORX_AUTH_MODE'] in ('local','proxy') and not _has_login_secret(values):
        token = new_setup_token(values)
    print('Each operator can now configure providers, model IDs and encrypted API keys inside the browser.')
    print('Optional: retain a shared OpenAI key configured on this host for legacy v2 compatibility.')
    if input('Configure the optional shared OpenAI API key? [y/N]: ').strip().lower()=='y':
        key=getpass.getpass('API key (Enter keeps existing; - clears): ').strip()
        if key: values['OPENAI_API_KEY']='' if key=='-' else key
        ask('OPENAI_MODEL','OpenAI model ID available to your API project')
    print('For remote Ollama / LM Studio / vLLM, edit FOXWORX_AI_ENDPOINTS in settings.env locally.')
    print('Example: {"gpu-ollama":{"provider":"ollama","url":"http://192.168.1.10:11434"}}')
    ask('FOXWORX_EXTRA_TOOL_PATHS','Extra absolute tool directories, colon-separated')
    _validated_write(path, values)
    print('Saved ' + str(path))
    if token: announce_token(token)
    print('Apply changes: systemctl --user restart foxworx-workbench')


def operator_command(cfg: Settings, options) -> int:
    """Manage per-operator web accounts (stored hashed in the state database)."""
    from .store import Store
    from .accounts import Accounts
    accounts = Accounts(Store(cfg.state))
    action = options.action
    if action == 'list':
        for item in accounts.list():
            print(f"{item['name']}\tcreated {item['created']}" + ('\tdisabled' if item['disabled'] else ''))
        return 0
    if not options.name:
        raise ValueError('An operator name is required')
    if action == 'remove':
        accounts.remove(options.name)
        print(f'Removed {options.name} and revoked their sessions. Their records remain in the state database.')
        return 0
    if action == 'logout':
        print(f'Revoked {accounts.revoke_all(options.name.strip().lower())} session(s).')
        return 0
    if options.password_stdin:
        password = sys.stdin.readline().rstrip('\n')
    else:
        password = getpass.getpass(f'Password for {options.name}: ')
        if getpass.getpass('Repeat password: ') != password:
            raise ValueError('Passwords did not match')
    if action == 'add':
        accounts.add(options.name, password)
        print(f'Created operator {options.name}. They sign in with this name and password.')
    else:
        accounts.set_password(options.name, password)
        print(f'Changed the password for {options.name} and revoked their sessions.')
    return 0


def vendor_check() -> list[str]:
    manifest=json.loads((ROOT/'static/vendor/manifest.json').read_text())
    errors=[]
    # Manifest file map is deliberately explicit; no network during startup.
    files=manifest.get('files',{})
    if not files:
        return ['manifest has no file hashes']
    if isinstance(files,list):
        files={x['name']:x['sha256'] for x in files}
    for name, expected in files.items():
        if isinstance(expected,dict): expected=expected['sha256']
        p=ROOT/'static/vendor'/name
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:
            errors.append(name)
    return errors


def doctor(cfg: Settings) -> int:
    from .registry import Registry
    warnings=[]
    if os.geteuid()==0: warnings.append('Root account: the web service will refuse to start.')
    hardware=host_snapshot(fresh=True)
    if not hardware['supported']: warnings.append('Unsupported host architecture or operating system.')
    warnings.extend(hardware['warnings'])
    if cfg.config_path.exists() and stat.S_IMODE(cfg.config_path.stat().st_mode)&0o077:
        warnings.append('Configuration permissions are too broad; run chmod 600 on the settings file.')
    from .providers import ProviderManager
    from .store import Store
    if not cfg.ai_encryption_key: warnings.append('Dedicated AI encryption key is missing; run configure --init to migrate before saving browser credentials.')
    try:
        from litellm import acompletion  # noqa: F401  (availability probe)
        ai_sdk=True
    except ImportError:
        ai_sdk=False
        warnings.append('LiteLLM is not installed. Install optional requirements-ai.txt for all cloud and local models.')
    if cfg.mode=='open': warnings.append('Open (no-login) mode: any local user or process that can reach 127.0.0.1 can operate the Linux service account. Run configure --require-login.')
    elif cfg.mode=='proxy' or cfg.public_origin: warnings.append('Check your independently configured HTTPS proxy and access policy.')
    cfg.state.mkdir(parents=True,exist_ok=True,mode=0o700)
    disk=shutil.disk_usage(cfg.state)
    if disk.free<cfg.reserve_bytes: warnings.append('Free disk space is below the configured reserve; managed jobs will be rejected.')
    from .adapters import AdapterPolicy
    adapter_status=AdapterPolicy(cfg.adapter_policy_path).current()
    if adapter_status['status']!='active':
        warnings.append('Rabbit Hole unattended adapters are disabled until an administrator installs a trusted root-owned policy; see docs/ADAPTER-POLICY.md.')
    reg=Registry(cfg); reg.scan()
    items=reg.list()
    failed_assets=vendor_check()
    if failed_assets: warnings.append('Vendor asset checksum mismatch: '+', '.join(failed_assets))
    output={'version':__version__, 'python':platform.python_version(), 'architecture':hardware['architecture'],
            'hardware':hardware,
            'config':str(cfg.config_path), 'state':str(cfg.state), 'mode':cfg.mode,
            'listening_url':f'http://{cfg.bind_host}:{cfg.port}', 'public_origin':cfg.public_origin,
            'installed_executables':sum(x['installed'] for x in items),
            'curated_installed':sum(x['installed'] and x['curated'] for x in items),
            'curated_total':sum(x['curated'] for x in items), 'free_disk_mib':disk.free//1048576,
            'vendor_checksums':'PASS' if not failed_assets else 'FAIL', 'ai_sdk_installed':ai_sdk,
            'rabbit_adapter_policy':adapter_status['status'],
            'rabbit_enabled_adapters':len(adapter_status['enabled_profiles']),
            'ai_provider_count':len(ProviderManager(cfg, Store(cfg.state)).describe('local-operator')['providers']), 'warnings':warnings,
            'not_checked':['Optional external reverse proxy access', 'API credentials/model access', 'native Kali tool runtime', 'HackerOne authorization']}
    print(json.dumps(output,indent=2))
    return 1 if failed_assets or os.geteuid()==0 or not hardware['supported'] else 0


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=f'FOXWORX Kali Workbench v{__version__} local administration')
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('version')
    for name in ('serve','configure','doctor','operator'):
        p=commands.add_parser(name)
        p.add_argument('--config',type=Path,default=DEFAULT_CONFIG)
        if name=='configure':
            p.add_argument('--profile',choices=('auto','pi','workstation'),default='auto',
                           help='Preset only for initial config; never overwrites an existing config')
            group=p.add_mutually_exclusive_group()
            for flag in ('init','upgrade','show-token','rotate-token','local-service','clear-public-origin',
                         'insecure-open-mode','require-login'):
                group.add_argument('--'+flag,action='store_true')
            group.add_argument('--public-origin',metavar='HTTPS_URL',
                               help='Allow the exact HTTPS tunnel hostname in addition to localhost')
        if name=='operator':
            p.add_argument('action',choices=('add','passwd','remove','list','logout'))
            p.add_argument('name',nargs='?',default='')
            p.add_argument('--password-stdin',action='store_true',help='Read the password from one line of stdin')
    args=parser.parse_args(argv)
    try:
        if args.command=='version': print(__version__); return 0
        if args.command=='configure': configure(args.config,args); return 0
        cfg=Settings.load(args.config)
        if args.command=='doctor': return doctor(cfg)
        if args.command=='operator': return operator_command(cfg,args)
        if not host_snapshot()['supported']:
            raise ValueError('FOXWORX requires native Linux ARM64 or x86-64; run host preflight first')
        if os.geteuid()==0: raise ValueError('Never run this shell-capable service as root. Use a dedicated non-root account.')
        import uvicorn
        from .app import create_app
        if not is_loopback(cfg.bind_host):  # Defence in depth; Settings.validate already refuses this.
            raise ValueError('Refusing to bind a non-loopback address')
        if cfg.mode=='open':
            print('FOXWORX WARNING: open (no-login) mode. Loopback-only; proxied requests are refused.',file=sys.stderr)
        uvicorn.run(create_app(cfg),host=cfg.bind_host,port=cfg.port,workers=1,proxy_headers=False,
                    access_log=False,ws_max_size=65536,ws_max_queue=16,timeout_graceful_shutdown=15)
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print('FOXWORX: '+str(exc),file=sys.stderr)
        return 1

if __name__=='__main__':
    raise SystemExit(main())
