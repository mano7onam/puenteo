"""Block Goose agent sessions (~/.config/goose or ~/.local/share/goose)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

from ..models import Message, Session, Transcript
from ..util import clean_title, cwd_matches, expand, strip_ansi, stringify_content


def _roots() -> List[Path]:
    import sys

    home = Path(expand("~"))
    roots = [
        home / ".config" / "goose",
        home / ".local" / "share" / "goose",
        home / ".goose",
    ]
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or str(home / "AppData" / "Roaming")
        local = os.environ.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
        roots = [
            Path(appdata) / "goose",
            Path(local) / "goose",
            home / ".goose",
            home / ".config" / "goose",
        ]
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        if xdg:
            roots.insert(0, Path(xdg) / "goose")
    # dedupe
    seen = set()
    out = []
    for r in roots:
        k = str(r)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def list_sessions(*, cwd: Optional[str] = None) -> List[Session]:
    out: List[Session] = []
    for root in _roots():
        if not root.is_dir():
            continue
        for pattern in ("**/sessions/**/*.json", "**/*session*.json", "**/history/**/*.json", "**/*.jsonl"):
            for f in root.glob(pattern):
                if not f.is_file() or f.stat().st_size < 10:
                    continue
                if f.name in ("config.json", "settings.json", "profiles.json"):
                    continue
                sess = _from_file(f, cwd=cwd)
                if sess:
                    out.append(sess)
    by = {s.path: s for s in out}
    out = list(by.values())
    out.sort(key=lambda s: s.mtime, reverse=True)
    return out


def session_from_path(path: str) -> Optional[Session]:
    path = expand(path)
    if not os.path.isfile(path):
        return None
    return _from_file(Path(path))


def _from_file(path: Path, *, cwd: Optional[str] = None) -> Optional[Session]:
    try:
        st = path.stat()
        if path.suffix == ".jsonl":
            title, scwd, sid, n = _peek_jsonl(path)
        else:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            title, scwd, sid, n = _peek_json(data, path.stem)
    except Exception:
        return None
    if cwd and scwd and not cwd_matches(cwd, scwd):
        return None
    return Session(
        provider="goose",
        session_id=sid,
        path=str(path),
        title=title or f"Goose {sid[:8]}",
        cwd=scwd,
        mtime=st.st_mtime,
        size=st.st_size,
        message_count=n,
    )


def _peek_json(data, default_sid: str):
    title = ""
    scwd = ""
    sid = default_sid
    n = 0
    if isinstance(data, dict):
        sid = str(data.get("id") or data.get("session_id") or sid)
        title = str(data.get("title") or data.get("name") or "")
        scwd = str(data.get("cwd") or data.get("working_dir") or data.get("directory") or "")
        msgs = data.get("messages") or data.get("history") or []
        if isinstance(msgs, list):
            n = len(msgs)
            for m in msgs[:15]:
                if isinstance(m, dict) and (m.get("role") or "").lower() == "user":
                    cand = clean_title(stringify_content(m.get("content") or m.get("text")))
                    if cand:
                        title = title or cand
                        break
    elif isinstance(data, list):
        n = len(data)
    return title, scwd, sid, n


def _peek_jsonl(path: Path):
    title = ""
    scwd = ""
    sid = path.stem
    n = 0
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            n += 1
            if o.get("cwd") and not scwd:
                scwd = str(o["cwd"])
            if (o.get("role") or "").lower() == "user" and not title:
                cand = clean_title(stringify_content(o.get("content") or o.get("text")))
                if cand:
                    title = cand
            if i > 50 and title:
                break
    return title, scwd, sid, n


def load_transcript(session: Session, *, include_tools: bool = False) -> Transcript:
    path = Path(session.path)
    messages: List[Message] = []
    title = session.title
    cwd = session.cwd
    idx = 0

    def add(role: str, text: str, ts: str = ""):
        nonlocal idx, title
        text = strip_ansi(text or "")
        if not text.strip():
            return
        if role == "user" and (not title or title.startswith("Goose")):
            cand = clean_title(text)
            if cand:
                title = cand
        messages.append(Message(role=role, text=text, timestamp=ts, index=idx))
        idx += 1

    try:
        if path.suffix == ".jsonl":
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    o = json.loads(line)
                    role = (o.get("role") or "assistant").lower()
                    if role == "tool" and not include_tools:
                        continue
                    add(role if role in ("user", "assistant", "system", "tool") else "assistant", stringify_content(o.get("content") or o.get("text")), str(o.get("timestamp") or ""))
        else:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            items = data.get("messages") or data.get("history") if isinstance(data, dict) else data
            if isinstance(data, dict):
                cwd = str(data.get("cwd") or data.get("working_dir") or cwd)
            for m in items or []:
                if not isinstance(m, dict):
                    continue
                role = (m.get("role") or "assistant").lower()
                if role == "tool" and not include_tools:
                    continue
                add(role if role in ("user", "assistant", "system", "tool") else "assistant", stringify_content(m.get("content") or m.get("text")))
    except Exception:
        pass
    session.title = title or session.title
    session.cwd = cwd or session.cwd
    session.message_count = len(messages)
    return Transcript(session=session, messages=messages)
