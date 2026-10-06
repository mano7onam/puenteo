"""Direct HTTP endpoint for LAN/VPN peers, and a tiny self-hosted Nostr relay.

- ``start_direct_endpoint(node, port)``: POST /mesh/event (one signed event) → node.receive().
  Events are verified by signature, so the endpoint needs no extra auth.
- ``serve_relay(port)``: a minimal NIP-01 relay (EVENT / REQ / CLOSE, filters by kinds/authors/#t/#p/#d/since/limit),
  events kept in SQLite. For teams that don't want public relays: ``puenteo mesh relay --port 7777``.
"""

from __future__ import annotations

import json
import queue
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List

from .nostr import Event
from .ws import encode_frame, read_frame, server_handshake, WSClosed


def start_direct_endpoint(node, port: int, host: str = "0.0.0.0") -> ThreadingHTTPServer:  # nosec B104 - LAN peers
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            if self.path != "/mesh/event":
                self.send_response(404)
                self.end_headers()
                return
            n = int(self.headers.get("Content-Length") or 0)
            if n > 256_000:
                self.send_response(413)
                self.end_headers()
                return
            try:
                ok = node.receive(json.loads(self.rfile.read(n)), via="direct", peer_ip=self.client_address[0])
            except Exception:
                ok = False
            self.send_response(202 if ok else 400)
            self.send_header("Content-Length", "0")
            self.end_headers()

    srv = ThreadingHTTPServer((host, port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True, name="mesh direct").start()
    return srv


def _match(ev: Dict[str, Any], f: Dict[str, Any]) -> bool:
    if "ids" in f and ev["id"] not in f["ids"]:
        return False
    if "kinds" in f and ev["kind"] not in f["kinds"]:
        return False
    if "authors" in f and ev["pubkey"] not in f["authors"]:
        return False
    if "since" in f and ev["created_at"] < f["since"]:
        return False
    if "until" in f and ev["created_at"] > f["until"]:
        return False
    for k, want in f.items():
        if k.startswith("#") and len(k) == 2:
            vals = {t[1] for t in ev["tags"] if len(t) > 1 and t[0] == k[1]}
            if not vals & set(want):
                return False
    return True


class _Store:
    def __init__(self, path: str):
        self.con = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.con.execute("CREATE TABLE IF NOT EXISTS ev (id TEXT PRIMARY KEY, created INTEGER, kind INTEGER, json TEXT)")
        self.lock = threading.Lock()

    def add(self, ev: Dict[str, Any]) -> None:
        with self.lock:
            if 30000 <= ev["kind"] < 40000:  # parameterized replaceable: keep newest per (pubkey, kind, d)
                d = next((t[1] for t in ev["tags"] if t and t[0] == "d" and len(t) > 1), "")
                for (old_id, js) in self.con.execute("SELECT id, json FROM ev WHERE kind=?", (ev["kind"],)).fetchall():
                    o = json.loads(js)
                    od = next((t[1] for t in o["tags"] if t and t[0] == "d" and len(t) > 1), "")
                    if o["pubkey"] == ev["pubkey"] and od == d:
                        if o["created_at"] > ev["created_at"]:
                            return
                        self.con.execute("DELETE FROM ev WHERE id=?", (old_id,))
            self.con.execute("INSERT OR IGNORE INTO ev(id, created, kind, json) VALUES (?,?,?,?)",
                             (ev["id"], ev["created_at"], ev["kind"], json.dumps(ev)))
            self.con.execute("DELETE FROM ev WHERE created < ?", (int(time.time()) - 14 * 86400,))

    def query(self, f: Dict[str, Any]) -> List[Dict[str, Any]]:
        lim = int(f.get("limit", 500))
        with self.lock:
            rows = self.con.execute("SELECT json FROM ev ORDER BY created DESC LIMIT 5000").fetchall()
        out = []
        for (js,) in rows:
            ev = json.loads(js)
            if _match(ev, f):
                out.append(ev)
                if len(out) >= lim:
                    break
        return list(reversed(out))


def serve_relay(*, port: int = 7777, host: str = "0.0.0.0", db: str = "", ready=None) -> int:  # nosec B104
    from ..paths import state_dir

    store = _Store(db or str(state_dir() / "relay.db"))
    clients: Dict[Any, Dict[str, List[Dict[str, Any]]]] = {}
    clock = threading.Lock()

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            if not server_handshake(self.headers, self.wfile):
                body = json.dumps({"name": "puenteo relay", "supported_nips": [1], "software": "puenteo"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/nostr+json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            sock = self.connection
            subs: Dict[str, List[Dict[str, Any]]] = {}
            slock = threading.Lock()
            with clock:
                clients[sock] = subs

            outq: "queue.Queue[bytes]" = queue.Queue(maxsize=2000)

            def writer():
                # one writer per client: a slow or dead client never blocks broadcasts to others
                while True:
                    item = outq.get()
                    if item is None:
                        return
                    try:
                        with slock:
                            sock.sendall(item)
                    except OSError:
                        return

            threading.Thread(target=writer, daemon=True).start()

            def send(msg):
                try:
                    outq.put_nowait(encode_frame(json.dumps(msg).encode(), 1, mask=False))
                except queue.Full:
                    pass  # client too slow: drop rather than stall the relay

            clients_send[sock] = send
            try:
                while True:
                    op, payload = read_frame(sock)
                    if op == 8:
                        break
                    if op == 9:
                        with slock:
                            sock.sendall(encode_frame(payload, 10, mask=False))
                        continue
                    if op != 1:
                        continue
                    try:
                        msg = json.loads(payload)
                    except ValueError:
                        continue
                    if not isinstance(msg, list) or not msg:
                        continue
                    if msg[0] == "EVENT" and len(msg) > 1:
                        try:
                            ev = Event.from_dict(msg[1])
                        except Exception:
                            send(["OK", msg[1].get("id", "") if isinstance(msg[1], dict) else "", False, "invalid: bad event"])
                            continue
                        if not ev.verify():
                            send(["OK", ev.id, False, "invalid: bad signature"])
                            continue
                        d = ev.to_dict()
                        store.add(d)
                        send(["OK", ev.id, True, ""])
                        with clock:
                            targets = list(clients.items())
                        for s2, ss in targets:
                            for sid, filters in list(ss.items()):
                                if any(_match(d, f) for f in filters):
                                    try:
                                        clients_send[s2](["EVENT", sid, d])
                                    except Exception:
                                        pass
                                    break
                    elif msg[0] == "REQ" and len(msg) > 2:
                        sid, filters = str(msg[1]), [f for f in msg[2:] if isinstance(f, dict)]
                        subs[sid] = filters
                        seen = set()
                        for f in filters:
                            for ev in store.query(f):
                                if ev["id"] not in seen:
                                    seen.add(ev["id"])
                                    send(["EVENT", sid, ev])
                        send(["EOSE", sid])
                    elif msg[0] == "CLOSE" and len(msg) > 1:
                        subs.pop(str(msg[1]), None)
            except (WSClosed, OSError):
                pass
            finally:
                with clock:
                    clients.pop(sock, None)
                clients_send.pop(sock, None)
                try:
                    outq.put_nowait(None)
                except queue.Full:
                    pass
                self.close_connection = True

    clients_send: Dict[Any, Any] = {}
    srv = ThreadingHTTPServer((host, port), H)
    srv.daemon_threads = True
    print(f"puenteo relay on ws://{host}:{srv.server_address[1]}  (point nodes at it: puenteo mesh relays ws://<host>:{srv.server_address[1]})",
          file=sys.stderr, flush=True)
    if ready:
        ready(srv)
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    return 0
