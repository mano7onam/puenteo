"""GitHub Copilot CLI sessions (``~/.copilot/session-state/<uuid>/``).

Each session dir has ``workspace.yaml`` (id, cwd, branch, name, timestamps) and
``events.jsonl`` with typed events: ``user.message``, ``assistant.message``,
``tool.execution_start|complete``, ``session.*``. Thousands of empty dirs are
normal (one per launch); sessions without a user message are skipped.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from ..models import Message, Session, Transcript
from ..util import clean_title, cwd_matches, expand, strip_ansi, stringify_content


def _root() -> Path:
    base = os.environ.get("COPILOT_HOME") or expand("~/.copilot")
    return Path(base) / "session-state"


def _yaml(path: Path) -> Dict[str, str]:
    """Flat ``key: value`` YAML (all workspace.yaml needs); no dependency."""
    out: Dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if ":" in line and not line.startswith((" ", "\t", "#")):
                k, v = line.split(":", 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                    v = v[1:-1]
                out[k.strip()] = v
    except Exception:
        pass
    return out


def _peek(events: Path) -> Dict[str, object]:
    users = 0
    first = ""
    try:
        with open(events, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"user.message"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") == "user.message":
                    users += 1
                    if not first:
                        first = clean_title(_user_text(o.get("data") or {}))
    except Exception:
        pass
    return {"users": users, "first": first}


def _user_text(d: dict) -> str:
    t = d.get("content") or d.get("transformedContent") or ""
    t = stringify_content(t)
    # Copilot appends <system_notification> blocks to the prompt
    return t.split("<system_notification>", 1)[0].strip()


def list_sessions(*, cwd: Optional[str] = None) -> List[Session]:
    from ..metacache import cached

    root = _root()
    if not root.is_dir():
        return []
    out: List[Session] = []
    for d in root.iterdir():
        ev = d / "events.jsonl"
        try:
            st = ev.stat()
        except OSError:
            continue
        if st.st_size < 200:
            continue
        ws = d / "workspace.yaml"
        meta = cached("copilot.ws", str(ws), lambda: _yaml(ws), stat_path=str(ev))
        scwd = meta.get("cwd") or ""
        if cwd and not cwd_matches(cwd, scwd):
            continue
        peek = cached("copilot.peek", str(ev), lambda: _peek(ev))
        if not peek.get("users"):
            continue
        name = meta.get("name") or ""
        title = clean_title(name) if name and meta.get("user_named") == "true" else ""
        title = title or str(peek.get("first") or "") or clean_title(name)
        out.append(
            Session(
                provider="copilot",
                session_id=meta.get("id") or d.name,
                path=str(ev),
                title=title or f"Copilot {d.name[:8]}",
                cwd=scwd,
                mtime=st.st_mtime,
                size=st.st_size,
                meta={k: v for k, v in (("branch", meta.get("branch")), ("client", meta.get("client_name"))) if v},
            )
        )
    return out


def session_from_path(path: str) -> Optional[Session]:
    p = Path(expand(path))
    if p.is_dir():
        p = p / "events.jsonl"
    if not p.is_file() or "session-state" not in p.parts:
        return None
    sid = p.parent.name
    for s in list_sessions():
        if s.session_id == sid or s.path == str(p):
            return s
    return None


def load_transcript(session: Session, *, include_tools: bool = False) -> Transcript:
    messages: List[Message] = []
    idx = 0
    try:
        fh = open(session.path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return Transcript(session=session, messages=[])
    with fh:
        for line in fh:
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o.get("type")
            d = o.get("data") or {}
            ts = str(o.get("timestamp") or "")
            text = ""
            role = ""
            if t == "user.message":
                role, text = "user", _user_text(d)
            elif t == "assistant.message":
                role, text = "assistant", stringify_content(d.get("content") or "")
                if include_tools and d.get("toolRequests"):
                    names = [str(r.get("name") or r.get("toolName") or "tool") for r in d["toolRequests"] if isinstance(r, dict)]
                    text = (text + "\n" + " ".join(f"[tool_use {n}]" for n in names)).strip()
            elif t == "tool.execution_start" and include_tools:
                role = "assistant"
                text = f"[tool_use {d.get('toolName')}] {json.dumps(d.get('arguments'), ensure_ascii=False)[:400]}"
            elif t == "tool.execution_complete" and include_tools:
                role = "tool"
                text = f"[tool_result] {stringify_content(d.get('result'))[:600]}"
            elif t == "session.error":
                role, text = "system", f"[error] {d.get('message') or d.get('errorType') or ''}"
            text = strip_ansi(text or "").strip()
            if not role or not text:
                continue
            messages.append(Message(role=role, text=text, timestamp=ts, index=idx))
            idx += 1
    session.message_count = len(messages)
    return Transcript(session=session, messages=messages)
