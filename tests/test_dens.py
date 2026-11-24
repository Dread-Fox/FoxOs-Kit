"""Dens: verification, encryption, mesh sync, files, agent limits and removal (real sockets, no Tor)."""
import asyncio
import socket
import time

import pytest

from foxworx import den_crypto as dc
from foxworx.den import DenService, decode_invite
from foxworx.den_net import Dialer
from foxworx.den_store import DenError, check_entry, make_entry, replay


def free_port() -> int:
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def node(tmp_path, name, ask=None, maintain=True):
    port = free_port()
    svc = DenService(tmp_path / name, listen_port=port, dialer=Dialer(allow_direct=True),
                     address=f'direct:127.0.0.1:{port}', label=name, ask=ask)
    await svc.net.start()
    if maintain:
        svc._started = True
    return svc


async def until(cond, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        await asyncio.sleep(0.05)
    raise AssertionError('condition not reached')


def texts(svc, den, kind='chat'):
    return [i.get('text') or i.get('title') for i in svc.feed(den, (kind,))]


@pytest.fixture
async def trio(tmp_path):
    a, b, c = [await node(tmp_path, n) for n in ('Alpha', 'Bravo', 'Charlie')]
    yield a, b, c
    for s in (a, b, c):
        await s.stop()


async def test_invite_join_chat_and_mesh_relay(trio):
    a, b, c = trio
    den = (await a.create('Lab den'))['id']
    await b.join(a.invite(den)['code'])
    # Charlie is invited by Bravo (any member may invite) and only ever talks to Bravo.
    await c.join(b.invite(den)['code'])
    assert {m['label'] for m in a.summary(den)['members']} >= {'Alpha', 'Bravo'}
    await until(lambda: len([m for m in c.summary(den)['members'] if m['active']]) == 3)
    await a.post(den, {'kind': 'chat', 'text': 'hello from alpha'})
    # Alpha never connected to Charlie: the message reaches Charlie through Bravo (mesh relay).
    assert not a.net.connected(c.ident.sign_pub)
    await until(lambda: 'hello from alpha' in texts(c, den))
    await c.post(den, {'kind': 'chat', 'text': 'charlie here'})
    await until(lambda: 'charlie here' in texts(a, den))
    # Everyone sees the same safety words for a pair.
    a_view = {m['id']: m['safety_words'] for m in a.summary(den)['members']}
    b_view = {m['id']: m['safety_words'] for m in b.summary(den)['members']}
    assert a_view[b.ident.sign_pub] == b_view[a.ident.sign_pub]


async def test_invites_are_single_use_and_checked(trio):
    a, b, c = trio
    den = (await a.create('Den'))['id']
    code = a.invite(den)['code']
    await b.join(code)
    with pytest.raises(DenError, match='invalid, expired or already used'):
        await c.join(code, timeout=10)
    tampered = decode_invite(a.invite(den)['code'])
    bogus = 'fxden1.' + dc.b64e(dc.canonical({**tampered, 't': 'not-the-token'}))
    with pytest.raises(DenError):
        await c.join(bogus, timeout=10)
    assert c.store.den_meta(den) is None  # failed joins leave nothing behind


async def test_files_are_encrypted_and_integrity_checked(trio):
    a, b, _ = trio
    den = (await a.create('Files'))['id']
    await b.join(a.invite(den)['code'])
    payload = bytes(range(256)) * 2000  # ~500 KB, several chunks
    item = await a.share_file(den, '../evil name.bin', payload, by='op')
    assert item['name'] == '.._evil name.bin' and item['size'] == len(payload)
    await until(lambda: any(f['file_id'] == item['file_id'] for f in b.feed(den, ('file',))))
    await until(lambda: b.file_bytes(den, item['file_id'])[1] == payload)
    # Chunks are not shown as feed items, and the stored entries hold no plaintext.
    assert not any(i['kind'] == 'chunk' for i in b.feed(den))
    raw = b''.join(e['body'].get('box', {}).get('c', '').encode() for e in b.store.entries(den))
    assert b'evil name' not in raw


async def test_agent_requests_are_light_and_inboxed(tmp_path):
    calls = []

    async def ask(question, max_tokens, allow_cloud):
        calls.append((question, max_tokens, allow_cloud))
        return {'text': 'short answer', 'tokens': 42, 'local': True}
    a = await node(tmp_path, 'A')
    b = await node(tmp_path, 'B', ask=ask)
    try:
        den = (await a.create('Agents'))['id']
        await b.join(a.invite(den)['code'])
        await a.post(den, {'kind': 'note', 'title': 'Redis on lab host', 'body': 'redis 6379 open, no auth on lab box'})
        await until(lambda: texts(b, den, 'note'))
        q = await a.request(den, 'recall', 'anything about redis?')
        await until(lambda: any(i['id'] == q['id'] for i in b.feed(den, ('agent_query',))))
        # Default: inbox only, nothing answered automatically and no AI called.
        await asyncio.sleep(0.3)
        assert not b.feed(den, ('agent_answer',)) and not calls
        draft = await b.draft(den, q['id'], 'workspace')
        assert 'Redis on lab host' in draft['text'] and draft['tokens'] == 0
        await b.answer(den, q['id'], draft['text'], 'workspace')
        await until(lambda: any(x['reply_to'] == q['id'] for x in a.feed(den, ('agent_answer',))))
        # Opt-in auto answers: recall from the workspace costs no tokens.
        b.settings(den, {'auto_answer': 'workspace'})
        q2 = await a.request(den, 'recall', 'redis auth?')
        await until(lambda: any(x['reply_to'] == q2['id'] for x in a.feed(den, ('agent_answer',))))
        assert not calls
        # 'ask' uses the model with the local cap by default; cloud must be allowed explicitly.
        b.settings(den, {'auto_answer': 'workspace+ask', 'daily_requests': 1})
        q3 = await a.request(den, 'ask', 'one-line tip for redis hardening?')
        await until(lambda: any(x['reply_to'] == q3['id'] for x in a.feed(den, ('agent_answer',))))
        assert calls[-1][1:] == (4096, False)
        # Daily per-partner budget: the next model request is refused and stays in the inbox.
        q4 = await a.request(den, 'ask', 'another?')
        await until(lambda: any(i['id'] == q4['id'] for i in b.feed(den, ('agent_query',))))
        await asyncio.sleep(0.3)
        assert len(calls) == 1
        with pytest.raises(DenError, match='limit'):
            await b.draft(den, q4['id'], 'model')
        b.settings(den, {'cloud_answers': True, 'cloud_max_tokens': 5000})
        assert b.settings(den)['cloud_max_tokens'] == 600  # hard ceiling for paid accounts
    finally:
        await a.stop()
        await b.stop()


async def test_requests_reject_heavy_kinds(trio):
    a, _, _ = trio
    den = (await a.create('Den'))['id']
    with pytest.raises(DenError):
        await a.request(den, 'scan', 'nmap the world')
    with pytest.raises(DenError):
        await a.post(den, {'kind': 'exec', 'command': 'rm -rf /'})


async def test_removal_rotates_key_and_blocks_backdated_posts(trio):
    a, b, c = trio
    den = (await a.create('Rotate'))['id']
    await b.join(a.invite(den)['code'])
    await c.join(a.invite(den)['code'])
    await until(lambda: len(b.summary(den)['members']) == 3)
    await a.remove(den, c.ident.sign_pub)
    await until(lambda: not [m for m in b.summary(den)['members'] if m['id'] == c.ident.sign_pub][0]['active'])
    await a.post(den, {'kind': 'chat', 'text': 'after removal'})
    await until(lambda: 'after removal' in texts(b, den))
    assert a.store.latest_key(den)[0] == 1 and b.store.latest_key(den)[0] == 1
    # Charlie keeps the old key only: new content is unreadable to them.
    assert c.store.latest_key(den)[0] == 0
    # A post Charlie signs after removal, even backdated, is not accepted by members.
    seq, prev = c.store.head_hash(den, c.ident.sign_pub)
    forged = make_entry(c.ident, den, seq + 1, prev, 'msg', {'box': dc.encrypt(c.store.key(den, 0), b'{}', b'x')}, 0)
    forged['ts'] = 1
    forged['sig'] = c.ident.sign_bytes(dc.canonical({k: v for k, v in forged.items() if k != 'sig'}))
    a.store.add_entry(forged)
    from foxworx.den_store import entry_hash
    assert entry_hash(forged) not in a.store.state(den)['accepted']


def test_entries_are_tamper_evident_and_non_members_ignored():
    founder, outsider, friend = dc.Identity.generate(), dc.Identity.generate(), dc.Identity.generate()
    from foxworx.den_store import den_id_for
    den = den_id_for(founder.sign_pub, 'D', 'n')
    g = make_entry(founder, den, 1, '', 'genesis', {'name': 'D', 'nonce': 'n', 'card': founder.card('', 'F')})
    bad = dict(g, body={**g['body'], 'name': 'Other'})
    with pytest.raises(dc.DenCryptoError):
        check_entry(bad)
    # An outsider adding themselves to someone else's den is ignored by replay.
    rogue = make_entry(outsider, den, 1, '', 'add', {'card': friend.card('', 'X')})
    st = replay([g, rogue], den)
    assert friend.sign_pub not in st['members'] and outsider.sign_pub not in st['members']
    # Clock skew: a new member's first post timestamped before their add is still accepted.
    from foxworx.den_store import entry_hash
    add = make_entry(founder, den, 2, entry_hash(g), 'add', {'card': friend.card('', 'Friend')})
    early = make_entry(friend, den, 1, '', 'leave', {})
    early['ts'] = g['ts'] - 30
    early['sig'] = friend.sign_bytes(dc.canonical({k: v for k, v in early.items() if k != 'sig'}))
    st = replay([g, add, early], den)
    assert entry_hash(early) in st['accepted'] and not st['members'][friend.sign_pub]['active']


async def test_handshake_rejects_wrong_identity(tmp_path):
    a = await node(tmp_path, 'A')
    b = await node(tmp_path, 'B')
    try:
        # Dialing B's address while expecting A's key must fail.
        assert await a.net.dial(b.address, expect_pub=a.ident.sign_pub) is None
        peer = await a.net.dial(b.address, expect_pub=b.ident.sign_pub)
        assert peer is not None and peer.pub == b.ident.sign_pub
    finally:
        await a.stop()
        await b.stop()
