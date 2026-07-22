"""Continue.dev sessions (~/.continue)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

from ..models import Message, Session, Transcript
from ..util import clean_title, cwd_matches, expand, strip_ansi, stringify_content


def _roots() -> List[Path]:
    home = Path(expand("~"))
    return [
        home / ".continue",
        home / ".continue" / "sessions",
        home / ".continue" / "index",
    ]


def list_sessions(*, cwd: Optional[str] = None) -> List[Session]:
    out: List[Session] = []
    home = Path(expand("~/.continue"))
    if not home.is_dir():
        return []
    # sessions/*.json
    for pattern in ("sessions/**/*.json", "sessions/*.json", "**/*session*.json", "index/**/*.json"):
        for f in home.glob(pattern):
            if not f.is_file() or f.stat().st_size < 20:
                continue
            if "config" in f.name.lower() or "package" in f.name.lower():
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            scwd = ""
            title = f.stem
            sid = f.stem
            msgs = 0
            if isinstance(data, dict):
                sid = str(data.get("sessionId") or data.get("id") or sid)
                title = str(data.get("title") or data.get("name") or title)
                scwd = str(data.get("workspaceDirectory") or data.get("cwd") or data.get("workspaceDir") or "")
                hist = data.get("history") or data.get("messages") or []
                if isinstance(hist, list):
                    msgs = len(hist)
                    if not title or title == f.stem:
                        for m in hist[:10]:
                            if isinstance(m, dict) and (m.get("role") or "").lower() == "user":
                                cand = clean_title(stringify_content(m.get("content") or m.get("message")))
                                if cand:
                                    title = cand
                                    break
            if cwd and scwd and not cwd_matches(cwd, scwd):
                continue
            st = f.stat()
            out.append(
                Session(
                    provider="continue",
                    session_id=sid,
                    path=str(f),
                    title=title or sid[:8],
                    cwd=scwd,
                    mtime=st.st_mtime,
                    size=st.st_size,
                    message_count=msgs,
                )
            )
    # dedupe
    by = {s.path: s for s in out}
    out = list(by.values())
    out.sort(key=lambda s: s.mtime, reverse=True)
    return out


def session_from_path(path: str) -> Optional[Session]:
    path = expand(path)
    if not os.path.isfile(path):
        return None
    st = os.stat(path)
    return Session(
        provider="continue",
        session_id=Path(path).stem,
        path=path,
        title=Path(path).stem,
        cwd="",
        mtime=st.st_mtime,
        size=st.st_size,
    )


def load_transcript(session: Session, *, include_tools: bool = False) -> Transcript:
    messages: List[Message] = []
    title = session.title
    cwd = session.cwd
    try:
        data = json.loads(Path(session.path).read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return Transcript(session=session, messages=[])
    items = []
    if isinstance(data, dict):
        cwd = str(data.get("workspaceDirectory") or data.get("cwd") or cwd)
        title = str(data.get("title") or title)
        items = data.get("history") or data.get("messages") or []
    elif isinstance(data, list):
        items = data
    idx = 0
    for m in items:
        if not isinstance(m, dict):
            continue
        role = (m.get("role") or "assistant").lower()
        if role in ("human",):
            role = "user"
        text = strip_ansi(stringify_content(m.get("content") or m.get("message") or m.get("text")))
        if not text.strip():
            continue
        if role == "tool" and not include_tools:
            continue
        if role == "user" and (not title or title == Path(session.path).stem):
            cand = clean_title(text)
            if cand:
                title = cand
        messages.append(Message(role=role if role in ("user", "assistant", "system", "tool") else "assistant", text=text, index=idx))
        idx += 1
    session.title = title or session.title
    session.cwd = cwd or session.cwd
    session.message_count = len(messages)
    return Transcript(session=session, messages=messages)
