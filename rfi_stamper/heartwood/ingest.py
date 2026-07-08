"""Heartwood ingest — everything that puts knowledge into the store.

Four doors in, one honesty rule.  Imports and documents (a knowledge-base
file from the companion trade estimator, a PDF, pasted text) become regular
chunks.  Notes — RFI answers Planloom captured, daybook lines, things the
user teaches — enter through the self-learning lane: they land UNVERIFIED,
are indexed with their origin label so every answer can flag them, and only
a human's ``trust_note`` promotes them.  NOTHING enters trusted content
automatically.

``rebuild`` retrains the whole meaning layer (postings + vectors + thesaurus
miner) and is cheap enough to run after every ingest.  No network anywhere:
the importer reads a local sqlite file, the PDF reader is the bundled fitz.
"""
from __future__ import annotations

import os
import re
import sqlite3

from . import thesaurus, vectors

CHUNK_CHARS = 1200      # target chunk size, paragraph-aligned

# ------------------------------------------------------------- chunking -----

def chunk_text(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Split text into ~size-char chunks, aligned to paragraph boundaries
    (blank lines); an oversized paragraph falls back to sentence-ish/space
    splits so no chunk balloons."""
    text = ("" if text is None else str(text)).strip()
    if not text:
        return []
    paras: list[str] = []
    for p in re.split(r"\n\s*\n", text):
        p = p.strip()
        if not p:
            continue
        while len(p) > size:                    # oversized paragraph
            cut = p.rfind(". ", 0, size)
            if cut < size // 2:
                cut = p.rfind(" ", 0, size)
            if cut <= 0:
                cut = size
            paras.append(p[:cut + 1].strip())
            p = p[cut + 1:].strip()
        if p:
            paras.append(p)
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if cur and len(cur) + len(p) + 2 > size:
            chunks.append(cur)
            cur = p
        else:
            cur = (cur + "\n\n" + p) if cur else p
    if cur:
        chunks.append(cur)
    return chunks


def _add_doc_with_chunks(store, title: str, text: str, trade: str | None,
                         source: str | None, origin: str) -> tuple[int, int]:
    doc_id = store.add_document(title, trade, source, origin)
    n = 0
    for seq, chunk in enumerate(chunk_text(text)):
        store.add_chunk(doc_id, seq, chunk)
        n += 1
    return doc_id, n


# ------------------------------------------------------------ documents -----

def add_text(store, title: str, text: str, trade: str | None = None) -> dict:
    """Index a plain-text document.  Returns {'doc_id', 'chunks'}."""
    doc_id, n = _add_doc_with_chunks(store, title or "untitled", text, trade,
                                     None, "text")
    return {"doc_id": doc_id, "chunks": n}


def add_pdf(store, path: str, trade: str | None = None) -> dict:
    """Index a local PDF (text extracted per page via the bundled fitz).
    Returns {'doc_id', 'chunks'}."""
    import fitz                                  # deferred: engine loads fast
    doc = fitz.open(path)
    try:
        text = "\n\n".join(page.get_text() for page in doc)
    finally:
        doc.close()
    title = os.path.splitext(os.path.basename(path))[0]
    doc_id, n = _add_doc_with_chunks(store, title, text, trade, path, "pdf")
    return {"doc_id": doc_id, "chunks": n}


# --------------------------------------------------------------- import -----

_KB_DOC_COLS = {"id", "source", "title", "trade"}
_KB_CHUNK_COLS = {"id", "document_id", "ord", "heading", "content"}


def import_tradeforge(store, db_path: str) -> dict:
    """Seed Heartwood from the companion trade estimator's knowledge base.

    Reads its sqlite file (kb_documents / kb_chunks, plus human-approved
    thesaurus rows when present), copies everything into hw_* with origin
    'import', then retrains.  Copes with the db being absent or foreign:
    returns {'docs': 0, ..., 'error': '<clear reason>'} instead of raising.
    Re-import is idempotent per document source."""
    counts = {"docs": 0, "chunks": 0, "thesaurus": 0, "error": None}
    if not os.path.isfile(db_path):
        counts["error"] = f"knowledge base not found: {db_path}"
        return counts
    try:
        src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as e:
        counts["error"] = f"cannot open knowledge base: {e}"
        return counts
    try:
        src.row_factory = sqlite3.Row
        try:
            tables = {r["name"] for r in src.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
        except sqlite3.Error as e:
            counts["error"] = f"not a sqlite knowledge base: {e}"
            return counts
        if not {"kb_documents", "kb_chunks"} <= tables:
            counts["error"] = ("no kb_documents/kb_chunks tables in "
                               f"{os.path.basename(db_path)} — not a "
                               "knowledge-base export")
            return counts
        cols = {r["name"] for r in src.execute("PRAGMA table_info(kb_documents)")}
        ccols = {r["name"] for r in src.execute("PRAGMA table_info(kb_chunks)")}
        if not (_KB_DOC_COLS <= cols and _KB_CHUNK_COLS <= ccols):
            counts["error"] = ("kb_documents/kb_chunks columns do not match "
                               "the expected knowledge-base schema")
            return counts

        for d in src.execute(
                "SELECT id, source, trade, title FROM kb_documents ORDER BY id"):
            if store.find_document("import", d["source"]) is not None:
                continue                          # already imported
            doc_id = store.add_document(d["title"] or d["source"], d["trade"],
                                        d["source"], "import")
            counts["docs"] += 1
            for c in src.execute(
                    "SELECT ord, heading, content FROM kb_chunks "
                    "WHERE document_id = ? ORDER BY ord, id", (d["id"],)):
                text = ((c["heading"] + "\n") if c["heading"] else "") + c["content"]
                store.add_chunk(doc_id, int(c["ord"]), text)
                counts["chunks"] += 1

        # Human-approved thesaurus rows ride along (already reviewed there).
        if "journeyman_thesaurus_proposed" in tables:
            known = {(thesaurus.norm(r["term"]), thesaurus.norm(r["canonical"]))
                     for r in store.thesaurus_rows()}
            for r in src.execute(
                    "SELECT term, canonical FROM journeyman_thesaurus_proposed "
                    "WHERE status = 'approved' ORDER BY id"):
                key = (thesaurus.norm(r["term"]), thesaurus.norm(r["canonical"]))
                if key in known:
                    continue
                known.add(key)
                store.add_thesaurus(r["term"], r["canonical"], None, "approved")
                counts["thesaurus"] += 1
    except sqlite3.Error as e:
        counts["error"] = f"knowledge-base read failed: {e}"
        return counts
    finally:
        src.close()

    if counts["docs"] or counts["chunks"]:
        rebuild(store)
    return counts


# ------------------------------------------------- notes (lane 2, gated) ----

def add_note(store, text: str, author: str = "", origin: str = "note") -> int:
    """File a note in the self-learning lane: stored UNVERIFIED and indexed
    immediately under its origin label (answers will flag it until a human
    trusts it).  Returns the note id."""
    text = ("" if text is None else str(text)).strip()
    if not text:
        raise ValueError("empty note")
    note_id = store.add_note(text, author, origin)
    title = text.splitlines()[0][:80]
    _add_doc_with_chunks(store, title, text, None, f"note:{note_id}", origin)
    return note_id


def trust_note(store, note_id: int) -> bool:
    """Human gate: promote a note to trusted.  Its chunks stay indexed and
    stop carrying the unverified flag."""
    row = store.note(note_id)
    if row is None or row["status"] == "rejected":
        return False
    return store.set_note_status(note_id, "trusted")


def reject_note(store, note_id: int) -> bool:
    """Human gate: reject a note — de-indexed entirely (chunks, postings and
    vectors gone), the note row kept as a record."""
    row = store.note(note_id)
    if row is None:
        return False
    doc = store.note_document(note_id)
    if doc is not None:
        store.delete_document(int(doc["id"]))
    return store.set_note_status(note_id, "rejected")


def _rec_get(rec, key: str, alt: str | None = None) -> str:
    """Field from an RFI record — dataclass attribute or dict key."""
    if isinstance(rec, dict):
        v = rec.get(key, rec.get(alt) if alt else None)
    else:
        v = getattr(rec, key, getattr(rec, alt, None) if alt else None)
    return "" if v is None else str(v)


def capture_rfis(store, records) -> dict:
    """Lane-2 capture from Planloom's own workflow: one unverified note per
    ANSWERED RFI record (rfi_stamper.core record objects or equivalent dicts
    with number/title/question/answer).  Unanswered records are skipped;
    duplicates are deduped by (origin, first 120 chars).
    Returns {'captured': n, 'skipped': m}."""
    captured = skipped = 0
    for rec in records or []:
        number = _rec_get(rec, "number")
        subject = _rec_get(rec, "title", "subject")
        question = _rec_get(rec, "question").strip()
        answer = _rec_get(rec, "answer").strip()
        answered = getattr(rec, "has_answer", None)
        if answered is None:                     # dicts: core's own rule
            answered = len(re.sub(r"\s", "", answer)) >= 25
        if not answered:
            skipped += 1
            continue
        text = (f"RFI {number} — {subject}\n"
                f"Q: {question}\nA: {answer}")
        if store.find_note("rfi", text[:120]) is not None:
            skipped += 1
            continue
        add_note(store, text, author=_rec_get(rec, "source"), origin="rfi")
        captured += 1
    return {"captured": captured, "skipped": skipped}


# -------------------------------------------------------------- rebuild -----

def rebuild(store, log=None) -> dict:
    """Retrain the whole meaning layer on the current store: postings
    reindexed, vectors retrained, thesaurus miner re-run.  Idempotent, safe
    to call any time, fast enough to call after every ingest."""
    reindexed = store.reindex_postings()
    trained = vectors.train(store, log)
    mined = thesaurus.mine(store)
    from . import corral                    # lazy: the maintenance arm
    corral.record_growth(store)             # the Ground Truth growth series
    return dict(trained, reindexed=reindexed, mined=mined["proposed"],
                scanned=mined["scanned"])
