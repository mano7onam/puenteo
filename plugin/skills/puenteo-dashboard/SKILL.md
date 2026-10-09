---
name: puenteo-dashboard
description: >-
  Run and use the puenteo local web dashboard and HTTP gateway: live view of all agent sessions, bus
  traffic, claims, other machines and bazaar offers; REST API, Server-Sent Events, MCP over HTTP and
  an A2A v1.0 endpoint. Use when the user wants a UI to watch agents, "open the dashboard", a REST/SSE
  API for puenteo, MCP over HTTP, A2A, or to integrate a non-shell tool.
---

# Dashboard and HTTP gateway

```bash
puenteo serve --open            # http://127.0.0.1:7357 in the browser
puenteo serve --port 8000
puenteo serve --print-token     # bearer token (stored 0600 in the state dir)
```

The dashboard shows live sessions, the message feed (SSE), claims, other machines, bazaar offers, a send box and cross-agent history search. Click a session or an offer to address it.

## API (localhost only; `Authorization: Bearer <token>`)

| Endpoint | What |
|---|---|
| `GET /api/ps`, `/api/sessions?cwd=&provider=&limit=` | live and past sessions |
| `GET /api/search?q=…`, `/api/pull?session=…&q=`, `/api/outline?session=…` | history |
| `GET /api/log?channel=#x`, `/api/channels`, `/api/claims`, `/api/mesh` | bus state |
| `GET /api/inbox?as=agent:id` | read an inbox (`peek=1` to keep unread) |
| `POST /api/send {to, text, as?, thread?, reply_to?}` | send |
| `POST /api/claim {resource, ttl_s?, note?}` | claim |
| `GET /api/events?token=…[&address=…]` | SSE stream of new messages (`Last-Event-ID` resume) |
| `POST /mcp` | MCP JSON-RPC over HTTP (same tools as `puenteo mcp`) |
| `GET /.well-known/agent-card.json`, `POST /a2a` | A2A v1.0: `SendMessage` (metadata.to relays to a session; no `to` = history search), `GetTask` |

Example:

```bash
T=$(puenteo serve --print-token)
curl -s -H "Authorization: Bearer $T" localhost:7357/api/ps
curl -N "localhost:7357/api/events?token=$T"
```

Security: the gateway binds to 127.0.0.1 only and rejects non-localhost Host and Origin headers. Never expose it with a tunnel unless the user explicitly asks.
