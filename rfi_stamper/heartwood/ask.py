"""Heartwood ask — the Old Hand answers, or honestly does not.

Ask a question and the Old Hand searches the store BY MEANING, quotes what
it says with citations, restates it in plain field words when asked, and
summarizes across documents when several speak to the point.  Shop notes —
the self-learning lane — come back marked: an unverified note is ALWAYS
flagged so the GUI can label it "shop note — unverified".

The restriction to the trades is physics, not policy: every answer block is
a verbatim (or number-locked restated) sentence from hw_chunks.  When
retrieval is not confident, the Old Hand says so — "not in the knowledge
base yet" — and offers the nearest terms he DOES know.  He cannot invent a
code requirement, because nothing in this module can emit text that did not
come out of the store.
"""
from __future__ import annotations

from . import digest, lex, restate, search, thesaurus, vectors
from .store import NOTE_ORIGINS

QUOTE_BLOCKS = 3          # top passages carried into the answer
SENTENCES_PER_CHUNK = 2   # central sentences per quoted passage
DIGEST_MIN_CHUNKS = 4     # digest only when the answer spans...
DIGEST_MIN_DOCS = 2       # ...this many chunks across this many docs

REFUSAL = ("Not in the knowledge base yet. I only answer from the loaded "
           "documents, and nothing there backs this with enough confidence "
           "to cite.")


def _cite(doc_title: str, chunk_id: int) -> str:
    """" [source: doc §chunk]" — the suffix every emitted block carries."""
    return f" [source: {doc_title} §{chunk_id}]"


def related_terms(store, question: str, limit: int = 3) -> list[str]:
    """The nearest terms the Old Hand DOES know (refusal suggestions)."""
    out: list[str] = []
    seen: set[str] = set()

    def push(term: str) -> None:
        key = thesaurus.norm(term)
        if not key or key in seen:
            return
        seen.add(key)
        out.append(term)

    # Thesaurus bridges first (curated, always meaningful).
    toks = lex.tokenize(question)
    words = [t.raw for t in toks]
    for n in range(1, 4):
        for i in range(len(words) - n + 1):
            for e in thesaurus.expand(" ".join(words[i:i + n]), store):
                push(e["term"])
    # Then trained neighbors, best first across all query terms.
    if vectors.load(store):
        near = []
        for t in toks:
            if t.is_num:
                continue
            near.extend(vectors.similar_terms(store, t.t, 5, 0.3))
        near.sort(key=lambda nb: (-nb["cos"], nb["term"]))
        for nb in near:
            push(nb["term"])
    return out[:limit]


def _block(kind: str, text: str, row_origin: str, doc_title: str,
           chunk_id: int, unverified: bool) -> dict:
    """One answer block; note-lane chunks always read as kind 'note'."""
    if row_origin in NOTE_ORIGINS:
        kind = "note"
    return {"kind": kind, "text": text, "doc_title": doc_title,
            "chunk_id": chunk_id, "unverified": unverified}


def ask(store, question: str, mode: str = "quote", trade: str | None = None,
        include_unverified: bool = True) -> dict:
    """Answer a question from the store, or refuse honestly.  Returns::

        {blocks: [{kind: 'quote'|'restated'|'summary'|'note', text,
                   doc_title, chunk_id, unverified}],
         related: [terms], confidence: float, refused: bool, message: str}

    mode 'quote' returns verbatim cited sentences; mode 'plain' runs them
    through the number-locked restater (falls back to verbatim, never
    unsafe).  Unverified-origin blocks ALWAYS carry unverified=True.
    Every chunk that backs a block is logged as 'shown' feedback."""
    mode = "plain" if mode == "plain" else "quote"
    results = search.search(store, question, k=10, trade=trade,
                            include_unverified=include_unverified)
    related = related_terms(store, question, 3)
    confidence = results[0]["score"] if results else 0.0

    if not search.confident(results):
        message = REFUSAL + (
            f" Nearest terms I do know: {', '.join(related)}."
            if related else "")
        return {"blocks": [], "related": related, "confidence": confidence,
                "refused": True, "message": message}

    by_chunk = {r["chunk_id"]: r for r in results}
    entries = thesaurus.entries(store)   # approved-only substitution source
    blocks: list[dict] = []
    for r in results[:QUOTE_BLOCKS]:
        # The central sentences of this passage, verbatim with citation.
        for s in digest.summarize(store, [r["chunk_id"]],
                                  max_sentences=SENTENCES_PER_CHUNK):
            text = s["text"]
            kind = "quote"
            if mode == "plain":
                re_out = restate.restate(text, "plain", entries=entries)
                text = re_out["text"]
                kind = "restated" if re_out["changed"] else "quote"
            blocks.append(_block(
                kind, text + _cite(s["doc_title"], s["chunk_id"]),
                r["origin"], s["doc_title"], s["chunk_id"], r["unverified"]))

    # Across many documents: a graph-ranked digest of the whole result set.
    docs_in_play = {r["doc_id"] for r in results}
    if len(results) >= DIGEST_MIN_CHUNKS and len(docs_in_play) >= DIGEST_MIN_DOCS:
        already = {b["text"] for b in blocks}
        for s in digest.summarize(store, results, max_sentences=5):
            text = s["text"] + _cite(s["doc_title"], s["chunk_id"])
            if text in already:
                continue
            src = by_chunk.get(s["chunk_id"])
            blocks.append(_block(
                "summary", text, src["origin"] if src else "text",
                s["doc_title"], s["chunk_id"],
                bool(src and src["unverified"])))

    for cid in dict.fromkeys(b["chunk_id"] for b in blocks):
        store.log_feedback(question, cid, "shown")

    return {"blocks": blocks, "related": related, "confidence": confidence,
            "refused": False, "message": ""}


def mark_used(store, query: str, chunk_id: int) -> None:
    """GUI hook: the human actually used this chunk for this query — the
    lane-1 signal that boosts it for similar future questions."""
    store.log_feedback(query, chunk_id, "used")


def similar(store, term: str) -> dict:
    """The meaning neighborhood of one term: trained vector neighbors +
    thesaurus bridges.  What a "did you mean" UI feeds on."""
    vectors.load(store)
    return {"term": term,
            "neighbors": vectors.similar_terms(store, term, 8, 0.35),
            "thesaurus": thesaurus.expand(term, store)}
