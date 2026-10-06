"""Where puenteo keeps its own files (cache, index, message bus).

Everything is overridable with ``PUENTEO_HOME`` (tests use it to sandbox state).

- cache  → index.db (session metadata + full-text index; safe to delete)
- state  → bus.db  (messages between live sessions; do not delete casually)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _home_override() -> Path:
    raw = os.environ.get("PUENTEO_HOME", "").strip()
    return Path(os.path.expanduser(raw)) if raw else Path()


def cache_dir() -> Path:
    o = _home_override()
    if str(o) not in ("", "."):
        p = o / "cache"
    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Caches" / "puenteo"
    elif sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        p = Path(base) / "puenteo" / "Cache"
    else:
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
        p = Path(base) / "puenteo"
    p.mkdir(parents=True, exist_ok=True)
    return p


def state_dir() -> Path:
    o = _home_override()
    if str(o) not in ("", "."):
        p = o / "state"
    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / "puenteo"
    elif sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        p = Path(base) / "puenteo" / "State"
    else:
        base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
        p = Path(base) / "puenteo"
    p.mkdir(parents=True, exist_ok=True)
    return p


def index_db_path() -> Path:
    return cache_dir() / "index.db"


def bus_db_path() -> Path:
    raw = os.environ.get("PUENTEO_BUS", "").strip()
    if raw:
        p = Path(os.path.expanduser(raw))
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    return state_dir() / "bus.db"
