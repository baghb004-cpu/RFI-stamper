"""Heartwood vectors — from-scratch distributional semantics (Random Indexing).

How a hand learns what a word means without a dictionary: by the company it
keeps.  Every vocabulary term gets a deterministic sparse SIGNATURE (a few
+1/-1 spikes at hashed positions).  One streaming pass over the operator's
own knowledge base adds every neighbor's signature into a term's context
vector, weighted by distance.  Terms that keep the same company — "hot wire"
next to breakers and ampacity, "ungrounded conductor" next to the same —
end up pointing the same way.  No pretrained weights, no downloads: the
model IS the operator's store, distilled into geometry.

Deterministic end to end: signatures come from a seeded hash (fnv1a ->
xorshift32 PRNG) ported EXACTLY from the field-proven reference
implementation — including its UTF-16 code-unit hashing and its seed string
prefixes — so two trainings over the same corpus produce identical vectors,
and vectors stay stable across rebuilds and across the two codebases.

Persistence (store.py):
    hw_vectors(term PK, df, vec BLOB)      float32 numpy bytes, L2-normalized
    hw_chunk_vecs(chunk_id PK, vec BLOB)   TF-IDF-weighted mean of term vecs
    hw_meta(key PK, value)

Scale guard: vocabulary capped at 60k terms by document-frequency rank;
chunks are streamed (never a full-corpus string in memory).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np

from . import lex

DIM = 256            # vector dimensionality
SIG_K = 8            # nonzero (+1/-1) entries per signature
WINDOW = 4           # sliding context window (± tokens)
VOCAB_CAP = 60000    # max vocabulary, by df rank
SUBSAMPLE_TOP = 0.01  # subsample the top-1% most frequent terms
MIN_DF = 2           # a term seen once is noise, not vocabulary

# ------------------------------------------------- deterministic hash / PRNG --

_MASK32 = 0xFFFFFFFF


def _utf16_units(s: str):
    """Iterate a string as UTF-16 code units — what the reference hash saw."""
    for ch in s:
        o = ord(ch)
        if o > 0xFFFF:                      # surrogate pair
            o -= 0x10000
            yield 0xD800 + (o >> 10)
            yield 0xDC00 + (o & 0x3FF)
        else:
            yield o


def fnv1a(s: str) -> int:
    """fnv1a 32-bit hash of a string — the seed source for everything here."""
    h = 0x811C9DC5
    for unit in _utf16_units(s):
        h ^= unit
        h = (h * 0x01000193) & _MASK32     # the shift-add form of *16777619
    return h


def xorshift32(seed: int):
    """xorshift32 PRNG — tiny, fast, fully reproducible.  Returns a callable
    yielding floats in [0, 1)."""
    x = seed & _MASK32
    if x == 0:
        x = 0x9E3779B9

    def nxt() -> float:
        nonlocal x
        x = (x ^ (x << 13)) & _MASK32
        x ^= x >> 17
        x = (x ^ (x << 5)) & _MASK32
        return x / 0x100000000

    return nxt


def signature(term: str) -> tuple[np.ndarray, np.ndarray]:
    """Deterministic sparse signature of a term: SIG_K distinct positions in
    [0, DIM) with +1/-1 signs, all derived from the term's own hash.
    Returned as parallel (idx int32, sign int8) arrays.  The 'jm-sig:' seed
    prefix is kept verbatim from the reference implementation so signatures
    are identical across both codebases."""
    rnd = xorshift32(fnv1a("jm-sig:" + term))
    idx = np.zeros(SIG_K, dtype=np.int32)
    sign = np.zeros(SIG_K, dtype=np.int8)
    seen: set[int] = set()
    i = 0
    while i < SIG_K:
        p = int(rnd() * DIM)
        if p in seen:
            continue                        # collision consumes only one draw
        seen.add(p)
        idx[i] = p
        sign[i] = -1 if rnd() < 0.5 else 1
        i += 1
    return idx, sign


def hash_float(term: str, n: int) -> float:
    """Deterministic uniform [0,1) draw for a (term, occurrence) pair."""
    return fnv1a(f"jm-sub:{term}:{n}") / 0x100000000


# ------------------------------------------------------------------- math --

def cosine(a, b) -> float:
    """Cosine similarity of two float32 vectors (0 when either is zero)."""
    if a is None or b is None:
        return 0.0
    na = float(np.dot(a, a))
    nb = float(np.dot(b, b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b)) / (math.sqrt(na) * math.sqrt(nb))


def l2norm(v: np.ndarray) -> np.ndarray:
    """L2-normalize in place; returns the same array (zero stays zero)."""
    s = float(np.dot(v, v))
    if s == 0.0:
        return v
    v *= np.float32(1.0 / math.sqrt(s))
    return v


# --------------------------------------------------------- model / training --

class _Model:
    """In-memory trained state: term matrix + df map, cached on the store."""

    __slots__ = ("terms", "index", "mat", "df", "docs", "trained_at")

    def __init__(self, vecs: dict[str, np.ndarray], df: dict[str, int],
                 docs: int, trained_at: str):
        self.terms = list(vecs.keys())
        self.index = {t: i for i, t in enumerate(self.terms)}
        self.mat = (np.stack([vecs[t] for t in self.terms])
                    if self.terms else np.zeros((0, DIM), dtype=np.float32))
        self.df = df
        self.docs = docs
        self.trained_at = trained_at

    def vec(self, term: str) -> np.ndarray | None:
        i = self.index.get(term)
        return None if i is None else self.mat[i]


def train(store, log=None) -> dict:
    """Idempotent full rebuild: two streaming passes over hw_chunks (df
    census, then context accumulation), then TF-IDF-weighted chunk vectors —
    all persisted, all deterministic.  Returns stats."""
    say = log if callable(log) else (lambda *_: None)

    # Pass 1 — document-frequency census (df = how many chunks carry a term).
    df: dict[str, int] = {}
    total_tokens = 0
    docs = 0
    for _cid, text in store.iter_chunks():
        docs += 1
        seen: set[str] = set()
        for t in lex.terms(text):
            total_tokens += 1
            if t.t not in seen:
                seen.add(t.t)
                df[t.t] = df.get(t.t, 0) + 1
    say(f"heartwood: census {len(df)} raw terms across {docs} chunks")

    # Vocabulary: cap by df rank; drop hapax noise.  Ties break lexically so
    # the cut line is deterministic.
    ranked = sorted(((t, d) for t, d in df.items() if d >= MIN_DF),
                    key=lambda td: (-td[1], td[0]))[:VOCAB_CAP]
    vocab = dict(ranked)

    # Subsampling threshold: the df at the top-1% rank boundary.  Occurrences
    # of terms above it are kept with probability sqrt(threshold/df) —
    # frequent glue words stop drowning out the trade terms.
    top_n = max(1, int(len(ranked) * SUBSAMPLE_TOP))
    subsample_df = ranked[min(top_n, len(ranked)) - 1][1] if ranked else float("inf")

    # Pass 2 — sliding-window context accumulation into float32 vectors.
    vecs = {term: np.zeros(DIM, dtype=np.float32) for term in vocab}
    sig_cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    def sig_of(term: str):
        s = sig_cache.get(term)
        if s is None:
            s = signature(term)
            sig_cache[term] = s
        return s

    occ_count: dict[str, int] = {}   # per-term counter, deterministic subsample

    for _cid, text in store.iter_chunks():
        stream: list[str] = []
        for t in lex.terms(text):
            d = vocab.get(t.t)
            if d is None:
                continue
            if d > subsample_df:
                n = occ_count.get(t.t, 0) + 1
                occ_count[t.t] = n
                if hash_float(t.t, n) >= math.sqrt(subsample_df / d):
                    continue                 # dropped
            stream.append(t.t)
        for i, term in enumerate(stream):
            target = vecs[term]
            lo = max(0, i - WINDOW)
            hi = min(len(stream) - 1, i + WINDOW)
            for j in range(lo, hi + 1):
                if j == i:
                    continue
                w = np.float32(1.0 / abs(j - i))
                idx, sign = sig_of(stream[j])
                target[idx] += sign * w      # idx positions are distinct
    for v in vecs.values():
        l2norm(v)
    say(f"heartwood: trained {len(vecs)} term vectors")

    # Chunk vectors: TF-IDF-weighted mean of member-term vectors, L2-normed.
    def idf(term: str) -> float:
        return math.log(1.0 + docs / (1.0 + vocab.get(term, 0)))

    chunk_vecs: list[tuple[int, np.ndarray]] = []
    for cid, text in store.iter_chunks():
        tf: dict[str, int] = {}
        for t in lex.terms(text):
            if t.t in vocab:
                tf[t.t] = tf.get(t.t, 0) + 1
        cv = np.zeros(DIM, dtype=np.float32)
        for term, f in tf.items():
            cv += vecs[term] * np.float32((1.0 + math.log(f)) * idf(term))
        chunk_vecs.append((cid, l2norm(cv)))

    # Persist (idempotent rebuild inside one transaction).
    trained_at = datetime.now(timezone.utc).isoformat()
    store.save_vectors(
        [(term, vocab[term], v.tobytes()) for term, v in vecs.items()],
        [(cid, v.tobytes()) for cid, v in chunk_vecs],
        {"dim": str(DIM), "vocab": str(len(vecs)),
         "chunks": str(len(chunk_vecs)), "docs": str(docs),
         "tokens": str(total_tokens), "trained_at": trained_at})

    store._vec_model = _Model(vecs, vocab, docs, trained_at)
    stats = {"vocab": len(vecs), "chunks": len(chunk_vecs), "docs": docs,
             "tokens": total_tokens, "trained_at": trained_at, "dim": DIM}
    say(f"heartwood: training done ({stats['vocab']} terms, "
        f"{stats['chunks']} chunk vectors)")
    return stats


# ------------------------------------------------------ loading + query API --

def load(store) -> bool:
    """Load persisted vectors into memory (no-op if already loaded and
    fresh).  True when a trained model is available."""
    trained_at = store.get_meta("trained_at")
    model = getattr(store, "_vec_model", None)
    if trained_at is None:
        return model is not None and len(model.terms) > 0
    if model is not None and model.trained_at == trained_at:
        return True
    rows = [(term, d, blob) for term, d, blob in store.iter_vectors()]
    if len(rows) > VOCAB_CAP:
        # the Corral: the vocabulary cap holds at LOAD too — an oversized
        # or hand-edited store cannot balloon memory.  Same cut line as
        # train(): df rank, ties lexical.
        rows.sort(key=lambda r: (-r[1], r[0]))
        del rows[VOCAB_CAP:]
    vecs: dict[str, np.ndarray] = {}
    dfm: dict[str, int] = {}
    for term, d, blob in rows:
        vecs[term] = np.frombuffer(blob, dtype=np.float32).copy()
        dfm[term] = d
    docs = int(store.get_meta("docs") or 0)
    store._vec_model = _Model(vecs, dfm, docs, trained_at)
    return len(vecs) > 0


def unload(store) -> None:
    """Drop the in-memory model (tests / re-open on another store)."""
    store._vec_model = None


def term_vec(store, term: str) -> np.ndarray | None:
    """Vector for a term.  Stems the term the same way training did, so
    callers can pass surface forms ("relays" finds "relay"'s vector)."""
    model = getattr(store, "_vec_model", None)
    if model is None:
        return None
    raw = ("" if term is None else str(term)).lower().strip()
    v = model.vec(raw)
    return v if v is not None else model.vec(lex.stem(raw))


def phrase_vec(store, text: str) -> np.ndarray | None:
    """IDF-weighted mean of the phrase's term vectors (same weighting as
    chunk vectors), L2-normed.  None when no term is in vocabulary."""
    model = getattr(store, "_vec_model", None)
    if model is None:
        return None
    docs = model.docs or 1
    out = np.zeros(DIM, dtype=np.float32)
    hits = 0
    for t in lex.terms(text):
        v = model.vec(t.t)
        if v is None:
            continue
        w = math.log(1.0 + docs / (1.0 + model.df.get(t.t, 0)))
        out += v * np.float32(w)
        hits += 1
    return l2norm(out) if hits else None


def similar_terms(store, term: str, k: int = 8,
                  min_cos: float = 0.35) -> list[dict]:
    """Nearest vocabulary neighbors by cosine — the raw material for meaning
    expansion.  [{term, cos, df}], best first, ties broken lexically."""
    model = getattr(store, "_vec_model", None)
    if model is None:
        return []
    raw = ("" if term is None else str(term)).lower().strip()
    key = raw if raw in model.index else lex.stem(raw)
    i = model.index.get(key)
    if i is None:
        return []
    v = model.mat[i]
    nv = float(np.dot(v, v))
    if nv == 0.0:
        return []
    norms = np.linalg.norm(model.mat, axis=1)
    norms[norms == 0.0] = 1.0
    sims = (model.mat @ v) / (norms * math.sqrt(nv))
    out = [{"term": model.terms[j], "cos": float(sims[j]),
            "df": model.df.get(model.terms[j], 0)}
           for j in np.nonzero(sims >= min_cos)[0] if j != i]
    out.sort(key=lambda r: (-r["cos"], r["term"]))
    return out[:k]


def chunk_vec(store, chunk_id: int) -> np.ndarray | None:
    """Persisted chunk vector (None when absent)."""
    blob = store.get_chunk_vec(chunk_id)
    if blob is None:
        return None
    return np.frombuffer(blob, dtype=np.float32)


def meta(store) -> dict:
    """Training metadata from the store ({} when never trained)."""
    return store.all_meta()
