"""``puenteo install`` — put the skills, MCP server and (opt-in) hooks into every agent.

Design rules:
- Idempotent: running twice changes nothing the second time.
- Minimal edits: we only add/replace *our own* ``puenteo`` entry; every file we
  touch is backed up first (``<file>.puenteo-bak``) and written atomically.
- Prefer the agent's own CLI (``claude mcp add``, ``codex mcp add``) when present.
- ``--dry-run`` prints the plan without touching anything; ``uninstall`` reverses it.
- Hooks change agent behaviour, so they are only installed with ``--hooks``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

SKILLS = ("puenteo", "puenteo-bus")
HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop")
MCP_NAME = "puenteo"


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def skills_source() -> Path:
    return Path(__file__).resolve().parent / "data" / "skills"


def mcp_command() -> List[str]:
    """How agents should launch our MCP server: the installed console script, else this interpreter."""
    exe = shutil.which("puenteo")
    if exe:
        return [exe, "mcp"]
    return [sys.executable, "-m", "puenteo", "mcp"]


HOOK_MARK = "PUENTEO_HOOK=1"


def hook_command(event: str, agent: str) -> str:
    base = mcp_command()[:-1]  # drop "mcp"
    args = [*base, "hook", event, "--agent", agent]
    if sys.platform == "win32":
        return subprocess.list2cmdline(args)
    return HOOK_MARK + " " + " ".join(_q(x) for x in args)


def _q(s: str) -> str:
    return s if re.match(r"^[\w@%+=:,./-]+$", s) else "'" + s.replace("'", "'\"'\"'") + "'"


# ----------------------------------------------------------------------------- file helpers


def _backup(path: Path) -> None:
    """Keep the pristine pre-puenteo copy, plus a backup of the state before *this* run."""
    if path.exists():
        bak = path.with_name(path.name + ".puenteo-bak")
        if not bak.exists():
            shutil.copy2(path, bak)
        shutil.copy2(path, path.with_name(path.name + ".puenteo-prev"))


def _atomic_write(path: Path, text: str) -> None:
    # Write through symlinks (dotfile managers) and keep the original file mode.
    path = Path(os.path.realpath(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = None
    try:
        mode = path.stat().st_mode & 0o7777
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, mode if mode is not None else 0o644)
    os.replace(tmp, path)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        # JSONC (comments / trailing commas): we refuse rather than silently drop the user's comments
        raise ValueError(f"{path} is not plain JSON ({e.msg}); add the puenteo entry by hand") from None


def _edit_json(path: Path, fn: Callable[[dict], bool], dry: bool) -> bool:
    """Apply fn(data) → changed?; back up + write when changed."""
    data = _load_json(path)
    before = json.dumps(data, sort_keys=True)
    fn(data)
    if json.dumps(data, sort_keys=True) == before:
        return False
    if not dry:
        _backup(path)
        _atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return True


# ----------------------------------------------------------------------------- targets


@dataclass
class Step:
    agent: str
    kind: str  # skill | mcp | hooks
    target: str
    action: str = ""  # added | updated | unchanged | removed | skipped: …
    detail: str = ""


@dataclass
class Agent:
    name: str
    present: Callable[[], bool]
    skill_dirs: List[Path] = field(default_factory=list)
    mcp: Optional[Callable[[bool, bool], Step]] = None  # (dry, remove) → Step
    hooks: Optional[Callable[[bool, bool], Step]] = None


def _mcp_json_servers(path: Path, key: str = "mcpServers") -> Callable[[bool, bool], Step]:
    """Generic ``{"mcpServers": {"puenteo": {command, args}}}`` config file."""

    def run(dry: bool, remove: bool) -> Step:
        cmd = mcp_command()
        entry = {"command": cmd[0], "args": cmd[1:]}

        def fn(d: dict) -> bool:
            servers = d.setdefault(key, {})
            if remove:
                servers.pop(MCP_NAME, None)
                if not servers:
                    d.pop(key, None)
            else:
                servers[MCP_NAME] = {**servers.get(MCP_NAME, {}), **entry}
            return True

        changed = _edit_json(path, fn, dry)
        return Step("", "mcp", str(path), ("removed" if remove else "added/updated") if changed else "unchanged")

    return run


def _opencode_mcp(path: Path) -> Callable[[bool, bool], Step]:
    def run(dry: bool, remove: bool) -> Step:
        def fn(d: dict) -> bool:
            m = d.setdefault("mcp", {})
            if remove:
                m.pop(MCP_NAME, None)
                if not m:
                    d.pop("mcp", None)
            else:
                m[MCP_NAME] = {"type": "local", "command": mcp_command(), "enabled": True}
            return True

        changed = _edit_json(path, fn, dry)
        return Step("", "mcp", str(path), ("removed" if remove else "added/updated") if changed else "unchanged")

    return run


def _cli_mcp(exe: str, add: List[str], remove: List[str], get: List[str]) -> Callable[[bool, bool], Step]:
    def run(dry: bool, rm: bool) -> Step:
        path = shutil.which(exe)
        if not path:
            return Step("", "mcp", exe, "skipped: CLI not on PATH")
        have = subprocess.run([path, *get], capture_output=True, text=True, timeout=30).returncode == 0
        if rm:
            if not have:
                return Step("", "mcp", f"{exe} mcp", "unchanged")
            if not dry:
                subprocess.run([path, *remove], capture_output=True, text=True, timeout=30)
            return Step("", "mcp", f"{exe} mcp", "removed")
        if have:
            out = subprocess.run([path, *get], capture_output=True, text=True, timeout=30).stdout
            if " ".join(mcp_command()) in " ".join(out.split()) or mcp_command()[0] in out:
                return Step("", "mcp", f"{exe} mcp", "unchanged")
            if not dry:
                subprocess.run([path, *remove], capture_output=True, text=True, timeout=30)
        if not dry:
            r = subprocess.run([path, *add], capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                return Step("", "mcp", f"{exe} mcp", f"failed: {(r.stderr or r.stdout).strip()[:200]}")
        return Step("", "mcp", f"{exe} mcp add", "updated" if have else "added")

    return run


def _claude_hooks(path: Path, agent: str = "claude") -> Callable[[bool, bool], Step]:
    """Claude Code / Codex hooks.json style: {"hooks": {Event: [{matcher?, hooks:[{type,command}]}]}}."""

    def run(dry: bool, remove: bool) -> Step:
        def fn(d: dict) -> bool:
            hooks = d.setdefault("hooks", {})
            for ev in HOOK_EVENTS:
                groups = [g for g in hooks.get(ev, []) if not _is_ours(g)]
                if not remove:
                    entry = {"type": "command", "command": hook_command(ev, agent), "timeout": 10}
                    groups.append({"hooks": [entry]} if ev != "PostToolUse" else {"matcher": "*", "hooks": [entry]})
                if groups:
                    hooks[ev] = groups
                else:
                    hooks.pop(ev, None)
            if not hooks:
                d.pop("hooks", None)
            return True

        changed = _edit_json(path, fn, dry)
        return Step("", "hooks", str(path), ("removed" if remove else "added/updated") if changed else "unchanged")

    return run


def _is_ours(group: dict) -> bool:
    """Only groups whose every hook is a puenteo hook command we generated."""
    hooks = (group or {}).get("hooks", []) or []
    if not hooks:
        return False
    for h in hooks:
        cmd = str(h.get("command", ""))
        if not (cmd.startswith(HOOK_MARK) or re.search(r"puenteo(?:\.exe)?\"?\s+hook\s+(SessionStart|UserPromptSubmit|PostToolUse|Stop)\s+--agent\s", cmd)):
            return False
    return True


def agents() -> List[Agent]:
    h = _home()
    shared = h / ".agents" / "skills"  # cross-vendor skills dir (Codex, Cursor, Copilot, Gemini, OpenCode, Amp, Goose)
    claude_cmd = mcp_command()
    return [
        Agent(
            "claude",
            lambda: (h / ".claude").is_dir(),
            [h / ".claude" / "skills"],
            _cli_mcp(
                "claude",
                ["mcp", "add", "-s", "user", MCP_NAME, "--", *claude_cmd],
                ["mcp", "remove", "-s", "user", MCP_NAME],
                ["mcp", "get", MCP_NAME],
            ),
            _claude_hooks(h / ".claude" / "settings.json", "claude"),
        ),
        Agent(
            "codex",
            lambda: (h / ".codex").is_dir(),
            [shared],
            _cli_mcp(
                "codex",
                ["mcp", "add", MCP_NAME, "--", *claude_cmd],
                ["mcp", "remove", MCP_NAME],
                ["mcp", "get", MCP_NAME],
            ),
            _claude_hooks(h / ".codex" / "hooks.json", "codex"),
        ),
        Agent("gemini", lambda: (h / ".gemini" / "settings.json").exists() or shutil.which("gemini") is not None,
              [shared], _mcp_json_servers(h / ".gemini" / "settings.json")),
        Agent("qwen", lambda: (h / ".qwen").is_dir(), [h / ".qwen" / "skills"], _mcp_json_servers(h / ".qwen" / "settings.json")),
        Agent("cursor", lambda: (h / ".cursor").is_dir(), [shared], _mcp_json_servers(h / ".cursor" / "mcp.json")),
        Agent("opencode", lambda: (h / ".config" / "opencode").is_dir(), [shared],
              _opencode_mcp(h / ".config" / "opencode" / "opencode.json")),
        Agent("copilot", lambda: (h / ".copilot").is_dir(), [shared], _mcp_json_servers(h / ".copilot" / "mcp-config.json")),
        Agent("grok", lambda: (h / ".grok").is_dir(), [h / ".grok" / "skills"]),
        Agent("pi", lambda: (h / ".pi").is_dir(), [shared]),
        Agent("antigravity", lambda: (h / ".gemini" / "antigravity").is_dir(), [shared],
              _mcp_json_servers(h / ".gemini" / "antigravity" / "mcp_config.json")),
    ]


# ----------------------------------------------------------------------------- skills


def _install_skill(dest_root: Path, name: str, dry: bool, remove: bool) -> str:
    src = skills_source() / name
    dest = dest_root / name
    if remove:
        if dest.is_symlink() or dest.exists():
            if not _owned(dest):
                return "skipped: not installed by puenteo"
            if not dry:
                if dest.is_symlink() or dest.is_file():
                    dest.unlink()
                else:
                    shutil.rmtree(dest)
            return "removed"
        return "unchanged"
    if dest.is_symlink() and not dest.exists():
        if not dry:
            dest.unlink()  # dangling link from an old checkout (agent-session-bridge, …)
        status = "replaced broken symlink"
    elif dest.is_symlink():
        if not _owned(dest):
            return "skipped: foreign symlink"
        status = "updated"
    elif dest.exists() and not _owned(dest):
        return "skipped: exists, not installed by puenteo"
    elif dest.exists():
        if (dest / "SKILL.md").read_bytes() == (src / "SKILL.md").read_bytes():
            return "unchanged"
        status = "updated"
    else:
        status = "added"
    if not dry:
        dest_root.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink():
            dest.unlink()
        elif dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        (dest / ".puenteo").write_text("installed by puenteo install\n", encoding="utf-8")
    return status


def _owned(dest: Path) -> bool:
    if dest.is_symlink():
        return "puenteo" in os.readlink(dest) or "agent-session-bridge" in os.readlink(dest)
    return (dest / ".puenteo").exists()


# ----------------------------------------------------------------------------- driver


def plan_and_apply(
    *,
    only: Optional[List[str]] = None,
    skills: bool = True,
    mcp: bool = True,
    hooks: bool = False,
    dry: bool = False,
    remove: bool = False,
    legacy_cleanup: bool = True,
) -> List[Step]:
    steps: List[Step] = []
    seen_dirs = set()
    for ag in agents():
        if only and ag.name not in only:
            continue
        if not ag.present():
            if only:
                steps.append(Step(ag.name, "-", "-", "skipped: agent not found"))
            continue
        if skills:
            for d in ag.skill_dirs:
                if d in seen_dirs:
                    continue
                seen_dirs.add(d)
                for name in SKILLS:
                    steps.append(Step(ag.name, "skill", str(d / name), _install_skill(d, name, dry, remove)))
                if legacy_cleanup:
                    # stale symlinks from the pre-rename package
                    old = d / "agent-session-bridge"
                    if old.is_symlink() and not old.exists():
                        if not dry:
                            old.unlink()
                        steps.append(Step(ag.name, "skill", str(old), "removed broken legacy symlink"))
        if mcp and ag.mcp:
            try:
                st = ag.mcp(dry, remove)
            except Exception as e:
                st = Step("", "mcp", "?", f"failed: {e}")
            st.agent = ag.name
            steps.append(st)
        if hooks and ag.hooks:
            try:
                st = ag.hooks(dry, remove)
            except Exception as e:
                st = Step("", "hooks", "?", f"failed: {e}")
            st.agent = ag.name
            steps.append(st)
    # Codex still reads the legacy ~/.codex/skills; drop our old copies there to avoid duplicates
    if skills and legacy_cleanup and (not only or "codex" in only):
        legacy = _home() / ".codex" / "skills"
        for name in (*SKILLS, "agent-session-bridge"):
            p = legacy / name
            if (p.is_symlink() or p.exists()) and _owned(p):
                if not dry:
                    p.unlink() if p.is_symlink() else shutil.rmtree(p)
                steps.append(Step("codex", "skill", str(p), "removed legacy duplicate"))
    return steps


def render(steps: List[Step], *, dry: bool) -> str:
    if not steps:
        return "No supported agents found."
    lines = [("DRY RUN — nothing changed\n" if dry else "")]
    w = max(len(s.agent) for s in steps)
    for s in steps:
        lines.append(f"  {s.agent:{w}}  {s.kind:6} {s.action:28} {s.target}")
    lines.append("")
    lines.append("Restart running agent sessions (or start new ones) to pick up skills and MCP.")
    return "\n".join(lines)
