"""Google Antigravity sessions (~/.gemini/antigravity/brain/*/transcript*.jsonl)."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import List, Optional, Tuple

from ..models import Message, Session, Transcript
from ..util import (
    clean_title,
    cwd_matches,
    expand,
    first_line,
    strip_ansi,
    stringify_content,
)

USER_REQ_RE = re.compile(
    r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>",
    re.S | re.I,
)
PATH_RE = re.compile(r"(?:file://)?(/Users/[^\s\"'<>\x00-\x1f]{3,200}|/[a-zA-Z0-9._/-]{4,200})")


def _roots() -> List[Path]:
    home = Path(expand("~"))
    return [
        home / ".gemini" / "antigravity",
        home / ".gemini" / "antigravity-ide",
    ]


def list_sessions(*, cwd: Optional[str] = None) -> List[Session]:
    out: List[Session] = []
    seen = set()
    for root in _roots():
        brain = root / "brain"
        if not brain.is_dir():
            continue
        for sess_dir in brain.iterdir():
            if not sess_dir.is_dir():
                continue
            sid = sess_dir.name
            if len(sid) < 8 or sid in seen:
                continue
            transcript = _pick_transcript(sess_dir)
            if not transcript:
                continue
            try:
                st = transcript.stat()
            except OSError:
                continue
            title, real_cwd = _peek(transcript, sess_dir, root, sid)
            if _is_junk_cwd(real_cwd):
                real_cwd = ""
            if cwd and not cwd_matches(cwd, real_cwd):
                # still allow if transcript text mentions the project
                if not _cwd_in_file(transcript, cwd or ""):
                    continue
            seen.add(sid)
            out.append(
                Session(
                    provider="antigravity",
                    session_id=sid,
                    path=str(transcript),
                    title=title or f"Antigravity {sid[:8]}",
                    cwd=real_cwd,
                    mtime=st.st_mtime,
                    size=st.st_size,
                    meta={"brain_dir": str(sess_dir), "store": str(root)},
                )
            )
    out.sort(key=lambda s: s.mtime, reverse=True)
    return out


def session_from_path(path: str) -> Optional[Session]:
    path = expand(path)
    p = Path(path)
    if p.is_dir():
        tr = _pick_transcript(p)
        if not tr:
            return None
        p = tr
    if not p.is_file():
        return None
    # brain/<sid>/.../transcript*.jsonl
    parts = p.parts
    sid = ""
    try:
        i = parts.index("brain")
        sid = parts[i + 1]
    except (ValueError, IndexError):
        sid = p.stem
    title, real_cwd = _peek(p, p.parent, p.parents[3] if len(p.parts) > 4 else p.parent, sid)
    st = p.stat()
    return Session(
        provider="antigravity",
        session_id=sid or p.stem,
        path=str(p),
        title=title or f"Antigravity {(sid or p.stem)[:8]}",
        cwd=real_cwd,
        mtime=st.st_mtime,
        size=st.st_size,
    )


def load_transcript(session: Session, *, include_tools: bool = False) -> Transcript:
    path = session.path
    messages: List[Message] = []
    title = session.title
    cwd = session.cwd
    idx = 0

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            role, text, tools = _parse_step(o, include_tools=include_tools)
            if not text and not tools:
                continue
            if tools and include_tools:
                text = (text + "\n" + "\n".join(tools)).strip() if text else "\n".join(tools)
            elif not text:
                continue
            text = strip_ansi(text)
            if role == "user":
                m = USER_REQ_RE.search(text)
                if m:
                    text = m.group(1).strip()
                cand = clean_title(text)
                if cand and (not title or title.startswith("Antigravity")):
                    title = cand
                # infer cwd from absolute paths in user text
                if not cwd:
                    cwd = _first_project_path(text) or cwd
            messages.append(
                Message(
                    role=role,
                    text=text,
                    timestamp=str(o.get("created_at") or ""),
                    index=idx,
                    meta={"type": o.get("type"), "source": o.get("source")},
                )
            )
            idx += 1
            if include_tools and not cwd:
                for t in tools:
                    c = _first_project_path(t)
                    if c:
                        cwd = c
                        break

    if not cwd or _is_junk_cwd(cwd):
        cwd = _cwd_from_db(session.session_id) or ""
    if _is_junk_cwd(cwd):
        cwd = ""

    session.title = title or session.title
    session.cwd = cwd or session.cwd
    if _is_junk_cwd(session.cwd):
        session.cwd = ""
    session.message_count = len(messages)
    return Transcript(session=session, messages=messages)


def _pick_transcript(sess_dir: Path) -> Optional[Path]:
    full = sess_dir / ".system_generated" / "logs" / "transcript_full.jsonl"
    slim = sess_dir / ".system_generated" / "logs" / "transcript.jsonl"
    if full.is_file():
        return full
    if slim.is_file():
        return slim
    # fallback: any jsonl under logs
    logs = sess_dir / ".system_generated" / "logs"
    if logs.is_dir():
        for p in sorted(logs.glob("*.jsonl")):
            return p
    return None


def _peek(transcript: Path, sess_dir: Path, root: Path, sid: str) -> Tuple[str, str]:
    title = ""
    cwd = ""
    # walkthrough.md is a great summary title
    for name in ("walkthrough.md", "task.md", "implementation_plan.md"):
        wp = sess_dir / name
        if wp.is_file():
            try:
                first = wp.read_text(encoding="utf-8", errors="replace").splitlines()[0]
                first = first.lstrip("# ").strip()
                if first:
                    title = first[:160]
                    break
            except Exception:
                pass
    try:
        with open(transcript, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i > 80:
                    break
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") == "USER_INPUT" and not title:
                    content = str(o.get("content") or "")
                    m = USER_REQ_RE.search(content)
                    raw = m.group(1).strip() if m else content
                    cand = clean_title(raw, 140)
                    if cand:
                        title = cand
                if not cwd:
                    blob = json.dumps(o, ensure_ascii=False)
                    cwd = _first_project_path(blob) or cwd
    except Exception:
        pass
    if not cwd:
        cwd = _cwd_from_db(sid) or ""
    return title, cwd


def _cwd_from_db(sid: str) -> str:
    for root in _roots():
        db = root / "conversations" / f"{sid}.db"
        if not db.is_file():
            continue
        try:
            conn = sqlite3.connect(str(db))
            row = conn.execute(
                "select data from trajectory_metadata_blob where id='main' limit 1"
            ).fetchone()
            conn.close()
            if not row:
                continue
            data = row[0]
            if isinstance(data, memoryview):
                data = data.tobytes()
            if isinstance(data, bytes):
                text = data.decode("utf-8", "replace")
            else:
                text = str(data)
            # prefer real project paths, never ~/.gemini brain paths
            found = _first_project_path(text)
            if found:
                return found
            m = re.search(r"file://(/[^\x00-\x1f\s]{3,200})", text)
            if m:
                cand = _clean_path(m.group(1))
                if not _is_junk_cwd(cand):
                    return cand
        except Exception:
            continue
    return ""


def _clean_path(p: str) -> str:
    # strip trailing garbage from protobuf decoding
    p = p.split("\x00")[0]
    while p and not os.path.isdir(p) and "/" in p:
        # trim trailing non-path junk one char at a time then by segment
        if p[-1].isalnum() or p[-1] in "._-":
            # try parent if full path missing
            parent = os.path.dirname(p)
            if os.path.isdir(p):
                return p
            if os.path.isdir(parent):
                # if last segment looks corrupted, use parent when parent is a project
                base = os.path.basename(p)
                if any(c in base for c in "\x7f\x80") or not re.match(r"^[\w.+=@-]+$", base):
                    return parent
            break
        p = p[:-1]
    if os.path.isdir(p):
        return p
    # longest existing prefix
    cur = p
    while cur and cur != "/":
        if os.path.isdir(cur):
            return cur
        cur = os.path.dirname(cur)
    return p.rstrip("/\\")


def _is_junk_cwd(path: str) -> bool:
    if not path:
        return True
    p = path.replace("\\", "/")
    junk_bits = (
        "/.gemini/",
        "/antigravity/brain/",
        "/antigravity/scratch",
        "/.system_generated/",
        "/Library/Application Support/",
        "/node_modules/",
    )
    return any(b in p for b in junk_bits)


def _first_project_path(text: str) -> str:
    if not text:
        return ""
    candidates: List[str] = []
    for m in PATH_RE.finditer(text):
        cand = _clean_path(m.group(1))
        if not (cand.startswith("/Users/") or cand.startswith("/home/") or cand.startswith("/opt/")):
            continue
        if _is_junk_cwd(cand):
            continue
        if cand.count("/") < 3:
            continue
        # prefer directory that exists
        if os.path.isdir(cand):
            candidates.append(cand)
        else:
            parent = os.path.dirname(cand)
            if os.path.isdir(parent) and not _is_junk_cwd(parent):
                candidates.append(parent)
    if not candidates:
        return ""
    # score: prefer shorter project roots under dev/Documents/projects
    def score(p: str) -> tuple:
        pref = 0
        if "/dev/" in p or p.rstrip("/").endswith("/dev"):
            pref += 2
        if "/Documents/" in p or "/projects/" in p or "/PycharmProjects/" in p:
            pref += 1
        return (pref, -p.count("/"), len(p))

    candidates.sort(key=score, reverse=True)
    return candidates[0]


def _cwd_in_file(transcript: Path, cwd: str) -> bool:
    if not cwd:
        return False
    frag = cwd if not cwd.startswith("/") else os.path.basename(cwd.rstrip("/"))
    try:
        # cheap: check first 200KB
        data = transcript.read_text(encoding="utf-8", errors="replace")[:200_000]
        return cwd in data or (frag and frag in data)
    except Exception:
        return False


def _parse_step(o: dict, *, include_tools: bool) -> Tuple[str, str, List[str]]:
    t = str(o.get("type") or "")
    source = str(o.get("source") or "")
    tools: List[str] = []

    if t == "USER_INPUT":
        return "user", str(o.get("content") or ""), tools
    if t in ("SYSTEM_MESSAGE", "CONVERSATION_HISTORY", "CHECKPOINT"):
        return "system", "", tools  # skip by empty
    if t == "ERROR_MESSAGE":
        return "assistant", strip_ansi(str(o.get("content") or o.get("text") or "")), tools

    # model / planner turns
    text = ""
    for key in ("content", "text", "message", "response"):
        if o.get(key):
            text = stringify_content(o.get(key))
            break

    # Skip noisy GENERIC task-status lines unless tools requested
    if t == "GENERIC":
        if not include_tools:
            return "assistant", "", tools
        if text.startswith("Created At:") or "background task" in text.lower():
            return "assistant", "", tools

    if o.get("tool_calls"):
        for tc in o.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            name = tc.get("name") or "tool"
            args = tc.get("args") or tc.get("arguments") or {}
            try:
                arg_s = json.dumps(args, ensure_ascii=False)[:500]
            except Exception:
                arg_s = str(args)[:500]
            summary = ""
            if isinstance(args, dict):
                summary = str(
                    args.get("toolSummary")
                    or args.get("toolAction")
                    or args.get("CommandLine")
                    or args.get("AbsolutePath")
                    or args.get("DirectoryPath")
                    or ""
                )[:200]
            tools.append(f"[tool_use {name}] {summary or arg_s}")
        if not include_tools:
            # keep natural-language planner replies; drop pure tool-call steps
            if not (text or "").strip():
                return "assistant", "", []

    # tool result steps (VIEW_FILE, RUN_COMMAND, etc.) — surface lightly when tools on
    if t not in ("PLANNER_RESPONSE", "GENERIC", "USER_INPUT") and t.isupper():
        if include_tools:
            snippet = text or json.dumps(
                {k: o[k] for k in o if k not in ("step_index", "source", "status", "created_at")},
                ensure_ascii=False,
            )[:600]
            return "tool", f"[{t}] {snippet}", tools
        return "assistant", "", tools

    role = "assistant"
    if source == "USER_EXPLICIT":
        role = "user"
    elif source == "SYSTEM":
        role = "system"
    return role, text, tools
