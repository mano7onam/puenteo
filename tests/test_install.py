"""puenteo install: idempotent, minimal edits, reversible (fake $HOME, no agent CLIs)."""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    (h / ".codex" / "skills").mkdir(parents=True)
    (h / ".gemini").mkdir()
    (h / ".gemini" / "settings.json").write_text(json.dumps({"theme": "dark", "mcpServers": {"other": {"command": "x"}}}))
    (h / ".claude" / "settings.json").write_text(json.dumps({
        "model": "opus",
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]},
    }))
    try:
        (h / ".codex" / "skills" / "puenteo").symlink_to(tmp_path / "gone" / "skills" / "puenteo")
    except OSError:  # Windows without symlink privilege
        pass
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.setenv("PATH", str(tmp_path / "nonexistent"))  # no claude/codex CLIs → their MCP steps skip
    return h


def test_install_idempotent_and_reversible(home):
    from puenteo.install import plan_and_apply

    steps = plan_and_apply(hooks=True)
    assert (home / ".claude" / "skills" / "puenteo" / "SKILL.md").exists()
    assert (home / ".agents" / "skills" / "puenteo-bus" / "SKILL.md").exists()
    assert not (home / ".codex" / "skills" / "puenteo").is_symlink(), "legacy dangling link removed"

    g = json.loads((home / ".gemini" / "settings.json").read_text())
    assert g["theme"] == "dark" and "other" in g["mcpServers"] and g["mcpServers"]["puenteo"]["args"][-1] == "mcp"
    assert (home / ".gemini" / "settings.json.puenteo-bak").exists()

    c = json.loads((home / ".claude" / "settings.json").read_text())
    assert c["model"] == "opus"
    stop_cmds = [h["command"] for g in c["hooks"]["Stop"] for h in g["hooks"]]
    assert "say done" in stop_cmds and any("hook Stop" in x for x in stop_cmds)
    assert any("skipped" in s.action for s in steps if s.kind == "mcp" and s.agent == "claude")

    again = plan_and_apply(hooks=True)
    assert all(s.action in ("unchanged",) or s.action.startswith("skipped") for s in again), [
        (s.agent, s.kind, s.action) for s in again if s.action != "unchanged"]

    plan_and_apply(hooks=True, remove=True)
    assert not (home / ".claude" / "skills" / "puenteo").exists()
    g = json.loads((home / ".gemini" / "settings.json").read_text())
    assert "puenteo" not in g["mcpServers"] and "other" in g["mcpServers"]
    c = json.loads((home / ".claude" / "settings.json").read_text())
    assert [h["command"] for g in c["hooks"]["Stop"] for h in g["hooks"]] == ["say done"]
    assert set(c["hooks"]) == {"Stop"}


def test_dry_run_touches_nothing(home):
    from puenteo.install import plan_and_apply

    before = (home / ".gemini" / "settings.json").read_text()
    steps = plan_and_apply(hooks=True, dry=True)
    assert steps
    assert (home / ".gemini" / "settings.json").read_text() == before
    assert not (home / ".claude" / "skills").exists()


def test_uninstall_leaves_foreign_skill(home):
    from puenteo.install import plan_and_apply

    mine = home / ".claude" / "skills" / "puenteo"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("user's own skill")
    plan_and_apply(remove=True, only=["claude"])
    assert (mine / "SKILL.md").read_text() == "user's own skill"


def test_skills_are_packaged():
    from puenteo.install import SKILLS, skills_source

    for name in SKILLS:
        text = (skills_source() / name / "SKILL.md").read_text()
        assert text.startswith("---\nname: " + name)


def test_plugin_skills_in_sync():
    """plugin/skills is a real copy (plugins can't rely on symlinks); keep it identical."""
    import pathlib

    from puenteo.install import SKILLS, skills_source

    repo = pathlib.Path(__file__).resolve().parents[1]
    for root in (repo / "plugin" / "skills", repo / "skills"):
        if not root.exists():
            pytest.skip("not a source checkout")
        for name in SKILLS:
            assert (root / name / "SKILL.md").read_text() == (skills_source() / name / "SKILL.md").read_text(), (
                f"run: cp -R puenteo/data/skills/. {root.relative_to(repo)}/")


def test_manifest_versions_match():
    import json
    import pathlib

    from puenteo.version import __version__

    repo = pathlib.Path(__file__).resolve().parents[1]
    for rel in ("plugin/.claude-plugin/plugin.json", "gemini-extension.json"):
        p = repo / rel
        if p.exists():
            assert json.loads(p.read_text())["version"] == __version__, rel


@pytest.mark.skipif(__import__("sys").platform == "win32", reason="POSIX symlinks and file modes")
def test_install_writes_through_symlink_and_keeps_mode(home, tmp_path):
    import os
    import stat

    from puenteo.install import plan_and_apply

    real = tmp_path / "dotfiles" / "gemini.json"
    real.parent.mkdir()
    real.write_text("{}")
    os.chmod(real, 0o640)
    link = home / ".gemini" / "settings.json"
    link.unlink()
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("no symlinks")
    plan_and_apply(only=["gemini"])
    assert link.is_symlink()
    assert "puenteo" in json.loads(real.read_text())["mcpServers"]
    assert stat.S_IMODE(real.stat().st_mode) == 0o640


def test_install_never_overwrites_foreign_skill(home):
    from puenteo.install import plan_and_apply

    mine = home / ".claude" / "skills" / "puenteo"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("user's own")
    steps = plan_and_apply(only=["claude"])
    assert (mine / "SKILL.md").read_text() == "user's own"
    assert any("not installed by puenteo" in s.action for s in steps)


def test_uninstall_keeps_lookalike_user_hook(home):
    from puenteo.install import plan_and_apply

    p = home / ".claude" / "settings.json"
    d = json.loads(p.read_text())
    d["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "echo puenteo hook done"}]})
    p.write_text(json.dumps(d))
    plan_and_apply(hooks=True, only=["claude"])
    plan_and_apply(hooks=True, only=["claude"], remove=True)
    cmds = [h["command"] for g in json.loads(p.read_text())["hooks"]["Stop"] for h in g["hooks"]]
    assert "echo puenteo hook done" in cmds and "say done" in cmds


def test_jsonc_config_is_refused_not_mangled(home):
    from puenteo.install import plan_and_apply

    p = home / ".gemini" / "settings.json"
    p.write_text('{\n  // my comment\n  "theme": "dark"\n}\n')
    steps = plan_and_apply(only=["gemini"])
    assert "// my comment" in p.read_text()
    assert any(s.action.startswith("failed") for s in steps if s.kind == "mcp")


def test_mcp_registry_manifest():
    import json
    import pathlib

    p = pathlib.Path(__file__).resolve().parents[1] / "server.json"
    if not p.exists():
        pytest.skip("not a source checkout")
    d = json.loads(p.read_text())
    assert len(d["description"]) <= 100, "MCP Registry limit"
    readme = (p.parent / "README.md").read_text()
    assert f"mcp-name: {d['name']}" in readme, "PyPI ownership marker"
