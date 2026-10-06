"""
Public library API for Puenteo.

Prefer importing from the package root::

    import puenteo

    sessions = puenteo.list_sessions(limit=20)
    path = puenteo.export_session(sessions[0].session_id, fmt="md", output="chat.md")
    hits = puenteo.search("gatekeeper")
    pack = puenteo.pull(sessions[0].session_id, query="dmg", mode="query")

CLI is the same package: ``puenteo`` / ``pto`` / ``python -m puenteo``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from .exporters import SUPPORTED_FORMATS, clipboard_text, render
from .extract import build_outline, smart_pull
from .models import Message, Session, Transcript
from .providers import PROVIDER_NAMES, list_sessions, load_transcript, resolve_session
from .rich import (
    find_best_agent_transcript,
    list_sources_for_cwd,
    load_transcript as load_rich_transcript,
)
from .search import Hit, search_all, search_transcript
from .version import APP_NAME, __version__

# Re-export rich models for export consumers (Terminal Dashboard, etc.)
from .rich import Attachment, Message as RichMessage, Transcript as RichTranscript


def status() -> Dict[str, Any]:
    """Discover which agent stores exist and how many sessions each has."""
    import platform

    from .providers import PROVIDER_NAMES, provider_store_status

    return {
        "name": APP_NAME,
        "version": __version__,
        "role": "library+cli",
        "platform": platform.system().lower(),  # darwin | linux | windows
        "providers": provider_store_status(),
        "export_formats": list(SUPPORTED_FORMATS),
        "provider_names": list(PROVIDER_NAMES),
        "session_ref": "full uuid | unique prefix | path | title substring",
        "python_api": {
            "list": "puenteo.list_sessions(cwd='~/proj', limit=20)",
            "search": "puenteo.search('topic', exclude_session='abc')",
            "pull": "puenteo.pull('019f7a24', query='dmg', mode='query', top_k=15)",
            "outline": "puenteo.outline('019f7a24')",
        },
    }


def get_session(
    ref: str,
    *,
    providers: Optional[Sequence[str]] = None,
    cwd: Optional[str] = None,
) -> Optional[Session]:
    """Resolve a session by id prefix, path, or title substring."""
    prov = list(providers) if providers else None
    return resolve_session(ref, providers=prov, cwd=cwd)


def load(
    ref: Union[str, Session],
    *,
    rich: bool = True,
    include_tools: bool = False,
    include_thinking: bool = False,
    providers: Optional[Sequence[str]] = None,
    cwd: Optional[str] = None,
) -> Union[Transcript, RichTranscript]:
    """
    Load a session transcript.

    - ``rich=True`` (default): full export model (tools, attachments) via ``rich`` loaders
    - ``rich=False``: lightweight messages for search / pull
    """
    sess = ref if isinstance(ref, Session) else get_session(ref, providers=providers, cwd=cwd)
    if not sess:
        raise LookupError(f"Session not found: {ref!r}")
    if rich:
        return load_rich_transcript(
            sess.provider,
            sess.path,
            include_tools=include_tools,
            include_thinking=include_thinking,
        )
    return load_transcript(sess, include_tools=include_tools)


def search(
    query: str,
    *,
    session: Optional[str] = None,
    providers: Optional[Sequence[str]] = None,
    cwd: Optional[str] = None,
    limit: int = 20,
    session_limit: int = 40,
    exclude_session: Optional[str] = None,
    exclude_sessions: Optional[Sequence[str]] = None,
) -> List[Hit]:
    """Search message text across sessions (or one session)."""
    prov = list(providers) if providers else None
    if session:
        sess = get_session(session, providers=prov, cwd=cwd)
        if not sess:
            return []
        tr = load_transcript(sess)
        return search_transcript(tr, query, limit=limit)
    excl = list(exclude_sessions) if exclude_sessions else []
    if exclude_session:
        excl.append(exclude_session)
    return search_all(
        query,
        providers=prov,
        cwd=cwd,
        session_limit=session_limit,
        hit_limit=limit,
        exclude_sessions=excl or None,
    )


def pull(
    ref: Union[str, Session],
    *,
    query: Optional[str] = None,
    mode: str = "auto",
    last: int = 0,
    max_chars: int = 12000,
    max_messages: int = 30,
    top_k: int = 0,
    around: Optional[int] = None,
    radius: int = 5,
    include_tools: bool = False,
    providers: Optional[Sequence[str]] = None,
    cwd: Optional[str] = None,
) -> List[Message]:
    """Build a compact message pack for another agent (same as ``puenteo pull``)."""
    sess = ref if isinstance(ref, Session) else get_session(ref, providers=providers, cwd=cwd)
    if not sess:
        raise LookupError(f"Session not found: {ref!r}")
    tr = load_transcript(sess, include_tools=include_tools)
    return smart_pull(
        tr,
        query=query,
        mode=mode,
        last=last,
        max_chars=max_chars,
        max_messages=max_messages,
        top_k=top_k,
        around=around,
        radius=radius,
    )


def outline(
    ref: Union[str, Session],
    *,
    include_tools: bool = False,
    providers: Optional[Sequence[str]] = None,
    cwd: Optional[str] = None,
) -> Dict[str, Any]:
    """Session map: counts, time span, milestones (same as ``puenteo outline``)."""
    sess = ref if isinstance(ref, Session) else get_session(ref, providers=providers, cwd=cwd)
    if not sess:
        raise LookupError(f"Session not found: {ref!r}")
    tr = load_transcript(sess, include_tools=include_tools)
    return build_outline(tr)


def export_session(
    ref: Union[str, Session],
    *,
    fmt: str = "md",
    output: Optional[Union[str, Path]] = None,
    include_tools: bool = False,
    include_thinking: bool = False,
    providers: Optional[Sequence[str]] = None,
    cwd: Optional[str] = None,
    query: Optional[str] = None,
    mode: str = "auto",
    max_chars: int = 12000,
    max_messages: int = 30,
    top_k: int = 0,
    around: Optional[int] = None,
    radius: int = 5,
) -> Path:
    """
    Export a full transcript (or a relevance slice) to a file.

    ``fmt``: md | txt | html | pdf | json | zip | csv | xml | yaml | all

    When ``query`` / ``around`` / non-auto ``mode`` is set and ``fmt`` is
    md|txt|json, writes a compact pack (same idea as ``puenteo export --query``).

    Returns the primary written path (or directory when ``fmt=all``).
    """
    from . import format as fmtmod

    sess = ref if isinstance(ref, Session) else get_session(ref, providers=providers, cwd=cwd)
    if not sess:
        raise LookupError(f"Session not found: {ref!r}")

    fmt_arg = (fmt or "md").lower().strip()
    want_slice = bool(query or around is not None or (mode and mode != "auto"))
    if want_slice and fmt_arg in ("md", "txt", "json"):
        tr = load_transcript(sess, include_tools=include_tools)
        pull_mode = mode or "auto"
        if around is not None and pull_mode == "auto":
            pull_mode = "around"
        if query and pull_mode == "auto":
            pull_mode = "query"
        msgs = smart_pull(
            tr,
            query=query,
            mode=pull_mode,
            max_chars=max_chars,
            max_messages=max_messages,
            top_k=top_k,
            around=around,
            radius=radius,
        )
        text = fmtmod.format_pack(
            sess,
            msgs,
            purpose=f"export:{pull_mode}",
            json_mode=(fmt_arg == "json"),
            max_chars=0,
        )
        data = text.encode("utf-8")
        if output:
            path = Path(os.path.expanduser(str(output))).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            path = Path.cwd() / f"puenteo-slice-{sess.session_id[:8]}.{fmt_arg}"
        path.write_bytes(data)
        return path

    rich_tr = load_rich_transcript(
        sess.provider,
        sess.path,
        include_tools=include_tools,
        include_thinking=include_thinking,
    )

    formats = list(SUPPORTED_FORMATS) if fmt_arg == "all" else [fmt_arg]
    out = Path(os.path.expanduser(str(output))).resolve() if output else None

    written: List[Path] = []
    for f in formats:
        data, _media, filename = render(
            rich_tr,
            f,
            include_tools=include_tools,
            include_thinking=include_thinking,
        )
        if out is None:
            path = Path.cwd() / filename
        elif out.is_dir() or fmt_arg == "all" or (out.suffix == "" and len(formats) > 1):
            out.mkdir(parents=True, exist_ok=True)
            path = out / filename if out.is_dir() or out.suffix == "" else out
            if out.suffix == "" and not out.is_dir():
                # treat as directory path to create
                out.mkdir(parents=True, exist_ok=True)
                path = out / filename
        else:
            path = out
            path.parent.mkdir(parents=True, exist_ok=True)

        path.write_bytes(data)
        written.append(path)

    if not written:
        raise RuntimeError("Nothing exported")
    return written[0] if len(written) == 1 else (out if out and out.is_dir() else written[0].parent)


def export_bytes(
    ref: Union[str, Session],
    *,
    fmt: str = "md",
    include_tools: bool = False,
    include_thinking: bool = False,
    providers: Optional[Sequence[str]] = None,
    cwd: Optional[str] = None,
) -> tuple:
    """
    Export without writing a file.

    Returns ``(data: bytes, media_type: str, filename: str)``.
    """
    sess = ref if isinstance(ref, Session) else get_session(ref, providers=providers, cwd=cwd)
    if not sess:
        raise LookupError(f"Session not found: {ref!r}")
    rich_tr = load_rich_transcript(
        sess.provider,
        sess.path,
        include_tools=include_tools,
        include_thinking=include_thinking,
    )
    return render(
        rich_tr,
        fmt,
        include_tools=include_tools,
        include_thinking=include_thinking,
    )


# --------------------------------------------------------------------------- live sessions & bus


def ps(*, cwd: Optional[str] = None, agents: Optional[Sequence[str]] = None) -> list:
    """Agent sessions running right now (any vendor). Same as ``puenteo ps``."""
    from .live import live_sessions

    return live_sessions(agents=list(agents) if agents else None, cwd=cwd)


def whoami():
    """The agent session this process runs inside (``Me``) or None."""
    from .live import whoami as _w

    return _w()


def _me(sender: Optional[str]) -> str:
    if sender:
        return sender
    me = whoami()
    if me:
        return me.address
    return "user:" + (os.environ.get("USER") or os.environ.get("USERNAME") or "python")


def send(to: str, text: str, *, sender: Optional[str] = None, thread: str = "", wait: float = 0, push: bool = True):
    """
    Message live sessions: ``to`` = ``agent:id`` | ``@name`` | ``#channel`` | ``agent:codex`` | ``cwd:.`` | ``*``.

    Returns the sent ``BusMessage``; with ``wait`` > 0 returns ``(message, replies)``.
    """
    from .bus import Bus
    from .deliver import push_pending

    me = _me(sender)
    with Bus() as b:
        msg = b.send(me, to, text, thread=thread)
        if push:
            msg.meta["delivery"] = push_pending(b, msg)
        if wait:
            return msg, b.wait(me, timeout=wait, thread=msg.thread)
        return msg


def inbox(*, address: Optional[str] = None, unread_only: bool = True, mark_read: bool = True) -> list:
    """Messages other sessions sent to ``address`` (default: this session)."""
    from .bus import Bus

    with Bus() as b:
        return b.inbox(_me(address), unread_only=unread_only, mark_read=mark_read)


def reply(msg_id: str, text: str, *, sender: Optional[str] = None):
    from .bus import Bus
    from .deliver import push_pending

    with Bus() as b:
        msg = b.send(_me(sender), "", text, reply_to=msg_id)
        msg.meta["delivery"] = push_pending(b, msg)
        return msg


def listen(*, address: Optional[str] = None, timeout: Optional[float] = None, mark_read: bool = True):
    """
    Generator of incoming messages, delivered as they arrive (doorbell-driven)::

        for msg in puenteo.listen():
            print(msg.sender, msg.body)
            puenteo.reply(msg.id, "on it")
    """
    import time as _t

    from .bus import Bus
    from .notify import Bell

    me = _me(address)
    deadline = _t.time() + timeout if timeout else None
    with Bus() as b, Bell(me) as bell:
        while True:
            for m in b.inbox(me, unread_only=True, mark_read=mark_read):
                yield m
            if deadline and _t.time() >= deadline:
                return
            bell.wait(5.0 if not deadline else max(0.0, min(5.0, deadline - _t.time())))


def handoff(ref: Union[str, Session], *, query: Optional[str] = None, max_chars: int = 12000) -> str:
    """Structured markdown handoff of a session (goal, state, plan, files, commits, problems, tail)."""
    from .extract import handoff_brief

    sess = ref if isinstance(ref, Session) else get_session(ref)
    if not sess:
        raise LookupError(f"Session not found: {ref!r}")
    return handoff_brief(load_transcript(sess), query=query, max_chars=max_chars)


__all__ = [
    "APP_NAME",
    "SUPPORTED_FORMATS",
    "Attachment",
    "Hit",
    "Message",
    "PROVIDER_NAMES",
    "RichMessage",
    "RichTranscript",
    "Session",
    "Transcript",
    "__version__",
    "build_outline",
    "clipboard_text",
    "export_bytes",
    "export_session",
    "find_best_agent_transcript",
    "get_session",
    "list_sessions",
    "list_sources_for_cwd",
    "load",
    "load_rich_transcript",
    "load_transcript",
    "outline",
    "pull",
    "render",
    "resolve_session",
    "search",
    "search_all",
    "search_transcript",
    "smart_pull",
    "status",
    "ps",
    "whoami",
    "send",
    "inbox",
    "reply",
    "handoff",
    "listen",
]
