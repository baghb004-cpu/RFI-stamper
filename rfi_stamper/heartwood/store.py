"""Heartwood store — one SQLite file, stdlib sqlite3 only.

The dense center wood of the tree: everything Heartwood knows lives in this
single database file (default ``~/.planloom/heartwood.db``).  Documents are
split into chunks; chunks are indexed two ways — a pure-Python BM25 posting
list (hw_postings; no FTS5, for portability across sqlite builds) and the
Random-Indexing vectors trained by vectors.py (hw_vectors/hw_chunk_vecs).
Notes (the human-gated self-learning lane) and thesaurus rows carry an
explicit status so nothing unverified can masquerade as trusted content.

All writes go through transactions; the db file is the only artifact, atomic
enough via sqlite journaling.  Schema creation is CREATE IF NOT EXISTS —
opening an existing store is idempotent.
"""
from __future__ import annotations

import math
import os
import re
import sqlite3

from . import lex

BM25_K1 = 1.5
BM25_B = 0.75

# Origins whose documents came from the self-learning note lane: their chunks
# carry the note's status so answers can flag them "shop note — unverified".
NOTE_ORIGINS = ("rfi", "daybook", "note")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hw_documents (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    title  TEXT NOT NULL,
    trade  TEXT,
    source TEXT,
    origin TEXT NOT NULL DEFAULT 'text',
    added  TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS hw_chunks (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES hw_documents(id) ON DELETE CASCADE,
    seq    INTEGER NOT NULL,
    text   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hw_chunks_doc ON hw_chunks(doc_id);
CREATE TABLE IF NOT EXISTS hw_postings (
    term     TEXT NOT NULL,
    chunk_id INTEGER NOT NULL REFERENCES hw_chunks(id) ON DELETE CASCADE,
    tf       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hw_postings_term ON hw_postings(term);
CREATE INDEX IF NOT EXISTS idx_hw_postings_chunk ON hw_postings(chunk_id);
CREATE TABLE IF NOT EXISTS hw_vectors (
    term TEXT PRIMARY KEY,
    df   INTEGER NOT NULL DEFAULT 0,
    vec  BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS hw_chunk_vecs (
    chunk_id INTEGER PRIMARY KEY REFERENCES hw_chunks(id) ON DELETE CASCADE,
    vec      BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS hw_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS hw_thesaurus (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    term         TEXT NOT NULL,
    canonical    TEXT NOT NULL,
    trade        TEXT,
    status       TEXT NOT NULL DEFAULT 'unverified'
                 CHECK (status IN ('seed','unverified','approved','rejected')),
    source_chunk INTEGER REFERENCES hw_chunks(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_hw_thesaurus_status ON hw_thesaurus(status);
CREATE TABLE IF NOT EXISTS hw_notes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    text    TEXT NOT NULL,
    author  TEXT DEFAULT '',
    created TEXT DEFAULT (datetime('now')),
    origin  TEXT NOT NULL DEFAULT 'note',
    status  TEXT NOT NULL DEFAULT 'unverified'
            CHECK (status IN ('unverified','trusted','rejected'))
);
CREATE TABLE IF NOT EXISTS hw_feedback (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    query    TEXT NOT NULL,
    chunk_id INTEGER NOT NULL,
    kind     TEXT NOT NULL CHECK (kind IN ('shown','used')),
    ts       TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_hw_feedback_chunk ON hw_feedback(chunk_id);
"""


class HeartwoodStore:
    """The one-file knowledge store.  Owns the sqlite connection and every
    query; the engine modules never touch SQL directly."""

    def __init__(self, path: str):
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        if parent and path != ":memory:":
            os.makedirs(parent, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        with self.db:
            self.db.executescript(_SCHEMA)
        self._vec_model = None      # cache slot used by vectors.py

    def close(self) -> None:
        self.db.close()
        self._vec_model = None

    # ------------------------------------------------------- documents ------

    def add_document(self, title: str, trade: str | None = None,
                     source: str | None = None, origin: str = "text") -> int:
        with self.db:
            cur = self.db.execute(
                "INSERT INTO hw_documents(title, trade, source, origin) "
                "VALUES (?, ?, ?, ?)", (title, trade, source, origin))
        return int(cur.lastrowid)

    def find_document(self, origin: str, source: str):
        row = self.db.execute(
            "SELECT id FROM hw_documents WHERE origin = ? AND source = ?",
            (origin, source)).fetchone()
        return int(row["id"]) if row else None

    def delete_document(self, doc_id: int) -> None:
        """Remove a document and everything derived from its chunks."""
        with self.db:
            self.db.execute(
                "DELETE FROM hw_postings WHERE chunk_id IN "
                "(SELECT id FROM hw_chunks WHERE doc_id = ?)", (doc_id,))
            self.db.execute(
                "DELETE FROM hw_chunk_vecs WHERE chunk_id IN "
                "(SELECT id FROM hw_chunks WHERE doc_id = ?)", (doc_id,))
            self.db.execute("DELETE FROM hw_chunks WHERE doc_id = ?", (doc_id,))
            self.db.execute("DELETE FROM hw_documents WHERE id = ?", (doc_id,))

    def document(self, doc_id: int):
        return self.db.execute(
            "SELECT * FROM hw_documents WHERE id = ?", (doc_id,)).fetchone()

    # ---------------------------------------------------------- chunks ------

    def add_chunk(self, doc_id: int, seq: int, text: str) -> int:
        """Insert a chunk and index its postings in the same transaction, so
        BM25 search sees new content immediately (vectors wait for rebuild)."""
        with self.db:
            cur = self.db.execute(
                "INSERT INTO hw_chunks(doc_id, seq, text) VALUES (?, ?, ?)",
                (doc_id, seq, text))
            cid = int(cur.lastrowid)
            self._index_postings(cid, text)
        return cid

    def _index_postings(self, chunk_id: int, text: str) -> None:
        tf: dict[str, int] = {}
        for t in lex.terms(text):
            tf[t.t] = tf.get(t.t, 0) + 1
        self.db.executemany(
            "INSERT INTO hw_postings(term, chunk_id, tf) VALUES (?, ?, ?)",
            [(term, chunk_id, f) for term, f in tf.items()])

    def reindex_postings(self) -> int:
        """Rebuild the whole posting list from hw_chunks (rebuild path)."""
        n = 0
        with self.db:
            self.db.execute("DELETE FROM hw_postings")
            for cid, text in self.iter_chunks():
                self._index_postings(cid, text)
                n += 1
        return n

    def iter_chunks(self):
        """Stream (chunk_id, text) in id order — the training corpus."""
        for row in self.db.execute(
                "SELECT id, text FROM hw_chunks ORDER BY id"):
            yield int(row["id"]), row["text"]

    def chunk(self, chunk_id: int):
        """One chunk joined with its document (title/trade/origin/source) and
        the backing note's status when the document came from the note lane."""
        return self.db.execute(
            """SELECT c.id, c.doc_id, c.seq, c.text,
                      d.title, d.trade, d.origin, d.source,
                      n.status AS note_status
                 FROM hw_chunks c
                 JOIN hw_documents d ON d.id = c.doc_id
                 LEFT JOIN hw_notes n
                        ON d.source = 'note:' || n.id
                       AND d.origin IN ('rfi','daybook','note')
                WHERE c.id = ?""", (chunk_id,)).fetchone()

    @staticmethod
    def chunk_unverified(row) -> bool:
        """Is this chunk row backed by a not-yet-trusted note?"""
        return (row is not None and row["origin"] in NOTE_ORIGINS
                and (row["note_status"] or "unverified") != "trusted")

    # ------------------------------------------------------------ BM25 ------

    def bm25(self, terms: list[str], limit: int = 200,
             trade: str | None = None,
             include_unverified: bool = True) -> list[tuple[int, float]]:
        """Pure-Python Okapi BM25 (k1=1.5, b=0.75) over hw_postings.

        Returns [(chunk_id, score)] best-first (ties by chunk id).  ``trade``
        keeps chunks of that trade plus trade-neutral documents;
        ``include_unverified=False`` drops chunks backed by unverified notes.
        """
        terms = sorted(set(t for t in terms if t))
        if not terms:
            return []
        n_row = self.db.execute("SELECT COUNT(*) AS n FROM hw_chunks").fetchone()
        n_chunks = int(n_row["n"])
        if not n_chunks:
            return []
        tot = self.db.execute(
            "SELECT COALESCE(SUM(tf), 0) AS s FROM hw_postings").fetchone()
        avgdl = (int(tot["s"]) / n_chunks) or 1.0

        marks = ",".join("?" * len(terms))
        df: dict[str, int] = {}
        for row in self.db.execute(
                f"SELECT term, COUNT(*) AS df FROM hw_postings "
                f"WHERE term IN ({marks}) GROUP BY term", terms):
            df[row["term"]] = int(row["df"])
        if not df:
            return []

        # candidate postings + per-chunk length + doc filters, one pass
        scores: dict[int, float] = {}
        rows = self.db.execute(
            f"""SELECT p.term, p.chunk_id, p.tf,
                       (SELECT SUM(tf) FROM hw_postings q
                         WHERE q.chunk_id = p.chunk_id) AS dl,
                       d.trade, d.origin, d.source,
                       n.status AS note_status
                  FROM hw_postings p
                  JOIN hw_chunks c ON c.id = p.chunk_id
                  JOIN hw_documents d ON d.id = c.doc_id
                  LEFT JOIN hw_notes n
                         ON d.source = 'note:' || n.id
                        AND d.origin IN ('rfi','daybook','note')
                 WHERE p.term IN ({marks})""", terms).fetchall()
        for r in rows:
            if trade and (r["trade"] or "general") not in (trade, "general", ""):
                continue
            if not include_unverified and self.chunk_unverified(r):
                continue
            term_df = df.get(r["term"], 0)
            idf = math.log(1.0 + (n_chunks - term_df + 0.5) / (term_df + 0.5))
            tf = int(r["tf"])
            dl = int(r["dl"] or 0)
            denom = tf + BM25_K1 * (1.0 - BM25_B + BM25_B * dl / avgdl)
            cid = int(r["chunk_id"])
            scores[cid] = scores.get(cid, 0.0) + idf * tf * (BM25_K1 + 1.0) / denom
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return ranked[:limit]

    # ---------------------------------------------------------- vectors -----

    def save_vectors(self, term_rows, chunk_rows, meta: dict) -> None:
        """Persist a full retrain atomically (delete + insert + meta)."""
        with self.db:
            self.db.execute("DELETE FROM hw_vectors")
            self.db.execute("DELETE FROM hw_chunk_vecs")
            self.db.executemany(
                "INSERT INTO hw_vectors(term, df, vec) VALUES (?, ?, ?)",
                term_rows)
            self.db.executemany(
                "INSERT INTO hw_chunk_vecs(chunk_id, vec) VALUES (?, ?)",
                chunk_rows)
            self.db.executemany(
                "INSERT INTO hw_meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                list(meta.items()))

    def iter_vectors(self):
        for row in self.db.execute("SELECT term, df, vec FROM hw_vectors"):
            yield row["term"], int(row["df"]), row["vec"]

    def get_chunk_vec(self, chunk_id: int):
        row = self.db.execute(
            "SELECT vec FROM hw_chunk_vecs WHERE chunk_id = ?",
            (chunk_id,)).fetchone()
        return row["vec"] if row else None

    def get_meta(self, key: str):
        row = self.db.execute(
            "SELECT value FROM hw_meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def all_meta(self) -> dict:
        return {row["key"]: row["value"]
                for row in self.db.execute("SELECT key, value FROM hw_meta")}

    # -------------------------------------------------------- thesaurus -----

    def add_thesaurus(self, term: str, canonical: str, trade: str | None,
                      status: str, source_chunk: int | None = None) -> int:
        with self.db:
            cur = self.db.execute(
                "INSERT INTO hw_thesaurus(term, canonical, trade, status, "
                "source_chunk) VALUES (?, ?, ?, ?, ?)",
                (term, canonical, trade, status, source_chunk))
        return int(cur.lastrowid)

    def thesaurus_rows(self, statuses: tuple[str, ...] | None = None):
        if statuses:
            marks = ",".join("?" * len(statuses))
            sql = (f"SELECT * FROM hw_thesaurus WHERE status IN ({marks}) "
                   f"ORDER BY id")
            return self.db.execute(sql, statuses).fetchall()
        return self.db.execute(
            "SELECT * FROM hw_thesaurus ORDER BY id").fetchall()

    def set_thesaurus_status(self, row_id: int, status: str,
                             only_if: str = "unverified") -> bool:
        with self.db:
            cur = self.db.execute(
                "UPDATE hw_thesaurus SET status = ? WHERE id = ? AND status = ?",
                (status, row_id, only_if))
        return cur.rowcount > 0

    # ------------------------------------------------------------ notes -----

    def add_note(self, text: str, author: str = "",
                 origin: str = "note") -> int:
        with self.db:
            cur = self.db.execute(
                "INSERT INTO hw_notes(text, author, origin) VALUES (?, ?, ?)",
                (text, author, origin))
        return int(cur.lastrowid)

    def note(self, note_id: int):
        return self.db.execute(
            "SELECT * FROM hw_notes WHERE id = ?", (note_id,)).fetchone()

    def notes(self, status: str | None = None):
        if status:
            return self.db.execute(
                "SELECT * FROM hw_notes WHERE status = ? ORDER BY id",
                (status,)).fetchall()
        return self.db.execute("SELECT * FROM hw_notes ORDER BY id").fetchall()

    def set_note_status(self, note_id: int, status: str) -> bool:
        with self.db:
            cur = self.db.execute(
                "UPDATE hw_notes SET status = ? WHERE id = ?",
                (status, note_id))
        return cur.rowcount > 0

    def find_note(self, origin: str, head: str):
        """Dedupe probe: a live note with this origin and 120-char head."""
        return self.db.execute(
            "SELECT id FROM hw_notes WHERE origin = ? "
            "AND substr(text, 1, 120) = ? AND status != 'rejected'",
            (origin, head[:120])).fetchone()

    def note_document(self, note_id: int):
        """The indexed document backing a note (None when de-indexed)."""
        return self.db.execute(
            "SELECT * FROM hw_documents WHERE source = ?",
            (f"note:{note_id}",)).fetchone()

    # --------------------------------------------------------- feedback -----

    def log_feedback(self, query: str, chunk_id: int, kind: str) -> None:
        norm = re.sub(r"\s+", " ", str(query)).strip().lower()
        with self.db:
            self.db.execute(
                "INSERT INTO hw_feedback(query, chunk_id, kind) "
                "VALUES (?, ?, ?)", (norm, chunk_id, kind))

    def used_feedback(self) -> list[tuple[str, int]]:
        """(query, chunk_id) for every 'used' mark — the usage-boost signal."""
        return [(row["query"], int(row["chunk_id"])) for row in self.db.execute(
            "SELECT query, chunk_id FROM hw_feedback WHERE kind = 'used'")]

    # ------------------------------------------------------------ stats -----

    def counts(self) -> dict:
        one = lambda sql, *a: int(self.db.execute(sql, a).fetchone()[0])  # noqa: E731
        return {
            "documents": one("SELECT COUNT(*) FROM hw_documents"),
            "chunks": one("SELECT COUNT(*) FROM hw_chunks"),
            "postings": one("SELECT COUNT(*) FROM hw_postings"),
            "notes_unverified": one(
                "SELECT COUNT(*) FROM hw_notes WHERE status = 'unverified'"),
            "notes_trusted": one(
                "SELECT COUNT(*) FROM hw_notes WHERE status = 'trusted'"),
            "notes_rejected": one(
                "SELECT COUNT(*) FROM hw_notes WHERE status = 'rejected'"),
            "feedback": one("SELECT COUNT(*) FROM hw_feedback"),
        }
