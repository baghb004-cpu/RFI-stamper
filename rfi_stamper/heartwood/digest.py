"""Heartwood digest — multi-document extractive summary (graph-ranked).

When four documents each say a piece of the answer, Heartwood reads them all
and hands back the sentences that matter — VERBATIM, each with its citation.
There is no generation here: sentences are ranked by centrality (a
similarity graph over sentence vectors, scored by power-iteration PageRank,
implemented from scratch), then picked for diversity (maximal marginal
relevance) so seven sentences cover seven points, not one point seven times.

The safety property is structural: every output sentence is a substring of
a stored chunk, carrying {doc_title, chunk_id}.  It cannot invent a
requirement because it cannot write — it can only choose.
"""
from __future__ import annotations

import math
import re

from . import vectors

SIM_FLOOR = 0.15    # graph edge threshold
DAMPING = 0.85      # PageRank damping factor
ITERATIONS = 30     # power-iteration rounds
MMR_LAMBDA = 0.7    # relevance vs. diversity trade-off

# Abbreviations that a period does NOT end a sentence after.
_ABBREV = re.compile(
    r"(?:\b(?:e\.g|i\.e|etc|no|min|max|approx|fig|sec|art|para|dia|dwg|spec"
    r"|typ|misc|dept|bldg|mfr|qty|std|vs|inc|ltd|mr|mrs|ms|dr|st)\.$)",
    re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9]+")


def split_sentences(text: str) -> list[str]:
    """Abbreviation-safe sentence splitter.  Never splits inside a decimal
    or a code reference ("NEC 210.8" stays whole) and respects common
    construction abbreviations.  Returns trimmed sentences in order."""
    s = re.sub(r"\s+", " ", "" if text is None else str(text)).strip()
    if not s:
        return []
    out: list[str] = []
    start = 0
    n = len(s)
    for i, ch in enumerate(s):
        if ch not in ".!?":
            continue
        nxt = s[i + 1] if i + 1 < n else None
        if ch == ".":
            # decimal / section number: digit on both sides
            if i > 0 and s[i - 1].isdigit() and nxt is not None and nxt.isdigit():
                continue
            # known abbreviation before the period
            back = s[max(start, i - 8):i + 1]
            if _ABBREV.search(back):
                continue
            # single-letter initial ("J. Smith")
            if re.search(r"\b[A-Z]$", s[start:i]):
                continue
        # sentence end only when followed by space + capital/digit, or EOF
        after = s[i + 2] if i + 2 < n else ""
        if nxt is None or (nxt == " " and re.match(r"[A-Z0-9\"“(]", after)):
            sent = s[start:i + 1].strip()
            if sent:
                out.append(sent)
            start = i + 1
    tail = s[start:].strip()
    if tail:
        out.append(tail)
    return out


def page_rank(sim: list[list[float]]) -> list[float]:
    """From-scratch PageRank by power iteration over a dense similarity
    matrix (symmetric, zero diagonal).  Returns rank scores (sum ~ 1)."""
    n = len(sim)
    if n == 0:
        return []
    out_sum = [sum(row) for row in sim]
    rank = [1.0 / n] * n
    for _ in range(ITERATIONS):
        nxt = [(1.0 - DAMPING) / n] * n
        for j in range(n):
            if out_sum[j] == 0.0:
                share = DAMPING * rank[j] / n      # dangling node
                for i in range(n):
                    nxt[i] += share
                continue
            row = sim[j]
            rj = DAMPING * rank[j] / out_sum[j]
            for i in range(n):
                if row[i] > 0.0:
                    nxt[i] += rj * row[i]
        rank = nxt
    return rank


def _lexical_vec(sentence: str) -> dict[str, int]:
    """Cheap lexical bag fallback when the vector model is not trained."""
    counts: dict[str, int] = {}
    for w in _WORD.findall(sentence.lower()):
        counts[w] = counts.get(w, 0) + 1
    return counts


def _lexical_cos(a: dict[str, int], b: dict[str, int]) -> float:
    na = sum(v * v for v in a.values())
    nb = sum(v * v for v in b.values())
    if not na or not nb:
        return 0.0
    dot = sum(v * b[k] for k, v in a.items() if k in b)
    return dot / (math.sqrt(na) * math.sqrt(nb))


def summarize(store, chunk_ids_or_results, max_sentences: int = 7) -> list[dict]:
    """Graph-ranked extractive digest across chunks.

    Accepts raw chunk ids or search results ({'chunk_id': ...}).  Sentences
    come back VERBATIM with their source chunk's citation, best-first::

        [{text, doc_title, chunk_id, score}]
    """
    ids: list[int] = []
    for x in (chunk_ids_or_results or []):
        cid = x if isinstance(x, int) else (x or {}).get("chunk_id")
        if isinstance(cid, int) and cid not in ids:
            ids.append(cid)
    if not ids:
        return []

    sentences: list[dict] = []
    for cid in ids:
        row = store.chunk(cid)
        if row is None:
            continue
        for sent in split_sentences(row["text"]):
            if len(sent.split()) < 4:
                continue          # fragments carry no summary weight
            sentences.append({"text": sent, "doc_title": row["title"],
                              "chunk_id": cid})
    n = len(sentences)
    if n == 0:
        return []

    # Sentence vectors: trained phrase vectors when available, else lexical.
    trained = vectors.load(store)
    vecs = [vectors.phrase_vec(store, s["text"]) if trained else None
            for s in sentences]
    lex_bags = [_lexical_vec(s["text"]) for s in sentences]

    def sim_of(i: int, j: int) -> float:
        if vecs[i] is not None and vecs[j] is not None:
            return vectors.cosine(vecs[i], vecs[j])
        return _lexical_cos(lex_bags[i], lex_bags[j])

    # Similarity graph (edges below the floor are noise, dropped).
    sim = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            c = sim_of(i, j)
            if c > SIM_FLOOR:
                sim[i][j] = c
                sim[j][i] = c
    rank = page_rank(sim)

    # MMR selection: relevance (rank) balanced against similarity to what is
    # already picked, so the digest covers ground instead of repeating itself.
    picked: list[int] = []
    candidates = list(range(n))
    max_rank = max(max(rank), 1e-12)
    while len(picked) < min(max_sentences, n) and candidates:
        best_idx = -1
        best_val = -math.inf
        for i in candidates:
            max_sim = max((sim_of(i, p) for p in picked), default=0.0)
            val = MMR_LAMBDA * (rank[i] / max_rank) - (1 - MMR_LAMBDA) * max_sim
            if val > best_val + 1e-12 or (abs(val - best_val) <= 1e-12
                                          and (best_idx < 0 or i < best_idx)):
                best_val = val
                best_idx = i
        if best_idx < 0:
            break
        picked.append(best_idx)
        candidates.remove(best_idx)

    return [{"text": sentences[i]["text"],
             "doc_title": sentences[i]["doc_title"],
             "chunk_id": sentences[i]["chunk_id"],
             "score": rank[i]} for i in picked]
