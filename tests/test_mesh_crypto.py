"""Mesh crypto against the official BIP-340 and NIP-44 v2 test vectors."""

from __future__ import annotations

import csv
import hashlib
import json
import pathlib

import pytest

from puenteo.mesh import crypto as c

V = pathlib.Path(__file__).parent / "vectors"


def test_bip340_vectors():
    rows = list(csv.DictReader(open(V / "bip340.csv", encoding="utf-8")))
    assert len(rows) >= 15
    for r in rows:
        pub = bytes.fromhex(r["public key"])
        msg = bytes.fromhex(r["message"])
        sig = bytes.fromhex(r["signature"])
        ok = r["verification result"] == "TRUE"
        if r["secret key"]:
            sk = bytes.fromhex(r["secret key"])
            assert c.pubkey(sk) == pub, r["index"]
            assert c.schnorr_sign(msg, sk, bytes.fromhex(r["aux_rand"])) == sig, r["index"]
        assert c.schnorr_verify(msg, pub, sig) == ok, (r["index"], r["comment"])


NIP44 = json.load(open(V / "nip44.json", encoding="utf-8"))["v2"]


def test_nip44_conversation_keys():
    for v in NIP44["valid"]["get_conversation_key"]:
        assert c.conversation_key(bytes.fromhex(v["sec1"]), bytes.fromhex(v["pub2"])).hex() == v["conversation_key"]


def test_nip44_message_keys():
    mk = NIP44["valid"]["get_message_keys"]
    ck = bytes.fromhex(mk["conversation_key"])
    for k in mk["keys"]:
        a, b, h = c._msg_keys(ck, bytes.fromhex(k["nonce"]))
        assert (a.hex(), b.hex(), h.hex()) == (k["chacha_key"], k["chacha_nonce"], k["hmac_key"])


def test_nip44_padding():
    for n, padded in NIP44["valid"]["calc_padded_len"]:
        assert c._calc_padded_len(n) == padded


def test_nip44_encrypt_decrypt():
    for v in NIP44["valid"]["encrypt_decrypt"]:
        s1, s2 = bytes.fromhex(v["sec1"]), bytes.fromhex(v["sec2"])
        ck = c.conversation_key(s1, c.pubkey(s2))
        assert ck.hex() == v["conversation_key"]
        assert c.nip44_encrypt(v["plaintext"], ck, bytes.fromhex(v["nonce"])) == v["payload"]
        assert c.nip44_decrypt(v["payload"], c.conversation_key(s2, c.pubkey(s1))) == v["plaintext"]


def test_nip44_long_messages():
    for v in NIP44["valid"]["encrypt_decrypt_long_msg"]:
        pt = v["pattern"] * v["repeat"]
        assert hashlib.sha256(pt.encode()).hexdigest() == v["plaintext_sha256"]
        payload = c.nip44_encrypt(pt, bytes.fromhex(v["conversation_key"]), bytes.fromhex(v["nonce"]))
        assert hashlib.sha256(payload.encode()).hexdigest() == v["payload_sha256"]


def test_nip44_invalid():
    for v in NIP44["invalid"]["decrypt"]:
        with pytest.raises(Exception):
            c.nip44_decrypt(v["payload"], bytes.fromhex(v["conversation_key"]))
    for v in NIP44["invalid"]["get_conversation_key"]:
        with pytest.raises(Exception):
            c.conversation_key(bytes.fromhex(v["sec1"]), bytes.fromhex(v["pub2"]))
    for n in NIP44["invalid"]["encrypt_msg_lengths"]:
        with pytest.raises(Exception):
            c._pad("a" * n)


def test_npub_roundtrip():
    pub = c.pubkey(c.generate_secret())
    assert c.parse_pub(c.npub(pub)) == pub
