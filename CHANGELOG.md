# Changelog

## 0.10.0 — 2026-10-06

- **Plugins** via entry points: `puenteo.providers`, `puenteo.tools` (MCP), `puenteo.delivery`, `puenteo.live`. Plugins are isolated (a broken one is reported and skipped), can't override built-ins and are versioned (`PUENTEO_API`). Adds `puenteo plugins`, docs/PLUGINS.md and a template plugin.
- **Contribution gates**: `checks` workflow (zero-dependency + stdlib-only imports, Bandit, gitleaks, DCO sign-off, AI-agent disclosure), CODEOWNERS, CONTRIBUTING, GOVERNANCE with trust levels (area maintainers, including organizations), PR and issue templates.
- Security: zip export no longer uses `tempfile.mktemp` (race).
- Dockerfile for MCP directories (passes introspection).

## 0.9.1 — 2026-10-06

- MCP server re-detects its session lazily and moves a provisional inbox to the real address. Before this, Codex threads that had just started could not receive replies. Verified live with a Claude ↔ Codex ↔ Claude test.

## 0.9.0 — 2026-10-06

- **Instant delivery**: Unix-socket doorbells replace polling in send/wait/watch/log/SSE (0.8 ms median).
- **`puenteo serve`**: local HTTP gateway with a live dashboard, REST, SSE, MCP over HTTP, and an A2A v1.0 facade (agent card + JSON-RPC `SendMessage`/`GetTask`). Loopback only, token auth, Host/Origin checks.
- **Rust core** (`puenteo-core`, optional `puenteo[fast]`): byte-level JSONL routing and parallel parsing. Output is identical to the Python parser. Cold index 3.5x faster.
- `watch --exec`, `puenteo.listen()` streaming API, `puenteo guard` git pre-commit claim guard.
- Review fixes: transactional inbox/claims, frame-forgery-proof message rendering, redaction before JSON escaping, Windows-safe hook/MCP output, safer `install` (symlinks, modes, foreign skills, JSONC).

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
