"""Incremental full-text index over every local session (SQLite FTS5, stdlib only).

- One row per user/assistant message, keyed by (provider, session_id, idx).
- A session is re-indexed only when its file (size, mtime) changes.
- Ranking is FTS5 ``bm25`` over the *whole corpus* (global IDF), plus a small
  recency boost — scores are comparable across sessions.
- If the SQLite build lacks FTS5, :func:`available` is False and callers fall
  back to the in-memory scan in :mod:`puenteo.search`.

Disable with ``PUENTEO_NO_INDEX=1``.
"""

from __future__ import annotations

import math
import os
import re
import sqlite3
import sys
import time
from typing import Iterable, List, Optional, Sequence, Tuple

from .models import Message, Session
from .paths import index_db_path

_SCHEMA = 4
_MAX_MSG_CHARS = 20000  # per message stored in the index (snippets + ranking)

_fts5: Optional[bool] = None


def available() -> bool:
    global _fts5
    if os.environ.get("PUENTEO_NO_INDEX", "").strip() not in ("", "0", "false"):
        return False
    if _fts5 is None:
        try:
            con = sqlite3.connect(":memory:")
            con.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
            con.close()
            _fts5 = True
        except Exception:
            _fts5 = False
    return _fts5


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(index_db_path()), timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
    row = con.execute("SELECT v FROM kv WHERE k='fts_schema'").fetchone()
    if not row or row[0] != str(_SCHEMA):
        con.executescript(
            """
            DROP TABLE IF EXISTS fts_sessions;
            DROP TABLE IF EXISTS fts_messages;
            CREATE TABLE fts_sessions (
                provider TEXT NOT NULL, session_id TEXT NOT NULL, path TEXT NOT NULL,
                size INTEGER, mtime REAL, indexed_at REAL, n INTEGER,
                PRIMARY KEY (provider, session_id, path)
            );
            CREATE VIRTUAL TABLE fts_messages USING fts5(
                body, role UNINDEXED, provider UNINDEXED, session_id UNINDEXED,
                path UNINDEXED, idx UNINDEXED, ts UNINDEXED,
                tokenize = 'unicode61 remove_diacritics 2'
            );
            """
        )
        con.execute("INSERT OR REPLACE INTO kv(k, v) VALUES ('fts_schema', ?)", (str(_SCHEMA),))
        con.commit()
    return con


def refresh(
    sessions: Sequence[Session],
    *,
    progress: bool = False,
    budget_s: Optional[float] = None,
) -> Tuple[int, int]:
    """
    Bring the index up to date for ``sessions``. Returns (reindexed, total).

    ``budget_s`` caps time spent (newest sessions first); the rest is indexed
    on a later call. Stale rows for deleted files are pruned.
    """
    from .providers import load_transcript

    con = _connect()
    try:
        have = {
            (p, sid, path): (size, mtime)
            for p, sid, path, size, mtime in con.execute(
                "SELECT provider, session_id, path, size, mtime FROM fts_sessions"
            )
        }
        todo = []
        for s in sessions:
            key = (s.provider, s.session_id, s.path)
            cur = have.get(key)
            if cur and cur[0] == s.size and abs((cur[1] or 0) - s.mtime) < 1e-6:
                continue
            todo.append(s)
        todo.sort(key=lambda s: s.mtime, reverse=True)

        t0 = time.time()
        done = 0
        def parse(s: Session):
            try:
                tr = load_transcript(s, include_tools=False)
                return [m for m in tr.messages if m.role in ("user", "assistant") and (m.text or "").strip()]
            except Exception:
                return []

        from ._native import core as _native_core

        workers = (os.cpu_count() or 4) if _native_core is not None else 1
        if workers > 1 and len(todo) > 4:
            # the Rust extractor releases the GIL: threads parse in parallel
            from concurrent.futures import ThreadPoolExecutor

            pool = ThreadPoolExecutor(max_workers=min(workers, 16))
            parsed = pool.map(parse, todo)
        else:
            pool = None
            parsed = (parse(s) for s in todo)

        for i, (s, msgs) in enumerate(zip(todo, parsed)):
            if budget_s is not None and time.time() - t0 > budget_s:
                break
            if progress and sys.stderr.isatty():
                print(f"\rindexing {i + 1}/{len(todo)} {s.provider} {s.session_id[:13]}", end="", file=sys.stderr)
            con.execute(
                "DELETE FROM fts_messages WHERE provider=? AND session_id=? AND path=?",
                (s.provider, s.session_id, s.path),
            )
            con.executemany(
                "INSERT INTO fts_messages(body, role, provider, session_id, path, idx, ts) VALUES (?,?,?,?,?,?,?)",
                [
                    (m.text[:_MAX_MSG_CHARS], m.role, s.provider, s.session_id, s.path, m.index, m.timestamp)
                    for m in msgs
                ],
            )
            con.execute(
                "INSERT OR REPLACE INTO fts_sessions(provider, session_id, path, size, mtime, indexed_at, n)"
                " VALUES (?,?,?,?,?,?,?)",
                (s.provider, s.session_id, s.path, s.size, s.mtime, time.time(), len(msgs)),
            )
            done += 1
            if done % 25 == 0:
                con.commit()
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True) if sys.version_info >= (3, 9) else pool.shutdown(wait=False)
        if progress and todo and sys.stderr.isatty():
            print("", file=sys.stderr)

        # prune sessions whose files vanished (only within the providers we were given)
        live = {(s.provider, s.session_id, s.path) for s in sessions}
        provs = {s.provider for s in sessions}
        for key in list(have):
            if key[0] in provs and key not in live and not os.path.exists(key[2]):
                con.execute(
                    "DELETE FROM fts_messages WHERE provider=? AND session_id=? AND path=?", key
                )
                con.execute(
                    "DELETE FROM fts_sessions WHERE provider=? AND session_id=? AND path=?", key
                )
        con.commit()
        return done, len(todo)
    finally:
        con.close()


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def fts_query(text: str) -> str:
    """
    Turn free text into a safe FTS5 query: each word quoted, OR-ed, with a
    prefix wildcard on words of 4+ chars (cheap stemming: ``export`` → ``exporting``).
    """
    words = [w for w in _TOKEN_RE.findall(text or "") if len(w) >= 2]
    if not words:
        return ""
    parts = []
    for w in words[:24]:
        w = w.replace('"', "")
        parts.append(f'"{w}"*' if len(w) >= 4 else f'"{w}"')
    return " OR ".join(parts)


def search(
    query: str,
    sessions: Sequence[Session],
    *,
    limit: int = 20,
    per_session: int = 4,
    roles: Optional[Iterable[str]] = None,
) -> List[Tuple[Session, Message, float]]:
    """Rank messages across ``sessions`` (already filtered by provider/cwd/exclude)."""
    q = fts_query(query)
    if not q or not sessions:
        return []
    by_key = {(s.provider, s.session_id, s.path): s for s in sessions}
    roles_set = set(roles or ("user", "assistant"))
    phrase = " ".join((query or "").lower().split())
    words = [w.lower() for w in _TOKEN_RE.findall(query or "") if len(w) >= 2]
    now = time.time()

    con = _connect()
    try:
        rows = con.execute(
            "SELECT provider, session_id, path, idx, role, ts, body, bm25(fts_messages) AS r"
            " FROM fts_messages WHERE fts_messages MATCH ? ORDER BY r LIMIT ?",
            (q, max(limit * 40, 400)),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()

    scored = []
    for provider, sid, path, idx, role, ts, body, r in rows:
        sess = by_key.get((provider, sid, path))
        if sess is None or role not in roles_set:
            continue
        base = -float(r)  # bm25() is lower-is-better
        tl = body.lower()
        coverage = sum(1 for w in words if w in tl) / max(len(words), 1)
        bonus = 2.0 * coverage
        if len(phrase) > 3 and phrase in tl:
            bonus += 2.5
        if role == "user":
            bonus += 0.2
        age_days = max(0.0, (now - (sess.mtime or now)) / 86400)
        bonus += 1.0 * math.exp(-age_days / 30)
        msg = Message(role=role, text=body, timestamp=ts or "", index=int(idx))
        scored.append((base + bonus, sess, msg))

    scored.sort(key=lambda x: x[0], reverse=True)
    out: List[Tuple[Session, Message, float]] = []
    per: dict = {}
    for score, sess, msg in scored:
        k = (sess.provider, sess.session_id, sess.path)
        if per.get(k, 0) >= per_session:
            continue
        per[k] = per.get(k, 0) + 1
        out.append((sess, msg, round(score, 3)))
        if len(out) >= limit:
            break
    return out


def stats() -> dict:
    con = _connect()
    try:
        n_s = con.execute("SELECT COUNT(*) FROM fts_sessions").fetchone()[0]
        n_m = con.execute("SELECT COALESCE(SUM(n), 0) FROM fts_sessions").fetchone()[0]
    finally:
        con.close()
    path = index_db_path()
    return {
        "path": str(path),
        "sessions": n_s,
        "messages": n_m,
        "bytes": path.stat().st_size if path.exists() else 0,
    }


def clear() -> None:
    con = _connect()
    try:
        con.execute("DELETE FROM fts_messages")
        con.execute("DELETE FROM fts_sessions")
        con.commit()
    finally:
        con.close()
