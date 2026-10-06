"""Structured facts mined from a session's tool calls (not just its prose).

What another agent needs to continue someone's work is rarely in the chat
text alone: it's *which files were touched*, *what the last plan/TODO looked
like*, *which commands ran and failed*, *what got committed*. Claude Code and
Codex record all of that as tool calls; we read them from the raw log.

Other providers fall back to regex over message text (paths, ``git commit``).
"""

from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .models import Session


@dataclass
class Signals:
    files: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # path → {ops:set, count, last}
    commands: List[Dict[str, Any]] = field(default_factory=list)  # {cmd, ok, ts}
    commits: List[Dict[str, Any]] = field(default_factory=list)  # {message, ts}
    plan: List[Dict[str, str]] = field(default_factory=list)  # last TODO/plan: {text, status}
    errors: List[Dict[str, Any]] = field(default_factory=list)  # {text, ts}
    final_message: str = ""  # last assistant message of the last completed turn
    branch: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["files"] = {k: {**v, "ops": sorted(v["ops"])} for k, v in self.files.items()}
        return d

    # -- recording helpers
    def touch(self, path: str, op: str, ts: str = "") -> None:
        if not path:
            return
        f = self.files.setdefault(path, {"ops": set(), "count": 0, "last": ""})
        f["ops"].add(op)
        f["count"] += 1
        f["last"] = ts or f["last"]

    def command(self, cmd: str, ok: Optional[bool], ts: str = "") -> None:
        raw_cmd = cmd or ""
        cmd = " ".join(raw_cmd.split())
        if not cmd:
            return
        self.commands.append({"cmd": cmd[:400], "ok": ok, "ts": ts})
        # Subjects come from git's output ([branch sha] subject) when available; this
        # covers `git commit -q -m …` and `-F - <<EOF` heredocs, which print nothing.
        for seg in re.finditer(r"\bgit\s+(?:-C\s+\S+\s+)?commit\b((?:\"[^\"]*\"|'[^']*'|[^;&|\n\"'])*)", raw_cmd):
            args = seg.group(1)
            subj = ""
            m = re.search(r"(?:^|\s)-?-?(?:\w*m|message)[= ]?\s*(?:\"([^\"]+)\"|'([^']+)')", args)
            if m:
                subj = m.group(1) or m.group(2) or m.group(3) or ""
            elif re.search(r"(?:-F|--file)[= ]\s*-", args):
                h = re.search(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n(.*?)\n\s*\1\s*$", raw_cmd[seg.start():], re.S | re.M)
                if h:
                    subj = h.group(2).strip().splitlines()[0] if h.group(2).strip() else ""
            # skip failed commits and our own parser source showing up inside heredocs
            if subj and ok is not False and not subj.lstrip().startswith(("if ", "for ", "re.", "m = ")):
                self.commits.append({"message": subj.splitlines()[0][:200], "ts": ts, "ok": ok})
        b = re.search(r"git\s+(?:checkout\s+-b|switch\s+-c|checkout|switch)\s+([\w./-]+)", cmd)
        if b and not b.group(1).startswith("-"):
            self.branch = b.group(1)

    def error(self, text: str, ts: str = "") -> None:
        t = " ".join((text or "").split())
        if t:
            self.errors.append({"text": t[:500], "ts": ts})


_EDIT_TOOLS = {"Edit": "edit", "MultiEdit": "edit", "Write": "write", "NotebookEdit": "edit"}
_ERR_RE = re.compile(r"(Traceback \(most recent call last\)|\bError:|\bFAILED\b|\bfatal:|exit code [1-9]|Exception:)")
_PATCH_FILE_RE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$", re.M)


def collect(session: Session, *, max_lines: int = 400_000) -> Signals:
    p = (session.provider or "").lower()
    try:
        if p in ("claude_code", "claude"):
            return _claude(session.path, max_lines)
        if p == "codex":
            return _codex(session.path, max_lines)
    except Exception:
        pass
    return _from_text(session)


def _claude(path: str, max_lines: int) -> Signals:
    sig = Signals()
    pending: Dict[str, Dict[str, Any]] = {}  # tool_use_id → {name, input}
    last_text = ""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            if i > max_lines:
                break
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("gitBranch"):
                sig.branch = str(o["gitBranch"])
            msg = o.get("message") or {}
            content = msg.get("content")
            ts = str(o.get("timestamp") or "")
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text" and msg.get("role") == "assistant" and not o.get("isMeta"):
                    t = (b.get("text") or "").strip()
                    if t:
                        last_text = t
                elif bt == "tool_use":
                    name = b.get("name") or ""
                    inp = b.get("input") or {}
                    pending[b.get("id") or ""] = {"name": name, "input": inp, "ts": ts}
                    if name in _EDIT_TOOLS:
                        sig.touch(str(inp.get("file_path") or inp.get("notebook_path") or ""), _EDIT_TOOLS[name], ts)
                    elif name == "TodoWrite":
                        todos = inp.get("todos") or []
                        sig.plan = [
                            {"text": str(t.get("content") or t.get("activeForm") or ""), "status": str(t.get("status") or "")}
                            for t in todos if isinstance(t, dict)
                        ]
                elif bt == "tool_result":
                    call = pending.pop(b.get("tool_use_id") or "", None)
                    is_err = bool(b.get("is_error"))
                    text = _text(b.get("content"))
                    if call and call["name"] == "Bash":
                        cmd = str((call["input"] or {}).get("command") or "")
                        sig.command(cmd, not is_err and not _ERR_RE.search(text[:4000]), call["ts"])
                        _commit_from_output(sig, text, ts)
                    if is_err or (_ERR_RE.search(text[:4000]) and call and call["name"] == "Bash"):
                        sig.error(_error_excerpt(text), ts)
    sig.final_message = last_text
    return sig


def _codex(path: str, max_lines: int) -> Signals:
    sig = Signals()
    calls: Dict[str, Dict[str, Any]] = {}
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            if i > max_lines:
                break
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o.get("type")
            pl = o.get("payload") or {}
            ts = str(o.get("timestamp") or "")
            if t == "turn_context" and pl.get("git_branch"):
                sig.branch = str(pl["git_branch"])
            if t == "response_item":
                pt = pl.get("type")
                if pt in ("function_call", "custom_tool_call"):
                    name = pl.get("name") or ""
                    raw = pl.get("arguments") if pl.get("arguments") is not None else pl.get("input")
                    args = _maybe_json(raw)
                    calls[pl.get("call_id") or pl.get("id") or ""] = {"name": name, "args": args, "ts": ts}
                    if name == "apply_patch" or (isinstance(raw, str) and "*** Begin Patch" in raw):
                        for op, f in _PATCH_FILE_RE.findall(str(raw)):
                            sig.touch(f.strip(), {"Add": "write", "Update": "edit", "Delete": "delete"}[op], ts)
                    elif name == "update_plan" and isinstance(args, dict):
                        sig.plan = [
                            {"text": str(s.get("step") or ""), "status": str(s.get("status") or "")}
                            for s in args.get("plan") or [] if isinstance(s, dict)
                        ]
                elif pt in ("function_call_output", "custom_tool_call_output"):
                    call = calls.pop(pl.get("call_id") or "", None)
                    out = _text(pl.get("output"))
                    if call and call["name"] in ("shell", "exec_command", "container.exec", "local_shell"):
                        sig.command(_codex_cmd(call["args"]), not _ERR_RE.search(out[:4000]), call["ts"])
                    if _ERR_RE.search(out[:4000]) and call and call["name"] in ("shell", "exec_command", "local_shell"):
                        sig.error(_error_excerpt(out), ts)
            elif t == "event_msg":
                et = pl.get("type")
                if et == "task_complete" and pl.get("last_agent_message"):
                    sig.final_message = str(pl["last_agent_message"])
                elif et == "item_completed":
                    _codex_item(sig, pl.get("item") or {}, ts)
    return sig


def _codex_item(sig: Signals, item: Dict[str, Any], ts: str) -> None:
    kind = item.get("type") or ""
    if kind == "CommandExecution":
        status = str(item.get("status") or "")
        code = item.get("exit_code")
        ok = None
        if code is not None:
            ok = code == 0
        elif status in ("completed", "failed", "declined"):
            ok = status == "completed"
        out = str(item.get("aggregated_output") or item.get("stdout") or "") + str(item.get("stderr") or "")
        sig.command(_codex_cmd(item.get("command")), ok, ts)
        _commit_from_output(sig, out, ts)
        if ok is False and out.strip():
            sig.error(_error_excerpt(out), ts)
    elif kind == "FileChange":
        ch = item.get("changes") or {}
        if isinstance(ch, dict):
            for path, info in ch.items():
                op = (info or {}).get("type") if isinstance(info, dict) else "edit"
                sig.touch(str(path), {"add": "write", "update": "edit", "delete": "delete"}.get(str(op), str(op or "edit")), ts)
        elif isinstance(ch, list):
            for c in ch:
                if isinstance(c, dict):
                    sig.touch(str(c.get("path") or ""), str(c.get("kind") or "edit"), ts)
    elif kind == "AgentMessage":
        text = _text(item.get("content"))
        if text.strip():
            sig.final_message = text.strip()
    elif kind == "McpToolCall" and str(item.get("status")) == "failed":
        sig.error(f"mcp {item.get('server')}/{item.get('tool')}: {_text((item.get('result') or {}).get('content'))}", ts)


_COMMIT_OUT_RE = re.compile(r"^\[([\w./-]+)(?: \(root-commit\))? ([0-9a-f]{7,12})\] (.+)$", re.M)


def _commit_from_output(sig: Signals, out: str, ts: str) -> None:
    """``git commit`` prints ``[branch sha] subject`` — reliable even for -F/heredoc commits."""
    for branch, sha, subject in _COMMIT_OUT_RE.findall(out or ""):
        if any(c.get("sha") == sha for c in sig.commits):
            continue
        # replace the command-derived entry for the same commit, if any
        for c in reversed(sig.commits):
            if not c.get("sha") and c["message"] == subject[:200]:
                c.update(sha=sha, branch=branch)
                break
        else:
            sig.commits.append({"message": subject[:200], "sha": sha, "branch": branch, "ts": ts})


_PATH_RE = re.compile(r"(?<![\w/.-])((?:~|/)[\w.@+-]+(?:/[\w.@+-]+){1,12}\.\w{1,8})\b")


def _from_text(session: Session) -> Signals:
    from .providers import load_transcript

    sig = Signals()
    tr = load_transcript(session, include_tools=True)
    for m in tr.messages:
        for p in _PATH_RE.findall(m.text or ""):
            if not p.startswith(("/tmp/", "/var/", "/private/", "/dev/")):  # nosec B108 - path filter, not a file write
                sig.touch(p, "mentioned", m.timestamp)
        for cmd in re.findall(r"git commit[^\n`]*", m.text or ""):
            sig.command(cmd, None, m.timestamp)
        if m.role == "assistant" and (m.text or "").strip():
            sig.final_message = m.text.strip()
    return sig


def _codex_cmd(args: Any) -> str:
    if isinstance(args, dict):
        c = args.get("command") or args.get("cmd") or ""
    else:
        c = args
    if isinstance(c, list):
        if len(c) >= 3 and c[0] in ("bash", "zsh", "sh") and c[1] in ("-lc", "-c"):
            return str(c[2])
        return " ".join(shlex.quote(str(x)) for x in c)
    return str(c or "")


def _maybe_json(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return raw
    return raw


def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_text(x.get("text") if isinstance(x, dict) else x) for x in content)
    if isinstance(content, dict):
        return _text(content.get("text") or content.get("output") or content.get("content") or "")
    return str(content)


def _error_excerpt(text: str, n: int = 400) -> str:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    for i, ln in enumerate(lines):
        if _ERR_RE.search(ln):
            return "\n".join(lines[max(0, i - 1): i + 4])[:n]
    return "\n".join(lines[-4:])[:n]


def short_path(path: str, cwd: str) -> str:
    if cwd and path.startswith(cwd.rstrip("/") + "/"):
        return path[len(cwd.rstrip("/")) + 1:]
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home) else path
