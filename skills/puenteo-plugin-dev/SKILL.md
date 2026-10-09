---
name: puenteo-plugin-dev
description: >-
  Extend puenteo with a plugin package: support a new agent's session store (provider), add MCP tools,
  add a delivery channel (Slack, IDE, notifications) or detect live sessions of a new agent. Use when
  the user wants puenteo to support another agent/tool, "write a puenteo plugin", "add a provider for
  X", or to push puenteo messages somewhere new.
---

# Write a puenteo plugin

A plugin is a normal Python package with entry points. Start from the template, `examples/puenteo-example-plugin` in the puenteo repo, which shows all four kinds.

```toml
# pyproject.toml of your package
[project.entry-points."puenteo.providers"]   # name = provider name (refs: name:id, -p name)
cline = "puenteo_cline.provider"
[project.entry-points."puenteo.tools"]
cline = "puenteo_cline.tools:register"
[project.entry-points."puenteo.delivery"]
slack = "puenteo_slack:push"
[project.entry-points."puenteo.live"]
cline = "puenteo_cline.provider:live_sessions"
```

## Contracts (set `PUENTEO_API = 1` in each module)

- **Provider**: `list_sessions(*, cwd=None) -> list[Session]`, `load_transcript(session, *, include_tools=False) -> Transcript`, and optionally `session_from_path(path)`. Use `puenteo.models.Session/Message/Transcript` and `puenteo.util.cwd_matches`. Cache expensive peeks with `puenteo.metacache.cached(kind, path, fn)`.
- **Tools**: `register(server)`, then `@server.tool(name, description, json_schema, read_only=True)`. The handler gets the arguments dict and returns JSON-able data.
- **Delivery**: `push(address, message) -> str | None`. Return a status string if you delivered it, or `None` to pass.
- **Live**: `() -> list[puenteo.live.LiveSession]`.

## Develop and test

```bash
pip install -e ./my-plugin && puenteo plugins     # lists loaded plugins and any errors
puenteo list -p cline -n 5 && puenteo search "x" -p cline
PUENTEO_DISABLE_PLUGINS=cline puenteo …           # turn yours off
```

Write tests with a fake `$HOME` and fixture files (see puenteo's `tests/conftest.py`). Never use real transcripts in fixtures.

## Rules the loader enforces / you must follow

- A plugin that crashes is reported and skipped. It can't break puenteo, and it can't override built-in providers or tools.
- Open other agents' stores read-only (SQLite `mode=ro`). Never write into them, and never read credential or auth files.
- Publish as `puenteo-<thing>` and tag the repo `puenteo-plugin`. To get listed, open the "Plugin listing" issue in mano7onam/puenteo.
