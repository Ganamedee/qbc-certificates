"""Sealed cards: a season entry that only the club's scoring workflow can open.

A card is sealed to the club's season key with ECIES on X25519 (RFC 7748): a fresh key pair for every card,
HKDF-SHA256 (RFC 5869) for two keys, an HMAC-SHA256 key stream for the text and an HMAC-SHA256 tag over all of
it (encrypt, then MAC). Standard library only, so it runs in every member's venv and in the workflow. The key
pair comes from the SEASONS_SEED secret: the workflow publishes the public half, and the private half never
leaves the workflow.

    text = seal({"season": 1, ...}, public_key, season=1)     # what a member pastes into the form
    card = open_card(text, private_key, season=1)            # the workflow, with the secret
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import zlib

MAGIC = b"QBC1"
BEGIN = "-----BEGIN QBC SEALED CARD-----"
END = "-----END QBC SEALED CARD-----"
KEY_PREFIX = "qbc-seasons-key-1 "
MAX_TEXT = 60_000          # an issue body holds 65,536 characters; the form adds its headings
MAX_PAYLOAD = 200_000      # the opened card, after decompression

_P = 2**255 - 19
_A24 = 121665
_BASE = (9).to_bytes(32, "little")
_ARMOUR = re.compile(re.escape(BEGIN) + r"\s*([A-Za-z0-9+/=\s]+?)\s*" + re.escape(END))


class SealError(ValueError):
    """A card that can't be sealed or opened. The message is safe to show the member."""


def _clamp(k: bytes) -> int:
    b = bytearray(k)
    b[0] &= 248
    b[31] &= 127
    b[31] |= 64
    return int.from_bytes(b, "little")


def x25519(k: bytes, u: bytes) -> bytes:
    """RFC 7748 X25519: scalar k times the point with u-coordinate u (both 32 bytes, little-endian)."""
    if len(k) != 32 or len(u) != 32:
        raise SealError("X25519 takes 32-byte values")
    n = _clamp(k)
    x1 = int.from_bytes(u[:31] + bytes([u[31] & 127]), "little") % _P
    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    for t in range(254, -1, -1):
        bit = (n >> t) & 1
        swap ^= bit
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = bit
        a = (x2 + z2) % _P
        aa = a * a % _P
        b = (x2 - z2) % _P
        bb = b * b % _P
        e = (aa - bb) % _P
        c = (x3 + z3) % _P
        d = (x3 - z3) % _P
        da = d * a % _P
        cb = c * b % _P
        x3 = (da + cb) * (da + cb) % _P
        z3 = x1 * (da - cb) * (da - cb) % _P
        x2 = aa * bb % _P
        z2 = e * (aa + _A24 * e) % _P
    if swap:
        x2, x3, z2, z3 = x3, x2, z3, z2
    return (x2 * pow(z2, _P - 2, _P) % _P).to_bytes(32, "little")


def hkdf(ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    """RFC 5869 HKDF with SHA-256."""
    prk = hmac.new(salt or bytes(32), ikm, hashlib.sha256).digest()
    out, block, i = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([i]), hashlib.sha256).digest()
        out += block
        i += 1
    return out[:length]


def private_key_from_seed(seed: str) -> bytes:
    """The season key's private half, from the SEASONS_SEED secret (any text of 32 or more characters)."""
    if not isinstance(seed, str) or len(seed.strip()) < 32:
        raise SealError("SEASONS_SEED must be at least 32 characters")
    return hkdf(seed.strip().encode(), b"", b"qbc seasons sealing key v1", 32)


def public_key(private_key: bytes) -> bytes:
    return x25519(private_key, _BASE)


def key_line(public: bytes) -> str:
    """The public key as one line of text, as the workflow publishes it."""
    return KEY_PREFIX + base64.b64encode(public).decode()


def parse_key_line(text: str) -> bytes:
    line = text.strip()
    if not line.startswith(KEY_PREFIX):
        raise SealError("that isn't the club's season key")
    try:
        key = base64.b64decode(line[len(KEY_PREFIX):], validate=True)
    except ValueError:
        raise SealError("the club's season key is damaged") from None
    if len(key) != 32:
        raise SealError("the club's season key is damaged")
    return key


def _keys(shared: bytes, header: bytes, recipient: bytes) -> tuple[bytes, bytes]:
    if not any(shared):
        raise SealError("this card can't be opened")
    both = hkdf(shared, header + recipient, b"qbc sealed card v1", 64)
    return both[:32], both[32:]


def _stream(key: bytes, n: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:n])


def seal(payload: dict, recipient: bytes, season: int) -> str:
    """The armoured card text for payload (a JSON-able dict), sealed to recipient (32 bytes)."""
    if not 1 <= season <= 255:
        raise SealError("season must be 1 to 255")
    plain = zlib.compress(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(), 9)
    eph = secrets.token_bytes(32)
    header = MAGIC + bytes([season]) + public_key(eph)
    enc_key, mac_key = _keys(x25519(eph, recipient), header, recipient)
    body = bytes(a ^ b for a, b in zip(plain, _stream(enc_key, len(plain))))
    tag = hmac.new(mac_key, header + body, hashlib.sha256).digest()
    raw = base64.b64encode(header + body + tag).decode()
    text = "\n".join([BEGIN, *(raw[i:i + 64] for i in range(0, len(raw), 64)), END])
    if len(text) > MAX_TEXT:
        raise SealError("this card is too big to submit")
    return text


def open_card(text: str, private_key: bytes, season: int) -> dict:
    """The payload inside a sealed card, if it's genuine and for this season; SealError otherwise."""
    if not isinstance(text, str) or len(text) > MAX_TEXT + 1_000:
        raise SealError("no sealed card found: paste the whole block, BEGIN and END lines included")
    found = _ARMOUR.search(text)
    if not found:
        raise SealError("no sealed card found: paste the whole block, BEGIN and END lines included")
    try:
        raw = base64.b64decode("".join(found.group(1).split()), validate=True)
    except ValueError:
        raise SealError("the sealed card is damaged: copy it again from your terminal") from None
    if len(raw) < 4 + 1 + 32 + 32 or raw[:4] != MAGIC:
        raise SealError("the sealed card is damaged: copy it again from your terminal")
    if raw[4] != season:
        raise SealError(f"this card is for Season {raw[4]}, not Season {season}")
    header, body, tag = raw[:37], raw[37:-32], raw[-32:]
    recipient = public_key(private_key)
    enc_key, mac_key = _keys(x25519(private_key, header[5:]), header, recipient)
    if not hmac.compare_digest(tag, hmac.new(mac_key, header + body, hashlib.sha256).digest()):
        raise SealError("the sealed card doesn't open: copy it again from your terminal, and seal it with "
                        "the current pack")
    plain = bytes(a ^ b for a, b in zip(body, _stream(enc_key, len(body))))
    inflater = zlib.decompressobj()
    try:
        data = inflater.decompress(plain, MAX_PAYLOAD)
    except zlib.error:
        raise SealError("the sealed card is damaged: copy it again from your terminal") from None
    if inflater.unconsumed_tail:
        raise SealError("the sealed card is too big to score")
    try:
        payload = json.loads(data)
    except ValueError:
        raise SealError("the sealed card is damaged: copy it again from your terminal") from None
    if not isinstance(payload, dict):
        raise SealError("the sealed card is damaged: copy it again from your terminal")
    return payload
