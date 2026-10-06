"""Pure-Python crypto for the mesh: secp256k1 BIP-340 Schnorr + NIP-44 v2 encryption.

Stdlib only (hashlib/hmac/secrets). Verified against the official BIP-340 and
NIP-44 test vectors (tests/test_mesh_crypto.py). Constant-time-ness is not a goal
here (local agent coordination, not a wallet); correctness and interop are.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from typing import Optional, Tuple

# ---------------------------------------------------------------- secp256k1

P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)

Point = Optional[Tuple[int, int]]


def _inv(a: int, m: int = P) -> int:
    return pow(a, m - 2, m)


# Jacobian coordinates for speed (Python big ints; ~1–3 ms per scalar mult)
def _to_jac(p: Point):
    return None if p is None else (p[0], p[1], 1)


def _from_jac(j) -> Point:
    if j is None or j[2] == 0:
        return None
    zi = _inv(j[2])
    zi2 = zi * zi % P
    return (j[0] * zi2 % P, j[1] * zi2 * zi % P)


def _jdouble(j):
    if j is None or j[1] == 0:
        return None
    x, y, z = j
    s = 4 * x * y * y % P
    m = 3 * x * x % P
    nx = (m * m - 2 * s) % P
    ny = (m * (s - nx) - 8 * pow(y, 4, P)) % P
    nz = 2 * y * z % P
    return (nx, ny, nz)


def _jadd(a, b):
    if a is None:
        return b
    if b is None:
        return a
    x1, y1, z1 = a
    x2, y2, z2 = b
    z1z1, z2z2 = z1 * z1 % P, z2 * z2 % P
    u1, u2 = x1 * z2z2 % P, x2 * z1z1 % P
    s1, s2 = y1 * z2 * z2z2 % P, y2 * z1 * z1z1 % P
    if u1 == u2:
        return _jdouble(a) if s1 == s2 else None
    h = (u2 - u1) % P
    r = (s2 - s1) % P
    h2 = h * h % P
    h3 = h * h2 % P
    nx = (r * r - h3 - 2 * u1 * h2) % P
    ny = (r * (u1 * h2 - nx) - s1 * h3) % P
    nz = h * z1 * z2 % P
    return (nx, ny, nz)


def point_mul(p: Point, k: int) -> Point:
    r = None
    j = _to_jac(p)
    while k:
        if k & 1:
            r = _jadd(r, j)
        j = _jdouble(j)
        k >>= 1
    return _from_jac(r)


def point_add(a: Point, b: Point) -> Point:
    return _from_jac(_jadd(_to_jac(a), _to_jac(b)))


def lift_x(x: int) -> Point:
    if x >= P:
        return None
    c = (pow(x, 3, P) + 7) % P
    y = pow(c, (P + 1) // 4, P)
    if y * y % P != c:
        return None
    return (x, y if y % 2 == 0 else P - y)


def _i2b(i: int) -> bytes:
    return i.to_bytes(32, "big")


def _b2i(b: bytes) -> int:
    return int.from_bytes(b, "big")


def tagged_hash(tag: str, msg: bytes) -> bytes:
    t = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(t + t + msg).digest()


# ---------------------------------------------------------------- keys & BIP-340


def generate_secret() -> bytes:
    while True:
        k = secrets.token_bytes(32)
        if 0 < _b2i(k) < N:
            return k


def pubkey(secret: bytes) -> bytes:
    """x-only public key (32 bytes)."""
    d = _b2i(secret)
    if not 0 < d < N:
        raise ValueError("invalid secret key")
    return _i2b(point_mul(G, d)[0])


def schnorr_sign(msg: bytes, secret: bytes, aux: Optional[bytes] = None) -> bytes:
    d0 = _b2i(secret)
    if not 0 < d0 < N:
        raise ValueError("invalid secret key")
    pt = point_mul(G, d0)
    d = d0 if pt[1] % 2 == 0 else N - d0
    aux = aux if aux is not None else secrets.token_bytes(32)
    t = _i2b(d ^ _b2i(tagged_hash("BIP0340/aux", aux)))
    k0 = _b2i(tagged_hash("BIP0340/nonce", t + _i2b(pt[0]) + msg)) % N
    if k0 == 0:
        raise ValueError("bad nonce")
    r = point_mul(G, k0)
    k = k0 if r[1] % 2 == 0 else N - k0
    e = _b2i(tagged_hash("BIP0340/challenge", _i2b(r[0]) + _i2b(pt[0]) + msg)) % N
    return _i2b(r[0]) + _i2b((k + e * d) % N)


def schnorr_verify(msg: bytes, pub: bytes, sig: bytes) -> bool:
    if len(pub) != 32 or len(sig) != 64:
        return False
    pt = lift_x(_b2i(pub))
    r, s = _b2i(sig[:32]), _b2i(sig[32:])
    if pt is None or r >= P or s >= N:
        return False
    e = _b2i(tagged_hash("BIP0340/challenge", sig[:32] + pub + msg)) % N
    rr = point_add(point_mul(G, s), point_mul(pt, N - e))
    return rr is not None and rr[1] % 2 == 0 and rr[0] == r


# ---------------------------------------------------------------- ChaCha20 (RFC 8439)


def _rotl(v: int, c: int) -> int:
    return ((v << c) & 0xFFFFFFFF) | (v >> (32 - c))


def _qr(s, a, b, c, d):
    s[a] = (s[a] + s[b]) & 0xFFFFFFFF; s[d] = _rotl(s[d] ^ s[a], 16)  # noqa: E702
    s[c] = (s[c] + s[d]) & 0xFFFFFFFF; s[b] = _rotl(s[b] ^ s[c], 12)  # noqa: E702
    s[a] = (s[a] + s[b]) & 0xFFFFFFFF; s[d] = _rotl(s[d] ^ s[a], 8)  # noqa: E702
    s[c] = (s[c] + s[d]) & 0xFFFFFFFF; s[b] = _rotl(s[b] ^ s[c], 7)  # noqa: E702


def _chacha_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    st = [0x61707865, 0x3320646E, 0x79622D32, 0x6B206574, *struct.unpack("<8I", key), counter & 0xFFFFFFFF,
          *struct.unpack("<3I", nonce)]
    w = list(st)
    for _ in range(10):
        _qr(w, 0, 4, 8, 12); _qr(w, 1, 5, 9, 13); _qr(w, 2, 6, 10, 14); _qr(w, 3, 7, 11, 15)  # noqa: E702
        _qr(w, 0, 5, 10, 15); _qr(w, 1, 6, 11, 12); _qr(w, 2, 7, 8, 13); _qr(w, 3, 4, 9, 14)  # noqa: E702
    return struct.pack("<16I", *[(w[i] + st[i]) & 0xFFFFFFFF for i in range(16)])


def chacha20(key: bytes, nonce: bytes, data: bytes, counter: int = 0) -> bytes:
    out = bytearray()
    for i in range(0, len(data), 64):
        ks = _chacha_block(key, counter + i // 64, nonce)
        chunk = data[i:i + 64]
        out += bytes(a ^ b for a, b in zip(chunk, ks))
    return bytes(out)


# ---------------------------------------------------------------- HKDF (RFC 5869, SHA-256)


def hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    out, t, i = b"", b"", 1
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        out += t
        i += 1
    return out[:length]


# ---------------------------------------------------------------- NIP-44 v2


def ecdh_x(secret: bytes, pub: bytes) -> bytes:
    pt = lift_x(_b2i(pub))
    if pt is None:
        raise ValueError("invalid public key")
    return _i2b(point_mul(pt, _b2i(secret))[0])


def conversation_key(secret: bytes, pub: bytes) -> bytes:
    return hkdf_extract(b"nip44-v2", ecdh_x(secret, pub))


def _calc_padded_len(n: int) -> int:
    if n <= 32:
        return 32
    next_power = 1 << ((n - 1).bit_length())
    chunk = 32 if next_power <= 256 else next_power // 8
    return chunk * ((n - 1) // chunk + 1)


def _pad(plaintext: str) -> bytes:
    raw = plaintext.encode("utf-8")
    if not 1 <= len(raw) <= 65535:
        raise ValueError("NIP-44 plaintext must be 1..65535 bytes")
    return struct.pack(">H", len(raw)) + raw + b"\x00" * (_calc_padded_len(len(raw)) - len(raw))


def _unpad(padded: bytes) -> str:
    n = struct.unpack(">H", padded[:2])[0]
    raw = padded[2:2 + n]
    if n == 0 or len(raw) != n or len(padded) != 2 + _calc_padded_len(n):
        raise ValueError("invalid padding")
    return raw.decode("utf-8")


def _msg_keys(conv_key: bytes, nonce: bytes) -> Tuple[bytes, bytes, bytes]:
    k = hkdf_expand(conv_key, nonce, 76)
    return k[:32], k[32:44], k[44:76]


def nip44_encrypt(plaintext: str, conv_key: bytes, nonce: Optional[bytes] = None) -> str:
    nonce = nonce or secrets.token_bytes(32)
    ck, cn, hk = _msg_keys(conv_key, nonce)
    ct = chacha20(ck, cn, _pad(plaintext))
    mac = hmac.new(hk, nonce + ct, hashlib.sha256).digest()
    return base64.b64encode(b"\x02" + nonce + ct + mac).decode()


def nip44_decrypt(payload: str, conv_key: bytes) -> str:
    if not payload or payload[0] == "#":
        raise ValueError("unknown NIP-44 version")
    raw = base64.b64decode(payload)
    if len(raw) < 99 or raw[0] != 2:
        raise ValueError("unsupported NIP-44 payload")
    nonce, ct, mac = raw[1:33], raw[33:-32], raw[-32:]
    ck, cn, hk = _msg_keys(conv_key, nonce)
    if not hmac.compare_digest(mac, hmac.new(hk, nonce + ct, hashlib.sha256).digest()):
        raise ValueError("invalid MAC")
    return _unpad(chacha20(ck, cn, ct))


def symmetric_key(secret_phrase: str, label: str = "puenteo-room") -> bytes:
    """Room key from a shared secret (private rooms): HKDF over the phrase."""
    return hkdf_extract(label.encode(), hashlib.sha256(secret_phrase.encode("utf-8")).digest())


# ---------------------------------------------------------------- bech32 (npub/nsec display)

_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _polymod(values):
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        b = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if ((b >> i) & 1) else 0
    return chk


def _hrp_expand(hrp):
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def _convertbits(data, frm, to, pad=True):
    acc, bits, ret, maxv = 0, 0, [], (1 << to) - 1
    for v in data:
        acc = (acc << frm) | v
        bits += frm
        while bits >= to:
            bits -= to
            ret.append((acc >> bits) & maxv)
    if pad and bits:
        ret.append((acc << (to - bits)) & maxv)
    elif not pad and (bits >= frm or ((acc << (to - bits)) & maxv)):
        raise ValueError("invalid padding")
    return ret


def bech32_encode(hrp: str, data: bytes) -> str:
    d5 = _convertbits(data, 8, 5)
    pm = _polymod(_hrp_expand(hrp) + d5 + [0] * 6) ^ 1
    return hrp + "1" + "".join(_CHARSET[x] for x in d5 + [(pm >> 5 * (5 - i)) & 31 for i in range(6)])


def bech32_decode(s: str) -> Tuple[str, bytes]:
    s = s.lower()
    pos = s.rfind("1")
    hrp, data = s[:pos], [_CHARSET.index(c) for c in s[pos + 1:]]
    if _polymod(_hrp_expand(hrp) + data) != 1:
        raise ValueError("bad bech32 checksum")
    return hrp, bytes(_convertbits(data[:-6], 5, 8, False))


def npub(pub: bytes) -> str:
    return bech32_encode("npub", pub)


def parse_pub(s: str) -> bytes:
    s = s.strip()
    if s.startswith("npub1"):
        return bech32_decode(s)[1]
    b = bytes.fromhex(s)
    if len(b) != 32:
        raise ValueError("public key must be 32 bytes hex or npub")
    return b
