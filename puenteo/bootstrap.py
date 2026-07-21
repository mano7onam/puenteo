"""Path helpers so other tools can import puenteo without a global install."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional


def package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def ensure_importable() -> Path:
    """Ensure ``puenteo`` is importable; return package root."""
    root = package_root()
    root_s = str(root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)
    return root


def discover_roots() -> List[Path]:
    """Candidate install locations (sibling repos, env, ~/dev)."""
    roots: List[Path] = []
    env = os.environ.get("PUENTEO_PATH") or os.environ.get("AGENT_SESSION_BRIDGE_PATH")
    if env:
        roots.append(Path(os.path.expanduser(env)))
    roots.append(package_root())
    roots.append(Path.home() / "dev" / "puenteo")
    roots.append(Path.home() / "dev" / "agent-session-bridge")  # legacy folder name

    here = Path(__file__).resolve()
    for p in here.parents:
        for name in ("puenteo", "agent-session-bridge"):
            sibling = p / name
            if sibling.is_dir():
                roots.append(sibling)

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
        if (r / "puenteo").is_dir() or (r / "pyproject.toml").is_file():
            out.append(r)
    return out


def which_puenteo() -> Optional[str]:
    """Path to ``puenteo`` CLI if available (PATH or local venv)."""
    for name in ("puenteo", "pto"):
        found = shutil.which(name)
        if found:
            return found
    for root in discover_roots():
        for rel in (".venv/bin/puenteo", ".venv/bin/pto", "puenteo"):
            cand = root / rel
            if cand.is_file() and os.access(cand, os.X_OK):
                return str(cand)
    local = Path.home() / ".local" / "bin" / "puenteo"
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    return None


# Back-compat aliases
which_asb = which_puenteo


def puenteo_cli_hint() -> dict:
    """Metadata for UIs that link to the CLI project."""
    roots = discover_roots()
    root = str(roots[0]) if roots else str(Path.home() / "dev" / "puenteo")
    return {
        "package": "puenteo",
        "cli": "puenteo",
        "path": root,
        "binary": which_puenteo(),
        "docs": (
            "Use `puenteo list|search|pull|export` for cross-agent context. "
            "Terminal Dashboard uses the same library for chat export."
        ),
        "github": "https://github.com/mano7onam/puenteo",
        "pypi": "https://pypi.org/project/puenteo/",
        "install": "pip install puenteo  # or: uv add puenteo",
    }


asb_cli_hint = puenteo_cli_hint
