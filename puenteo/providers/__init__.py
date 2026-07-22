from __future__ import annotations

from typing import List, Optional, Union

from ..models import Session, Transcript
from . import (
    aider,
    antigravity,
    claude,
    codex,
    continue_dev,
    cursor,
    gemini_cli,
    goose,
    grok,
    openhands,
    pi,
    qwen,
)

# Canonical provider name → module
PROVIDERS = {
    "claude_code": claude,
    "claude": claude,
    "codex": codex,
    "grok": grok,
    "pi": pi,
    "antigravity": antigravity,
    "agy": antigravity,
    "qwen": qwen,
    "aider": aider,
    "cursor": cursor,
    "continue": continue_dev,
    "continue_dev": continue_dev,
    "openhands": openhands,
    "opendevin": openhands,
    "goose": goose,
    "gemini": gemini_cli,
    "gemini_cli": gemini_cli,
}

# Display / default scan order (primary names only)
PROVIDER_NAMES = (
    "claude_code",
    "codex",
    "grok",
    "pi",
    "antigravity",
    "qwen",
    "gemini",
    "cursor",
    "continue",
    "aider",
    "openhands",
    "goose",
)

# Human-friendly aliases for --provider
PROVIDER_ALIASES = {
    "claude": "claude_code",
    "claude-code": "claude_code",
    "claude_code": "claude_code",
    "codex": "codex",
    "openai": "codex",
    "grok": "grok",
    "xai": "grok",
    "pi": "pi",
    "pi-agent": "pi",
    "antigravity": "antigravity",
    "agy": "antigravity",
    "google-antigravity": "antigravity",
    "qwen": "qwen",
    "qwen-code": "qwen",
    "gemini": "gemini",
    "gemini-cli": "gemini",
    "cursor": "cursor",
    "continue": "continue",
    "continue.dev": "continue",
    "aider": "aider",
    "openhands": "openhands",
    "opendevin": "openhands",
    "goose": "goose",
}


def normalize_provider_name(name: str) -> str:
    n = (name or "").strip().lower()
    return PROVIDER_ALIASES.get(n, n)


def _parse_time_bound(value: Optional[Union[str, float, int]]) -> Optional[float]:
    """Parse --since/--until into epoch seconds. Accepts epoch, YYYY-MM-DD, or YYYY-MM-DDTHH:MM."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    from datetime import datetime

    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            continue
    raise ValueError(f"Unrecognized time bound: {value!r} (use epoch or YYYY-MM-DD)")


def list_sessions(
    *,
    providers: Optional[List[str]] = None,
    cwd: Optional[str] = None,
    limit: int = 50,
    since: Optional[Union[str, float, int]] = None,
    until: Optional[Union[str, float, int]] = None,
) -> List[Session]:
    if providers:
        names = []
        for p in providers:
            n = normalize_provider_name(p)
            if n not in names:
                names.append(n)
    else:
        names = list(PROVIDER_NAMES)

    out: List[Session] = []
    for name in names:
        mod = PROVIDERS.get(name)
        if not mod:
            continue
        try:
            out.extend(mod.list_sessions(cwd=cwd))
        except Exception:
            continue
    out.sort(key=lambda s: s.mtime, reverse=True)

    since_ts = _parse_time_bound(since)
    until_ts = _parse_time_bound(until)
    if since_ts is not None:
        out = [s for s in out if (s.mtime or 0) >= since_ts]
    if until_ts is not None:
        out = [s for s in out if (s.mtime or 0) <= until_ts]

    if limit and limit > 0:
        out = out[:limit]
    return out


def load_transcript(session: Session, *, include_tools: bool = False) -> Transcript:
    name = normalize_provider_name(session.provider)
    mod = PROVIDERS.get(name) or PROVIDERS.get(session.provider)
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
    if os.path.isfile(ref) or os.path.isdir(ref):
        path = os.path.abspath(os.path.expanduser(ref))
        path_l = path.replace("\\", "/")
        # infer provider from path
        if "/.claude/" in path_l or (path_l.endswith(".jsonl") and "projects" in path_l and "claude" in path_l):
            return claude.session_from_path(path)
        if "/.codex/" in path_l:
            return codex.session_from_path(path)
        if "/.grok/" in path_l:
            return grok.session_from_path(path)
        if "/.pi/" in path_l:
            return pi.session_from_path(path)
        if "antigravity" in path_l:
            return antigravity.session_from_path(path)
        if "/.qwen/" in path_l:
            return qwen.session_from_path(path)
        if "/.gemini/" in path_l:
            s = gemini_cli.session_from_path(path)
            if s:
                return s
            return antigravity.session_from_path(path)
        if "Cursor" in path or "/.cursor/" in path_l:
            return cursor.session_from_path(path)
        if "/.continue/" in path_l:
            return continue_dev.session_from_path(path)
        if "aider" in os.path.basename(path).lower():
            return aider.session_from_path(path)
        if "openhands" in path_l:
            return openhands.session_from_path(path)
        if "goose" in path_l:
            return goose.session_from_path(path)
        # try all
        for mod in (
            claude,
            codex,
            grok,
            pi,
            antigravity,
            qwen,
            gemini_cli,
            cursor,
            continue_dev,
            aider,
            openhands,
            goose,
        ):
            try:
                s = mod.session_from_path(path)
            except Exception:
                s = None
            if s:
                return s
        return None

    sessions = list_sessions(providers=providers, cwd=cwd, limit=800)
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


# Known on-disk roots for status / doctor
PROVIDER_HOMES = {
    "claude_code": "~/.claude/projects",
    "codex": "~/.codex/sessions",
    "grok": "~/.grok/sessions",
    "pi": "~/.pi/agent/sessions",
    "antigravity": "~/.gemini/antigravity/brain",
    "qwen": "~/.qwen/projects",
    "gemini": "~/.gemini",
    "cursor": "~/Library/Application Support/Cursor",
    "continue": "~/.continue",
    "aider": "project/.aider.chat.history.md (scan with --cwd or PUENTEO_AIDER_ROOTS)",
    "openhands": "~/.openhands/openhands.db",
    "goose": "~/.config/goose",
}
