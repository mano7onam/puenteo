"""Hermetic tests for core behaviour (fake $HOME, no real agent data)."""

from __future__ import annotations

import os

import pytest

from conftest import CLAUDE_SID, CODEX_ROOT, CODEX_SUB


def _ids(sessions):
    return {s.session_id for s in sessions}


def test_list_finds_all_providers(fake_home):
    from puenteo.providers import list_sessions

    ss = list_sessions(limit=0)
    provs = {s.provider for s in ss}
    assert {"claude_code", "codex", "gemini", "continue"} <= provs
    assert CODEX_SUB in _ids(ss), "subagent must keep its own id, not the parent's"
    assert CODEX_ROOT in _ids(ss)


def test_codex_subagent_meta(fake_home):
    from puenteo.providers import list_sessions

    sub = [s for s in list_sessions(providers=["codex"], limit=0) if s.session_id == CODEX_SUB][0]
    assert sub.meta.get("parent_id") == CODEX_ROOT
    assert "Hubble" in sub.title


def test_codex_title_skips_agents_md(fake_home):
    from puenteo.providers import list_sessions, load_transcript

    root = [s for s in list_sessions(providers=["codex"], limit=0) if s.session_id == CODEX_ROOT][0]
    tr = load_transcript(root)
    assert not tr.session.title.startswith("# AGENTS.md")
    assert all("AGENTS.md" not in m.text for m in tr.messages)


def test_codex_mirror_dedup_light_and_rich(fake_home):
    import puenteo

    tr = puenteo.load(CODEX_ROOT, rich=False)
    texts = [(m.role, m.text) for m in tr.messages]
    assert len(texts) == len(set(texts)) == 2
    rich = puenteo.load(CODEX_ROOT, rich=True)
    rtexts = [(m.role, m.text) for m in rich.messages if m.text and m.role in ("user", "assistant")]
    assert len(rtexts) == len(set(rtexts))


def test_ambiguous_prefix_raises(fake_home):
    from puenteo.providers import AmbiguousSessionError, resolve_session

    with pytest.raises(AmbiguousSessionError) as ei:
        resolve_session("01a0fe44")
    assert len(ei.value.candidates) == 2
    assert resolve_session("01a0fe44-d").session_id == CODEX_ROOT
    assert resolve_session("codex:01a0fe44-f").session_id == CODEX_SUB


def test_resolve_last(fake_home):
    from puenteo.providers import resolve_session

    assert resolve_session("@last:claude").session_id == CLAUDE_SID


def test_claude_meta_skipped_and_fragments_merged(fake_home):
    import puenteo

    tr = puenteo.load(CLAUDE_SID, rich=False, include_tools=True)
    joined = "\n".join(m.text for m in tr.messages)
    assert "Skill body" not in joined
    assistants = [m for m in tr.messages if m.role == "assistant"]
    assert len(assistants) == 2
    assert "notarization" in assistants[0].text and "codesign" in assistants[0].text


def test_gemini_and_continue_roles(fake_home):
    from puenteo.providers import list_sessions, load_transcript

    g = list_sessions(providers=["gemini"], limit=0)[0]
    roles = [m.role for m in load_transcript(g).messages]
    assert roles == ["user", "assistant"]
    c = list_sessions(providers=["continue"], limit=0)[0]
    cm = load_transcript(c).messages
    assert [m.role for m in cm] == ["user", "assistant"]
    assert cm[0].text == "continue user text"


def test_cwd_dot_is_current_dir(fake_home, monkeypatch):
    from puenteo.util import cwd_matches

    monkeypatch.chdir(fake_home["proj"])
    assert cwd_matches(".", str(fake_home["proj"]))
    assert not cwd_matches(".", "/somewhere/.air/x")


def test_unique_prefixes():
    from puenteo.util import unique_prefixes

    p = unique_prefixes([CODEX_ROOT, CODEX_SUB, CLAUDE_SID])
    assert p[CLAUDE_SID] == CLAUDE_SID[:8]
    assert p[CODEX_ROOT] != p[CODEX_SUB]
    assert CODEX_ROOT.startswith(p[CODEX_ROOT])


def test_index_search_global(fake_home):
    from puenteo import index
    from puenteo.search import search_all

    if not index.available():
        pytest.skip("no FTS5")
    hits = search_all("gatekeeper notarytool")
    assert hits and hits[0].session.session_id == CLAUDE_SID
    # meta (skill body) is not indexed
    assert all("Skill body" not in h.message.text for h in hits)
    # exclude works
    assert not [h for h in search_all("gatekeeper", exclude_session=CLAUDE_SID[:8])
                if h.session.session_id == CLAUDE_SID]


def test_scan_fallback_matches_index(fake_home):
    from puenteo.search import search_all

    hits = search_all("markdown export", use_index=False)
    assert hits and hits[0].session.session_id == CODEX_ROOT


def test_handoff_keeps_tail():
    from puenteo.extract import smart_pull
    from puenteo.models import Message, Session, Transcript

    msgs = [Message(role="user" if i % 2 == 0 else "assistant", text=f"msg{i} " + "x" * 3000, index=i)
            for i in range(40)]
    tr = Transcript(session=Session(provider="t", session_id="s", path="p"), messages=msgs)
    out = smart_pull(tr, mode="handoff", max_chars=8000)
    assert out[-1].index == 39, "the latest message must survive the budget"
    assert out[0].index == 0, "the opening goal should survive too"
    assert sum(len(m.text) for m in out) <= 8000


def test_budget_never_exceeds_with_huge_first_message():
    from puenteo.extract import smart_pull
    from puenteo.models import Message, Session, Transcript

    tr = Transcript(session=Session(provider="t", session_id="s", path="p"),
                    messages=[Message(role="user", text="y" * 100000, index=0)])
    out = smart_pull(tr, mode="last", max_chars=5000)
    assert sum(len(m.text) for m in out) <= 5000


def test_cli_ambiguous_exit_code(fake_home, capsys):
    from puenteo.cli import main

    assert main(["show", "01a0fe44"]) == 4
    assert "Ambiguous" in capsys.readouterr().err


def test_cli_list_json(fake_home, capsys):
    import json

    from puenteo.cli import main

    assert main(["list", "--json", "-n", "0"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) >= 5


def test_metacache_reuses(fake_home):
    from puenteo import metacache

    calls = []
    p = str(fake_home["proj"] / "f.txt")
    open(p, "w").write("a")
    for _ in range(3):
        metacache.cached("t", p, lambda: calls.append(1) or 7)
    assert len(calls) == 1
    open(p, "w").write("bb")
    metacache.cached("t", p, lambda: calls.append(1) or 7)
    assert len(calls) == 2


def test_opencode_and_copilot(more_providers):
    from puenteo.providers import list_sessions, load_transcript, resolve_session

    oc = list_sessions(providers=["opencode"], limit=0)
    assert [s.session_id for s in oc] == ["ses_abc"]
    tr = load_transcript(oc[0])
    assert [(m.role, m.text) for m in tr.messages] == [("user", "refactor the tokenizer"), ("assistant", "tokenizer refactored")]
    assert "[tool_use edit]" in load_transcript(oc[0], include_tools=True).messages[1].text

    cp = list_sessions(providers=["copilot"], limit=0)
    assert len(cp) == 1 and cp[0].title == "Fix login"
    msgs = load_transcript(cp[0]).messages
    assert msgs[0].text == "fix the login bug" and msgs[1].role == "assistant"
    assert resolve_session("copilot:c0ffee").session_id.startswith("c0ffee00")


def test_redaction():
    from puenteo.redact import redact

    raw = (
        "key sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAA and ghp_" + "a" * 36 + "\n"
        "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz123\n"
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123\n"
        "postgres://admin:hunter22@db.local/x  AKIAABCDEFGHIJKLMNOP\n"
        "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----\n"
        "normal text sk-short and tokenizer"
    )
    out = redact(raw)
    for leaked in ("sk-ant-api03", "ghp_aaaa", "sk-proj-abc", "abcdefghijklmnopqrstuvwxyz0123", "hunter22", "AKIAABCD", "MIIE"):
        assert leaked not in out, leaked
    assert "OPENAI_API_KEY=" in out and "Authorization: Bearer" in out and "postgres://admin:" in out
    assert "normal text sk-short and tokenizer" in out


def test_cli_redacts_pull(fake_home, capsys, monkeypatch):
    import json as _json

    from conftest import _jsonl
    from puenteo.cli import main

    enc = "".join(c if c.isalnum() else "-" for c in str(fake_home["proj"]))
    _jsonl(fake_home["home"] / ".claude" / "projects" / enc / "22222222-aaaa-4bbb-8ccc-000000000002.jsonl", [
        {"type": "user", "sessionId": "22222222-aaaa-4bbb-8ccc-000000000002", "cwd": str(fake_home["proj"]),
         "message": {"role": "user", "content": "my token is ghp_" + "b" * 36}},
    ])
    assert main(["pull", "22222222", "--mode", "last"]) == 0
    out = capsys.readouterr().out
    assert "ghp_bbbb" not in out and "[REDACTED:github]" in out
    assert main(["pull", "22222222", "--mode", "last", "--no-redact"]) == 0
    assert "ghp_bbbb" in capsys.readouterr().out


def test_cwd_filter_ignores_relative_session_cwd(tmp_path, monkeypatch):
    from puenteo.util import cwd_matches

    monkeypatch.chdir(tmp_path)
    assert not cwd_matches(str(tmp_path), "-Users-x-dev-other")
    assert cwd_matches("other", "-Users-x-dev-other")
