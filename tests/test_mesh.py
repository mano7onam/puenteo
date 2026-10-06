"""Mesh: two (or three) nodes in one process, each with its own bus, wired through the built-in relay."""

from __future__ import annotations

import os
import socket
import threading
import time

import pytest


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def relay_url(tmp_path_factory):
    from puenteo.mesh.relay import serve_relay

    port = _free_port()
    db = str(tmp_path_factory.mktemp("relay") / "relay.db")
    threading.Thread(target=serve_relay, kwargs=dict(port=port, host="127.0.0.1", db=db), daemon=True).start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    return f"ws://127.0.0.1:{port}"


class Box:
    """One 'machine': its own PUENTEO_BUS/HOME, swapped in around every call."""

    def __init__(self, root, name, relay):
        self.env = {"PUENTEO_BUS": str(root / f"{name}.db"), "PUENTEO_HOME": str(root / f"{name}-home")}
        self.name = name
        with self:
            from puenteo.bus import Bus
            from puenteo.mesh import node as nd
            from puenteo.mesh.nostr import Identity

            os.makedirs(self.env["PUENTEO_HOME"], exist_ok=True)
            self.ident = Identity.load()
            b = Bus()
            nd.config_set(b, "name", name)
            self.node = nd.Node(ident=self.ident, name=name, relays=[relay], lan=False)
            self.node.start()

    def __enter__(self):
        self._old = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        return self

    def __exit__(self, *a):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def bus(self):
        from puenteo.bus import Bus

        with self:
            return Bus()


@pytest.fixture()
def boxes(tmp_path, relay_url, monkeypatch):
    monkeypatch.setenv("PUENTEO_NO_PUSH", "1")
    monkeypatch.setenv("PUENTEO_NO_PLUGINS", "1")
    from puenteo import live

    monkeypatch.setattr(live, "live_sessions", lambda **kw: [])
    a, b, c = Box(tmp_path, "alpha", relay_url), Box(tmp_path, "bravo", relay_url), Box(tmp_path, "charlie", relay_url)
    for x in (a, b, c):
        with x:
            x.node.pool.wait_connected(5)
    time.sleep(0.3)
    for x in (a, b, c):
        with x:
            x.node.announce()
    keys = {x.ident.pubhex for x in (a, b, c)}
    assert _until(lambda: all(_knows_keys(x, keys - {x.ident.pubhex}) for x in (a, b, c)))
    yield a, b, c
    for x in (a, b, c):
        x.node.stop()


def _knows_keys(box, keys) -> bool:
    with box:
        from puenteo.mesh import node as nd

        have = {r[0] for r in nd._db(box.bus()).execute("SELECT pubkey FROM mesh_nodes")}
        return keys <= have


def _known(box) -> int:
    with box:
        from puenteo.mesh import node as nd

        return nd._db(box.bus()).execute("SELECT COUNT(*) FROM mesh_nodes").fetchone()[0]


def _until(fn, timeout=8.0):
    t = time.time() + timeout
    while time.time() < t:
        if fn():
            return True
        time.sleep(0.05)
    return False


def _offer(box, addr, text, tags):
    import json
    import uuid

    from puenteo.mesh import node as nd

    with box:
        con = nd._db(box.bus())
        oid = uuid.uuid4().hex[:10]
        con.execute("INSERT INTO mesh_offers(id, pubkey, node, address, text, tags, created, expires, local) VALUES (?,?,?,?,?,?,?,?,1)",
                    (oid, box.ident.pubhex, "", addr, text, json.dumps(tags), time.time(), time.time() + 3600))
        box.node.announce()
    return oid


def test_discovery_and_bazaar(boxes):
    a, b, c = boxes
    _offer(b, "claude:pay", "payments service expert, runs integration tests", ["payments", "tests"])
    _offer(c, "codex:ui", "react frontend and design system", ["frontend"])
    from puenteo.mesh.node import find

    assert _until(lambda: len(find(a.bus(), "payments tests")) >= 1)
    hits = find(a.bus(), "who can run payments tests")
    assert hits[0]["address"] == "claude:pay@bravo"
    assert all("frontend" not in h["tags"] for h in hits)


def test_request_reply_across_nodes(boxes):
    a, b, _ = boxes
    _offer(b, "claude:pay", "payments", ["payments"])
    ba, bb = a.bus(), b.bus()
    with a:
        q = ba.send("codex:asker", "claude:pay@bravo", "are payments tests green?")
    with b:
        assert _until(lambda: bool(bb.inbox("claude:pay", mark_read=False)))
        got = bb.inbox("claude:pay")[0]
        assert got.sender == "codex:asker@alpha" and got.meta.get("trust") == "remote-peer"
        bb.send("claude:pay", "", "yes, 42/42", reply_to=got.id)
    with a:
        r = ba.wait("codex:asker", timeout=8, poll=0.1, thread=q.thread)
    assert [m.body for m in r] == ["yes, 42/42"] and r[0].sender == "claude:pay@bravo"


def test_unsolicited_dropped_trusted_allowed_blocked_dropped(boxes):
    a, b, _ = boxes
    from puenteo.mesh import node as nd

    bb = b.bus()
    with a:
        a.bus().send("x:stranger", "claude:nooffer@bravo", "unsolicited")
    time.sleep(1.0)
    with b:
        assert not bb.inbox("claude:nooffer", mark_read=False)
        con = nd._db(bb)
        assert dict(con.execute("SELECT k, v FROM mesh_stats").fetchall()).get("dropped_policy", 0) >= 1
        con.execute("UPDATE mesh_nodes SET trusted=1 WHERE name='alpha'")
    with a:
        a.bus().send("x:friend", "claude:nooffer@bravo", "trusted hello")
    with b:
        assert _until(lambda: bool(bb.inbox("claude:nooffer", mark_read=False)))
        con.execute("UPDATE mesh_nodes SET trusted=0, blocked=1 WHERE name='alpha'")
        bb.inbox("claude:nooffer")
    _offer(b, "claude:open", "open", ["x"])
    with a:
        a.bus().send("x:friend", "claude:open@bravo", "from a blocked node")
    time.sleep(1.0)
    with b:
        assert not bb.inbox("claude:open", mark_read=False)


def test_cross_node_hop_limit(boxes):
    """Two auto-responders that trust each other must not ping-pong forever."""
    a, b, _ = boxes
    from puenteo.bus import MAX_HOPS
    from puenteo.mesh import node as nd

    for x, other in ((a, "bravo"), (b, "alpha")):
        with x:
            nd._db(x.bus()).execute("UPDATE mesh_nodes SET trusted=1 WHERE name=?", (other,))
    stop = threading.Event()

    def responder(box, me):
        bus = box.bus()
        while not stop.is_set():
            with box:
                for m in bus.inbox(me):
                    if not m.sender.startswith("mesh:"):
                        try:
                            bus.send(me, "", "re: " + m.body[:20], reply_to=m.id)
                        except Exception:
                            pass
            time.sleep(0.05)

    ts = [threading.Thread(target=responder, args=(a, "bot:a"), daemon=True),
          threading.Thread(target=responder, args=(b, "bot:b"), daemon=True)]
    for t in ts:
        t.start()
    with a:
        a.bus().send("bot:a", "bot:b@bravo", "ping")
    time.sleep(4)
    stop.set()
    with a:
        n = a.bus().con.execute("SELECT COUNT(*) FROM messages WHERE body LIKE 're:%' OR body='ping'").fetchone()[0]
    assert n <= MAX_HOPS + 2, f"loop not bounded: {n} messages"


def test_rooms_public_and_private(boxes):
    a, b, c = boxes
    from puenteo.mesh import node as nd

    for x in (a, b, c):
        with x:
            con = nd._db(x.bus())
            con.execute("INSERT OR REPLACE INTO mesh_rooms(name, secret, joined) VALUES ('war','',?)", (time.time(),))
            secret = "WRONG" if x is c else "s3cret"
            con.execute("INSERT OR REPLACE INTO mesh_rooms(name, secret, joined) VALUES ('plan',?,?)", (secret, time.time()))
            x.bus().subscribe(f"watch:{x.name}", "war")
            x.bus().subscribe(f"watch:{x.name}", "plan")
            x.node._subscribe_rooms()
    time.sleep(0.5)
    with a:
        a.bus().send("claude:a", "#war@*", "hello room")
        a.bus().send("claude:a", "#plan@*", "ship friday")
    for x in (b, c):
        bx = x.bus()
        with x:
            assert _until(lambda: any(m.body == "hello room" for m in bx.inbox(f"watch:{x.name}", mark_read=False)))
    time.sleep(0.8)
    with b:
        assert any(m.body == "ship friday" for m in b.bus().inbox("watch:bravo", unread_only=False, mark_read=False))
    with c:
        assert not any(m.body == "ship friday" for m in c.bus().inbox("watch:charlie", unread_only=False, mark_read=False))


def test_remote_address_routing_is_local_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("PUENTEO_BUS", str(tmp_path / "b.db"))
    from puenteo.bus import MESH_OUTBOX, Bus, is_remote

    assert is_remote("claude:x@mac") and is_remote("@bob@mac") and is_remote("#r@*")
    assert not is_remote("@bob") and not is_remote("#r") and not is_remote("codex:y")
    with Bus() as b:
        m = b.send("user:me", "claude:x@far", "hi")
        assert m.meta["recipients"] == [MESH_OUTBOX]


def test_name_reused_by_new_key_resolves_to_freshest(tmp_path, monkeypatch):
    monkeypatch.setenv("PUENTEO_BUS", str(tmp_path / "b.db"))
    monkeypatch.setenv("PUENTEO_HOME", str(tmp_path / "h"))
    from puenteo.mesh import crypto
    from puenteo.mesh import node as nd
    from puenteo.mesh.nostr import Identity

    n = nd.Node(ident=Identity(crypto.generate_secret()), name="me", relays=[], lan=False)
    con = nd._db(n.bus)
    # the OLD key is *received* later (relay replay) but was *announced* earlier
    con.execute("INSERT INTO mesh_nodes(pubkey, name, last_seen, announced_at) VALUES ('aa'||?, 'mac', ?, ?)",
                ("0" * 62, time.time(), time.time() - 3600))
    con.execute("INSERT INTO mesh_nodes(pubkey, name, last_seen, announced_at) VALUES ('bb'||?, 'mac', ?, ?)",
                ("0" * 62, time.time() - 5, time.time()))
    assert n.resolve_node("mac").startswith("bb")
    con.execute("UPDATE mesh_nodes SET trusted=1 WHERE pubkey LIKE 'aa%'")
    assert n.resolve_node("mac").startswith("aa"), "a trusted key is pinned even if an impostor is fresher"


def test_direct_endpoint_rejects_non_http(tmp_path, monkeypatch):
    monkeypatch.setenv("PUENTEO_BUS", str(tmp_path / "b.db"))
    monkeypatch.setenv("PUENTEO_HOME", str(tmp_path / "h"))
    from puenteo.mesh import crypto
    from puenteo.mesh import node as nd
    from puenteo.mesh.nostr import Identity

    n = nd.Node(ident=Identity(crypto.generate_secret()), name="me", relays=[], lan=False)
    ev = n.ident.sign(1, "x")
    assert n._post_direct("file:///etc/passwd", ev) is False
    assert n._post_direct("ftp://x/", ev) is False
