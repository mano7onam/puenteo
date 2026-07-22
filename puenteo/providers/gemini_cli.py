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


def _root() -> Path:
    return Path(expand("~/.gemini"))


def list_sessions(*, cwd: Optional[str] = None) -> List[Session]:
    root = _root()
    if not root.is_dir():
        return []
    out: List[Session] = []
    # common CLI chat dumps
    for pattern in (
        "tmp/**/*.json",
        "tmp/**/*.jsonl",
        "history/**/*",
        "sessions/**/*",
        "chats/**/*",
        "**/chat_history*",
        "**/session-*.json",
    ):
        for f in root.glob(pattern):
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
                    if (o.get("role") or "").lower() == "user" and title == path.stem:
                        cand = clean_title(stringify_content(o.get("content") or o.get("text")))
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
            with open(path) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    o = json.loads(line)
                    role = (o.get("role") or "assistant").lower()
                    if role == "tool" and not include_tools:
                        continue
                    add(role if role in ("user", "assistant", "system", "tool") else "assistant", stringify_content(o.get("content") or o.get("text")))
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
                role = (m.get("role") or "assistant").lower()
                add(role if role in ("user", "assistant", "system", "tool") else "assistant", stringify_content(m.get("content") or m.get("text")))
        else:
            add("assistant", path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    session.title = title or session.title
    session.cwd = cwd or session.cwd
    session.message_count = len(messages)
    return Transcript(session=session, messages=messages)
