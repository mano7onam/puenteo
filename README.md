# Agent Session Bridge (`asb`)

**Python library + CLI in one package.** Discover, search, and **export** local coding-agent sessions — Claude Code, Codex, Grok, Pi — to Markdown, HTML, PDF, JSON, ZIP, CSV, XML, YAML.

No cloud. No npm. Python 3.9+ stdlib only.

```bash
pip install -e .          # or: pip install agent-session-bridge (once published)
asb status
asb list --json
asb export <session_id> -f md -o chat.md
asb export <session_id> -f pdf -o chat.pdf
asb export <session_id> -f all -o ./exports/
```

Used as a **library** by [Terminal Dashboard](https://github.com/mano7onam/terminal-dashboard) so parsers are not duplicated.

## Install

```bash
git clone https://github.com/mano7onam/agent-session-bridge.git
cd agent-session-bridge
python3 -m venv .venv
.venv/bin/pip install -e .
ln -sf "$PWD/.venv/bin/asb" ~/.local/bin/asb   # optional
./scripts/install_skills.sh                    # optional agent skill
```

Entry points: `asb` and `agent-session-bridge`.

## CLI

```bash
asb status                              # providers + formats
asb list -n 30
asb list --provider claude,grok --cwd ~/dev/myapp
asb show <id> --last 40
asb search "gatekeeper dmg" --json
asb pull <id> --query "topic" --mode query --max-chars 8000
asb export <id> -f md                   # stdout
asb export <id> -f html -o chat.html
asb export <id> -f pdf -o chat.pdf
asb export <id> -f json -o chat.json
asb export <id> -f zip -o chat.zip      # md+html+txt+json+csv+xml+yaml+assets
asb export <id> -f csv|xml|yaml -o …
asb export <id> -f all -o ./out/        # every format
asb export <id> -f md --tools --thinking
```

`<id>` = full session id, **prefix**, file path, or unique title substring.

### Pull modes (compact context for another agent)

| Mode | Content |
|------|---------|
| `handoff` / `auto` | Goals + decisions + recent turns |
| `query` | BM25 hits + neighbors |
| `last` / `code` / `errors` / `decisions` | Specialized slices |

## Library API

```python
from agent_session_bridge.providers import list_sessions, resolve_session
from agent_session_bridge.rich import load_transcript, list_sources_for_cwd
from agent_session_bridge.search import search_all
from agent_session_bridge.extract import smart_pull
from agent_session_bridge.exporters import render, SUPPORTED_FORMATS

sessions = list_sessions(limit=20)
t = load_transcript("claude_code", sessions[0].path, include_tools=True)
data, media_type, filename = render(t, "pdf")
open(filename, "wb").write(data)
```

## Providers

| Provider | Store |
|----------|--------|
| Claude Code | `~/.claude/projects/**/*.jsonl` |
| Codex | `~/.codex/sessions/**/rollout-*.jsonl` |
| Grok | `~/.grok/sessions/**/chat_history.jsonl` |
| Pi | `~/.pi/agent/sessions/**/*.jsonl` |

## Export formats

`md` · `txt` · `html` · `pdf` · `json` · `zip` · `csv` · `xml` · `yaml`

- **PDF**: Chrome/Edge/Brave headless when available; text fallback otherwise  
- **ZIP**: multi-format bundle + image assets  

## Skill (for agents)

```bash
./scripts/install_skills.sh
# → ~/.grok/skills/agent-session-bridge
# → ~/.claude/skills/agent-session-bridge
# → ~/.codex/skills/agent-session-bridge
```

Agents should:

1. `asb list --json`  
2. `asb search "<topic>" --json`  
3. `asb pull <id> --query "<topic>" --mode query` **or** `asb export <id> -f md -o /tmp/ctx.md`  

## License

MIT
