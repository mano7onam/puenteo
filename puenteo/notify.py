"""Instant wake-ups for bus listeners ("doorbells").

Polling SQLite every second is cheap but slow to react. Instead, every
listener (``watch``, ``wait``, SSE streams, MCP ``wait``) binds a Unix
datagram socket named after its address; after committing a message the
sender rings the recipients' sockets. Listeners ``select()`` on their socket
with a timeout, so a missed or impossible ring (Windows, permissions) only
degrades to the old polling — never to lost messages: the bus stays the
source of truth.

There is also a ``*`` bell that every traffic watcher (``log -f``, the web
dashboard, SSE ``?all=1``) listens on.
"""

from __future__ import annotations

import hashlib
import os
import select
import socket
import sys
import tempfile
from pathlib import Path
from typing import Iterable, List, Optional

SUPPORTED = hasattr(socket, "AF_UNIX") and sys.platform != "win32"
ALL = "*"


def _bell_dir() -> Path:
    from .paths import state_dir

    d = state_dir() / "bell"
    # AF_UNIX paths are limited to ~104 bytes on macOS; fall back to a short tmp dir.
    if len(str(d)) > 80:
        uid = os.getuid() if hasattr(os, "getuid") else 0
        d = Path(tempfile.gettempdir() if len(tempfile.gettempdir()) < 40 else "/tmp") / f"puenteo-{uid}" / hashlib.sha1(
            str(state_dir()).encode()).hexdigest()[:8]
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def _name(address: str) -> str:
    return hashlib.sha1(address.encode("utf-8")).hexdigest()[:20]


class Bell:
    """A listener's doorbell. Use as a context manager; ``wait(timeout)`` → rang?"""

    def __init__(self, address: str):
        self.address = address
        self.sock: Optional[socket.socket] = None
        self.path: Optional[Path] = None
        if not SUPPORTED:
            return
        try:
            d = _bell_dir() / _name(address)
            d.mkdir(exist_ok=True)
            self.path = d / f"{os.getpid()}-{id(self) & 0xFFFF:x}.sock"
            if self.path.exists():
                self.path.unlink()
            s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            s.bind(str(self.path))
            s.setblocking(False)
            self.sock = s
        except OSError:
            self.sock = None
            self.path = None

    def wait(self, timeout: float) -> bool:
        if self.sock is None:
            import time

            time.sleep(max(0.0, timeout))
            return False
        try:
            r, _, _ = select.select([self.sock], [], [], max(0.0, timeout))
        except (OSError, ValueError):
            return False
        if not r:
            return False
        try:
            while True:
                self.sock.recv(64)
        except (BlockingIOError, OSError):
            pass
        return True

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        if self.path is not None:
            try:
                self.path.unlink()
            except OSError:
                pass
            self.path = None

    def __enter__(self) -> "Bell":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def ring(addresses: Iterable[str]) -> int:
    """Wake every listener of ``addresses`` (and of ``*``). Returns sockets rung."""
    if not SUPPORTED:
        return 0
    try:
        base = _bell_dir()
    except OSError:
        return 0
    n = 0
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        for addr in set(list(addresses) + [ALL]):
            d = base / _name(addr)
            if not d.is_dir():
                continue
            for sock_path in d.glob("*.sock"):
                try:
                    s.sendto(b"!", str(sock_path))
                    n += 1
                except (ConnectionRefusedError, FileNotFoundError):
                    try:  # listener died without cleaning up
                        sock_path.unlink()
                    except OSError:
                        pass
                except OSError:
                    continue
    finally:
        s.close()
    return n


def listeners(address: str) -> List[str]:
    if not SUPPORTED:
        return []
    d = _bell_dir() / _name(address)
    return [str(p) for p in d.glob("*.sock")] if d.is_dir() else []
