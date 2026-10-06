---
description: Pull a handoff pack from another (past or running) session into this one
argument-hint: [session ref or topic]
---
Find the session for: $ARGUMENTS
1. If it looks like an id/ref, use it; otherwise `puenteo search "$ARGUMENTS" --exclude-self` and pick the best session (prefer this project's cwd).
2. `puenteo outline <ref>`, then `puenteo pull <ref> --mode handoff` (add `--query` for a topic).
3. Summarize goal, decisions, current state, and open items. Verify against files on disk before acting.
