"""Pi agent sessions (~/.pi/agent/sessions)."""

from __future__ import annotations

import glob
import json
import os
from typing import List, Optional

from ..models import Message, Session, Transcript
from ..util import (
    expand,
    first_line,
    is_noise_user_text,
    normalize_path,
    paths_related,
    stringify_content,
)


def _root() -> str:
    return expand("~/.pi/agent/sessions")


def list_sessions(*, cwd: Optional[str] = None) -> List[Session]:
    root = _root()
    if not os.path.isdir(root):
        return []
    cwd_n = normalize_path(cwd) if cwd else ""
    files = sorted(
        glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True),
        key=os.path.getmtime,
        reverse=True,
    )
    out: List[Session] = []
    for path in files[:400]:
        meta = _peek(path)
        if not meta:
            continue
        scwd = meta.get("cwd") or ""
        if cwd_n and scwd and not paths_related(cwd_n, scwd):
            continue
        try:
            st = os.stat(path)
        except OSError:
            continue
        sid = meta.get("session_id") or os.path.basename(path)
        out.append(
            Session(
                provider="pi",
                session_id=str(sid),
                path=path,
                title=meta.get("title") or f"Pi {str(sid)[:8]}",
                cwd=scwd,
                mtime=st.st_mtime,
                size=st.st_size,
            )
        )
    return out


def session_from_path(path: str) -> Optional[Session]:
    path = expand(path)
    if not os.path.isfile(path):
        return None
    meta = _peek(path) or {}
    st = os.stat(path)
    sid = meta.get("session_id") or os.path.basename(path)
    return Session(
        provider="pi",
        session_id=str(sid),
        path=path,
        title=meta.get("title") or f"Pi {str(sid)[:8]}",
        cwd=meta.get("cwd") or "",
        mtime=st.st_mtime,
        size=st.st_size,
    )


def _peek(path: str) -> Optional[dict]:
    title = ""
    cwd = ""
    sid = ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i > 80:
                    break
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                t = o.get("type")
                if t == "session":
                    sid = o.get("id") or sid
                    cwd = o.get("cwd") or cwd
                if t == "message":
                    msg = o.get("message") or {}
                    if msg.get("role") == "user" and not title:
                        text = stringify_content(msg.get("content"))
                        if text and not is_noise_user_text(text):
                            title = first_line(text)
        return {"session_id": sid, "cwd": cwd, "title": title}
    except Exception:
        return None


def load_transcript(session: Session, *, include_tools: bool = False) -> Transcript:
    messages: List[Message] = []
    title = session.title
    cwd = session.cwd
    sid = session.session_id
    idx = 0
    with open(session.path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o.get("type")
            if t == "session":
                sid = o.get("id") or sid
                cwd = o.get("cwd") or cwd
                continue
            if t != "message":
                continue
            msg = o.get("message") or {}
            role = msg.get("role") or "assistant"
            content = msg.get("content")
            text_parts = []
            thinking = ""
            tool_bits = []
            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        text_parts.append(str(block))
                        continue
                    bt = block.get("type")
                    if bt == "text":
                        text_parts.append(block.get("text") or "")
                    elif bt == "thinking":
                        thinking = block.get("thinking") or block.get("text") or ""
                    elif bt in ("tool_use", "toolCall", "function_call") and include_tools:
                        name = block.get("name") or "tool"
                        tool_bits.append(f"[tool_use {name}]")
                    elif bt in ("tool_result",) and include_tools:
                        tool_bits.append("[tool_result]")
            text = "\n".join(p for p in text_parts if p)
            if role == "user" and is_noise_user_text(text):
                continue
            if thinking and include_tools:
                # keep as meta in text lightly
                pass
            if tool_bits:
                text = (text + "\n" + "\n".join(tool_bits)).strip()
            if not text.strip() and not thinking:
                continue
            if not text.strip() and thinking:
                text = thinking if include_tools else ""
            if not text.strip():
                continue
            messages.append(
                Message(
                    role=role,
                    text=text,
                    timestamp=str(o.get("timestamp") or ""),
                    index=idx,
                    meta={"thinking": thinking} if thinking else {},
                )
            )
            idx += 1
            if role == "user" and (not title or title.startswith("Pi ")):
                title = first_line(text)

    session.title = title or session.title
    session.cwd = cwd or session.cwd
    session.session_id = str(sid)
    session.message_count = len(messages)
    return Transcript(session=session, messages=messages)
