#!/usr/bin/env bash
# Install Agent Session Bridge skill into local agent skill directories.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/skills/agent-session-bridge"

if [[ ! -f "$SRC/SKILL.md" ]]; then
  echo "Missing $SRC/SKILL.md" >&2
  exit 1
fi

link_skill() {
  local dest_parent="$1"
  local name="$2"
  mkdir -p "$dest_parent"
  local dest="$dest_parent/$name"
  if [[ -L "$dest" || -d "$dest" || -f "$dest" ]]; then
    rm -rf "$dest"
  fi
  ln -s "$SRC" "$dest"
  echo "Linked $dest -> $SRC"
}

# Grok user skills
if [[ -d "$HOME/.grok" ]]; then
  link_skill "$HOME/.grok/skills" "agent-session-bridge"
fi

# Claude Code — user skills dir (create if claude home exists)
if [[ -d "$HOME/.claude" ]]; then
  link_skill "$HOME/.claude/skills" "agent-session-bridge"
fi

# Codex skills
if [[ -d "$HOME/.codex" ]]; then
  link_skill "$HOME/.codex/skills" "agent-session-bridge"
fi

echo "Done. Restart agents / new sessions to pick up the skill."
echo "Verify CLI: asb status"
