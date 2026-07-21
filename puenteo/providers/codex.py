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


def _sessions_root() -> str:
    return expand("~/.codex/sessions")


def list_sessions(*, cwd: Optional[str] = None) -> List[Session]:
    root = _sessions_root()
    if not os.path.isdir(root):
        return []
    cwd_n = normalize_path(cwd) if cwd else ""
    files = sorted(
        glob.glob(os.path.join(root, "**", "rollout-*.jsonl"), recursive=True),
        key=os.path.getmtime,
        reverse=True,
    )
    out: List[Session] = []
    for path in files[:400]:
        meta = _peek_meta(path)
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
        title = meta.get("title") or f"Codex {sid[:8]}"
        out.append(
            Session(
                provider="codex",
                session_id=str(sid),
                path=path,
                title=title,
                cwd=scwd,
                mtime=st.st_mtime,
                size=st.st_size,
                meta={"cli_version": meta.get("cli_version"), "model": meta.get("model")},
            )
        )
    return out


def session_from_path(path: str) -> Optional[Session]:
    path = expand(path)
    if not os.path.isfile(path):
        return None
    meta = _peek_meta(path) or {}
    st = os.stat(path)
    sid = meta.get("session_id") or os.path.basename(path)
    return Session(
        provider="codex",
        session_id=str(sid),
        path=path,
        title=meta.get("title") or f"Codex {str(sid)[:8]}",
        cwd=meta.get("cwd") or "",
        mtime=st.st_mtime,
        size=st.st_size,
    )


def _peek_meta(path: str) -> Optional[dict]:
    title = ""
    meta: dict = {}
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
                payload = o.get("payload") or {}
                if t == "session_meta":
                    meta = {
                        "session_id": payload.get("session_id") or payload.get("id"),
                        "cwd": payload.get("cwd"),
                        "cli_version": payload.get("cli_version"),
                        "model": payload.get("model_provider"),
                    }
                if t == "event_msg" and payload.get("type") == "user_message" and not title:
                    text = payload.get("message") or payload.get("text") or ""
                    if isinstance(text, dict):
                        text = stringify_content(text)
                    if text and not is_noise_user_text(str(text)):
                        title = first_line(str(text))
                if t == "response_item" and (payload.get("type") == "message"):
                    if payload.get("role") == "user" and not title:
                        text = stringify_content(payload.get("content"))
                        if text and not is_noise_user_text(text):
                            title = first_line(text)
        if title:
            meta["title"] = title
        return meta or None
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
            payload = o.get("payload") or {}
            ts = str(o.get("timestamp") or "")

            if t == "session_meta":
                sid = payload.get("session_id") or payload.get("id") or sid
                cwd = payload.get("cwd") or cwd
                continue

            if t == "response_item":
                ptype = payload.get("type")
                if ptype == "message":
                    role = payload.get("role") or "assistant"
                    if role in ("developer", "system"):
                        text = stringify_content(payload.get("content"))
                        if len(text) > 1500:
                            continue
                        if not text.strip():
                            continue
                        role = "system"
                    else:
                        text = stringify_content(payload.get("content"))
                    if role == "user" and is_noise_user_text(text):
                        continue
                    if not text.strip():
                        continue
                    messages.append(Message(role=role, text=text, timestamp=ts, index=idx))
                    idx += 1
                    if role == "user" and (not title or title.startswith("Codex")):
                        title = first_line(text)
                elif ptype in ("function_call", "tool_call", "custom_tool_call") and include_tools:
                    name = payload.get("name") or payload.get("tool_name") or "tool"
                    args = payload.get("arguments") or payload.get("input") or ""
                    messages.append(
                        Message(
                            role="assistant",
                            text=f"[tool_call {name}] {str(args)[:500]}",
                            timestamp=ts,
                            index=idx,
                        )
                    )
                    idx += 1
                elif (
                    ptype in ("function_call_output", "tool_result", "custom_tool_call_output")
                    and include_tools
                ):
                    out = payload.get("output") or payload.get("content") or ""
                    messages.append(
                        Message(
                            role="tool",
                            text=f"[tool_result] {stringify_content(out)[:800]}",
                            timestamp=ts,
                            index=idx,
                        )
                    )
                    idx += 1

            elif t == "event_msg":
                et = payload.get("type")
                if et in ("user_message", "agent_message"):
                    role = "user" if et == "user_message" else "assistant"
                    text = payload.get("message") or payload.get("text") or ""
                    if isinstance(text, dict):
                        text = stringify_content(text)
                    text = str(text)
                    if role == "user" and is_noise_user_text(text):
                        continue
                    if text.strip():
                        messages.append(
                            Message(role=role, text=text, timestamp=ts, index=idx)
                        )
                        idx += 1
                        if role == "user" and (not title or str(title).startswith("Codex")):
                            title = first_line(text)

    # Deduplicate near-identical consecutive messages (codex often doubles event_msg + response_item)
    deduped: List[Message] = []
    for m in messages:
        if deduped and deduped[-1].role == m.role and deduped[-1].text == m.text:
            continue
        deduped.append(m)
    for i, m in enumerate(deduped):
        m.index = i

    session.title = title or session.title
    session.cwd = cwd or session.cwd
    session.session_id = str(sid)
    session.message_count = len(deduped)
    return Transcript(session=session, messages=deduped)
