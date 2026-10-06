"""Nostr (NIP-01) events, node identity, and a small multi-relay client."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from . import crypto
from .ws import WebSocket, WSClosed

DEFAULT_RELAYS = ("wss://relay.damus.io", "wss://nos.lol", "wss://relay.primal.net")

KIND_APP = 30078        # parameterized replaceable "app data": node announcements, offers
KIND_DM = 4344          # puenteo direct envelope (NIP-44 v2 encrypted to the recipient)
KIND_ROOM = 24343       # puenteo room message (ephemeral-ish; payload maybe room-encrypted)


@dataclass
class Event:
    pubkey: str
    created_at: int
    kind: int
    tags: List[List[str]]
    content: str
    id: str = ""
    sig: str = ""

    def serialize(self) -> bytes:
        return json.dumps([0, self.pubkey, self.created_at, self.kind, self.tags, self.content],
                          separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    def compute_id(self) -> str:
        return hashlib.sha256(self.serialize()).hexdigest()

    def verify(self) -> bool:
        try:
            if self.compute_id() != self.id:
                return False
            return crypto.schnorr_verify(bytes.fromhex(self.id), bytes.fromhex(self.pubkey), bytes.fromhex(self.sig))
        except Exception:
            return False

    def tag(self, name: str) -> Optional[str]:
        for t in self.tags:
            if t and t[0] == name and len(t) > 1:
                return t[1]
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "pubkey": self.pubkey, "created_at": self.created_at, "kind": self.kind,
                "tags": self.tags, "content": self.content, "sig": self.sig}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Event":
        return cls(pubkey=d["pubkey"], created_at=int(d["created_at"]), kind=int(d["kind"]),
                   tags=[list(map(str, t)) for t in d.get("tags", [])], content=str(d.get("content", "")),
                   id=d.get("id", ""), sig=d.get("sig", ""))


class Identity:
    """This node's keypair, stored 0600 in the state dir."""

    def __init__(self, secret: bytes):
        self.secret = secret
        self.pub = crypto.pubkey(secret)
        self.pubhex = self.pub.hex()
        self._ck: Dict[str, bytes] = {}

    @classmethod
    def load(cls, path=None) -> "Identity":
        from ..paths import state_dir

        p = path or (state_dir() / "mesh.key")
        try:
            return cls(bytes.fromhex(p.read_text(encoding="utf-8").strip()))
        except (OSError, ValueError):
            sk = crypto.generate_secret()
            fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(sk.hex() + "\n")
            return cls(sk)

    @property
    def npub(self) -> str:
        return crypto.npub(self.pub)

    def sign(self, kind: int, content: str, tags: Optional[List[List[str]]] = None, created_at: Optional[int] = None) -> Event:
        ev = Event(pubkey=self.pubhex, created_at=int(created_at or time.time()), kind=kind, tags=tags or [], content=content)
        ev.id = ev.compute_id()
        ev.sig = crypto.schnorr_sign(bytes.fromhex(ev.id), self.secret).hex()
        return ev

    def conv_key(self, other_pubhex: str) -> bytes:
        k = self._ck.get(other_pubhex)
        if k is None:
            k = crypto.conversation_key(self.secret, bytes.fromhex(other_pubhex))
            self._ck[other_pubhex] = k
        return k

    def encrypt_to(self, other_pubhex: str, plaintext: str) -> str:
        return crypto.nip44_encrypt(plaintext, self.conv_key(other_pubhex))

    def decrypt_from(self, other_pubhex: str, payload: str) -> str:
        return crypto.nip44_decrypt(payload, self.conv_key(other_pubhex))


class Relay:
    """One relay connection with auto-reconnect; delivers EVENTs to a callback."""

    def __init__(self, url: str, on_event: Callable[[str, Event], None], log: Callable[[str], None] = lambda m: None):
        self.url = url
        self.on_event = on_event
        self.log = log
        self.ws: Optional[WebSocket] = None
        self.subs: Dict[str, List[Dict[str, Any]]] = {}
        self.outbox: "queue.Queue[str]" = queue.Queue(maxsize=1000)
        self.connected = threading.Event()
        self._stop = threading.Event()
        self.last_error = ""
        self.ok_count = 0
        threading.Thread(target=self._run, daemon=True, name=f"relay {url}").start()

    def publish(self, ev: Event) -> None:
        try:
            self.outbox.put_nowait(json.dumps(["EVENT", ev.to_dict()], ensure_ascii=False))
        except queue.Full:
            pass

    def subscribe(self, sub_id: str, filters: List[Dict[str, Any]]) -> None:
        self.subs[sub_id] = filters
        if self.connected.is_set():
            self._send(["REQ", sub_id, *filters])

    def _send(self, msg) -> None:
        try:
            if self.ws:
                self.ws.send_text(json.dumps(msg, ensure_ascii=False))
        except Exception as e:
            self.last_error = str(e)

    def close(self) -> None:
        self._stop.set()
        if self.ws:
            self.ws.close()

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self.ws = WebSocket(self.url, timeout=10)
                self.connected.set()
                self.last_error = ""
                backoff = 1.0
                for sid, f in self.subs.items():
                    self._send(["REQ", sid, *f])
                while not self._stop.is_set():
                    while True:
                        try:
                            raw = self.outbox.get_nowait()
                        except queue.Empty:
                            break
                        self.ws.send_text(raw)
                    msg = self.ws.recv_text(timeout=0.25)
                    if msg is None:
                        continue
                    self._handle(msg)
            except (WSClosed, OSError, ValueError) as e:
                self.last_error = str(e)
            finally:
                self.connected.clear()
                if self.ws:
                    self.ws.close()
                self.ws = None
            if self._stop.wait(backoff):
                return
            backoff = min(backoff * 2, 60.0)

    def _handle(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if not isinstance(msg, list) or not msg:
            return
        if msg[0] == "EVENT" and len(msg) >= 3 and isinstance(msg[2], dict):
            try:
                ev = Event.from_dict(msg[2])
            except Exception:
                return
            if ev.verify():
                self.on_event(self.url, ev)
        elif msg[0] == "OK" and len(msg) >= 3 and msg[2]:
            self.ok_count += 1
        elif msg[0] in ("NOTICE", "CLOSED"):
            self.log(f"{self.url}: {msg[1:]}")


class RelayPool:
    """Publish to and subscribe on several relays; dedupe events by id."""

    def __init__(self, urls, on_event: Callable[[Event], None], log: Callable[[str], None] = lambda m: None):
        self._seen: "dict[str, float]" = {}
        self._lock = threading.Lock()
        self.on_event = on_event
        self.relays = [Relay(u, self._got, log) for u in urls]

    def _got(self, url: str, ev: Event) -> None:
        with self._lock:
            if ev.id in self._seen:
                return
            self._seen[ev.id] = time.time()
            if len(self._seen) > 20000:
                cut = sorted(self._seen.items(), key=lambda kv: kv[1])[:10000]
                for k, _ in cut:
                    self._seen.pop(k, None)
        self.on_event(ev)

    def publish(self, ev: Event) -> None:
        for r in self.relays:
            r.publish(ev)

    def subscribe(self, sub_id: str, filters: List[Dict[str, Any]]) -> None:
        for r in self.relays:
            r.subscribe(sub_id, filters)

    def wait_connected(self, timeout: float = 10.0) -> int:
        deadline = time.time() + timeout
        while time.time() < deadline:
            n = sum(r.connected.is_set() for r in self.relays)
            if n:
                return n
            time.sleep(0.1)
        return 0

    def status(self) -> List[Dict[str, Any]]:
        return [{"url": r.url, "connected": r.connected.is_set(), "accepted": r.ok_count, "error": r.last_error}
                for r in self.relays]

    def close(self) -> None:
        for r in self.relays:
            r.close()
