"""Cursor IDE chats (Application Support state DBs / composer data)."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..models import Message, Session, Transcript
from ..util import clean_title, cwd_matches, expand, strip_ansi, stringify_content


def _support_roots() -> List[Path]:
    home = Path(expand("~"))
    return [
        home / "Library" / "Application Support" / "Cursor",
        home / ".cursor",
        home / "Library" / "Application Support" / "Cursor Nightly",
    ]


def list_sessions(*, cwd: Optional[str] = None) -> List[Session]:
    out: List[Session] = []
    for root in _support_roots():
        if not root.exists():
            continue
        # workspaceStorage/*/state.vscdb
        ws = root / "User" / "workspaceStorage"
        if ws.is_dir():
            for d in ws.iterdir():
                if not d.is_dir():
                    continue
                db = d / "state.vscdb"
                if not db.is_file():
                    continue
                sess = _session_from_vscdb(db, cwd=cwd)
                if sess:
                    out.append(sess)
        # globalStorage aichat / composer
        gs = root / "User" / "globalStorage"
        if gs.is_dir():
            for db in gs.rglob("state.vscdb"):
                for sess in _sessions_from_global_db(db, cwd=cwd):
                    out.append(sess)
            # also JSON chat dumps some builds leave
            for f in gs.rglob("*.json"):
                name = f.name.lower()
                if any(k in name for k in ("chat", "composer", "aichat", "conversation")):
                    sess = _session_from_json_file(f, cwd=cwd)
                    if sess:
                        out.append(sess)
    # dedupe by path
    by_path = {s.path: s for s in out}
    out = list(by_path.values())
    out.sort(key=lambda s: s.mtime, reverse=True)
    return out


def session_from_path(path: str) -> Optional[Session]:
    path = expand(path)
    p = Path(path)
    if p.suffix == ".vscdb" or p.name == "state.vscdb":
        return _session_from_vscdb(p)
    if p.suffix == ".json":
        return _session_from_json_file(p)
    return None


def load_transcript(session: Session, *, include_tools: bool = False) -> Transcript:
    path = Path(session.path)
    messages: List[Message] = []
    title = session.title
    idx = 0

    if path.suffix == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            messages, title = _messages_from_obj(data, title)
        except Exception:
            pass
    else:
        # vscdb — re-read chat-like values
        try:
            conn = sqlite3.connect(str(path))
            rows = conn.execute("select key, value from ItemTable").fetchall()
            conn.close()
            for key, value in rows:
                if not value:
                    continue
                k = str(key).lower()
                if not any(x in k for x in ("chat", "composer", "aichat", "conversation", "bubble")):
                    continue
                try:
                    if isinstance(value, bytes):
                        value = value.decode("utf-8", "replace")
                    obj = json.loads(value)
                except Exception:
                    continue
                msgs, t2 = _messages_from_obj(obj, title)
                if msgs:
                    messages.extend(msgs)
                    if t2:
                        title = t2
        except Exception:
            pass

    # reindex
    fixed = []
    for i, m in enumerate(messages):
        fixed.append(Message(role=m.role, text=strip_ansi(m.text), timestamp=m.timestamp, index=i, meta=m.meta))
    session.title = title or session.title
    session.message_count = len(fixed)
    return Transcript(session=session, messages=fixed)


def _session_from_vscdb(db: Path, *, cwd: Optional[str] = None) -> Optional[Session]:
    try:
        st = db.stat()
    except OSError:
        return None
    workspace_cwd = _workspace_cwd(db.parent)
    if cwd and workspace_cwd and not cwd_matches(cwd, workspace_cwd):
        return None
    title = f"Cursor {db.parent.name[:8]}"
    n_msgs = 0
    try:
        conn = sqlite3.connect(str(db))
        rows = conn.execute("select key, value from ItemTable").fetchall()
        conn.close()
        for key, value in rows:
            k = str(key).lower()
            if not any(x in k for x in ("chat", "composer", "aichat", "conversation")):
                continue
            try:
                if isinstance(value, bytes):
                    value = value.decode("utf-8", "replace")
                obj = json.loads(value)
                msgs, t2 = _messages_from_obj(obj, title)
                n_msgs += len(msgs)
                if t2 and not title.startswith("Cursor "):
                    pass
                if t2:
                    title = t2
            except Exception:
                continue
    except Exception:
        return None
    if n_msgs == 0 and not workspace_cwd:
        return None
    return Session(
        provider="cursor",
        session_id=db.parent.name[:16],
        path=str(db),
        title=title,
        cwd=workspace_cwd or "",
        mtime=st.st_mtime,
        size=st.st_size,
        message_count=n_msgs,
    )


def _sessions_from_global_db(db: Path, *, cwd: Optional[str] = None) -> List[Session]:
    # one global db may hold many composer sessions; expose as one blob for now
    s = _session_from_vscdb(db, cwd=cwd)
    return [s] if s else []


def _session_from_json_file(path: Path, *, cwd: Optional[str] = None) -> Optional[Session]:
    try:
        st = path.stat()
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    msgs, title = _messages_from_obj(data, path.stem)
    scwd = ""
    if isinstance(data, dict):
        scwd = str(data.get("cwd") or data.get("workspace") or data.get("folder") or "")
    if cwd and scwd and not cwd_matches(cwd, scwd):
        return None
    return Session(
        provider="cursor",
        session_id=path.stem[:32],
        path=str(path),
        title=title or path.stem,
        cwd=scwd,
        mtime=st.st_mtime,
        size=st.st_size,
        message_count=len(msgs),
    )


def _workspace_cwd(workspace_dir: Path) -> str:
    wf = workspace_dir / "workspace.json"
    if not wf.is_file():
        return ""
    try:
        data = json.loads(wf.read_text(encoding="utf-8", errors="replace"))
        folder = data.get("folder") or data.get("workspace")
        if isinstance(folder, str) and folder.startswith("file://"):
            return folder[len("file://") :]
        if isinstance(folder, str):
            return folder
    except Exception:
        pass
    return ""


def _messages_from_obj(obj: Any, title: str) -> Tuple[List[Message], str]:
    msgs: List[Message] = []
    t = title

    def walk(node: Any, depth: int = 0):
        nonlocal t
        if depth > 12:
            return
        if isinstance(node, dict):
            # common cursor shapes
            role = (node.get("role") or node.get("type") or node.get("kind") or "").lower()
            text = node.get("text") or node.get("content") or node.get("bubbleId") and node.get("rawText")
            if text is None and "parts" in node:
                text = stringify_content(node.get("parts"))
            else:
                text = stringify_content(text) if text is not None else ""
            if role in ("user", "human", "assistant", "ai", "system", "bot") and text.strip():
                r = "user" if role in ("user", "human") else ("assistant" if role in ("assistant", "ai", "bot") else role)
                if r == "user" and (not t or t.startswith("Cursor")):
                    cand = clean_title(text)
                    if cand:
                        t = cand
                msgs.append(Message(role=r if r in ("user", "assistant", "system") else "assistant", text=text, index=len(msgs)))
            for v in node.values():
                walk(v, depth + 1)
        elif isinstance(node, list):
            for it in node:
                walk(it, depth + 1)

    walk(obj)
    return msgs, t
