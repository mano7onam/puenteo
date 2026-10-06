from __future__ import annotations

import glob
import json
import os
import re
from typing import List, Optional

from ..models import Message, Session, Transcript
from ..util import (
    clean_title,
    dedup_mirrored,
    norm_text_key,
    cwd_matches,
    expand,
    first_line,
    is_noise_user_text,
    stringify_content,
)


def _sessions_root() -> str:
    return expand("~/.codex/sessions")


def list_sessions(*, cwd: Optional[str] = None) -> List[Session]:
    root = _sessions_root()
    if not os.path.isdir(root):
        return []
    files = sorted(
        glob.glob(os.path.join(root, "**", "rollout-*.jsonl"), recursive=True),
        key=os.path.getmtime,
        reverse=True,
    )
    out: List[Session] = []
    from ..metacache import cached

    names = _thread_names()
    for path in files:
        meta = cached("codex.meta", path, lambda: _peek_meta(path))
        if not meta:
            continue
        scwd = meta.get("cwd") or ""
        if cwd and not cwd_matches(cwd, scwd):
            continue
        try:
            st = os.stat(path)
        except OSError:
            continue
        sid = str(meta.get("session_id") or _id_from_filename(path))
        out.append(_make_session(path, st, sid, meta, names.get(sid)))
    return out


def _id_from_filename(path: str) -> str:
    m = _ROLLOUT_ID_RE.search(os.path.basename(path))
    return m.group(1) if m else os.path.basename(path)


def _make_session(path: str, st, sid: str, meta: dict, row: Optional[dict]) -> Session:
    row = row or {}
    nick = row.get("agent_nickname") or meta.get("nickname")
    title = row.get("name") or row.get("title") or meta.get("title") or ""
    title = clean_title(title) or title
    if _is_boilerplate_title(title):
        title = clean_title(row.get("first_user_message") or "") or ""
    if _is_boilerplate_title(title):
        title = ""
    if nick:
        title = f"[subagent {nick}] {title or row.get('agent_path') or ''}".strip()
    extra = {
        "cli_version": meta.get("cli_version"),
        "model": row.get("model") or meta.get("model"),
        "parent_id": row.get("parent_id") or meta.get("parent_id"),
        "nickname": nick,
        "git_branch": row.get("git_branch"),
        "originator": meta.get("originator"),
    }
    return Session(
        provider="codex",
        session_id=sid,
        path=path,
        title=title or f"Codex {sid[:13]}",
        cwd=meta.get("cwd") or row.get("cwd") or "",
        mtime=st.st_mtime,
        size=st.st_size,
        meta={k: v for k, v in extra.items() if v},
    )


_ROLLOUT_ID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")


def _is_boilerplate_title(title: str) -> bool:
    t = (title or "").strip()
    return (
        not t
        or t.startswith("# AGENTS.md instructions")
        or t.startswith("<environment_context>")
        or t.startswith("<permissions")
        or (len(t) <= 2 and t.isdigit())
    )


def _thread_names() -> dict:
    """Read Codex's own thread index (``state_*.sqlite``) read-only; {} if absent."""
    import sqlite3

    dbs = sorted(glob.glob(expand("~/.codex/state_*.sqlite")))
    if not dbs:
        return {}
    db = dbs[-1]
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
    except Exception:
        return {}
    out = {}
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(threads)")}
        want = [
            c
            for c in (
                "id", "title", "name", "first_user_message", "agent_nickname",
                "agent_path", "git_branch", "model", "cwd", "source",
            )
            if c in cols
        ]
        if "id" not in want:
            return {}
        for row in con.execute(f"SELECT {', '.join(want)} FROM threads"):  # nosec B608 - columns from a fixed whitelist
            d = dict(zip(want, row))
            src = d.pop("source", None) or ""
            if isinstance(src, str) and "parent_thread_id" in src:
                try:
                    import json as _json

                    d["parent_id"] = _json.loads(src)["subagent"]["thread_spawn"]["parent_thread_id"]
                except Exception:
                    pass
            out[d["id"]] = d
    except Exception:
        return {}
    finally:
        con.close()
    return out


def session_from_path(path: str) -> Optional[Session]:
    path = expand(path)
    if not os.path.isfile(path):
        return None
    meta = _peek_meta(path) or {}
    st = os.stat(path)
    sid = str(meta.get("session_id") or _id_from_filename(path))
    return _make_session(path, st, sid, meta, _thread_names().get(sid))


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
                if t == "session_meta" and not meta:
                    # ``id`` is this thread; ``session_id`` is the root thread
                    # (subagents and forks share it with their parent).
                    meta = {
                        "session_id": payload.get("id") or payload.get("session_id"),
                        "root_id": payload.get("session_id"),
                        "parent_id": payload.get("parent_thread_id") or payload.get("forked_from_id"),
                        "cwd": payload.get("cwd"),
                        "cli_version": payload.get("cli_version"),
                        "model": payload.get("model_provider"),
                        "nickname": payload.get("agent_nickname"),
                        "thread_source": payload.get("thread_source"),
                        "originator": payload.get("originator"),
                    }
                if t == "event_msg" and payload.get("type") == "user_message" and not title:
                    text = payload.get("message") or payload.get("text") or ""
                    if isinstance(text, dict):
                        text = stringify_content(text)
                    cand = clean_title(str(text) if text else "")
                    if cand:
                        title = cand
                if t == "response_item" and (payload.get("type") == "message"):
                    if payload.get("role") == "user" and not title:
                        text = stringify_content(payload.get("content"))
                        cand = clean_title(text)
                        if cand:
                            title = cand
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
    seen_meta = False

    from .._native import core

    if core is not None:
        try:
            rows = core.extract_codex(session.path, include_tools)
        except OSError:
            rows = None
        if rows is not None:
            for role, text, ts, src in rows:
                if role == "__meta__":
                    if not seen_meta:
                        sid, cwd, seen_meta = src or sid, text or cwd, True
                    continue
                if src == "response_item":
                    if role in ("developer", "system"):
                        if len(text) > 1500 or not text.strip():
                            continue
                        role = "system"
                    if role == "user" and is_noise_user_text(text):
                        continue
                    if not text.strip():
                        continue
                    messages.append(Message(role=role, text=text, timestamp=ts, index=idx))
                elif src == "event_msg":
                    if role == "user" and is_noise_user_text(text):
                        continue
                    if not text.strip():
                        continue
                    messages.append(Message(role=role, text=text, timestamp=ts, index=idx, meta={"src": "event_msg"}))
                else:
                    messages.append(Message(role=role, text=text, timestamp=ts, index=idx))
                idx += 1
                if role == "user" and (not title or str(title).startswith("Codex")) and not _is_boilerplate_title(first_line(text)):
                    title = first_line(text)
            return _finish_codex(session, messages, title, cwd, sid)

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
                if not seen_meta:
                    sid = payload.get("id") or payload.get("session_id") or sid
                    cwd = payload.get("cwd") or cwd
                    seen_meta = True
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
                    if role == "user" and (not title or title.startswith("Codex")) and not _is_boilerplate_title(first_line(text)):
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
                            Message(role=role, text=text, timestamp=ts, index=idx, meta={"src": "event_msg"})
                        )
                        idx += 1
                        if role == "user" and (not title or str(title).startswith("Codex")) and not _is_boilerplate_title(first_line(text)):
                            title = first_line(text)

    return _finish_codex(session, messages, title, cwd, sid)


def _finish_codex(session: Session, messages: List[Message], title, cwd, sid) -> Transcript:
    # Codex logs each turn as response_item *and* event_msg (not always adjacent).
    deduped: List[Message] = dedup_mirrored(
        messages,
        key=lambda m: None if m.text.startswith(("[tool_call", "[tool_result")) else norm_text_key(m.role, m.text),
        is_mirror=lambda m: m.meta.get("src") == "event_msg",
    )
    for i, m in enumerate(deduped):
        m.index = i

    session.title = title or session.title
    session.cwd = cwd or session.cwd
    session.session_id = str(sid)
    session.message_count = len(deduped)
    return Transcript(session=session, messages=deduped)
