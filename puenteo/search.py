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


_DECISION_RE = re.compile(
    r"\b(decided|decision|fix|root cause|we'll|we will|done|shipped|instead)\b"
    r"|реш(ил|ено|ение)|итог|вместо|готово",
    re.I,
)


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
    if _DECISION_RE.search(text or ""):
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
    session_limit: int = 0,
    hit_limit: int = 20,
    per_session: int = 4,
    exclude_session: Optional[str] = None,
    exclude_sessions: Optional[Sequence[str]] = None,
    exclude_self: bool = False,
    since: Optional[str] = None,
    use_index: Optional[bool] = None,
    index_budget_s: Optional[float] = None,
) -> List[Hit]:
    """
    Search every matching session (``session_limit=0`` = all of them).

    Uses the SQLite FTS5 index (global BM25, incremental) when available;
    otherwise scans transcripts in memory.
    """
    from . import index as fts

    sessions = list_sessions(providers=providers, cwd=cwd, limit=session_limit, since=since)
    exclude: List[str] = []
    if exclude_session:
        exclude.append(exclude_session.strip())
    if exclude_sessions:
        exclude.extend(s.strip() for s in exclude_sessions if s and str(s).strip())
    if exclude_self:
        try:
            from .live import whoami

            me = whoami()
            if me and me.session_id:
                exclude.append(me.session_id)
        except Exception:
            pass
    exclude = [e for e in exclude if e]

    def _excluded(sess: Session) -> bool:
        sid = sess.session_id or ""
        for ex in exclude:
            if sid == ex or (len(ex) >= 4 and sid.startswith(ex)):
                return True
        return False

    sessions = [s for s in sessions if not _excluded(s)]

    if use_index is None:
        use_index = fts.available()
    if use_index:
        try:
            fts.refresh(sessions, progress=True, budget_s=index_budget_s)
            rows = fts.search(query, sessions, limit=hit_limit, per_session=per_session)
            return [
                Hit(session=s, message=m, score=sc, snippet=_snippet(m.text, query))
                for s, m, sc in rows
            ]
        except Exception as e:  # corrupt index, locked db, … → degrade, don't fail
            import sys

            print(f"puenteo: index unavailable ({e}); scanning files", file=sys.stderr)

    return _scan_search(query, sessions, hit_limit=hit_limit, per_session=per_session)


def _scan_search(query: str, sessions: Sequence[Session], *, hit_limit: int, per_session: int) -> List[Hit]:
    """Fallback without FTS5: BM25 with corpus-wide IDF over all loaded messages."""
    import time

    q_tokens = tokenize(query)
    if not q_tokens:
        return []
    docs: List[Tuple[Session, Message, List[str]]] = []
    for sess in sessions:
        try:
            tr = load_transcript(sess, include_tools=False)
        except Exception:
            continue
        for m in tr.messages:
            if m.role in ("user", "assistant") and (m.text or "").strip():
                docs.append((sess, m, tokenize(m.text)))
    scores = _bm25_scores(q_tokens, [d[2] for d in docs])
    now = time.time()
    ranked = []
    for (sess, m, _toks), sc in zip(docs, scores):
        if sc <= 0:
            continue
        age_days = max(0.0, (now - (sess.mtime or now)) / 86400)
        total = sc + _boost(m.text, query) + math.exp(-age_days / 30)
        ranked.append((total, sess, m))
    ranked.sort(key=lambda x: x[0], reverse=True)
    out: List[Hit] = []
    per: Dict[str, int] = {}
    for total, sess, m in ranked:
        k = sess.provider + ":" + sess.session_id
        if per.get(k, 0) >= per_session:
            continue
        per[k] = per.get(k, 0) + 1
        out.append(Hit(session=sess, message=m, score=round(total, 3), snippet=_snippet(m.text, query)))
        if len(out) >= hit_limit:
            break
    return out


def _snippet(text: str, query: str, width: int = 240) -> str:
    """Window around the phrase, else around the first query word that occurs."""
    text = " ".join((text or "").split())
    if not text:
        return ""
    tl = text.lower()
    ql = (query or "").strip().lower()
    idx, span = -1, 0
    if ql:
        idx, span = tl.find(ql), len(ql)
        if idx < 0:
            for w in sorted(tokenize(ql), key=len, reverse=True):
                j = tl.find(w)
                if j >= 0:
                    idx, span = j, len(w)
                    break
    if idx < 0:
        return text if len(text) <= width else text[: width - 1] + "…"
    start = max(0, idx - 60)
    # snap to a word boundary so the snippet doesn't start mid-word
    if start > 0:
        sp = text.rfind(" ", 0, start)
        start = sp + 1 if sp >= 0 and start - sp < 20 else start
    end = min(len(text), start + width)
    snip = text[start:end]
    if start > 0:
        snip = "…" + snip
    if end < len(text):
        snip = snip + "…"
    return snip
