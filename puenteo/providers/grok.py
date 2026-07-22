from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

from ..models import Message, Session, Transcript
from ..util import (
    clean_title,
    cwd_matches,
    decode_url_path,
    expand,
    extract_user_query,
    first_line,
    is_noise_user_text,
    strip_ansi,
    stringify_content,
)


def _sessions_root() -> Path:
    return Path(expand("~/.grok/sessions"))


def list_sessions(*, cwd: Optional[str] = None) -> List[Session]:
    root = _sessions_root()
    if not root.is_dir():
        return []
    out: List[Session] = []

    for workspace_dir in root.iterdir():
        if not workspace_dir.is_dir():
            continue
        # skip non-workspace entries
        name = workspace_dir.name
        if name in ("session_search.sqlite",) or name.endswith(".sqlite"):
            continue
        workspace_cwd = decode_url_path(name)
        # early skip when workspace path clearly unrelated (still re-check real_cwd later)
        if cwd and workspace_cwd.startswith("/") and not cwd_matches(cwd, workspace_cwd):
            # may still match if summary has a different real_cwd
            pass

        for sess_dir in workspace_dir.iterdir():
            if not sess_dir.is_dir():
                continue
            sid = sess_dir.name
            # uuid-like
            if len(sid) < 8:
                continue
            chat = sess_dir / "chat_history.jsonl"
            summary = sess_dir / "summary.json"
            path = str(chat if chat.is_file() else summary if summary.is_file() else sess_dir)
            if not chat.is_file() and not summary.is_file():
                continue

            title = ""
            real_cwd = workspace_cwd if workspace_cwd.startswith("/") else ""
            mtime = 0.0
            size = 0
            msg_count = 0

            if summary.is_file():
                try:
                    data = json.loads(summary.read_text(encoding="utf-8", errors="replace"))
                    title = strip_ansi(
                        data.get("session_summary")
                        or data.get("title")
                        or data.get("generated_title")
                        or ""
                    )
                    info = data.get("info") or {}
                    real_cwd = info.get("cwd") or real_cwd
                    msg_count = int(data.get("num_chat_messages") or data.get("num_messages") or 0)
                    mtime = summary.stat().st_mtime
                except Exception:
                    pass

            if chat.is_file():
                try:
                    st = chat.stat()
                    mtime = max(mtime, st.st_mtime)
                    size = st.st_size
                except OSError:
                    pass
                if not title:
                    title = _peek_title(chat)

            if cwd and not cwd_matches(cwd, real_cwd):
                continue

            out.append(
                Session(
                    provider="grok",
                    session_id=sid,
                    path=str(chat if chat.is_file() else sess_dir),
                    title=title or sid[:8],
                    cwd=real_cwd,
                    mtime=mtime,
                    size=size,
                    message_count=msg_count,
                    meta={"workspace_dir": str(workspace_dir)},
                )
            )

    out.sort(key=lambda s: s.mtime, reverse=True)
    return out


def session_from_path(path: str) -> Optional[Session]:
    path = expand(path)
    p = Path(path)
    if p.is_dir():
        chat = p / "chat_history.jsonl"
        if chat.is_file():
            p = chat
        else:
            return None
    if not p.is_file():
        return None
    # climb to session id + workspace
    sess_dir = p.parent
    sid = sess_dir.name
    workspace_dir = sess_dir.parent
    workspace_cwd = decode_url_path(workspace_dir.name)
    title = ""
    real_cwd = workspace_cwd if str(workspace_cwd).startswith("/") else ""
    summary = sess_dir / "summary.json"
    if summary.is_file():
        try:
            data = json.loads(summary.read_text(encoding="utf-8", errors="replace"))
            title = data.get("session_summary") or ""
            real_cwd = (data.get("info") or {}).get("cwd") or real_cwd
        except Exception:
            pass
    if not title:
        title = _peek_title(p)
    st = p.stat()
    return Session(
        provider="grok",
        session_id=sid,
        path=str(p),
        title=title or sid[:8],
        cwd=real_cwd,
        mtime=st.st_mtime,
        size=st.st_size,
    )


def _peek_title(chat_path: Path) -> str:
    try:
        with open(chat_path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i > 40:
                    break
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") != "user":
                    continue
                if o.get("synthetic_reason"):
                    continue
                text = stringify_content(o.get("content"))
                text = extract_user_query(text)
                cand = clean_title(text)
                if cand:
                    return cand
    except Exception:
        pass
    return ""


def load_transcript(session: Session, *, include_tools: bool = False) -> Transcript:
    path = session.path
    if os.path.isdir(path):
        chat = os.path.join(path, "chat_history.jsonl")
        if os.path.isfile(chat):
            path = chat
    messages: List[Message] = []
    idx = 0
    title = session.title

    if not os.path.isfile(path):
        return Transcript(session=session, messages=[])

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o.get("type") or ""
            if t == "system":
                continue
            if t == "reasoning" and not include_tools:
                continue
            if t == "tool_result" and not include_tools:
                continue

            if t == "user":
                if o.get("synthetic_reason") in ("system_reminder", "compaction_meta"):
                    continue
                text = strip_ansi(stringify_content(o.get("content")))
                text = extract_user_query(text)
                if is_noise_user_text(text):
                    continue
                messages.append(Message(role="user", text=text, index=idx))
                idx += 1
                if not title or len(title) < 4 or is_noise_user_text(title):
                    cand = clean_title(text)
                    if cand:
                        title = cand
            elif t == "assistant":
                content = o.get("content")
                if isinstance(content, str):
                    text = strip_ansi(content)
                else:
                    text = strip_ansi(stringify_content(content))
                # also surface tool_use blocks lightly
                if include_tools and isinstance(content, list):
                    tools = []
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            tools.append(f"[tool_use {b.get('name')}]")
                    if tools:
                        text = (text + "\n" + "\n".join(tools)).strip()
                if not (text or "").strip():
                    continue
                messages.append(Message(role="assistant", text=text, index=idx))
                idx += 1
            elif t == "tool_result" and include_tools:
                text = stringify_content(o.get("content") or o.get("result") or o)
                messages.append(
                    Message(role="tool", text=f"[tool_result] {text[:800]}", index=idx)
                )
                idx += 1
            elif t == "reasoning" and include_tools:
                text = stringify_content(o.get("content") or o.get("text") or "")
                if text.strip():
                    messages.append(Message(role="reasoning", text=text, index=idx))
                    idx += 1

    session.title = title or session.title
    session.message_count = len(messages)
    return Transcript(session=session, messages=messages)
