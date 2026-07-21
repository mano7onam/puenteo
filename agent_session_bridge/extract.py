from __future__ import annotations

import re
from typing import List, Optional

from .models import Message, Transcript
from .search import search_transcript
from .util import clip


CODE_FENCE_RE = re.compile(r"```[\w.+-]*\n.*?```", re.S)
ERROR_RE = re.compile(
    r"(Traceback \(most recent call last\):.*?)(?:\n\n|\Z)"
    r"|(Error:.*$)|(FAILED.*$)|(Exception:.*$)",
    re.M | re.S,
)
DECISION_RE = re.compile(
    r"(?i)(^|\n)(.*\b(decided|decision|fix|root cause|instead we|we'll|we will|"
    r"final approach|ship(?:ped)?|done|solution|workaround|"
    r"реш(?:ил|ено|ение)|итог|вместо|готово|фикс|исправ)\b.*)",
)


def last_n(transcript: Transcript, n: int = 12, *, roles: Optional[List[str]] = None) -> List[Message]:
    roles = roles or ["user", "assistant"]
    msgs = [m for m in transcript.messages if m.role in roles and m.text.strip()]
    return msgs[-n:]


def extract_code(transcript: Transcript, *, limit: int = 20) -> List[Message]:
    out: List[Message] = []
    for m in transcript.messages:
        fences = CODE_FENCE_RE.findall(m.text or "")
        if not fences:
            # also bare-looking code dumps
            if m.role == "assistant" and _looks_like_code(m.text):
                out.append(m)
            continue
        joined = "\n\n".join(fences)
        out.append(
            Message(
                role=m.role,
                text=joined,
                timestamp=m.timestamp,
                index=m.index,
                meta={"kind": "code"},
            )
        )
        if len(out) >= limit:
            break
    return out


def extract_errors(transcript: Transcript, *, limit: int = 15) -> List[Message]:
    out: List[Message] = []
    for m in transcript.messages:
        text = m.text or ""
        if not any(
            k in text
            for k in (
                "Traceback",
                "Error:",
                "ERROR",
                "Exception",
                "FAILED",
                "fatal:",
                "TypeError",
                "ValueError",
            )
        ):
            continue
        m2 = Message(
            role=m.role,
            text=clip(text, 2500),
            timestamp=m.timestamp,
            index=m.index,
            meta={"kind": "error"},
        )
        out.append(m2)
        if len(out) >= limit:
            break
    return out


def extract_decisions(transcript: Transcript, *, limit: int = 20) -> List[Message]:
    out: List[Message] = []
    for m in transcript.messages:
        if m.role not in ("assistant", "user"):
            continue
        text = m.text or ""
        if not DECISION_RE.search(text) and not _looks_like_decision(text):
            continue
        # keep shorter decision-ish messages whole; clip long ones around match
        out.append(
            Message(
                role=m.role,
                text=clip(text, 1800),
                timestamp=m.timestamp,
                index=m.index,
                meta={"kind": "decision"},
            )
        )
        if len(out) >= limit:
            break
    return out


def smart_pull(
    transcript: Transcript,
    *,
    query: Optional[str] = None,
    mode: str = "auto",
    last: int = 0,
    max_chars: int = 12000,
    max_messages: int = 30,
) -> List[Message]:
    """
    Build a compact message set for injection into another agent.

    modes:
      auto | query | last | code | errors | decisions | handoff
    """
    mode = (mode or "auto").lower()
    if last and mode == "auto" and not query:
        mode = "last"
    if query and mode == "auto":
        mode = "query"
    if mode == "auto":
        mode = "handoff"

    if mode == "last":
        msgs = last_n(transcript, last or 16)
    elif mode == "code":
        msgs = extract_code(transcript)
    elif mode in ("error", "errors"):
        msgs = extract_errors(transcript)
    elif mode in ("decision", "decisions"):
        msgs = extract_decisions(transcript)
    elif mode == "query":
        hits = search_transcript(transcript, query or "", limit=max_messages)
        # expand neighbors for context
        by_idx = {m.index: m for m in transcript.messages}
        picked = []
        seen = set()
        for h in hits:
            for j in (h.message.index - 1, h.message.index, h.message.index + 1):
                if j in seen or j not in by_idx:
                    continue
                m = by_idx[j]
                if m.role not in ("user", "assistant"):
                    continue
                seen.add(j)
                picked.append(m)
        msgs = sorted(picked, key=lambda m: m.index)
    elif mode == "handoff":
        msgs = _handoff_pack(transcript, query=query)
    else:
        msgs = last_n(transcript, last or 16)

    # budget by chars
    out: List[Message] = []
    total = 0
    for m in msgs:
        t = m.text or ""
        if total + len(t) > max_chars and out:
            # try clipped last piece
            remain = max_chars - total
            if remain > 400:
                out.append(
                    Message(
                        role=m.role,
                        text=clip(t, remain),
                        timestamp=m.timestamp,
                        index=m.index,
                        meta=m.meta,
                    )
                )
            break
        out.append(m)
        total += len(t)
        if len(out) >= max_messages:
            break
    return out


def _handoff_pack(transcript: Transcript, *, query: Optional[str] = None) -> List[Message]:
    """First real user goals + last exchange + decisions + optional query hits."""
    msgs = [m for m in transcript.messages if m.role in ("user", "assistant") and m.text.strip()]
    if not msgs:
        return []
    picked: List[Message] = []
    seen = set()

    def add(m: Message):
        if m.index in seen:
            return
        seen.add(m.index)
        picked.append(m)

    # opening goals (first 2 user + following assistant)
    users = [m for m in msgs if m.role == "user"]
    for u in users[:2]:
        add(u)
        # next assistant after u
        for m in msgs:
            if m.index > u.index and m.role == "assistant":
                add(m)
                break

    # decisions
    for m in extract_decisions(transcript, limit=6):
        # map back to full message if possible
        for full in msgs:
            if full.index == m.index:
                add(full)
                break
        else:
            add(m)

    # query hits
    if query:
        for h in search_transcript(transcript, query, limit=6):
            add(h.message)

    # closing context
    for m in msgs[-8:]:
        add(m)

    return sorted(picked, key=lambda m: m.index)


def _looks_like_code(text: str) -> bool:
    if not text or len(text) < 40:
        return False
    lines = text.splitlines()
    if len(lines) < 4:
        return False
    codey = sum(
        1
        for ln in lines
        if ln.strip().startswith(
            ("def ", "class ", "import ", "from ", "function ", "const ", "let ", "var ", "#include")
        )
        or ln.rstrip().endswith("{")
        or re.match(r"^\s{2,}\S", ln)
    )
    return codey >= 3


def _looks_like_decision(text: str) -> bool:
    t = text.strip()
    if len(t) < 20 or len(t) > 1200:
        return False
    # short conclusive assistant turns
    return bool(
        re.search(
            r"(?i)^(done|fixed|shipped|here's what|итог|готово|сделал|исправил)\b",
            t,
        )
    )
