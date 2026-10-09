---
name: puenteo-setup
description: >-
  Install, configure and verify puenteo, the bridge between coding-agent sessions. Use when the user
  asks to "set up puenteo", "connect my agents", "install the puenteo MCP/skills/hooks", "make
  Claude/Codex/Gemini/Cursor see each other", configure which agents get it, enable hooks, or
  undo the setup. Covers install, uninstall, dry runs, doctor, and per-agent config paths.
---

# Set up puenteo

## 1. Install the CLI (pick one)

```bash
uv tool install puenteo            # recommended
uv tool install 'puenteo[fast]'    # + optional Rust core (3–8x faster indexing)
pipx install puenteo   |   pip install --user puenteo
```

Check it with `puenteo --version`. Python 3.9 or newer, no other dependencies.

## 2. Wire it into every agent on this machine

```bash
puenteo install --dry-run     # show the plan first, change nothing
puenteo install               # skills + MCP server into every agent it finds
puenteo install --agent claude,codex      # only some agents
puenteo install --hooks       # also deliver messages via Claude Code / Codex hooks (opt-in)
```

What it touches. Each config gets only a `puenteo` entry, and a backup `*.puenteo-bak` is made first.

| Agent | Skills | MCP |
|---|---|---|
| Claude Code | `~/.claude/skills/` | `claude mcp add -s user puenteo` |
| Codex | `~/.agents/skills/` | `codex mcp add puenteo` |
| Gemini CLI / Antigravity | `~/.agents/skills/` | `~/.gemini/settings.json`, `~/.gemini/antigravity/mcp_config.json` |
| Cursor | `~/.agents/skills/` | `~/.cursor/mcp.json` |
| OpenCode | `~/.agents/skills/` | `~/.config/opencode/opencode.json` |
| Copilot CLI | `~/.agents/skills/` | `~/.copilot/mcp-config.json` |
| Qwen / Grok / Pi | own skills dir | Qwen: `~/.qwen/settings.json` |

Alternatives:
- Claude Code plugin: `claude plugin marketplace add mano7onam/puenteo && claude plugin install puenteo@puenteo`
- Gemini extension: `gemini extensions install https://github.com/mano7onam/puenteo`

## 3. Verify

```bash
puenteo doctor     # ✓/!/✗ for PATH, index, bus, mesh, every skill and MCP registration
puenteo whoami     # which session you are (run inside an agent)
puenteo ps         # other sessions running now
```

Running sessions pick up new skills and MCP servers only after a restart, so tell the user to restart them.

## 4. Optional extras

- Instant wake-ups for Claude Code: run `puenteo watch` with the Monitor tool (see the puenteo-bus skill).
- Other machines: `puenteo mesh service install` (see the puenteo-mesh skill).
- Dashboard: `puenteo serve --open` (see the puenteo-dashboard skill).

## Undo

`puenteo uninstall` (add `--hooks` if hooks were installed) removes exactly what `install` added. It never touches skills or hooks it didn't create.

## Environment knobs

`PUENTEO_HOME` (state + cache root), `PUENTEO_BUS` (bus file), `PUENTEO_SESSION=agent:id` (force identity), `PUENTEO_NO_INDEX=1`, `PUENTEO_NO_CACHE=1`, `PUENTEO_NO_REDACT=1`, `PUENTEO_NATIVE=0`, `PUENTEO_NO_PLUGINS=1`, `PUENTEO_QUIET=1`.
