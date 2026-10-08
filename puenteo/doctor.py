"""``puenteo doctor`` — is everything wired up? (PATH, skills, MCP, hooks, bus, index)."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import List, Tuple

OK, WARN, FAIL = "ok", "warn", "fail"


def checks() -> List[Tuple[str, str, str]]:
    from . import index
    from .install import SKILLS, agents
    from .live import whoami
    from .paths import bus_db_path, index_db_path
    from .version import __version__

    out: List[Tuple[str, str, str]] = []
    exe = shutil.which("puenteo")
    out.append(("cli on PATH", OK if exe else WARN, exe or "puenteo not on PATH — agents can't run it (uv tool install puenteo)"))
    out.append(("version", OK, __version__))
    out.append(("sqlite fts5", OK if index.available() else WARN,
                sqlite3.sqlite_version + ("" if index.available() else " (no FTS5: search scans files)")))
    try:
        st = index.stats()
        out.append(("search index", OK, f"{st['sessions']} sessions, {st['messages']} messages, {st['bytes'] / 1e6:.0f} MB  {index_db_path()}"))
    except Exception as e:
        out.append(("search index", WARN, str(e)))
    try:
        from .bus import Bus

        with Bus() as b:
            n = len(b.peers(alive_only=True))
        out.append(("message bus", OK, f"{bus_db_path()}  ({n} live peer(s))"))
    except Exception as e:
        out.append(("message bus", FAIL, str(e)))
    try:
        from .mesh import service
        from .mesh.node import PEER_STALE_S, _db, node_name
        from .mesh.nostr import Identity
        import time as _t

        with Bus() as b:
            nm = node_name(b, Identity.load())
            online = _db(b).execute("SELECT COUNT(*) FROM mesh_nodes WHERE last_seen>=?", (_t.time() - PEER_STALE_S,)).fetchone()[0]
        if service.running():
            out.append(("mesh", OK, f"bridge running as {nm}, {online} online peer machine(s)"))
        else:
            out.append(("mesh", WARN, f"bridge not running (node {nm}); other machines can't reach your sessions → "
                                      "`puenteo mesh service install`"))
    except Exception as e:
        out.append(("mesh", WARN, str(e)))
    me = whoami()
    out.append(("whoami", OK if me else WARN, f"{me.address} via {me.how}" if me else "not inside an agent session (fine in a plain shell)"))

    for ag in agents():
        if not ag.present():
            continue
        for d in ag.skill_dirs:
            for name in SKILLS:
                p = d / name
                if p.is_symlink() and not p.exists():
                    out.append((f"{ag.name} skill {name}", FAIL, f"broken symlink {p} → run `puenteo install`"))
                elif (p / "SKILL.md").exists():
                    out.append((f"{ag.name} skill {name}", OK, str(p)))
                else:
                    out.append((f"{ag.name} skill {name}", WARN, f"missing in {d} → `puenteo install`"))
        out.append((f"{ag.name} mcp", *_mcp_state(ag.name)))
    return out


def _mcp_state(agent: str) -> Tuple[str, str]:
    h = Path(os.path.expanduser("~"))
    try:
        if agent in ("claude", "codex"):
            exe = shutil.which(agent)
            if not exe:
                return WARN, f"{agent} CLI not on PATH"
            r = subprocess.run([exe, "mcp", "get", "puenteo"], capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                return WARN, "not registered → `puenteo install`"
            txt = r.stdout
            if "Failed" in txt or "✗" in txt:
                return FAIL, " ".join(txt.split())[:160]
            return OK, "registered" + (" (connected)" if "Connected" in txt else "")
        files = {
            "gemini": h / ".gemini" / "settings.json",
            "qwen": h / ".qwen" / "settings.json",
            "cursor": h / ".cursor" / "mcp.json",
            "copilot": h / ".copilot" / "mcp-config.json",
            "antigravity": h / ".gemini" / "antigravity" / "mcp_config.json",
            "opencode": h / ".config" / "opencode" / "opencode.json",
        }
        f = files.get(agent)
        if not f:
            return OK, "n/a (skills only)"
        if not f.exists():
            return WARN, f"{f} missing → `puenteo install`"
        data = json.loads(f.read_text(encoding="utf-8") or "{}")
        entry = (data.get("mcp") or {}).get("puenteo") if agent == "opencode" else (data.get("mcpServers") or {}).get("puenteo")
        if not entry:
            return WARN, f"not in {f} → `puenteo install`"
        cmd = entry.get("command")
        cmd0 = cmd[0] if isinstance(cmd, list) else cmd
        if cmd0 and not (shutil.which(cmd0) or os.path.exists(cmd0)):
            return FAIL, f"command {cmd0} not found"
        return OK, str(f)
    except Exception as e:
        return WARN, str(e)


def render(rows: List[Tuple[str, str, str]]) -> str:
    icon = {OK: "✓", WARN: "!", FAIL: "✗"}
    w = max(len(r[0]) for r in rows)
    lines = [f" {icon[s]} {name:{w}}  {detail}" for name, s, detail in rows]
    bad = sum(1 for r in rows if r[1] == FAIL)
    warn = sum(1 for r in rows if r[1] == WARN)
    lines.append("")
    lines.append(f"{bad} problem(s), {warn} warning(s)." + ("  Fix most with: puenteo install" if bad or warn else ""))
    return "\n".join(lines)
