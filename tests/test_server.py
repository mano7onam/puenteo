"""puenteo serve: auth, host/origin checks, REST, MCP-over-HTTP, A2A, SSE."""

from __future__ import annotations

import json
import threading
import time
import urllib.request

import pytest


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("PUENTEO_BUS", str(tmp_path / "bus.db"))
    monkeypatch.setenv("PUENTEO_HOME", str(tmp_path / "ph"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PUENTEO_NO_PUSH", "1")
    from http.server import ThreadingHTTPServer

    from puenteo import server
    from puenteo.bus import Bus

    with Bus() as b:
        b.register("codex:aaaa1111", name="alice", via="mcp")
    gw = server.Gateway(token="tok123", me="user:web", port=0)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(gw))
    httpd.daemon_threads = True
    gw.port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{gw.port}"
    httpd.shutdown()


def call(url, method="GET", body=None, token="tok123", headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def test_auth_and_origin(srv):
    assert call(srv + "/api/ps", token=None)[0] == 401
    assert call(srv + "/api/ps", token="wrong")[0] == 401
    assert call(srv + "/api/ps", headers={"Origin": "https://evil.example"})[0] == 403
    assert call(srv + "/api/ps", headers={"Host": "evil.example:7357"})[0] == 403
    assert call(srv + "/api/health", token=None)[0] == 200


def test_rest_send_and_inbox(srv):
    code, m = call(srv + "/api/send", "POST", {"to": "@alice", "text": "hi via http"})
    assert code == 200 and m["meta"]["recipients"] == ["codex:aaaa1111"]
    code, msgs = call(srv + "/api/inbox?as=codex:aaaa1111")
    assert [x["body"] for x in msgs] == ["hi via http"]
    assert call(srv + "/api/send", "POST", {"to": "@nobody", "text": "x"})[0] == 400


def test_mcp_over_http(srv):
    code, r = call(srv + "/mcp", "POST", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert code == 200 and any(t["name"] == "send" for t in r["result"]["tools"])
    code, r = call(srv + "/mcp", "POST", {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert code == 202


def test_a2a_card_and_relay(srv):
    code, card = call(srv + "/.well-known/agent-card.json", token=None)
    assert code == 200 and card["supportedInterfaces"][0]["url"].endswith("/a2a")
    req = {"jsonrpc": "2.0", "id": 7, "method": "SendMessage",
           "params": {"message": {"role": "user", "messageId": "m1", "parts": [{"text": "status?"}],
                                  "metadata": {"to": "@alice", "from": "a2a:client"}}}}
    code, r = call(srv + "/a2a", "POST", req)
    task = r["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_WORKING"
    from puenteo.bus import Bus

    with Bus() as b:
        q = b.inbox("codex:aaaa1111")[0]
        b.send("codex:aaaa1111", "", "all green", reply_to=q.id)
    code, r = call(srv + "/a2a", "POST", {"jsonrpc": "2.0", "id": 8, "method": "GetTask", "params": {"id": task["id"]}})
    assert r["result"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert r["result"]["artifacts"][0]["parts"][0]["text"] == "all green"


def test_sse_streams_new_messages(srv):
    got = []

    ready = threading.Event()

    def listen():
        req = urllib.request.Request(srv + "/api/events?token=tok123")
        with urllib.request.urlopen(req, timeout=10) as r:
            event = ""
            for raw in r:
                line = raw.decode().strip()
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: "):
                    if event == "ready":
                        ready.set()
                    else:
                        got.append(json.loads(line[6:]))
                        return

    t = threading.Thread(target=listen, daemon=True)
    t.start()
    assert ready.wait(5)
    t0 = time.time()
    call(srv + "/api/send", "POST", {"to": "@alice", "text": "streamed"})
    t.join(8)
    assert got and got[0]["body"] == "streamed"
    from puenteo.notify import SUPPORTED

    if SUPPORTED:
        assert time.time() - t0 < 2, "doorbell should wake the SSE stream immediately"


def test_dashboard_page(srv):
    with urllib.request.urlopen(srv + "/", timeout=5) as r:
        html = r.read().decode()
    assert "<title>puenteo</title>" in html and "EventSource" in html


def test_mesh_endpoint(srv):
    code, r = call(srv + "/api/mesh")
    assert code == 200 and "peers" in r and "offers" in r and "bridge_running" in r["peers"]
