"""Authentication, host/origin validation and CSRF protection.

Modes
-----
``local`` (default)
    Login is required. Operators sign in with a per-operator account
    (``foxworx.cli operator add``) or with the installer-generated setup token,
    which maps to the built-in ``local-operator`` identity. Sessions are
    server-side, expire, and are revocable.
``proxy``
    Same login as ``local``, served through an operator-managed HTTPS reverse
    proxy at one exact public origin. Cookies are always ``Secure``.
``cloudflare`` (legacy)
    Every request must carry a verified Cloudflare Access JWT for an
    allowlisted email address.
``open`` (explicit opt-in only)
    No login. Accepted only on a loopback bind, only for loopback clients and
    loopback ``Host`` headers, and never with a public origin or proxy headers.

In every mode the ``Host`` header must match an allowlisted origin, which
defeats DNS-rebinding attacks against the loopback listener, and every
state-changing request must carry a same-origin ``Origin`` header plus a
session-bound CSRF token.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit
import asyncio
import hashlib
import hmac
import secrets
import time

import jwt
from jwt import PyJWKClient
from fastapi import HTTPException

from .accounts import Accounts, LoginThrottle, SETUP_ACTOR
from .config import Settings

SESSION_COOKIE = 'foxworx_session'
# Headers that indicate a request arrived through a tunnel or reverse proxy.
FORWARDING_HEADERS = ('cf-connecting-ip', 'cf-ray', 'cf-access-jwt-assertion', 'x-forwarded-for',
                      'x-forwarded-host', 'x-forwarded-proto', 'x-real-ip', 'forwarded')


@dataclass(frozen=True)
class Actor:
    name: str
    expires: int
    session: str = ''  # Digest of the server-side session; empty for open/cloudflare.


class Auth:
    def __init__(self, cfg: Settings, accounts: Accounts | None = None):
        self.cfg = cfg
        self.accounts = accounts
        self.jwks = (PyJWKClient(cfg.cf_team + '/cdn-cgi/access/certs', cache_jwk_set=True, lifespan=300, timeout=5)
                     if cfg.mode == 'cloudflare' else None)
        self.throttle = LoginThrottle()
        self.tickets: dict[str, tuple[str, str, float]] = {}

    @property
    def login_enabled(self) -> bool:
        return self.cfg.mode in ('local', 'proxy')

    # ---- host / origin --------------------------------------------------
    def request_origin(self, request) -> str:
        """Return the allowlisted origin matching the exact ``Host`` header.

        Forwarded / X-Forwarded-* headers are never trusted to add an origin.
        """
        host = request.headers.get('host', '').lower()
        for origin in self.cfg.origins:
            if urlsplit(origin).netloc.lower() == host:
                return origin
        raise HTTPException(403, 'Unexpected hostname. Configure FOXWORX_PUBLIC_ORIGIN for HTTPS tunnel access.')

    def is_public_host(self, request) -> bool:
        return self.cfg.public_origin != '' and self.request_origin(request) == self.cfg.public_origin

    def check_host(self, request) -> None:
        requested = self.request_origin(request)
        if self.cfg.mode == 'cloudflare':
            return
        client = request.client.host if request.client else ''
        if client not in ('127.0.0.1', '::1'):
            raise HTTPException(403, 'This listener only accepts loopback connections')
        forwarded = any(h in request.headers for h in FORWARDING_HEADERS)
        if self.cfg.mode == 'open' and forwarded:
            # A tunnel or proxy in front of no-login mode would publish a shell.
            raise HTTPException(403, 'Open (no-login) mode refuses proxied or tunnelled requests')
        # Never accept requests proxied onto the insecure localhost hostname.
        # Legitimate HTTPS tunnels must preserve their PUBLIC Host header.
        if self.cfg.mode == 'local' and requested != self.cfg.public_origin and forwarded:
            raise HTTPException(403, 'Tunnel must preserve its configured public Host header')

    def origin(self, request) -> None:
        if request.headers.get('origin', '') != self.request_origin(request):
            raise HTTPException(403, 'Origin check failed. Use the same address in the browser.')

    def secure_cookie(self, request) -> bool:
        scheme = urlsplit(self.request_origin(request)).scheme
        return self.cfg.mode == 'proxy' or scheme == 'https'

    # ---- CSRF -----------------------------------------------------------
    def csrf(self, actor: Actor | str) -> str:
        """CSRF token bound to the server-side session (or identity when sessionless)."""
        binding = (actor.session or actor.name) if isinstance(actor, Actor) else actor
        return hmac.new(self.cfg.secret.encode(), ('csrf:' + binding).encode(), hashlib.sha256).hexdigest()

    def mutation(self, request, actor: Actor) -> None:
        self.origin(request)
        if not hmac.compare_digest(request.headers.get('x-foxworx-csrf', ''), self.csrf(actor)):
            raise HTTPException(403, 'Session check failed. Reload the page and try again.')

    # ---- identity -------------------------------------------------------
    def verify_cloudflare(self, raw: str) -> Actor:
        if not raw:
            raise HTTPException(401, 'Cloudflare Access login is required')
        try:
            key = self.jwks.get_signing_key_from_jwt(raw)
            claims = jwt.decode(raw, key.key, algorithms=['RS256'], issuer=self.cfg.cf_team,
                                audience=self.cfg.cf_aud, options={'require': ['exp', 'iat', 'iss', 'aud', 'sub']})
            email = str(claims.get('email', '')).lower()
            if not email or email not in self.cfg.emails:
                raise HTTPException(403, 'This identity is not in the workbench email allowlist')
            return Actor(email, int(claims['exp']))
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(401, 'Access identity could not be verified. Sign in again; check issuer/AUD if this continues.') from exc

    async def actor(self, request) -> Actor:
        """Authenticate an HTTP request or WebSocket handshake, or raise 401/403."""
        self.check_host(request)
        if self.cfg.mode == 'open':
            # Explicit opt-in: one shared identity on a loopback-only listener.
            return Actor(SETUP_ACTOR, int(time.time()) + 86400)
        if self.cfg.mode == 'cloudflare':
            return await asyncio.to_thread(self.verify_cloudflare, request.headers.get('cf-access-jwt-assertion', ''))
        session = self.accounts.session(request.cookies.get(SESSION_COOKIE, '')) if self.accounts else None
        if session is None:
            raise HTTPException(401, 'Sign in to the workbench')
        return Actor(session.actor, session.expires, session.id_digest)

    async def authenticated(self, request) -> bool:
        try:
            await self.actor(request)
            return True
        except HTTPException:
            return False

    # Bandit B107 false positive: empty defaults, not credentials.
    def login(self, client: str, token: str = '', username: str = '', password: str = '',  # nosec B107
              remember: bool = False) -> tuple[str, str, int]:
        """Verify credentials; return ``(actor, session_cookie, max_age)``."""
        if not self.login_enabled or self.accounts is None:
            raise HTTPException(404, 'Password login is not used in this authentication mode')
        username = (username or '').strip().lower()
        try:
            self.throttle.check(client, username)
        except PermissionError as exc:
            raise HTTPException(429, str(exc)) from exc
        if username:
            who = self.accounts.authenticate(username, password)
        else:
            who = SETUP_ACTOR if token and self.cfg.check_setup_token(token) else None
        if who is None:
            self.throttle.failed(client, username)
            raise HTTPException(401, 'Incorrect operator name, password or setup token.')
        self.throttle.succeeded(client, username)
        cookie, max_age = self.accounts.create_session(who, remember)
        return who, cookie, max_age

    def logout(self, request) -> None:
        if self.accounts is not None:
            self.accounts.revoke(request.cookies.get(SESSION_COOKIE, ''))

    # ---- terminal WebSocket tickets -------------------------------------
    def ticket(self, actor: str, terminal_id: str) -> str:
        self.tickets = {k: v for k, v in self.tickets.items() if v[2] > time.time()}
        if len(self.tickets) >= 100:
            raise HTTPException(429, 'Too many pending terminal connections')
        token = secrets.token_urlsafe(32)
        self.tickets[token] = (actor, terminal_id, time.time() + 30)
        return token

    def consume_ticket(self, token: str, actor: str, terminal_id: str) -> bool:
        value = self.tickets.pop(token, None)
        return bool(value and value[:2] == (actor, terminal_id) and value[2] > time.time())
