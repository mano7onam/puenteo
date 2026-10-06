"""Minimal RFC 6455 WebSocket client + server frames (stdlib only).

Enough for Nostr: text frames, ping/pong, close, client masking, TLS (wss://).
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import struct
import threading
import urllib.parse
from typing import Optional, Tuple

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WSClosed(Exception):
    pass


def _recv_exact(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise WSClosed("connection closed")
        buf += chunk
    return buf


def encode_frame(payload: bytes, opcode: int = 1, mask: bool = True) -> bytes:
    head = bytes([0x80 | opcode])
    n = len(payload)
    mbit = 0x80 if mask else 0
    if n < 126:
        head += bytes([mbit | n])
    elif n < 65536:
        head += bytes([mbit | 126]) + struct.pack(">H", n)
    else:
        head += bytes([mbit | 127]) + struct.pack(">Q", n)
    if not mask:
        return head + payload
    key = os.urandom(4)
    return head + key + bytes(b ^ key[i % 4] for i, b in enumerate(payload))


def read_frame(sock) -> Tuple[int, bytes]:
    """Return (opcode, payload), reassembling fragments."""
    data, op0 = b"", None
    while True:
        b1, b2 = _recv_exact(sock, 2)
        fin, op = b1 & 0x80, b1 & 0x0F
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack(">H", _recv_exact(sock, 2))[0]
        elif n == 127:
            n = struct.unpack(">Q", _recv_exact(sock, 8))[0]
        if n > 16 * 1024 * 1024:
            raise WSClosed("frame too large")
        key = _recv_exact(sock, 4) if b2 & 0x80 else None
        payload = _recv_exact(sock, n) if n else b""
        if key:
            payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
        if op >= 8:  # control frames may interleave
            return op, payload
        op0 = op if op0 is None else op0
        data += payload
        if fin:
            return op0, data


class WebSocket:
    """Blocking client connection. Thread-safe send."""

    def __init__(self, url: str, timeout: float = 10.0):
        u = urllib.parse.urlparse(url)
        if u.scheme not in ("ws", "wss"):
            raise ValueError(f"not a websocket url: {url}")
        port = u.port or (443 if u.scheme == "wss" else 80)
        raw = socket.create_connection((u.hostname, port), timeout=timeout)
        if u.scheme == "wss":
            raw = ssl.create_default_context().wrap_socket(raw, server_hostname=u.hostname)
        self.sock = raw
        self.url = url
        self._lock = threading.Lock()
        key = base64.b64encode(os.urandom(16)).decode()
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        host = u.hostname + (f":{u.port}" if u.port else "")
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nUser-Agent: puenteo\r\n\r\n")
        raw.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = raw.recv(1024)
            if not chunk:
                raise WSClosed("handshake failed")
            resp += chunk
            if len(resp) > 16384:
                raise WSClosed("handshake too large")
        head, _, rest = resp.partition(b"\r\n\r\n")
        status = head.split(b"\r\n")[0]
        if b" 101 " not in status:
            raise WSClosed("handshake rejected: " + status.decode(errors="replace"))
        want = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()  # nosec B324 - RFC 6455
        if want.lower().encode() not in head.lower():
            raise WSClosed("bad Sec-WebSocket-Accept")
        self._pending = rest
        if rest:
            self.sock = _Prefixed(raw, rest)

    def send_text(self, text: str) -> None:
        with self._lock:
            self.sock.sendall(encode_frame(text.encode("utf-8"), 1, mask=True))

    def recv_text(self, timeout: Optional[float] = None) -> Optional[str]:
        """Next text message; None on timeout. Answers pings transparently."""
        self.sock.settimeout(timeout)
        while True:
            try:
                op, payload = read_frame(self.sock)
            except socket.timeout:
                return None
            if op == 1:
                return payload.decode("utf-8", "replace")
            if op == 9:
                with self._lock:
                    self.sock.sendall(encode_frame(payload, 10, mask=True))
            elif op == 8:
                raise WSClosed("closed by peer")

    def close(self) -> None:
        try:
            with self._lock:
                self.sock.sendall(encode_frame(b"", 8, mask=True))
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


class _Prefixed:
    """Socket wrapper that first returns bytes already read past the handshake."""

    def __init__(self, sock, prefix: bytes):
        self._s, self._p = sock, prefix

    def recv(self, n):
        if self._p:
            out, self._p = self._p[:n], self._p[n:]
            return out
        return self._s.recv(n)

    def __getattr__(self, k):
        return getattr(self._s, k)


def server_handshake(headers, wfile) -> bool:
    """Complete a server-side upgrade inside http.server; returns False if not a WS request."""
    if (headers.get("Upgrade") or "").lower() != "websocket":
        return False
    key = headers.get("Sec-WebSocket-Key", "")
    accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()  # nosec B324 - RFC 6455
    wfile.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                 f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
    wfile.flush()
    return True
