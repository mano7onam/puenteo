"""Adds an MCP tool to the puenteo server."""

PUENTEO_API = 1


def register(server) -> None:
    @server.tool("example_count_sessions", "Count local agent sessions per provider (example plugin).", {
        "type": "object", "properties": {}, "additionalProperties": False})
    def _count(args):
        from collections import Counter

        from puenteo.providers import list_sessions

        return dict(Counter(s.provider for s in list_sessions(limit=0)))
