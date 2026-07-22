"""OpenHands / OpenDevin (~/.openhands)."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import List, Optional

from ..models import Message, Session, Transcript
from ..util import clean_title, cwd_matches, expand, strip_ansi, stringify_content


def _db_path() -> Path:
    return Path(expand("~/.openhands/openhands.db"))


def list_sessions(*, cwd: Optional[str] = None) -> List[Session]:
    db = _db_path()
    if not db.is_file():
        return []
    out: List[Session] = []
    try:
        conn = sqlite3.connect(str(db))
        # conversation_metadata
        try:
            rows = conn.execute(
                "select conversation_id, title, selected_repository, last_updated_at, created_at from conversation_metadata"
            ).fetchall()
        except Exception:
            rows = []
        for row in rows:
            cid, title, repo, updated, created = row
            scwd = repo or ""
            if cwd and scwd and not cwd_matches(cwd, scwd):
                continue
            # mtime from updated
            mtime = _parse_ts(updated) or _parse_ts(created) or db.stat().st_mtime
            out.append(
                Session(
                    provider="openhands",
                    session_id=str(cid),
                    path=str(db),
                    title=title or f"OpenHands {str(cid)[:8]}",
                    cwd=scwd,
                    mtime=mtime,
                    size=db.stat().st_size,
                    meta={"repo": repo},
                )
            )
        # pending_messages as activity indicator
        try:
            pending = conn.execute(
                "select conversation_id, count(*) from pending_messages group by conversation_id"
            ).fetchall()
            counts = {str(a): b for a, b in pending}
            for s in out:
                s.message_count = int(counts.get(s.session_id, 0))
        except Exception:
            pass
        conn.close()
    except Exception:
        return []
    out.sort(key=lambda s: s.mtime, reverse=True)
    return out


def session_from_path(path: str) -> Optional[Session]:
    path = expand(path)
    if path.endswith("openhands.db") or Path(path).name == "openhands.db":
        ss = list_sessions()
        return ss[0] if ss else None
    return None


def load_transcript(session: Session, *, include_tools: bool = False) -> Transcript:
    messages: List[Message] = []
    db = _db_path()
    if not db.is_file():
        return Transcript(session=session, messages=[])
    try:
        conn = sqlite3.connect(str(db))
        # pending_messages for this conversation
        try:
            rows = conn.execute(
                "select role, content, created_at from pending_messages where conversation_id=? order by created_at",
                (session.session_id,),
            ).fetchall()
        except Exception:
            rows = []
        idx = 0
        for role, content, created in rows:
            text = content
            if isinstance(content, str):
                try:
                    text = stringify_content(json.loads(content))
                except Exception:
                    text = content
            else:
                text = stringify_content(content)
            text = strip_ansi(str(text))
            if not text.strip():
                continue
            r = (role or "user").lower()
            messages.append(Message(role=r if r in ("user", "assistant", "system") else "user", text=text, timestamp=str(created or ""), index=idx))
            idx += 1
        conn.close()
    except Exception:
        pass
    if not messages:
        messages.append(
            Message(
                role="assistant",
                text=f"[openhands] session {session.session_id} metadata only (no local message body)",
                index=0,
            )
        )
    session.message_count = len(messages)
    return Transcript(session=session, messages=messages)


def _parse_ts(val) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        return 0.0
    try:
        from datetime import datetime

        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        ):
            try:
                return datetime.strptime(s[:26], fmt).timestamp()
            except ValueError:
                continue
    except Exception:
        pass
    return 0.0
