from __future__ import annotations

from typing import List, Optional

from ..models import Session, Transcript
from . import claude, codex, grok, pi

PROVIDERS = {
    "claude_code": claude,
    "claude": claude,
    "codex": codex,
    "grok": grok,
    "pi": pi,
}

PROVIDER_NAMES = ("claude_code", "codex", "grok", "pi")


def list_sessions(
    *,
    providers: Optional[List[str]] = None,
    cwd: Optional[str] = None,
    limit: int = 50,
) -> List[Session]:
    names = providers or list(PROVIDER_NAMES)
    out: List[Session] = []
    for name in names:
        mod = PROVIDERS.get(name)
        if not mod:
            continue
        # normalize claude alias
        if name == "claude":
            name = "claude_code"
        out.extend(mod.list_sessions(cwd=cwd))
    out.sort(key=lambda s: s.mtime, reverse=True)
    if limit and limit > 0:
        out = out[:limit]
    return out


def load_transcript(session: Session, *, include_tools: bool = False) -> Transcript:
    mod = PROVIDERS.get(session.provider) or PROVIDERS.get(
        "claude_code" if session.provider == "claude" else session.provider
    )
    if not mod:
        raise ValueError(f"Unknown provider: {session.provider}")
    return mod.load_transcript(session, include_tools=include_tools)


def resolve_session(
    ref: str,
    *,
    providers: Optional[List[str]] = None,
    cwd: Optional[str] = None,
) -> Optional[Session]:
    """Resolve by full/short session id, path, or unique title substring."""
    import os

    ref = (ref or "").strip()
    if not ref:
        return None
    if os.path.isfile(ref):
        # infer provider from path
        path = os.path.abspath(ref)
        if "/.claude/" in path or (path.endswith(".jsonl") and "projects" in path):
            return claude.session_from_path(path)
        if "/.codex/" in path:
            return codex.session_from_path(path)
        if "/.grok/" in path:
            return grok.session_from_path(path)
        if "/.pi/" in path:
            return pi.session_from_path(path)
        # try all
        for mod in (claude, codex, grok, pi):
            s = mod.session_from_path(path)
            if s:
                return s
        return None

    sessions = list_sessions(providers=providers, cwd=cwd, limit=500)
    ref_l = ref.lower()

    # exact id
    for s in sessions:
        if s.session_id == ref:
            return s
    # prefix id
    hits = [s for s in sessions if s.session_id.startswith(ref)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        # newest
        return hits[0]

    # title substring
    hits = [s for s in sessions if ref_l in (s.title or "").lower()]
    if hits:
        return hits[0]

    # cwd path substring match in session cwd
    hits = [s for s in sessions if ref_l in (s.cwd or "").lower()]
    if hits:
        return hits[0]

    return None
