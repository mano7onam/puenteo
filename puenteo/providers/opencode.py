"""OpenCode sessions.

- v1.14+: SQLite ``~/.local/share/opencode/opencode.db`` (session / message / part tables;
  ``part.data`` JSON: text | reasoning | tool | patch | step-*). Times are epoch ms.
- older: JSON files under ``~/.local/share/opencode/storage/{session,message,part}``.

``Session.path`` is ``<db>#<session id>`` so every session has a stable, unique ref.
The DB is opened read-only.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import List, Optional

from ..models import Message, Session, Transcript
from ..util import clean_title, cwd_matches, expand, strip_ansi


def _roots() -> List[Path]:
    roots = []
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        roots.append(Path(xdg) / "opencode")
    roots.append(Path(expand("~/.local/share/opencode")))
    if os.name == "nt":
        roots.append(Path(os.environ.get("LOCALAPPDATA", expand("~/AppData/Local"))) / "opencode")
    out, seen = [], set()
    for r in roots:
        if str(r) not in seen:
            seen.add(str(r))
            out.append(r)
    return out


def _db() -> Optional[Path]:
    for r in _roots():
        p = r / "opencode.db"
        if p.is_file():
            return p
    return None


def _connect(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)


def list_sessions(*, cwd: Optional[str] = None) -> List[Session]:
    db = _db()
    if not db:
        return []
    out: List[Session] = []
    try:
        con = _connect(db)
    except Exception:
        return []
    try:
        rows = con.execute(
            "SELECT s.id, s.parent_id, s.directory, s.title, s.time_created, s.time_updated, s.model, s.agent,"
            " (SELECT COUNT(*) FROM message m WHERE m.session_id = s.id)"
            " FROM session s WHERE s.time_archived IS NULL ORDER BY s.time_updated DESC"
        ).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        con.close()
    for sid, parent, directory, title, created, updated, model, agent, n in rows:
        if not n:
            continue
        if cwd and not cwd_matches(cwd, directory or ""):
            continue
        t = clean_title(title or "")
        if t.startswith("New session - "):
            t = ""
        out.append(
            Session(
                provider="opencode",
                session_id=str(sid),
                path=f"{db}#{sid}",
                title=t or f"OpenCode {str(sid)[:12]}",
                cwd=str(directory or ""),
                mtime=(updated or created or 0) / 1000.0,
                size=0,
                message_count=int(n or 0),
                meta={k: v for k, v in (("parent_id", parent), ("model", model), ("agent", agent)) if v},
            )
        )
    return out


def session_from_path(path: str) -> Optional[Session]:
    if "#" not in path:
        return None
    sid = path.rsplit("#", 1)[1]
    for s in list_sessions():
        if s.session_id == sid:
            return s
    return None


def load_transcript(session: Session, *, include_tools: bool = False) -> Transcript:
    db_path, _, sid = session.path.rpartition("#")
    messages: List[Message] = []
    try:
        con = _connect(Path(db_path))
    except Exception:
        return Transcript(session=session, messages=[])
    try:
        msgs = con.execute(
            "SELECT id, time_created, data FROM message WHERE session_id=? ORDER BY time_created, id", (sid,)
        ).fetchall()
        parts = con.execute(
            "SELECT message_id, data FROM part WHERE session_id=? ORDER BY message_id, id", (sid,)
        ).fetchall()
    except sqlite3.Error:
        msgs, parts = [], []
    finally:
        con.close()
    by_msg: dict = {}
    for mid, data in parts:
        try:
            by_msg.setdefault(mid, []).append(json.loads(data))
        except Exception:
            continue
    idx = 0
    from ..util import format_mtime  # noqa: F401  (keeps import cheap path consistent)

    for mid, created, data in msgs:
        try:
            info = json.loads(data)
        except Exception:
            info = {}
        role = info.get("role") or "assistant"
        texts: List[str] = []
        for p in by_msg.get(mid, []):
            pt = p.get("type")
            if pt == "text" and not p.get("synthetic"):
                texts.append(p.get("text") or "")
            elif pt == "tool" and include_tools:
                st = p.get("state") or {}
                inp = json.dumps(st.get("input"), ensure_ascii=False)[:400] if st.get("input") is not None else ""
                texts.append(f"[tool_use {p.get('tool')}] {inp}")
                if st.get("output"):
                    texts.append(f"[tool_result] {str(st.get('output'))[:600]}")
            elif pt == "patch" and include_tools:
                texts.append(f"[patch] {', '.join(p.get('files') or [])}")
        text = strip_ansi("\n".join(t for t in texts if t).strip())
        if not text:
            continue
        ts = ""
        if created:
            import datetime as _dt

            ts = _dt.datetime.utcfromtimestamp(created / 1000.0).strftime("%Y-%m-%dT%H:%M:%SZ")
        messages.append(Message(role=role if role in ("user", "assistant", "system") else "assistant", text=text, timestamp=ts, index=idx))
        idx += 1
    if session.title.startswith("OpenCode ") or not session.title:
        for m in messages:
            if m.role == "user":
                session.title = clean_title(m.text) or session.title
                break
    session.message_count = len(messages)
    return Transcript(session=session, messages=messages)
