"""Heartwood corral — caps, compaction, provenance and the carry file.

The Corral standing rule (ROADMAP): the brain may GROW — from uploads,
typed teachings, question phrasing and Planloom's own work — but never
unbounded and never untraceably.  This module is the fence work:

* :data:`LIMITS` — the store caps, every one overridable per call.  Hard
  caps are enforced by :func:`compact` (feedback log, vector vocabulary,
  chunks per document); soft caps only WARN (total store size,
  unverified-note queue) because deleting content is always a human's
  call.  The vocabulary cap is additionally enforced at LOAD time in
  vectors.load(), so even a hand-edited store cannot balloon memory.
* :func:`compact` — cap pruning + orphan sweep + in-document dedupe +
  VACUUM.  Every content mutation runs in ONE transaction (atomic),
  the whole job is idempotent, and it NEVER touches note content —
  trusted or unverified: the self-learning lane is judged by a human in
  the Manage screen, not by a janitor.
* :func:`provenance` / :func:`purge` — where every learned item came
  from, and one-call removal including its index entries.  Shipped
  thesaurus seeds are DISABLED (status 'rejected'), never deleted: the
  kept row is the tombstone that stops ensure_seed() from resurrecting
  the entry on the next open.
* :func:`snapshot` / :func:`restore` — the learned state as ONE JSON
  carry file (format ``planloom-heartwood-learning``, version 1) for
  hand-carrying learning between machines, fully offline.  The bundle
  holds human-APPROVED thesaurus rows, trusted + unverified notes WITH
  their statuses, and the feedback log with each row's chunk re-matched
  by content head on restore (chunk ids never cross machines).  A JSON
  bundle was chosen over a raw SQLite backup on purpose: the carry file
  holds ONLY the learned state — no bulk documents, no vectors — and
  restore merges it into a live store without ever promoting anything a
  human did not already approve (unverified stays unverified; pending
  proposals do not travel at all).
* :func:`gauges` / :func:`record_growth` — the Ground Truth numbers:
  size, counts, queue depths, 7-day activity and the chunk-count growth
  series (the last :data:`GROWTH_KEEP` snapshots, appended in hw_meta on
  every rebuild and every compact).

Uniquely among the heartwood modules the corral speaks SQL: it is the
store's maintenance arm, and schema maintenance is SQL work.  Everything
here is offline, deterministic and GUI-free.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from . import thesaurus

#: The store caps.  Defaults; every entry may be overridden per call via
#: the ``limits=`` argument.  store_mb and unverified_notes are SOFT caps:
#: they warn, they never delete.
LIMITS = {
    "feedback_rows": 20_000,     # hw_feedback rows kept (oldest pruned)
    "vocab": 60_000,             # hw_vectors rows kept, by df rank
    "chunks_per_doc": 2_000,     # chunks kept per document, by seq order
    "store_mb": 512.0,           # soft: total store size (warn only)
    "unverified_notes": 500,     # soft: unverified-note queue (warn only)
}

SNAPSHOT_FORMAT = "planloom-heartwood-learning"
SNAPSHOT_VERSION = 1

GROWTH_KEY = "growth_series"     # hw_meta key holding the growth snapshots
GROWTH_KEEP = 8                  # snapshots kept (the sparkline's width)

_TABLES = ("hw_documents", "hw_chunks", "hw_postings", "hw_vectors",
           "hw_chunk_vecs", "hw_thesaurus", "hw_notes", "hw_feedback")

#: WHERE fragment keeping compaction OFF the self-learning lane: note-backed
#: documents (source 'note:<id>') carry human-judged content and are never
#: deduped or trimmed here.
_NOT_NOTE_DOC = "(d.source IS NULL OR d.source NOT LIKE 'note:%')"

_SQL_BATCH = 500                 # ids per DELETE ... IN (...) statement


def _limits(overrides: dict | None) -> dict:
    lim = dict(LIMITS)
    lim.update(overrides or {})
    return lim


def db_size_mb(store) -> float:
    """The store file's size in MB (0.0 for :memory: stores)."""
    try:
        if store.path and store.path != ":memory:" \
                and os.path.isfile(store.path):
            return os.path.getsize(store.path) / (1024.0 * 1024.0)
    except OSError:
        pass
    return 0.0


def table_counts(store) -> dict:
    """Row count per hw_* table — the before/after axis of a report."""
    return {t: int(store.db.execute(f"SELECT COUNT(*) FROM {t}")
                   .fetchone()[0]) for t in _TABLES}


def _warnings(store, lim: dict) -> list[str]:
    """Soft-cap warnings (warn only — nothing here ever deletes)."""
    out = []
    size = db_size_mb(store)
    if size > float(lim["store_mb"]):
        out.append(f"store is {size:.1f} MB — past the "
                   f"{float(lim['store_mb']):.0f} MB soft cap; compact, or "
                   "purge documents you no longer need")
    queue = int(store.db.execute(
        "SELECT COUNT(*) FROM hw_notes WHERE status = 'unverified'"
    ).fetchone()[0])
    if queue > int(lim["unverified_notes"]):
        out.append(f"{queue} unverified note(s) await judgement (cap "
                   f"{int(lim['unverified_notes'])}) — review them in the "
                   "Old Hand's Manage screen")
    return out


def _drop_chunks(db, ids: list[int]) -> None:
    """Delete chunks AND their index entries (postings, chunk vectors),
    batched under the SQL variable limit.  Caller holds the transaction."""
    for i in range(0, len(ids), _SQL_BATCH):
        batch = ids[i:i + _SQL_BATCH]
        marks = ",".join("?" * len(batch))
        db.execute(f"DELETE FROM hw_postings WHERE chunk_id IN ({marks})",
                   batch)
        db.execute(f"DELETE FROM hw_chunk_vecs WHERE chunk_id IN ({marks})",
                   batch)
        db.execute(f"DELETE FROM hw_chunks WHERE id IN ({marks})", batch)


# ------------------------------------------------------------- compaction --

def compact(store, limits: dict | None = None) -> dict:
    """Prune caps, sweep orphans, dedupe, VACUUM.  Returns a report dict::

        {tables: {hw_*: {before, after}}, feedback_pruned, vectors_pruned,
         orphans_dropped: {chunks, postings, chunk_vecs}, chunks_deduped,
         chunks_trimmed, vacuumed, db_size_mb: {before, after}, warnings}

    Idempotent (a second run reports zeros) and atomic: every content
    mutation happens inside one transaction; VACUUM runs after commit (it
    only reclaims space and cannot lose data).  Note content — trusted OR
    unverified — is never touched: note rows are read-only here and
    note-backed documents are excluded from dedupe and trimming."""
    lim = _limits(limits)
    db = store.db
    before = table_counts(store)
    size_before = db_size_mb(store)
    orphans = {"chunks": 0, "postings": 0, "chunk_vecs": 0}
    deduped = trimmed = fb_pruned = vec_pruned = 0

    with db:
        # 1. orphaned chunks: parent document gone (foreign stores / old
        #    connections without foreign_keys=ON can leave these behind)
        ids = [int(r[0]) for r in db.execute(
            "SELECT c.id FROM hw_chunks c "
            "LEFT JOIN hw_documents d ON d.id = c.doc_id "
            "WHERE d.id IS NULL")]
        _drop_chunks(db, ids)
        orphans["chunks"] = len(ids)

        # 2. orphaned postings / chunk vectors: parent chunk gone
        cur = db.execute("DELETE FROM hw_postings WHERE chunk_id NOT IN "
                         "(SELECT id FROM hw_chunks)")
        orphans["postings"] = max(0, cur.rowcount)
        cur = db.execute("DELETE FROM hw_chunk_vecs WHERE chunk_id NOT IN "
                         "(SELECT id FROM hw_chunks)")
        orphans["chunk_vecs"] = max(0, cur.rowcount)

        # 3. dedupe identical chunks WITHIN a document (keep the first by
        #    id).  Cross-document repeats are kept — two specs quoting the
        #    same sentence are two citations.  Note docs excluded.
        ids = [int(r[0]) for r in db.execute(
            "SELECT c.id FROM hw_chunks c "
            "JOIN hw_documents d ON d.id = c.doc_id "
            f"WHERE {_NOT_NOTE_DOC} AND EXISTS ("
            "  SELECT 1 FROM hw_chunks c2 WHERE c2.doc_id = c.doc_id "
            "    AND c2.text = c.text AND c2.id < c.id)")]
        _drop_chunks(db, ids)
        deduped = len(ids)

        # 4. per-document chunk cap: keep the first chunks_per_doc by
        #    (seq, id).  Note docs excluded.
        cap = int(lim["chunks_per_doc"])
        ids = [int(r[0]) for r in db.execute(
            "SELECT c.id FROM hw_chunks c "
            "JOIN hw_documents d ON d.id = c.doc_id "
            f"WHERE {_NOT_NOTE_DOC} AND ("
            "  SELECT COUNT(*) FROM hw_chunks c2 WHERE c2.doc_id = c.doc_id "
            "    AND (c2.seq < c.seq OR (c2.seq = c.seq AND c2.id < c.id))"
            ") >= ?", (cap,))]
        _drop_chunks(db, ids)
        trimmed = len(ids)

        # 5. feedback log cap: keep the NEWEST rows (ids are monotonic)
        cap = int(lim["feedback_rows"])
        n_fb = int(db.execute("SELECT COUNT(*) FROM hw_feedback")
                   .fetchone()[0])
        if n_fb > cap:
            db.execute("DELETE FROM hw_feedback WHERE id NOT IN "
                       "(SELECT id FROM hw_feedback ORDER BY id DESC "
                       " LIMIT ?)", (cap,))
            fb_pruned = n_fb - cap

        # 6. vector vocabulary cap: keep the top df ranks, ties lexical —
        #    exactly the train() cut line
        cap = int(lim["vocab"])
        n_vec = int(db.execute("SELECT COUNT(*) FROM hw_vectors")
                    .fetchone()[0])
        if n_vec > cap:
            db.execute("DELETE FROM hw_vectors WHERE term NOT IN "
                       "(SELECT term FROM hw_vectors "
                       " ORDER BY df DESC, term ASC LIMIT ?)", (cap,))
            vec_pruned = n_vec - cap

    db.execute("VACUUM")                    # not transactional: space only
    store._vec_model = None                 # the in-memory model may be stale
    record_growth(store)

    return {
        "tables": {t: {"before": before[t], "after": a}
                   for t, a in table_counts(store).items()},
        "feedback_pruned": fb_pruned,
        "vectors_pruned": vec_pruned,
        "orphans_dropped": orphans,
        "chunks_deduped": deduped,
        "chunks_trimmed": trimmed,
        "vacuumed": True,
        "db_size_mb": {"before": size_before, "after": db_size_mb(store)},
        "warnings": _warnings(store, lim),
    }


# ------------------------------------------------------------- provenance --

def provenance(store) -> list[dict]:
    """Every learned item with its origin, shaped for a tree view.  Each::

        {kind: 'thesaurus'|'note'|'document'|'feedback', id, label,
         origin, status, deletable}

    ``deletable`` is False only for shipped thesaurus seeds — those can be
    disabled through :func:`purge` but never removed.  Note-backed
    documents are represented by their note (not listed twice)."""
    items: list[dict] = []
    for r in store.thesaurus_rows():
        if r["source_chunk"]:
            chunk = store.chunk(int(r["source_chunk"]))
            origin = (f"mined: {chunk['title']} §{int(r['source_chunk'])}"
                      if chunk else f"mined: §{int(r['source_chunk'])}")
        elif r["status"] == "seed":
            origin = "shipped seed"
        else:
            origin = "taught / imported"
        items.append({"kind": "thesaurus", "id": int(r["id"]),
                      "label": f"{r['term']} = {r['canonical']}",
                      "origin": origin, "status": str(r["status"]),
                      "deletable": r["status"] != "seed"})
    for n in store.notes():
        head = str(n["text"]).splitlines()[0][:80]
        origin = str(n["origin"])
        if n["author"]:
            origin += f" · {n['author']}"
        if n["created"]:
            origin += f" · {n['created']}"
        items.append({"kind": "note", "id": int(n["id"]), "label": head,
                      "origin": origin, "status": str(n["status"]),
                      "deletable": True})
    for d in store.db.execute(
            "SELECT d.id, d.title, d.origin, d.source, d.added, "
            "  (SELECT COUNT(*) FROM hw_chunks c WHERE c.doc_id = d.id) AS n "
            "FROM hw_documents d "
            f"WHERE {_NOT_NOTE_DOC} ORDER BY d.id"):
        origin = str(d["origin"])
        if d["source"]:
            origin += f": {d['source']}"
        items.append({"kind": "document", "id": int(d["id"]),
                      "label": str(d["title"]), "origin": origin,
                      "status": f"{int(d['n'])} passage(s)",
                      "deletable": True})
    for f in store.db.execute(
            "SELECT query, SUM(kind = 'shown') AS shown, "
            "  SUM(kind = 'used') AS used, COUNT(*) AS n "
            "FROM hw_feedback GROUP BY query ORDER BY MIN(id)"):
        items.append({"kind": "feedback", "id": str(f["query"]),
                      "label": str(f["query"])[:80],
                      "origin": (f"{int(f['shown'] or 0)} shown · "
                                 f"{int(f['used'] or 0)} used"),
                      "status": f"{int(f['n'])} row(s)",
                      "deletable": True})
    return items


def purge(store, kind: str, ident) -> bool:
    """Remove ONE learned item including its index entries.  ``kind`` is a
    provenance kind; ``ident`` its id (the query string for feedback).

    Thesaurus SEEDS are disabled, not deleted: the row flips to status
    'rejected' (out of every live path) and stays on file so ensure_seed()
    cannot re-add the pair on the next open.  Everything else is removed
    outright — a note purge also drops its indexed document, chunks,
    postings and chunk vectors.  Returns False on anything unknown."""
    db = store.db
    kind = str(kind)
    if kind == "thesaurus":
        try:
            row_id = int(ident)
        except (TypeError, ValueError):
            return False
        row = db.execute("SELECT status FROM hw_thesaurus WHERE id = ?",
                         (row_id,)).fetchone()
        if row is None:
            return False
        if row["status"] == "seed":
            with db:
                cur = db.execute(
                    "UPDATE hw_thesaurus SET status = 'rejected' "
                    "WHERE id = ? AND status = 'seed'", (row_id,))
            return cur.rowcount > 0
        with db:
            cur = db.execute("DELETE FROM hw_thesaurus WHERE id = ?",
                             (row_id,))
        return cur.rowcount > 0
    if kind == "note":
        try:
            note_id = int(ident)
        except (TypeError, ValueError):
            return False
        if store.note(note_id) is None:
            return False
        doc = store.note_document(note_id)
        if doc is not None:
            store.delete_document(int(doc["id"]))
        with db:
            cur = db.execute("DELETE FROM hw_notes WHERE id = ?", (note_id,))
        return cur.rowcount > 0
    if kind == "document":
        try:
            doc_id = int(ident)
        except (TypeError, ValueError):
            return False
        if store.document(doc_id) is None:
            return False
        store.delete_document(doc_id)
        return True
    if kind == "feedback":
        with db:
            cur = db.execute("DELETE FROM hw_feedback WHERE query = ?",
                             (str(ident),))
        return cur.rowcount > 0
    return False


# --------------------------------------------------------- the carry file --

def snapshot(store, out_path: str) -> dict:
    """Export the learned state to ONE file (atomic: temp + replace).

    Carried: human-approved thesaurus rows, trusted + unverified notes
    with their statuses, and the feedback log (each chunk-backed row keyed
    by the first 120 chars of its chunk so restore can re-match it —
    chunk ids never cross machines; chunk_id 0 phrase records travel
    as-is).  NOT carried: seeds (they ship with the product), rejected
    anything, pending proposals, documents, vectors."""
    thes = [{"term": r["term"], "canonical": r["canonical"],
             "trade": r["trade"]}
            for r in store.thesaurus_rows(("approved",))]
    notes = [{"text": n["text"], "author": n["author"] or "",
              "created": n["created"], "origin": n["origin"],
              "status": n["status"]}
             for n in store.notes()
             if n["status"] in ("trusted", "unverified")]
    feedback = []
    for r in store.db.execute(
            "SELECT query, chunk_id, kind, ts FROM hw_feedback ORDER BY id"):
        head = None
        if int(r["chunk_id"]):
            chunk = store.chunk(int(r["chunk_id"]))
            if chunk is None:
                continue                     # orphan row: not portable
            head = str(chunk["text"])[:120]
        feedback.append({"query": r["query"], "kind": r["kind"],
                         "ts": r["ts"], "chunk_head": head})
    bundle = {
        "format": SNAPSHOT_FORMAT,
        "version": SNAPSHOT_VERSION,
        "created": datetime.now(timezone.utc).isoformat(),
        "thesaurus": thes,
        "notes": notes,
        "feedback": feedback,
    }
    tmp = str(out_path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=1)
    os.replace(tmp, out_path)
    return {"path": str(out_path), "thesaurus": len(thes),
            "notes": len(notes), "feedback": len(feedback), "error": None}


def restore(store, path: str) -> dict:
    """Merge a carry file into this store.  Never raises on a bad file —
    returns counts with an 'error' string instead (the import_tradeforge
    convention).  The gate rules hold end to end:

    * approved thesaurus rows land approved (a human approved them on the
      source machine); pairs already live here are skipped, and existing
      local rows are NEVER promoted or modified;
    * notes keep their exact status — trusted stays trusted, unverified
      stays unverified, nothing is promoted; duplicates (by origin + text
      head) are skipped;
    * feedback rows re-match their chunk by content head; rows whose
      chunk is absent here, and exact duplicates, are skipped.

    Rebuilds the meaning layer when anything landed."""
    out = {"thesaurus_added": 0, "notes_added": 0, "notes_skipped": 0,
           "feedback_added": 0, "feedback_skipped": 0, "error": None}
    if not os.path.isfile(path):
        out["error"] = f"learning snapshot not found: {path}"
        return out
    try:
        with open(path, encoding="utf-8") as f:
            bundle = json.load(f)
    except (OSError, ValueError) as e:
        out["error"] = f"cannot read learning snapshot: {e}"
        return out
    if not isinstance(bundle, dict) or bundle.get("format") != SNAPSHOT_FORMAT:
        out["error"] = ("not a Planloom learning snapshot (missing format "
                        f"marker) — {os.path.basename(str(path))}")
        return out

    from . import ingest             # lazy: ingest calls back for growth

    live = {(thesaurus.norm(r["term"]), thesaurus.norm(r["canonical"]))
            for r in store.thesaurus_rows(("seed", "approved"))}
    for e in bundle.get("thesaurus") or []:
        if not isinstance(e, dict):
            continue
        term = str(e.get("term") or "").strip()
        canon = str(e.get("canonical") or "").strip()
        if not term or not canon:
            continue
        key = (thesaurus.norm(term), thesaurus.norm(canon))
        if key in live:
            continue
        live.add(key)
        store.add_thesaurus(term, canon, e.get("trade"), "approved")
        out["thesaurus_added"] += 1

    for e in bundle.get("notes") or []:
        if not isinstance(e, dict):
            continue
        text = str(e.get("text") or "").strip()
        status = str(e.get("status") or "unverified")
        if not text or status not in ("trusted", "unverified"):
            out["notes_skipped"] += 1
            continue
        origin = str(e.get("origin") or "note")
        if store.find_note(origin, text[:120]) is not None:
            out["notes_skipped"] += 1
            continue
        note_id = ingest.add_note(store, text,
                                  str(e.get("author") or ""), origin)
        if status == "trusted":              # the human trusted it already
            store.set_note_status(note_id, "trusted")
        if e.get("created"):                 # keep the provenance timestamp
            with store.db:
                store.db.execute("UPDATE hw_notes SET created = ? "
                                 "WHERE id = ?",
                                 (str(e["created"]), note_id))
        out["notes_added"] += 1

    # feedback re-match AFTER notes land, so note chunks are matchable too
    heads: dict[str, int] = {}
    for cid, text in store.iter_chunks():
        heads.setdefault(str(text)[:120], cid)
    for e in bundle.get("feedback") or []:
        if not isinstance(e, dict):
            continue
        query = str(e.get("query") or "").strip()
        kind = e.get("kind")
        head = e.get("chunk_head")
        if not query or kind not in ("shown", "used"):
            out["feedback_skipped"] += 1
            continue
        if head is None:
            cid = 0                          # phrase record (no chunk)
        elif head in heads:
            cid = heads[head]
        else:
            out["feedback_skipped"] += 1     # its chunk lives elsewhere
            continue
        ts = str(e.get("ts") or "") or None
        dup = store.db.execute(
            "SELECT 1 FROM hw_feedback WHERE query = ? AND chunk_id = ? "
            "AND kind = ? AND ts IS ?", (query, cid, kind, ts)).fetchone()
        if dup is not None:
            out["feedback_skipped"] += 1
            continue
        with store.db:
            if ts is None:
                store.db.execute(
                    "INSERT INTO hw_feedback(query, chunk_id, kind) "
                    "VALUES (?, ?, ?)", (query, cid, kind))
            else:
                store.db.execute(
                    "INSERT INTO hw_feedback(query, chunk_id, kind, ts) "
                    "VALUES (?, ?, ?, ?)", (query, cid, kind, ts))
        out["feedback_added"] += 1

    if out["notes_added"] or out["thesaurus_added"]:
        out["rebuilt"] = ingest.rebuild(store)
    return out


# ------------------------------------------------------------- the gauges --

def record_growth(store) -> list[int]:
    """Append a chunk-count snapshot to the growth series in hw_meta
    (called on every rebuild and every compact); keeps the last
    :data:`GROWTH_KEEP`.  Returns the series as counts."""
    n = int(store.db.execute("SELECT COUNT(*) FROM hw_chunks").fetchone()[0])
    try:
        series = json.loads(store.get_meta(GROWTH_KEY) or "[]")
    except ValueError:
        series = []
    series = [s for s in series if isinstance(s, dict)]
    series.append({"ts": datetime.now(timezone.utc).isoformat(),
                   "chunks": n})
    series = series[-GROWTH_KEEP:]
    store.set_meta(GROWTH_KEY, json.dumps(series))
    return [int(s.get("chunks", 0)) for s in series]


def gauges(store) -> dict:
    """The Ground Truth numbers, one dict::

        {db_size_mb, docs, chunks, vocab,
         notes: {unverified, trusted, rejected}, proposals_pending,
         feedback_rows, asks_7d, uses_7d, growth: [chunk counts...],
         warnings: [soft-cap strings]}

    asks_7d counts DISTINCT questions shown an answer in the last 7 days;
    uses_7d counts click-to-reinforce marks in the same window.  growth is
    the chunk-count series :func:`record_growth` maintains."""
    c = store.counts()
    db = store.db
    proposals = int(db.execute(
        "SELECT COUNT(*) FROM hw_thesaurus WHERE status = 'unverified'"
    ).fetchone()[0])
    asks = int(db.execute(
        "SELECT COUNT(DISTINCT query) FROM hw_feedback WHERE kind = 'shown' "
        "AND ts >= datetime('now', '-7 days')").fetchone()[0])
    uses = int(db.execute(
        "SELECT COUNT(*) FROM hw_feedback WHERE kind = 'used' "
        "AND ts >= datetime('now', '-7 days')").fetchone()[0])
    try:
        series = json.loads(store.get_meta(GROWTH_KEY) or "[]")
    except ValueError:
        series = []
    growth = [int(s.get("chunks", 0)) for s in series if isinstance(s, dict)]
    return {
        "db_size_mb": db_size_mb(store),
        "docs": c["documents"],
        "chunks": c["chunks"],
        "vocab": int(store.get_meta("vocab") or 0),
        "notes": {"unverified": c["notes_unverified"],
                  "trusted": c["notes_trusted"],
                  "rejected": c["notes_rejected"]},
        "proposals_pending": proposals,
        "feedback_rows": c["feedback"],
        "asks_7d": asks,
        "uses_7d": uses,
        "growth": growth,
        "warnings": _warnings(store, LIMITS),
    }
