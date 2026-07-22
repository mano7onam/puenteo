#!/usr/bin/env python3
"""Puenteo CLI — discover / search / pull other agent sessions."""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from . import extract, format as fmt
from .providers import list_sessions, load_transcript, resolve_session
from .search import search_all, search_transcript
from .version import APP_NAME, __version__


def _parse_providers(raw: Optional[str]) -> Optional[List[str]]:
    if not raw or raw in ("all", "*"):
        return None
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    mapped = []
    for p in parts:
        if p in ("claude", "claude_code", "claude-code"):
            mapped.append("claude_code")
        elif p in ("codex", "openai"):
            mapped.append("codex")
        elif p in ("grok", "xai"):
            mapped.append("grok")
        elif p in ("pi", "pi-agent"):
            mapped.append("pi")
        else:
            mapped.append(p)
    return mapped or None


def _common_flags(target: argparse.ArgumentParser) -> None:
    """Flags usable before or after the subcommand (agents pass both styles)."""
    target.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="JSON output (agent-friendly)",
    )
    target.add_argument(
        "--provider",
        "-p",
        default=argparse.SUPPRESS,
        help="claude|codex|grok|pi|all or comma list (default: all)",
    )
    target.add_argument(
        "--cwd",
        default=argparse.SUPPRESS,
        help="Filter sessions related to this project path",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="puenteo",
        description=(
            f"{APP_NAME} — the bridge between coding agents. "
            "List, search, and export Claude Code / Codex / Grok / Pi sessions "
            "to md/html/pdf/json/zip/csv/xml/yaml."
        ),
    )
    p.add_argument("--version", "-V", action="version", version=f"{APP_NAME} {__version__}")
    _common_flags(p)
    # defaults when flags omitted on both parent and subparser
    p.set_defaults(json=False, provider="all", cwd=None)

    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("list", help="List known agent sessions (newest first)")
    _common_flags(sp)
    sp.add_argument("--limit", "-n", type=int, default=30)

    sp = sub.add_parser("show", help="Show a session transcript (or tail)")
    _common_flags(sp)
    sp.add_argument("session", help="Session id (prefix ok), path, or title substring")
    sp.add_argument("--last", "-n", type=int, default=20, help="Last N messages (0=all)")
    sp.add_argument("--tools", action="store_true", help="Include tool calls/results")

    sp = sub.add_parser("search", help="Search across sessions (or one session)")
    _common_flags(sp)
    sp.add_argument("query", help="Search query")
    sp.add_argument("--session", "-s", default=None, help="Limit to one session id")
    sp.add_argument("--limit", "-n", type=int, default=15)
    sp.add_argument("--session-limit", type=int, default=40, help="How many sessions to scan")

    sp = sub.add_parser(
        "pull",
        help="Extract a compact context pack for another agent (default handoff)",
    )
    _common_flags(sp)
    sp.add_argument("session", help="Session id / path / title")
    sp.add_argument("--query", "-q", default=None, help="Focus query (BM25 + neighbors)")
    sp.add_argument(
        "--mode",
        choices=["auto", "handoff", "query", "last", "code", "errors", "decisions"],
        default="auto",
        help="Extraction strategy (default: auto)",
    )
    sp.add_argument("--last", type=int, default=0, help="With mode=last, how many messages")
    sp.add_argument("--max-chars", type=int, default=12000)
    sp.add_argument("--max-messages", type=int, default=30)
    sp.add_argument("--tools", action="store_true")
    sp.add_argument(
        "-o",
        "--output",
        default=None,
        help="Write pack to file instead of stdout",
    )

    sp = sub.add_parser("pack", help="Alias for pull (agent-oriented name)")
    _common_flags(sp)
    sp.add_argument("session")
    sp.add_argument("--query", "-q", default=None)
    sp.add_argument(
        "--mode",
        choices=["auto", "handoff", "query", "last", "code", "errors", "decisions"],
        default="auto",
    )
    sp.add_argument("--last", type=int, default=0)
    sp.add_argument("--max-chars", type=int, default=12000)
    sp.add_argument("--max-messages", type=int, default=30)
    sp.add_argument("--tools", action="store_true")
    sp.add_argument("-o", "--output", default=None)

    sp = sub.add_parser(
        "export",
        help="Export full session transcript (md/html/pdf/json/zip/csv/xml/yaml)",
    )
    _common_flags(sp)
    sp.add_argument("session", help="Session id / path / title")
    sp.add_argument(
        "-f",
        "--format",
        default="md",
        help="md|txt|html|pdf|json|zip|csv|xml|yaml|all (default: md)",
    )
    sp.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output file or directory (for format=all). Default: stdout for text, else ./export-…",
    )
    sp.add_argument("--tools", action="store_true", help="Include tool calls/results")
    sp.add_argument("--thinking", action="store_true", help="Include thinking blocks")

    sp = sub.add_parser("status", help="Show what providers/session stores were found")
    _common_flags(sp)
    sp = sub.add_parser("doctor", help="Alias for status")
    _common_flags(sp)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    providers = _parse_providers(getattr(args, "provider", "all"))
    cwd = getattr(args, "cwd", None)
    if cwd:
        cwd = os.path.abspath(os.path.expanduser(cwd))
    json_mode = bool(getattr(args, "json", False))

    try:
        if args.cmd == "list":
            sessions = list_sessions(providers=providers, cwd=cwd, limit=args.limit)
            print(fmt.format_session_list(sessions, json_mode=json_mode))
            return 0

        if args.cmd in ("status", "doctor"):
            return cmd_status(json_mode=json_mode)

        if args.cmd == "show":
            sess = resolve_session(args.session, providers=providers, cwd=cwd)
            if not sess:
                print(f"Session not found: {args.session}", file=sys.stderr)
                return 1
            tr = load_transcript(sess, include_tools=bool(args.tools))
            print(fmt.format_transcript(tr, last=args.last, json_mode=json_mode))
            return 0

        if args.cmd == "search":
            if args.session:
                sess = resolve_session(args.session, providers=providers, cwd=cwd)
                if not sess:
                    print(f"Session not found: {args.session}", file=sys.stderr)
                    return 1
                tr = load_transcript(sess)
                hits = search_transcript(tr, args.query, limit=args.limit)
            else:
                hits = search_all(
                    args.query,
                    providers=providers,
                    cwd=cwd,
                    session_limit=args.session_limit,
                    hit_limit=args.limit,
                )
            print(fmt.format_hits(hits, json_mode=json_mode))
            return 0

        if args.cmd in ("pull", "pack"):
            sess = resolve_session(args.session, providers=providers, cwd=cwd)
            if not sess:
                print(f"Session not found: {args.session}", file=sys.stderr)
                return 1
            tr = load_transcript(sess, include_tools=bool(args.tools))
            mode = args.mode
            if args.query and mode == "auto":
                mode = "query"
            msgs = extract.smart_pull(
                tr,
                query=args.query,
                mode=mode,
                last=args.last,
                max_chars=args.max_chars,
                max_messages=args.max_messages,
            )
            text = fmt.format_pack(
                sess,
                msgs,
                purpose=mode if mode != "auto" else "handoff",
                json_mode=json_mode,
                max_chars=0 if json_mode else args.max_chars + 2000,
            )
            if args.output:
                path = os.path.abspath(os.path.expanduser(args.output))
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)
                print(f"Wrote {path} ({len(text)} chars, {len(msgs)} messages)", file=sys.stderr)
            else:
                sys.stdout.write(text)
            return 0

        if args.cmd == "export":
            return cmd_export(args, providers=providers, cwd=cwd)

        parser.error(f"Unknown command: {args.cmd}")
        return 2
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def cmd_export(args, *, providers, cwd) -> int:
    from .exporters import SUPPORTED_FORMATS, render
    from .rich import load_transcript as load_rich

    sess = resolve_session(args.session, providers=providers, cwd=cwd)
    if not sess:
        print(f"Session not found: {args.session}", file=sys.stderr)
        return 1

    rich_tr = load_rich(
        sess.provider,
        sess.path,
        include_tools=bool(args.tools),
        include_thinking=bool(args.thinking),
    )
    fmt_arg = (args.format or "md").lower().strip()
    formats = list(SUPPORTED_FORMATS) if fmt_arg == "all" else [fmt_arg]

    out = args.output
    if out:
        out = os.path.abspath(os.path.expanduser(out))

    written = []
    for fmt in formats:
        data, media_type, filename = render(
            rich_tr,
            fmt,
            include_tools=bool(args.tools),
            include_thinking=bool(args.thinking),
        )
        if len(formats) == 1 and not out and fmt in ("md", "txt", "html", "json", "csv", "xml", "yaml"):
            # text-ish → stdout
            sys.stdout.buffer.write(data)
            if not data.endswith(b"\n"):
                sys.stdout.buffer.write(b"\n")
            return 0

        if out and os.path.isdir(out):
            path = os.path.join(out, filename)
        elif out and len(formats) == 1:
            path = out
        elif out:
            # treat as directory prefix
            os.makedirs(out, exist_ok=True)
            path = os.path.join(out, filename)
        else:
            path = os.path.abspath(filename)

        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        written.append((path, len(data), media_type))
        print(f"Wrote {path} ({len(data)} bytes, {media_type})", file=sys.stderr)

    if not written:
        print("Nothing written", file=sys.stderr)
        return 1
    return 0


def cmd_status(*, json_mode: bool = False) -> int:
    import json
    from pathlib import Path

    from .exporters import SUPPORTED_FORMATS

    homes = {
        "claude_code": Path.home() / ".claude" / "projects",
        "codex": Path.home() / ".codex" / "sessions",
        "grok": Path.home() / ".grok" / "sessions",
        "pi": Path.home() / ".pi" / "agent" / "sessions",
    }
    info = {}
    for name, path in homes.items():
        exists = path.is_dir()
        count = 0
        if exists:
            count = len(list_sessions(providers=[name], limit=500))
        info[name] = {"path": str(path), "exists": exists, "sessions": count}

    if json_mode:
        print(
            json.dumps(
                {
                    "version": __version__,
                    "providers": info,
                    "export_formats": list(SUPPORTED_FORMATS),
                    "role": "library+cli",
                },
                indent=2,
            )
        )
    else:
        print(f"{APP_NAME} v{__version__}  (library + CLI)")
        print("Providers:")
        for name, d in info.items():
            mark = "OK" if d["exists"] else "—"
            print(f"  [{mark}] {name:12}  sessions≈{d['sessions']:<4}  {d['path']}")
        print()
        print("Export formats:", ", ".join(SUPPORTED_FORMATS))
        print()
        print("Commands:")
        print("  puenteo list --json     # same as: asb list --json")
        print("  asb search 'topic' --json")
        print("  asb pull <id> --query 'topic' --mode query")
        print("  asb export <id> -f md|html|pdf|json|zip|csv|xml|yaml|all -o out")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
