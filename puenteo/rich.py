"""Rich agent transcripts for export + reuse by Terminal Dashboard and puenteo CLI.

This is the **shared library** surface for full conversation export (tools,
thinking, attachments). The lightweight `providers/` package is used for fast
list/search in the CLI; both live in `puenteo`.
"""

from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .util import normalize_path


@dataclass
class Attachment:
    kind: str  # image | file | text | binary
    name: str
    media_type: str = ""
    data_base64: str = ""
    path: str = ""
    text: str = ""
    size: int = 0


@dataclass
class Message:
    role: str  # user | assistant | system | tool | developer
    text: str = ""
    timestamp: str = ""
    thinking: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tool_results: List[Dict[str, Any]] = field(default_factory=list)
    attachments: List[Attachment] = field(default_factory=list)
    raw_type: str = ""


@dataclass
class Transcript:
    source: str  # claude_code | codex | scrollback
    title: str
    cwd: str
    session_id: str
    path: str  # source file
    messages: List[Message] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def message_count(self) -> int:
        return len(self.messages)

    @property
    def image_count(self) -> int:
        return sum(1 for m in self.messages for a in m.attachments if a.kind == "image")


def claude_project_dir_for_path(path: str) -> Optional[str]:
    """Map filesystem path → ~/.claude/projects/<encoded> (longest prefix match)."""
    path = normalize_path(path) or path
    if not path or path == "Unknown/Remote":
        return None
    root = os.path.expanduser("~/.claude/projects")
    if not os.path.isdir(root):
        return None

    def encode(p: str) -> str:
        return p.replace("/", "-").replace(".", "-")

    candidates = []
    # Exact and parent prefixes
    cur = path
    while cur and cur != "/":
        enc = encode(cur)
        full = os.path.join(root, enc)
        if os.path.isdir(full):
            candidates.append((len(cur), full))
        cur = os.path.dirname(cur)
    if not candidates:
        # Also try bare encode without requiring walk (symlink cases)
        enc = encode(path)
        full = os.path.join(root, enc)
        if os.path.isdir(full):
            return full
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def list_claude_sessions(cwd: str) -> List[Dict[str, Any]]:
    proj = claude_project_dir_for_path(cwd)
    if not proj:
        return []
    out = []
    for f in glob.glob(os.path.join(proj, "*.jsonl")):
        sid = os.path.splitext(os.path.basename(f))[0]
        # skip subagent-only weird names
        if not re.match(r"^[0-9a-f-]{8,}$", sid, re.I):
            # still allow uuid-like
            pass
        mtime = os.path.getmtime(f)
        size = os.path.getsize(f)
        title = _peek_claude_title(f) or sid[:8]
        out.append({
            "provider": "claude_code",
            "session_id": sid,
            "path": f,
            "title": title,
            "mtime": mtime,
            "size": size,
            "cwd": cwd,
        })
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def list_codex_sessions(cwd: str, limit: int = 40) -> List[Dict[str, Any]]:
    root = os.path.expanduser("~/.codex/sessions")
    if not os.path.isdir(root):
        return []
    cwd_n = normalize_path(cwd) or cwd
    found = []
    # Walk recent rollouts; match by cwd in session_meta
    files = sorted(
        glob.glob(os.path.join(root, "**", "rollout-*.jsonl"), recursive=True),
        key=os.path.getmtime,
        reverse=True,
    )
    for f in files[:200]:
        meta = _peek_codex_meta(f)
        if not meta:
            continue
        scwd = normalize_path(meta.get("cwd") or "") or meta.get("cwd")
        if not scwd:
            continue
        # Match exact or parent/child relationship
        if not _paths_related(cwd_n, scwd):
            continue
        found.append({
            "provider": "codex",
            "session_id": meta.get("session_id") or os.path.basename(f),
            "path": f,
            "title": meta.get("title") or f"Codex {meta.get('session_id', '')[:8]}",
            "mtime": os.path.getmtime(f),
            "size": os.path.getsize(f),
            "cwd": scwd,
        })
        if len(found) >= limit:
            break
    return found


def _paths_related(a: str, b: str) -> bool:
    if not a or not b:
        return False
    a = a.rstrip("/")
    b = b.rstrip("/")
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _peek_claude_title(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i > 80:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") == "ai-title" and o.get("title"):
                    return str(o["title"])[:120]
                if o.get("type") == "user":
                    text = _extract_text(o.get("message", {}).get("content"))
                    if text:
                        return text.strip().splitlines()[0][:100]
    except Exception:
        pass
    return None


def _peek_codex_meta(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i > 30:
                    break
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") == "session_meta":
                    p = o.get("payload") or {}
                    return {
                        "session_id": p.get("session_id") or p.get("id"),
                        "cwd": p.get("cwd"),
                        "title": None,
                    }
    except Exception:
        pass
    return None


def load_transcript(
    provider: str,
    path: str,
    *,
    include_tools: bool = True,
    include_thinking: bool = False,
) -> Transcript:
    if provider in ("claude_code", "claude"):
        return _load_claude(path, include_tools=include_tools, include_thinking=include_thinking)
    if provider == "codex":
        return _load_codex(path, include_tools=include_tools, include_thinking=include_thinking)
    if provider == "grok":
        return _load_grok(path, include_tools=include_tools, include_thinking=include_thinking)
    if provider == "pi":
        return _load_pi(path, include_tools=include_tools, include_thinking=include_thinking)
    raise ValueError(f"Unknown provider: {provider}")


def find_best_agent_transcript(
    cwd: str,
    *,
    include_tools: bool = True,
    include_thinking: bool = False,
    prefer_title: Optional[str] = None,
) -> Optional[Transcript]:
    """
    Pick a Claude/Codex session for this cwd.

    If prefer_title is set (terminal window title), rank by title similarity first
    so two Ghostty windows in the same folder don't share the wrong chat.
    """
    candidates = (
        list_claude_sessions(cwd)
        + list_codex_sessions(cwd)
        + list_grok_sessions(cwd)
        + list_pi_sessions(cwd)
    )
    if not candidates:
        return None

    if prefer_title:
        # Lazy import to avoid circular deps at module load
        try:
            from ..preview import _title_score, AGENT_TITLE_MIN_SCORE
            ranked = sorted(
                candidates,
                key=lambda c: (
                    _title_score(prefer_title, c.get("title") or ""),
                    c.get("mtime") or 0,
                ),
                reverse=True,
            )
            best = ranked[0]
            if _title_score(prefer_title, best.get("title") or "") < AGENT_TITLE_MIN_SCORE:
                # Ambiguous — still export newest rather than nothing for export flow
                candidates.sort(key=lambda x: x["mtime"], reverse=True)
                best = candidates[0]
            else:
                best = best
        except Exception:
            candidates.sort(key=lambda x: x["mtime"], reverse=True)
            best = candidates[0]
    else:
        candidates.sort(key=lambda x: x["mtime"], reverse=True)
        best = candidates[0]

    return load_transcript(
        best["provider"],
        best["path"],
        include_tools=include_tools,
        include_thinking=include_thinking,
    )


def list_sources_for_cwd(cwd: str) -> List[Dict[str, Any]]:
    """Claude + Codex + Grok + Pi sessions related to this project folder."""
    out = (
        list_claude_sessions(cwd)
        + list_codex_sessions(cwd)
        + list_grok_sessions(cwd)
        + list_pi_sessions(cwd)
    )
    out.sort(key=lambda x: x.get("mtime") or 0, reverse=True)
    return out


def _sessions_from_provider(mod, provider: str, cwd: Optional[str], limit: int) -> List[Dict[str, Any]]:
    try:
        sessions = mod.list_sessions(cwd=cwd)
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for s in sessions[:limit]:
        out.append({
            "provider": provider,
            "session_id": s.session_id,
            "path": s.path,
            "title": s.title,
            "mtime": s.mtime,
            "size": s.size,
            "cwd": s.cwd,
        })
    return out


def list_grok_sessions(cwd: str, limit: int = 40) -> List[Dict[str, Any]]:
    """List Grok sessions whose workspace cwd matches."""
    try:
        from .providers import grok as grok_mod
    except Exception:
        return []
    return _sessions_from_provider(grok_mod, "grok", cwd, limit)


def list_pi_sessions(cwd: str, limit: int = 40) -> List[Dict[str, Any]]:
    """List Pi agent sessions related to this cwd."""
    try:
        from .providers import pi as pi_mod
    except Exception:
        return []
    return _sessions_from_provider(pi_mod, "pi", cwd, limit)


# ---------- Claude ----------

def _load_claude(path: str, *, include_tools: bool, include_thinking: bool) -> Transcript:
    messages: List[Message] = []
    title = ""
    cwd = ""
    session_id = os.path.splitext(os.path.basename(path))[0]
    paste_cache = os.path.expanduser("~/.claude/paste-cache")

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
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
                title = str(o["title"])
                continue
            if t not in ("user", "assistant", "system"):
                continue
            if o.get("cwd") and not cwd:
                cwd = o["cwd"]
            if o.get("sessionId"):
                session_id = o["sessionId"]

            msg = o.get("message") or {}
            role = msg.get("role") or t
            content = msg.get("content")
            text_parts: List[str] = []
            thinking = ""
            tool_calls = []
            tool_results = []
            attachments: List[Attachment] = []

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
                    elif bt == "tool_use":
                        if include_tools:
                            tool_calls.append({
                                "id": block.get("id"),
                                "name": block.get("name"),
                                "input": block.get("input"),
                            })
                    elif bt == "tool_result":
                        if include_tools:
                            tool_results.append({
                                "tool_use_id": block.get("tool_use_id"),
                                "content": _stringify_content(block.get("content")),
                                "is_error": block.get("is_error"),
                            })
                    elif bt == "image":
                        att = _image_from_block(block)
                        if att:
                            attachments.append(att)
                    elif bt in ("document", "file"):
                        att = _file_from_block(block)
                        if att:
                            attachments.append(att)
                    else:
                        # unknown block — keep text dump
                        if bt and bt not in ("server_tool_use",):
                            text_parts.append(f"[{bt}] {_stringify_content(block)[:500]}")

            # Resolve paste-cache placeholders like [Pasted text #1] if present in text
            text = "\n".join(p for p in text_parts if p)
            text, extra_atts = _expand_paste_refs(text, paste_cache)
            attachments.extend(extra_atts)

            if not include_thinking:
                thinking = ""

            # Skip empty tool-only assistant turns when tools excluded
            if not text and not attachments and not tool_calls and not tool_results and not thinking:
                continue

            # Prefer first real user line as title when no ai-title
            if not title and role == "user" and text.strip():
                title = text.strip().splitlines()[0][:120]

            messages.append(Message(
                role=role,
                text=text,
                timestamp=o.get("timestamp") or "",
                thinking=thinking,
                tool_calls=tool_calls,
                tool_results=tool_results,
                attachments=attachments,
                raw_type=t,
            ))

    if not title:
        title = session_id[:8]

    return Transcript(
        source="claude_code",
        title=title,
        cwd=cwd,
        session_id=session_id,
        path=path,
        messages=messages,
        meta={"provider": "Claude Code", "file": path},
    )


def _image_from_block(block: Dict[str, Any]) -> Optional[Attachment]:
    src = block.get("source") or {}
    if not isinstance(src, dict):
        return None
    data = src.get("data") or ""
    media = src.get("media_type") or block.get("media_type") or "image/png"
    if not data and src.get("type") == "url":
        return Attachment(kind="image", name="image", media_type=media, path=src.get("url") or "")
    if not data:
        return None
    ext = "png"
    if "jpeg" in media or "jpg" in media:
        ext = "jpg"
    elif "gif" in media:
        ext = "gif"
    elif "webp" in media:
        ext = "webp"
    return Attachment(
        kind="image",
        name=f"image.{ext}",
        media_type=media,
        data_base64=data,
        size=len(data),
    )


def _file_from_block(block: Dict[str, Any]) -> Optional[Attachment]:
    name = block.get("name") or block.get("filename") or "file"
    media = block.get("media_type") or block.get("mime_type") or "application/octet-stream"
    src = block.get("source") or {}
    data = ""
    path = block.get("path") or ""
    if isinstance(src, dict):
        data = src.get("data") or ""
        path = path or src.get("path") or ""
    return Attachment(
        kind="file",
        name=str(name),
        media_type=media,
        data_base64=data or "",
        path=path,
        text=block.get("text") or "",
    )


def _expand_paste_refs(text: str, paste_cache: str) -> Tuple[str, List[Attachment]]:
    """If Claude stored huge pastes separately, try to inline from paste-cache by hash mentions."""
    # Currently paste-cache is content-addressed; without explicit refs we leave text as-is.
    return text, []


# ---------- Codex ----------

def _load_codex(path: str, *, include_tools: bool, include_thinking: bool) -> Transcript:
    messages: List[Message] = []
    title = os.path.basename(path)
    cwd = ""
    session_id = ""
    attachments_root = os.path.expanduser("~/.codex/attachments")

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
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
            ts = o.get("timestamp") or ""

            if t == "session_meta":
                session_id = payload.get("session_id") or payload.get("id") or session_id
                cwd = payload.get("cwd") or cwd
                continue

            if t == "response_item":
                ptype = payload.get("type")
                if ptype == "message":
                    role = payload.get("role") or "assistant"
                    # Skip noisy system/developer dumps by default (keep user+assistant)
                    if role in ("developer", "system"):
                        # Keep short system notes only
                        text = _codex_content_text(payload.get("content"))
                        if len(text) > 2000:
                            continue
                    text, atts, thinking = _codex_parse_content(
                        payload.get("content"), attachments_root
                    )
                    if not include_thinking:
                        thinking = ""
                    if text or atts or thinking:
                        messages.append(Message(
                            role=role if role != "developer" else "system",
                            text=text,
                            timestamp=ts,
                            thinking=thinking,
                            attachments=atts,
                            raw_type=t,
                        ))
                elif ptype in ("function_call", "tool_call", "custom_tool_call") and include_tools:
                    name = payload.get("name") or payload.get("tool_name") or "tool"
                    args = payload.get("arguments") or payload.get("input") or payload.get("params")
                    messages.append(Message(
                        role="assistant",
                        text="",
                        timestamp=ts,
                        tool_calls=[{"name": name, "input": args, "id": payload.get("id")}],
                        raw_type=ptype,
                    ))
                elif ptype in ("function_call_output", "tool_result", "custom_tool_call_output") and include_tools:
                    out = payload.get("output") or payload.get("content") or payload.get("result")
                    messages.append(Message(
                        role="tool",
                        text="",
                        timestamp=ts,
                        tool_results=[{
                            "tool_use_id": payload.get("call_id") or payload.get("id"),
                            "content": _stringify_content(out),
                        }],
                        raw_type=ptype,
                    ))

            elif t == "event_msg":
                et = payload.get("type")
                if et in ("user_message", "agent_message"):
                    role = "user" if et == "user_message" else "assistant"
                    text = payload.get("message") or payload.get("text") or ""
                    if isinstance(text, dict):
                        text = _stringify_content(text)
                    if text:
                        messages.append(Message(role=role, text=str(text), timestamp=ts, raw_type=et))
                elif et == "agent_reasoning" and include_thinking:
                    text = payload.get("text") or payload.get("content") or ""
                    if text:
                        messages.append(Message(role="assistant", thinking=str(text), timestamp=ts, raw_type=et))

    if not title or title.startswith("rollout-"):
        # first user line
        for m in messages:
            if m.role == "user" and m.text.strip():
                title = m.text.strip().splitlines()[0][:100]
                break

    return Transcript(
        source="codex",
        title=title,
        cwd=cwd,
        session_id=session_id or os.path.basename(path),
        path=path,
        messages=messages,
        meta={"provider": "Codex", "file": path},
    )


def _codex_content_text(content) -> str:
    text, _, _ = _codex_parse_content(content, "")
    return text


def _codex_parse_content(content, attachments_root: str) -> Tuple[str, List[Attachment], str]:
    texts: List[str] = []
    atts: List[Attachment] = []
    thinking = ""
    if content is None:
        return "", [], ""
    if isinstance(content, str):
        return content, [], ""
    if not isinstance(content, list):
        return _stringify_content(content), [], ""

    for block in content:
        if isinstance(block, str):
            texts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        bt = block.get("type") or ""
        if bt in ("input_text", "output_text", "text"):
            texts.append(block.get("text") or "")
        elif bt in ("input_image", "output_image", "image"):
            # image_url or base64
            url = block.get("image_url") or block.get("url") or ""
            if isinstance(url, dict):
                url = url.get("url") or ""
            data = block.get("data") or ""
            media = block.get("mime_type") or block.get("media_type") or "image/png"
            if isinstance(url, str) and url.startswith("data:"):
                # data:image/png;base64,....
                try:
                    header, b64 = url.split(",", 1)
                    media = header.split(";")[0].replace("data:", "") or media
                    data = b64
                except Exception:
                    pass
            if data:
                atts.append(Attachment(kind="image", name="image.png", media_type=media, data_base64=data, size=len(data)))
            elif url:
                atts.append(Attachment(kind="image", name=os.path.basename(url) or "image", path=url, media_type=media))
        elif bt in ("reasoning", "thought"):
            thinking = block.get("text") or block.get("content") or thinking
        elif bt in ("input_file", "file"):
            name = block.get("filename") or block.get("name") or "file"
            path = block.get("path") or ""
            # Try codex attachments folder
            if not path and attachments_root and os.path.isdir(attachments_root):
                # best-effort by name
                pass
            atts.append(Attachment(kind="file", name=str(name), path=path, text=block.get("text") or ""))
        else:
            if block.get("text"):
                texts.append(str(block.get("text")))
    return "\n".join(t for t in texts if t), atts, thinking


def _load_from_light_provider(
    provider: str,
    path: str,
    *,
    include_tools: bool,
    include_thinking: bool,
    display_name: str,
) -> Transcript:
    from .models import Session as LightSession
    from .providers import PROVIDERS

    mod = PROVIDERS.get(provider)
    if not mod:
        raise ValueError(f"Unknown provider: {provider}")
    sess = mod.session_from_path(path)
    if not sess:
        sess = LightSession(provider=provider, session_id=os.path.basename(path), path=path)
    light = mod.load_transcript(sess, include_tools=include_tools)
    messages: List[Message] = []
    for m in light.messages:
        messages.append(
            Message(
                role=m.role,
                text=m.text,
                timestamp=m.timestamp,
                thinking="" if not include_thinking else (m.meta.get("thinking") or ""),
                raw_type=m.role,
            )
        )
    return Transcript(
        source=provider,
        title=light.session.title or sess.title,
        cwd=light.session.cwd or sess.cwd,
        session_id=light.session.session_id or sess.session_id,
        path=light.session.path or path,
        messages=messages,
        meta={"provider": display_name, "file": light.session.path or path},
    )


def _load_grok(path: str, *, include_tools: bool, include_thinking: bool) -> Transcript:
    """Load Grok chat_history.jsonl (or session dir) into a rich Transcript."""
    return _load_from_light_provider(
        "grok", path, include_tools=include_tools, include_thinking=include_thinking, display_name="Grok"
    )


def _load_pi(path: str, *, include_tools: bool, include_thinking: bool) -> Transcript:
    return _load_from_light_provider(
        "pi", path, include_tools=include_tools, include_thinking=include_thinking, display_name="Pi"
    )


def _extract_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, str):
                parts.append(b)
            elif isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text") or "")
        return "\n".join(parts)
    return str(content)


def _stringify_content(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, str):
                parts.append(b)
            elif isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text") or "")
                else:
                    parts.append(json.dumps(b, ensure_ascii=False)[:2000])
            else:
                parts.append(str(b))
        return "\n".join(parts)
    try:
        return json.dumps(content, ensure_ascii=False, indent=2)
    except Exception:
        return str(content)


def transcript_from_scrollback(text: str, session: Dict[str, Any], source: str) -> Transcript:
    return Transcript(
        source="scrollback",
        title=session.get("title") or f"{session.get('app')} scrollback",
        cwd=session.get("cwd") or "",
        session_id=session.get("uid") or "",
        path="",
        messages=[Message(role="terminal", text=text, raw_type="scrollback")],
        meta={
            "provider": "Terminal scrollback",
            "app": session.get("app"),
            "capture_source": source,
            "tty": session.get("tty"),
        },
    )


def format_ts(ts: str) -> str:
    if not ts:
        return ""
    try:
        # 2026-07-01T16:04:23.493Z
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts
