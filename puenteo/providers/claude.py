from __future__ import annotations

import glob
import json
import os
import re
from typing import List, Optional, Tuple

from ..models import Message, Session, Transcript
from ..util import (
    clean_title,
    cwd_matches,
    expand,
    extract_user_query,
    first_line,
    is_noise_user_text,
    normalize_path,
    strip_ansi,
    stringify_content,
)


def _projects_root() -> str:
    return expand("~/.claude/projects")


def _encode_path(p: str) -> str:
    """Claude Code project dir name: every non-alphanumeric char becomes ``-``."""
    return re.sub(r"[^A-Za-z0-9]", "-", p)


def list_sessions(*, cwd: Optional[str] = None) -> List[Session]:
    root = _projects_root()
    if not os.path.isdir(root):
        return []
    out: List[Session] = []
    pattern = os.path.join(root, "**", "*.jsonl")
    for path in glob.glob(pattern, recursive=True):
        if "/subagents/" in path.replace("\\", "/"):
            sub = _subagent_session(path, cwd)
            if sub:
                out.append(sub)
            continue
        # project dir name → approximate cwd
        rel = os.path.relpath(path, root)
        parts = rel.split(os.sep)
        if len(parts) < 2:
            continue
        proj = parts[0]
        # Claude encodes /Users/foo/bar as -Users-foo-bar
        approx_cwd = proj
        if approx_cwd.startswith("-"):
            # best-effort decode: leading - then replace - with /
            # this is lossy for hyphens in path components; we refine from file content
            approx_cwd = approx_cwd.replace("-", "/")
            if not approx_cwd.startswith("/"):
                approx_cwd = "/" + approx_cwd.lstrip("/")

        try:
            st = os.stat(path)
        except OSError:
            continue

        title, real_cwd, sid = _peek_cached(path)
        sid = sid or os.path.splitext(os.path.basename(path))[0]
        real_cwd = real_cwd or approx_cwd

        if cwd and not cwd_matches(cwd, real_cwd):
            # fallback: lossy encoded project dir may still contain the fragment
            if not cwd_matches(cwd, approx_cwd) and not cwd_matches(cwd, proj):
                continue

        out.append(
            Session(
                provider="claude_code",
                session_id=sid,
                path=path,
                title=title or sid[:8],
                cwd=real_cwd,
                mtime=st.st_mtime,
                size=st.st_size,
                meta={"project_dir": proj},
            )
        )
    out.sort(key=lambda s: s.mtime, reverse=True)
    return out


def _subagent_session(path: str, cwd: Optional[str]) -> Optional[Session]:
    """``<project>/<parent-sid>/subagents/agent-<id>.jsonl`` → child session ``agent-<id>``."""
    from ..metacache import cached

    parts = path.replace("\\", "/").split("/")
    try:
        i = parts.index("subagents")
    except ValueError:
        return None
    parent = parts[i - 1]
    aid = os.path.splitext(parts[-1])[0]
    try:
        st = os.stat(path)
    except OSError:
        return None
    title, real_cwd, _sid = _peek_cached(path)
    if cwd and not cwd_matches(cwd, real_cwd):
        return None
    meta_path = path[: -len(".jsonl")] + ".meta.json"
    info = cached("claude.submeta", meta_path, lambda: _read_json(meta_path), stat_path=path) or {}
    desc = str(info.get("description") or "")
    label = f"[subagent {info.get('agentType') or 'agent'}] {desc or title}".strip()
    return Session(
        provider="claude_code",
        session_id=aid,
        path=path,
        title=label,
        cwd=real_cwd,
        mtime=st.st_mtime,
        size=st.st_size,
        meta={"parent_id": parent, "subagent": True, "agent_type": info.get("agentType"), "tool_use_id": info.get("toolUseId")},
    )


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def session_from_path(path: str) -> Optional[Session]:
    path = expand(path)
    if not os.path.isfile(path):
        return None
    if "/subagents/" in path.replace("\\", "/"):
        return _subagent_session(path, None)
    title, cwd, sid = _peek_cached(path)
    sid = sid or os.path.splitext(os.path.basename(path))[0]
    st = os.stat(path)
    return Session(
        provider="claude_code",
        session_id=sid,
        path=path,
        title=title or sid[:8],
        cwd=cwd or "",
        mtime=st.st_mtime,
        size=st.st_size,
    )


def _peek_cached(path: str) -> Tuple[str, str, str]:
    from ..metacache import cached

    return tuple(cached("claude.peek", path, lambda: list(_peek(path))))  # type: ignore[return-value]


def _peek(path: str) -> Tuple[str, str, str]:
    title = ""
    cwd = ""
    sid = ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i > 120:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("sessionId") and not sid:
                    sid = str(o["sessionId"])
                if o.get("cwd") and not cwd:
                    cwd = str(o["cwd"])
                t = o.get("type")
                if t == "ai-title" and o.get("title") and not title:
                    cand = clean_title(str(o["title"]), 160)
                    if cand:
                        title = cand
                if t == "user" and not title and not o.get("isMeta"):
                    msg = o.get("message") or {}
                    text = stringify_content(msg.get("content"))
                    text = extract_user_query(text)
                    cand = clean_title(text, 120)
                    if cand:
                        title = cand
    except Exception:
        pass
    return title, cwd, sid


def load_transcript(session: Session, *, include_tools: bool = False) -> Transcript:
    messages: List[Message] = []
    title = session.title
    cwd = session.cwd
    sid = session.session_id
    idx = 0

    from .._native import core

    rows = None
    if core is not None:
        try:
            rows = core.extract_claude(session.path, include_tools)
        except OSError:
            rows = None
    if rows is not None:
        for role, text, ts, src in rows:
            if role == "__title__":
                cand = clean_title(text, 160)
                if cand:
                    title = cand
                continue
            mid, rcwd, rsid = (src.split("\t") + ["", "", ""])[:3]
            cwd = rcwd or cwd
            sid = rsid or sid
            text = strip_ansi(text)
            has_tools = "[tool_use " in text or "[tool_result]" in text
            if role == "user":
                text = extract_user_query(text)
                if is_noise_user_text(text) and not has_tools:
                    continue
            if not text.strip():
                continue
            if role == "assistant" and mid and messages and messages[-1].meta.get("message_id") == mid:
                messages[-1].text = (messages[-1].text + "\n" + text).strip()
                continue
            messages.append(Message(role=role, text=text, timestamp=ts, index=idx,
                                    meta={"message_id": mid} if (mid and role == "assistant") else {}))
            idx += 1
    else:
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
            if t == "ai-title" and o.get("title"):
                cand = clean_title(str(o["title"]), 160)
                if cand:
                    title = cand
                continue
            if t not in ("user", "assistant", "system"):
                continue
            # skill bodies, caveats, compaction summaries: injected, not said by anyone
            if o.get("isMeta") or o.get("isCompactSummary") or o.get("isVisibleInTranscriptOnly"):
                continue
            if o.get("cwd"):
                cwd = o["cwd"]
            if o.get("sessionId"):
                sid = o["sessionId"]

            msg = o.get("message") or {}
            role = msg.get("role") or t
            content = msg.get("content")
            text_parts: List[str] = []
            tool_bits: List[str] = []

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
                        continue
                    elif bt == "tool_use" and include_tools:
                        name = block.get("name") or "tool"
                        inp = block.get("input")
                        try:
                            import json as _json

                            arg = _json.dumps(inp, ensure_ascii=False)[:400]
                        except Exception:
                            arg = str(inp)[:400]
                        tool_bits.append(f"[tool_use {name}] {arg}")
                    elif bt == "tool_result" and include_tools:
                        tool_bits.append(
                            f"[tool_result] {stringify_content(block.get('content'))[:600]}"
                        )
                    elif bt == "image":
                        text_parts.append("[image]")
            text = strip_ansi("\n".join(p for p in text_parts if p))
            if role == "user":
                text = extract_user_query(text)
                if is_noise_user_text(text) and not tool_bits:
                    continue
            if tool_bits:
                text = (text + "\n" + "\n".join(tool_bits)).strip()
            if not text.strip():
                continue
            # One API response is streamed as several jsonl lines sharing message.id
            # (text block, then tool_use, …): merge them into one message.
            mid = msg.get("id") if role == "assistant" else None
            if mid and messages and messages[-1].meta.get("message_id") == mid:
                messages[-1].text = (messages[-1].text + "\n" + text).strip()
                continue
            messages.append(
                Message(
                    role=role,
                    text=text,
                    timestamp=str(o.get("timestamp") or ""),
                    index=idx,
                    meta={"message_id": mid} if mid else {},
                )
            )
            idx += 1

    if not title:
        for m in messages:
            if m.role == "user":
                cand = clean_title(m.text)
                if cand:
                    title = cand
                    break

    if not session.meta.get("subagent"):
        session.title = title or session.title
        session.session_id = sid or session.session_id
    session.cwd = cwd or session.cwd
    session.message_count = len(messages)
    return Transcript(session=session, messages=messages)
