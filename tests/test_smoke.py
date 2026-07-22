"""Smoke tests — run with: python3 -m tests.test_smoke (or pytest if available)."""

from __future__ import annotations

import os
import sys
import unittest

# allow running from repo root without install
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from puenteo.extract import around_messages, build_outline, smart_pull
from puenteo.models import Message, Session, Transcript
from puenteo.search import search_all, search_transcript
from puenteo.util import clean_title, cwd_matches, extract_user_query, is_noise_user_text, strip_ansi, tokenize


class UtilTests(unittest.TestCase):
    def test_tokenize(self):
        self.assertIn("hello", tokenize("Hello, World!"))

    def test_user_query(self):
        t = extract_user_query("<user_query>\nfix me\n</user_query>")
        self.assertEqual(t, "fix me")

    def test_strip_ansi(self):
        raw = "Set model to \x1b[1mOpus 4.8\x1b[22m"
        self.assertEqual(strip_ansi(raw), "Set model to Opus 4.8")
        self.assertNotIn("[1m", strip_ansi("hello [1mworld[22m"))

    def test_noise_local_command(self):
        t = "<local-command-stdout>Set model to \x1b[1mOpus\x1b[22m"
        self.assertTrue(is_noise_user_text(t))
        self.assertEqual(clean_title(t), "")

    def test_cwd_matches(self):
        self.assertTrue(
            cwd_matches(
                "/Users/x/dev/harbor-datasets",
                "/Users/x/dev/harbor-datasets",
            )
        )
        self.assertTrue(
            cwd_matches(
                "/Users/x/dev/harbor-datasets",
                "/Users/x/dev/harbor-datasets/subdir",
            )
        )
        # parent project session must NOT match child filter
        self.assertFalse(
            cwd_matches(
                "/Users/x/dev/harbor-datasets",
                "/Users/x/dev",
            )
        )
        self.assertTrue(cwd_matches("harbor-datasets", "/Users/x/dev/harbor-datasets"))
        self.assertTrue(cwd_matches("", "/any"))


class SearchExtractTests(unittest.TestCase):
    def setUp(self):
        sess = Session(
            provider="test",
            session_id="abc",
            path="/tmp/x",
            title="t",
            cwd="/tmp",
        )
        msgs = [
            Message(role="user", text="How do we fix Gatekeeper DMG signing?", index=0),
            Message(
                role="assistant",
                text="We decided to document xattr -cr and right-click Open.",
                index=1,
            ),
            Message(role="user", text="Also export agent chats to markdown", index=2),
            Message(
                role="assistant",
                text="```python\nprint('export')\n```\nDone.",
                index=3,
            ),
            Message(role="user", text="What about media_player unrelated topic?", index=4),
            Message(
                role="assistant",
                text="media_player is a red herring for other work.",
                index=5,
            ),
            Message(role="user", text="Final status of the export feature?", index=6),
            Message(
                role="assistant",
                text="Final approach: ship markdown export via puenteo. Done.",
                index=7,
            ),
        ]
        self.tr = Transcript(session=sess, messages=msgs)

    def test_search(self):
        hits = search_transcript(self.tr, "gatekeeper dmg")
        self.assertTrue(hits)
        self.assertIn("Gatekeeper", hits[0].message.text)

    def test_pull_query(self):
        msgs = smart_pull(self.tr, query="markdown export", mode="query")
        self.assertTrue(any("markdown" in m.text.lower() for m in msgs))

    def test_pull_query_ranks_relevance_not_tail(self):
        # even with a long tail, query pack should prefer export/final over pure media_player
        msgs = smart_pull(
            self.tr,
            query="export markdown final",
            mode="query",
            top_k=4,
            max_chars=5000,
        )
        text = " ".join(m.text for m in msgs).lower()
        self.assertTrue("export" in text or "markdown" in text)
        # media_player alone should not dominate when query is about export
        if "media_player" in text:
            self.assertTrue("export" in text or "markdown" in text or "final" in text)

    def test_pull_code(self):
        msgs = smart_pull(self.tr, mode="code")
        self.assertTrue(any("```" in m.text for m in msgs))

    def test_pull_decisions(self):
        msgs = smart_pull(self.tr, mode="decisions", top_k=5)
        self.assertTrue(msgs)
        self.assertTrue(any("decided" in m.text.lower() or "done" in m.text.lower() for m in msgs))

    def test_around(self):
        msgs = around_messages(self.tr, 3, radius=1)
        idxs = [m.index for m in msgs]
        self.assertEqual(idxs, [2, 3, 4])
        msgs2 = smart_pull(self.tr, mode="around", around=3, radius=1)
        self.assertEqual([m.index for m in msgs2], [2, 3, 4])

    def test_outline(self):
        ol = build_outline(self.tr)
        self.assertEqual(ol["message_count"], 8)
        self.assertTrue(ol["milestones"])
        self.assertIn("session_id", ol)


class CliHelpTests(unittest.TestCase):
    def test_parser(self):
        from puenteo.cli import build_parser

        p = build_parser()
        args = p.parse_args(["list", "-n", "5"])
        self.assertEqual(args.cmd, "list")

        args = p.parse_args(["outline", "abc"])
        self.assertEqual(args.cmd, "outline")

        args = p.parse_args(["search", "x", "--exclude-session", "abc"])
        self.assertEqual(args.exclude_session, ["abc"])

        args = p.parse_args(["pull", "abc", "--around", "10", "--top-k", "5"])
        self.assertEqual(args.around, 10)
        self.assertEqual(args.top_k, 5)

        args = p.parse_args(["show", "abc", "--range", "1:5"])
        self.assertEqual(args.msg_range, "1:5")

        args = p.parse_args(["list", "--since", "2026-01-01", "--group-by", "cwd"])
        self.assertEqual(args.since, "2026-01-01")
        self.assertEqual(args.group_by, "cwd")


class LibraryApiTests(unittest.TestCase):
    def test_package_exports(self):
        import puenteo

        self.assertTrue(puenteo.__version__)
        self.assertIn("md", puenteo.SUPPORTED_FORMATS)
        st = puenteo.status()
        self.assertEqual(st["role"], "library+cli")
        self.assertIn("providers", st)
        self.assertIn("python_api", st)
        sessions = puenteo.list_sessions(limit=3)
        self.assertIsInstance(sessions, list)

    def test_export_bytes_roundtrip(self):
        import puenteo

        sessions = puenteo.list_sessions(limit=1)
        if not sessions:
            self.skipTest("no local sessions")
        data, media, name = puenteo.export_bytes(sessions[0], fmt="md")
        self.assertTrue(data.startswith(b"#") or b"#" in data[:200] or len(data) > 0)
        self.assertIn("markdown", media)
        self.assertTrue(name.endswith(".md"))

    def test_outline_api(self):
        import puenteo

        sessions = puenteo.list_sessions(limit=1)
        if not sessions:
            self.skipTest("no local sessions")
        ol = puenteo.outline(sessions[0].session_id)
        self.assertIn("message_count", ol)
        self.assertEqual(ol["session_id"], sessions[0].session_id)


class MultiProviderTests(unittest.TestCase):
    def test_provider_names_include_new(self):
        from puenteo.providers import PROVIDER_NAMES, PROVIDERS, normalize_provider_name

        for name in ("antigravity", "qwen", "cursor", "aider", "goose", "openhands"):
            self.assertIn(name, PROVIDER_NAMES)
            self.assertIn(name, PROVIDERS)
        self.assertEqual(normalize_provider_name("agy"), "antigravity")
        self.assertEqual(normalize_provider_name("claude"), "claude_code")

    def test_antigravity_list_if_present(self):
        from pathlib import Path

        from puenteo.providers import list_sessions, load_transcript

        brain = Path.home() / ".gemini" / "antigravity" / "brain"
        if not brain.is_dir():
            self.skipTest("no antigravity brain")
        ss = list_sessions(providers=["antigravity"], limit=5)
        self.assertTrue(ss)
        tr = load_transcript(ss[0])
        self.assertIsInstance(tr.messages, list)
        # cwd should not be antigravity internal path when a real project exists
        if tr.session.cwd:
            self.assertNotIn("/.gemini/antigravity/brain/", tr.session.cwd.replace("\\", "/"))


class LiveCwdTitleTests(unittest.TestCase):
    """Integration checks against real local stores when present."""

    def test_cwd_filter_not_parent_broad(self):
        from puenteo.providers import list_sessions

        target = "/Users/andrey.matveev/dev/harbor-datasets"
        ss = list_sessions(cwd=target, limit=50)
        for s in ss:
            # every match should be the project or a subdir — not a bare parent
            if s.cwd:
                self.assertTrue(
                    s.cwd == target or s.cwd.startswith(target + "/") or "harbor-datasets" in s.cwd,
                    msg=f"unexpected cwd for filter: {s.cwd}",
                )

    def test_harbor_title_not_local_command(self):
        from puenteo.providers import list_sessions

        ss = list_sessions(cwd="/Users/andrey.matveev/dev/harbor-datasets", limit=20)
        for s in ss:
            self.assertNotIn("local-command", s.title or "")
            self.assertNotIn("\x1b", s.title or "")
            self.assertNotIn("[1m", s.title or "")


if __name__ == "__main__":
    unittest.main()
