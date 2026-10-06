"""Git pre-commit guard: refuse commits that touch files a *peer* session has claimed.

``puenteo guard install`` (in a repo) writes ``.git/hooks/pre-commit`` that runs
``puenteo guard check``. Override once with ``PUENTEO_GUARD=off git commit …``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import List

MARK = "# puenteo-guard"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def staged_paths() -> List[str]:
    root = _git("rev-parse", "--show-toplevel").strip()
    names = _git("diff", "--cached", "--name-only", "-z").split("\0")
    return [os.path.join(root, n) for n in names if n]


def check(me: str = "") -> int:
    if os.environ.get("PUENTEO_GUARD", "").lower() in ("off", "0", "no"):
        return 0
    from .bus import Bus

    if not me:
        from .live import whoami

        w = whoami()
        me = w.address if w else ""
    try:
        paths = staged_paths()
    except Exception:
        return 0
    if not paths:
        return 0
    with Bus() as b:
        conflicts = b.check(paths, me=me)
    if not conflicts:
        return 0
    print("puenteo guard: these staged paths are claimed by another agent session:", file=sys.stderr)
    for c in conflicts:
        print(f"  {c.resource}  ← {c.holder}  ({int(c.expires - __import__('time').time())}s left) {c.note}", file=sys.stderr)
    print("Ask them first:  puenteo send <holder> \"…\"   ·   override once: PUENTEO_GUARD=off git commit …", file=sys.stderr)
    return 1


def install(repo: str = ".") -> str:
    hooks = Path(_git("-C", repo, "rev-parse", "--git-path", "hooks").strip())
    if not hooks.is_absolute():
        hooks = Path(repo).resolve() / hooks
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    exe = "puenteo"
    line = f'{exe} guard check || exit 1  {MARK}\n'
    if hook.exists():
        text = hook.read_text(encoding="utf-8")
        if MARK in text:
            return f"already installed: {hook}"
        hook.write_text(text.rstrip("\n") + "\n" + line, encoding="utf-8")
    else:
        hook.write_text("#!/bin/sh\n" + line, encoding="utf-8")
    hook.chmod(0o755)
    return f"installed: {hook}"


def uninstall(repo: str = ".") -> str:
    hook = Path(_git("-C", repo, "rev-parse", "--git-path", "hooks").strip())
    hook = (hook if hook.is_absolute() else Path(repo).resolve() / hook) / "pre-commit"
    if not hook.exists():
        return "not installed"
    lines = [l for l in hook.read_text(encoding="utf-8").splitlines(True) if MARK not in l]
    if "".join(lines).strip() in ("", "#!/bin/sh"):
        hook.unlink()
    else:
        hook.write_text("".join(lines), encoding="utf-8")
    return f"removed from {hook}"
