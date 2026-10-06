"""The mesh node: bridges the local bus to other machines (LAN, Nostr relays, direct peers).

Outbound: the bus routes any ``…@node`` / ``#room@*`` address to the ``mesh:out``
mailbox; the node drains it, signs + encrypts, and ships it.
Inbound: verified events pass the access policy and are inserted into the local
bus with sender ``<addr>@<node>``, so every local delivery path (doorbell, hooks,
codex queue, MCP, watch) just works and ``reply`` routes back automatically.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..bus import MESH_OUTBOX, Bus, BusError, BusMessage, normalize_address
from . import crypto
from .nostr import DEFAULT_RELAYS, KIND_APP, KIND_DM, KIND_ROOM, Event, Identity, RelayPool

PROTO = "puenteo/1"
MCAST_GRP, MCAST_PORT = "239.255.77.57", 47357
ANNOUNCE_EVERY_S = 300
MAX_REMOTE_BODY = 16_000
REMOTE_RATE = 60          # inbound messages per node per 10 min
SCHEMA = """
CREATE TABLE IF NOT EXISTS mesh_nodes (
    pubkey TEXT PRIMARY KEY, name TEXT, trusted INTEGER DEFAULT 0, blocked INTEGER DEFAULT 0,
    last_seen REAL, via TEXT, endpoints TEXT, sessions TEXT, info TEXT, announced_at REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS mesh_nodes_name ON mesh_nodes(name);
CREATE TABLE IF NOT EXISTS mesh_offers (
    id TEXT PRIMARY KEY, pubkey TEXT, node TEXT, address TEXT, text TEXT, tags TEXT,
    created REAL, expires REAL, local INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mesh_rooms (
    name TEXT PRIMARY KEY, secret TEXT, joined REAL
);
CREATE TABLE IF NOT EXISTS mesh_threads (
    thread TEXT PRIMARY KEY, pubkey TEXT, created REAL
);
CREATE TABLE IF NOT EXISTS mesh_stats (k TEXT PRIMARY KEY, v INTEGER);
CREATE TABLE IF NOT EXISTS mesh_config (k TEXT PRIMARY KEY, v TEXT);
"""


def _db(bus: Bus) -> sqlite3.Connection:
    con = bus.con
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(mesh_nodes)")}
    if "announced_at" not in cols:  # migrate older state
        con.execute("ALTER TABLE mesh_nodes ADD COLUMN announced_at REAL DEFAULT 0")
    return con


def config_get(bus: Bus, key: str, default: str = "") -> str:
    row = _db(bus).execute("SELECT v FROM mesh_config WHERE k=?", (key,)).fetchone()
    return row[0] if row else default


def config_set(bus: Bus, key: str, value: str) -> None:
    _db(bus).execute("INSERT OR REPLACE INTO mesh_config(k, v) VALUES (?,?)", (key, value))


def node_name(bus: Bus, ident: Identity) -> str:
    name = config_get(bus, "name")
    if not name:
        host = re.sub(r"[^a-z0-9-]", "-", socket.gethostname().split(".")[0].lower()).strip("-") or "node"
        name = f"{host}-{ident.pubhex[:4]}"
        config_set(bus, "name", name)
    return name


def relays(bus: Bus) -> List[str]:
    raw = config_get(bus, "relays")
    if raw == "none":
        return []
    return [r for r in (raw.split(",") if raw else DEFAULT_RELAYS) if r]


def split_remote(addr: str) -> Tuple[str, str]:
    """``claude:abc@mac`` → ("claude:abc", "mac");  ``@bob@mac`` → ("@bob", "mac");  ``#r@*`` → ("#r", "*")."""
    if addr.startswith("@"):
        local, _, node = addr[1:].rpartition("@")
        return "@" + local, node
    local, _, node = addr.rpartition("@")
    return local, node


def room_tag(name: str, secret: str = "") -> str:
    """Relay-visible topic tag. Private rooms hash name+secret so the topic is not leaked."""
    import hashlib

    basis = f"{name}\x00{secret}" if secret else name
    return "puenteo-room-" + hashlib.sha256(basis.encode()).hexdigest()[:24]


def _bump(con, key: str, n: int = 1) -> None:
    con.execute("INSERT INTO mesh_stats(k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = v + ?", (key, n, n))


@dataclass
class Node:
    ident: Identity
    name: str
    relays: List[str] = field(default_factory=list)
    lan: bool = True
    http_port: int = 0  # our `puenteo serve` port, advertised to LAN peers for direct POSTs
    log_fn: Any = None
    bus_path: str = ""  # pinned at construction: background threads must never follow env changes

    def __post_init__(self):
        from ..paths import bus_db_path

        self.bus_path = self.bus_path or str(bus_db_path())
        self._tls = threading.local()
        _db(self.bus)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._rate: Dict[str, List[float]] = {}
        self.pool: Optional[RelayPool] = None

    @property
    def bus(self) -> Bus:
        """One bus connection per thread (sqlite3 connections aren't shareable across threads)."""
        b = getattr(self._tls, "bus", None)
        if b is None:
            b = Bus(self.bus_path)
            _db(b)
            self._tls.bus = b
        return b

    # ------------------------------------------------------------ lifecycle
    def log(self, msg: str) -> None:
        if self.log_fn:
            self.log_fn(msg)
        elif os.environ.get("PUENTEO_DEBUG"):
            print(f"[mesh] {msg}", file=sys.stderr, flush=True)

    def start(self) -> None:
        if self.relays:
            self.pool = RelayPool(self.relays, self._on_event, self.log)
            since = int(time.time()) - 600
            self.pool.subscribe("dm", [{"kinds": [KIND_DM], "#p": [self.ident.pubhex], "since": since}])
            self.pool.subscribe("disc", [{"kinds": [KIND_APP], "#t": ["puenteo"], "since": int(time.time()) - 7 * 86400}])
            self._subscribe_rooms()
        if self.lan:
            threading.Thread(target=self._lan_listen, daemon=True, name="mesh lan").start()
        threading.Thread(target=self._outbox_loop, daemon=True, name="mesh outbox").start()
        threading.Thread(target=self._announce_loop, daemon=True, name="mesh announce").start()

    def stop(self) -> None:
        self._stop.set()
        if self.pool:
            self.pool.close()

    # ------------------------------------------------------------ announce / discovery
    def local_sessions(self) -> List[Dict[str, str]]:
        from ..live import live_sessions

        out = []
        names = {p.address: p.name for p in self.bus.peers()}
        for s in live_sessions():
            out.append({"address": s.address, "agent": s.agent, "name": names.get(s.address) or "",
                        "cwd_tail": "/".join((s.cwd or "").rstrip("/").split("/")[-2:])})
        return out[:64]

    def announcement(self) -> Event:
        offers = [dict(id=o[0], address=o[1], text=o[2], tags=json.loads(o[3] or "[]"))
                  for o in _db(self.bus).execute(
                      "SELECT id, address, text, tags FROM mesh_offers WHERE local=1 AND expires>?", (time.time(),))]
        content = {"proto": PROTO, "name": self.name, "sessions": self.local_sessions(), "offers": offers,
                   "http_port": self.http_port or None,
                   "rooms_public": [r[0] for r in _db(self.bus).execute("SELECT name FROM mesh_rooms WHERE secret=''")]}
        tags = [["d", "puenteo:node"], ["t", "puenteo"], ["name", self.name]]
        for o in offers:
            for t in o["tags"][:8]:
                tags.append(["t", "puenteo-" + t.lower()[:40]])
        return self.ident.sign(KIND_APP, json.dumps(content, ensure_ascii=False), tags)

    def announce(self) -> None:
        ev = self.announcement()
        if self.pool:
            self.pool.publish(ev)
        if self.lan:
            self._lan_send(ev)

    def _announce_loop(self) -> None:
        time.sleep(1.0)
        while not self._stop.is_set():
            try:
                self.announce()
            except Exception as e:
                self.log(f"announce failed: {e}")
            self._stop.wait(ANNOUNCE_EVERY_S)

    def _learn_node(self, ev: Event, via: str, endpoint: str = "") -> None:
        try:
            info = json.loads(ev.content)
        except ValueError:
            return
        if info.get("proto") != PROTO or ev.pubkey == self.ident.pubhex:
            return
        name = re.sub(r"[^\w.-]", "-", str(info.get("name") or ev.pubkey[:8]))[:64]
        con = _db(self.bus)
        with self._lock:
            row = con.execute("SELECT endpoints FROM mesh_nodes WHERE pubkey=?", (ev.pubkey,)).fetchone()
            eps = set(json.loads(row[0])) if row and row[0] else set()
            if endpoint:
                eps.add(endpoint)
            prev = con.execute("SELECT announced_at FROM mesh_nodes WHERE pubkey=?", (ev.pubkey,)).fetchone()
            if prev and (prev[0] or 0) > ev.created_at:
                return  # an older announcement replayed by a relay: ignore
            con.execute(
                "INSERT INTO mesh_nodes(pubkey, name, last_seen, via, endpoints, sessions, info, announced_at)"
                " VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(pubkey) DO UPDATE SET name=excluded.name, last_seen=excluded.last_seen, via=excluded.via,"
                " endpoints=excluded.endpoints, sessions=excluded.sessions, info=excluded.info,"
                " announced_at=excluded.announced_at",
                (ev.pubkey, name, time.time(), via, json.dumps(sorted(eps)), json.dumps(info.get("sessions", [])),
                 json.dumps({k: info.get(k) for k in ("rooms_public",)}), float(ev.created_at)),
            )
            con.execute("DELETE FROM mesh_offers WHERE pubkey=? AND local=0", (ev.pubkey,))
            for o in info.get("offers", [])[:32]:
                con.execute(
                    "INSERT OR REPLACE INTO mesh_offers(id, pubkey, node, address, text, tags, created, expires, local)"
                    " VALUES (?,?,?,?,?,?,?,?,0)",
                    (str(o.get("id"))[:40], ev.pubkey, name, str(o.get("address"))[:200], str(o.get("text"))[:2000],
                     json.dumps(o.get("tags", [])[:16]), ev.created_at, time.time() + 2 * ANNOUNCE_EVERY_S + 600),
                )

    # ------------------------------------------------------------ outbound
    def resolve_node(self, node: str) -> Optional[str]:
        """Node name, npub/hex pubkey, or unique name prefix → pubkey hex."""
        con = _db(self.bus)
        if node.startswith("npub1") or re.fullmatch(r"[0-9a-f]{64}", node or ""):
            return crypto.parse_pub(node).hex()
        # Exact name first. Several keys may claim one name (a reinstalled machine keeps its
        # name but gets a new key): the most recently seen wins; trust pins a key explicitly.
        rows = con.execute(
            "SELECT pubkey FROM mesh_nodes WHERE name=? AND blocked=0 ORDER BY trusted DESC, announced_at DESC, last_seen DESC",
            (node,)).fetchall()
        if not rows:  # only blocked candidates: still resolvable (for unblock / explicit errors)
            rows = con.execute("SELECT pubkey FROM mesh_nodes WHERE name=? ORDER BY announced_at DESC", (node,)).fetchall()
        if rows:
            return rows[0][0]
        rows = con.execute("SELECT DISTINCT name, pubkey FROM mesh_nodes WHERE substr(name, 1, ?)=? ORDER BY announced_at DESC",
                           (len(node), node)).fetchall()
        names = {r[0] for r in rows}
        return rows[0][1] if len(names) == 1 else None

    def _outbox_loop(self) -> None:
        from ..notify import Bell

        with Bell(MESH_OUTBOX, self.bus_path) as bell:
            while not self._stop.is_set():
                try:
                    for m in self.bus.inbox(MESH_OUTBOX, unread_only=True, mark_read=True, limit=100):
                        self.ship(m)
                except Exception as e:
                    self.log(f"outbox error: {e}")
                bell.wait(5.0)

    def ship(self, m: BusMessage) -> None:
        local_to, node = split_remote(m.to)
        con = _db(self.bus)
        if m.body and len(m.body) > MAX_REMOTE_BODY:
            self._bounce(m, f"message too large for the mesh ({len(m.body)} > {MAX_REMOTE_BODY})")
            return
        if local_to.startswith("#"):
            self._ship_room(m, local_to[1:])
            return
        pub = self.resolve_node(node)
        if not pub:
            self._bounce(m, f"unknown mesh node {node!r} (see `puenteo mesh peers`)")
            return
        row = self._node_row(pub)
        if row and row[2]:
            self._bounce(m, f"node {node} is blocked")
            return
        # remember threads we start so replies are allowed back in
        con.execute("INSERT OR IGNORE INTO mesh_threads(thread, pubkey, created) VALUES (?,?,?)", (m.thread, pub, time.time()))
        payload = {"proto": PROTO, "id": m.id, "from": m.sender, "to": local_to, "thread": m.thread,
                   "reply_to": self._remote_reply_ref(m.reply_to), "kind": m.kind, "body": m.body, "hops": m.hops,
                   "node": self.name, "http_port": self.http_port or None}
        ev = self.ident.sign(KIND_DM, self.ident.encrypt_to(pub, json.dumps(payload, ensure_ascii=False)), [["p", pub]])
        sent = False
        for ep in json.loads((con.execute("SELECT endpoints FROM mesh_nodes WHERE pubkey=?", (pub,)).fetchone() or ["[]"])[0] or "[]"):
            if self._post_direct(ep, ev):
                sent = True
                break
        if self.pool:
            self.pool.publish(ev)
            sent = True
        _bump(con, "out")
        if not sent:
            self._bounce(m, "no transport reached the node (no relays, no direct endpoint)")

    def _remote_reply_ref(self, reply_to: str) -> str:
        """Replies reference the *remote* message id we received (stored in meta.remote_id)."""
        if not reply_to:
            return ""
        parent = self.bus.get(reply_to)
        return (parent.meta.get("remote_id") if parent else "") or reply_to

    def _ship_room(self, m: BusMessage, room: str) -> None:
        row = _db(self.bus).execute("SELECT secret FROM mesh_rooms WHERE name=?", (room,)).fetchone()
        if not row:
            self._bounce(m, f"not in room #{room}; `puenteo room join {room}` first")
            return
        secret = row[0] or ""
        payload = json.dumps({"proto": PROTO, "id": m.id, "from": m.sender, "room": room, "body": m.body,
                              "thread": m.thread}, ensure_ascii=False)
        content = crypto.nip44_encrypt(payload, crypto.symmetric_key(secret, "puenteo-room:" + room)) if secret else payload
        ev = self.ident.sign(KIND_ROOM, content, [["t", room_tag(room, secret)], ["enc", "1" if secret else "0"]])
        if self.pool:
            self.pool.publish(ev)
        if self.lan:
            self._lan_send(ev)
        _bump(_db(self.bus), "out_room")

    def _bounce(self, m: BusMessage, why: str) -> None:
        self.log(f"bounce {m.id}: {why}")
        try:
            self.bus.send("mesh:bridge", m.sender, f"[mesh] could not deliver {m.id} to {m.to}: {why}", reply_to="")
        except BusError:
            pass

    def _post_direct(self, endpoint: str, ev: Event) -> bool:
        import urllib.parse
        import urllib.request

        # endpoints come from (signed, but untrusted) peer announcements: http(s) only, no file:// etc.
        if urllib.parse.urlparse(endpoint).scheme not in ("http", "https"):
            return False
        try:
            req = urllib.request.Request(endpoint.rstrip("/") + "/mesh/event", data=json.dumps(ev.to_dict()).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3) as r:  # nosec B310 - scheme checked above
                return r.status == 202 or r.status == 200
        except Exception:
            return False

    # ------------------------------------------------------------ inbound
    def _on_event(self, ev: Event, via: str = "relay") -> None:
        if ev.pubkey == self.ident.pubhex:
            return
        try:
            if ev.kind == KIND_APP and ev.tag("d") == "puenteo:node":
                self._learn_node(ev, via)
            elif ev.kind == KIND_DM and ev.tag("p") == self.ident.pubhex:
                self._on_dm(ev, via)
            elif ev.kind == KIND_ROOM:
                self._on_room(ev)
        except Exception as e:
            self.log(f"bad event {ev.id[:8]}: {e}")

    def receive(self, ev_dict: Dict[str, Any], via: str = "http", peer_ip: str = "") -> bool:
        """Entry for direct transports (LAN HTTP POST, LAN multicast). Verifies first."""
        ev = Event.from_dict(ev_dict)
        if not ev.verify():
            return False
        self._peer_ip = peer_ip
        try:
            self._on_event(ev, via)
        finally:
            self._peer_ip = ""
        return True

    def _rate_ok(self, pub: str) -> bool:
        now = time.time()
        q = [t for t in self._rate.get(pub, []) if now - t < 600]
        if len(q) >= REMOTE_RATE:
            self._rate[pub] = q
            return False
        q.append(now)
        self._rate[pub] = q
        return True

    def _node_row(self, pub: str):
        return _db(self.bus).execute("SELECT name, trusted, blocked FROM mesh_nodes WHERE pubkey=?", (pub,)).fetchone()

    def _on_dm(self, ev: Event, via: str) -> None:
        con = _db(self.bus)
        row = self._node_row(ev.pubkey)
        if row and row[2]:
            _bump(con, "dropped_blocked")
            return
        try:
            p = json.loads(self.ident.decrypt_from(ev.pubkey, ev.content))
        except Exception:
            _bump(con, "dropped_undecryptable")
            return
        if p.get("proto") != PROTO:
            return
        if not self._rate_ok(ev.pubkey):
            _bump(con, "dropped_rate")
            return
        claimed = re.sub(r"[^\w.-]", "-", str(p.get("node") or ""))[:64]
        node = row[0] if row else (claimed or ("n-" + ev.pubkey[:8]))
        ip = getattr(self, "_peer_ip", "")
        endpoint = f"http://{ip}:{int(p['http_port'])}" if ip and p.get("http_port") else ""
        if not row:
            con.execute("INSERT OR IGNORE INTO mesh_nodes(pubkey, name, last_seen, via, endpoints) VALUES (?,?,?,?,?)",
                        (ev.pubkey, node, time.time(), via, json.dumps([endpoint] if endpoint else [])))
            threading.Thread(target=self._greet, args=(ev.pubkey,), daemon=True).start()
        else:
            eps = set(json.loads((con.execute("SELECT endpoints FROM mesh_nodes WHERE pubkey=?", (ev.pubkey,)).fetchone() or ["[]"])[0] or "[]"))
            if endpoint and endpoint not in eps:
                eps.add(endpoint)
            con.execute("UPDATE mesh_nodes SET last_seen=?, endpoints=? WHERE pubkey=?", (time.time(), json.dumps(sorted(eps)), ev.pubkey))
        to = normalize_address(str(p.get("to") or ""))
        thread = str(p.get("thread") or "")
        allowed, why = self._allowed(ev.pubkey, to, thread, trusted=bool(row and row[1]))
        if not allowed:
            _bump(con, "dropped_policy")
            self.log(f"drop from {node}: {why}")
            return
        body = str(p.get("body") or "")[:MAX_REMOTE_BODY]
        sender = f"{p.get('from') or 'unknown'}@{node}"
        reply_to = self._local_for_remote(str(p.get("reply_to") or ""))
        try:
            from ..bus import check_address

            check_address(sender)
        except BusError:
            sender = f"remote:{ev.pubkey[:16]}@{node}"
        try:
            local_to = self._resolve_local(to)
            m = self.bus.send(sender, local_to, body, thread=self._safe_thread(thread), reply_to=reply_to,
                              meta={"remote_id": p.get("id"), "remote_node": node, "remote_pub": ev.pubkey,
                                    "via": via, "trust": "remote-peer"},
                              min_hops=int(p.get("hops") or 0) + 1)
            _bump(con, "in")
            self.log(f"in {m.id} {sender} -> {local_to}")
        except BusError as e:
            _bump(con, "dropped_unroutable")
            self.log(f"unroutable from {node}: {e}")

    def _greet(self, pub: str) -> None:
        """Introduce ourselves to a node that just contacted us (so it learns our name, sessions, endpoint)."""
        try:
            ev = self.announcement()
            row = _db(self.bus).execute("SELECT endpoints FROM mesh_nodes WHERE pubkey=?", (pub,)).fetchone()
            for ep in json.loads((row or ["[]"])[0] or "[]"):
                if self._post_direct(ep, ev):
                    return
            if self.pool:
                self.pool.publish(ev)
            if self.lan:
                self._lan_send(ev)
        except Exception as e:
            self.log(f"greet failed: {e}")

    def _safe_thread(self, t: str) -> str:
        return t if re.fullmatch(r"[\w.:@#*/~+=-]{1,64}", t or "") else ""

    def _local_for_remote(self, remote_id: str) -> str:
        if not remote_id:
            return ""
        own = self.bus.get(remote_id) if re.fullmatch(r"[0-9a-f]{6,12}", remote_id) else None
        if own:
            return own.id
        row = self.bus.con.execute(
            "SELECT id FROM messages WHERE json_extract(meta, '$.remote_id')=? ORDER BY seq DESC LIMIT 1", (remote_id,)
        ).fetchone()
        return row[0] if row else ""

    def _resolve_local(self, to: str) -> str:
        if not to:
            raise BusError("empty recipient")
        if to in ("*",) or to.startswith(("agent:", "cwd:")):
            raise BusError("broadcast targets are not reachable from the mesh")
        return to

    def _allowed(self, pub: str, to: str, thread: str, *, trusted: bool) -> Tuple[bool, str]:
        con = _db(self.bus)
        if trusted:
            return True, "trusted node"
        if thread and con.execute("SELECT 1 FROM mesh_threads WHERE thread=? AND pubkey=?", (thread, pub)).fetchone():
            return True, "reply in our thread"
        local_offer_addrs = {r[0] for r in con.execute(
            "SELECT address FROM mesh_offers WHERE local=1 AND expires>?", (time.time(),))}
        if to in local_offer_addrs:
            return True, "addressed to a session with an offer"
        if to.startswith("@"):
            peer = con.execute("SELECT address FROM peers WHERE name=?", (to[1:],)).fetchone()
            if peer and peer[0] in local_offer_addrs:
                return True, "addressed to an offering session by name"
        return False, "not trusted, not a reply, recipient has no offer"

    def _on_room(self, ev: Event) -> None:
        tag = ev.tag("t") or ""
        con = _db(self.bus)
        for name, secret in con.execute("SELECT name, secret FROM mesh_rooms").fetchall():
            if room_tag(name, secret or "") != tag:
                continue
            row = self._node_row(ev.pubkey)
            if row and row[2]:
                return
            try:
                raw = crypto.nip44_decrypt(ev.content, crypto.symmetric_key(secret, "puenteo-room:" + name)) if secret else ev.content
                p = json.loads(raw)
            except Exception:
                _bump(con, "dropped_room_undecryptable")
                return
            if p.get("proto") != PROTO or not self._rate_ok(ev.pubkey):
                return
            node = row[0] if row else ("n-" + ev.pubkey[:8])
            sender = f"{p.get('from') or 'unknown'}@{node}"
            try:
                from ..bus import check_address

                check_address(sender)
            except BusError:
                sender = f"remote:{ev.pubkey[:16]}@{node}"
            try:
                self.bus.send(sender, "#" + name, str(p.get("body") or "")[:MAX_REMOTE_BODY],
                              meta={"remote_id": p.get("id"), "remote_node": node, "trust": "remote-peer"})
                _bump(con, "in_room")
            except BusError as e:
                self.log(f"room deliver failed: {e}")
            return

    def _subscribe_rooms(self) -> None:
        if not self.pool:
            return
        tags = [room_tag(n, s or "") for n, s in _db(self.bus).execute("SELECT name, secret FROM mesh_rooms")]
        if tags:
            self.pool.subscribe("rooms", [{"kinds": [KIND_ROOM], "#t": tags, "since": int(time.time()) - 60}])

    # ------------------------------------------------------------ LAN (UDP multicast)
    def _lan_send(self, ev: Event) -> None:
        data = json.dumps(ev.to_dict(), ensure_ascii=False).encode()
        if len(data) > 60000:
            return
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            s.sendto(data, (MCAST_GRP, MCAST_PORT))
            s.close()
        except OSError as e:
            self.log(f"lan send failed: {e}")

    def _lan_listen(self) -> None:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            s.bind(("", MCAST_PORT))
            mreq = struct.pack("4sl", socket.inet_aton(MCAST_GRP), socket.INADDR_ANY)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            s.settimeout(1.0)
        except OSError as e:
            self.log(f"lan disabled: {e}")
            return
        while not self._stop.is_set():
            try:
                data, addr = s.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                ev = Event.from_dict(json.loads(data))
            except Exception:
                continue
            if not ev.verify():
                continue
            if ev.kind == KIND_APP and ev.tag("d") == "puenteo:node":
                try:
                    port = int(json.loads(ev.content).get("http_port") or 0)
                except Exception:
                    port = 0
                self._learn_node(ev, "lan", f"http://{addr[0]}:{port}" if port else "")
                continue
            self._on_event(ev, "lan")


# ------------------------------------------------------------ bazaar search (local index of known offers)


def find(bus: Bus, query: str, *, limit: int = 10) -> List[Dict[str, Any]]:
    """Rank known offers (remote + local) and node sessions by BM25-ish token overlap."""
    import math

    from ..util import tokenize

    con = _db(bus)
    q = [t for t in tokenize(query) if len(t) > 1]
    rows = con.execute(
        "SELECT o.id, o.node, o.address, o.text, o.tags, o.local, n.last_seen FROM mesh_offers o"
        " LEFT JOIN mesh_nodes n ON n.pubkey=o.pubkey WHERE o.expires>?", (time.time(),)
    ).fetchall()
    docs = []
    for oid, node, addr, text, tags, local, seen in rows:
        tg = json.loads(tags or "[]")
        toks = tokenize(text + " " + " ".join(tg) * 2)
        docs.append((oid, node or "(this node)", addr, text, tg, bool(local), seen or time.time(), toks))
    if not docs:
        return []
    N = len(docs)
    df: Dict[str, int] = {}
    for d in docs:
        for t in set(d[7]):
            df[t] = df.get(t, 0) + 1
    out = []
    for oid, node, addr, text, tg, local, seen, toks in docs:
        if not q:
            score = 0.1
        else:
            score = 0.0
            for t in q:
                tf = toks.count(t) + sum(1 for x in toks if x.startswith(t) and x != t) * 0.5
                if tf:
                    score += math.log(1 + (N - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5)) * (tf * 2.2) / (tf + 1.2)
            if score <= 0:
                continue
        score += 0.5 * math.exp(-max(0.0, time.time() - seen) / 3600)
        full = addr if local else f"{addr}@{node}"
        out.append({"offer": oid, "node": node, "address": full, "text": text, "tags": tg, "score": round(score, 3),
                    "local": local})
    out.sort(key=lambda r: r["score"], reverse=True)
    return out[:limit]
