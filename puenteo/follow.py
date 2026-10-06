"""``puenteo follow <ref>`` — live tail of another session's transcript (any provider)."""

from __future__ import annotations

import os
import sys
import time
from typing import Optional

from .models import Session


def follow(
    session: Session,
    *,
    interval: float = 1.0,
    last: int = 3,
    tools: bool = False,
    timeout: Optional[float] = None,
    width: int = 400,
    out=None,
) -> int:
    """Print the last ``last`` messages, then every new one as the file grows."""
    from .providers import load_transcript

    out = out or sys.stdout
    deadline = time.time() + timeout if timeout else None
    seen = 0
    sig = None
    printed = 0
    first = True
    while True:
        try:
            st = os.stat(session.path)
            cur = (st.st_size, st.st_mtime)
        except OSError:
            cur = None
        if cur != sig:
            sig = cur
            try:
                tr = load_transcript(session, include_tools=tools)
            except Exception as e:
                print(f"[puenteo follow] read error: {e}", file=sys.stderr)
                tr = None
            if tr is not None:
                msgs = tr.messages
                start = max(0, len(msgs) - last) if first else seen
                for m in msgs[start:]:
                    text = " ".join((m.text or "").split())
                    if len(text) > width:
                        text = text[: width - 1] + "…"
                    ts = (m.timestamp or "")[11:19]
                    out.write(f"[{session.provider}:{session.session_id[:8]} #{m.index} {m.role}{' ' + ts if ts else ''}] {text}\n")
                    printed += 1
                out.flush()
                seen = len(msgs)
                first = False
        if deadline and time.time() >= deadline:
            return printed
        time.sleep(interval)
