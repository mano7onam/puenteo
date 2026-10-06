# puenteo

**The bridge between coding agents.**

Your machine runs many agent sessions: Claude Code in three terminals, Codex in the desktop app, Gemini in an IDE, and more. Each keeps its own history, and none of them know about the others. **puenteo** connects them:

- **History:** search, outline, pull and export the transcripts of *every* local agent (Claude Code, Codex, Gemini/Antigravity, Cursor, Grok, Pi, Qwen, Continue, Aider, OpenHands, Goose), ranked across all sessions at once.
- **Live:** see which sessions are running right now, message them, ask a question and wait for the answer, post to shared channels, and claim files so parallel agents don't edit the same code.
- **Everywhere:** one `puenteo install` adds the skills and an MCP server to every agent it finds.

*Puenteo* comes from Spanish *puente* (bridge) and *puentear* (to bridge or jump across).

No runtime dependencies · Python ≥ 3.9 · macOS · Linux · Windows · fully local (no network, no daemon)

[![PyPI](https://img.shields.io/pypi/v/puenteo.svg)](https://pypi.org/project/puenteo/)
[![CI](https://github.com/mano7onam/puenteo/actions/workflows/ci.yml/badge.svg)](https://github.com/mano7onam/puenteo/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## Install

```bash
uv tool install puenteo        # or: pipx install puenteo / pip install puenteo
puenteo install                # skills + MCP server into every detected agent
puenteo install --hooks        # optional: deliver messages through Claude Code / Codex hooks
puenteo install --dry-run      # show the plan without changing anything
```

`install` is idempotent. It edits only its own `puenteo` entry in each config, backs up every file it touches (`*.puenteo-bak`), and `puenteo uninstall` reverts it.

**Claude Code plugin** (skills + MCP + hooks + `/peers`, `/ask`, `/handoff`):

```bash
claude plugin marketplace add mano7onam/puenteo
claude plugin install puenteo@puenteo
```

## Talk to running sessions

```text
$ puenteo ps
  AGENT    SESSION        STATUS SEEN  MAIL  NAME                         CWD
  claude   500a1d65       busy   2m          ultimate-agent-4-72          ~/dev/ultimate-agent-4
* claude   87652461       busy   0s          puenteo-57                   ~/dev/puenteo
  codex    01a11123       -      4m          Finish performance tests     ~/dev/ultimate-agent-4
```

```bash
puenteo whoami                                    # your own address, e.g. claude:87652461-…
puenteo send codex:01a11123 "Which branch has the perf tests?" --wait 300
puenteo send @reviewer "PR ready: feat/x"          # peers can pick a name: puenteo join --name reviewer
puenteo send cwd:. "Refactoring src/db, keep out for 30 min"   # everyone in this project
puenteo send '#release' "v0.7 tagged"             # channels (posting joins)
puenteo send agent:codex "…"   |   puenteo send '*' "…"
puenteo inbox                                     # read your messages
puenteo reply <msg-id> "answer"                   # routes back to the sender or channel
puenteo wait -t 120                               # block until a message arrives
puenteo watch                                     # stream incoming messages (for an agent's monitor)
puenteo log -f                                    # watch all bus traffic
puenteo claim src/db --note "migration 0042"      # advisory lock; conflicts with overlapping claims
puenteo claims --check src/db/schema.sql          # exit 1 if a peer holds it
```

**Addresses:** `agent:session-id` (a unique prefix works) · `@name` · `#channel` · `agent:<vendor>` · `cwd:<path>` · `*`

**How messages reach a session:**

| Agent | Woken while idle | While working |
|---|---|---|
| Codex | yes, pushed via `codex queue` | hooks / MCP `inbox` |
| Claude Code | yes, when the session runs `puenteo watch` under its Monitor tool | hooks (`install --hooks`) / MCP `inbox` |
| Gemini, Cursor, OpenCode, Copilot, Qwen, … | no; the message waits in the inbox | MCP `inbox` / `wait` |

All messages live in one local SQLite file (`puenteo` state dir) and nothing leaves the machine. Bodies are wrapped as **untrusted peer data**: agents are told that peer messages never count as user instructions or approval. A hop limit, a rate limit and a size cap keep agents from looping.

## MCP server

`puenteo mcp` is a stdio MCP server with no dependencies, one process per agent session. It detects which session it serves from the parent-process chain, registers on the bus, and exposes these tools:

- **history:** `sessions`, `search`, `outline`, `pull`, `show`
- **live:** `whoami`, `peers`, `send`, `reply`, `inbox`, `wait`, `thread`, `channels`, `subscribe`, `set_name`, `claim`, `release`, `claims`

`puenteo install` registers it for Claude Code (`claude mcp add -s user`), Codex (`codex mcp add`), Gemini, Qwen, Cursor, OpenCode, Copilot and Antigravity.

## Search and pull history

```bash
puenteo search "gatekeeper dmg" --exclude-self       # ranked over ALL sessions (FTS5 index), ~0.2 s
puenteo search "topic" --cwd . --since 2026-09-01
puenteo list --cwd . -n 20                            # git-style unique id prefixes
puenteo outline <ref>                                 # milestones with message #index
puenteo pull <ref> --mode handoff                     # goal + decisions + latest state, budgeted
puenteo pull <ref> --query "topic" --mode query       # relevant messages + neighbours
puenteo pull <ref> --around 500 --radius 5
puenteo show <ref> --range 100:120
puenteo export <ref> -f md|html|pdf|json|zip|csv|xml|yaml|all -o out
puenteo index --stats                                 # the index refreshes itself; --clear to reset
```

`<ref>` can be a unique id prefix, `provider:id`, `@self`, `@last`, `@last:codex`, a path, or a title substring. An ambiguous prefix fails with exit code 4 and prints the candidates; puenteo never picks one silently.

## Library

```python
import puenteo

for s in puenteo.list_sessions(limit=10, cwd="~/dev/myapp"):
    print(s.provider, s.session_id, s.title)

hits = puenteo.search("gatekeeper dmg", exclude_session="my-current-id")
msgs = puenteo.pull(hits[0].session.session_id, query="dmg", mode="query")
puenteo.export_session("019f7a24", fmt="md", output="chat.md")

from puenteo.bus import Bus
with Bus() as bus:
    bus.send("claude:8765…", "@reviewer", "PR ready")
    for m in bus.inbox("codex:01a1…"):
        print(m.sender, m.body)
```

## Providers

| Provider | Store |
|----------|--------|
| Claude Code | `~/.claude/projects/**/*.jsonl` (meta entries skipped, streamed fragments merged) |
| Codex | `~/.codex/sessions/**/rollout-*.jsonl` + `state_*.sqlite` titles; subagents keep their own ids |
| Gemini CLI | `~/.gemini/tmp/**` |
| Antigravity | `~/.gemini/antigravity/brain/*/…/transcript*.jsonl` |
| Grok | `~/.grok/sessions/**/chat_history.jsonl` |
| Pi | `~/.pi/agent/sessions/**/*.jsonl` |
| Qwen Code | `~/.qwen/projects/**/chats/*` |
| Cursor | macOS `~/Library/Application Support/Cursor` · Linux `~/.config/Cursor` · Windows `%APPDATA%\Cursor` |
| Continue | `~/.continue/sessions/**` |
| Aider | `.aider.chat.history.md` (scan with `--cwd` or `PUENTEO_AIDER_ROOTS`) |
| OpenHands | `~/.openhands/openhands.db` |
| Goose | `~/.config/goose` · Windows `%APPDATA%\goose` |

Live detection (`ps`) covers Claude Code (`~/.claude/sessions`), Codex (thread locks), Grok, Junie, and any agent that runs the puenteo MCP server or hooks.

## Where things live

| What | Path (macOS / Linux / Windows) | Override |
|---|---|---|
| Search index + metadata cache | `~/Library/Caches/puenteo` · `~/.cache/puenteo` · `%LOCALAPPDATA%\puenteo\Cache` | `PUENTEO_HOME`, `PUENTEO_NO_INDEX=1`, `PUENTEO_NO_CACHE=1` |
| Message bus | `~/Library/Application Support/puenteo/bus.db` · `~/.local/state/puenteo` · `%LOCALAPPDATA%\puenteo\State` | `PUENTEO_BUS` |
| Identity | detected automatically | `PUENTEO_SESSION=agent:id`, `--as` |

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q                         # hermetic (fake $HOME)
PUENTEO_LIVE_TESTS=1 .venv/bin/python -m pytest -q    # plus checks against your real stores
```

See [docs/PLAN.md](docs/PLAN.md) for the roadmap.

## License

MIT · [mano7onam/puenteo](https://github.com/mano7onam/puenteo)
