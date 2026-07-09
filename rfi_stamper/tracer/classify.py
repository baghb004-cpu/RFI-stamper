"""NCC (normalized cross-correlation) template classifier — the P1 workhorse.

Each normalized glyph cell is flattened, mean-subtracted and L2-normalized; so
is every template.  The cosine similarity between them is then a single dot
product, and the whole page batches into one matmul (OCR_PLAN §5: "feature +
classify thousands of glyphs < 0.1 s").  Mean-subtraction before normalizing is
what makes this *cross-correlation* rather than raw overlap — it discounts the
shared "mostly-ink-in-the-middle" bias and keeps the discriminative structure.

Per class we keep the **max** cosine over that class's variants (Helvetica /
Courier), then rank classes.  Confidence is reported two ways, per the plan: the
raw top-1 cosine (absolute match quality) and the top1−top2 margin (how decided
the call is).  P1 stops here; the gradient-feature MLP + kNN ensemble and the
topology gate are P2 (OCR_PLAN §3).
"""
from __future__ import annotations

import numpy as np


def _flatten_norm(cells: np.ndarray) -> np.ndarray:
    """Flatten, mean-subtract, L2-normalize a stack of cells → (N, D) rows."""
    x = cells.reshape(cells.shape[0], -1).astype(np.float64)
    x = x - x.mean(axis=1, keepdims=True)
    n = np.linalg.norm(x, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return x / n


# Weight of the aspect-ratio tie-break (per unit of |Δ aspect|).  Small enough
# to leave a decisive cell match untouched, large enough to separate marks that
# normalize to the same blob (hyphen aspect ≈ 4–6 vs. period ≈ 1).
ASPECT_LAMBDA = 0.08
ASPECT_CLIP = 5.0         # clamp aspects so one wild box can't dominate


class NCC:
    """Nearest-template classifier by cosine similarity over prototype cells."""

    def __init__(self):
        self._T = None            # (n_templates, D) normalized template rows
        self._labels = None       # char per template row
        self._classes = None      # unique class list (stable order)
        self._col_of_class = None # per-template class index
        self._aspect = None       # per-template raw aspect ratio

    def fit(self, prototypes: dict) -> "NCC":
        """Ingest ``{char: (cells, aspects)}`` and stack the template bank."""
        rows, labels, aspects = [], [], []
        for ch, (cells, asp) in prototypes.items():
            flat = _flatten_norm(np.asarray(cells))
            rows.append(flat)
            labels.extend([ch] * flat.shape[0])
            aspects.extend(list(np.asarray(asp)))
        self._T = np.vstack(rows)
        self._labels = np.array(labels, dtype="<U2")
        self._classes = list(dict.fromkeys(labels))  # insertion-ordered unique
        idx = {c: i for i, c in enumerate(self._classes)}
        self._col_of_class = np.array([idx[c] for c in labels])
        self._aspect = np.clip(np.asarray(aspects, np.float64), 0, ASPECT_CLIP)
        return self

    def _class_scores(self, S: np.ndarray) -> np.ndarray:
        """Reduce per-template scores ``S`` (M, n_templates) → per-class max."""
        M = S.shape[0]
        out = np.full((M, len(self._classes)), -9.0)
        for ci in range(len(self._classes)):
            cols = np.where(self._col_of_class == ci)[0]
            np.maximum(out[:, ci], S[:, cols].max(axis=1), out=out[:, ci])
        return out

    def classify_batch(self, cells: np.ndarray, aspects=None):
        """Classify a stack of cells → list of ranked ``[(char, score), ...]``.

        One matmul scores every glyph against every template (cosine of the
        mean-subtracted, L2-normalized cells); if per-glyph ``aspects`` are
        supplied, a light ``ASPECT_LAMBDA·|Δaspect|`` penalty is subtracted per
        template before the per-class max-pool and ranking.  Returned lists are
        sorted by descending score (top-3 kept — enough for a margin).
        """
        if cells.shape[0] == 0:
            return []
        V = _flatten_norm(cells)
        S = V @ self._T.T
        if aspects is not None:
            ga = np.clip(np.asarray(aspects, np.float64), 0, ASPECT_CLIP)
            S = S - ASPECT_LAMBDA * np.abs(ga[:, None] - self._aspect[None, :])
        cls = self._class_scores(S)
        order = np.argsort(-cls, axis=1)[:, :3]
        classes = self._classes
        out = []
        for m in range(cls.shape[0]):
            out.append([(classes[j], float(cls[m, j])) for j in order[m]])
        return out

    def classify(self, cell: np.ndarray, aspect=None):
        """Classify a single (cell, cell) glyph → ranked ``[(char, score)]``."""
        asp = None if aspect is None else [aspect]
        return self.classify_batch(cell[None, ...], asp)[0]


_DEFAULT: NCC | None = None


def default_classifier() -> NCC:
    """Return a process-wide NCC fitted on the cached synthetic prototypes."""
    global _DEFAULT
    if _DEFAULT is None:
        from .fonts import prototypes
        _DEFAULT = NCC().fit(prototypes())
    return _DEFAULT
