"""Per-operator accounts, server-side sessions and login throttling.

Accounts and sessions live in the workbench SQLite database (mode 0600 in a
0700 state directory). Passwords are hashed with scrypt; session cookies are
random 256-bit values and only their SHA-256 digest is stored, so a copy of the
database does not yield usable session cookies.

Operator accounts give each person an attributable identity and a separate
record namespace inside the application. They are NOT operating-system
isolation: every operator's terminal still runs as the one Linux service
account.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import re
import secrets
import time

from .store import Store, now

# The setup-token identity. Reserved so an account can never impersonate it.
SETUP_ACTOR = 'local-operator'
USERNAME = re.compile(r'[a-z][a-z0-9._-]{2,31}')
RESERVED = frozenset({SETUP_ACTOR, 'admin-token', 'root', 'system'})
MIN_PASSWORD = 12
MAX_PASSWORD = 256
SESSION_SECONDS = 8 * 3600
REMEMBER_SECONDS = 7 * 86400
MAX_SESSIONS_PER_ACTOR = 20
# scrypt parameters: ~16 MiB memory per hash; tolerable on a Raspberry Pi.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password: str) -> str:
    """Return a self-describing scrypt hash string."""
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f'scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${key.hex()}'


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time check of ``password`` against a :func:`hash_password` value."""
    try:
        scheme, n, r, p, salt, expected = encoded.split('$')
        if scheme != 'scrypt':
            return False
        key = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(expected) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(key.hex(), expected)


# Burn comparable CPU time for unknown usernames so response timing does not
# reveal which operator names exist.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def validate_username(name: str) -> str:
    name = (name or '').strip().lower()
    if not USERNAME.fullmatch(name) or name in RESERVED:
        raise ValueError('Operator names are 3–32 characters: a lowercase letter, then letters, digits, ".", "_" or "-"')
    return name


def validate_password(password: str) -> str:
    if not isinstance(password, str) or not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise ValueError(f'Passwords must be {MIN_PASSWORD}–{MAX_PASSWORD} characters')
    if password.strip() != password or len(set(password)) < 4:
        raise ValueError('Choose a password without leading/trailing spaces and with more variety')
    return password


@dataclass(frozen=True)
class Session:
    id_digest: str
    actor: str
    expires: int


class Accounts:
    """Operator accounts and server-side sessions stored alongside workbench data."""

    def __init__(self, store: Store):
        self.store = store
        with store.db() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS operators (
                    name TEXT PRIMARY KEY, password TEXT NOT NULL,
                    created TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS sessions (
                    digest TEXT PRIMARY KEY, actor TEXT NOT NULL,
                    created REAL NOT NULL, expires REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS sessions_by_actor ON sessions(actor, expires);
            ''')

    # ---- accounts -------------------------------------------------------
    def add(self, name: str, password: str) -> str:
        name = validate_username(name)
        encoded = hash_password(validate_password(password))
        with self.store.db() as c:
            if c.execute('SELECT 1 FROM operators WHERE name=?', (name,)).fetchone():
                raise ValueError('That operator already exists')
            c.execute('INSERT INTO operators(name,password,created) VALUES(?,?,?)', (name, encoded, now()))
        self.store.event(name, 'operator-created', name)
        return name

    def set_password(self, name: str, password: str) -> None:
        name = validate_username(name)
        encoded = hash_password(validate_password(password))
        with self.store.db() as c:
            if not c.execute('UPDATE operators SET password=? WHERE name=?', (encoded, name)).rowcount:
                raise ValueError('No such operator')
            c.execute('DELETE FROM sessions WHERE actor=?', (name,))
        self.store.event(name, 'operator-password-changed', name)

    def remove(self, name: str) -> None:
        name = validate_username(name)
        with self.store.db() as c:
            if not c.execute('DELETE FROM operators WHERE name=?', (name,)).rowcount:
                raise ValueError('No such operator')
            c.execute('DELETE FROM sessions WHERE actor=?', (name,))
        self.store.event(name, 'operator-removed', name)

    def list(self) -> list[dict]:
        with self.store.db() as c:
            rows = c.execute('SELECT name,created,disabled FROM operators ORDER BY name').fetchall()
        return [{'name': r['name'], 'created': r['created'], 'disabled': bool(r['disabled'])} for r in rows]

    def exists(self, name: str) -> bool:
        with self.store.db() as c:
            return c.execute('SELECT 1 FROM operators WHERE name=? AND disabled=0', (name,)).fetchone() is not None

    def authenticate(self, name: str, password: str) -> str | None:
        """Return the canonical operator name for valid credentials, else ``None``."""
        try:
            name = validate_username(name)
        except ValueError:
            verify_password(password, _DUMMY_HASH)
            return None
        with self.store.db() as c:
            row = c.execute('SELECT password FROM operators WHERE name=? AND disabled=0', (name,)).fetchone()
        if not row:
            verify_password(password, _DUMMY_HASH)
            return None
        return name if verify_password(password, row['password']) else None

    # ---- sessions -------------------------------------------------------
    def create_session(self, actor: str, remember: bool = False) -> tuple[str, int]:
        """Create a session; return the cookie value and its lifetime in seconds."""
        lifetime = REMEMBER_SECONDS if remember else SESSION_SECONDS
        token = secrets.token_urlsafe(32)
        t = time.time()
        with self.store.db() as c:
            c.execute('DELETE FROM sessions WHERE expires<=?', (t,))
            c.execute('INSERT INTO sessions VALUES(?,?,?,?)', (_digest(token), actor, t, t + lifetime))
            # Cap concurrent sessions per operator; the oldest are revoked first.
            c.execute('''DELETE FROM sessions WHERE actor=? AND digest NOT IN (
                           SELECT digest FROM sessions WHERE actor=? ORDER BY created DESC LIMIT ?)''',
                      (actor, actor, MAX_SESSIONS_PER_ACTOR))
        return token, lifetime

    def session(self, token: str) -> Session | None:
        if not token or len(token) > 128:
            return None
        with self.store.db() as c:
            row = c.execute('SELECT digest,actor,expires FROM sessions WHERE digest=?', (_digest(token),)).fetchone()
        if not row or row['expires'] <= time.time():
            return None
        if row['actor'] != SETUP_ACTOR and not self.exists(row['actor']):
            self.revoke(token)
            return None
        return Session(row['digest'], row['actor'], int(row['expires']))

    def revoke(self, token: str) -> None:
        with self.store.db() as c:
            c.execute('DELETE FROM sessions WHERE digest=?', (_digest(token),))

    def revoke_all(self, actor: str | None = None) -> int:
        with self.store.db() as c:
            if actor is None:
                return c.execute('DELETE FROM sessions').rowcount
            return c.execute('DELETE FROM sessions WHERE actor=?', (actor,)).rowcount


class LoginThrottle:
    """Sliding-window failure limits per client address, per username and globally.

    Behind a tunnel every client appears as loopback, so the per-username and
    global windows still bound online guessing in that deployment.
    """

    def __init__(self, per_key: int = 8, global_limit: int = 60, window: float = 900.0):
        self.per_key, self.global_limit, self.window = per_key, global_limit, window
        self.failures: dict[str, list[float]] = {}

    def _recent(self, key: str, t: float) -> list[float]:
        values = [x for x in self.failures.get(key, []) if x > t - self.window]
        if values:
            self.failures[key] = values
        else:
            self.failures.pop(key, None)
        return values

    def check(self, client: str, username: str) -> None:
        t = time.time()
        if (len(self._recent('ip:' + client, t)) >= self.per_key or
                (username and len(self._recent('user:' + username, t)) >= self.per_key) or
                len(self._recent('*', t)) >= self.global_limit):
            raise PermissionError('Too many failed sign-in attempts. Wait 15 minutes and try again.')

    def failed(self, client: str, username: str) -> None:
        t = time.time()
        for key in ('ip:' + client, '*', *(('user:' + username,) if username else ())):
            self.failures.setdefault(key, []).append(t)
        if len(self.failures) > 10000:  # Bound memory under a spray of distinct names.
            for key in list(self.failures)[:5000]:
                self.failures.pop(key, None)

    def succeeded(self, client: str, username: str) -> None:
        self.failures.pop('ip:' + client, None)
        if username:
            self.failures.pop('user:' + username, None)
