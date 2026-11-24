"""Dens: encrypted, serverless shared workspaces between DreadFox installs (peer-to-peer over Tor).

A den is a group of DreadFox nodes that verified each other through signed invites. Members share an
encrypted log (see den_store) that syncs directly between whichever members are online; entries new
to a node are forwarded to its other peers, so the mesh grows without any central server.

What a den carries (all end-to-end encrypted under the den key; signed by the author node):
  chat        human operator chat
  note        shared project notes            finding   shared findings (severity + detail)
  file/chunk  encrypted file sharing (verified by SHA-256 on download)
  agent_query a partner agent's light request  agent_answer  the reply
  proposal    a suggested command for a human to review (never executed by the den)

Hard rules:
  * Nothing from a den is ever executed. Proposals are text an operator may copy into their own
    Copilot, where local scope, guardrails and approval still apply.
  * Den content is untrusted data, never instructions.
  * Partner agent requests are light by design: 'recall' (answered from the shared den workspace,
    zero AI tokens) or 'ask' (one short question). Requests land in an inbox; answering
    automatically is opt-in per den. 'ask' answers use the local model by default (free); a cloud
    model must be explicitly allowed and is capped at a small token budget. Daily limits per partner.
  * Removing a member rotates the den key; removed members can't read anything posted after.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import secrets
import time
from pathlib import Path
from typing import Awaitable, Callable

from . import den_crypto as dc
from .den_net import DenNet, Dialer, Peer, TorRunner
from .den_store import DenError, DenStore, den_id_for, entry_hash, make_entry, msg_aad

LOG = logging.getLogger(__name__)
INVITE_PREFIX = 'fxden1.'
CHUNK = 192 * 1024
MAX_FILE = 10 * 1024 * 1024
SEVERITIES = ('info', 'low', 'medium', 'high', 'critical')
AUTO_MODES = ('off', 'workspace', 'workspace+ask')
DEFAULT_SETTINGS = {'auto_answer': 'off', 'daily_requests': 10, 'cloud_answers': False, 'cloud_max_tokens': 300,
                    'local_max_tokens': 4096}
OUTGOING_DAILY_REQUESTS = 25
RECONNECT_SECONDS = 30

# ask(question, max_tokens, allow_cloud) -> {'text','tokens','local'}; raises on refusal/failure
AskFn = Callable[[str, int, bool], Awaitable[dict]]
RecallFn = Callable[[str], Awaitable[list[str]]]


def _text(value, limit: int) -> str:
    text = str(value or '')
    text = ''.join(ch for ch in text if ch.isprintable() or ch in '\n\t')
    return text[:limit].strip()


def _sanitize(content: dict) -> dict | None:
    """Validate decrypted content from any member. Unknown kinds are dropped."""
    if not isinstance(content, dict):
        return None
    kind = content.get('kind')
    by = _text(content.get('by'), 40)
    if kind == 'chat':
        text = _text(content.get('text'), 4000)
        return {'kind': kind, 'text': text, 'by': by} if text else None
    if kind == 'note':
        title = _text(content.get('title'), 120)
        return {'kind': kind, 'title': title, 'body': _text(content.get('body'), 20000), 'by': by} if title else None
    if kind == 'finding':
        title = _text(content.get('title'), 120)
        sev = content.get('severity') if content.get('severity') in SEVERITIES else 'info'
        return {'kind': kind, 'title': title, 'severity': sev, 'detail': _text(content.get('detail'), 8000), 'by': by} if title else None
    if kind == 'file':
        name = re.sub(r'[^A-Za-z0-9._ -]', '_', _text(content.get('name'), 120)) or 'file'
        try:
            size, chunks = int(content.get('size')), int(content.get('chunks'))
        except (TypeError, ValueError):
            return None
        sha, fid = str(content.get('sha256', '')), str(content.get('file_id', ''))
        if not (0 <= size <= MAX_FILE and 1 <= chunks <= MAX_FILE // CHUNK + 1 and re.fullmatch(r'[0-9a-f]{64}', sha)
                and re.fullmatch(r'[0-9a-f]{24}', fid)):
            return None
        return {'kind': kind, 'file_id': fid, 'name': name, 'size': size, 'sha256': sha, 'chunks': chunks, 'by': by}
    if kind == 'chunk':
        fid, i, data = str(content.get('file_id', '')), content.get('i'), content.get('data')
        if not (re.fullmatch(r'[0-9a-f]{24}', fid) and isinstance(i, int) and 0 <= i < 100 and isinstance(data, str)
                and len(data) <= CHUNK * 2):
            return None
        return {'kind': kind, 'file_id': fid, 'i': i, 'data': data}
    if kind == 'agent_query':
        q = content.get('qtype')
        text = _text(content.get('text'), 500)
        to = str(content.get('to') or '*')
        if q not in ('recall', 'ask') or not text:
            return None
        return {'kind': kind, 'qtype': q, 'text': text, 'to': to[:64], 'by': by}
    if kind == 'agent_answer':
        ref = str(content.get('reply_to', ''))
        if not re.fullmatch(r'[0-9a-f]{64}', ref):
            return None
        src = content.get('source') if content.get('source') in ('workspace', 'memory', 'model', 'human') else 'human'
        tokens = content.get('tokens') if isinstance(content.get('tokens'), int) else 0
        return {'kind': kind, 'reply_to': ref, 'text': _text(content.get('text'), 4000), 'source': src,
                'tokens': max(0, min(tokens, 100000)), 'by': by}
    if kind == 'proposal':
        title = _text(content.get('title'), 120)
        cmd = _text(content.get('command'), 1000)
        return {'kind': kind, 'title': title, 'command': cmd, 'reason': _text(content.get('reason'), 1000), 'by': by} if title and cmd else None
    return None


def encode_invite(den: str, name: str, onion: str, pub: str, token: str) -> str:
    raw = dc.canonical({'d': den, 'n': name, 'o': onion, 'p': pub, 't': token})
    return INVITE_PREFIX + dc.b64e(raw)


def decode_invite(code: str) -> dict:
    code = code.strip()
    if not code.startswith(INVITE_PREFIX) or len(code) > 1000:
        raise DenError('Not a den invite code')
    try:
        data = json.loads(dc.b64d(code[len(INVITE_PREFIX):]))
    except (ValueError, dc.DenCryptoError) as exc:
        raise DenError('Damaged invite code') from exc
    if not (isinstance(data, dict) and re.fullmatch(r'[0-9a-f]{32}', str(data.get('d', ''))) and dc.valid_onion(str(data.get('o', '')))
            and isinstance(data.get('p'), str) and isinstance(data.get('t'), str)):
        raise DenError('Damaged invite code')
    return data


def _token_hash(token: str) -> str:
    return hashlib.sha256(('foxworx-den-invite:' + token).encode()).hexdigest()


class DenService:
    def __init__(self, root: Path, *, listen_port: int, dialer: Dialer, tor: TorRunner | None = None,
                 address: str = '', label: str = 'DreadFox', ask: AskFn | None = None, recall: RecallFn | None = None,
                 on_event: Callable[[str], None] | None = None):
        self.root = root
        self.store = DenStore(root)
        self.ident = dc.Identity.load_or_create(root / 'identity.json')
        self.tor = tor
        self._fixed_address = address
        self._label_path = root / 'label.txt'
        if not self._label_path.exists():
            self._label_path.write_text(dc.clean_label(label))
        self.ask_fn, self.recall_fn, self.on_event = ask, recall, on_event
        self.net = DenNet(self.ident, self.card, dialer, listen_port, self._on_message, self._on_connect, self._on_disconnect)
        self.version = 0
        self._card: dict | None = None
        self._joins: dict[str, asyncio.Future] = {}
        self._tasks: list[asyncio.Task] = []
        self._next_dial: dict[str, float] = {}
        self._started = False

    # -- identity -----------------------------------------------------------------------------
    @property
    def address(self) -> str:
        return self._fixed_address or (self.tor.onion if self.tor else '')

    @property
    def label(self) -> str:
        return dc.clean_label(self._label_path.read_text())

    def set_label(self, label: str) -> None:
        self._label_path.write_text(dc.clean_label(label))
        self._card = None
        self._bump()

    def card(self) -> dict:
        if self._card is None or self._card['onion'] != self.address or self._card['label'] != self.label:
            self._card = self.ident.card(self.address, self.label)
        return self._card

    def node(self) -> dict:
        tor = self.tor
        return {'id': self.ident.sign_pub, 'fingerprint': dc.fingerprint(self.ident.sign_pub), 'label': self.label,
                'address': self.address, 'tor': tor.status if tor else ('direct' if self._fixed_address else 'off'),
                'tor_detail': tor.detail if tor else '', 'peers': len(self.net.peers)}

    def _bump(self) -> None:
        self.version += 1

    def _event(self, action: str) -> None:
        if self.on_event:
            try:
                self.on_event(action)
            except Exception:  # nosec B110 - optional observer
                pass

    # -- lifecycle ----------------------------------------------------------------------------
    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        await self.net.start()
        loop = asyncio.get_running_loop()
        if self.tor is not None:
            self._tasks.append(loop.create_task(self._start_tor()))
        self._tasks.append(loop.create_task(self._maintain()))

    async def _start_tor(self) -> None:
        await self.tor.start()
        self._card = None
        self._bump()
        await self._refresh_my_cards()

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # nosec B110 - shutdown
                pass
        await self.net.stop()
        if self.tor is not None:
            await self.tor.stop()

    async def _maintain(self) -> None:
        while True:
            try:
                await self.connect_members()
            except Exception:
                LOG.exception('den maintenance failed')
            await asyncio.sleep(RECONNECT_SECONDS)

    async def connect_members(self) -> None:
        if not self.address:
            return
        now = time.monotonic()
        targets: dict[str, str] = {}
        for den in self.store.den_ids():
            st = self.store.state(den)
            if not self._is_active(st, self.ident.sign_pub):
                continue
            for pub, m in st['members'].items():
                if pub != self.ident.sign_pub and m['active'] and m['card'].get('onion'):
                    targets[pub] = m['card']['onion']
        for pub, addr in targets.items():
            if self.net.connected(pub) or self._next_dial.get(pub, 0) > now:
                continue
            self._next_dial[pub] = now + RECONNECT_SECONDS
            asyncio.get_running_loop().create_task(self.net.dial(addr, expect_pub=pub))

    async def _refresh_my_cards(self) -> None:
        """If my onion changed (first Tor start), publish an updated card in every den I'm in."""
        if not self.address:
            return
        for den in self.store.den_ids():
            st = self.store.state(den)
            me = st['members'].get(self.ident.sign_pub)
            if me and me['active'] and me['card'].get('onion') != self.address:
                await self._append(den, 'add', {'card': self.card(), 'role': me['role']})

    # -- helpers ------------------------------------------------------------------------------
    @staticmethod
    def _is_active(st: dict, pub: str) -> bool:
        m = st['members'].get(pub)
        return bool(m and m['active'])

    def _require_member(self, den: str) -> dict:
        if self.store.den_meta(den) is None:
            raise DenError('Unknown den')
        st = self.store.state(den)
        if not self._is_active(st, self.ident.sign_pub):
            raise DenError('You are no longer a member of this den')
        return st

    async def _append(self, den: str, type_: str, body: dict, epoch: int = 0) -> dict:
        seq, prev = self.store.head_hash(den, self.ident.sign_pub)
        entry = make_entry(self.ident, den, seq + 1, prev, type_, body, epoch)
        self.store.add_entry(entry)
        self._absorb_keys(den)
        self._bump()
        await self._broadcast(den, [entry])
        return entry

    async def _broadcast(self, den: str, entries: list[dict], skip: str = '') -> None:
        st = self.store.state(den)
        for pub, peer in list(self.net.peers.items()):
            if pub != skip and self._is_active(st, pub):
                try:
                    await peer.send({'t': 'entries', 'den': den, 'entries': entries})
                except Exception:  # nosec B110 - peer went away; it will resync on reconnect
                    pass

    def _absorb_keys(self, den: str) -> None:
        st = self.store.state(den)
        for e in self.store.entries(den):
            if e['type'] == 'key' and entry_hash(e) in st['accepted']:
                wrap = (e['body'].get('wraps') or {}).get(self.ident.sign_pub)
                epoch = e['body'].get('epoch')
                if wrap and isinstance(epoch, int) and self.store.key(den, epoch) is None:
                    try:
                        self.store.set_key(den, epoch, self.ident.open_sealed(wrap))
                    except dc.DenCryptoError:
                        LOG.warning('den key wrap for this node could not be opened')

    # -- den management -----------------------------------------------------------------------
    async def create(self, name: str, invite_policy: str = 'any') -> dict:
        name = _text(name, 60)
        if not name:
            raise DenError('Name the den')
        nonce = secrets.token_hex(16)
        den = den_id_for(self.ident.sign_pub, name, nonce)
        self.store.save_den_meta(den, {'joined': int(time.time()), 'settings': dict(DEFAULT_SETTINGS)})
        policy = 'admins' if invite_policy == 'admins' else 'any'
        genesis = make_entry(self.ident, den, 1, '', 'genesis',
                             {'name': name, 'nonce': nonce, 'card': self.card(), 'policy': {'invite': policy}})
        self.store.add_entry(genesis)
        key = dc.new_den_key()
        await self._append(den, 'key', {'epoch': 0, 'wraps': {self.ident.sign_pub: dc.seal(self.ident.box_pub, key)}})
        self.store.set_key(den, 0, key)
        self._event('den-created')
        return self.summary(den)

    def invite(self, den: str, hours: int = 24) -> dict:
        st = self._require_member(den)
        me = st['members'][self.ident.sign_pub]
        if st['policy']['invite'] == 'admins' and me['role'] != 'admin':
            raise DenError('Only den admins can invite in this den')
        if not self.address:
            raise DenError('This node has no network address yet (Tor is still starting).')
        token = dc.b64e(secrets.token_bytes(18))
        hours = max(1, min(int(hours), 168))
        expires = int(time.time()) + hours * 3600
        self.store.add_invite(_token_hash(token), den, expires)
        return {'code': encode_invite(den, st['name'], self.address, self.ident.sign_pub, token),
                'expires': expires, 'fingerprint': dc.fingerprint(self.ident.sign_pub)}

    async def join(self, code: str, timeout: float = 120.0) -> dict:
        inv = decode_invite(code)
        den = inv['d']
        if self.store.den_meta(den) is not None and self._is_active(self.store.state(den), self.ident.sign_pub):
            return self.summary(den)
        if not self.address:
            raise DenError('This node has no network address yet (Tor is still starting).')
        peer = await self.net.dial(inv['o'], expect_pub=inv['p'])
        if peer is None:
            raise DenError('Could not reach the inviter. They need FoxWorx running and online; try again.')
        fut = asyncio.get_running_loop().create_future()
        self._joins[den] = fut
        self.store.save_den_meta(den, {'joined': int(time.time()), 'settings': dict(DEFAULT_SETTINGS), 'pending': True})
        try:
            await peer.send({'t': 'join', 'den': den, 'token': inv['t']})
            async with asyncio.timeout(timeout):
                result = await fut
        except (TimeoutError, asyncio.TimeoutError) as exc:
            self.store.forget_den(den)
            raise DenError('The inviter did not answer in time.') from exc
        except DenError:
            self.store.forget_den(den)
            raise
        finally:
            self._joins.pop(den, None)
        meta = self.store.den_meta(den) or {}
        meta.pop('pending', None)
        self.store.save_den_meta(den, meta)
        self._event('den-joined')
        return result

    async def remove(self, den: str, member: str) -> dict:
        st = self._require_member(den)
        if st['members'][self.ident.sign_pub]['role'] != 'admin':
            raise DenError('Only den admins can remove members')
        if member == st['founder'] or member not in st['members'] or not st['members'][member]['active']:
            raise DenError('That member cannot be removed')
        last = self.store.heads(den).get(member, 0)
        await self._append(den, 'remove', {'member': member, 'last_seq': last})
        await self._rotate(den)
        return self.summary(den)

    async def _rotate(self, den: str) -> None:
        st = self.store.state(den)
        latest = self.store.latest_key(den)
        epoch = max(st['epoch'], latest[0] if latest else 0) + 1
        key = dc.new_den_key()
        wraps = {pub: dc.seal(m['card']['box_pub'], key) for pub, m in st['members'].items() if m['active']}
        await self._append(den, 'key', {'epoch': epoch, 'wraps': wraps})
        self.store.set_key(den, epoch, key)

    async def leave(self, den: str) -> None:
        st = self._require_member(den)
        if st['founder'] == self.ident.sign_pub:
            raise DenError('The founder cannot leave; remove the other members or keep the den.')
        await self._append(den, 'leave', {})
        self.store.forget_den(den)
        self._bump()

    def settings(self, den: str, changes: dict | None = None) -> dict:
        meta = self.store.den_meta(den)
        if meta is None:
            raise DenError('Unknown den')
        s = {**DEFAULT_SETTINGS, **meta.get('settings', {})}
        if changes:
            if changes.get('auto_answer') in AUTO_MODES:
                s['auto_answer'] = changes['auto_answer']
            if 'daily_requests' in changes:
                s['daily_requests'] = max(0, min(int(changes['daily_requests']), 100))
            if 'cloud_answers' in changes:
                s['cloud_answers'] = bool(changes['cloud_answers'])
            if 'cloud_max_tokens' in changes:
                s['cloud_max_tokens'] = max(50, min(int(changes['cloud_max_tokens']), 600))
            meta['settings'] = s
            self.store.save_den_meta(den, meta)
            self._bump()
        return s

    # -- reading ------------------------------------------------------------------------------
    def dens(self) -> list[dict]:
        out = []
        for den in self.store.den_ids():
            try:
                out.append(self.summary(den))
            except DenError:
                continue
        return sorted(out, key=lambda d: d['name'].lower())

    def summary(self, den: str) -> dict:
        meta = self.store.den_meta(den)
        if meta is None:
            raise DenError('Unknown den')
        st = self.store.state(den)
        me = st['members'].get(self.ident.sign_pub)
        members = []
        for pub, m in st['members'].items():
            members.append({'id': pub, 'label': m['card']['label'], 'fingerprint': dc.fingerprint(pub), 'role': m['role'],
                            'active': m['active'], 'online': pub == self.ident.sign_pub or self.net.connected(pub),
                            'me': pub == self.ident.sign_pub,
                            'safety_words': '' if pub == self.ident.sign_pub else dc.safety_words(self.ident.sign_pub, pub)})
        members.sort(key=lambda m: (not m['me'], not m['active'], m['label'].lower()))
        return {'id': den, 'name': st['name'] or '(joining…)', 'pending': bool(meta.get('pending')), 'founder': st['founder'],
                'role': me['role'] if me else '', 'active': bool(me and me['active']),
                'policy': st['policy']['invite'], 'members': members,
                'online': sum(1 for m in members if m['online'] and m['active']), 'settings': self.settings(den)}

    def feed(self, den: str, kinds: tuple[str, ...] = (), limit: int = 300) -> list[dict]:
        if self.store.den_meta(den) is None:
            raise DenError('Unknown den')
        st = self.store.state(den)
        items = []
        for e in self.store.entries(den):
            if e['type'] != 'msg' or entry_hash(e) not in st['accepted']:
                continue
            content = self._open(den, e)
            if content is None or content['kind'] == 'chunk' or (kinds and content['kind'] not in kinds):
                continue
            author = st['members'].get(e['author'])
            items.append({'id': entry_hash(e), 'ts': e['ts'], 'author': e['author'], 'mine': e['author'] == self.ident.sign_pub,
                          'author_label': author['card']['label'] if author else '?', **content})
        for item in items:
            if item['kind'] == 'agent_query':
                local = self.store.local_get(den, 'q:' + item['id']) or {}
                item['status'] = local.get('status', 'pending' if not item['mine'] else 'sent')
                item['for_me'] = not item['mine'] and item['to'] in ('*', self.ident.sign_pub)
        return items[-limit:]

    def _open(self, den: str, e: dict) -> dict | None:
        key = self.store.key(den, e['epoch'])
        if key is None:
            return None
        try:
            raw = dc.decrypt(key, e['body'].get('box') or {}, msg_aad(den, e['author'], e['seq'], e['epoch']))
            return _sanitize(json.loads(raw))
        except (dc.DenCryptoError, ValueError):
            return None

    # -- posting ------------------------------------------------------------------------------
    async def post(self, den: str, content: dict) -> dict:
        self._require_member(den)
        clean = _sanitize(content)
        if clean is None:
            raise DenError('Nothing to post, or unsupported content')
        latest = self.store.latest_key(den)
        if latest is None:
            raise DenError('This node has no key for the den yet')
        epoch, key = latest
        seq, _ = self.store.head_hash(den, self.ident.sign_pub)
        box = dc.encrypt(key, dc.canonical(clean), msg_aad(den, self.ident.sign_pub, seq + 1, epoch))
        entry = await self._append(den, 'msg', {'box': box}, epoch=epoch)
        if clean['kind'] in ('note', 'finding', 'file'):
            self._event('den-shared')
        return {'id': entry_hash(entry), **clean}

    async def share_file(self, den: str, name: str, data: bytes, by: str = '') -> dict:
        if not data or len(data) > MAX_FILE:
            raise DenError(f'Files must be 1 byte to {MAX_FILE // (1024 * 1024)} MB')
        fid = secrets.token_hex(12)
        chunks = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)]
        for i, part in enumerate(chunks):
            await self.post(den, {'kind': 'chunk', 'file_id': fid, 'i': i, 'data': base64.b64encode(part).decode()})
        return await self.post(den, {'kind': 'file', 'file_id': fid, 'name': name, 'size': len(data),
                                     'sha256': hashlib.sha256(data).hexdigest(), 'chunks': len(chunks), 'by': by})

    def file_bytes(self, den: str, file_id: str) -> tuple[str, bytes]:
        st = self.store.state(den)
        meta, parts = None, {}
        for e in self.store.entries(den):
            if e['type'] != 'msg' or entry_hash(e) not in st['accepted']:
                continue
            c = self._open(den, e)
            if not c or c.get('file_id') != file_id:
                continue
            if c['kind'] == 'file':
                meta = c
            elif c['kind'] == 'chunk' and c['i'] not in parts:
                parts[c['i']] = base64.b64decode(c['data'])
        if meta is None:
            raise DenError('Unknown file')
        if len(parts) < meta['chunks']:
            raise DenError('The file is still syncing; try again shortly')
        data = b''.join(parts[i] for i in range(meta['chunks']))
        if hashlib.sha256(data).hexdigest() != meta['sha256'] or len(data) != meta['size']:
            raise DenError('File failed its integrity check')
        return meta['name'], data

    async def request(self, den: str, qtype: str, text: str, to: str = '*', by: str = '') -> dict:
        """Ask partner agents for a light task (recall or one short question)."""
        today = time.strftime('%Y-%m-%d')
        sent = self.store.local_get(den, 'sent:' + today) or {'n': 0}
        if sent['n'] >= OUTGOING_DAILY_REQUESTS:
            raise DenError('Daily limit for partner requests reached; partners pay for their AI, keep it light.')
        item = await self.post(den, {'kind': 'agent_query', 'qtype': qtype, 'text': text, 'to': to, 'by': by})
        self.store.local_put(den, 'sent:' + today, {'n': sent['n'] + 1})
        return item

    # -- agent inbox --------------------------------------------------------------------------
    def _query(self, den: str, ref: str) -> dict:
        for item in self.feed(den, ('agent_query',)):
            if item['id'] == ref and item['for_me']:
                return item
        raise DenError('Unknown request')

    def workspace_answer(self, den: str, question: str) -> str:
        """Zero-token recall: the best-matching shared notes/findings in this den."""
        words = {w for w in re.findall(r'[a-z0-9]{3,}', question.lower())}
        scored = []
        for item in self.feed(den, ('note', 'finding')):
            hay = ' '.join([item.get('title', ''), item.get('body', ''), item.get('detail', '')]).lower()
            score = sum(1 for w in words if w in hay)
            if score:
                scored.append((score, item))
        scored.sort(key=lambda x: -x[0])
        lines = []
        for _, item in scored[:3]:
            summary = (item.get('body') or item.get('detail') or '')[:400]
            lines.append(f"• {item['title']} ({item.get('severity', 'note')}, by {item['author_label']}): {summary}")
        return '\n'.join(lines)

    def _budget(self, den: str, partner: str, settings: dict) -> dict:
        today = time.strftime('%Y-%m-%d')
        b = self.store.local_get(den, f'budget:{partner}:{today}') or {'requests': 0, 'tokens': 0}
        b['ok'] = b['requests'] < settings['daily_requests']
        return b

    def _spend(self, den: str, partner: str, tokens: int) -> None:
        today = time.strftime('%Y-%m-%d')
        b = self.store.local_get(den, f'budget:{partner}:{today}') or {'requests': 0, 'tokens': 0}
        b['requests'] += 1
        b['tokens'] += tokens
        self.store.local_put(den, f'budget:{partner}:{today}', b)

    async def draft(self, den: str, ref: str, source: str) -> dict:
        """Draft an answer for the operator to review. Nothing is sent."""
        q = self._query(den, ref)
        if source == 'workspace':
            return {'text': self.workspace_answer(den, q['text']) or 'No matching notes or findings in this den.',
                    'source': 'workspace', 'tokens': 0}
        if source == 'memory':
            if not self.recall_fn:
                raise DenError('Zenko memory is not available on this node')
            lessons = await self.recall_fn(q['text'])
            return {'text': '\n'.join('• ' + s for s in lessons) or 'No related lessons.', 'source': 'memory', 'tokens': 0}
        if source == 'model':
            return await self._model_answer(den, q)
        raise DenError('Unknown draft source')

    async def _model_answer(self, den: str, q: dict) -> dict:
        if not self.ask_fn:
            raise DenError('No AI model is configured on this node')
        s = self.settings(den)
        b = self._budget(den, q['author'], s)
        if not b['ok']:
            raise DenError("This partner reached today's request limit")
        result = await self.ask_fn(q['text'], s['local_max_tokens'] if not s['cloud_answers'] else s['cloud_max_tokens'],
                                   bool(s['cloud_answers']))
        self._spend(den, q['author'], int(result.get('tokens', 0)))
        return {'text': _text(result.get('text'), 4000), 'source': 'model', 'tokens': int(result.get('tokens', 0))}

    async def answer(self, den: str, ref: str, text: str, source: str = 'human', tokens: int = 0, by: str = '') -> dict:
        q = self._query(den, ref)
        item = await self.post(den, {'kind': 'agent_answer', 'reply_to': q['id'], 'text': text, 'source': source,
                                     'tokens': tokens, 'by': by})
        self.store.local_put(den, 'q:' + ref, {'status': 'answered'})
        self._bump()
        return item

    def decline(self, den: str, ref: str) -> None:
        self._query(den, ref)
        self.store.local_put(den, 'q:' + ref, {'status': 'declined'})
        self._bump()

    async def _auto_answer(self, den: str, entries: list[dict]) -> None:
        s = self.settings(den)
        if s['auto_answer'] == 'off':
            return
        hashes = {entry_hash(e) for e in entries}
        for q in self.feed(den, ('agent_query',)):
            if q['id'] not in hashes or not q['for_me'] or q['status'] != 'pending':
                continue
            try:
                if q['qtype'] == 'recall':
                    text = self.workspace_answer(den, q['text'])
                    if text:
                        await self.answer(den, q['id'], text, 'workspace')
                elif q['qtype'] == 'ask' and s['auto_answer'] == 'workspace+ask':
                    d = await self._model_answer(den, q)
                    if d['text']:
                        await self.answer(den, q['id'], d['text'], 'model', d['tokens'])
            except Exception as exc:  # leave it in the inbox for the operator
                LOG.info('den auto-answer skipped: %s', exc)

    # -- protocol -----------------------------------------------------------------------------
    async def _on_connect(self, peer: Peer) -> None:
        self._bump()
        for den in self.store.den_ids():
            st = self.store.state(den)
            if self._is_active(st, peer.pub) and self._is_active(st, self.ident.sign_pub):
                await peer.send({'t': 'heads', 'den': den, 'heads': self.store.heads(den)})

    async def _on_disconnect(self, peer: Peer) -> None:
        self._bump()

    async def _on_message(self, peer: Peer, msg: dict) -> None:
        t, den = msg.get('t'), str(msg.get('den', ''))
        if not re.fullmatch(r'[0-9a-f]{32}', den):
            return
        if t == 'join':
            await self._handle_join(peer, den, str(msg.get('token', '')))
            return
        if t == 'join-refused':
            fut = self._joins.get(den)
            if fut and not fut.done():
                fut.set_exception(DenError(_text(msg.get('reason'), 200) or 'The inviter refused the join'))
            return
        meta = self.store.den_meta(den)
        if meta is None:
            return
        st = self.store.state(den)
        joining = den in self._joins
        if not (self._is_active(st, peer.pub) or (joining and meta.get('pending'))):
            return
        if t == 'heads' and isinstance(msg.get('heads'), dict):
            have = {k: v for k, v in msg['heads'].items() if isinstance(k, str) and isinstance(v, int)}
            while True:
                batch = self.store.entries_after(den, have, limit=100)
                if not batch:
                    break
                await peer.send({'t': 'entries', 'den': den, 'entries': batch})
                for e in batch:
                    have[e['author']] = max(have.get(e['author'], 0), e['seq'])
        elif t == 'entries' and isinstance(msg.get('entries'), list):
            await self._ingest(peer, den, msg['entries'][:200])

    async def _ingest(self, peer: Peer, den: str, entries: list) -> None:
        new, gap = [], False
        for e in sorted((x for x in entries if isinstance(x, dict)), key=lambda x: (str(x.get('author')), x.get('seq', 0))):
            if e.get('den') != den:
                continue
            try:
                if self.store.add_entry(e):
                    new.append(e)
            except DenError:
                gap = True
            except dc.DenCryptoError:
                continue
        if gap:  # we're missing earlier entries: ask this peer for everything after our heads
            await peer.send({'t': 'heads', 'den': den, 'heads': self.store.heads(den)})
        if not new:
            return
        self._absorb_keys(den)
        self._bump()
        fut = self._joins.get(den)
        if fut and not fut.done():
            st = self.store.state(den)
            if self._is_active(st, self.ident.sign_pub) and self.store.latest_key(den):
                fut.set_result(self.summary(den))
        await self._broadcast(den, new, skip=peer.pub)  # mesh: pass new entries on to other members
        if any(e['type'] == 'msg' for e in new):
            self._event('den-message-received')
            await self._auto_answer(den, new)

    async def _handle_join(self, peer: Peer, den: str, token: str) -> None:
        async def refuse(reason: str) -> None:
            await peer.send({'t': 'join-refused', 'den': den, 'reason': reason})
        if self.store.den_meta(den) is None:
            return await refuse('Unknown den')
        st = self.store.state(den)
        me = st['members'].get(self.ident.sign_pub)
        if not me or not me['active']:
            return await refuse('The inviter is no longer a member of this den')
        if st['policy']['invite'] == 'admins' and me['role'] != 'admin':
            return await refuse('Only admins can invite to this den')
        if not self.store.use_invite(_token_hash(token), den):
            return await refuse('This invite is invalid, expired or already used')
        latest = self.store.latest_key(den)
        if latest is None:
            return await refuse('The inviter has no den key')
        epoch, key = latest
        existing = st['members'].get(peer.pub)
        if existing and not existing['active'] and me['role'] != 'admin':
            return await refuse('Only an admin can re-admit a removed member')
        await self._append(den, 'add', {'card': peer.card, 'role': 'member'})
        await self._append(den, 'key', {'epoch': epoch, 'wraps': {peer.pub: dc.seal(peer.card['box_pub'], key)}})
        self._event('den-member-added')
        await self._on_message(peer, {'t': 'heads', 'den': den, 'heads': {}})  # send the whole den history
