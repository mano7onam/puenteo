"""Session lineage: parent → subagents / forks (Claude subagents, Codex spawn edges, OpenCode parent_id)."""

from __future__ import annotations

from typing import Dict, List, Optional

from .models import Session


def build(sessions: List[Session]) -> Dict[str, List[Session]]:
    kids: Dict[str, List[Session]] = {}
    for s in sessions:
        p = (s.meta or {}).get("parent_id")
        if p and p != s.session_id:
            kids.setdefault(p, []).append(s)
    for v in kids.values():
        v.sort(key=lambda s: s.mtime)
    return kids


def render(root: Session, sessions: List[Session], *, max_depth: int = 6) -> str:
    from .util import format_mtime

    kids = build(sessions)
    by_id = {s.session_id: s for s in sessions}
    # climb to the top-most ancestor so `tree <child>` shows the whole family
    top = root
    seen = set()
    while (top.meta or {}).get("parent_id") in by_id and top.session_id not in seen:
        seen.add(top.session_id)
        top = by_id[top.meta["parent_id"]]

    lines: List[str] = []

    def walk(s: Session, prefix: str, last: bool, depth: int) -> None:
        mark = "" if depth == 0 else ("└─ " if last else "├─ ")
        here = " ◀" if s.session_id == root.session_id else ""
        lines.append(f"{prefix}{mark}{s.provider}:{s.session_id[:13]}  {format_mtime(s.mtime)}  {(s.title or '')[:70]}{here}")
        if depth >= max_depth:
            return
        ch = kids.get(s.session_id, [])
        for i, c in enumerate(ch):
            ext = "" if depth == 0 else ("   " if last else "│  ")
            walk(c, prefix + ext, i == len(ch) - 1, depth + 1)

    walk(top, "", True, 0)
    return "\n".join(lines)
