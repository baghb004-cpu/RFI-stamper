"""Heartwood thesaurus — the curated meaning bridge.

Every trade has two vocabularies: the one on the drawings and the one on the
scaffold.  Heartwood speaks both.  This module carries the mapping — "hot
wire" is an ungrounded conductor, a sillcock is a hose bibb — as REVIEWABLE
DATA, never model magic: a shipped, human-curated seed file
(``thesaurus_seed.json``, loaded into hw_thesaurus as status 'seed') plus a
miner that reads the operator's own chunks for "also known as" phrasing and
files what it finds as UNVERIFIED proposals with a chunk citation.  Nothing
mined is ever used until a human approves it — the same rule the whole
self-learning side of Heartwood lives by.

Deterministic, offline, citable.
"""
from __future__ import annotations

import json
import os
import re

_SEED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "thesaurus_seed.json")

_seed_cache: list[dict] | None = None

# expand()/entries() draw from these statuses ONLY — mined-but-unreviewed
# rows never steer a search or a restatement.
_LIVE_STATUSES = ("seed", "approved")


def norm(term: str) -> str:
    """Normalize a term for matching: lowercase, collapsed whitespace, no
    edge dots."""
    t = re.sub(r"\s+", " ", ("" if term is None else str(term)).lower())
    return t.strip(". ").strip()


def seed_entries() -> list[dict]:
    """The shipped seed entries (parsed once; approved-only)."""
    global _seed_cache
    if _seed_cache is not None:
        return _seed_cache
    try:
        with open(_SEED_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        _seed_cache = [e for e in raw.get("entries", [])
                       if e and e.get("field") and e.get("canonical")
                       and e.get("approved") is True]
    except (OSError, ValueError):
        _seed_cache = []
    return _seed_cache


def ensure_seed(store) -> int:
    """Idempotently load the shipped seed into hw_thesaurus (status 'seed').
    Returns the number of rows inserted (0 on every open after the first)."""
    existing = {(norm(r["term"]), norm(r["canonical"]))
                for r in store.thesaurus_rows()}
    added = 0
    for e in seed_entries():
        key = (norm(e["field"]), norm(e["canonical"]))
        if key in existing:
            continue
        existing.add(key)
        store.add_thesaurus(e["field"], e["canonical"],
                            e.get("trade") or "general", "seed")
        added += 1
    return added


def entries(store) -> list[dict]:
    """The live thesaurus: seed rows plus HUMAN-APPROVED mined rows.  Each:
    {field, canonical, trade, approved, why} — 'why' says which bridge."""
    out = []
    for r in store.thesaurus_rows(_LIVE_STATUSES):
        out.append({"field": r["term"], "canonical": r["canonical"],
                    "trade": r["trade"] or "general", "approved": True,
                    "why": "thesaurus" if r["status"] == "seed" else "mined"})
    return out


def expand(term: str, store) -> list[dict]:
    """[{term, why, trade}] — both directions: a field word expands to its
    canonical phrase and a canonical phrase expands to its field words.
    Matching is normalized and exact-phrase (whole entry), never substring —
    "rock" must not fire on "bedrock"."""
    q = norm(term)
    if not q:
        return []
    out = []
    seen: set[str] = set()
    for e in entries(store):
        f = norm(e["field"])
        c = norm(e["canonical"])
        hit = None
        if q == f:
            hit = e["canonical"]
        elif q == c:
            hit = e["field"]
        if hit and norm(hit) not in seen:
            seen.add(norm(hit))
            out.append({"term": hit, "why": e["why"], "trade": e["trade"]})
    return out


# ------ miner: read the operator's own chunks for definitional phrasing ------

# "X, also known as Y" / "X (a.k.a. Y)" / "X, commonly called Y" /
# "X, referred to as Y" / "X (also called Y)".  Term windows are kept short
# so the miner proposes terms, not sentences.
_TERM = r"[A-Za-z][A-Za-z0-9'/-]*(?:\s+[A-Za-z][A-Za-z0-9'/-]*){0,4}"
_MINE_PATTERNS = [
    re.compile(
        "(" + _TERM + r")\s*[,(]\s*(?:is\s+)?(?:also\s+known\s+as"
        r"|a\.?\s?k\.?\s?a\.?|commonly\s+called|(?:also\s+)?referred\s+to\s+as"
        r"|also\s+called)\s+[\"“]?(" + _TERM + r")[\"”]?\)?",
        re.IGNORECASE),
    re.compile(
        "(" + _TERM + r")\s+(?:is\s+)?(?:also\s+known\s+as|commonly\s+called"
        r"|(?:also\s+)?referred\s+to\s+as|also\s+called)"
        r"\s+(?:an?\s+|the\s+)?[\"“]?(" + _TERM + r")[\"”]?",
        re.IGNORECASE),
]

# Leading glue the term window may have swallowed ("the", "a", "an", "or"...).
_LEAD_GLUE = re.compile(
    r"^(?:the|a|an|or|and|is|are|it|its|this|that|of|as|in|on|for|to|so"
    r"|called|known|referred|also)\s+", re.IGNORECASE)
_TRAIL_GLUE = re.compile(
    r"\s+(?:is|are|was|were|the|a|an|or|and|of|in|on|for|to|as)$",
    re.IGNORECASE)
_LEFT_COPULA = re.compile(
    r"\b(?:is|are|was|were|called|named)\s+(?:the\s+|a\s+|an\s+)?(.+)$",
    re.IGNORECASE)
_RIGHT_TRAIL = re.compile(
    r"\s+(?:in|on|at|by|with|from|for|per|which|that|when|where|used|found"
    r"|among)\b[\s\S]*$", re.IGNORECASE)


def _clean_term(s: str) -> str:
    """Trim a mined term candidate down to the meaningful phrase ('' rejects)."""
    t = norm(s)
    for _ in range(6):
        if not _LEAD_GLUE.match(t):
            break
        t = _LEAD_GLUE.sub("", t, count=1)
    t = _TRAIL_GLUE.sub("", t)
    if len(t) < 3 or len(t) > 60 or not re.search(r"[a-z]", t):
        return ""
    return t


def _clean_left(s: str) -> str:
    """The LEFT side of "X, also known as Y" often drags its clause along
    ("a drain is the wet vent") — keep only the noun phrase after the last
    copula/naming verb."""
    t = str(s)
    m = _LEFT_COPULA.search(t)
    if m:
        t = m.group(1)
    return _clean_term(t)


def _clean_right(s: str) -> str:
    """The RIGHT side often trails into a prepositional phrase — cut at the
    first trailing preposition/clause."""
    return _clean_term(_RIGHT_TRAIL.sub("", str(s)))


def mine(store) -> dict:
    """Scan every chunk for definitional phrasing and file each
    (term, canonical) pair into hw_thesaurus as status 'unverified' WITH its
    source_chunk citation.  Never auto-approved; duplicates (against the
    seed and against prior proposals) are skipped.
    Returns {'proposed': n, 'scanned': m}."""
    known = {norm(r["term"]) + " " + norm(r["canonical"])
             for r in store.thesaurus_rows()}
    found: list[tuple[str, str, int]] = []
    scanned = 0
    for cid, text in store.iter_chunks():
        scanned += 1
        for pat in _MINE_PATTERNS:
            for m in pat.finditer(str(text or "")):
                a = _clean_left(m.group(1))
                b = _clean_right(m.group(2))
                if not a or not b or a == b:
                    continue
                # field term = the alias (b); canonical = the defined term (a)
                key = b + " " + a
                if key in known:
                    continue
                known.add(key)
                found.append((b, a, cid))
    for term, canonical, cid in found:
        store.add_thesaurus(term, canonical, None, "unverified", cid)
    return {"proposed": len(found), "scanned": scanned}


def list_proposed(store) -> list[dict]:
    """Everything mined and still waiting for a human, with its citation."""
    out = []
    for r in store.thesaurus_rows(("unverified",)):
        chunk = store.chunk(r["source_chunk"]) if r["source_chunk"] else None
        out.append({"id": r["id"], "term": r["term"],
                    "canonical": r["canonical"],
                    "source_chunk": r["source_chunk"],
                    "doc_title": chunk["title"] if chunk else None,
                    "status": r["status"]})
    return out


def approve(store, row_id: int) -> bool:
    """Human gate: promote one mined proposal into the live thesaurus."""
    return store.set_thesaurus_status(row_id, "approved")


def reject(store, row_id: int) -> bool:
    """Human gate: discard one mined proposal (kept as a row, marked
    rejected)."""
    return store.set_thesaurus_status(row_id, "rejected")


def stats(store) -> dict:
    """Counts for status displays."""
    rows = store.thesaurus_rows()
    out = {"seed": 0, "approved": 0, "unverified": 0, "rejected": 0}
    for r in rows:
        out[r["status"]] = out.get(r["status"], 0) + 1
    return out
