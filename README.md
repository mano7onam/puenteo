# puenteo

**The bridge between coding agents.**

Python **library + CLI** to discover, search, and export local sessions from Claude Code, Codex, Grok, and Pi — to Markdown, HTML, PDF, JSON, ZIP, CSV, XML, YAML.

*Puenteo* ← Spanish *puente* (bridge) + *puentear* (to bridge / jump across).

Zero runtime dependencies · Python ≥ 3.9

[![PyPI](https://img.shields.io/pypi/v/puenteo.svg)](https://pypi.org/project/puenteo/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## Install

```bash
pip install puenteo
# or
uv add puenteo
# or
pipx install puenteo
```

From source:

```bash
git clone https://github.com/mano7onam/puenteo.git
cd puenteo
python3 -m venv .venv && .venv/bin/pip install -e .
```

## Library

```python
import puenteo

print(puenteo.status())

for s in puenteo.list_sessions(limit=10, cwd="~/dev/myapp"):
    print(s.provider, s.session_id[:8], s.title)

t = puenteo.load("019f7a24", include_tools=True)
hits = puenteo.search("gatekeeper dmg", exclude_session="my-current-id")
msgs = puenteo.pull("019f7a24", query="export", mode="query", top_k=15)
print(puenteo.outline("019f7a24"))

puenteo.export_session("019f7a24", fmt="md", output="chat.md")
puenteo.export_session("019f7a24", fmt="md", output="slice.md", query="export")
puenteo.export_session("019f7a24", fmt="pdf", output="chat.pdf")
puenteo.export_session("019f7a24", fmt="all", output="./exports/")

data, media_type, filename = puenteo.export_bytes("019f7a24", fmt="html")
```

## CLI

After `pip install puenteo` three commands point to the same CLI:

| Command | Notes |
|---------|--------|
| **`puenteo`** | main name |
| **`asb`** | short alias (Agent Session Bridge vibe) |
| **`pto`** | ultra-short |

```bash
puenteo status          # same as:
asb status
pto status

# Discover
puenteo list -n 20 --json
asb list --provider claude,grok --cwd ~/dev/myapp
asb list --since 2026-07-01 --group-by cwd

# Map a session, then pull what matters
asb outline <id>
asb show <id> --last 40
asb show <id> --range 100:120
asb search "topic" --json
asb search "topic" --session <id>
asb search "topic" --exclude-session <my-id>   # global search without yourself

asb pull <id> --query "topic" --mode query --top-k 15 --max-chars 8000
asb pull <id> --mode decisions --top-k 15
asb pull <id> --around 500 --radius 5

asb export <id> -f md -o chat.md
asb export <id> --query "topic" -f md -o slice.md
puenteo export <id> -f pdf -o chat.pdf
asb export <id> -f all -o ./out/
```

`<id>` = full uuid, **unique prefix** (e.g. `3627012b`), path, or unique title substring.

### Agent handoff recipe

```bash
puenteo status
puenteo list --cwd ~/dev/myapp
puenteo outline <id>
puenteo pull <id> --mode decisions --top-k 15
puenteo search 'keyword' --session <id>
puenteo pull <id> --around 500
```

## Providers

| Provider | Store |
|----------|--------|
| Claude Code | `~/.claude/projects/**/*.jsonl` |
| Codex | `~/.codex/sessions/**/rollout-*.jsonl` |
| Grok | `~/.grok/sessions/**/chat_history.jsonl` |
| Pi | `~/.pi/agent/sessions/**/*.jsonl` |
| **Antigravity** | `~/.gemini/antigravity/brain/*/…/transcript*.jsonl` |
| Qwen Code | `~/.qwen/projects/**/chats/*` |
| Gemini CLI | `~/.gemini` (non-antigravity chat dumps) |
| Cursor | `~/Library/Application Support/Cursor/**/state.vscdb` |
| Continue | `~/.continue/sessions/**` |
| Aider | `.aider.chat.history.md` (scan with `--cwd` or `PUENTEO_AIDER_ROOTS`) |
| OpenHands | `~/.openhands/openhands.db` |
| Goose | `~/.config/goose/**` |

```bash
asb list --provider antigravity,claude -n 20
asb pull <agy-id> --query "topic" --mode query
```

## Export formats

`md` · `txt` · `html` · `pdf` · `json` · `zip` · `csv` · `xml` · `yaml`

## Agent skill

```bash
./scripts/install_skills.sh
```

## Used by

[Terminal Dashboard](https://github.com/mano7onam/terminal-dashboard) imports **puenteo** for chat export (shared parsers, no duplication).

## License

MIT · [mano7onam/puenteo](https://github.com/mano7onam/puenteo)
