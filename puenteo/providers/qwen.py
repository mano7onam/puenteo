"""Qwen Code sessions (~/.qwen/projects/**/chats)."""

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
    first_line,
    is_noise_user_text,
    strip_ansi,
    stringify_content,
)


def _root() -> Path:
    return Path(expand("~/.qwen"))


def list_sessions(*, cwd: Optional[str] = None) -> List[Session]:
    root = _root()
    projects = root / "projects"
    if not projects.is_dir():
        return []
    out: List[Session] = []
    for proj in projects.iterdir():
        if not proj.is_dir():
            continue
        # Qwen encodes paths like Claude: -Users-foo-bar
        name = proj.name
        approx = name
        if approx.startswith("-"):
            approx = approx.replace("-", "/")
            if not approx.startswith("/"):
                approx = "/" + approx.lstrip("/")
        else:
            approx = decode_url_path(name)

        chats = proj / "chats"
        if not chats.is_dir():
            continue
        for f in chats.iterdir():
            if not f.is_file():
                continue
            if f.suffix not in (".json", ".jsonl") and ".json" not in f.name:
                continue
            # skip pure runtime markers without conversation body when tiny + runtime
            try:
                st = f.stat()
            except OSError:
                continue
            title, real_cwd, sid, msgs_hint = _peek(f, approx)
            if cwd and not cwd_matches(cwd, real_cwd or approx):
                continue
            out.append(
                Session(
                    provider="qwen",
                    session_id=sid,
                    path=str(f),
                    title=title or f"Qwen {sid[:8]}",
                    cwd=real_cwd or approx,
                    mtime=st.st_mtime,
                    size=st.st_size,
                    message_count=msgs_hint,
                    meta={"project_dir": str(proj)},
                )
            )
    out.sort(key=lambda s: s.mtime, reverse=True)
    return out


def session_from_path(path: str) -> Optional[Session]:
    path = expand(path)
    if not os.path.isfile(path):
        return None
    title, cwd, sid, _ = _peek(Path(path), "")
    st = os.stat(path)
    return Session(
        provider="qwen",
        session_id=sid,
        path=path,
        title=title or f"Qwen {sid[:8]}",
        cwd=cwd,
        mtime=st.st_mtime,
        size=st.st_size,
    )


def _peek(path: Path, approx_cwd: str) -> tuple:
    sid = path.stem.split(".")[0]
    title = ""
    cwd = approx_cwd
    msgs = 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix == ".jsonl" or text[:1] != "{" and text[:1] != "[":
            # jsonl
            for i, line in enumerate(text.splitlines()):
                if i > 40:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                msgs += 1
                role = (o.get("role") or o.get("type") or "").lower()
                content = stringify_content(o.get("content") or o.get("text") or o.get("message"))
                if role in ("user", "human") and not title:
                    cand = clean_title(content)
                    if cand:
                        title = cand
                if o.get("cwd") and not cwd:
                    cwd = str(o["cwd"])
        else:
            data = json.loads(text)
            if isinstance(data, dict):
                sid = str(data.get("id") or data.get("sessionId") or data.get("session_id") or sid)
                cwd = str(data.get("cwd") or data.get("workdir") or cwd)
                title = str(data.get("title") or data.get("summary") or title)
                messages = data.get("messages") or data.get("history") or data.get("chat") or []
                if isinstance(messages, list):
                    msgs = len(messages)
                    for m in messages[:20]:
                        if not isinstance(m, dict):
                            continue
                        role = (m.get("role") or "").lower()
                        content = stringify_content(m.get("content") or m.get("text"))
                        if role == "user" and not title:
                            cand = clean_title(content)
                            if cand:
                                title = cand
                                break
            elif isinstance(data, list):
                msgs = len(data)
    except Exception:
        pass
    return title, cwd, sid, msgs


def load_transcript(session: Session, *, include_tools: bool = False) -> Transcript:
    path = Path(session.path)
    messages: List[Message] = []
    title = session.title
    cwd = session.cwd
    idx = 0

    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return Transcript(session=session, messages=[])

    def add(role: str, text: str, ts: str = ""):
        nonlocal idx, title
        text = strip_ansi(text or "")
        if not text.strip():
            return
        if role == "user":
            if is_noise_user_text(text):
                return
            cand = clean_title(text)
            if cand and (not title or title.startswith("Qwen")):
                title = cand
        messages.append(Message(role=role, text=text, timestamp=ts, index=idx))
        idx += 1

    try:
        if path.suffix == ".jsonl" or (raw and raw[0] not in "{["):
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                role = (o.get("role") or o.get("type") or "assistant").lower()
                if role in ("human",):
                    role = "user"
                if role in ("model", "ai", "bot"):
                    role = "assistant"
                if role == "tool" and not include_tools:
                    continue
                text = stringify_content(o.get("content") or o.get("text") or o.get("message"))
                add(role if role in ("user", "assistant", "system", "tool") else "assistant", text, str(o.get("timestamp") or ""))
                if o.get("cwd"):
                    cwd = str(o["cwd"])
        else:
            data = json.loads(raw)
            if isinstance(data, dict):
                cwd = str(data.get("cwd") or data.get("workdir") or cwd)
                items = data.get("messages") or data.get("history") or data.get("chat") or []
            elif isinstance(data, list):
                items = data
            else:
                items = []
            for m in items:
                if not isinstance(m, dict):
                    continue
                role = (m.get("role") or "assistant").lower()
                if role in ("human",):
                    role = "user"
                if role in ("model", "ai"):
                    role = "assistant"
                if role == "tool" and not include_tools:
                    continue
                text = stringify_content(m.get("content") or m.get("text"))
                add(role if role in ("user", "assistant", "system", "tool") else "assistant", text, str(m.get("timestamp") or ""))
    except Exception:
        # runtime-only stub
        if not messages:
            add("assistant", f"[qwen session stub] {path.name} — no message body on disk yet")

    session.title = title or session.title
    session.cwd = cwd or session.cwd
    session.message_count = len(messages)
    return Transcript(session=session, messages=messages)
