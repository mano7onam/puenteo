"""Aider chat histories (.aider.chat.history.md under projects)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Iterable, List, Optional

from ..models import Message, Session, Transcript
from ..util import clean_title, cwd_matches, expand, first_line, strip_ansi

HISTORY_NAMES = (
    ".aider.chat.history.md",
    ".aider.chat.history.md.bak",
)


def _search_roots(cwd: Optional[str] = None) -> List[str]:
    """
    Aider histories live inside project trees, so we only scan:
    - explicit --cwd (or absolute path filter)
    - PUENTEO_AIDER_ROOTS / ASB_AIDER_ROOTS (os.pathsep-separated)
    Never walk all of ~/dev by default — too slow on big monorepos.
    """
    roots: List[str] = []
    env = os.environ.get("PUENTEO_AIDER_ROOTS") or os.environ.get("ASB_AIDER_ROOTS")
    if env:
        roots.extend(p.strip() for p in env.split(os.pathsep) if p.strip())
    if cwd:
        raw = cwd.strip()
        if raw.startswith(("~", "/")) or (len(raw) > 1 and raw[1] == ":"):
            c = expand(raw)
            if os.path.isdir(c):
                roots.append(c)
    seen = set()
    out = []
    for r in roots:
        r = expand(r)
        if r not in seen and os.path.isdir(r):
            seen.add(r)
            out.append(r)
    return out


def _find_histories(roots: Iterable[str], *, max_files: int = 80, max_depth: int = 6) -> List[str]:
    found: List[str] = []
    skip_dirs = {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        "__pycache__",
        ".tox",
        ".mypy_cache",
        "target",
        ".next",
        ".idea",
        ".gradle",
        "out",
        "community",
        ".cache",
    }
    for root in roots:
        root = os.path.abspath(root)
        root_depth = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            depth = dirpath.rstrip(os.sep).count(os.sep) - root_depth
            if depth >= max_depth:
                dirnames[:] = []
                continue
            dirnames[:] = [
                d
                for d in dirnames
                if d not in skip_dirs and not d.startswith(".aider") and d not in (".turbo", ".pnpm")
            ]
            for name in HISTORY_NAMES:
                if name in filenames:
                    found.append(os.path.join(dirpath, name))
                    if len(found) >= max_files:
                        return found
            for fn in filenames:
                if "aider" in fn.lower() and "history" in fn.lower() and fn.endswith((".md", ".txt")):
                    p = os.path.join(dirpath, fn)
                    if p not in found:
                        found.append(p)
                        if len(found) >= max_files:
                            return found
    return found


def list_sessions(*, cwd: Optional[str] = None) -> List[Session]:
    roots = _search_roots(cwd)
    if not roots:
        return []

    out: List[Session] = []
    for path in _find_histories(roots):
        try:
            st = os.stat(path)
        except OSError:
            continue
        proj = os.path.dirname(path)
        if cwd and not cwd_matches(cwd, proj):
            continue
        sid = hashlib.sha1(path.encode()).hexdigest()[:16]
        title = _peek_title(path) or f"Aider {os.path.basename(proj)}"
        out.append(
            Session(
                provider="aider",
                session_id=sid,
                path=path,
                title=title,
                cwd=proj,
                mtime=st.st_mtime,
                size=st.st_size,
            )
        )
    out.sort(key=lambda s: s.mtime, reverse=True)
    return out


def session_from_path(path: str) -> Optional[Session]:
    path = expand(path)
    if not os.path.isfile(path):
        return None
    st = os.stat(path)
    sid = hashlib.sha1(path.encode()).hexdigest()[:16]
    return Session(
        provider="aider",
        session_id=sid,
        path=path,
        title=_peek_title(path) or f"Aider {os.path.basename(os.path.dirname(path))}",
        cwd=os.path.dirname(path),
        mtime=st.st_mtime,
        size=st.st_size,
    )


def _peek_title(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i > 80:
                    break
                if line.lower().startswith("#### user") or line.lower().startswith("# user"):
                    continue
                s = line.strip()
                if s and not s.startswith("#") and not s.startswith("####"):
                    cand = clean_title(s)
                    if cand:
                        return cand
    except Exception:
        pass
    return ""


def load_transcript(session: Session, *, include_tools: bool = False) -> Transcript:
    """Parse aider markdown history into messages."""
    messages: List[Message] = []
    title = session.title
    idx = 0
    role = "user"
    buf: List[str] = []

    def flush():
        nonlocal idx, title, buf, role
        text = strip_ansi("\n".join(buf).strip())
        buf = []
        if not text:
            return
        if role == "user" and (not title or title.startswith("Aider")):
            cand = clean_title(text)
            if cand:
                title = cand
        messages.append(Message(role=role, text=text, index=idx))
        idx += 1

    try:
        with open(session.path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                low = line.strip().lower()
                if low.startswith("#### user") or low == "# user" or low.startswith("### user"):
                    flush()
                    role = "user"
                    continue
                if (
                    low.startswith("#### assistant")
                    or low.startswith("#### aider")
                    or low == "# assistant"
                    or low.startswith("### assistant")
                ):
                    flush()
                    role = "assistant"
                    continue
                buf.append(line.rstrip("\n"))
            flush()
    except OSError:
        pass

    session.title = title or session.title
    session.message_count = len(messages)
    return Transcript(session=session, messages=messages)
