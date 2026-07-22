from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .models import Message, Transcript
from .search import search_transcript
from .util import clip, first_line, format_mtime


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
MILESTONE_RE = re.compile(
    r"(?i)\b(decided|decision|fix|root cause|shipped|done|fixed|workaround|"
    r"failed|error|traceback|instead|"
    r"реш(?:ил|ено|ение)|итог|готово|фикс|ошибк)\b"
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


def around_messages(
    transcript: Transcript,
    center: int,
    *,
    radius: int = 5,
    roles: Optional[List[str]] = None,
) -> List[Message]:
    """Messages with index in [center-radius, center+radius]."""
    roles = roles or ["user", "assistant"]
    lo = center - max(0, radius)
    hi = center + max(0, radius)
    out: List[Message] = []
    for m in transcript.messages:
        if m.index < lo or m.index > hi:
            continue
        if m.role not in roles:
            continue
        if not (m.text or "").strip():
            continue
        out.append(m)
    return out


def range_messages(
    transcript: Transcript,
    start: int,
    end: int,
    *,
    roles: Optional[List[str]] = None,
) -> List[Message]:
    """Messages with index in [start, end] inclusive."""
    roles = roles or ["user", "assistant", "system", "tool", "reasoning"]
    lo, hi = (start, end) if start <= end else (end, start)
    return [
        m
        for m in transcript.messages
        if lo <= m.index <= hi and m.role in roles and (m.text or "").strip()
    ]


def build_outline(
    transcript: Transcript,
    *,
    max_milestones: int = 24,
) -> Dict[str, Any]:
    """
    Compact session map: counts, time span, cwd changes, milestone messages.
    """
    sess = transcript.session
    msgs = [m for m in transcript.messages if (m.text or "").strip()]
    text_msgs = [m for m in msgs if m.role in ("user", "assistant")]
    users = [m for m in text_msgs if m.role == "user"]
    assistants = [m for m in text_msgs if m.role == "assistant"]

    timestamps = [m.timestamp for m in msgs if m.timestamp]
    cwd_path = []
    last_cwd = None
    # session-level cwd always first
    if sess.cwd:
        cwd_path.append({"index": 0, "cwd": sess.cwd})
        last_cwd = sess.cwd
    for m in msgs:
        c = (m.meta or {}).get("cwd")
        if c and c != last_cwd:
            cwd_path.append({"index": m.index, "cwd": c})
            last_cwd = c

    milestones: List[Dict[str, Any]] = []
    # opening user goals
    for m in users[:3]:
        milestones.append(
            {
                "index": m.index,
                "role": m.role,
                "kind": "goal",
                "preview": first_line(m.text, 140),
                "timestamp": m.timestamp,
            }
        )
    # heuristic decision / error / done turns
    for m in text_msgs:
        if len(milestones) >= max_milestones:
            break
        kind = None
        if m.role == "assistant" and _looks_like_decision(m.text):
            kind = "decision"
        elif DECISION_RE.search(m.text or ""):
            kind = "decision"
        elif any(
            k in (m.text or "")
            for k in ("Traceback", "Error:", "Exception", "FAILED")
        ):
            kind = "error"
        elif MILESTONE_RE.search(m.text or "") and m.role == "assistant" and len(m.text or "") < 900:
            kind = "milestone"
        if not kind:
            continue
        if any(x["index"] == m.index for x in milestones):
            continue
        milestones.append(
            {
                "index": m.index,
                "role": m.role,
                "kind": kind,
                "preview": first_line(m.text, 140),
                "timestamp": m.timestamp,
            }
        )
    # closing turn
    if text_msgs:
        last = text_msgs[-1]
        if not any(x["index"] == last.index for x in milestones):
            milestones.append(
                {
                    "index": last.index,
                    "role": last.role,
                    "kind": "latest",
                    "preview": first_line(last.text, 140),
                    "timestamp": last.timestamp,
                }
            )
    milestones.sort(key=lambda x: x["index"])

    return {
        "session_id": sess.session_id,
        "provider": sess.provider,
        "title": sess.title,
        "cwd": sess.cwd,
        "path": sess.path,
        "mtime": sess.mtime,
        "mtime_human": format_mtime(sess.mtime),
        "message_count": len(msgs),
        "user_count": len(users),
        "assistant_count": len(assistants),
        "first_index": text_msgs[0].index if text_msgs else None,
        "last_index": text_msgs[-1].index if text_msgs else None,
        "first_timestamp": timestamps[0] if timestamps else "",
        "last_timestamp": timestamps[-1] if timestamps else "",
        "cwd_path": cwd_path,
        "milestones": milestones,
    }


def smart_pull(
    transcript: Transcript,
    *,
    query: Optional[str] = None,
    mode: str = "auto",
    last: int = 0,
    max_chars: int = 12000,
    max_messages: int = 30,
    top_k: int = 0,
    around: Optional[int] = None,
    radius: int = 5,
) -> List[Message]:
    """
    Build a compact message set for injection into another agent.

    modes:
      auto | query | last | code | errors | decisions | handoff | around
    """
    mode = (mode or "auto").lower()
    k = top_k if top_k and top_k > 0 else max_messages

    if around is not None and mode == "auto":
        mode = "around"
    if last and mode == "auto" and not query and around is None:
        mode = "last"
    if query and mode == "auto":
        mode = "query"
    if mode == "auto":
        mode = "handoff"

    if mode == "around":
        center = around if around is not None else last
        msgs = around_messages(transcript, int(center or 0), radius=radius)
    elif mode == "last":
        msgs = last_n(transcript, last or 16)
    elif mode == "code":
        msgs = extract_code(transcript, limit=k)
    elif mode in ("error", "errors"):
        msgs = extract_errors(transcript, limit=k)
    elif mode in ("decision", "decisions"):
        msgs = extract_decisions(transcript, limit=k)
        if query:
            # re-rank decisions by query relevance when provided
            hits = search_transcript(
                Transcript(session=transcript.session, messages=msgs),
                query,
                limit=k,
            )
            if hits:
                msgs = [h.message for h in hits]
    elif mode == "query":
        msgs = _query_pack(transcript, query or "", top_k=k, max_chars=max_chars)
        # already budgeted by relevance; still cap message count
        return msgs[:k] if k else msgs
    elif mode == "handoff":
        msgs = _handoff_pack(transcript, query=query)
    else:
        msgs = last_n(transcript, last or 16)

    return _apply_budget(msgs, max_chars=max_chars, max_messages=k)


def _query_pack(
    transcript: Transcript,
    query: str,
    *,
    top_k: int = 30,
    max_chars: int = 12000,
) -> List[Message]:
    """
    Rank by relevance (not chronology), expand ±1 neighbors, then fill budget
    by score. Return in chronological order for readability.
    """
    hits = search_transcript(transcript, query, limit=max(top_k, 1))
    by_idx = {m.index: m for m in transcript.messages}
    scored: Dict[int, float] = {}
    for rank, h in enumerate(hits):
        base = float(h.score) + (len(hits) - rank) * 0.001
        for offset, weight in ((-1, 0.35), (0, 1.0), (1, 0.35)):
            j = h.message.index + offset
            if j not in by_idx:
                continue
            m = by_idx[j]
            if m.role not in ("user", "assistant") or not (m.text or "").strip():
                continue
            scored[j] = max(scored.get(j, 0.0), base * weight)

    if not scored:
        return []

    # pick highest score first until budget
    ordered = sorted(scored.items(), key=lambda x: x[1], reverse=True)
    picked: List[Message] = []
    total = 0
    for idx, _sc in ordered:
        if len(picked) >= top_k:
            break
        m = by_idx[idx]
        t = m.text or ""
        if total + len(t) > max_chars and picked:
            remain = max_chars - total
            if remain > 400:
                picked.append(
                    Message(
                        role=m.role,
                        text=clip(t, remain),
                        timestamp=m.timestamp,
                        index=m.index,
                        meta={**(m.meta or {}), "score": round(_sc, 3)},
                    )
                )
            break
        picked.append(
            Message(
                role=m.role,
                text=t,
                timestamp=m.timestamp,
                index=m.index,
                meta={**(m.meta or {}), "score": round(_sc, 3)},
            )
        )
        total += len(t)

    return sorted(picked, key=lambda m: m.index)


def _apply_budget(
    msgs: List[Message],
    *,
    max_chars: int,
    max_messages: int,
) -> List[Message]:
    out: List[Message] = []
    total = 0
    for m in msgs:
        t = m.text or ""
        if total + len(t) > max_chars and out:
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
