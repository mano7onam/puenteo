"""Every skill is well-formed, installed everywhere, and every `puenteo …` command it shows parses."""

from __future__ import annotations

import pathlib
import re
import shlex

import pytest

from puenteo.install import SKILLS, skills_source

ROOT = skills_source()
ALL = sorted(p.name for p in ROOT.iterdir() if (p / "SKILL.md").exists())


def test_install_ships_every_skill():
    assert sorted(SKILLS) == ALL


@pytest.mark.parametrize("name", ALL)
def test_frontmatter(name):
    text = (ROOT / name / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"^---\nname: (\S+)\ndescription: >-\n((?:  .*\n)+)---\n", text)
    assert m, f"{name}: bad frontmatter"
    assert m.group(1) == name
    desc = " ".join(l.strip() for l in m.group(2).splitlines())
    assert 80 <= len(desc) <= 1024, f"{name}: description length {len(desc)}"
    assert "Use when" in desc, f"{name}: description must say when to use it"


def _commands(text: str):
    lines = []
    for lang, block in re.findall(r"```([\w-]*)\n(.*?)```", text, re.S):  # every fence, with its language
        if lang in ("", "bash", "sh", "shell", "console"):
            lines += block.splitlines()
    lines += re.findall(r"`(puenteo [^`]+)`", text)  # inline commands in prose and tables
    if True:
        for line in lines:
            line = line.split(" #")[0].strip()
            for part in re.split(r"\s*(?:\|\||&&|\|)\s*", line):
                part = part.strip()
                if part.startswith("puenteo ") and "<" not in part.split(" ", 2)[1]:
                    yield part


@pytest.mark.parametrize("name", ALL)
def test_documented_commands_parse(name):
    from puenteo.cli import build_parser

    parser = build_parser()
    text = (ROOT / name / "SKILL.md").read_text(encoding="utf-8")
    checked = 0
    for cmd in _commands(text):
        argv = shlex.split(re.sub(r"<[^>]+>", "X", cmd).replace("…", "x"))[1:]
        if argv[:1] == ["mcp"]:
            continue
        try:
            parser.parse_args(argv)
        except SystemExit as e:
            if e.code not in (0, None):  # --help / --version exit 0
                pytest.fail(f"{name}: `{cmd}` does not parse (exit {e.code})")
        checked += 1
    assert checked, f"{name}: no commands found to check"
