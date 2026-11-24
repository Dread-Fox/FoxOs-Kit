"""Den storage: per-member signed, hash-chained logs and deterministic membership replay.

Every den is a set of append-only feeds, one per member. Each entry is signed by its author and
chained to that author's previous entry, so peers can relay each other's entries without being able
to forge, reorder, drop-from-the-middle or alter them. Any member can sync any other member's feed,
so someone who was offline catches up from whoever is online.

Entry (all fields signed):
    {v, den, author, seq, prev, ts, type, epoch, body, sig}
Types:
    genesis  founder creates the den (den id = hash of founder key + name + nonce)
    add      a member adds a new member card (any member, or admins only, per den policy)
    remove   an admin removes a member; records the last seq of theirs it accepts
    leave    a member removes themself
    key      den key wraps: rotation to a new epoch (admins) or the current key for a new member
    msg      encrypted content (chat, notes, findings, files, agent requests) under an epoch key

Membership is recomputed by replaying entries in (ts, hash) order. A removed member's entries are
accepted only up to the ``last_seq`` recorded in the removal, so backdated posts are rejected.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import den_crypto as dc

ENTRY_VERSION = 1
MAX_ENTRY_BYTES = 400_000
TYPES = ('genesis', 'add', 'remove', 'leave', 'key', 'msg')
ROLES = ('member', 'admin')


class DenError(ValueError):
    pass


def den_id_for(founder: str, name: str, nonce: str) -> str:
    return dc.digest({'founder': founder, 'name': name, 'nonce': nonce})[:32]


def entry_hash(entry: dict) -> str:
    return dc.digest(entry)


def msg_aad(den: str, author: str, seq: int, epoch: int) -> bytes:
    return dc.canonical({'den': den, 'author': author, 'seq': seq, 'epoch': epoch, 'type': 'msg'})


def check_entry(entry: dict) -> dict:
    """Structure + signature check. Membership is decided later by replay."""
    if not isinstance(entry, dict):
        raise DenError('Entry must be an object')
    keys = {'v', 'den', 'author', 'seq', 'prev', 'ts', 'type', 'epoch', 'body', 'sig'}
    if set(entry) != keys:
        raise DenError('Entry has unexpected fields')
    if entry['v'] != ENTRY_VERSION or entry['type'] not in TYPES:
        raise DenError('Unsupported entry')
    if not isinstance(entry['seq'], int) or entry['seq'] < 1 or not isinstance(entry['epoch'], int) or entry['epoch'] < 0:
        raise DenError('Bad sequence or epoch')
    if not isinstance(entry['ts'], int) or not isinstance(entry['body'], dict):
        raise DenError('Bad timestamp or body')
    if not all(isinstance(entry[k], str) for k in ('den', 'author', 'prev', 'sig')) or len(entry['den']) != 32:
        raise DenError('Bad identifiers')
    if len(dc.canonical(entry)) > MAX_ENTRY_BYTES:
        raise DenError('Entry too large')
    body = {k: v for k, v in entry.items() if k != 'sig'}
    dc.verify(entry['author'], dc.canonical(body), entry['sig'])
    return entry


def make_entry(ident: dc.Identity, den: str, seq: int, prev: str, type_: str, body: dict, epoch: int = 0) -> dict:
    entry = {'v': ENTRY_VERSION, 'den': den, 'author': ident.sign_pub, 'seq': seq, 'prev': prev,
             'ts': int(time.time()), 'type': type_, 'epoch': epoch, 'body': body}
    entry['sig'] = ident.sign_bytes(dc.canonical(entry))
    return check_entry(entry)


def _replay_once(entries: list[dict], den: str, bounds: dict[str, int]) -> tuple[dict, dict[str, int]]:
    """One causal replay with removal bounds applied from the start. Returns (state, new_bounds)."""
    state = {'name': '', 'founder': '', 'policy': {'invite': 'any'}, 'members': {}, 'accepted': set(), 'epoch': 0}
    members = state['members']
    found: dict[str, int] = {}
    ACCEPT, DEFER, REJECT = 1, 0, -1

    def within(pub: str, seq: int) -> bool:
        return pub in members and (pub not in bounds or seq <= bounds[pub])

    def apply(e: dict) -> int:
        body, author, t = e['body'], e['author'], e['type']
        if t == 'genesis':
            if state['founder'] or e['seq'] != 1:
                return REJECT
            name, nonce = str(body.get('name', ''))[:60], str(body.get('nonce', ''))
            try:
                card = dc.verify_card(body.get('card') or {})
            except dc.DenCryptoError:
                return REJECT
            if card['sign_pub'] != author or den_id_for(author, name, nonce) != den:
                return REJECT
            state.update(name=name, founder=author,
                         policy={'invite': 'admins' if (body.get('policy') or {}).get('invite') == 'admins' else 'any'})
            members[author] = {'card': card, 'role': 'admin', 'added_by': '', 'joined': e['ts']}
            return ACCEPT
        if not state['founder'] or author not in members:
            return DEFER
        if not within(author, e['seq']):
            return REJECT
        is_admin = members[author]['role'] == 'admin'
        if t == 'add':
            try:
                card = dc.verify_card(body.get('card') or {})
            except dc.DenCryptoError:
                return REJECT
            if card['sign_pub'] == author:  # a member refreshing their own card (e.g. new onion address)
                members[author]['card'] = card
                return ACCEPT
            if state['policy']['invite'] == 'admins' and not is_admin:
                return REJECT
            role = body.get('role') if body.get('role') in ROLES else 'member'
            if role == 'admin' and not is_admin:
                role = 'member'
            pub = card['sign_pub']
            if pub in members:
                if pub not in bounds:
                    members[pub]['card'] = card  # card refresh (e.g. new onion address)
                return ACCEPT
            members[pub] = {'card': card, 'role': role, 'added_by': author, 'joined': e['ts']}
        elif t == 'remove':
            target = body.get('member', '')
            if not is_admin or target == state['founder']:
                return REJECT
            if target not in members:
                return DEFER
            last = body.get('last_seq')
            last = last if isinstance(last, int) and last >= 0 else 0
            found[target] = min(found.get(target, last), last)
        elif t == 'leave':
            if author == state['founder']:
                return REJECT
            found[author] = min(found.get(author, e['seq']), e['seq'])
        elif t == 'key':
            ep = body.get('epoch')
            if not isinstance(ep, int) or ep < 0:
                return REJECT
            if ep > state['epoch']:
                if not is_admin:
                    return REJECT
                state['epoch'] = ep
        return ACCEPT

    pending = sorted(entries, key=lambda e: (e['type'] != 'genesis', e['ts'], entry_hash(e)))
    changed = True
    while changed and pending:
        changed = False
        rest = []
        for e in pending:
            r = apply(e)
            if r == ACCEPT:
                state['accepted'].add(entry_hash(e))
                changed = True
            elif r == DEFER:
                rest.append(e)
        pending = rest
    return state, found


def replay(entries: list[dict], den: str) -> dict:
    """Deterministic membership state from a den's entries.

    Causal, not clock-based: entries whose author isn't a member yet are deferred and retried, so
    clock skew can't drop a new member's first posts. Removal is bound to the removed member's
    ``last_seq`` (from the admin's remove entry, or their own leave), and those bounds are applied
    from the start of a fresh replay until they stop changing, so processing order can't let a
    removed member's later entries through.

    Returns {'name','founder','policy','members':{pub:{card,role,active,added_by,joined}},
             'accepted':set(hashes),'epoch':highest admin-rotated epoch}."""
    bounds: dict[str, int] = {}
    for _ in range(6):
        state, found = _replay_once(entries, den, bounds)
        if found == bounds:
            break
        bounds = found
    for pub, m in state['members'].items():
        m['active'] = pub not in bounds
    return state


class DenStore:
    """SQLite-backed den data (separate file, 0600, inside the FOXWORX state directory)."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        self.path = root / 'dens.sqlite3'
        self._lock = threading.RLock()
        self._replay_cache: dict[str, tuple[int, dict]] = {}
        with self.db() as c:
            c.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS entries (den TEXT, author TEXT, seq INTEGER, hash TEXT, ts INTEGER,
                    type TEXT, data TEXT, received INTEGER, PRIMARY KEY(den, author, seq));
                CREATE INDEX IF NOT EXISTS entries_hash ON entries(hash);
                CREATE TABLE IF NOT EXISTS dens (den TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS keys (den TEXT, epoch INTEGER, key TEXT, PRIMARY KEY(den, epoch));
                CREATE TABLE IF NOT EXISTS invites (token TEXT PRIMARY KEY, den TEXT, expires INTEGER,
                    used INTEGER DEFAULT 0, created INTEGER);
                CREATE TABLE IF NOT EXISTS local (den TEXT, ref TEXT, data TEXT, PRIMARY KEY(den, ref));
                CREATE TABLE IF NOT EXISTS equivocations (den TEXT, author TEXT, seq INTEGER, hash TEXT,
                    PRIMARY KEY(den, author, seq, hash));
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        with self._lock:
            c = sqlite3.connect(self.path, timeout=10)
            c.row_factory = sqlite3.Row
            try:
                with c:
                    yield c
            finally:
                c.close()

    # -- dens ---------------------------------------------------------------------------------
    def den_ids(self) -> list[str]:
        with self.db() as c:
            return [r['den'] for r in c.execute('SELECT den FROM dens')]

    def den_meta(self, den: str) -> dict | None:
        with self.db() as c:
            row = c.execute('SELECT data FROM dens WHERE den=?', (den,)).fetchone()
        return json.loads(row['data']) if row else None

    def save_den_meta(self, den: str, data: dict) -> None:
        with self.db() as c:
            c.execute('INSERT INTO dens VALUES(?,?) ON CONFLICT(den) DO UPDATE SET data=excluded.data',
                      (den, json.dumps(data)))

    def forget_den(self, den: str) -> None:
        with self.db() as c:
            for table in ('entries', 'dens', 'keys', 'invites', 'local', 'equivocations'):
                c.execute(f'DELETE FROM {table} WHERE den=?', (den,))  # nosec B608 - fixed table names
        self._replay_cache.pop(den, None)

    # -- entries ------------------------------------------------------------------------------
    def heads(self, den: str) -> dict[str, int]:
        with self.db() as c:
            return {r['author']: r['m'] for r in
                    c.execute('SELECT author, MAX(seq) AS m FROM entries WHERE den=? GROUP BY author', (den,))}

    def head_hash(self, den: str, author: str) -> tuple[int, str]:
        with self.db() as c:
            row = c.execute('SELECT seq, hash FROM entries WHERE den=? AND author=? ORDER BY seq DESC LIMIT 1',
                            (den, author)).fetchone()
        return (row['seq'], row['hash']) if row else (0, '')

    def add_entry(self, entry: dict) -> bool:
        """Store a verified entry if it extends its author's chain. Returns True if new."""
        check_entry(entry)
        h = entry_hash(entry)
        with self.db() as c:
            row = c.execute('SELECT hash FROM entries WHERE den=? AND author=? AND seq=?',
                            (entry['den'], entry['author'], entry['seq'])).fetchone()
            if row:
                if row['hash'] != h:  # same author signed two different entries at one position
                    c.execute('INSERT OR IGNORE INTO equivocations VALUES(?,?,?,?)',
                              (entry['den'], entry['author'], entry['seq'], h))
                return False
            prev = c.execute('SELECT hash FROM entries WHERE den=? AND author=? AND seq=?',
                             (entry['den'], entry['author'], entry['seq'] - 1)).fetchone()
            expected = prev['hash'] if prev else ''
            if (entry['seq'] == 1 and entry['prev'] != '') or (entry['seq'] > 1 and (not prev or entry['prev'] != expected)):
                raise DenError('Entry does not extend its author\'s chain')
            c.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,?,?)',
                      (entry['den'], entry['author'], entry['seq'], h, entry['ts'], entry['type'],
                       json.dumps(entry, separators=(',', ':')), int(time.time())))
        self._replay_cache.pop(entry['den'], None)
        return True

    def entries(self, den: str) -> list[dict]:
        with self.db() as c:
            return [json.loads(r['data']) for r in c.execute('SELECT data FROM entries WHERE den=? ORDER BY ts, author, seq', (den,))]

    def entries_after(self, den: str, have: dict[str, int], limit: int = 200) -> list[dict]:
        """Entries the peer lacks, given its heads, in per-author order."""
        out = []
        with self.db() as c:
            for r in c.execute('SELECT author, data, seq FROM entries WHERE den=? ORDER BY author, seq', (den,)):
                if r['seq'] > int(have.get(r['author'], 0)):
                    out.append(json.loads(r['data']))
                    if len(out) >= limit:
                        break
        return out

    def entry_by_hash(self, den: str, h: str) -> dict | None:
        with self.db() as c:
            row = c.execute('SELECT data FROM entries WHERE den=? AND hash=?', (den, h)).fetchone()
        return json.loads(row['data']) if row else None

    def state(self, den: str) -> dict:
        with self.db() as c:
            count = c.execute('SELECT COUNT(*) AS n FROM entries WHERE den=?', (den,)).fetchone()['n']
        cached = self._replay_cache.get(den)
        if cached and cached[0] == count:
            return cached[1]
        st = replay(self.entries(den), den)
        self._replay_cache[den] = (count, st)
        return st

    # -- keys / invites / local state ---------------------------------------------------------
    def set_key(self, den: str, epoch: int, key: bytes) -> None:
        with self.db() as c:
            c.execute('INSERT OR IGNORE INTO keys VALUES(?,?,?)', (den, epoch, dc.b64e(key)))

    def key(self, den: str, epoch: int) -> bytes | None:
        with self.db() as c:
            row = c.execute('SELECT key FROM keys WHERE den=? AND epoch=?', (den, epoch)).fetchone()
        return dc.b64d(row['key']) if row else None

    def latest_key(self, den: str) -> tuple[int, bytes] | None:
        with self.db() as c:
            row = c.execute('SELECT epoch, key FROM keys WHERE den=? ORDER BY epoch DESC LIMIT 1', (den,)).fetchone()
        return (row['epoch'], dc.b64d(row['key'])) if row else None

    def add_invite(self, token_hash: str, den: str, expires: int) -> None:
        with self.db() as c:
            c.execute('INSERT INTO invites VALUES(?,?,?,0,?)', (token_hash, den, expires, int(time.time())))

    def use_invite(self, token_hash: str, den: str) -> bool:
        with self.db() as c:
            n = c.execute('UPDATE invites SET used=1 WHERE token=? AND den=? AND used=0 AND expires>?',
                          (token_hash, den, int(time.time()))).rowcount
        return bool(n)

    def local_get(self, den: str, ref: str) -> dict | None:
        with self.db() as c:
            row = c.execute('SELECT data FROM local WHERE den=? AND ref=?', (den, ref)).fetchone()
        return json.loads(row['data']) if row else None

    def local_put(self, den: str, ref: str, data: dict) -> None:
        with self.db() as c:
            c.execute('INSERT INTO local VALUES(?,?,?) ON CONFLICT(den, ref) DO UPDATE SET data=excluded.data',
                      (den, ref, json.dumps(data)))
