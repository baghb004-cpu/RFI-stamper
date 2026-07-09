"""The Tracer — Phase P3: two-lane self-learning + per-firm font profile.

Mirrors Heartwood/the Corral's provenance discipline (OCR_PLAN §3): the engine
gets monotonically better on a firm's own hand of sheets, offline, without ever
letting an unreviewed glyph rewrite the shipped model.

* **Auto lane** — :func:`learn_verified_token` appends the glyphs of a token
  that the lexicon/grammar VERIFIED (a real sheet number, a parsed dimension, an
  in-lexicon word) and that the classifier read with high confidence to the kNN
  memory via ``Ensemble.add_exemplar``.  The MLP/NCC bank are untouched; only
  the self-learning memory grows, capped and provenance-tagged.

* **Human-gated lane** — :class:`Corrections` records ``(glyph_cell, true_char)``
  to a pending list.  NOTHING promotes to the memory until a human calls
  :meth:`Corrections.promote` — the shipped bank never drifts on its own.

* **Font profile** — a per-firm sidecar (:class:`FontProfile`) of adapted kNN
  exemplars keyed by producer, saved/loaded as a compact ``.npz``.  When a new
  set matches the producer the profile is applied, giving few-shot adaptation to
  that firm's title-block lettering — the structural edge a frozen engine lacks.

Pure numpy + stdlib, deterministic, offline.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np

from .fonts import CHARSET
from .lexicon import dim_ok, field_of, TAU_HI


# --------------------------------------------------------------------------- #
#  Auto lane                                                                   #
# --------------------------------------------------------------------------- #
def token_is_verified(text: str, box, ctx: "Context | None") -> bool:
    """Is ``text`` grammar/lexicon-verified enough to auto-harvest its glyphs?

    A sheet token whose canonical form is in the document's own index, a
    dimension that parses, or a word in the lexicon.  Conservative by design —
    only self-supervised-certain tokens feed the memory.
    """
    if not text:
        return False
    fieldname = field_of(text, box, ctx.page_wh if ctx else None)
    if fieldname == "sheet":
        if ctx and ctx.sheet_hints:
            from ..core import SHEET_TOKEN, canon
            m = SHEET_TOKEN.search(text.upper())
            tok = canon(m.group(1), m.group(2)) if m else text.upper()
            return tok in ctx.sheet_hints
        return False
    if fieldname == "dim":
        return dim_ok(text)
    if fieldname == "word":
        return bool(ctx and ctx.lexicon and ctx.lexicon.contains(text))
    return False


def learn_verified_token(ensemble, cells, text, box=None, ctx=None,
                         confidences=None, tau=TAU_HI, provenance="auto") -> int:
    """Auto-lane: append the glyphs of a verified, confident token to the store.

    ``cells`` is the list/stack of normalized ``(28, 28)`` glyph cells that
    produced ``text`` (one per character).  Returns the number of exemplars
    added — ``0`` unless the token is verified AND (if ``confidences`` given)
    every glyph read at or above ``tau``.  The store grows by exactly the glyph
    count on success.
    """
    if not token_is_verified(text, box, ctx):
        return 0
    chars = [c for c in text if c in CHARSET]
    cells = list(cells)
    if len(chars) != len(cells) or not cells:
        return 0
    if confidences is not None:
        if len(confidences) != len(cells) or min(confidences) < tau:
            return 0
    added = 0
    for cell, ch in zip(cells, chars):
        if ensemble.add_exemplar(np.asarray(cell), ch, provenance=provenance):
            added += 1
    return added


# --------------------------------------------------------------------------- #
#  Human-gated lane                                                            #
# --------------------------------------------------------------------------- #
@dataclass
class Corrections:
    """A pending queue of human glyph corrections (nothing ships until promoted)."""

    pending: list = field(default_factory=list)   # [(cell(28,28), true_char)]

    def record_correction(self, glyph_cell, true_char: str) -> bool:
        """File one correction for later, human-reviewed promotion."""
        if true_char not in CHARSET:
            return False
        self.pending.append((np.asarray(glyph_cell, np.float32), true_char))
        return True

    def promote(self, ensemble, provenance: str = "human") -> int:
        """Human gate: fold every pending correction into the kNN memory.

        Returns the number promoted; clears the queue.  This is the ONLY path by
        which a human label reaches the memory, and it never touches the MLP/NCC.
        """
        added = 0
        for cell, ch in self.pending:
            if ensemble.add_exemplar(cell, ch, provenance=provenance):
                added += 1
        self.pending = []
        return added


# --------------------------------------------------------------------------- #
#  Per-firm font profile sidecar                                              #
# --------------------------------------------------------------------------- #
@dataclass
class FontProfile:
    """Adapted kNN exemplars for one firm's producer, persisted as ``.npz``.

    Minimal + honest (OCR_PLAN §3): the profile carries the harvested feature
    exemplars and their labels keyed by ``producer``.  Applying it seeds a fresh
    ensemble's memory with this firm's own lettering.
    """

    producer: str = ""
    knn_X: np.ndarray | None = None
    knn_y: np.ndarray | None = None
    provenance: list = field(default_factory=list)

    @staticmethod
    def from_ensemble(ensemble, producer: str) -> "FontProfile":
        """Snapshot the ensemble's current kNN memory as a named profile."""
        X = None if ensemble.knn.X is None else np.asarray(ensemble.knn.X, np.float32)
        y = None if ensemble.knn.y is None else np.asarray(ensemble.knn.y, np.int64)
        prov = list(getattr(ensemble, "provenance", []))
        return FontProfile(producer=producer, knn_X=X, knn_y=y, provenance=prov)

    def save(self, path: str) -> str:
        """Atomically write the profile to ``path`` (``.npz`` container)."""
        X = self.knn_X if self.knn_X is not None else np.zeros((0, 1), np.float32)
        y = self.knn_y if self.knn_y is not None else np.zeros((0,), np.int64)
        prov = np.array([str(p) for p in self.provenance])
        tmp = path + ".part"
        # pass a file handle so numpy writes exactly ``tmp`` (no .npz suffix)
        with open(tmp, "wb") as fh:
            np.savez(fh, producer=np.array(self.producer),
                     knn_X=X, knn_y=y, provenance=prov)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return path

    @staticmethod
    def load(path: str) -> "FontProfile":
        d = np.load(path, allow_pickle=False)
        X = d["knn_X"]
        y = d["knn_y"]
        return FontProfile(
            producer=str(d["producer"]),
            knn_X=None if X.size == 0 else X.astype(np.float32),
            knn_y=None if y.size == 0 else y.astype(np.int64),
            provenance=[str(p) for p in d["provenance"]] if "provenance" in d else [])

    def apply_to(self, ensemble) -> int:
        """Seed ``ensemble``'s kNN memory with this profile's exemplars."""
        if self.knn_X is None or self.knn_y is None:
            return 0
        added = 0
        for feat, lab in zip(self.knn_X, self.knn_y):
            ch = CHARSET[int(lab)]
            if ensemble.add_exemplar(feat, ch, provenance="profile"):
                added += 1
        return added


def save_profile(path: str, profile: "FontProfile") -> str:
    """Persist a :class:`FontProfile` sidecar (module-level convenience)."""
    return profile.save(path)


def load_profile(path: str) -> "FontProfile":
    """Load a :class:`FontProfile` sidecar (module-level convenience)."""
    return FontProfile.load(path)
