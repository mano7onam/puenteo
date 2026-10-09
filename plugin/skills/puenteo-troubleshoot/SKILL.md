---
name: puenteo-troubleshoot
description: >-
  Fix puenteo problems: a puenteo message never arrived, the other session is not visible, session
  not found or ambiguous id, search misses results, wrong whoami, MCP tools or hooks missing, mesh
  peers not showing, slow indexing. Use whenever a puenteo command fails or behaves unexpectedly, or
  the user says puenteo "doesn't work", "message didn't arrive", "can't see the other session".
---

# Troubleshoot puenteo

Start every investigation with:

```bash
puenteo doctor            # one line per check: CLI, index, bus, mesh, skills, MCP per agent
puenteo --version && puenteo whoami && puenteo ps
```

| Symptom | Check | Fix |
|---|---|---|
| `Ambiguous session ref` (exit 4) | candidates are printed | use a longer prefix or `provider:id` |
| `Session not found` | `puenteo list -n 50`, `-p <agent>` | the store may be new or moved: `puenteo status`; for Aider set `PUENTEO_AIDER_ROOTS` |
| search misses something | `puenteo index --stats` | `puenteo index` (rebuilds changed files); `--no-index` to scan raw; `puenteo index --clear` to reset |
| whoami wrong or empty | `puenteo whoami` shows "detected via" | inside a nested agent, set `PUENTEO_SESSION=agent:id`; plain shells have no session (fine) |
| message didn't arrive | `puenteo log -n 20`, then `puenteo inbox --as <addr> --peek` | wrong address (use `puenteo ps` ids); recipient not watching: Claude needs `puenteo watch` (Monitor) or hooks, Codex is pushed via `codex queue` |
| `codex queue` push failed | the delivery line in `send` output | the message stays in the inbox; codex CLI missing from PATH |
| MCP tools missing in an agent | `puenteo doctor` MCP rows | `puenteo install --agent <name>`, then restart that agent |
| hooks not firing | `~/.claude/settings.json` hooks contain `PUENTEO_HOOK=1` | `puenteo install --hooks`; restart the session |
| mesh: no peers | `puenteo mesh service status`, `puenteo mesh status` | start the bridge on *both* machines; same relays (`puenteo mesh relays`); LAN multicast may be blocked, so use relays or `puenteo mesh peer http://<host>:7358 --pubkey <npub>` |
| mesh: message dropped | `puenteo mesh status` counters (`dropped_policy`) | the recipient needs an offer, or a trusted node, or reply within its thread |
| everything slow | `pip install 'puenteo[fast]'` (Rust core), `PUENTEO_NATIVE` unset | first index run of many GB takes a few seconds; later runs are incremental |
| secrets masked in output | expected (redaction) | `--no-redact` only when the user asks |
| plugin errors | `puenteo plugins` | fix or `PUENTEO_DISABLE_PLUGINS=<name>` |

Debug output: `PUENTEO_DEBUG=1 puenteo …`. State lives in `puenteo doctor`'s paths (cache: index; state: bus.db, mesh.key, mesh.log).

When reporting a bug upstream, include the `puenteo doctor` output with private paths and transcript content removed.
