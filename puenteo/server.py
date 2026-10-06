"""``puenteo serve`` — local HTTP gateway to the bus and the session history.

One stdlib process exposes everything puenteo does through modern agent-facing
interfaces:

- ``/``                         live dashboard (sessions, traffic, claims, send box)
- ``/api/*``                    REST + JSON (ps, sessions, search, pull, send, inbox, claims, …)
- ``/api/events``               Server-Sent Events: bus traffic as it happens (doorbell-driven)
- ``POST /mcp``                 MCP over Streamable HTTP (same 18 tools as ``puenteo mcp``)
- ``/.well-known/agent-card.json`` + ``POST /a2a``   A2A v1.0 facade (JSON-RPC binding)

Security (per MCP's guidance for local HTTP servers): binds 127.0.0.1 only,
requires a bearer token (random, stored 0600 in the state dir; ``?token=`` for
the dashboard/SSE), and rejects requests whose Host/Origin is not localhost
(DNS-rebinding protection).
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple

from .version import __version__

DEFAULT_PORT = 7357  # "PTSS"
A2A_VERSION = "1.0"


def token_path():
    from .paths import state_dir

    return state_dir() / "serve.token"


def load_token(create: bool = True) -> str:
    p = token_path()
    try:
        t = p.read_text(encoding="utf-8").strip()
        if t:
            return t
    except OSError:
        pass
    if not create:
        return ""
    t = secrets.token_urlsafe(24)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(t + "\n")
    return t


def _j(obj: Any) -> bytes:
    from .redact import enabled_by_default

    if enabled_by_default():
        from .mcp import _redact_tree

        obj = _redact_tree(obj)
    return json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")


class Gateway:
    """Request routing, shared by the HTTP handler and tests."""

    def __init__(self, *, token: str, me: str = "user:web", port: int = DEFAULT_PORT):
        self.token = token
        self.me = me
        self.port = port
        from .mcp import Server

        self.mcp = Server(me=me)  # reuse tool handlers; identity = the web user

    # ------------------------------------------------------------- security
    def allowed_host(self, host: str) -> bool:
        h = (host or "").split(":")[0].strip("[]").lower()
        return h in ("127.0.0.1", "localhost", "::1")

    def allowed_origin(self, origin: Optional[str]) -> bool:
        if not origin:
            return True  # non-browser clients
        try:
            u = urllib.parse.urlparse(origin)
        except Exception:
            return False
        return (u.hostname or "") in ("127.0.0.1", "localhost", "::1")

    def authorized(self, headers, query: Dict[str, List[str]]) -> bool:
        auth = headers.get("Authorization", "")
        if auth.startswith("Bearer ") and secrets.compare_digest(auth[7:].strip(), self.token):
            return True
        q = (query.get("token") or [""])[0]
        return bool(q) and secrets.compare_digest(q, self.token)

    # ------------------------------------------------------------- REST
    def rest(self, method: str, path: str, query: Dict[str, List[str]], body: Dict[str, Any]) -> Tuple[int, Any]:
        from .bus import Bus

        q = {k: v[0] for k, v in query.items() if v}
        call = lambda tool, args: self._tool(tool, args)  # noqa: E731
        as_addr = body.get("as") or q.get("as") or self.me
        if path == "/api/health":
            return 200, {"ok": True, "version": __version__}
        if path == "/api/ps":
            return 200, call("peers", {k: q[k] for k in ("cwd", "agent") if k in q})
        if path == "/api/sessions":
            return 200, call("sessions", {"cwd": q.get("cwd"), "provider": q.get("provider"),
                                          "limit": int(q.get("limit", 30)), "since": q.get("since")})
        if path == "/api/search":
            if not q.get("q"):
                return 400, {"error": "q required"}
            return 200, call("search", {"query": q["q"], "limit": int(q.get("limit", 15)), "cwd": q.get("cwd"),
                                        "provider": q.get("provider"), "include_self": True})
        if path == "/api/pull":
            return 200, call("pull", {"session": q.get("session", ""), "query": q.get("q"), "mode": q.get("mode")})
        if path == "/api/outline":
            return 200, call("outline", {"session": q.get("session", "")})
        if path == "/api/log":
            with Bus() as b:
                msgs = b.history(channel=q.get("channel"), address=q.get("address"),
                                 limit=int(q.get("limit", 50)), after_seq=int(q.get("after", 0)))
                return 200, [m.to_dict() for m in msgs]
        if path == "/api/channels":
            return 200, call("channels", {})
        if path == "/api/claims":
            return 200, call("claims", {})
        if path == "/api/inbox":
            with Bus() as b:
                msgs = b.inbox(as_addr, unread_only=q.get("all") != "1", mark_read=q.get("peek") != "1")
                return 200, [m.to_dict() for m in msgs]
        if path == "/api/send" and method == "POST":
            from .deliver import push_pending

            with Bus() as b:
                m = b.send(as_addr, body.get("to", ""), body.get("text", ""), thread=body.get("thread", ""),
                           reply_to=body.get("reply_to", ""))
                d = m.to_dict()
                d["delivery"] = push_pending(b, m)
                return 200, d
        if path == "/api/claim" and method == "POST":
            with Bus() as b:
                c = b.claim(as_addr, body["resource"], ttl_s=int(body.get("ttl_s", 1800)), note=body.get("note", ""))
                return 200, c.to_dict()
        return 404, {"error": f"no route {method} {path}"}

    def _tool(self, name: str, args: Dict[str, Any]) -> Any:
        args = {k: v for k, v in args.items() if v is not None}
        return self.mcp.handlers[name](args)

    # ------------------------------------------------------------- MCP over HTTP
    def mcp_http(self, req: Any) -> Tuple[int, Any]:
        reqs = req if isinstance(req, list) else [req]
        out = []
        for r in reqs:
            if not isinstance(r, dict):
                out.append({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}})
                continue
            resp = self.mcp.handle(r)
            if resp is not None:
                out.append(resp)
        if not out:
            return 202, None
        return 200, out if isinstance(req, list) else out[0]

    # ------------------------------------------------------------- A2A v1.0 facade
    def agent_card(self) -> Dict[str, Any]:
        base = f"http://127.0.0.1:{self.port}"
        return {
            "name": "puenteo",
            "description": "Local bridge between coding-agent sessions on this machine. Send a message to any "
                           "running session (Claude Code, Codex, Gemini, …) or search their history.",
            "version": __version__,
            "supportedInterfaces": [{"url": f"{base}/a2a", "protocolBinding": "JSONRPC", "protocolVersion": A2A_VERSION}],
            "capabilities": {"streaming": False, "pushNotifications": False},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain", "application/json"],
            "securitySchemes": {"bearer": {"httpAuthSecurityScheme": {"scheme": "Bearer"}}},
            "security": [{"bearer": []}],
            "skills": [
                {"id": "relay", "name": "Relay to a live session",
                 "description": "Deliver the message to metadata.to (agent:id, @name, #channel, cwd:<path>, *) "
                                "and return the reply as the task result.",
                 "tags": ["messaging", "multi-agent"]},
                {"id": "history", "name": "Search agent history",
                 "description": "Without metadata.to, the text is a search over every local agent session.",
                 "tags": ["search", "sessions"]},
            ],
        }

    def a2a(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """JSON-RPC: SendMessage (relay or search), GetTask. Tasks = bus threads."""
        from .bus import Bus

        rid = req.get("id")
        method = req.get("method", "")
        p = req.get("params") or {}
        try:
            if method in ("SendMessage", "message/send"):
                msg = p.get("message") or {}
                text = "\n".join(part.get("text", "") for part in msg.get("parts", []) if isinstance(part, dict))
                meta = {**(p.get("metadata") or {}), **(msg.get("metadata") or {})}
                to = meta.get("to")
                if not to:
                    hits = self._tool("search", {"query": text, "limit": 8, "include_self": True})
                    return {"jsonrpc": "2.0", "id": rid, "result": {"message": {
                        "role": "agent", "messageId": secrets.token_hex(6),
                        "parts": [{"text": json.dumps(hits, ensure_ascii=False, indent=1)}]}}}
                sender = meta.get("from") or self.me
                wait_s = min(float(meta.get("wait_s", 0) or 0), 300.0)
                from .deliver import push_pending

                with Bus() as b:
                    m = b.send(sender, to, text)
                    push_pending(b, m)
                    replies = b.wait(sender, timeout=wait_s, thread=m.thread) if wait_s else []
                return {"jsonrpc": "2.0", "id": rid, "result": {"task": self._task(m.thread, replies or None)}}
            if method in ("GetTask", "tasks/get"):
                with Bus() as b:
                    msgs = b.thread(p.get("id", ""))
                return {"jsonrpc": "2.0", "id": rid, "result": self._task(p.get("id", ""), msgs[1:], history=msgs)}
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": str(e)}}

    @staticmethod
    def _task(thread: str, replies, history=None) -> Dict[str, Any]:
        def as_msg(m):
            return {"role": "agent" if m.kind != "user" else "user", "messageId": m.id,
                    "parts": [{"text": m.body}], "metadata": {"from": m.sender, "to": m.to}}

        state = "TASK_STATE_COMPLETED" if replies else "TASK_STATE_WORKING"
        task: Dict[str, Any] = {"id": thread, "contextId": thread, "status": {"state": state}}
        if replies:
            task["artifacts"] = [{"artifactId": r.id, "parts": [{"text": r.body}]} for r in replies]
        if history:
            task["history"] = [as_msg(m) for m in history]
        return task


def make_handler(gw: Gateway):
    class H(BaseHTTPRequestHandler):
        server_version = f"puenteo/{__version__}"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quiet by default
            if os.environ.get("PUENTEO_DEBUG"):
                sys.stderr.write("[serve] " + fmt % args + "\n")

        # ---------------------------------------------------------------
        def _send(self, code: int, payload: Any = None, ctype: str = "application/json", extra: Optional[Dict[str, str]] = None):
            data = b"" if payload is None else (payload if isinstance(payload, bytes) else _j(payload))
            self.send_response(code)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("application/json", "text/")) else ""))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if data and self.command != "HEAD":
                self.wfile.write(data)

        def _guard(self, query) -> bool:
            if not gw.allowed_host(self.headers.get("Host", "")) or not gw.allowed_origin(self.headers.get("Origin")):
                self._send(403, {"error": "forbidden host/origin"})
                return False
            if self.path.split("?")[0] in ("/", "/favicon.ico", "/.well-known/agent-card.json", "/api/health"):
                return True
            if not gw.authorized(self.headers, query):
                self._send(401, {"error": "missing or bad token (see `puenteo serve --print-token`)"},
                           extra={"WWW-Authenticate": "Bearer"})
                return False
            return True

        def _body(self) -> Any:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 2_000_000:
                raise ValueError("body too large")
            raw = self.rfile.read(n) if n else b""
            return json.loads(raw) if raw.strip() else {}

        # ---------------------------------------------------------------
        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(u.query)
            if not self._guard(query):
                return
            try:
                if u.path == "/":
                    from .web import PAGE

                    return self._send(200, PAGE.encode("utf-8"), "text/html",
                                      extra={"Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"})
                if u.path == "/favicon.ico":
                    return self._send(204)
                if u.path == "/.well-known/agent-card.json":
                    return self._send(200, gw.agent_card())
                if u.path == "/api/events":
                    return self._sse(query)
                code, data = gw.rest("GET", u.path, query, {})
                return self._send(code, data)
            except Exception as e:
                return self._error(e)

        def do_POST(self):
            u = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(u.query)
            if not self._guard(query):
                return
            try:
                body = self._body()
                if u.path == "/mcp":
                    code, data = gw.mcp_http(body)
                    return self._send(code, data)
                if u.path == "/a2a":
                    return self._send(200, gw.a2a(body))
                code, data = gw.rest("POST", u.path, query, body if isinstance(body, dict) else {})
                return self._send(code, data)
            except Exception as e:
                return self._error(e)

        def do_OPTIONS(self):
            self._send(204, extra={"Allow": "GET, POST, OPTIONS"})

        def _error(self, e: Exception):
            from .bus import BusError

            if isinstance(e, (BusError, ValueError, KeyError, LookupError)):
                return self._send(400, {"error": str(e)})
            if os.environ.get("PUENTEO_DEBUG"):
                traceback.print_exc()
            return self._send(500, {"error": str(e)})

        def _sse(self, query):
            """Stream bus traffic. ?address=X → only that inbox (marks read unless peek=1); else everything."""
            from .bus import Bus
            from .notify import ALL, Bell

            addr = (query.get("address") or [""])[0]
            peek = (query.get("peek") or ["1" if not addr else "0"])[0] == "1"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            b = Bus()
            last = int((query.get("after") or [self.headers.get("Last-Event-ID") or "0"])[0] or 0)
            if not last:
                tail = b.history(limit=1)
                last = tail[-1].seq if tail else 0
            bell = Bell(addr or ALL, b.path)

            idle = 15.0 if bell.sock is not None else 1.0  # no doorbell (Windows): poll fast
            try:
                # tell the client where the stream starts, so it can tell "connected" from "missed"
                self.wfile.write(f"event: ready\ndata: {json.dumps({'after': last})}\n\n".encode())
                self.wfile.flush()
                while True:
                    if addr:
                        msgs = b.inbox(addr, unread_only=not peek, mark_read=not peek, after_seq=last)
                    else:
                        msgs = b.history(limit=200, after_seq=last)
                    for m in msgs:
                        last = max(last, m.seq)
                        self.wfile.write(f"id: {m.seq}\nevent: message\ndata: ".encode() + _j(m.to_dict()) + b"\n\n")
                    if not msgs:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    bell.wait(idle)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                bell.close()
                b.close()

    return H


def serve(*, port: int = DEFAULT_PORT, host: str = "127.0.0.1", open_browser: bool = False,
          ready: Optional[Callable[[str], None]] = None) -> int:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("puenteo serve only binds to loopback (127.0.0.1)")
    token = load_token()
    who = os.environ.get("USER") or os.environ.get("USERNAME") or "web"
    gw = Gateway(token=token, me=f"user:{who}", port=port)
    httpd = ThreadingHTTPServer((host, port), make_handler(gw))
    httpd.daemon_threads = True
    gw.port = httpd.server_address[1]
    url = f"http://127.0.0.1:{gw.port}/?token={token}"
    print(f"puenteo serve on http://127.0.0.1:{gw.port}  (dashboard: {url})", file=sys.stderr, flush=True)
    print(f"  REST /api/*  SSE /api/events  MCP POST /mcp  A2A /.well-known/agent-card.json + POST /a2a", file=sys.stderr)
    print(f"  token: {token_path()}", file=sys.stderr, flush=True)
    if ready:
        ready(url)
    if open_browser:
        import webbrowser

        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
