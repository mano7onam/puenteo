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
from .extract import smart_pull
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
    from pathlib import Path as P

    homes = {
        "claude_code": P.home() / ".claude" / "projects",
        "codex": P.home() / ".codex" / "sessions",
        "grok": P.home() / ".grok" / "sessions",
        "pi": P.home() / ".pi" / "agent" / "sessions",
    }
    providers: Dict[str, Any] = {}
    for name, path in homes.items():
        exists = path.is_dir()
        count = 0
        if exists:
            try:
                count = len(list_sessions(providers=[name], limit=500))
            except Exception:
                count = 0
        providers[name] = {
            "path": str(path),
            "exists": exists,
            "sessions": count,
        }
    return {
        "name": APP_NAME,
        "version": __version__,
        "role": "library+cli",
        "providers": providers,
        "export_formats": list(SUPPORTED_FORMATS),
        "provider_names": list(PROVIDER_NAMES),
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
) -> List[Hit]:
    """Search message text across sessions (or one session)."""
    prov = list(providers) if providers else None
    if session:
        sess = get_session(session, providers=prov, cwd=cwd)
        if not sess:
            return []
        tr = load_transcript(sess)
        return search_transcript(tr, query, limit=limit)
    return search_all(
        query,
        providers=prov,
        cwd=cwd,
        session_limit=session_limit,
        hit_limit=limit,
    )


def pull(
    ref: Union[str, Session],
    *,
    query: Optional[str] = None,
    mode: str = "auto",
    last: int = 0,
    max_chars: int = 12000,
    max_messages: int = 30,
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
    )


def export_session(
    ref: Union[str, Session],
    *,
    fmt: str = "md",
    output: Optional[Union[str, Path]] = None,
    include_tools: bool = False,
    include_thinking: bool = False,
    providers: Optional[Sequence[str]] = None,
    cwd: Optional[str] = None,
) -> Path:
    """
    Export a full transcript to a file.

    ``fmt``: md | txt | html | pdf | json | zip | csv | xml | yaml | all

    Returns the primary written path (or directory when ``fmt=all``).
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

    fmt_arg = (fmt or "md").lower().strip()
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
    "pull",
    "render",
    "resolve_session",
    "search",
    "search_all",
    "search_transcript",
    "smart_pull",
    "status",
]
