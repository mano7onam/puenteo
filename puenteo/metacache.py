"""Persistent per-file metadata cache keyed by (path, size, mtime).

Providers call :func:`cached` around their cheap-but-not-free ``_peek`` helpers
(title / cwd / id sniffing). The first ``list`` pays the parse cost; later
calls only ``stat`` files. Storage is one SQLite table in the cache dir; set
``PUENTEO_NO_CACHE=1`` to bypass it entirely.
"""

from __future__ import annotations

import atexit
import json
import os
import sqlite3
import threading
from typing import Any, Callable, Dict, Optional, Tuple

from .paths import index_db_path

_SCHEMA_VERSION = 3

_lock = threading.Lock()
_mem: Optional[Dict[Tuple[str, str], Tuple[int, float, Any]]] = None
_dirty: Dict[Tuple[str, str], Tuple[int, float, Any]] = {}


def disabled() -> bool:
    return os.environ.get("PUENTEO_NO_CACHE", "").strip() not in ("", "0", "false")


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(index_db_path()), timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        "CREATE TABLE IF NOT EXISTS meta_cache ("
        " kind TEXT NOT NULL, path TEXT NOT NULL, size INTEGER, mtime REAL,"
        " value TEXT, PRIMARY KEY (kind, path))"
    )
    con.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
    row = con.execute("SELECT v FROM kv WHERE k='meta_cache_version'").fetchone()
    if not row or row[0] != str(_SCHEMA_VERSION):
        con.execute("DELETE FROM meta_cache")
        con.execute(
            "INSERT OR REPLACE INTO kv(k, v) VALUES ('meta_cache_version', ?)",
            (str(_SCHEMA_VERSION),),
        )
        con.commit()
    return con


def _load() -> Dict[Tuple[str, str], Tuple[int, float, Any]]:
    global _mem
    if _mem is not None:
        return _mem
    _mem = {}
    try:
        con = _connect()
        try:
            for kind, path, size, mtime, value in con.execute(
                "SELECT kind, path, size, mtime, value FROM meta_cache"
            ):
                try:
                    _mem[(kind, path)] = (int(size or 0), float(mtime or 0), json.loads(value))
                except Exception:
                    continue
        finally:
            con.close()
    except Exception:
        pass
    return _mem


def flush() -> None:
    """Write pending entries. Called automatically at interpreter exit."""
    with _lock:
        if not _dirty:
            return
        items = list(_dirty.items())
        _dirty.clear()
    try:
        con = _connect()
        try:
            con.executemany(
                "INSERT OR REPLACE INTO meta_cache(kind, path, size, mtime, value) VALUES (?,?,?,?,?)",
                [(k[0], k[1], v[0], v[1], json.dumps(v[2], ensure_ascii=False)) for k, v in items],
            )
            con.commit()
        finally:
            con.close()
    except Exception:
        pass


atexit.register(flush)


def cached(kind: str, path: str, fn: Callable[[], Any], *, stat_path: Optional[str] = None) -> Any:
    """
    Return ``fn()`` memoized by the file's (size, mtime).

    ``stat_path`` lets a provider key the entry on a different file than the
    one it names (e.g. a session dir keyed on its transcript file).
    """
    if disabled():
        return fn()
    try:
        st = os.stat(stat_path or path)
    except OSError:
        return fn()
    key = (kind, path)
    mem = _load()
    hit = mem.get(key)
    if hit and hit[0] == st.st_size and abs(hit[1] - st.st_mtime) < 1e-6:
        return hit[2]
    value = fn()
    entry = (st.st_size, st.st_mtime, value)
    with _lock:
        mem[key] = entry
        _dirty[key] = entry
    return value


def clear() -> None:
    global _mem
    with _lock:
        _mem = {}
        _dirty.clear()
    try:
        con = _connect()
        try:
            con.execute("DELETE FROM meta_cache")
            con.commit()
        finally:
            con.close()
    except Exception:
        pass
