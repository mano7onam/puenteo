"""Running agent sessions on this machine: ``puenteo ps`` and ``puenteo whoami``.

Signals (all read-only, no network):

- **Claude Code**: ``~/.claude/sessions/<pid>.json`` (sessionId, cwd, status,
  name, procStart). Alive = pid exists *and* its start time matches procStart.
- **Codex**: ``~/.codex/thread-writer-locks/<thread>.lock`` held open by a
  running Codex process (checked with ``lsof`` on macOS/Linux).
- **Grok**: ``~/.grok/active_sessions.json``.
- **Junie**: ``~/.junie/instances/<hash>_<pid>.json``.
- **puenteo bus peers**: any agent that registered through ``puenteo mcp``,
  hooks, or ``puenteo join`` (heartbeat in bus.db) — this is how agents without
  native session files (Gemini, Cursor, OpenCode, …) still show up.

``whoami`` resolves the session *this process* belongs to: explicit env
(``PUENTEO_SESSION``), agent env vars (``CLAUDE_CODE_SESSION_ID``,
``CODEX_THREAD_ID``), then the parent-process chain against the files above.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class LiveSession:
    agent: str  # claude_code | codex | grok | junie | <any bus peer agent>
    session_id: str
    pid: Optional[int] = None
    cwd: str = ""
    name: str = ""
    status: str = ""  # busy | idle | "" (unknown)
    started_at: float = 0.0
    updated_at: float = 0.0
    source: str = ""  # where we learned about it
    delivery: List[str] = field(default_factory=list)  # how a message can reach it
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def address(self) -> str:
        return f"{_short_agent(self.agent)}:{self.session_id}"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["address"] = self.address
        return d


def _short_agent(agent: str) -> str:
    return {"claude_code": "claude"}.get(agent, agent)


def _home() -> Path:
    return Path(os.path.expanduser("~"))


# --------------------------------------------------------------------------- processes


def pid_alive(pid: Optional[int]) -> bool:
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            return str(pid) in out
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _ps_lstart(pid: int) -> str:
    if sys.platform == "win32":
        return ""
    try:
        # Claude Code records procStart in UTC; ask ps for UTC too.
        env = dict(os.environ, TZ="UTC", LC_ALL="C")
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5, env=env
        ).stdout
        return " ".join(out.split())
    except Exception:
        return ""


def _ps_lstart_many(pids: List[int]) -> Dict[int, str]:
    """One ``ps`` call for many pids → {pid: lstart (UTC)}."""
    if not pids or sys.platform == "win32":
        return {}
    env = dict(os.environ, TZ="UTC", LC_ALL="C")
    try:
        out = subprocess.run(
            ["ps", "-o", "pid=,lstart=", "-p", ",".join(str(p) for p in pids)],
            capture_output=True, text=True, timeout=5, env=env,
        ).stdout
    except Exception:
        return {}
    res = {}
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            res[int(parts[0])] = " ".join(parts[1].split())
    return res


def parent_pids(pid: Optional[int] = None, depth: int = 12) -> List[int]:
    """pid, ppid, grand-ppid, … (best effort, all platforms)."""
    chain: List[int] = []
    cur = pid or os.getpid()
    for _ in range(depth):
        if not cur or cur <= 1 or cur in chain:
            break
        chain.append(cur)
        cur = _ppid(cur)
    return chain


def _ppid(pid: int) -> int:
    if pid == os.getpid():
        return os.getppid()
    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{pid}/stat") as fh:
                return int(fh.read().rsplit(")", 1)[1].split()[1])
        except Exception:
            return 0
    if sys.platform == "win32":
        try:
            out = subprocess.run(
                ["wmic", "process", "where", f"ProcessId={pid}", "get", "ParentProcessId", "/value"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            for line in out.splitlines():
                if line.startswith("ParentProcessId="):
                    return int(line.split("=", 1)[1] or 0)
        except Exception:
            return 0
        return 0
    try:
        out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
        return int(out.stdout.strip() or 0)
    except Exception:
        return 0


# --------------------------------------------------------------------------- per-agent detectors


def _claude_sessions(check_start: bool = True) -> List[LiveSession]:
    d = _home() / ".claude" / "sessions"
    if not d.is_dir():
        return []
    out = []
    rows = []
    for f in d.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        pid = data.get("pid")
        if data.get("sessionId") and pid_alive(pid):
            rows.append((f, data))
    starts = _ps_lstart_many([int(r[1]["pid"]) for r in rows]) if check_start else {}
    for f, data in rows:
        pid = data.get("pid")
        sid = data.get("sessionId")
        if check_start and data.get("procStart"):
            ls = starts.get(int(pid), "")
            if ls and ls != " ".join(str(data["procStart"]).split()):
                continue  # pid was reused by another process
        upd = max(float(data.get("updatedAt") or 0), float(data.get("statusUpdatedAt") or 0)) / 1000.0
        upd = max(upd, _claude_transcript_mtime(str(sid), str(data.get("cwd") or "")))
        out.append(
            LiveSession(
                agent="claude_code",
                session_id=str(sid),
                pid=int(pid),
                cwd=str(data.get("cwd") or ""),
                name=str(data.get("name") or ""),
                status=str(data.get("status") or ""),
                started_at=float(data.get("startedAt") or 0) / 1000.0,
                updated_at=upd,
                source=str(f),
                delivery=["bus", "hook"],
                meta={k: data.get(k) for k in ("kind", "entrypoint", "version") if data.get(k)},
            )
        )
    return out


def _claude_transcript_mtime(sid: str, cwd: str) -> float:
    import re

    proj = _home() / ".claude" / "projects"
    cands = []
    if cwd:
        cands.append(proj / re.sub(r"[^A-Za-z0-9]", "-", cwd) / f"{sid}.jsonl")
    for c in cands:
        try:
            return c.stat().st_mtime
        except OSError:
            pass
    return 0.0


def _pids_matching(pattern: str) -> List[int]:
    try:
        out = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return []
    return [int(x) for x in out.split() if x.isdigit()]


def _lsof_holders(paths: List[str], *, among: Optional[List[int]] = None) -> Dict[str, int]:
    """
    path → pid holding it open (macOS/Linux, via lsof). {} when unavailable.

    ``among`` restricts lsof to those pids — 15x faster on macOS than a global scan.
    """
    if not paths or sys.platform == "win32":
        return {}
    cmd = ["lsof", "-n", "-P", "-w", "-F", "pn"]
    if among:
        cmd += ["-a", "-p", ",".join(str(p) for p in among)]
    try:
        out = subprocess.run(
            [*cmd, "--", *paths], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception:
        return {}
    holders: Dict[str, int] = {}
    pid = 0
    for line in out.splitlines():
        if line.startswith("p"):
            try:
                pid = int(line[1:])
            except ValueError:
                pid = 0
        elif line.startswith("n") and pid:
            holders[line[1:]] = pid
    return holders


def _codex_sessions() -> List[LiveSession]:
    d = _home() / ".codex" / "thread-writer-locks"
    if not d.is_dir():
        return []
    locks = [str(p) for p in d.glob("*.lock")]
    if not locks:
        return []
    pids = _pids_matching("codex") if sys.platform != "win32" else []
    holders = _lsof_holders(locks, among=pids or None)
    if not holders and locks and sys.platform != "win32":
        return []
    names = {}
    try:
        from .providers.codex import _thread_names

        names = _thread_names()
    except Exception:
        pass
    out = []
    for lock in locks:
        pid = holders.get(lock) or holders.get(os.path.realpath(lock))
        if sys.platform != "win32" and not pid:
            continue
        tid = Path(lock).stem
        row = names.get(tid) or {}
        try:
            mt = os.stat(lock).st_mtime
        except OSError:
            mt = 0.0
        out.append(
            LiveSession(
                agent="codex",
                session_id=tid,
                pid=pid or None,
                cwd=str(row.get("cwd") or ""),
                name=str(row.get("name") or row.get("title") or row.get("agent_nickname") or "")[:80],
                status="",
                started_at=mt,
                updated_at=mt,
                source=lock,
                delivery=["bus", "codex-queue", "hook"],
                meta={k: v for k, v in (("parent_id", row.get("parent_id")), ("nickname", row.get("agent_nickname"))) if v},
            )
        )
    return out


def _grok_sessions() -> List[LiveSession]:
    f = _home() / ".grok" / "active_sessions.json"
    if not f.is_file():
        return []
    try:
        rows = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        pid = r.get("pid")
        if not r.get("session_id") or not pid_alive(pid):
            continue
        out.append(
            LiveSession(
                agent="grok",
                session_id=str(r["session_id"]),
                pid=int(pid),
                cwd=str(r.get("cwd") or ""),
                source=str(f),
                delivery=["bus"],
            )
        )
    return out


def _junie_sessions() -> List[LiveSession]:
    d = _home() / ".junie" / "instances"
    if not d.is_dir():
        return []
    out = []
    for f in d.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        pid = data.get("pid")
        if not pid_alive(pid):
            continue
        out.append(
            LiveSession(
                agent="junie",
                session_id=f.stem,
                pid=int(pid),
                cwd=str(data.get("project_path") or ""),
                source=str(f),
                delivery=["bus"],
            )
        )
    return out


def _copilot_sessions() -> List[LiveSession]:
    """Copilot CLI: ``session-state/<id>/inuse.<pid>.lock`` (stale locks pile up — check the pid)."""
    root = _home() / ".copilot" / "session-state"
    if not root.is_dir():
        return []
    out = []
    locks = []
    try:
        # scandir is ~10x faster than glob over thousands of session dirs
        horizon = time.time() - 3 * 86400  # creating inuse.<pid>.lock bumps the dir mtime
        for d in os.scandir(root):
            if not d.is_dir(follow_symlinks=False):
                continue
            try:
                if d.stat().st_mtime < horizon:
                    continue
            except OSError:
                continue
            for f in os.scandir(d.path):
                if f.name.startswith("inuse.") and f.name.endswith(".lock"):
                    locks.append(Path(f.path))
    except OSError:
        return []
    for lock in locks:
        try:
            pid = int(lock.name.split(".")[1])
        except (IndexError, ValueError):
            continue
        if not pid_alive(pid):
            continue
        d = lock.parent
        meta = {}
        try:
            from .providers.copilot import _yaml

            meta = _yaml(d / "workspace.yaml")
        except Exception:
            pass
        try:
            mt = (d / "events.jsonl").stat().st_mtime
        except OSError:
            mt = 0.0
        out.append(
            LiveSession(
                agent="copilot",
                session_id=meta.get("id") or d.name,
                pid=pid,
                cwd=meta.get("cwd", ""),
                name=meta.get("name", ""),
                updated_at=mt,
                source=str(lock),
                delivery=["bus"],
            )
        )
    return out


def _bus_peers() -> List[LiveSession]:
    try:
        from . import bus

        return [p.as_live() for p in bus.Bus().peers(alive_only=True)]
    except Exception:
        return []


DETECTORS = {
    "claude_code": _claude_sessions,
    "codex": _codex_sessions,
    "grok": _grok_sessions,
    "junie": _junie_sessions,
    "copilot": _copilot_sessions,
}


_LIVE_TTL_S = 5.0
_live_cache: Dict[Any, Any] = {}


def _native_cached() -> List[LiveSession]:
    """Native detectors are slow-ish (ps/lsof); share results for a few seconds across processes."""
    now = time.time()
    hit = _live_cache.get("native")
    if hit and now - hit[0] < _LIVE_TTL_S:
        return hit[1]
    rows: Optional[List[LiveSession]] = None
    cache_file = None
    try:
        from .paths import cache_dir

        cache_file = cache_dir() / "live.json"
        st = cache_file.stat()
        if now - st.st_mtime < _LIVE_TTL_S:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            rows = [LiveSession(**{k: v for k, v in d.items() if k != "address"}) for d in data]
    except Exception:
        rows = None
    if rows is None:
        rows = []
        for fn in DETECTORS.values():
            try:
                rows.extend(fn())
            except Exception:
                continue
        if cache_file is not None:
            try:
                tmp = cache_file.with_suffix(f".{os.getpid()}.tmp")
                tmp.write_text(json.dumps([r.to_dict() for r in rows], ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, cache_file)
            except Exception:
                pass
    _live_cache["native"] = (now, rows)
    return rows


def live_sessions(
    *,
    agents: Optional[List[str]] = None,
    cwd: Optional[str] = None,
    include_bus: bool = True,
    fresh: bool = False,
) -> List[LiveSession]:
    """Every running session we can see, newest activity first, deduped by address."""
    from .providers import normalize_provider_name
    from .util import cwd_matches

    want = {normalize_provider_name(a) for a in agents} if agents else None
    if fresh:
        _live_cache.pop("native", None)
        try:
            from .paths import cache_dir

            (cache_dir() / "live.json").unlink()
        except Exception:
            pass
    found: Dict[str, LiveSession] = {}
    import copy

    for s in _native_cached():
        if want and s.agent not in want:
            continue
        found.setdefault(s.address, copy.deepcopy(s))
    if include_bus:
        for p in _bus_peers():
            if want and normalize_provider_name(p.agent) not in want:
                continue
            cur = found.get(p.address)
            if cur:
                # native detector knows pid/status; bus knows name + that it reads the inbox
                cur.delivery = sorted(set(cur.delivery) | set(p.delivery))
                cur.name = cur.name or p.name
                cur.meta.setdefault("bus_name", p.name)
                cur.updated_at = max(cur.updated_at, p.updated_at)
            else:
                found[p.address] = p
    out = list(found.values())
    if cwd:
        out = [s for s in out if cwd_matches(cwd, s.cwd)]
    out.sort(key=lambda s: (s.updated_at or s.started_at), reverse=True)
    return out


# --------------------------------------------------------------------------- whoami


_ENV_HINTS = (
    ("PUENTEO_SESSION", None),
    ("CLAUDE_CODE_SESSION_ID", "claude_code"),
    ("CODEX_THREAD_ID", "codex"),
    ("CODEX_SESSION_ID", "codex"),
    ("GROK_SESSION_ID", "grok"),
    ("PI_SESSION_ID", "pi"),
    ("OPENCODE_SESSION_ID", "opencode"),
    ("GEMINI_SESSION_ID", "gemini"),
)


@dataclass
class Me:
    agent: str
    session_id: str
    cwd: str = ""
    pid: Optional[int] = None
    how: str = ""
    name: str = ""

    @property
    def address(self) -> str:
        return f"{_short_agent(self.agent)}:{self.session_id}"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["address"] = self.address
        return d


def _guess_agent_from_env() -> str:
    if os.environ.get("PUENTEO_AGENT"):
        return os.environ["PUENTEO_AGENT"]
    if os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_ENTRYPOINT"):
        return "claude_code"
    if any(k.startswith("CODEX_") for k in os.environ):
        return "codex"
    ai = os.environ.get("AI_AGENT", "")
    if ai:
        return ai.split("_", 1)[0].replace("claude-code", "claude_code")
    return ""


_whoami_cache: Dict[Any, Optional[Me]] = {}


def whoami(*, pid: Optional[int] = None) -> Optional[Me]:
    """Which agent session is this process running inside? None when unknown."""
    key = (pid or os.getpid(), tuple(os.environ.get(v, "") for v, _ in _ENV_HINTS))
    if key in _whoami_cache:
        return _whoami_cache[key]
    me = _whoami(pid)
    _whoami_cache[key] = me
    return me


_AGENT_EXES = {
    "claude": "claude_code",
    "codex": "codex",
    "CodexCLI": "codex",
    "grok": "grok",
    "gemini": "gemini",
    "qwen": "qwen",
    "opencode": "opencode",
    "copilot": "copilot",
    "cursor-agent": "cursor",
    "goose": "goose",
    "aider": "aider",
    "pi": "pi",
    "junie": "junie",
}


def _proc_name(pid: int) -> str:
    """Executable basename of ``pid`` ("" if unknown)."""
    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                argv = fh.read().split(b"\0")
            return _exe_label([a.decode("utf-8", "replace") for a in argv if a])
        except Exception:
            return ""
    if sys.platform == "win32":
        return ""
    try:
        out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
        return _exe_label(out.stdout.strip().split())
    except Exception:
        return ""


def _exe_label(argv: List[str]) -> str:
    if not argv:
        return ""
    base = os.path.basename(argv[0])
    # node/python launchers: look at the script (e.g. node …/bin/gemini)
    if base in ("node", "bun", "deno", "python", "python3") and len(argv) > 1:
        for a in argv[1:3]:
            b = os.path.basename(a)
            if b in _AGENT_EXES or b.split(".")[0] in _AGENT_EXES:
                return b.split(".")[0]
    return base


def nearest_agent(pid: Optional[int] = None) -> Optional[tuple]:
    """(agent, pid) of the closest ancestor process that is a known coding agent."""
    for p in parent_pids(pid)[1:]:
        name = _proc_name(p)
        agent = _AGENT_EXES.get(name)
        if agent:
            return agent, p
    return None


def _whoami(pid: Optional[int]) -> Optional[Me]:
    cwd = os.getcwd()
    explicit = os.environ.get("PUENTEO_SESSION", "").strip()
    if explicit and pid is None:
        if ":" in explicit:
            a, sid = explicit.split(":", 1)
            from .providers import normalize_provider_name

            return Me(agent=normalize_provider_name(a), session_id=sid, cwd=cwd, how="PUENTEO_SESSION")
        return Me(agent=_guess_agent_from_env() or "unknown", session_id=explicit, cwd=cwd, how="PUENTEO_SESSION")

    chain = parent_pids(pid)
    near = nearest_agent(pid)
    near_agent = near[0] if near else None

    # Agent env vars are inherited by every child — including *other* agents
    # started from inside a session. Trust one only if that agent is our
    # nearest agent ancestor (or we can't tell).
    if pid is None:
        for var, agent in _ENV_HINTS[1:]:
            val = os.environ.get(var, "").strip()
            if not val:
                continue
            if near_agent and near_agent != agent:
                continue
            if agent == "claude_code" and near and os.environ.get("CLAUDE_PID"):
                try:
                    if int(os.environ["CLAUDE_PID"]) != near[1]:
                        continue
                except ValueError:
                    pass
            return Me(agent=agent, session_id=val, cwd=cwd, how=var)

    # Claude: <pid>.json for any ancestor (MCP servers and hooks are children of claude)
    d = _home() / ".claude" / "sessions"
    for p in chain:
        if near and near_agent != "claude_code" and p == near[1]:
            break  # reached a non-Claude agent: anything above it belongs to another session
        f = d / f"{p}.json"
        if f.is_file():
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("sessionId"):
                    return Me(
                        agent="claude_code",
                        session_id=str(data["sessionId"]),
                        cwd=str(data.get("cwd") or cwd),
                        pid=p,
                        how="claude-pidfile",
                        name=str(data.get("name") or ""),
                    )
            except Exception:
                pass
    # Grok: active_sessions.json pid
    for s in _grok_sessions():
        if s.pid in chain:
            return Me(agent="grok", session_id=s.session_id, cwd=s.cwd or cwd, pid=s.pid, how="grok-active")
    # Codex: an ancestor holds a thread lock — only unambiguous if it holds one
    try:
        codex = [s for s in _codex_sessions() if s.pid in chain]
        if len(codex) == 1:
            s = codex[0]
            return Me(agent="codex", session_id=s.session_id, cwd=s.cwd or cwd, pid=s.pid, how="codex-lock")
        if len(codex) > 1:
            # app-server hosts many threads: pick the one in our cwd, most recently touched
            from .util import cwd_matches

            here = [s for s in codex if cwd_matches(s.cwd, cwd)] or codex
            here.sort(key=lambda s: s.updated_at, reverse=True)
            s = here[0]
            return Me(agent="codex", session_id=s.session_id, cwd=s.cwd or cwd, pid=s.pid, how="codex-lock-guess")
        if near_agent == "codex":
            # codex exec / fresh thread: newest rollout in this cwd written by our codex process
            s = _newest_codex_rollout_for(near[1], cwd)
            if s:
                return Me(agent="codex", session_id=s, cwd=cwd, pid=near[1], how="codex-rollout")
    except Exception:
        pass
    return None


def _newest_codex_rollout_for(codex_pid: int, cwd: str) -> Optional[str]:
    """Thread id of the rollout file the given codex process has open (lsof)."""
    if sys.platform == "win32":
        return None
    try:
        out = subprocess.run(["lsof", "-p", str(codex_pid), "-F", "n"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return None
    import re

    for line in out.splitlines():
        if line.startswith("n") and "/.codex/sessions/" in line and line.endswith(".jsonl"):
            m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", line)
            if m:
                return m.group(1)
    return None


def describe(s: LiveSession) -> str:
    age = ""
    ts = s.updated_at or s.started_at
    if ts:
        mins = int((time.time() - ts) / 60)
        age = f"{mins}m" if mins < 120 else f"{mins // 60}h"
    return f"{s.address}  {s.status or '-'}  {age}  {s.cwd}  {s.name}"
