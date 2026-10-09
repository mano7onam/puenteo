---
name: puenteo-export
description: >-
  Save or share an agent conversation as a file: PDF, HTML, Markdown, JSON, ZIP, CSV, XML or YAML,
  full or only the relevant part, with secrets masked — from any local agent (Claude Code, Codex,
  Gemini, Cursor, OpenCode, Copilot, …). Use when the user wants to export, print, archive, attach or
  send a chat transcript ("make a PDF of the codex chat for my manager", "save this session as
  markdown", "share what the agent did").
---

# Export conversations

```bash
puenteo export <ref> -f md -o chat.md                 # full transcript
puenteo export <ref> -f html -o chat.html --tools     # include tool calls (--thinking for reasoning)
puenteo export <ref> -f pdf -o chat.pdf
puenteo export <ref> -f all -o ./exports/             # md txt html pdf json zip csv xml yaml
puenteo export <ref> --query "auth bug" -f md -o slice.md   # only the relevant part
puenteo export <ref> -f md --redact -o share.md       # mask API keys/tokens before sharing
```

- `<ref>`: an id prefix from `puenteo list` or `search`, `provider:id`, `@last`, `@last:codex`, or a path.
- **Before sharing outside the machine**, always use `--redact` and skim the file. Transcripts contain tool output, paths and sometimes credentials.
- **For a short summary** instead of a dump: `puenteo pull <ref> -o brief.md` (structured handoff).
- **Python**: `puenteo.export_session(ref, fmt="md", output="chat.md")` or `puenteo.export_bytes(ref, fmt="html")`.
