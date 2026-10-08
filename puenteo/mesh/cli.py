"""CLI for the mesh: `puenteo mesh …`, `puenteo offer`, `puenteo find`, `puenteo ask`, `puenteo room …`."""

from __future__ import annotations

import json
import os
import sys
import time
from typing import List, Optional

MESH_COMMANDS = ("mesh", "offer", "find", "ask", "room")


def add_parsers(sub, common, as_flag) -> None:
    sp = sub.add_parser("mesh", help="Connect sessions across machines (LAN + Nostr relays, P2P, no servers)")
    common(sp)
    msub = sp.add_subparsers(dest="mesh_cmd", required=True)
    _orig_add = msub.add_parser

    def _add(*a, **kw):  # every subcommand accepts --json/--provider/--cwd, wherever agents put them
        x = _orig_add(*a, **kw)
        common(x)
        return x

    msub.add_parser = _add
    up = msub.add_parser("up", help="Run the mesh bridge for this machine (foreground)")
    up.add_argument("--no-lan", action="store_true", help="Disable LAN multicast discovery")
    up.add_argument("--port", type=int, default=7358, help="Direct HTTP port for LAN/VPN peers (0 = off)")
    up.add_argument("--relay", action="append", default=None, help="Relay URL (repeatable; overrides config)")
    msub.add_parser("status", help="Identity, relays, peers, counters")
    msub.add_parser("peers", help="Online nodes and their live sessions (--all: include offline)").add_argument("--all", action="store_true")
    fg = msub.add_parser("forget", help="Remove a node from the local peer list")
    fg.add_argument("node")
    for n, h in (("trust", "Allow any message from this node"), ("untrust", "Remove trust"),
                 ("block", "Drop everything from this node"), ("unblock", "Unblock a node")):
        x = msub.add_parser(n, help=h)
        x.add_argument("node", help="node name, npub or hex pubkey")
    nm = msub.add_parser("name", help="Show or set this node's name")
    nm.add_argument("name", nargs="?")
    sv = msub.add_parser("service", help="Run the bridge in the background at login (launchd / systemd / Task Scheduler)")
    sv.add_argument("action", choices=["install", "uninstall", "status"])
    rl = msub.add_parser("relays", help="Show or set relays (comma list, or 'none' for LAN/direct only)")
    rl.add_argument("relays", nargs="?")
    pa = msub.add_parser("peer", help="Add a direct peer endpoint (LAN/VPN/Tailscale): http://host:7358")
    pa.add_argument("url")
    pa.add_argument("--pubkey", required=True, help="the peer's npub (from its `puenteo mesh status`)")
    pa.add_argument("--name", default="")
    msub.add_parser("relay", help="Run a tiny Nostr relay for a team (self-hosted)").add_argument("--port", type=int, default=7777)

    sp = sub.add_parser("offer", help="Publish what this session offers to other machines (bazaar)")
    common(sp)
    sp.add_argument("text", nargs="?", help="What you can help with (omit with --list / --withdraw)")
    sp.add_argument("--tag", "-t", action="append", default=[])
    sp.add_argument("--ttl", type=int, default=24 * 3600, help="Seconds (default 1 day)")
    sp.add_argument("--list", action="store_true")
    sp.add_argument("--withdraw", metavar="ID", default=None)
    as_flag(sp)

    sp = sub.add_parser("find", help="Search sessions on other machines that offer something (bazaar)")
    common(sp)
    sp.add_argument("query", nargs="?", default="")
    sp.add_argument("--limit", "-n", type=int, default=10)

    sp = sub.add_parser("ask", help="Send a request to a remote session/offer and wait for the answer")
    common(sp)
    sp.add_argument("to", help="address@node, @name@node, or an offer id from `puenteo find`")
    sp.add_argument("text")
    sp.add_argument("--wait", type=float, default=300)
    as_flag(sp)

    sp = sub.add_parser("room", help="Many-to-many chat rooms across machines")
    common(sp)
    rsub = sp.add_subparsers(dest="room_cmd", required=True)
    _orig_radd = rsub.add_parser

    def _radd(*a, **kw):
        x = _orig_radd(*a, **kw)
        common(x)
        return x

    rsub.add_parser = _radd
    j = rsub.add_parser("join", help="Join (and bridge) a room")
    j.add_argument("name")
    j.add_argument("--secret", default="", help="Private room: only holders of the secret can read/post")
    as_flag(j)
    lv = rsub.add_parser("leave")
    lv.add_argument("name")
    rsub.add_parser("list")
    po = rsub.add_parser("say", help="Post to a room (same as: puenteo send '#name@*' …)")
    po.add_argument("name")
    po.add_argument("text")
    as_flag(po)


def run(args, *, json_mode: bool) -> int:
    from ..bus import Bus, BusError

    try:
        if args.cmd == "mesh":
            return _mesh(args, json_mode)
        if args.cmd == "offer":
            return _offer(args, json_mode)
        if args.cmd == "find":
            return _find(args, json_mode)
        if args.cmd == "ask":
            return _ask(args, json_mode)
        if args.cmd == "room":
            return _room(args, json_mode)
    except BusError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 2


def _ident():
    from .nostr import Identity

    return Identity.load()


def _out(obj, json_mode: bool, text: str) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str) if json_mode else text)


def _mesh(args, json_mode: bool) -> int:
    from ..bus import Bus
    from . import node as nd

    bus = Bus()
    ident = _ident()
    name = nd.node_name(bus, ident)
    con = nd._db(bus)
    c = args.mesh_cmd
    if c == "up":
        return _up(args, bus, ident, name)
    if c == "status":
        stats = dict(con.execute("SELECT k, v FROM mesh_stats").fetchall())
        n_nodes = con.execute("SELECT COUNT(*) FROM mesh_nodes").fetchone()[0]
        rooms = [r[0] for r in con.execute("SELECT name FROM mesh_rooms")]
        offers = con.execute("SELECT COUNT(*) FROM mesh_offers WHERE local=1 AND expires>?", (time.time(),)).fetchone()[0]
        obj = {"name": name, "npub": ident.npub, "pubkey": ident.pubhex, "relays": nd.relays(bus),
               "known_nodes": n_nodes, "rooms": rooms, "local_offers": offers, "counters": stats}
        _out(obj, json_mode, "\n".join([
            f"node:    {name}", f"npub:    {ident.npub}", f"relays:  {', '.join(nd.relays(bus)) or '(none: LAN/direct only)'}",
            f"peers:   {n_nodes} known   rooms: {', '.join(rooms) or '-'}   offers: {offers}",
            "traffic: " + (", ".join(f"{k}={v}" for k, v in sorted(stats.items())) or "-"),
            "", "Others reach your sessions as  <address>@" + name + "  (e.g. claude:1234@" + name + ")"]))
        return 0
    if c == "peers":
        show_all = getattr(args, "all", False)
        cutoff = time.time() - nd.PEER_STALE_S
        rows = con.execute("SELECT pubkey, name, trusted, blocked, last_seen, via, sessions FROM mesh_nodes"
                           " WHERE ? OR last_seen >= ? OR trusted=1 ORDER BY last_seen DESC", (1 if show_all else 0, cutoff)).fetchall()
        if json_mode:
            print(json.dumps([dict(pubkey=r[0], name=r[1], trusted=bool(r[2]), blocked=bool(r[3]), last_seen=r[4], via=r[5],
                                   sessions=json.loads(r[6] or "[]")) for r in rows], indent=2, ensure_ascii=False))
            return 0
        if not rows:
            print("No mesh peers yet. Run `puenteo mesh up` on each machine (same LAN, or shared relays).")
            return 0
        for pub, nm, tr, bl, seen, via, sess in rows:
            flag = "trusted" if tr else "blocked" if bl else ""
            ago = int(time.time() - (seen or 0))
            state = "online" if (seen or 0) >= cutoff else "offline"
            print(f"{nm:24} {state:7} {flag:8} via={via or '-':5} seen={_ago(ago)}  {pub[:12]}…")
            for s in json.loads(sess or "[]")[:8]:
                print(f"    {s.get('address')}@{nm}  {s.get('name') or ''}  {s.get('cwd_tail') or ''}")
        return 0
    if c == "forget":
        node = nd.Node(ident=ident, name=name, relays=[], lan=False)
        pub = node.resolve_node(args.node)
        if not pub:
            print(f"error: unknown node {args.node!r}", file=sys.stderr)
            return 2
        con.execute("DELETE FROM mesh_nodes WHERE pubkey=?", (pub,))
        con.execute("DELETE FROM mesh_offers WHERE pubkey=? AND local=0", (pub,))
        print(f"forgot {args.node}")
        return 0
    if c in ("trust", "untrust", "block", "unblock"):
        node = nd.Node(ident=ident, name=name, relays=[], lan=False)
        pub = node.resolve_node(args.node)
        if not pub:
            print(f"error: unknown node {args.node!r}", file=sys.stderr)
            return 2
        col, val = ("trusted", 1 if c == "trust" else 0) if c in ("trust", "untrust") else ("blocked", 1 if c == "block" else 0)
        con.execute(f"UPDATE mesh_nodes SET {col}=? WHERE pubkey=?", (val, pub))  # nosec B608 - fixed column names
        print(f"{c}ed {args.node}")
        return 0
    if c == "name":
        if args.name:
            import re

            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,40}", args.name):
                print("error: name must be lowercase letters, digits, '-'", file=sys.stderr)
                return 2
            nd.config_set(bus, "name", args.name)
            name = args.name
        print(name)
        return 0
    if c == "relays":
        if args.relays is not None:
            nd.config_set(bus, "relays", args.relays.strip())
        print(", ".join(nd.relays(bus)) or "none")
        return 0
    if c == "peer":
        from . import crypto

        pub = crypto.parse_pub(args.pubkey).hex()
        con.execute(
            "INSERT INTO mesh_nodes(pubkey, name, last_seen, via, endpoints) VALUES (?,?,?,?,?)"
            " ON CONFLICT(pubkey) DO UPDATE SET endpoints=excluded.endpoints, name=COALESCE(NULLIF(excluded.name,''), mesh_nodes.name)",
            (pub, args.name or ("n-" + pub[:8]), time.time(), "direct", json.dumps([args.url.rstrip("/")])),
        )
        print(f"added direct peer {args.name or pub[:8]} at {args.url}")
        return 0
    if c == "relay":
        from .relay import serve_relay

        return serve_relay(port=args.port)
    if c == "service":
        from . import service

        print(getattr(service, args.action)())
        return 0
    return 2


def _up(args, bus, ident, name) -> int:
    from . import node as nd

    rel = args.relay if args.relay is not None else nd.relays(bus)
    node = nd.Node(ident=ident, name=name, relays=rel, lan=not args.no_lan, http_port=args.port,
                   log_fn=lambda m: print(f"[mesh] {m}", file=sys.stderr, flush=True))
    if args.port:
        from .relay import start_direct_endpoint

        start_direct_endpoint(node, args.port)
    node.start()
    print(f"[mesh] {name} up  npub={ident.npub}", file=sys.stderr, flush=True)
    print(f"[mesh] relays: {', '.join(rel) or 'none'}  lan: {'on' if not args.no_lan else 'off'}  direct: "
          f"{'http://<this-host>:' + str(args.port) if args.port else 'off'}", file=sys.stderr, flush=True)
    print(f"[mesh] your sessions are reachable as <address>@{name}", file=sys.stderr, flush=True)
    import signal

    def _term(*_):
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, _term)
    except (ValueError, OSError):
        pass
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        node.stop()
        print("[mesh] stopped (said goodbye to peers)", file=sys.stderr, flush=True)
    return 0


def _ago(s: int) -> str:
    return f"{s}s ago" if s < 120 else f"{s // 60}m ago" if s < 7200 else f"{s // 3600}h ago" if s < 172800 else "long ago"


def _me(args) -> str:
    from ..cli_bus import me_address

    return me_address(args)


def _offer(args, json_mode: bool) -> int:
    import uuid

    from ..bus import Bus
    from . import node as nd

    bus = Bus()
    con = nd._db(bus)
    if args.list:
        rows = con.execute("SELECT id, address, text, tags, expires FROM mesh_offers WHERE local=1 AND expires>?",
                           (time.time(),)).fetchall()
        _out([dict(id=r[0], address=r[1], text=r[2], tags=json.loads(r[3]), expires=r[4]) for r in rows], json_mode,
             "\n".join(f"{r[0]}  {r[1]}  {r[2]}  [{', '.join(json.loads(r[3]))}]" for r in rows) or "No offers.")
        return 0
    if args.withdraw:
        con.execute("DELETE FROM mesh_offers WHERE id=? AND local=1", (args.withdraw,))
        print(f"withdrew {args.withdraw}")
        return 0
    if not args.text:
        print("error: offer text required", file=sys.stderr)
        return 2
    me = _me(args)
    oid = uuid.uuid4().hex[:10]
    con.execute("INSERT INTO mesh_offers(id, pubkey, node, address, text, tags, created, expires, local) VALUES (?,?,?,?,?,?,?,?,1)",
                (oid, _ident().pubhex, "", me, args.text[:2000], json.dumps([t.lower() for t in args.tag][:16]),
                 time.time(), time.time() + args.ttl))
    name = nd.node_name(bus, _ident())
    _out({"id": oid, "address": f"{me}@{name}"}, json_mode,
         f"offer {oid} published for {me}  (others: puenteo ask {oid} \"…\"  or  {me}@{name})\n"
         "A running `puenteo mesh up` announces it within 30 s; requests to this session are now allowed in.")
    return 0


def _find(args, json_mode: bool) -> int:
    from ..bus import Bus
    from . import node as nd

    bus = Bus()
    hits = nd.find(bus, args.query, limit=args.limit)
    if not json_mode and not hits:
        sessions = nd._db(bus).execute("SELECT name, sessions FROM mesh_nodes WHERE blocked=0").fetchall()
        print("No matching offers." + ("" if sessions else " No mesh peers known yet (is `puenteo mesh up` running?)."))
        return 0
    _out(hits, json_mode, "\n".join(
        f"[{h['score']:5.2f}] {h['offer']}  {h['address']}\n        {h['text']}  [{', '.join(h['tags'])}]" for h in hits))
    return 0


def _ask(args, json_mode: bool) -> int:
    from ..bus import Bus, BusError, format_message
    from . import node as nd

    bus = Bus()
    to = args.to
    if "@" not in to and ":" not in to:  # offer id
        row = nd._db(bus).execute("SELECT address, node, local FROM mesh_offers WHERE id=?", (to,)).fetchone()
        if not row:
            raise BusError(f"unknown offer {to!r} (see `puenteo find`)")
        to = row[0] if row[2] else f"{row[0]}@{row[1]}"
    me = _me(args)
    m = bus.send(me, to, args.text)
    print(f"sent {m.id} → {to}; waiting up to {int(args.wait)}s…", file=sys.stderr)
    got = bus.wait(me, timeout=args.wait, thread=m.thread)
    if json_mode:
        print(json.dumps([x.to_dict() for x in got], indent=2, ensure_ascii=False))
    else:
        for x in got:
            print(format_message(x, wrap=False))
    return 0 if got else 3


def _room(args, json_mode: bool) -> int:
    from ..bus import Bus
    from . import node as nd

    bus = Bus()
    con = nd._db(bus)
    c = args.room_cmd
    if c == "join":
        con.execute("INSERT OR REPLACE INTO mesh_rooms(name, secret, joined) VALUES (?,?,?)", (args.name, args.secret, time.time()))
        bus.subscribe(_me(args), args.name)
        print(f"joined room #{args.name}{' (private)' if args.secret else ''}. Post: puenteo send '#{args.name}@*' \"…\""
              "\n(a running `puenteo mesh up` picks this up within 30 s)")
        return 0
    if c == "leave":
        con.execute("DELETE FROM mesh_rooms WHERE name=?", (args.name,))
        print(f"left #{args.name}")
        return 0
    if c == "list":
        rows = con.execute("SELECT name, secret FROM mesh_rooms").fetchall()
        _out([dict(name=r[0], private=bool(r[1])) for r in rows], json_mode,
             "\n".join(f"#{r[0]}{'  (private)' if r[1] else ''}" for r in rows) or "No rooms.")
        return 0
    if c == "say":
        m = bus.send(_me(args), f"#{args.name}@*", args.text)
        print(f"posted {m.id} to #{args.name}")
        return 0
    return 2
