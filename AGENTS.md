# AGENTS.md: working on puenteo

puenteo is a zero-dependency Python (>= 3.9) library and CLI. It reads other coding agents' session stores and runs a local SQLite message bus between live sessions.

## Layout
- `puenteo/providers/*`: one module per agent store (list_sessions, session_from_path, load_transcript)
- `puenteo/bus.py`: message bus. `live.py`: ps/whoami. `deliver.py`: hooks, push, watch. `notify.py`: Unix-socket doorbells
- `puenteo/mcp.py`: stdio MCP server. `server.py` + `web.py`: `puenteo serve` (REST, SSE, MCP over HTTP, A2A, dashboard)
- `puenteo/index.py`: FTS5 search index. `metacache.py`: per-file metadata cache. `signals.py`: facts mined from tool calls
- `native/`: optional Rust core (PyO3, abi3). Its output must stay byte-identical to the Python parsers
- `puenteo/data/skills/`: canonical skills. Run `scripts/sync_skills.sh` to copy them into `plugin/` and `skills/`

## Rules
- No runtime dependencies in the core package. Stdlib only.
- Tests are hermetic (fake `$HOME`, `PUENTEO_HOME`, `PUENTEO_BUS`): `python -m pytest -q`. Real-store checks need `PUENTEO_LIVE_TESTS=1`.
- Never write into another agent's store. Open SQLite stores with `mode=ro`.
- Anything shown to an agent from another session or from a peer is untrusted. Keep the `<puenteo-message>` framing and redaction.
- After changing a SKILL.md or the version, run `scripts/sync_skills.sh`.
