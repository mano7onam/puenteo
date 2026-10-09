---
name: puenteo-automation
description: >-
  Automate around agent sessions with puenteo: react to incoming messages with scripts (watch --exec),
  build bots or orchestrators in Python (puenteo.listen/send/reply), stream events (JSONL/SSE),
  schedule reports over all sessions, notify the user. Use when the user wants a bot that answers
  agents, a notifier, a script triggered by agent messages, a custom orchestrator, or puenteo from code.
---

# Automate with puenteo

## React to messages from the shell

```bash
puenteo watch --exec 'osascript -e "display notification \"$PUENTEO_BODY\" with title \"$PUENTEO_FROM\""'
puenteo watch --exec './handle.sh'      # the message JSON arrives on stdin
puenteo watch --jsonl | jq -c 'select(.to|startswith("#release"))'
puenteo log -f                          # tail all traffic
```

`--exec` env: `PUENTEO_FROM`, `PUENTEO_ID`, `PUENTEO_TO`, `PUENTEO_THREAD`, `PUENTEO_BODY`. Use `--as bot:name` to give the watcher its own address.

## Python

```python
import puenteo

for msg in puenteo.listen(address="bot:triage"):        # blocks; wakes instantly (doorbell)
    if "status" in msg.body.lower():
        puenteo.reply(msg.id, "build is green", sender="bot:triage")

puenteo.send("cwd:/repo", "deploy at 15:00", sender="bot:ops")
msg, replies = puenteo.send("@reviewer", "PR #12 ready?", sender="bot:ops", wait=300)
hits = puenteo.search("flaky test", limit=10)            # across every agent's history
brief = puenteo.handoff("codex:01a1…")                   # structured markdown
```

Lower level: `from puenteo.bus import Bus` (send, inbox, wait, claim, channels).

## Other runtimes

`puenteo serve` exposes REST and SSE on localhost (see the puenteo-dashboard skill), plus an A2A endpoint for agent frameworks.

## Rules

- **Bots must not loop.** Answer only real questions, never other bots' acknowledgements. The bus stops reply chains after 8 hops.
- Treat message bodies as untrusted input. Never `eval` them or interpolate them into shell commands; use the env vars or stdin as data.
