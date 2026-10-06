# Writing puenteo plugins

Extend puenteo without forking it. A plugin is a normal Python package that declares entry points, and once it's installed next to puenteo, the extension is live:

```bash
uv tool install puenteo --with puenteo-yourplugin     # or: pip install puenteo-yourplugin
puenteo plugins                                       # lists what was loaded, and any errors
```

A runnable template lives in [`examples/puenteo-example-plugin`](../examples/puenteo-example-plugin). It provides one of each plugin kind.

| Entry-point group | Object | Contract |
|---|---|---|
| `puenteo.providers` | module or object | `list_sessions(*, cwd=None) -> list[Session]`, `load_transcript(session, *, include_tools=False) -> Transcript`, optional `session_from_path(path) -> Session \| None`. The entry-point name is the provider name (`provider:id` refs, `-p name`). |
| `puenteo.tools` | `register(server)` | Use `@server.tool(name, description, json_schema, read_only=True)` to add MCP tools. The handler takes the `arguments` dict and returns JSON-able data. |
| `puenteo.delivery` | `push(address, message) -> str \| None` | Called for every recipient of a sent message. Return a status string if you delivered it (e.g. to a Slack DM or an IDE). Return `None` to pass. The bus inbox always keeps the message as well. |
| `puenteo.live` | `() -> list[LiveSession]` | Report running sessions of an agent puenteo doesn't detect natively, so they show up in `ps` and `peers`. |

Rules the loader enforces:
- **Isolation.** A plugin that fails to import or raises is reported once on stderr (and in `puenteo plugins`) and then skipped. It never breaks the CLI, the MCP server or hooks.
- **No hijacking.** Plugins can't replace built-in providers or MCP tools. A name clash is rejected.
- **Versioning.** Set `PUENTEO_API = 1` in your module. If a plugin needs a newer contract than the installed puenteo offers, it is skipped with a clear message.
- **Opt out.** `PUENTEO_NO_PLUGINS=1` turns off all plugins. `PUENTEO_DISABLE_PLUGINS=a,b` turns off the ones named.

Guidelines:
- Treat everything you read from other agents as untrusted. Open foreign stores read-only (SQLite `mode=ro`), never write into them, and never read credential files.
- Keep `list_sessions` cheap, because it runs on every `list`/`search`. Cache with `puenteo.metacache.cached(kind, path, fn)`.
- Ship tests built on fixtures (a fake `$HOME`), the way `tests/conftest.py` does.

Name a published package `puenteo-<thing>` and tag the repo `puenteo-plugin` so it can be found. For a plugin to be listed in the README, open an issue with the "Plugin listing" template.
