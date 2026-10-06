# Minimal image for MCP directories (Glama, etc.): runs the stdio MCP server.
# Real use is local (`uvx puenteo mcp`): the server reads agent session files on the host.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PUENTEO_HOME=/tmp/puenteo
RUN pip install --no-cache-dir "puenteo>=0.9.1"
ENTRYPOINT ["puenteo", "mcp"]
