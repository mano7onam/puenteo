"""Gemini CLI sessions (~/.gemini — tmp/logs/project chats when present).

Note: Google Antigravity lives under ~/.gemini/antigravity and is handled by
the dedicated ``antigravity`` provider. This module covers the lighter Gemini CLI
chat histories when they appear.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

from ..models import Message, Session, Transcript
from ..util import clean_title, cwd_matches, expand, strip_ansi, stringify_content


def _role(o: dict) -> str:
    """Gemini CLI writes ``type: user|gemini|info|error``; older dumps use ``role: user|model``."""
    r = str(o.get("role") or o.get("type") or "").lower()
    if r in ("user", "human"):
        return "user"
    if r in ("gemini", "model", "assistant"):
        return "assistant"
    if r in ("tool", "function"):
        return "tool"
    if r in ("info", "error", "system", "warning"):
        return "system"
    return "assistant"


def _text(o: dict) -> str:
    c = o.get("content")
    if c is None:
        c = o.get("parts") or o.get("text") or o.get("displayContent")
    return stringify_content(c)


def _root() -> Path:
    return Path(expand("~/.gemini"))


def list_sessions(*, cwd: Optional[str] = None) -> List[Session]:
    root = _root()
    if not root.is_dir():
        return []
    out: List[Session] = []
    # common CLI chat dumps
    # Only walk Gemini CLI's own dirs; ~/.gemini/antigravity is huge and owned elsewhere.
    candidates = []
    for sub in ("tmp", "history", "sessions", "chats"):
        d = root / sub
        if d.is_dir():
            candidates.extend(d.rglob("*"))
    candidates.extend(root.glob("chat_history*"))
    candidates.extend(root.glob("session-*.json"))
    for f in candidates:
        if True:
            if not f.is_file():
                continue
            # skip antigravity tree — owned by antigravity provider
            if "antigravity" in f.parts:
                continue
            if f.suffix not in (".json", ".jsonl", ".txt", ".md"):
                continue
            if f.stat().st_size < 30:
                continue
            if f.name in ("config.json", "mcp_config.json", "installation_id"):
                continue
            sess = _from_file(f, cwd=cwd)
            if sess:
                out.append(sess)
    by = {s.path: s for s in out}
    out = list(by.values())
    out.sort(key=lambda s: s.mtime, reverse=True)
    return out


def session_from_path(path: str) -> Optional[Session]:
    path = expand(path)
    if "antigravity" in path.replace("\\", "/"):
        return None
    if not os.path.isfile(path):
        return None
    return _from_file(Path(path))


def _from_file(path: Path, *, cwd: Optional[str] = None) -> Optional[Session]:
    try:
        st = path.stat()
    except OSError:
        return None
    title = path.stem
    scwd = ""
    sid = path.stem[:32]
    n = 0
    try:
        if path.suffix == ".jsonl":
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    n += 1
                    if o.get("cwd") and not scwd:
                        scwd = str(o["cwd"])
                    if _role(o) == "user" and (o.get("role") or o.get("type")) and title == path.stem:
                        cand = clean_title(_text(o))
                        if cand:
                            title = cand
        elif path.suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict):
                sid = str(data.get("sessionId") or data.get("id") or sid)
                scwd = str(data.get("cwd") or data.get("directory") or "")
                title = str(data.get("title") or title)
                msgs = data.get("messages") or data.get("history") or []
                if isinstance(msgs, list):
                    n = len(msgs)
        else:
            # plain text / md — treat as single assistant log
            n = 1
    except Exception:
        return None
    if cwd and scwd and not cwd_matches(cwd, scwd):
        return None
    return Session(
        provider="gemini",
        session_id=sid,
        path=str(path),
        title=title or f"Gemini {sid[:8]}",
        cwd=scwd,
        mtime=st.st_mtime,
        size=st.st_size,
        message_count=n,
    )


def load_transcript(session: Session, *, include_tools: bool = False) -> Transcript:
    path = Path(session.path)
    messages: List[Message] = []
    title = session.title
    cwd = session.cwd
    idx = 0

    def add(role: str, text: str, ts: str = ""):
        nonlocal idx, title
        text = strip_ansi(text or "")
        if not text.strip():
            return
        if role == "user" and (not title or title.startswith("Gemini")):
            cand = clean_title(text)
            if cand:
                title = cand
        messages.append(Message(role=role, text=text, timestamp=ts, index=idx))
        idx += 1

    try:
        if path.suffix == ".jsonl":
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    o = json.loads(line)
                    if not isinstance(o, dict) or ("sessionId" in o and not (o.get("type") or o.get("role"))):
                        cwd = str(o.get("cwd") or cwd) if isinstance(o, dict) else cwd
                        continue
                    role = _role(o)
                    if role == "tool" and not include_tools:
                        continue
                    add(role, _text(o), str(o.get("timestamp") or ""))
        elif path.suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            items = []
            if isinstance(data, dict):
                cwd = str(data.get("cwd") or cwd)
                items = data.get("messages") or data.get("history") or []
            elif isinstance(data, list):
                items = data
            for m in items:
                if not isinstance(m, dict):
                    continue
                role = _role(m)
                if role == "tool" and not include_tools:
                    continue
                add(role, _text(m), str(m.get("timestamp") or ""))
        else:
            add("assistant", path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    session.title = title or session.title
    session.cwd = cwd or session.cwd
    session.message_count = len(messages)
    return Transcript(session=session, messages=messages)
