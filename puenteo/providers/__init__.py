from __future__ import annotations

from typing import List, Optional, Union

from ..models import Session, Transcript
from . import (
    aider,
    antigravity,
    claude,
    codex,
    continue_dev,
    copilot,
    cursor,
    gemini_cli,
    goose,
    grok,
    opencode,
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
    "opencode": opencode,
    "copilot": copilot,
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
    "opencode",
    "copilot",
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
    "opencode": "opencode",
    "sst": "opencode",
    "copilot": "copilot",
    "copilot-cli": "copilot",
    "github-copilot": "copilot",
}


_WARNED: set = set()


def _warn_provider(name: str, err: Exception) -> None:
    import os
    import sys

    if name in _WARNED or os.environ.get("PUENTEO_QUIET"):
        return
    _WARNED.add(name)
    print(f"puenteo: warning: provider {name!r} failed: {err}", file=sys.stderr)


_BUILTIN = set(PROVIDERS)
_plugins_merged = False


def _merge_plugins() -> None:
    """Add third-party providers (entry point ``puenteo.providers``); built-in names can't be overridden."""
    global _plugins_merged, PROVIDER_NAMES
    if _plugins_merged:
        return
    _plugins_merged = True
    try:
        from ..plugins import _report, providers as plugin_providers

        extra = []
        for name, mod in plugin_providers().items():
            if name in _BUILTIN or name in PROVIDER_ALIASES:
                _report(f"puenteo.providers:{name} clashes with a built-in provider; skipped")
                continue
            PROVIDERS[name] = mod
            extra.append(name)
        if extra:
            PROVIDER_NAMES = tuple(PROVIDER_NAMES) + tuple(extra)
    except Exception:
        pass


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
    _merge_plugins()
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
        except Exception as e:  # one broken store must not hide the others
            _warn_provider(name, e)
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
    _merge_plugins()
    name = normalize_provider_name(session.provider)
    mod = PROVIDERS.get(name) or PROVIDERS.get(session.provider)
    if not mod:
        raise ValueError(f"Unknown provider: {session.provider}")
    return mod.load_transcript(session, include_tools=include_tools)


class AmbiguousSessionError(LookupError):
    """A session ref (prefix / title) matched more than one session."""

    def __init__(self, ref: str, candidates: List[Session]):
        self.ref = ref
        self.candidates = candidates
        from ..util import unique_prefixes

        pref = unique_prefixes([c.session_id for c in candidates], min_len=8)
        lines = [f"Ambiguous session ref {ref!r}: {len(candidates)} matches. Use a longer prefix:"]
        for c in candidates[:12]:
            lines.append(f"  {c.provider:12} {pref.get(c.session_id, c.session_id):20} {(c.title or '')[:60]}")
        if len(candidates) > 12:
            lines.append(f"  … and {len(candidates) - 12} more")
        super().__init__("\n".join(lines))


def _split_provider_ref(ref: str) -> "tuple[Optional[str], str]":
    """``codex:01a0…`` → ("codex", "01a0…"). Plain refs and Windows paths pass through."""
    if ":" in ref and not (len(ref) > 1 and ref[1] == ":"):
        head, tail = ref.split(":", 1)
        name = normalize_provider_name(head)
        if name in PROVIDERS and tail:
            return name, tail
    return None, ref


def _self_session_id() -> Optional[str]:
    try:
        from ..live import whoami

        me = whoami()
        return me.session_id if me else None
    except Exception:
        return None


def resolve_session(
    ref: str,
    *,
    providers: Optional[List[str]] = None,
    cwd: Optional[str] = None,
    allow_ambiguous: bool = False,
) -> Optional[Session]:
    """
    Resolve a session reference.

    Accepted refs:
      - full id, ``provider:id``, or a unique id prefix
      - ``@self`` (the session running this process), ``@last`` / ``@last:codex``
      - a path to the session file
      - a title substring (only when no id matches)

    Raises :class:`AmbiguousSessionError` when a prefix/title matches several
    sessions (unless ``allow_ambiguous``; then the newest wins).
    """
    import os

    ref = (ref or "").strip()
    if not ref:
        return None

    if ref in ("@self", "@me", "self"):
        sid = _self_session_id()
        if not sid:
            return None
        return resolve_session(sid, providers=providers)

    if ref.startswith("@last"):
        prov = ref.split(":", 1)[1] if ":" in ref else None
        own = _self_session_id()
        for s in list_sessions(providers=[prov] if prov else providers, cwd=cwd, limit=0):
            if s.session_id != own:
                return s
        return None

    prov_from_ref, ref = _split_provider_ref(ref)
    if prov_from_ref:
        providers = [prov_from_ref]
    if os.path.isfile(ref) or os.path.isdir(ref):
        path = os.path.abspath(os.path.expanduser(ref))
        path_l = path.replace("\\", "/")
        # infer provider from path
        if "#ses_" in path_l or "opencode.db" in path_l:
            return opencode.session_from_path(path)
        if "/.copilot/session-state/" in path_l:
            return copilot.session_from_path(path)
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

    sessions = list_sessions(providers=providers, cwd=cwd, limit=0)
    ref_l = ref.lower()

    def pick(hits: List[Session]) -> Optional[Session]:
        # Same session can appear twice (e.g. a resumed Claude file); collapse by id.
        uniq: List[Session] = []
        seen = set()
        for s in hits:
            key = (s.provider, s.session_id)
            if key not in seen:
                seen.add(key)
                uniq.append(s)
        if not uniq:
            return None
        if len(uniq) == 1 or allow_ambiguous:
            return uniq[0]
        raise AmbiguousSessionError(ref, uniq)

    exact = [s for s in sessions if s.session_id == ref]
    if exact:
        return exact[0]

    hits = [s for s in sessions if s.session_id.lower().startswith(ref_l)]
    if hits:
        return pick(hits)

    if len(ref) >= 3:
        hits = [s for s in sessions if ref_l in (s.title or "").lower()]
        if hits:
            return pick(hits)

    return None


# Known on-disk roots for status / doctor (portable; Cursor expands per-OS)
PROVIDER_HOMES = {
    "claude_code": "~/.claude/projects",
    "codex": "~/.codex/sessions",
    "grok": "~/.grok/sessions",
    "pi": "~/.pi/agent/sessions",
    "antigravity": "~/.gemini/antigravity/brain",
    "qwen": "~/.qwen/projects",
    "gemini": "~/.gemini",
    "cursor": "Cursor app data (macOS Application Support / Windows %APPDATA% / Linux ~/.config)",
    "continue": "~/.continue",
    "aider": "project/.aider.chat.history.md (scan with --cwd or PUENTEO_AIDER_ROOTS)",
    "openhands": "~/.openhands/openhands.db",
    "goose": "~/.config/goose  (or %APPDATA%/goose on Windows)",
    "opencode": "~/.local/share/opencode/opencode.db",
    "copilot": "~/.copilot/session-state",
}


def cursor_home_display() -> str:
    """Human-readable Cursor store path for the current OS."""
    from ..util import platform_app_support_dirs

    roots = platform_app_support_dirs("Cursor")
    return str(roots[0]) if roots else "~/.cursor"


def provider_store_status() -> dict:
    """
    Per-provider store path + exists flag + session count for ``status`` CLI/API.
    Paths expand correctly on macOS, Linux, and Windows.
    """
    import os
    import sys
    from pathlib import Path

    from ..util import platform_app_support_dirs

    info = {}
    for name in PROVIDER_NAMES:
        raw = PROVIDER_HOMES.get(name, "")
        path_str = raw
        exists = False

        if name == "cursor":
            path_str = cursor_home_display()
            exists = any(p.exists() for p in platform_app_support_dirs("Cursor", "Cursor Nightly"))
            if not exists:
                exists = (Path.home() / ".cursor").exists()
        elif name == "goose":
            if sys.platform == "win32":
                appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
                path_str = str(Path(appdata) / "goose")
                exists = Path(path_str).is_dir() or (Path.home() / ".goose").is_dir()
            else:
                p = Path(os.path.expanduser("~/.config/goose"))
                path_str = str(p)
                exists = p.is_dir() or Path(os.path.expanduser("~/.goose")).is_dir()
        else:
            token = raw.split()[0] if raw else ""
            if token.startswith("~") or token.startswith("/") or (
                len(token) > 1 and token[1] == ":"
            ):
                p = Path(os.path.expanduser(token))
                path_str = str(p)
                exists = p.is_dir() or p.is_file()

        count = 0
        try:
            count = len(list_sessions(providers=[name], limit=0))
        except Exception:
            count = 0
        if not exists and count > 0:
            exists = True
        info[name] = {"path": path_str, "exists": exists, "sessions": count}
    return info
