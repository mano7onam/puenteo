from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Dict, List, Optional

from .models import Message, Session, Transcript
from .search import Hit
from .util import clip, format_mtime, format_size, short_id, strip_ansi


def session_row(s: Session) -> str:
    sid = short_id(s.session_id, 8)
    prov = {
        "claude_code": "claude",
        "codex": "codex",
        "grok": "grok",
        "antigravity": "agy",
        "continue_dev": "continue",
        "gemini_cli": "gemini",
        "openhands": "ohands",
    }.get(s.provider, s.provider)
    if len(prov) > 7:
        prov = prov[:7]
    cwd = s.cwd or "—"
    if len(cwd) > 48:
        cwd = "…" + cwd[-47:]
    title = strip_ansi((s.title or "").replace("\n", " "))
    if len(title) > 56:
        title = title[:55] + "…"
    return (
        f"{prov:7}  {sid:8}  {format_mtime(s.mtime):16}  "
        f"{format_size(s.size):6}  {title}\n         {cwd}"
    )


def format_session_list(
    sessions: List[Session],
    *,
    json_mode: bool = False,
    group_by: Optional[str] = None,
) -> str:
    if json_mode:
        if group_by == "cwd":
            groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            for s in sessions:
                groups[s.cwd or "(unknown)"].append(s.to_dict())
            return json.dumps(groups, ensure_ascii=False, indent=2)
        return json.dumps([s.to_dict() for s in sessions], ensure_ascii=False, indent=2)
    if not sessions:
        return "No sessions found."

    if group_by == "cwd":
        groups_s: Dict[str, List[Session]] = defaultdict(list)
        for s in sessions:
            groups_s[s.cwd or "(unknown)"].append(s)
        lines = [f"{'CWD GROUP':}  ({len(groups_s)} groups, {len(sessions)} sessions)", "-" * 88]
        # newest group first (by max mtime)
        ordered = sorted(
            groups_s.items(),
            key=lambda kv: max((x.mtime for x in kv[1]), default=0),
            reverse=True,
        )
        for cwd, items in ordered:
            lines.append("")
            lines.append(f"## {cwd}  ({len(items)} session(s))")
            for s in sorted(items, key=lambda x: x.mtime, reverse=True):
                lines.append(session_row(s))
        lines.append("")
        lines.append(
            f"{len(sessions)} session(s). Use: asb show <id>  |  asb pull <id> --query '…'"
        )
        return "\n".join(lines)

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
    lines.append("Tip: id prefix works (e.g. pull 3627012b). Filter: list --cwd ~/proj")
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
                    "text": strip_ansi(m.text),
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
        parts.append(strip_ansi(m.text).rstrip())
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
        f"# title: {strip_ansi(session.title)}",
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
        lines.append(strip_ansi(m.text).rstrip())
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
                "title": strip_ansi(h.session.title),
                "cwd": h.session.cwd,
                "message_index": h.message.index,
                "role": h.message.role,
                "snippet": strip_ansi(h.snippet),
                "path": h.session.path,
            }
            for h in hits
        ]
        return json.dumps(data, ensure_ascii=False, indent=2)
    if not hits:
        return "No hits."

    # Group by session so global search is less confusing
    by_sess: Dict[str, List[Hit]] = defaultdict(list)
    order: List[str] = []
    for h in hits:
        key = h.session.session_id
        if key not in by_sess:
            order.append(key)
        by_sess[key].append(h)

    lines: List[str] = []
    for key in order:
        group = by_sess[key]
        h0 = group[0]
        sid = short_id(h0.session.session_id, 8)
        full = h0.session.session_id
        prov = h0.session.provider.replace("claude_code", "claude")
        title = strip_ansi(h0.session.title or "")[:70]
        lines.append(f"━━ session {sid}  ({full})  [{prov}]  hits={len(group)}")
        lines.append(f"   title: {title}")
        lines.append(f"   cwd:   {h0.session.cwd or '—'}")
        for h in group:
            lines.append(
                f"   [{h.score:5.2f}]  #{h.message.index} {h.message.role}"
            )
            lines.append(f"          {strip_ansi(h.snippet)}")
        lines.append("")

    lines.append(
        "Pull one: asb pull <session_id_or_prefix> --query '…' --mode query"
    )
    lines.append(
        "Scope search: --session <id>  |  exclude self: --exclude-session <id>"
    )
    return "\n".join(lines)


def format_transcript(
    tr: Transcript,
    *,
    last: int = 0,
    json_mode: bool = False,
    start: Optional[int] = None,
    end: Optional[int] = None,
) -> str:
    msgs = tr.messages
    if start is not None or end is not None:
        lo = start if start is not None else 0
        hi = end if end is not None else 10**9
        if lo > hi:
            lo, hi = hi, lo
        msgs = [m for m in msgs if lo <= m.index <= hi]
    elif last and last > 0:
        msgs = msgs[-last:]
    return format_messages(msgs, session=tr.session, json_mode=json_mode)


def format_outline(outline: Dict[str, Any], *, json_mode: bool = False) -> str:
    if json_mode:
        return json.dumps(outline, ensure_ascii=False, indent=2)

    prov = str(outline.get("provider") or "").replace("claude_code", "claude")
    sid = outline.get("session_id") or ""
    lines = [
        f"# Outline  {prov}  {short_id(str(sid), 8)}  ({sid})",
        f"# title: {strip_ansi(str(outline.get('title') or ''))}",
        f"# cwd:   {outline.get('cwd') or '—'}",
        f"# mtime: {outline.get('mtime_human') or ''}",
        f"# path:  {outline.get('path') or ''}",
        "",
        f"messages: {outline.get('message_count')}  "
        f"(user={outline.get('user_count')}, assistant={outline.get('assistant_count')})  "
        f"range: #{outline.get('first_index')}–#{outline.get('last_index')}",
    ]
    if outline.get("first_timestamp") or outline.get("last_timestamp"):
        lines.append(
            f"time:    {outline.get('first_timestamp') or '—'}  →  {outline.get('last_timestamp') or '—'}"
        )

    cwd_path = outline.get("cwd_path") or []
    if len(cwd_path) > 1:
        lines.append("")
        lines.append("cwd changes:")
        for c in cwd_path:
            lines.append(f"  #{c.get('index')}: {c.get('cwd')}")

    milestones = outline.get("milestones") or []
    if milestones:
        lines.append("")
        lines.append("milestones:")
        for m in milestones:
            kind = (m.get("kind") or "msg")[:10]
            lines.append(
                f"  #{m.get('index'):<5}  {kind:10}  {m.get('role'):9}  "
                f"{strip_ansi(str(m.get('preview') or ''))}"
            )

    lines.append("")
    lines.append(
        "Next: asb pull <id> --mode decisions | --query '…' | --around <msg#>"
    )
    lines.append("      asb show <id> --range A:B")
    return "\n".join(lines)


def _session_header(s: Session) -> str:
    return (
        f"# {s.provider}  id={s.session_id}\n"
        f"# title: {strip_ansi(s.title)}\n"
        f"# cwd: {s.cwd}\n"
        f"# mtime: {format_mtime(s.mtime)}  size: {format_size(s.size)}\n"
        f"# path: {s.path}"
    )
