"""Path helpers so Terminal Dashboard can import this package without a global install."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional


def package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def ensure_importable() -> Path:
    """Ensure `agent_session_bridge` is importable; return package root."""
    root = package_root()
    root_s = str(root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)
    return root


def discover_roots() -> List[Path]:
    """Candidate install locations (sibling of terminal-dashboard, env, ~/dev)."""
    roots: List[Path] = []
    env = os.environ.get("AGENT_SESSION_BRIDGE_PATH") or os.environ.get("ASB_PATH")
    if env:
        roots.append(Path(os.path.expanduser(env)))
    roots.append(package_root())
    roots.append(Path.home() / "dev" / "agent-session-bridge")
    # sibling of caller projects
    here = Path(__file__).resolve()
    for p in here.parents:
        sibling = p / "agent-session-bridge"
        if sibling.is_dir():
            roots.append(sibling)
    # de-dupe
    seen = set()
    out: List[Path] = []
    for r in roots:
        try:
            key = r.resolve()
        except Exception:
            key = r
        if key in seen:
            continue
        seen.add(key)
        if (r / "agent_session_bridge").is_dir() or (r / "pyproject.toml").is_file():
            out.append(r)
    return out


def which_asb() -> Optional[str]:
    """Path to `asb` CLI if available (PATH or local venv)."""
    found = shutil.which("asb")
    if found:
        return found
    for root in discover_roots():
        for rel in (".venv/bin/asb", "asb"):
            cand = root / rel
            if cand.is_file() and os.access(cand, os.X_OK):
                return str(cand)
    local = Path.home() / ".local" / "bin" / "asb"
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    return None


def asb_cli_hint() -> dict:
    """Metadata for UIs that want to link to the CLI project."""
    roots = discover_roots()
    root = str(roots[0]) if roots else str(Path.home() / "dev" / "agent-session-bridge")
    return {
        "package": "agent-session-bridge",
        "cli": "asb",
        "path": root,
        "binary": which_asb(),
        "docs": "Use `asb list|search|pull` for cross-agent context; Terminal Dashboard uses the same library for export.",
        "github": "https://github.com/mano7onam/agent-session-bridge",
    }
