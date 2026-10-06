"""Mask secrets before session text leaves puenteo (packs, MCP results, messages).

Transcripts routinely contain tool output with API keys, tokens and private
keys. When that text is handed to *another* agent it ends up in another
provider's context and logs — so ``pull``, ``show``, ``search`` snippets and
MCP tools redact by default (``--no-redact`` / ``PUENTEO_NO_REDACT=1`` to opt out).
Full exports to a local file are left verbatim unless ``--redact`` is given.
"""

from __future__ import annotations

import os
import re
from typing import List, Tuple

_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("private-key", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----[\s\S]+?-----END (?:[A-Z ]+ )?PRIVATE KEY-----")),
    ("anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}")),
    ("github", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("gitlab", re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}")),
    ("slack", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("aws-key-id", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("google-api", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("stripe", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}")),
    ("huggingface", re.compile(r"\bhf_[A-Za-z0-9]{30,}")),
    ("npm", re.compile(r"\bnpm_[A-Za-z0-9]{30,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("bearer", re.compile(r"(?i)\b(authorization\s*[:=]\s*(?:bearer|basic|token)\s+)([A-Za-z0-9._~+/=\-]{16,})")),
    ("url-cred", re.compile(r"\b([a-z][a-z0-9+.\-]*://[^\s:/@]+:)([^\s@/]{4,})(@)")),
    (
        "assignment",
        re.compile(
            r"(?im)\b([A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|CLIENT_?SECRET)[A-Z0-9_]*"
            r"\s*[=:]\s*[\"']?)([^\s\"']{8,})"
        ),
    ),
]


def enabled_by_default() -> bool:
    return os.environ.get("PUENTEO_NO_REDACT", "").strip() in ("", "0", "false")


def redact(text: str) -> str:
    """Return ``text`` with secrets replaced by ``[REDACTED:<kind>]``."""
    if not text:
        return text
    out = text
    for kind, rx in _PATTERNS:
        if rx.groups >= 2:
            # keep the label/prefix, mask the value
            def _sub(m, kind=kind):
                g = m.groups()
                if len(g) == 3:  # url-cred: scheme://user: SECRET @
                    return f"{g[0]}[REDACTED:{kind}]{g[2]}"
                return f"{g[0]}[REDACTED:{kind}]"

            out = rx.sub(_sub, out)
        else:
            out = rx.sub(f"[REDACTED:{kind}]", out)
    return out


def count(text: str) -> int:
    return (text or "").count("[REDACTED:")
