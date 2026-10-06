# Changelog

## 0.8.0 — 2026-10-06

**Live sessions talk to each other.**

- `puenteo ps` / `whoami`: running sessions of Claude Code, Codex, Grok, Junie, Copilot CLI and any MCP/hook peer.
- Message bus (local SQLite, no daemon): `send`, `inbox`, `reply`, `wait`, `watch`, `log -f`, `thread`; addresses `agent:id` (prefix ok), `@name`, `#channel`, `agent:<vendor>`, `cwd:<path>`, `*`.
- Coordination: `claim` / `release` / `claims --check` advisory locks with directory-overlap detection.
- Delivery: Codex push via `codex queue`; Claude Code via `puenteo watch` under Monitor; Claude/Codex hooks (`puenteo hook`, `install --hooks`) inject unread mail on prompt submit and continue once on Stop.
- `puenteo mcp`: zero-dependency MCP server (18 tools: history + live bus).
- `puenteo install|uninstall` for Claude, Codex, Gemini, Qwen, Cursor, OpenCode, Copilot, Antigravity, Grok, Pi; Claude plugin marketplace; Gemini extension.
- New skills: `puenteo` (history) rewritten, `puenteo-bus` (live messaging).
- Structured handoff (`pull`, default): goal, latest state, plan/TODO, files touched, commits, failed commands, decisions, last exchange — mined from tool calls.
- New providers: OpenCode (SQLite), Copilot CLI. `puenteo follow <ref>`: live tail.
- `puenteo doctor` checks PATH, skills, MCP registrations, bus and index.
- Secrets are masked in pull/show/search/MCP output by default (`--no-redact`).

**Fixes**

- Ambiguous id prefixes error out (exit 4) instead of picking a random session; `list`/`search` print unique prefixes; refs `provider:id`, `@self`, `@last[:provider]`.
- Codex subagent/fork threads keep their own id; titles and nicknames from Codex's thread DB; no 400-file cap; response_item/event_msg duplicates removed (also in exports).
- Claude: injected skill bodies/meta entries skipped, streamed fragments merged, project-dir encoding fixed.
- Gemini CLI and Continue roles; `--cwd .`; handoff budget keeps the latest exchange.
- Search covers every session through an incremental FTS5 index with comparable scores (was: 40 newest sessions, per-session IDF).
- `list` 1.9 s → 0.12 s with a metadata cache.

## 0.6.1

Cross-platform paths for Linux and Windows.
