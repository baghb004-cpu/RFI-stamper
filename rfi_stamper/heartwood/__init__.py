"""Heartwood — Planloom's offline knowledge core.

The name is the naming story: heartwood is the dense center wood of a tree —
the oldest growth, the part that holds the whole trunk up.  Everything
Planloom knows lives here, in one SQLite file, and the answering persona the
GUI puts on top of it is **the Old Hand**: the worker who has seen it all,
speaks both the drawing-room and the scaffold vocabulary, and — crucially —
says "not in the knowledge base yet" instead of guessing.

The honest capability boundary, stated once and enforced everywhere: every
answer block is a quoted passage, a number-locked restatement of one, or a
cited extractive summary from the store.  There is no generator to
hallucinate with.  Low retrieval confidence produces a refusal plus the
nearest terms Heartwood does know.  Self-learning runs in two lanes — lane 1
(automatic, non-factual: usage boosts, alias-mining proposals, vector
retrains) never invents facts; lane 2 (factual: notes from RFI answers,
daybook lines, user teaching) lands UNVERIFIED, is always flagged in
answers, and only a human's trust promotes it.

Fully offline by policy (CLAUDE.md invariant #1): no module here imports
networking; the only I/O is the local store file, local PDFs, and a local
knowledge-base sqlite import.

    from rfi_stamper.heartwood import Heartwood
    hw = Heartwood()                        # ~/.planloom/heartwood.db
    hw.import_tradeforge("/path/kb.sqlite") # seed from the estimator's KB
    hw.ask("hot wire size for a 20 amp circuit")
"""
from __future__ import annotations

import os

from . import ask as _ask
from . import corral as _corral
from . import ingest as _ingest
from . import search as _search
from . import thesaurus as _thesaurus
from . import vectors as _vectors
from .store import HeartwoodStore

__all__ = ["Heartwood", "HeartwoodStore", "default_path"]


def default_path() -> str:
    """The default store location, next to Planloom's prefs: one file,
    ``~/.planloom/heartwood.db`` (computed here — the engine never imports
    the GUI)."""
    return os.path.join(os.path.expanduser("~"), ".planloom", "heartwood.db")


class Heartwood:
    """The facade the GUI (and the CLI, and tests) talk to.

    One instance owns one store file.  Methods map one-to-one onto the
    panels the workspace will grow: ask / teach / ingest / approve /
    rebuild / status / mark_used.
    """

    def __init__(self, path: str | None = None):
        self.store = HeartwoodStore(path or default_path())
        _thesaurus.ensure_seed(self.store)

    # ------------------------------------------------------------ asking --

    def ask(self, question: str, mode: str = "quote",
            trade: str | None = None, include_unverified: bool = True) -> dict:
        """Answer from the store or refuse honestly; see ask.ask()."""
        return _ask.ask(self.store, question, mode=mode, trade=trade,
                        include_unverified=include_unverified)

    def search(self, query: str, k: int = 10,
               trade: str | None = None) -> list[dict]:
        """Raw hybrid search results (the ask() substrate), best first."""
        return _search.search(self.store, query, k=k, trade=trade)

    def similar(self, term: str) -> dict:
        """Meaning neighborhood of a term (vector neighbors + thesaurus)."""
        return _ask.similar(self.store, term)

    def mark_used(self, query: str, chunk_id: int) -> None:
        """Record that the human actually used this chunk for this query."""
        _ask.mark_used(self.store, query, chunk_id)

    # ---------------------------------------------------------- ingesting --

    def import_tradeforge(self, db_path: str) -> dict:
        """Seed from the companion estimator's knowledge base; see
        ingest.import_tradeforge().  Never raises on a bad file — returns
        counts with an 'error' string instead."""
        return _ingest.import_tradeforge(self.store, db_path)

    def ingest_pdf(self, path: str, trade: str | None = None) -> dict:
        """Index a local PDF, then retrain."""
        out = _ingest.add_pdf(self.store, path, trade)
        out.update(rebuilt=self.rebuild())
        return out

    def ingest_text(self, title: str, text: str,
                    trade: str | None = None) -> dict:
        """Index pasted/typed text as a document, then retrain."""
        out = _ingest.add_text(self.store, title, text, trade)
        out.update(rebuilt=self.rebuild())
        return out

    def capture_rfis(self, records) -> dict:
        """One unverified note per answered Planloom RFI record."""
        return _ingest.capture_rfis(self.store, records)

    # -------------------------------------------- teaching (lane 2, gated) --

    def teach(self, text: str, author: str = "", origin: str = "note") -> int:
        """File a shop note: indexed immediately, flagged unverified until a
        human trusts it.  Returns the note id."""
        return _ingest.add_note(self.store, text, author, origin)

    def notes(self, status: str | None = None) -> list[dict]:
        """Notes on file (optionally filtered by status), oldest first."""
        return [dict(r) for r in self.store.notes(status)]

    def trust_note(self, note_id: int) -> bool:
        """Human gate: promote a note to trusted content."""
        return _ingest.trust_note(self.store, note_id)

    def reject_note(self, note_id: int) -> bool:
        """Human gate: reject a note and de-index it."""
        return _ingest.reject_note(self.store, note_id)

    # ------------------------------------------------- thesaurus approval --

    def proposals(self) -> list[dict]:
        """Mined thesaurus proposals awaiting review (with citations)."""
        return _thesaurus.list_proposed(self.store)

    def approve_term(self, proposal_id: int) -> bool:
        """Human gate: promote one mined alias into the live thesaurus."""
        return _thesaurus.approve(self.store, proposal_id)

    def reject_term(self, proposal_id: int) -> bool:
        """Human gate: discard one mined alias proposal."""
        return _thesaurus.reject(self.store, proposal_id)

    # ------------------------------------------------------- maintenance --

    def rebuild(self, log=None) -> dict:
        """Retrain the meaning layer (postings + vectors + miner)."""
        return _ingest.rebuild(self.store, log)

    # ------------------------------------------------------- the Corral --

    def compact(self, limits: dict | None = None) -> dict:
        """Cap pruning + orphan sweep + dedupe + VACUUM; never touches
        note content.  See corral.compact()."""
        return _corral.compact(self.store, limits)

    def provenance(self) -> list[dict]:
        """Every learned item with its origin, shaped for a tree view."""
        return _corral.provenance(self.store)

    def purge(self, kind: str, ident) -> bool:
        """Remove one learned item incl. its index entries (thesaurus
        seeds are disabled, never deleted)."""
        return _corral.purge(self.store, kind, ident)

    def snapshot(self, out_path: str) -> dict:
        """Export the learned state to one carry file (offline hand-off
        between machines).  See corral.snapshot()."""
        return _corral.snapshot(self.store, out_path)

    def restore(self, path: str) -> dict:
        """Merge a learned-state carry file into this store — statuses
        kept, nothing promoted.  See corral.restore()."""
        return _corral.restore(self.store, path)

    def gauges(self) -> dict:
        """Size / growth / queue numbers for the Ground Truth card row."""
        return _corral.gauges(self.store)

    def status(self) -> dict:
        """One dict for a status panel: training meta, store counts,
        thesaurus counts."""
        m = _vectors.meta(self.store)
        out = {
            "path": self.store.path,
            "trained": bool(m.get("trained_at")),
            "trained_at": m.get("trained_at"),
            "vocab": int(m.get("vocab") or 0),
            "chunk_vectors": int(m.get("chunks") or 0),
            "dim": int(m.get("dim") or 0),
            "thesaurus": _thesaurus.stats(self.store),
        }
        out.update(self.store.counts())
        return out

    # ------------------------------------------------------------ plumbing --

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> "Heartwood":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
