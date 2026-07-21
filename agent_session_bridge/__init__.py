"""Agent Session Bridge — shared library + CLI for local agent session context.

Library (importable by Terminal Dashboard and other tools):
  - agent_session_bridge.providers — fast list/load for Claude/Codex/Grok
  - agent_session_bridge.rich — full export transcripts (tools, attachments)
  - agent_session_bridge.search / extract — BM25 search + smart pull packs
  - agent_session_bridge.bootstrap — path discovery + `asb` CLI location

CLI entry points: `asb`, `agent-session-bridge`
"""

from .version import __version__

__all__ = ["__version__"]
