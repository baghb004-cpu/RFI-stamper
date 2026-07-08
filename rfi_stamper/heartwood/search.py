"""Heartwood search — hybrid meaning search over the store.

Keyword search finds the words you typed; Heartwood finds the words you
MEANT.  A query is expanded through two bridges — the curated trade
thesaurus ("hot wire" -> "ungrounded conductor") and the store-trained term
vectors (neighbors by shared context) — then run through the pure-Python
BM25 index for a shortlist, and re-ranked by a blend of lexical score,
whole-query vector similarity to each chunk, and an exact-phrase bonus.
Chunks a human actually used for similar past questions get a small,
capped usage bonus (lane-1 self-learning: it reorders, never invents).

The other half of the job is knowing when NOT to answer: ``confident()`` is
the honesty gate.  A thin or off-trade result set fails it, and callers
refuse rather than reach.  Answers can only ever come from hw_chunks —
there is nothing else here to answer from.

Deterministic, offline, no dependencies beyond the store handle passed in.
"""
from __future__ import annotations

import re

from . import lex, thesaurus, vectors

SHORTLIST = 200          # BM25 candidates before re-rank
W_BM25 = 0.55            # lexical weight
W_COS = 0.35             # meaning weight
W_PHRASE = 0.10          # exact-phrase bonus weight
BM25_SQUASH = 4.0        # b/(b+SQUASH): absolute-ish 0..1 lexical signal
NEIGHBOR_MIN_COS = 0.45  # vector neighbors must be at least this close
NEIGHBOR_PER_TERM = 3    # expansions per query term from the vector side
CONFIDENCE_MIN = 0.32    # below this, refuse honestly
CONFIDENCE_CHUNKS = 2    # need at least this many independent chunks
USAGE_BOOST_CAP = 0.05   # ceiling on the lane-1 usage bonus
USAGE_BOOST_STEP = 0.02  # per past 'used' mark on a similar query
USAGE_MIN_JACCARD = 0.5  # stemmed-term overlap for "similar query"


def expand_query(store, query: str) -> tuple[list[str], list[dict]]:
    """Expand a query into (phrases, expansions):

    * phrases: every search phrase (original words + thesaurus + neighbors);
    * expansions: [{term, why, from}] — the audit trail of WHY each
      expansion is in the net ('thesaurus' | 'mined' | 'vector').
    """
    toks = lex.tokenize(query)
    words = [t.raw for t in toks]
    phrases: dict[str, None] = {w.lower(): None for w in words}
    expansions: list[dict] = []

    def add_exp(term: str, why: str, src: str) -> None:
        key = str(term).lower()
        if key in phrases:
            return
        phrases[key] = None
        expansions.append({"term": term, "why": why, "from": src})

    # Thesaurus: try every n-gram (1..3) of the query — field phrases are
    # often multiword ("hot wire", "closet flange").
    for n in range(1, 4):
        for i in range(len(words) - n + 1):
            gram = " ".join(words[i:i + n])
            for e in thesaurus.expand(gram, store):
                add_exp(e["term"], e["why"], gram)

    # Trained vectors: nearest neighbors of each single content term.
    if vectors.load(store):
        for t in toks:
            if t.is_num:
                continue          # never "expand" a number or a code ref
            for nb in vectors.similar_terms(store, t.t, NEIGHBOR_PER_TERM,
                                            NEIGHBOR_MIN_COS):
                if len(nb["term"]) < 3 or any(c.isdigit() for c in nb["term"]):
                    continue
                add_exp(nb["term"], "vector", t.raw)
    return list(phrases), expansions


def _make_snippet(content: str, phrases: list[str]) -> str:
    """First ~160 chars of the chunk around the first matched phrase."""
    text = str(content or "")
    lower = text.lower()
    at = -1
    for p in phrases:
        i = lower.find(p.lower())
        if i >= 0 and (at < 0 or i < at):
            at = i
    start = max(0, 0 if at < 0 else at - 40)
    cut = re.sub(r"\s+", " ", text[start:start + 160]).strip()
    return (("…" if start > 0 else "") + cut
            + ("…" if start + 160 < len(text) else ""))


def _usage_boosts(store, query: str) -> dict[int, float]:
    """Lane-1 learning: chunks a human marked 'used' for similar past
    queries earn a small bonus, capped at USAGE_BOOST_CAP.  Similarity is
    stemmed-term Jaccard — cheap, deterministic, and query-shaped."""
    q_terms = {t.t for t in lex.terms(query)}
    if not q_terms:
        return {}
    counts: dict[int, int] = {}
    for past_q, cid in store.used_feedback():
        p_terms = {t.t for t in lex.terms(past_q)}
        if not p_terms:
            continue
        jac = len(q_terms & p_terms) / len(q_terms | p_terms)
        if jac >= USAGE_MIN_JACCARD:
            counts[cid] = counts.get(cid, 0) + 1
    return {cid: min(USAGE_BOOST_CAP, USAGE_BOOST_STEP * n)
            for cid, n in counts.items()}


def search(store, query: str, k: int = 10, trade: str | None = None,
           include_unverified: bool = True) -> list[dict]:
    """Hybrid search.  Returns, best first::

        [{chunk_id, doc_id, doc_title, trade, origin, score, bm25, cos,
          snippet, why, unverified}]

    ``why`` lists the expansions that actually appear in that chunk — the
    user can see exactly which meaning bridge found it."""
    q = ("" if query is None else str(query)).strip()
    if not q:
        return []

    phrases, expansions = expand_query(store, q)
    q_terms: set[str] = set()
    for p in phrases:
        q_terms.update(t.t for t in lex.terms(p))
    shortlist = store.bm25(sorted(q_terms), SHORTLIST, trade,
                           include_unverified)
    if not shortlist:
        return []

    qv = vectors.phrase_vec(store, q) if vectors.load(store) else None
    q_norm = re.sub(r"\s+", " ", q.lower()).strip()
    boosts = _usage_boosts(store, q)

    scored = []
    for cid, b in shortlist:
        row = store.chunk(cid)
        if row is None:
            continue
        bm25_norm = b / (b + BM25_SQUASH)
        cos = 0.0
        if qv is not None:
            cv = vectors.chunk_vec(store, cid)
            if cv is not None:
                cos = max(0.0, vectors.cosine(qv, cv))
        hay = re.sub(r"\s+", " ", (row["title"] + " " + row["text"]).lower())
        phrase_bonus = 1.0 if q_norm and q_norm in hay else 0.0
        why = [{"term": e["term"], "why": e["why"]} for e in expansions
               if e["term"].lower() in hay]
        scored.append({
            "chunk_id": cid,
            "doc_id": int(row["doc_id"]),
            "doc_title": row["title"],
            "trade": row["trade"],
            "origin": row["origin"],
            "score": (W_BM25 * bm25_norm + W_COS * cos
                      + W_PHRASE * phrase_bonus + boosts.get(cid, 0.0)),
            "bm25": b,
            "cos": cos,
            "snippet": _make_snippet(row["text"], phrases),
            "why": why,
            "unverified": store.chunk_unverified(row),
        })
    scored.sort(key=lambda r: (-r["score"], r["chunk_id"]))
    return scored[:k]


def confident(results: list[dict]) -> bool:
    """The honesty gate: True only when the top result clears the score
    floor AND at least two independent chunks back the answer.  Callers must
    refuse when this is False."""
    if not results or len(results) < CONFIDENCE_CHUNKS:
        return False
    if len({r["chunk_id"] for r in results}) < CONFIDENCE_CHUNKS:
        return False
    return results[0]["score"] >= CONFIDENCE_MIN
