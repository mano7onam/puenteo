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
argument-hint: "[list | search <query> | pull <session_id> --query … | export …]"
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

### 1. List sessions

```bash
asb list --json
puenteo list --provider claude --cwd "$PWD"
asb list -n 20
```

### 2. Search by topic

```bash
asb search "gatekeeper dmg" --json
asb search "export markdown" --session <id>
```

### 3. Pull compact pack or full export

```bash
asb pull <id> --query "topic" --mode query --max-chars 8000
asb pull <id> --mode handoff --max-chars 10000
asb export <id> -f md -o /tmp/ctx.md
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
3. Prefer **query mode** over dumping full sessions.
4. Session refs may be id **prefix**, full uuid, path, or title substring.
5. Do not leak secrets from packs into public logs.
6. Prefer sessions whose `cwd` matches the user’s project.

## Providers

| Name | Store |
|------|--------|
| `claude` | `~/.claude/projects/` |
| `codex` | `~/.codex/sessions/` |
| `grok` | `~/.grok/sessions/` |
| `pi` | `~/.pi/agent/sessions/` |

## Recipe

```text
1) asb list --json          # or: puenteo list --json
2) asb search "<topic>" --json
3) asb pull <best_id> --query "<topic>" --mode query --max-chars 8000
4) Apply only verified facts to the current repo.
```
