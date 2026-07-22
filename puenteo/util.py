from __future__ import annotations

import os
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Iterable, List, Optional

# CSI / OSC-ish ANSI sequences (colors, bold, etc.)
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
# Also catch leftover SGR fragments like "[1m" that sometimes remain after partial strip
_ANSI_FRAG_RE = re.compile(r"\x1b\[[0-9;]*m|\[\d{1,3}m")


def expand(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def normalize_path(path: Optional[str]) -> str:
    if not path:
        return ""
    try:
        return os.path.realpath(expand(path))
    except Exception:
        return expand(path)


def paths_related(a: str, b: str) -> bool:
    """True if either path is equal or a parent/child of the other."""
    a = normalize_path(a).rstrip("/")
    b = normalize_path(b).rstrip("/")
    if not a or not b:
        return False
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def cwd_matches(filter_cwd: Optional[str], session_cwd: Optional[str]) -> bool:
    """
    Whether a session cwd matches a list/search --cwd filter.

    Semantics (project-centric, not parent-broad):
    - empty filter → match all
    - absolute/~ path → session cwd equals filter or is under it
    - bare fragment (e.g. ``harbor-datasets``) → substring of session cwd
    """
    if not filter_cwd or not str(filter_cwd).strip():
        return True
    if not session_cwd or not str(session_cwd).strip():
        return False

    raw = str(filter_cwd).strip()
    sess = normalize_path(session_cwd).rstrip("/")
    if not sess:
        return False

    looks_abs = (
        raw.startswith("~")
        or raw.startswith("/")
        or (len(raw) > 1 and raw[1] == ":")  # Windows drive
    )
    if looks_abs:
        filt = normalize_path(raw).rstrip("/")
        if not filt:
            return False
        # session is this project or a subdirectory — not a parent project session
        return sess == filt or sess.startswith(filt + "/")

    # fragment / basename style
    frag = raw.lower().rstrip("/")
    return frag in sess.lower()


def strip_ansi(text: Optional[str]) -> str:
    """Remove ANSI escape sequences and common leftover SGR fragments."""
    if not text:
        return ""
    t = _ANSI_RE.sub("", text)
    t = _ANSI_FRAG_RE.sub("", t)
    return t


def format_mtime(ts: float) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(int(ts))


def format_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}K"
    return f"{n / (1024 * 1024):.1f}M"


def short_id(sid: str, n: int = 8) -> str:
    sid = sid or ""
    return sid if len(sid) <= n else sid[:n]


def decode_url_path(name: str) -> str:
    """Decode %2FUsers%2F... style directory names used by Grok."""
    try:
        return urllib.parse.unquote(name)
    except Exception:
        return name


def stringify_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                bt = block.get("type")
                if bt in (None, "text", "input_text", "output_text"):
                    if block.get("text"):
                        parts.append(str(block.get("text")))
                    elif block.get("content"):
                        parts.append(stringify_content(block.get("content")))
                elif bt in ("thinking", "reasoning"):
                    # skip in default stringify; callers can request
                    continue
                elif bt == "tool_use":
                    name = block.get("name") or "tool"
                    parts.append(f"[tool_use:{name}]")
                elif bt == "tool_result":
                    parts.append("[tool_result]")
                else:
                    if block.get("text"):
                        parts.append(str(block["text"]))
            else:
                parts.append(str(block))
        return "\n".join(p for p in parts if p)
    if isinstance(content, dict):
        if "text" in content:
            return str(content.get("text") or "")
        try:
            import json

            return json.dumps(content, ensure_ascii=False)[:4000]
        except Exception:
            return str(content)
    return str(content)


_WORD_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9_./-]{2,}")


def tokenize(text: str) -> List[str]:
    return [w.lower() for w in _WORD_RE.findall(text or "")]


def clip(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def first_line(text: str, n: int = 120) -> str:
    cleaned = strip_ansi(text or "")
    line = cleaned.strip().splitlines()[0] if cleaned.strip() else ""
    # strip common wrappers
    for tag in (
        "<user_query>",
        "</user_query>",
        "<user_info>",
        "</user_info>",
        "<local-command-stdout>",
        "</local-command-stdout>",
        "<local-command-stderr>",
        "</local-command-stderr>",
        "<command-message>",
        "</command-message>",
        "<command-name>",
        "</command-name>",
    ):
        line = line.replace(tag, "")
    line = " ".join(line.split())
    return line[:n]


def is_noise_user_text(text: str) -> bool:
    t = strip_ansi(text or "").strip()
    if not t:
        return True
    if t.startswith("<user_info>") or t.startswith("<system-reminder>"):
        return True
    if t.startswith("<action_safety>") or "You are Grok" in t[:200]:
        return True
    if t.startswith("<environment_context>") or t.startswith("<local-command-caveat>"):
        return True
    # Claude slash-command / local-command wrappers (not real user intent)
    if t.startswith("<local-command-") or "<local-command-" in t[:80]:
        return True
    if t.startswith("<command-message>") or t.startswith("<command-name>"):
        return True
    if t.startswith("Set model to ") and len(t) < 200:
        return True
    if "synthetic" in t[:40].lower() and len(t) < 80:
        return True
    return False


def clean_title(text: Optional[str], n: int = 120) -> str:
    """Title-safe first line: no ANSI, no command noise wrappers."""
    if not text:
        return ""
    t = strip_ansi(text)
    if is_noise_user_text(t):
        return ""
    return first_line(t, n)


def extract_user_query(text: str) -> str:
    """Prefer inner <user_query> content when present."""
    m = re.search(r"<user_query>\s*(.*?)\s*</user_query>", text or "", re.S | re.I)
    if m:
        return m.group(1).strip()
    return text or ""


def iter_chunks(items: Iterable[Any], size: int):
    buf = []
    for it in items:
        buf.append(it)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf
