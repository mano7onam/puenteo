from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .models import Message, Session, Transcript
from .search import Hit
from .util import clip, format_mtime, format_size, short_id


def session_row(s: Session) -> str:
    sid = short_id(s.session_id, 8)
    prov = {
        "claude_code": "claude",
        "codex": "codex",
        "grok": "grok",
    }.get(s.provider, s.provider)
    cwd = s.cwd or "—"
    if len(cwd) > 48:
        cwd = "…" + cwd[-47:]
    title = (s.title or "").replace("\n", " ")
    if len(title) > 56:
        title = title[:55] + "…"
    return (
        f"{prov:7}  {sid:8}  {format_mtime(s.mtime):16}  "
        f"{format_size(s.size):6}  {title}\n         {cwd}"
    )


def format_session_list(sessions: List[Session], *, json_mode: bool = False) -> str:
    if json_mode:
        return json.dumps([s.to_dict() for s in sessions], ensure_ascii=False, indent=2)
    if not sessions:
        return "No sessions found."
    lines = [
        f"{'PROV':7}  {'ID':8}  {'MTIME':16}  {'SIZE':6}  TITLE / CWD",
        "-" * 88,
    ]
    for s in sessions:
        lines.append(session_row(s))
    lines.append("")
    lines.append(
        f"{len(sessions)} session(s). Use: asb show <id>  |  asb pull <id> --query '…'  (or puenteo …)"
    )
    return "\n".join(lines)


def format_messages(
    messages: List[Message],
    *,
    session: Optional[Session] = None,
    json_mode: bool = False,
    max_chars: int = 0,
) -> str:
    if json_mode:
        payload: Dict[str, Any] = {
            "session": session.to_dict() if session else None,
            "messages": [
                {
                    "index": m.index,
                    "role": m.role,
                    "text": m.text,
                    "timestamp": m.timestamp,
                    "meta": m.meta,
                }
                for m in messages
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    parts: List[str] = []
    if session:
        parts.append(_session_header(session))
        parts.append("")
    for m in messages:
        parts.append(f"### [{m.index}] {m.role}")
        parts.append(m.text.rstrip())
        parts.append("")
    text = "\n".join(parts).rstrip() + "\n"
    if max_chars and max_chars > 0:
        text = clip(text, max_chars)
        if not text.endswith("\n"):
            text += "\n"
    return text


def format_pack(
    session: Session,
    messages: List[Message],
    *,
    purpose: str = "handoff",
    json_mode: bool = False,
    max_chars: int = 0,
) -> str:
    if json_mode:
        return format_messages(messages, session=session, json_mode=True, max_chars=max_chars)

    lines = [
        "# Puenteo — context pack",
        f"# purpose: {purpose}",
        f"# provider: {session.provider}",
        f"# session_id: {session.session_id}",
        f"# title: {session.title}",
        f"# cwd: {session.cwd}",
        f"# source: {session.path}",
        "",
        "The following messages were pulled from another local agent session.",
        "Use only what is relevant; do not assume files still match disk.",
        "",
        "---",
        "",
    ]
    for m in messages:
        lines.append(f"## {m.role} (#{m.index})")
        lines.append("")
        lines.append(m.text.rstrip())
        lines.append("")
        lines.append("---")
        lines.append("")
    text = "\n".join(lines)
    if max_chars and max_chars > 0:
        text = clip(text, max_chars)
        if not text.endswith("\n"):
            text += "\n"
    return text


def format_hits(hits: List[Hit], *, json_mode: bool = False) -> str:
    if json_mode:
        data = [
            {
                "score": h.score,
                "provider": h.session.provider,
                "session_id": h.session.session_id,
                "title": h.session.title,
                "cwd": h.session.cwd,
                "message_index": h.message.index,
                "role": h.message.role,
                "snippet": h.snippet,
                "path": h.session.path,
            }
            for h in hits
        ]
        return json.dumps(data, ensure_ascii=False, indent=2)
    if not hits:
        return "No hits."
    lines = []
    for h in hits:
        sid = short_id(h.session.session_id, 8)
        prov = h.session.provider.replace("claude_code", "claude")
        lines.append(
            f"[{h.score:5.2f}] {prov:7} {sid}  #{h.message.index} {h.message.role}"
        )
        lines.append(f"  title: {h.session.title[:70]}")
        lines.append(f"  cwd:   {h.session.cwd or '—'}")
        lines.append(f"  {h.snippet}")
        lines.append("")
    lines.append("Pull one: asb pull <session_id> --query '…' --mode query  (or puenteo pull …)")
    return "\n".join(lines)


def format_transcript(tr: Transcript, *, last: int = 0, json_mode: bool = False) -> str:
    msgs = tr.messages
    if last and last > 0:
        msgs = msgs[-last:]
    return format_messages(msgs, session=tr.session, json_mode=json_mode)


def _session_header(s: Session) -> str:
    return (
        f"# {s.provider}  id={s.session_id}\n"
        f"# title: {s.title}\n"
        f"# cwd: {s.cwd}\n"
        f"# mtime: {format_mtime(s.mtime)}  size: {format_size(s.size)}\n"
        f"# path: {s.path}"
    )
