# Changelog

## 0.13.0 — 2026-10-09

- **11 skills** instead of 2, each with sharp routing. The `puenteo` hub skill points to the right one:
  - puenteo-setup, puenteo-bus, puenteo-handoff, puenteo-team, puenteo-mesh
  - puenteo-dashboard, puenteo-export, puenteo-automation, puenteo-plugin-dev, puenteo-troubleshoot
- `puenteo install` ships all of them to every agent (Claude Code, Codex/Gemini/Cursor/OpenCode/Copilot via `~/.agents/skills`, Qwen, Grok), and so do the Claude plugin and the Gemini extension.
- `tests/test_skills.py`: every `puenteo …` command shown in any skill must parse with the real CLI, and every skill must have valid frontmatter with a "Use when". This caught a wrong `mesh peer add` in the docs.
- `evals/run_skill_eval.sh` checks that real Claude Code sessions load the right skill and answer with correct commands. Before the routing rewrite: 0/5. After: 5/6 correct commands, 4/6 exact skill, 4/4 no false triggers.

## 0.12.0 — 2026-10-08

- `puenteo mesh service install|uninstall|status`: the bridge runs in the background at login (launchd, systemd --user, Task Scheduler), restarts on crash and stops cleanly on SIGTERM.
- Exactly-once delivery: a persistent seen-set stops relay replays after a reconnect or restart from being delivered again.
- Presence: heartbeat, an "offline" goodbye on stop, and offline detection (silent for about 16 min). Replayed old announcements are ignored and offline nodes' offers are hidden. New commands `mesh peers --all` and `mesh forget`.
- Offers and rooms take effect within 30 s without restarting the bridge.
- `doctor` and the MCP `mesh_peers` tool report the bridge state. The dashboard has "Other machines" and "Bazaar offers" panels (`/api/mesh`).
- Verified live: a real Claude Code session on this Mac used MCP `find` and `send` to ask an agent on another machine over public Nostr relays and got its answer. The reverse direction woke this session through `puenteo watch`, and it worked again after a service restart.

## 0.11.0 — 2026-10-07

**Mesh: sessions across machines, P2P, no servers.**
- `puenteo mesh up`: a bridge between the local bus and other machines. Transports: LAN (UDP multicast discovery + direct HTTP), public Nostr relays (NAT-friendly), a self-hosted relay (`puenteo mesh relay`), or direct VPN peers (`mesh peer`).
- Addressing: any local address plus `@node` (`claude:8765@laptop`, `@reviewer@box`, `#room@*`). Remote messages land in the local bus, so hooks, `codex queue`, Monitor, MCP and the dashboard work with them unchanged. Replies route back automatically.
- Bazaar: `offer`, `find` (ranked across machines), `ask`. Rooms: `room join|leave|list|say`, many-to-many, with private rooms encrypted by a shared secret (the topic is hidden too).
- Security: BIP-340 Schnorr-signed events, NIP-44 v2 end-to-end encryption for DMs, a default-closed inbound policy (reply-to-own-thread / offer / trusted node / joined room), per-node rate limit, a cross-machine hop limit, block/trust, and stale-key handling when a machine is reinstalled.
- Pure-stdlib crypto (secp256k1, ChaCha20, HKDF, bech32), verified against the official BIP-340 and NIP-44 vectors. Interop verified live with public relays. Minimal WebSocket client and server.
- MCP: `mesh_peers`, `find`, `offer`, `room_join` (22 tools).
- Verified in Docker: 4 nodes on a LAN, 2 isolated networks through public relays and through a self-hosted relay.

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
