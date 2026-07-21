"""Smoke tests — run with: python3 -m tests.test_smoke (or pytest if available)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

# allow running from repo root without install
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent_session_bridge.extract import smart_pull
from agent_session_bridge.models import Message, Session, Transcript
from agent_session_bridge.search import search_transcript
from agent_session_bridge.util import extract_user_query, tokenize


class UtilTests(unittest.TestCase):
    def test_tokenize(self):
        self.assertIn("hello", tokenize("Hello, World!"))

    def test_user_query(self):
        t = extract_user_query("<user_query>\nfix me\n</user_query>")
        self.assertEqual(t, "fix me")


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
        ]
        self.tr = Transcript(session=sess, messages=msgs)

    def test_search(self):
        hits = search_transcript(self.tr, "gatekeeper dmg")
        self.assertTrue(hits)
        self.assertIn("Gatekeeper", hits[0].message.text)

    def test_pull_query(self):
        msgs = smart_pull(self.tr, query="markdown export", mode="query")
        self.assertTrue(any("markdown" in m.text.lower() for m in msgs))

    def test_pull_code(self):
        msgs = smart_pull(self.tr, mode="code")
        self.assertTrue(any("```" in m.text for m in msgs))


class CliHelpTests(unittest.TestCase):
    def test_parser(self):
        from agent_session_bridge.cli import build_parser

        p = build_parser()
        args = p.parse_args(["list", "-n", "5"])
        self.assertEqual(args.cmd, "list")


class LibraryApiTests(unittest.TestCase):
    def test_package_exports(self):
        import agent_session_bridge as asb

        self.assertTrue(asb.__version__)
        self.assertIn("md", asb.SUPPORTED_FORMATS)
        st = asb.status()
        self.assertEqual(st["role"], "library+cli")
        self.assertIn("providers", st)
        # list should not crash
        sessions = asb.list_sessions(limit=3)
        self.assertIsInstance(sessions, list)

    def test_export_bytes_roundtrip(self):
        import agent_session_bridge as asb

        sessions = asb.list_sessions(limit=1)
        if not sessions:
            self.skipTest("no local sessions")
        data, media, name = asb.export_bytes(sessions[0], fmt="md")
        self.assertTrue(data.startswith(b"#") or b"#" in data[:200] or len(data) > 0)
        self.assertIn("markdown", media)
        self.assertTrue(name.endswith(".md"))


if __name__ == "__main__":
    unittest.main()
