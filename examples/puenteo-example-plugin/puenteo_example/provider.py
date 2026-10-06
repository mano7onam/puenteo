"""Provider for a toy agent that stores chats as ``~/.notes-agent/<id>.jsonl`` lines ``{"role", "text"}``."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

from puenteo.models import Message, Session, Transcript
from puenteo.util import cwd_matches

PUENTEO_API = 1  # plugin contract version this module implements


def _root() -> Path:
    return Path(os.path.expanduser(os.environ.get("NOTES_AGENT_HOME", "~/.notes-agent")))


def list_sessions(*, cwd: Optional[str] = None) -> List[Session]:
    out = []
    for f in sorted(_root().glob("*.jsonl")):
        meta = {}
        first = f.open(encoding="utf-8").readline()
        try:
            meta = json.loads(first).get("meta", {})
        except Exception:
            pass
        if cwd and not cwd_matches(cwd, meta.get("cwd", "")):
            continue
        st = f.stat()
        out.append(Session(provider="notes", session_id=f.stem, path=str(f), title=meta.get("title", f.stem),
                           cwd=meta.get("cwd", ""), mtime=st.st_mtime, size=st.st_size))
    return out


def session_from_path(path: str) -> Optional[Session]:
    return next((s for s in list_sessions() if s.path == os.path.abspath(path)), None)


def load_transcript(session: Session, *, include_tools: bool = False) -> Transcript:
    msgs = []
    for line in open(session.path, encoding="utf-8"):
        o = json.loads(line)
        if "role" in o:
            msgs.append(Message(role=o["role"], text=o.get("text", ""), index=len(msgs)))
    return Transcript(session=session, messages=msgs)


def live_sessions():
    """Report a 'running' notes agent if it left a pid file."""
    from puenteo.live import LiveSession, pid_alive

    p = _root() / "running.pid"
    if p.exists():
        pid = int(p.read_text(encoding="utf-8").split()[0])
        if pid_alive(pid):
            return [LiveSession(agent="notes", session_id=f"pid{pid}", pid=pid, source=str(p), delivery=["bus", "file"])]
    return []
