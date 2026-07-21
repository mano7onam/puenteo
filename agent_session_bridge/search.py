from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .models import Message, Session, Transcript
from .providers import list_sessions, load_transcript
from .util import tokenize


@dataclass
class Hit:
    session: Session
    message: Message
    score: float
    snippet: str


def _bm25_scores(
    query_tokens: Sequence[str],
    docs: Sequence[Sequence[str]],
    *,
    k1: float = 1.4,
    b: float = 0.75,
) -> List[float]:
    """Lightweight BM25 over in-memory token lists (no deps)."""
    N = len(docs)
    if N == 0:
        return []
    df: Dict[str, int] = {}
    for doc in docs:
        for t in set(doc):
            df[t] = df.get(t, 0) + 1
    avgdl = sum(len(d) for d in docs) / max(N, 1)
    idf: Dict[str, float] = {}
    for t, n in df.items():
        # Robertson–Sparck Jones idf
        idf[t] = math.log(1 + (N - n + 0.5) / (n + 0.5))

    scores: List[float] = []
    qset = [t for t in query_tokens if t]
    for doc in docs:
        tf: Dict[str, int] = {}
        for t in doc:
            tf[t] = tf.get(t, 0) + 1
        dl = len(doc) or 1
        s = 0.0
        for t in qset:
            if t not in tf:
                continue
            f = tf[t]
            denom = f + k1 * (1 - b + b * dl / avgdl)
            s += idf.get(t, 0.0) * (f * (k1 + 1)) / denom
        scores.append(s)
    return scores


def _boost(text: str, query: str) -> float:
    """Extra boosts for phrase match, code fences, decision language."""
    tl = (text or "").lower()
    ql = (query or "").lower().strip()
    boost = 0.0
    if ql and ql in tl:
        boost += 2.5
    # multi-word near match
    words = [w for w in ql.split() if len(w) > 2]
    if words:
        hit = sum(1 for w in words if w in tl)
        boost += 0.4 * hit
    if "```" in text:
        boost += 0.3
    decision_re = re.compile(
        r"\b(decided|decision|fix|root cause|we'll|we will|done|shipped|instead)\b"
        r"|реш(ил|ено|ение)|итог|вместо|готово",
        re.I,
    )
    if decision_re.search(text or ""):
        boost += 0.5
    # prefer user questions slightly for discovery
    return boost


def search_transcript(
    transcript: Transcript,
    query: str,
    *,
    limit: int = 12,
    roles: Optional[List[str]] = None,
) -> List[Hit]:
    roles = roles or ["user", "assistant"]
    msgs = [m for m in transcript.messages if m.role in roles and (m.text or "").strip()]
    if not msgs:
        return []
    q_tokens = tokenize(query)
    if not q_tokens:
        # empty query → last messages
        out = []
        for m in msgs[-limit:]:
            out.append(
                Hit(
                    session=transcript.session,
                    message=m,
                    score=0.0,
                    snippet=_snippet(m.text, query),
                )
            )
        return list(reversed(out))

    docs = [tokenize(m.text) for m in msgs]
    scores = _bm25_scores(q_tokens, docs)
    ranked: List[Tuple[float, Message]] = []
    for m, s in zip(msgs, scores):
        s2 = s + _boost(m.text, query)
        if s2 > 0:
            ranked.append((s2, m))
    ranked.sort(key=lambda x: x[0], reverse=True)
    hits: List[Hit] = []
    for s, m in ranked[:limit]:
        hits.append(
            Hit(
                session=transcript.session,
                message=m,
                score=round(s, 3),
                snippet=_snippet(m.text, query),
            )
        )
    return hits


def search_all(
    query: str,
    *,
    providers: Optional[List[str]] = None,
    cwd: Optional[str] = None,
    session_limit: int = 40,
    hit_limit: int = 20,
    per_session: int = 4,
) -> List[Hit]:
    sessions = list_sessions(providers=providers, cwd=cwd, limit=session_limit)
    all_hits: List[Hit] = []
    for sess in sessions:
        try:
            tr = load_transcript(sess, include_tools=False)
        except Exception:
            continue
        hits = search_transcript(tr, query, limit=per_session)
        # slight recency bias
        recency = min(1.5, max(0.0, (sess.mtime or 0) / 1e12))  # tiny
        for h in hits:
            h.score = round(h.score + 0.01 * (sess.mtime % 1000) / 1000 + recency * 0, 3)
            all_hits.append(h)
    all_hits.sort(key=lambda h: (h.score, h.session.mtime), reverse=True)
    return all_hits[:hit_limit]


def _snippet(text: str, query: str, width: int = 220) -> str:
    text = " ".join((text or "").split())
    if not text:
        return ""
    ql = (query or "").strip().lower()
    if ql:
        idx = text.lower().find(ql)
        if idx >= 0:
            start = max(0, idx - 60)
            end = min(len(text), idx + len(ql) + 160)
            snip = text[start:end]
            if start > 0:
                snip = "…" + snip
            if end < len(text):
                snip = snip + "…"
            return snip[:width]
    return text if len(text) <= width else text[: width - 1] + "…"
