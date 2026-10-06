"""Shared fixtures: a fake $HOME with tiny, realistic agent stores."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _jsonl(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


CLAUDE_SID = "11111111-aaaa-4bbb-8ccc-000000000001"
CODEX_ROOT = "01a0fe44-d146-7000-8000-000000000001"
CODEX_SUB = "01a0fe44-f210-7000-8000-000000000002"


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    proj = home / "dev" / "my_app"
    proj.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PUENTEO_HOME", str(tmp_path / "puenteo"))
    monkeypatch.setenv("PUENTEO_QUIET", "1")
    for var in (
        "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "PUENTEO_SESSION",
        "PUENTEO_AGENT", "CLAUDE_PID",
    ):
        monkeypatch.delenv(var, raising=False)

    # --- Claude Code: project dir encodes every non-alnum char as '-'
    enc = "".join(c if c.isalnum() else "-" for c in str(proj))
    _jsonl(
        home / ".claude" / "projects" / enc / f"{CLAUDE_SID}.jsonl",
        [
            {"type": "user", "isMeta": True, "sessionId": CLAUDE_SID, "cwd": str(proj),
             "message": {"role": "user", "content": "Base directory for this skill: /x\n# Skill body gatekeeper"}},
            {"type": "user", "sessionId": CLAUDE_SID, "cwd": str(proj), "timestamp": "2026-10-01T10:00:00Z",
             "message": {"role": "user", "content": "Please fix the gatekeeper dmg signing"}},
            {"type": "assistant", "sessionId": CLAUDE_SID, "timestamp": "2026-10-01T10:00:05Z",
             "message": {"id": "msg_1", "role": "assistant", "content": [{"type": "text", "text": "Looking at notarization."}]}},
            {"type": "assistant", "sessionId": CLAUDE_SID, "timestamp": "2026-10-01T10:00:06Z",
             "message": {"id": "msg_1", "role": "assistant", "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "codesign -v"}}]}},
            {"type": "assistant", "sessionId": CLAUDE_SID, "timestamp": "2026-10-01T10:01:00Z",
             "message": {"id": "msg_2", "role": "assistant", "content": [{"type": "text", "text": "Decided: use notarytool instead of altool. Done."}]}},
        ],
    )

    # --- Codex: root thread + subagent whose session_meta carries the *parent* session_id
    day = home / ".codex" / "sessions" / "2026" / "10" / "01"
    _jsonl(
        day / f"rollout-2026-10-01T10-00-00-{CODEX_ROOT}.jsonl",
        [
            {"type": "session_meta", "payload": {"id": CODEX_ROOT, "session_id": CODEX_ROOT, "cwd": str(proj)}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "# AGENTS.md instructions for /x\n<INSTRUCTIONS>…"}]}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "Add export to markdown"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "Add export to markdown"}]}},
            {"type": "response_item", "payload": {"type": "reasoning", "summary": []}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "Export to markdown is implemented in exporters.py"}]}},
            {"type": "event_msg", "payload": {"type": "agent_message", "message": "Export to markdown is implemented in exporters.py"}},
        ],
    )
    _jsonl(
        day / f"rollout-2026-10-01T10-05-00-{CODEX_SUB}.jsonl",
        [
            {"type": "session_meta", "payload": {"id": CODEX_SUB, "session_id": CODEX_ROOT,
             "parent_thread_id": CODEX_ROOT, "agent_nickname": "Hubble", "cwd": str(proj)}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "subagent task: scan repo"}},
        ],
    )
    os.utime(day / f"rollout-2026-10-01T10-05-00-{CODEX_SUB}.jsonl", (1_790_000_000, 1_790_000_000))

    # --- Gemini CLI: type user|gemini
    _jsonl(
        home / ".gemini" / "tmp" / "abc" / "chats" / "session-2026-10-01-g1.jsonl",
        [
            {"sessionId": "g1", "projectHash": "abc", "startTime": "2026-10-01T09:00:00Z"},
            {"id": "1", "type": "user", "content": "gemini question about caching"},
            {"id": "2", "type": "gemini", "content": "gemini answer"},
        ],
    )

    # --- Continue: history[].message.{role,content}
    cont = home / ".continue" / "sessions"
    cont.mkdir(parents=True)
    (cont / "c1.json").write_text(json.dumps({
        "sessionId": "c1", "title": "Continue chat", "workspaceDirectory": str(proj),
        "history": [
            {"message": {"role": "user", "content": "continue user text"}},
            {"message": {"role": "assistant", "content": "continue assistant text"}},
        ],
    }))

    yield {"home": home, "proj": proj}

    from puenteo import metacache

    metacache._mem = None
    metacache._dirty.clear()
