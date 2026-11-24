"""Den networking: a private Tor onion service per node, SOCKS5 dialing and an authenticated peer protocol.

* Tor: FOXWORX runs its own ``tor`` process as the service user (no root, no system torrc), with its
  data in the FOXWORX state directory. The onion service forwards ONLY to the den peer listener on
  127.0.0.1:<listen_port>, never to the workbench web app. The onion address is stable across restarts.
* Dialing: outbound connections go through that Tor's SOCKS5 port with remote name resolution.
* Handshake (over a WebSocket, JSON frames), after which both sides know each other's node key:
      client -> {t:hello, card, nonce}
      server -> {t:hello, card, nonce, sig(server signs client nonce + its onion + client key)}
      client -> {t:auth, sig(client signs server nonce + server onion + server key)}
  The client also checks that the server's card names the onion it dialed (Tor guarantees the
  onion address belongs to whoever answers it). The peer listener knows nothing but this protocol.
* Limits: frame size, connection count, handshake timeout and a per-peer message rate.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
import socket
import time
from pathlib import Path
from typing import Awaitable, Callable

from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import serve as ws_serve
from websockets.exceptions import WebSocketException

from . import den_crypto as dc

LOG = logging.getLogger(__name__)
PROTOCOL = 1
MAX_FRAME = 2_000_000
MAX_PEERS = 64
HANDSHAKE_TIMEOUT = 45
RATE_PER_SEC, RATE_BURST = 40.0, 400.0


class TorRunner:
    """Run a private tor instance whose onion service points at the den listener."""

    def __init__(self, root: Path, listen_port: int, socks_port: int, tor_path: str = ''):
        self.root = root
        self.listen_port, self.socks_port = listen_port, socks_port
        self.tor_path = tor_path or shutil.which('tor') or ''
        self.proc: asyncio.subprocess.Process | None = None
        self.status = 'missing' if not self.tor_path else 'stopped'
        self.onion = ''
        self.detail = ''

    def _torrc(self) -> Path:
        data, hs = self.root / 'data', self.root / 'hs'
        for d in (self.root, data, hs):
            d.mkdir(parents=True, exist_ok=True, mode=0o700)
            d.chmod(0o700)
        rc = self.root / 'torrc'
        rc.write_text('\n'.join([
            f'DataDirectory {data}',
            f'SocksPort 127.0.0.1:{self.socks_port}',
            f'HiddenServiceDir {hs}',
            'HiddenServiceVersion 3',
            f'HiddenServicePort 80 127.0.0.1:{self.listen_port}',
            f'Log notice file {self.root / "tor.log"}',
            'AvoidDiskWrites 1',
            'ClientUseIPv6 1',
            '']))
        rc.chmod(0o600)
        return rc

    async def start(self, wait: float = 180.0) -> str:
        if not self.tor_path:
            self.status, self.detail = 'missing', 'Install tor (sudo apt install tor) to connect dens over the internet.'
            return self.status
        rc = self._torrc()
        self._reap_stale(rc)
        log = self.root / 'tor.log'
        start_size = log.stat().st_size if log.exists() else 0
        self.status = 'starting'
        self.proc = await asyncio.create_subprocess_exec(
            self.tor_path, '-f', str(rc), '--RunAsDaemon', '0',
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        (self.root / 'tor.pid').write_text(str(self.proc.pid))
        deadline = time.monotonic() + wait
        hostname = self.root / 'hs' / 'hostname'
        while time.monotonic() < deadline:
            if self.proc.returncode is not None:
                self.status, self.detail = 'failed', self._log_tail(log, start_size)
                return self.status
            if hostname.exists() and not self.onion:
                self.onion = hostname.read_text().strip()
            if self.onion and 'Bootstrapped 100%' in self._log_tail(log, start_size, 200_000):
                self.status, self.detail = 'online', ''
                return self.status
            await asyncio.sleep(1)
        self.status, self.detail = 'starting', 'Tor is still connecting; dens will connect once it finishes.'
        asyncio.get_running_loop().create_task(self._finish_bootstrap(log, start_size))
        return self.status

    async def _finish_bootstrap(self, log: Path, start: int) -> None:
        while self.proc and self.proc.returncode is None and self.status == 'starting':
            if 'Bootstrapped 100%' in self._log_tail(log, start, 200_000):
                self.status, self.detail = 'online', ''
                return
            await asyncio.sleep(3)

    def _reap_stale(self, rc: Path) -> None:
        """Stop a tor left running by a crashed previous FOXWORX (only one started with this exact torrc)."""
        pidfile = self.root / 'tor.pid'
        try:
            pid = int(pidfile.read_text().strip())
            cmdline = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        except (OSError, ValueError):
            return
        if str(rc).encode() in cmdline and any(Path(a.decode(errors='replace')).name == 'tor' for a in cmdline[:1]):
            try:
                os.kill(pid, 15)
                for _ in range(50):
                    if not Path(f'/proc/{pid}').exists():
                        break
                    time.sleep(0.1)
            except OSError:
                pass
        pidfile.unlink(missing_ok=True)

    @staticmethod
    def _log_tail(log: Path, start: int, size: int = 1500) -> str:
        try:
            with open(log, 'rb') as fh:
                fh.seek(start)
                return fh.read()[-size:].decode(errors='replace')
        except OSError:
            return ''

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 10)
            except (asyncio.TimeoutError, TimeoutError):
                self.proc.kill()
        (self.root / 'tor.pid').unlink(missing_ok=True)
        self.status = 'stopped' if self.tor_path else 'missing'


async def socks5_socket(host: str, port: int, socks_port: int, timeout: float = 90.0) -> socket.socket:
    """A connected TCP socket to host:port through the local Tor SOCKS5 port (remote DNS)."""
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setblocking(False)

    async def recv_exact(n: int) -> bytes:
        buf = b''
        while len(buf) < n:
            chunk = await loop.sock_recv(sock, n - len(buf))
            if not chunk:
                raise ConnectionError('SOCKS proxy closed the connection')
            buf += chunk
        return buf
    try:
        async with asyncio.timeout(timeout):
            await loop.sock_connect(sock, ('127.0.0.1', socks_port))
            await loop.sock_sendall(sock, b'\x05\x01\x00')
            if await recv_exact(2) != b'\x05\x00':
                raise ConnectionError('SOCKS proxy refused the handshake')
            name = host.encode('ascii')
            await loop.sock_sendall(sock, b'\x05\x01\x00\x03' + bytes([len(name)]) + name + port.to_bytes(2, 'big'))
            head = await recv_exact(4)
            if head[1] != 0:
                raise ConnectionError(f'Tor could not reach the peer (SOCKS error {head[1]})')
            extra = {1: 4, 4: 16}.get(head[3])
            if extra is None:
                extra = (await recv_exact(1))[0]
            await recv_exact(extra + 2)
        return sock
    except BaseException:
        sock.close()
        raise


class Dialer:
    """Opens a socket to a peer address: '<56>.onion' via Tor, or 'direct:host:port' (tests/LAN)."""

    def __init__(self, socks_port: int = 0, allow_direct: bool = False):
        self.socks_port, self.allow_direct = socks_port, allow_direct

    async def open(self, address: str) -> socket.socket:
        if address.startswith('direct:'):
            if not self.allow_direct:
                raise ConnectionError('Direct connections are disabled')
            host, _, port = address[7:].rpartition(':')
            sock = socket.create_connection((host, int(port)), timeout=10)
            sock.setblocking(False)
            return sock
        if not dc.valid_onion(address):
            raise ConnectionError('Not an onion address')
        if not self.socks_port:
            raise ConnectionError('Tor is not available')
        return await socks5_socket(address, 80, self.socks_port)


class Peer:
    def __init__(self, ws, card: dict, outbound: bool):
        self.ws, self.card, self.outbound = ws, card, outbound
        self.pub = card['sign_pub']
        self.address = card['onion']
        self.since = time.time()
        self._lock = asyncio.Lock()
        self._tokens, self._last = RATE_BURST, time.monotonic()

    async def send(self, msg: dict) -> None:
        data = json.dumps(msg, separators=(',', ':'))
        async with self._lock:
            await self.ws.send(data)

    def allow(self) -> bool:
        now = time.monotonic()
        self._tokens = min(RATE_BURST, self._tokens + (now - self._last) * RATE_PER_SEC)
        self._last = now
        if self._tokens < 1:
            return False
        self._tokens -= 1
        return True


Handler = Callable[[Peer, dict], Awaitable[None]]


class DenNet:
    """Peer listener + dialer. Delivers authenticated messages to ``on_message``."""

    def __init__(self, ident: dc.Identity, card_fn: Callable[[], dict], dialer: Dialer, listen_port: int,
                 on_message: Handler, on_connect: Callable[[Peer], Awaitable[None]] | None = None,
                 on_disconnect: Callable[[Peer], Awaitable[None]] | None = None):
        self.ident, self.card_fn, self.dialer = ident, card_fn, dialer
        self.listen_port = listen_port
        self.on_message, self.on_connect, self.on_disconnect = on_message, on_connect, on_disconnect
        self.peers: dict[str, Peer] = {}
        self.server = None
        self._dialing: set[str] = set()

    async def start(self) -> None:
        self.server = await ws_serve(self._serve, '127.0.0.1', self.listen_port, max_size=MAX_FRAME,
                                     ping_interval=60, ping_timeout=120, compression=None)

    async def stop(self) -> None:
        for p in list(self.peers.values()):
            try:
                await p.ws.close()
            except Exception:  # nosec B110 - closing best-effort during shutdown
                pass
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    def connected(self, pub: str) -> bool:
        return pub in self.peers

    # -- handshake ------------------------------------------------------------------------------
    def _auth_payload(self, role: str, peer_pub: str, nonce: str, onion: str) -> bytes:
        return dc.canonical({'t': 'den-auth', 'v': PROTOCOL, 'role': role, 'peer': peer_pub, 'nonce': nonce, 'onion': onion})

    async def _serve(self, ws) -> None:
        if len(self.peers) >= MAX_PEERS:
            await ws.close(1013, 'busy')
            return
        try:
            async with asyncio.timeout(HANDSHAKE_TIMEOUT):
                hello = json.loads(await ws.recv())
                if hello.get('t') != 'hello' or hello.get('v') != PROTOCOL:
                    raise dc.DenCryptoError('Bad hello')
                card = dc.verify_card(hello.get('card'))
                if card['sign_pub'] == self.ident.sign_pub:
                    raise dc.DenCryptoError('Connected to self')
                mine = self.card_fn()
                s_nonce = dc.b64e(secrets.token_bytes(24))
                sig = self.ident.sign_bytes(self._auth_payload('server', card['sign_pub'], str(hello.get('nonce', '')), mine['onion']))
                await ws.send(json.dumps({'t': 'hello', 'v': PROTOCOL, 'card': mine, 'nonce': s_nonce, 'sig': sig}))
                auth = json.loads(await ws.recv())
                dc.verify(card['sign_pub'], self._auth_payload('client', self.ident.sign_pub, s_nonce, mine['onion']),
                          str(auth.get('sig', '')))
        except (dc.DenCryptoError, ValueError, KeyError, TypeError, WebSocketException, TimeoutError, OSError) as exc:
            LOG.info('den handshake rejected: %s', type(exc).__name__)
            try:
                await ws.close(1008, 'handshake failed')
            except Exception:  # nosec B110
                pass
            return
        await self._run(Peer(ws, card, outbound=False))

    async def dial(self, address: str, expect_pub: str = '') -> Peer | None:
        """Connect to a peer address; returns the authenticated Peer (or None on failure)."""
        if expect_pub and expect_pub in self.peers:
            return self.peers[expect_pub]
        if address in self._dialing or address == self.card_fn().get('onion'):
            return None
        self._dialing.add(address)
        try:
            sock = await self.dialer.open(address)
            host = address[7:] if address.startswith('direct:') else address
            ws = await ws_connect(f'ws://{host}/den', sock=sock, max_size=MAX_FRAME, open_timeout=HANDSHAKE_TIMEOUT,
                                  ping_interval=60, ping_timeout=120, compression=None)
            try:
                async with asyncio.timeout(HANDSHAKE_TIMEOUT):
                    c_nonce = dc.b64e(secrets.token_bytes(24))
                    await ws.send(json.dumps({'t': 'hello', 'v': PROTOCOL, 'card': self.card_fn(), 'nonce': c_nonce}))
                    reply = json.loads(await ws.recv())
                    card = dc.verify_card(reply.get('card'))
                    if card['onion'] != address or (expect_pub and card['sign_pub'] != expect_pub):
                        raise dc.DenCryptoError('Peer identity does not match the address dialed')
                    dc.verify(card['sign_pub'], self._auth_payload('server', self.ident.sign_pub, c_nonce, address),
                              str(reply.get('sig', '')))
                    sig = self.ident.sign_bytes(self._auth_payload('client', card['sign_pub'], str(reply.get('nonce', '')), address))
                    await ws.send(json.dumps({'t': 'auth', 'sig': sig}))
            except BaseException:
                await ws.close()
                raise
        except (dc.DenCryptoError, ValueError, KeyError, TypeError, WebSocketException, TimeoutError, OSError,
                ConnectionError) as exc:
            LOG.info('den dial %s failed: %s', address[:16], exc)
            return None
        finally:
            self._dialing.discard(address)
        peer = Peer(ws, card, outbound=True)
        asyncio.get_running_loop().create_task(self._run(peer))
        return peer

    async def _run(self, peer: Peer) -> None:
        old = self.peers.get(peer.pub)
        self.peers[peer.pub] = peer
        if old is not None:
            try:
                await old.ws.close(1000, 'replaced')
            except Exception:  # nosec B110
                pass
        try:
            if self.on_connect:
                await self.on_connect(peer)
            async for raw in peer.ws:
                if not isinstance(raw, str) or not peer.allow():
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    try:
                        await self.on_message(peer, msg)
                    except Exception:
                        LOG.exception('den message handler failed')
        except WebSocketException:
            pass
        finally:
            if self.peers.get(peer.pub) is peer:
                del self.peers[peer.pub]
                if self.on_disconnect:
                    try:
                        await self.on_disconnect(peer)
                    except Exception:
                        LOG.exception('den disconnect handler failed')


