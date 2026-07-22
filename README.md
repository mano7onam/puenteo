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

for s in puenteo.list_sessions(limit=10):
    print(s.provider, s.session_id[:8], s.title)

t = puenteo.load("019f7a24", include_tools=True)
hits = puenteo.search("gatekeeper dmg")
msgs = puenteo.pull("019f7a24", query="export", mode="query")

puenteo.export_session("019f7a24", fmt="md", output="chat.md")
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

puenteo list -n 20 --json
asb list --provider claude,grok --cwd ~/dev/myapp
asb show <id> --last 40
asb search "topic" --json
asb pull <id> --query "topic" --mode query --max-chars 8000
asb export <id> -f md -o chat.md
puenteo export <id> -f pdf -o chat.pdf
asb export <id> -f all -o ./out/
```

`<id>` = full uuid, **prefix**, path, or unique title substring.

## Providers

| Provider | Store |
|----------|--------|
| Claude Code | `~/.claude/projects/**/*.jsonl` |
| Codex | `~/.codex/sessions/**/rollout-*.jsonl` |
| Grok | `~/.grok/sessions/**/chat_history.jsonl` |
| Pi | `~/.pi/agent/sessions/**/*.jsonl` |

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
