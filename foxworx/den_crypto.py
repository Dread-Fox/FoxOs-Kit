"""Den cryptography: node identities, signatures, sealed key delivery and den content encryption.

Built only from primitives in the ``cryptography`` package (already a FOXWORX dependency):

* Identity: an Ed25519 signing key (who you are) and an X25519 key (how den keys reach you).
  A node's ID is its Ed25519 public key; its fingerprint is a short hash of that key.
* Cards: a node's public keys + onion address + label, self-signed, so they can't be altered.
* Sealed boxes: a den key is delivered to one member by ephemeral X25519 + HKDF-SHA256 +
  ChaCha20-Poly1305. Only that member's X25519 private key opens it.
* Den content: ChaCha20-Poly1305 under the den's current epoch key with a random 96-bit nonce and
  the entry's identifying fields as associated data, so ciphertext can't be moved between entries.
* Safety words: a pair of operators can read 6 words to each other to confirm, out of band, that
  they hold each other's real keys (both sides compute the same words).

No custom primitives; only standard constructions. Canonical JSON (sorted keys, compact) is what
gets signed and hashed.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

CARD_VERSION = 1
SEAL_INFO = b'foxworx-den-seal-v1'
RAW = serialization.Encoding.Raw
RAW_PUB = serialization.PublicFormat.Raw
RAW_PRIV = serialization.PrivateFormat.Raw
NO_ENC = serialization.NoEncryption()


class DenCryptoError(ValueError):
    """Raised for any signature, decryption or format failure. Messages never include key material."""


def b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')


def b64d(text: str) -> bytes:
    if not isinstance(text, str) or len(text) > 2_000_000:
        raise DenCryptoError('Malformed encoded field')
    try:
        return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))
    except (ValueError, TypeError) as exc:
        raise DenCryptoError('Malformed encoded field') from exc


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def digest(obj) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def fingerprint(sign_pub: str) -> str:
    """Short, human-comparable node ID: 16 hex chars of SHA-256(public key) in groups of 4."""
    h = hashlib.sha256(b64d(sign_pub)).hexdigest()[:16].upper()
    return '-'.join(h[i:i + 4] for i in range(0, 16, 4))


# 256 short, distinct, easy-to-say words for safety phrases (one word per byte).
WORDS = (
    'acorn alder amber anchor apple arrow ash aspen atlas aurora autumn badge badger bamboo banner barley '
    'basil beacon bear beech berry birch bison blaze bloom bluff bolt bonfire boulder bramble breeze brook '
    'cabin cactus canyon cedar chalk cherry cinder citrus clover cobalt comet copper coral cosmos cougar cove '
    'crane crater creek crest crow crystal cypress dagger dawn delta desert dew dingo dune dusk eagle ebony '
    'echo eclipse elder elk ember emerald falcon fable fern ferret field fig finch fjord flame flint forest '
    'fossil fox frost galaxy garnet gecko geyser ginger glacier glade granite grove gull harbor hare hawk '
    'hazel heath heron hollow honey horizon husky ibis iceberg indigo iris island ivory ivy jade jaguar jasper '
    'juniper kelp kestrel kettle kiwi koala lagoon lantern larch lark laurel lava lemon lichen lilac lily lime '
    'linden lotus lunar lynx magma magpie mallow maple marble marsh meadow mesa meteor mint mist moose moss '
    'moth nebula nectar nettle nova oak oasis ocean olive onyx opal orbit orchid osprey otter owl panda '
    'panther pearl pebble pepper petal pine plume polar poppy prairie prism puma quail quartz quill rain raven '
    'reed reef ridge river robin rowan ruby rune sable saffron sage salmon sandstone sapphire scarlet sequoia '
    'shadow shale shell sierra silver slate sparrow spruce squall star stone storm summit sunset swallow swan '
    'tamarack tansy thistle thorn thunder tide tiger timber topaz torch trail tulip tundra twilight umber '
    'valley velvet violet viper walnut willow wolf wren yarrow yew zenith zephyr agate alpine aster bayou cairn '
    'condor dahlia egret glimmer hemlock jasmine kingfisher lupine mango'
).split()
if len(WORDS) != 256 or len(set(WORDS)) != 256:
    raise RuntimeError("safety word list must hold 256 distinct words")


def safety_words(pub_a: str, pub_b: str, count: int = 6) -> str:
    """Same 6 words on both machines for a given pair of node keys (order-independent)."""
    a, b = sorted([b64d(pub_a), b64d(pub_b)])
    h = hashlib.sha256(b'foxworx-den-safety-v1' + a + b).digest()
    return ' '.join(WORDS[x] for x in h[:count])


@dataclass
class Identity:
    sign: Ed25519PrivateKey
    box: X25519PrivateKey

    @classmethod
    def generate(cls) -> 'Identity':
        return cls(Ed25519PrivateKey.generate(), X25519PrivateKey.generate())

    @classmethod
    def load_or_create(cls, path: Path) -> 'Identity':
        if path.exists():
            data = json.loads(path.read_text())
            return cls(Ed25519PrivateKey.from_private_bytes(b64d(data['sign'])),
                       X25519PrivateKey.from_private_bytes(b64d(data['box'])))
        ident = cls.generate()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = path.with_suffix('.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as fh:
            json.dump({'v': 1, 'sign': b64e(ident.sign.private_bytes(RAW, RAW_PRIV, NO_ENC)),
                       'box': b64e(ident.box.private_bytes(RAW, RAW_PRIV, NO_ENC))}, fh)
        os.replace(tmp, path)
        return ident

    @property
    def sign_pub(self) -> str:
        return b64e(self.sign.public_key().public_bytes(RAW, RAW_PUB))

    @property
    def box_pub(self) -> str:
        return b64e(self.box.public_key().public_bytes(RAW, RAW_PUB))

    def sign_bytes(self, data: bytes) -> str:
        return b64e(self.sign.sign(data))

    def card(self, onion: str, label: str) -> dict:
        body = {'v': CARD_VERSION, 'sign_pub': self.sign_pub, 'box_pub': self.box_pub,
                'onion': onion, 'label': clean_label(label), 'ts': int(time.time())}
        return {**body, 'sig': self.sign_bytes(canonical(body))}

    def open_sealed(self, sealed: dict) -> bytes:
        try:
            eph = X25519PublicKey.from_public_bytes(b64d(sealed['eph']))
            key = _seal_key(self.box.exchange(eph), b64d(sealed['eph']), b64d(self.box_pub))
            return ChaCha20Poly1305(key).decrypt(b64d(sealed['n']), b64d(sealed['c']), SEAL_INFO)
        except (KeyError, TypeError, ValueError, InvalidTag) as exc:
            raise DenCryptoError('Sealed key could not be opened') from exc


def clean_label(label: str) -> str:
    text = ''.join(ch for ch in str(label or '') if ch.isprintable())[:40].strip()
    return text or 'DreadFox'


def verify(sign_pub: str, data: bytes, sig: str) -> None:
    try:
        Ed25519PublicKey.from_public_bytes(b64d(sign_pub)).verify(b64d(sig), data)
    except (InvalidSignature, ValueError) as exc:
        raise DenCryptoError('Bad signature') from exc


def verify_card(card: dict) -> dict:
    if not isinstance(card, dict) or card.get('v') != CARD_VERSION:
        raise DenCryptoError('Unsupported card')
    body = {k: card.get(k) for k in ('v', 'sign_pub', 'box_pub', 'onion', 'label', 'ts')}
    if not all(isinstance(body[k], str) for k in ('sign_pub', 'box_pub', 'onion', 'label')):
        raise DenCryptoError('Malformed card')
    if len(b64d(body['sign_pub'])) != 32 or len(b64d(body['box_pub'])) != 32:
        raise DenCryptoError('Malformed card keys')
    if body['onion'] and not valid_onion(body['onion']):
        raise DenCryptoError('Malformed onion address')
    verify(body['sign_pub'], canonical(body), card.get('sig', ''))
    return {**body, 'sig': card['sig']}


def valid_onion(addr: str) -> bool:
    """v3 onion (56 base32 chars + .onion). Test transports may use 'direct:<host>:<port>'."""
    if addr.startswith('direct:'):
        return True
    host = addr[:-6] if addr.endswith('.onion') else ''
    return len(host) == 56 and all(c in 'abcdefghijklmnopqrstuvwxyz234567' for c in host)


def _seal_key(shared: bytes, eph_pub: bytes, recipient_pub: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=eph_pub + recipient_pub, info=SEAL_INFO).derive(shared)


def seal(recipient_box_pub: str, payload: bytes) -> dict:
    eph = X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes(RAW, RAW_PUB)
    recipient = X25519PublicKey.from_public_bytes(b64d(recipient_box_pub))
    key = _seal_key(eph.exchange(recipient), eph_pub, b64d(recipient_box_pub))
    nonce = os.urandom(12)
    return {'eph': b64e(eph_pub), 'n': b64e(nonce), 'c': b64e(ChaCha20Poly1305(key).encrypt(nonce, payload, SEAL_INFO))}


def new_den_key() -> bytes:
    return secrets.token_bytes(32)


def encrypt(key: bytes, plaintext: bytes, aad: bytes) -> dict:
    nonce = os.urandom(12)
    return {'n': b64e(nonce), 'c': b64e(ChaCha20Poly1305(key).encrypt(nonce, plaintext, aad))}


def decrypt(key: bytes, box: dict, aad: bytes) -> bytes:
    try:
        return ChaCha20Poly1305(key).decrypt(b64d(box['n']), b64d(box['c']), aad)
    except (KeyError, TypeError, InvalidTag) as exc:
        raise DenCryptoError('Den content could not be decrypted') from exc
