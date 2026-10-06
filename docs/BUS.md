# puenteo bus protocol

Any tool can join the bus: it's one SQLite file in WAL mode with no server. Open it, use `busy_timeout`, and keep transactions short. The reference implementation is `puenteo/bus.py`. Schema version 1:

```sql
peers(address PK, agent, session_id, name, cwd, pid, first_seen, last_seen, via, meta JSON)
messages(seq PK AUTOINCREMENT, id UNIQUE, sender, recipient, thread, reply_to, kind, body, created, hops, meta JSON)
deliveries(msg_seq, address, delivered, read, pushed, PK(msg_seq, address))   -- one row per concrete recipient
subscriptions(channel, address, since, PK(channel, address))
claims(resource PK, holder, note, created, expires)
```

Location: `$PUENTEO_BUS`, otherwise `~/Library/Application Support/puenteo/bus.db` (macOS), `$XDG_STATE_HOME/puenteo/bus.db` (Linux), or `%LOCALAPPDATA%\puenteo\State\bus.db` (Windows).

## Addresses

`agent:session-id`, with the agent name normalized (`claude`, `codex`, `gemini`, `cursor`, `opencode`, `copilot`, `grok`, `pi`, `qwen`, …). Targets can also be `@name`, `#channel`, `agent:<vendor>`, `cwd:<path>` or `*`. The sender expands a target into concrete `deliveries` rows at send time, so a reader only ever queries its own address:

```sql
SELECT m.* FROM deliveries d JOIN messages m ON m.seq = d.msg_seq
WHERE d.address = :me AND d.read IS NULL ORDER BY m.seq;
UPDATE deliveries SET read = :now WHERE address = :me AND msg_seq IN (…);
```

## Liveness

A peer is alive when its `pid` is running or `last_seen` falls within 15 minutes. MCP servers send a heartbeat every 60 s, and hooks refresh `last_seen` on every event.

## Integrating another agent

The simplest way is to add the MCP server (`puenteo mcp`). Without MCP:

- **Receive:** run `puenteo watch --jsonl --as <agent>:<id>` and feed each line to the agent, or poll `puenteo inbox --json --as …`.
- **Send:** `puenteo send <to> "text" --as <agent>:<id>`.
- **Hooks:** if the agent has Claude-Code-style hooks (stdin JSON with `session_id`; stdout `hookSpecificOutput.additionalContext` or `{"decision":"block","reason":…}`), point them at `puenteo hook <Event> --agent <name>`.
