"""
Agent Session Bridge — **Python library + CLI** for local agent sessions.

Install
-------
::

    pip install -e .
    # CLI:
    asb list
    asb export <session_id> -f md -o chat.md

Library
-------
::

    import agent_session_bridge as asb

    print(asb.status())
    for s in asb.list_sessions(limit=10):
        print(s.provider, s.session_id[:8], s.title)

    asb.export_session("019f7a24", fmt="pdf", output="chat.pdf")
    hits = asb.search("export markdown")
    msgs = asb.pull("019f7a24", query="dmg", mode="query")

Providers: Claude Code, Codex, Grok, Pi.
Formats: md, txt, html, pdf, json, zip, csv, xml, yaml.
"""

from .api import (  # noqa: F401
    APP_NAME,
    SUPPORTED_FORMATS,
    Attachment,
    Hit,
    Message,
    PROVIDER_NAMES,
    RichMessage,
    RichTranscript,
    Session,
    Transcript,
    clipboard_text,
    export_bytes,
    export_session,
    find_best_agent_transcript,
    get_session,
    list_sessions,
    list_sources_for_cwd,
    load,
    load_rich_transcript,
    load_transcript,
    pull,
    render,
    resolve_session,
    search,
    search_all,
    search_transcript,
    smart_pull,
    status,
)
from .version import __version__

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
