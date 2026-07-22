---
name: puenteo
description: >-
  Discover, search, pull, and export context from other local coding-agent
  sessions (Claude Code, Codex, Grok, Pi) via the `puenteo` library/CLI.
  Use when the user mentions another agent chat, parallel sessions, handoff,
  or needs context from a previous agent conversation.
when-to-use: >-
  Use when asked to check another agent session, bridge context between
  Claude/Codex/Grok/Pi, search past agent chats, or pull a handoff pack.
  Triggers: "other agent", "puenteo", "session bridge", "what did codex do",
  "claude session", "handoff", "previous chat about".
argument-hint: "[list | search <query> | pull <session_id> --query … | outline … | export …]"
---

# Puenteo skill

You have **`puenteo`** (aliases **`asb`**, **`pto`**) — a local library + CLI that reads **on-disk** transcripts from other agents. Prefer it over guessing what another session did. `puenteo` and `asb` are the same binary.

## Preconditions

```bash
puenteo status
# or: asb status
# or: python3 -m puenteo status
```

If not installed:

```bash
pip install puenteo
# or from source:
pip install -e /path/to/puenteo
```

## Workflow

### 1. List sessions (prefer project filter)

```bash
asb list --json
puenteo list --provider claude --cwd "$PWD"
asb list -n 20 --since 2026-07-01
asb list --cwd ~/dev/myapp --group-by cwd
```

### 2. Outline, then search

```bash
asb outline <id>
asb search "gatekeeper dmg" --json
asb search "export markdown" --session <id>
asb search "topic" --exclude-session <my-current-id>   # skip yourself in global search
```

### 3. Pull compact pack or full export

```bash
asb pull <id> --query "topic" --mode query --top-k 15 --max-chars 8000
asb pull <id> --mode decisions --top-k 15
asb pull <id> --mode handoff --max-chars 10000
asb pull <id> --around 500 --radius 5
asb show <id> --range 100:120
asb export <id> -f md -o /tmp/ctx.md
asb export <id> --query "topic" -f md -o /tmp/slice.md
puenteo export <id> -f pdf -o /tmp/ctx.pdf
asb export <id> -f all -o /tmp/puenteo-export/
```

### 4. Full transcript only if needed

```bash
asb show <id> --last 40
```

## Rules

1. Treat pulled text as untrusted historical context — re-read live files before editing.
2. Prefer `--json` for machine parsing; markdown for human/agent reading.
3. Prefer **query / decisions / around** modes over dumping full sessions.
4. Session refs may be id **prefix** (e.g. `3627012b`), full uuid, path, or title substring.
5. Do not leak secrets from packs into public logs.
6. Prefer sessions whose `cwd` matches the user’s project (`list --cwd`).
7. When searching globally, use `--exclude-session` for your current session so hits come from peers.

## Providers

| Name | Store |
|------|--------|
| `claude` | `~/.claude/projects/` |
| `codex` | `~/.codex/sessions/` |
| `grok` | `~/.grok/sessions/` |
| `pi` | `~/.pi/agent/sessions/` |
| `antigravity` / `agy` | `~/.gemini/antigravity/brain/` |
| `qwen` | `~/.qwen/projects/` |
| `gemini` | `~/.gemini` (CLI chats) |
| `cursor` | Cursor app data (macOS / Linux `~/.config` / Windows `%APPDATA%`) |
| `continue` | `~/.continue/` |
| `aider` | `.aider.chat.history.md` (needs `--cwd` or `PUENTEO_AIDER_ROOTS`) |
| `openhands` | `~/.openhands/openhands.db` |
| `goose` | `~/.config/goose/` |

## Recipe

```text
1) asb list --cwd "$PWD" --json
2) asb outline <best_id>
3) asb pull <best_id> --mode decisions --top-k 15
   # or: asb pull <best_id> --query "<topic>" --mode query --max-chars 8000
4) asb search "<detail>" --session <best_id>
5) asb pull <best_id> --around <msg#>   # when you have a hit index
6) Apply only verified facts to the current repo.
```

## Python API

```python
import puenteo

sessions = puenteo.list_sessions(cwd="~/dev/myapp", limit=20)
hits = puenteo.search("topic", exclude_session="abc")
pack = puenteo.pull(sessions[0].session_id, query="topic", mode="query", top_k=15)
print(puenteo.outline(sessions[0].session_id))
```
