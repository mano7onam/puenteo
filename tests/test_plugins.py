"""Plugin system: all four entry-point groups, isolation of broken plugins, no overriding built-ins."""

from __future__ import annotations

import json
import sys
import types

import pytest


class _EP:
    def __init__(self, name, obj, group):
        self.name, self._obj, self.group = name, obj, group
        self.dist = types.SimpleNamespace(name="fake-dist", version="9.9")

    def load(self):
        if isinstance(self._obj, Exception):
            raise self._obj
        return self._obj


@pytest.fixture()
def plug(fake_home, monkeypatch, tmp_path):
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "examples" / "puenteo-example-plugin"))
    import puenteo_example.delivery as delivery
    import puenteo_example.provider as provider
    import puenteo_example.tools as tools

    from puenteo import plugins

    home = tmp_path / "notes"
    home.mkdir()
    monkeypatch.setenv("NOTES_AGENT_HOME", str(home))
    (home / "n1.jsonl").write_text(
        json.dumps({"meta": {"title": "notes chat", "cwd": str(fake_home["proj"])}}) + "\n"
        + json.dumps({"role": "user", "text": "plugin provider works"}) + "\n", encoding="utf-8")
    eps = {
        "puenteo.providers": [_EP("notes", provider, "p"), _EP("claude", provider, "p"), _EP("broken", RuntimeError("boom"), "p")],
        "puenteo.tools": [_EP("example", tools.register, "t"), _EP("bad", lambda s: 1 / 0, "t")],
        "puenteo.delivery": [_EP("file", delivery.push, "d")],
        "puenteo.live": [_EP("notes", provider.live_sessions, "l")],
    }
    monkeypatch.setattr(plugins, "_entry_points", lambda g: eps.get(g, []))
    monkeypatch.setenv("PUENTEO_QUIET", "1")
    plugins.reset()
    import puenteo.providers as prov

    monkeypatch.setattr(prov, "_plugins_merged", False)
    saved = dict(prov.PROVIDERS), prov.PROVIDER_NAMES
    yield home
    prov.PROVIDERS.clear()
    prov.PROVIDERS.update(saved[0])
    prov.PROVIDER_NAMES = saved[1]
    plugins.reset()


def test_provider_plugin_is_searchable(plug):
    from puenteo.providers import PROVIDERS, list_sessions, load_transcript, resolve_session

    ss = list_sessions(providers=["notes"], limit=0)
    assert [s.title for s in ss] == ["notes chat"]
    assert load_transcript(ss[0]).messages[0].text == "plugin provider works"
    assert resolve_session("notes:n1").session_id == "n1"
    assert PROVIDERS["claude"].__name__ == "puenteo.providers.claude", "plugins must not override built-ins"
    from puenteo import plugins

    errs = " ".join(plugins.errors())
    assert "broken" in errs and "clashes" in errs


def test_tool_plugin_and_isolation(plug):
    from puenteo.mcp import Server

    srv = Server(me="test:me")
    assert "example_count_sessions" in srv.tools and srv.tools["example_count_sessions"]["annotations"]["plugin"]
    assert "send" in srv.tools  # built-ins intact despite the crashing plugin
    assert srv.handlers["example_count_sessions"]({}).get("notes") == 1


def test_delivery_plugin(plug, monkeypatch):
    from puenteo.bus import Bus
    from puenteo.deliver import push_pending

    monkeypatch.delenv("PUENTEO_NO_PUSH", raising=False)
    with Bus() as b:
        m = b.send("user:x", "notes:pid1", "hello notes agent")
        r = push_pending(b, m)
    assert r["notes:pid1"].startswith("appended to notes inbox")
    assert "hello notes agent" in (plug / "inbox.jsonl").read_text(encoding="utf-8")


def test_live_plugin(plug):
    import os

    from puenteo import live

    (plug / "running.pid").write_text(str(os.getpid()), encoding="utf-8")
    live._live_cache.clear()
    found = [s for s in live.live_sessions(fresh=True) if s.agent == "notes"]
    assert found and found[0].pid == os.getpid()


def test_plugins_cli(plug, capsys):
    from puenteo.cli import main

    rc = main(["plugins", "--json"])
    d = json.loads(capsys.readouterr().out)
    assert {"puenteo.providers", "puenteo.tools", "puenteo.delivery", "puenteo.live"} <= {r["group"] for r in d["plugins"]}
    assert rc == 1 and d["errors"]  # the deliberately broken plugins are reported, not fatal
