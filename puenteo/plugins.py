"""Third-party extensions via Python entry points: no forking or editing of the core.

Install a package that declares any of these groups and puenteo picks it up::

    [project.entry-points."puenteo.providers"]
    cline = "puenteo_cline:provider"       # a module (or object) with list_sessions / load_transcript / session_from_path

    [project.entry-points."puenteo.tools"]
    jira = "puenteo_jira:register"          # register(server) -> adds MCP tools via @server.tool(...)

    [project.entry-points."puenteo.delivery"]
    slack = "puenteo_slack:push"            # push(address, message) -> str|None for addresses it owns

    [project.entry-points."puenteo.live"]
    cline = "puenteo_cline:live_sessions"   # () -> list[puenteo.live.LiveSession]

Contracts are small and stable (see docs/PLUGINS.md). A broken plugin is
reported once on stderr and skipped; it never breaks the CLI, MCP server or hooks.
Disable all plugins with ``PUENTEO_NO_PLUGINS=1``, or one with
``PUENTEO_DISABLE_PLUGINS=name1,name2``.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

GROUPS = ("puenteo.providers", "puenteo.tools", "puenteo.delivery", "puenteo.live")
API_VERSION = 1  # bumped only on breaking changes to a plugin contract

_cache: Dict[str, List["Loaded"]] = {}
_errors: List[str] = []


@dataclass
class Loaded:
    group: str
    name: str
    obj: Any
    dist: str = ""
    version: str = ""


def _disabled() -> set:
    raw = os.environ.get("PUENTEO_DISABLE_PLUGINS", "")
    return {x.strip() for x in raw.split(",") if x.strip()}


def _entry_points(group: str):
    try:
        from importlib.metadata import entry_points
    except ImportError:  # pragma: no cover
        return []
    try:
        eps = entry_points()
        if hasattr(eps, "select"):
            return list(eps.select(group=group))
        return list(eps.get(group, []))  # Python 3.9
    except Exception:
        return []


def load(group: str) -> List[Loaded]:
    """Load (once) every plugin of ``group``; failures are recorded, never raised."""
    if group in _cache:
        return _cache[group]
    out: List[Loaded] = []
    if os.environ.get("PUENTEO_NO_PLUGINS", "").strip() not in ("", "0", "false"):
        _cache[group] = out
        return out
    off = _disabled()
    for ep in _entry_points(group):
        if ep.name in off:
            continue
        dist = getattr(getattr(ep, "dist", None), "name", "") or ""
        ver = getattr(getattr(ep, "dist", None), "version", "") or ""
        try:
            obj = ep.load()
            need = getattr(obj, "PUENTEO_API", API_VERSION)
            if int(need) > API_VERSION:
                raise RuntimeError(f"needs plugin API {need}, this puenteo has {API_VERSION}")
            out.append(Loaded(group, ep.name, obj, dist, ver))
        except Exception as e:
            _report(f"{group}:{ep.name} ({dist or '?'}) failed to load: {e}")
    _cache[group] = out
    return out


def _report(msg: str) -> None:
    _errors.append(msg)
    if not os.environ.get("PUENTEO_QUIET"):
        print(f"puenteo: plugin {msg}", file=sys.stderr)


def errors() -> List[str]:
    return list(_errors)


def reset() -> None:
    """Forget loaded plugins (tests, or after installing one in-process)."""
    _cache.clear()
    _errors.clear()


def call(p: Loaded, fn: Callable, *args, **kw) -> Any:
    """Run plugin code; on error report once and return None."""
    try:
        return fn(*args, **kw)
    except Exception as e:
        _report(f"{p.group}:{p.name} raised {type(e).__name__}: {e}")
        return None


# ---------------------------------------------------------------- per-group helpers


REQUIRED_PROVIDER_FUNCS = ("list_sessions", "load_transcript")


def providers() -> Dict[str, Any]:
    """name → provider module/object (validated to expose the provider contract)."""
    res: Dict[str, Any] = {}
    for p in load("puenteo.providers"):
        missing = [f for f in REQUIRED_PROVIDER_FUNCS if not callable(getattr(p.obj, f, None))]
        if missing:
            _report(f"puenteo.providers:{p.name} is missing {', '.join(missing)}")
            continue
        res[p.name] = _SafeProvider(p)
    return res


class _SafeProvider:
    """Wraps a plugin provider so its exceptions surface as warnings, not crashes."""

    def __init__(self, p: Loaded):
        self._p = p

    def list_sessions(self, *, cwd: Optional[str] = None):
        return call(self._p, self._p.obj.list_sessions, cwd=cwd) or []

    def load_transcript(self, session, *, include_tools: bool = False):
        r = call(self._p, self._p.obj.load_transcript, session, include_tools=include_tools)
        if r is None:
            from .models import Transcript

            return Transcript(session=session, messages=[])
        return r

    def session_from_path(self, path: str):
        fn = getattr(self._p.obj, "session_from_path", None)
        return call(self._p, fn, path) if callable(fn) else None

    def __getattr__(self, item):
        return getattr(self._p.obj, item)


def register_tools(server) -> List[str]:
    """Let tool plugins add MCP tools; returns the names added."""
    before = set(server.tools)
    for p in load("puenteo.tools"):
        fn = p.obj if callable(p.obj) else getattr(p.obj, "register", None)
        if not callable(fn):
            _report(f"puenteo.tools:{p.name} is not callable and has no register()")
            continue
        call(p, fn, server)
    return sorted(set(server.tools) - before)


def deliver(address: str, msg) -> Optional[str]:
    """Ask delivery plugins to push ``msg`` to ``address``. Returns a status string from the first taker."""
    for p in load("puenteo.delivery"):
        fn = p.obj if callable(p.obj) else getattr(p.obj, "push", None)
        if not callable(fn):
            continue
        r = call(p, fn, address, msg)
        if r:
            return f"{r} ({p.name})"
    return None


def live_sessions() -> list:
    out: list = []
    for p in load("puenteo.live"):
        fn = p.obj if callable(p.obj) else getattr(p.obj, "live_sessions", None)
        if callable(fn):
            out.extend(call(p, fn) or [])
    return out


def describe() -> List[Dict[str, str]]:
    rows = []
    for g in GROUPS:
        for p in load(g):
            rows.append({"group": g, "name": p.name, "package": p.dist, "version": p.version})
    return rows
