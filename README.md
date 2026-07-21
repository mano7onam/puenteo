# agent-session-bridge (`asb`)

**Python library + CLI** to discover, search, and export local coding-agent chats.

- **Library:** `import agent_session_bridge as asb`
- **CLI:** `asb` / `agent-session-bridge` / `python -m agent_session_bridge`

Zero runtime dependencies. Python ≥ 3.9.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## Install

```bash
git clone https://github.com/mano7onam/agent-session-bridge.git
cd agent-session-bridge
python3 -m venv .venv
.venv/bin/pip install -e .

# optional: put CLI on PATH
ln -sf "$PWD/.venv/bin/asb" ~/.local/bin/asb
```

From another project (sibling / editable):

```bash
pip install -e ../agent-session-bridge
```

## Library (primary API)

```python
import agent_session_bridge as asb

# What is installed on this machine?
print(asb.status())
# → providers, session counts, export_formats

# List sessions (Claude / Codex / Grok / Pi)
for s in asb.list_sessions(limit=10):
    print(s.provider, s.session_id[:8], s.title, s.cwd)

# Load full transcript (rich model with tools/attachments)
t = asb.load("019f7a24", include_tools=True)
print(t.title, t.message_count)

# Search across all agents
for hit in asb.search("gatekeeper dmg", limit=5):
    print(hit.score, hit.session.provider, hit.snippet)

# Compact pack for another agent
msgs = asb.pull("019f7a24", query="export", mode="query")

# Export to any common format
asb.export_session("019f7a24", fmt="md", output="chat.md")
asb.export_session("019f7a24", fmt="html", output="chat.html")
asb.export_session("019f7a24", fmt="pdf", output="chat.pdf")
asb.export_session("019f7a24", fmt="json", output="chat.json")
asb.export_session("019f7a24", fmt="zip", output="chat.zip")   # multi-format bundle
asb.export_session("019f7a24", fmt="all", output="./exports/") # every format

# Or bytes (no disk write)
data, media_type, filename = asb.export_bytes("019f7a24", fmt="md")
```

### Public symbols

| Function | Purpose |
|----------|---------|
| `status()` | Providers + counts + formats |
| `list_sessions(...)` | Discover sessions |
| `get_session(ref)` / `resolve_session` | Resolve id / path / title |
| `load(ref, rich=True)` | Full or light transcript |
| `search(query)` | Cross-session BM25 search |
| `pull(ref, …)` | Smart compact context pack |
| `export_session(ref, fmt=…)` | Write md/html/pdf/… |
| `export_bytes(ref, fmt=…)` | In-memory export |
| `render(transcript, fmt)` | Low-level renderer |
| `list_sources_for_cwd(cwd)` | Sessions for a project folder |
| `find_best_agent_transcript(cwd)` | Best match for a folder |

Also used by [Terminal Dashboard](https://github.com/mano7onam/terminal-dashboard) for chat export (same parsers, no duplication).

## CLI

Same package, console entry points:

```bash
asb status
asb list -n 20 --json
asb list --provider claude,grok --cwd ~/dev/myapp
asb show <id> --last 40
asb search "topic" --json
asb pull <id> --query "topic" --mode query --max-chars 8000
asb export <id> -f md -o chat.md
asb export <id> -f pdf -o chat.pdf
asb export <id> -f html|json|zip|csv|xml|yaml -o …
asb export <id> -f all -o ./out/
asb export <id> -f md --tools --thinking
```

`<id>` = full uuid, **prefix**, filesystem path, or unique title substring.

## Providers

| Provider | On-disk store |
|----------|----------------|
| Claude Code | `~/.claude/projects/**/*.jsonl` |
| Codex | `~/.codex/sessions/**/rollout-*.jsonl` |
| Grok | `~/.grok/sessions/**/chat_history.jsonl` |
| Pi | `~/.pi/agent/sessions/**/*.jsonl` |

## Export formats

| Format | Notes |
|--------|--------|
| `md` | Markdown (default) |
| `txt` | Plain text |
| `html` | Standalone dark theme page |
| `pdf` | Chrome/Edge headless if present, else text PDF |
| `json` | Structured transcript |
| `zip` | md+html+txt+json+csv+xml+yaml + image assets |
| `csv` | One row per message |
| `xml` | Simple XML tree |
| `yaml` | Minimal YAML (no PyYAML needed) |

## Agent skill

```bash
./scripts/install_skills.sh
```

Installs `skills/agent-session-bridge` into `~/.grok/skills`, `~/.claude/skills`, `~/.codex/skills`.

## Project layout

```text
agent_session_bridge/     # installable package
  __init__.py             # public API re-exports
  api.py                  # library surface
  cli.py                  # asb entry point
  providers/              # claude, codex, grok, pi
  rich.py                 # full transcript model (export)
  exporters.py            # md/html/pdf/…
  search.py / extract.py  # BM25 + smart pull
skills/                   # optional agent skill
tests/
```

## License

MIT · [mano7onam/agent-session-bridge](https://github.com/mano7onam/agent-session-bridge)
