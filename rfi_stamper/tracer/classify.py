"""Glyph classification — the P1 NCC template bank and the P2 ensemble.

**NCC (normalized cross-correlation)** — the P1 workhorse, kept intact.  Each
normalized glyph cell is flattened, mean-subtracted and L2-normalized; so is
every template.  Cosine similarity is one dot product and the whole page batches
into a single matmul (OCR_PLAN §5).  Mean-subtraction makes it
*cross-correlation* not raw overlap.  Per class we keep the max cosine over that
class's variants (Helvetica / Courier).  The NCC is the ensemble's high
-precision voter and the per-firm font-adaptation vehicle (OCR_PLAN §3).

**The P2 ensemble** (``Ensemble``) is the new default for ``read_image``:

* a from-scratch numpy **MLP** (gradient-NCFE feature → hidden ReLU → 43-way
  softmax) trained by hand-written mini-batch SGD/backprop — the runtime
  workhorse (OCR_PLAN §3);
* a **kNN** exemplar store (the self-learning memory; cosine top-k);
* the **NCC** template bank as the precision voter;
combined into a per-class posterior, vetoed by a **Zhang–Suen topology gate**
(a proposed "O" over a no-loop blob is impossible), argmaxed, and scored with a
**calibrated confidence** (temperature-scaled MLP + margin-ratio reliability
binning + an absolute-match noise guard) so pure noise never reads as a
confident token.  Trained once and cached to ``model.npz`` (§ trained-weights).
"""
from __future__ import annotations

import os

import numpy as np

from . import features as _feat


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

    def class_scores(self, cells: np.ndarray, aspects=None):
        """Per-class cosine matrix ``(M, n_classes)`` + the class order.

        Exposed for the ensemble: it needs the whole per-class score vector
        (aligned to ``self._classes``), not just the ranked top-3.
        """
        if cells.shape[0] == 0:
            return np.zeros((0, len(self._classes))), list(self._classes)
        V = _flatten_norm(cells)
        S = V @ self._T.T
        if aspects is not None:
            ga = np.clip(np.asarray(aspects, np.float64), 0, ASPECT_CLIP)
            S = S - ASPECT_LAMBDA * np.abs(ga[:, None] - self._aspect[None, :])
        return self._class_scores(S), list(self._classes)


_DEFAULT: NCC | None = None


def default_classifier() -> NCC:
    """Return a process-wide NCC fitted on the cached synthetic prototypes."""
    global _DEFAULT
    if _DEFAULT is None:
        from .fonts import prototypes
        _DEFAULT = NCC().fit(prototypes())
    return _DEFAULT


# =========================================================================== #
#  P2: from-scratch numpy MLP                                                 #
# =========================================================================== #

def _softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(axis=1, keepdims=True)          # numerically stable
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


class MLP:
    """One-hidden-layer softmax classifier, hand-written SGD + backprop.

    ``D → H (ReLU) → C (softmax)``.  He init on the input layer (ReLU), Xavier
    on the output.  Training is mini-batch SGD with momentum, cross-entropy
    loss, a linear LR decay and light L2 — no ML library, fully deterministic
    given ``seed``.  Inference is two matmuls; a stored temperature ``T``
    softens the softmax for calibration.
    """

    def __init__(self, W1, b1, W2, b2, temperature: float = 1.0):
        self.W1 = np.asarray(W1, np.float32)
        self.b1 = np.asarray(b1, np.float32)
        self.W2 = np.asarray(W2, np.float32)
        self.b2 = np.asarray(b2, np.float32)
        self.T = float(temperature)

    @staticmethod
    def init(D: int, H: int, C: int, seed: int = 0) -> "MLP":
        rng = np.random.default_rng(seed)
        W1 = rng.normal(0, np.sqrt(2.0 / D), (D, H)).astype(np.float32)
        W2 = rng.normal(0, np.sqrt(1.0 / H), (H, C)).astype(np.float32)
        return MLP(W1, np.zeros(H, np.float32), W2, np.zeros(C, np.float32))

    def _logits(self, X):
        a1 = np.maximum(0.0, X @ self.W1 + self.b1)
        return a1, a1 @ self.W2 + self.b2

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, np.float32)
        _, z2 = self._logits(X)
        return _softmax(z2 / self.T)

    def logits(self, X):
        return self._logits(np.asarray(X, np.float32))[1]

    def fit(self, X, y, n_classes, epochs=90, batch=64, lr=0.3,
            momentum=0.9, l2=1e-4, clip=5.0, seed=0, log=None):
        rng = np.random.default_rng(seed)
        X = np.asarray(X, np.float32)
        Y = np.eye(n_classes, dtype=np.float32)[y]
        n = X.shape[0]
        vW1 = np.zeros_like(self.W1); vb1 = np.zeros_like(self.b1)
        vW2 = np.zeros_like(self.W2); vb2 = np.zeros_like(self.b2)
        for ep in range(epochs):
            order = rng.permutation(n)
            cur_lr = lr * (1.0 - 0.85 * ep / max(1, epochs - 1))
            for s in range(0, n, batch):
                idx = order[s:s + batch]
                xb = X[idx]; yb = Y[idx]
                a1 = np.maximum(0.0, xb @ self.W1 + self.b1)
                z2 = a1 @ self.W2 + self.b2
                p = _softmax(z2)
                m = xb.shape[0]
                dz2 = (p - yb) / m
                gW2 = a1.T @ dz2 + l2 * self.W2
                gb2 = dz2.sum(0)
                da1 = dz2 @ self.W2.T
                da1[a1 <= 0] = 0.0
                gW1 = xb.T @ da1 + l2 * self.W1
                gb1 = da1.sum(0)
                # global-norm gradient clipping keeps mini-batch SGD stable
                gn = np.sqrt(sum(float((g * g).sum())
                                 for g in (gW1, gb1, gW2, gb2)))
                if gn > clip:
                    sc = clip / (gn + 1e-9)
                    gW1 *= sc; gb1 *= sc; gW2 *= sc; gb2 *= sc
                vW1 = momentum * vW1 - cur_lr * gW1
                vb1 = momentum * vb1 - cur_lr * gb1
                vW2 = momentum * vW2 - cur_lr * gW2
                vb2 = momentum * vb2 - cur_lr * gb2
                self.W1 += vW1; self.b1 += vb1
                self.W2 += vW2; self.b2 += vb2
            if log and (ep % 10 == 0 or ep == epochs - 1):
                pr = self.predict_proba(X)
                acc = float((pr.argmax(1) == y).mean())
                log(f"    epoch {ep:3d}  lr={cur_lr:.3f}  train_acc={acc:.4f}")
        return self

    def fit_temperature(self, X, y) -> float:
        """Grid-search the temperature that minimizes held-out NLL."""
        z = self.logits(X)
        best_T, best_nll = 1.0, np.inf
        for T in np.arange(0.5, 8.01, 0.05):
            p = _softmax(z / T)
            nll = -np.log(np.clip(p[np.arange(len(y)), y], 1e-9, 1)).mean()
            if nll < best_nll:
                best_nll, best_T = nll, float(T)
        self.T = best_T
        return best_T


# =========================================================================== #
#  P2: kNN exemplar store (the self-learning memory)                          #
# =========================================================================== #

class KNN:
    """Vectorized cosine kNN over a small exemplar bank (OCR_PLAN §3).

    ``add`` is one ``np.vstack`` — every human correction grows the memory with
    no retraining.  ``proba`` scores each class by its best cosine match and
    softmaxes, so it drops cleanly into the ensemble as a third voter.
    """

    def __init__(self, X=None, y=None, n_classes=0, k: int = 5, temp: float = 0.12):
        self.X = None if X is None else _unit(np.asarray(X, np.float32))
        self.y = None if y is None else np.asarray(y, np.int64)
        self.n_classes = n_classes
        self.k = k
        self.temp = temp

    def add(self, vec, label):
        v = _unit(np.asarray(vec, np.float32).reshape(1, -1))
        self.X = v if self.X is None else np.vstack([self.X, v])
        lab = np.asarray([label], np.int64)
        self.y = lab if self.y is None else np.concatenate([self.y, lab])

    def proba(self, Xq: np.ndarray) -> np.ndarray:
        Xq = _unit(np.asarray(Xq, np.float32))
        n = Xq.shape[0]
        if self.X is None or self.X.shape[0] == 0:
            return np.full((n, self.n_classes), 1.0 / max(1, self.n_classes))
        sims = Xq @ self.X.T                      # (n, M) cosine
        best = np.full((n, self.n_classes), -1.0)
        for c in range(self.n_classes):
            cols = np.where(self.y == c)[0]
            if cols.size:
                np.maximum(best[:, c], sims[:, cols].max(1), out=best[:, c])
        return _softmax(best / self.temp)


def _unit(X):
    n = np.linalg.norm(X, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return X / n


# =========================================================================== #
#  P2: the ensemble + calibrated confidence + topology gate                   #
# =========================================================================== #

W_MLP, W_NCC, W_KNN = 0.55, 0.30, 0.15   # ensemble mixing weights
NCC_TEMP = 0.14                          # NCC cosine → probability temperature
NCC_FLOOR = 0.18                         # below this cosine the match is noise
NCC_FULL = 0.55                          # at/above this cosine no noise penalty


class Ensemble:
    """MLP + kNN + NCC posterior, topology-gated, calibrated confidence."""

    def __init__(self, featurizer, mlp, knn, ncc, charset, loop_min,
                 calib_edges, calib_vals):
        self.f = featurizer
        self.mlp = mlp
        self.knn = knn
        self.ncc = ncc
        self.charset = list(charset)
        self.loop_min = np.asarray(loop_min, np.int64)
        self.calib_edges = np.asarray(calib_edges, np.float64)
        self.calib_vals = np.asarray(calib_vals, np.float64)
        # NCC class order aligned to charset (prototypes iterate CHARSET)
        _, ncc_classes = ncc.class_scores(
            np.zeros((0, 28, 28), np.float32))
        self._ncc_perm = [ncc_classes.index(c) if c in ncc_classes else -1
                          for c in self.charset]

    # -- posterior ---------------------------------------------------------- #
    def _posterior(self, cells, aspects, rel_ys):
        feats = self.f.transform(cells, aspects, rel_ys)
        Pmlp = self.mlp.predict_proba(feats)
        Pknn = self.knn.proba(feats)
        cos, _ = self.ncc.class_scores(cells, aspects)     # (n, n_ncc)
        ncc_cos = np.zeros((cells.shape[0], len(self.charset)))
        for j, src in enumerate(self._ncc_perm):
            if src >= 0:
                ncc_cos[:, j] = cos[:, src]
        Pncc = _softmax(ncc_cos / NCC_TEMP)
        P = W_MLP * Pmlp + W_KNN * Pknn + W_NCC * Pncc
        ncc_top = ncc_cos.max(1)
        return P, ncc_top

    def _calibrate(self, raw):
        idx = np.clip(np.searchsorted(self.calib_edges, raw, side="right") - 1,
                      0, len(self.calib_vals) - 1)
        return self.calib_vals[idx]

    def topology_veto(self, cell) -> set:
        """Classes the Zhang–Suen loop count makes impossible for this glyph.

        A class whose every training prototype carried ≥1 enclosed loop cannot
        explain a glyph with zero loops — so an "O" (or 0/D/B/Q/8/&) proposed
        over a no-loop blob is vetoed.  Exposed for the hard-gate test.
        """
        loops = _feat.count_loops(np.asarray(cell) > 0.3)
        return {self.charset[c] for c in range(len(self.charset))
                if self.loop_min[c] >= 1 and loops < self.loop_min[c]}

    def classify_batch(self, cells, aspects=None, rel_ys=None, topk: int = 3):
        cells = np.asarray(cells, np.float32)
        if cells.shape[0] == 0:
            return []
        P, ncc_top = self._posterior(cells, aspects, rel_ys)
        # topology gate: veto loop-bearing classes for a no-loop glyph
        loops = np.array([_feat.count_loops(c > 0.3) for c in cells])
        for i in range(cells.shape[0]):
            veto = (self.loop_min >= 1) & (loops[i] < self.loop_min)
            if veto.any():
                P[i, veto] = 0.0
        rs = P.sum(1, keepdims=True)
        rs[rs == 0] = 1.0
        P = P / rs
        raw_top = P.max(1)
        cal = self._calibrate(raw_top)
        # absolute-match noise guard (raw softmax is overconfident on noise)
        q = np.clip((ncc_top - NCC_FLOOR) / (NCC_FULL - NCC_FLOOR), 0.0, 1.0)
        conf = cal * (0.5 + 0.5 * q)
        order = np.argsort(-P, axis=1)[:, :topk]
        out = []
        for i in range(cells.shape[0]):
            ranked = []
            for rank, j in enumerate(order[i]):
                sc = float(conf[i]) if rank == 0 else float(P[i, j])
                ranked.append((self.charset[j], sc))
            out.append(ranked)
        return out

    def classify(self, cell, aspect=None, rel_y=None):
        a = None if aspect is None else [aspect]
        r = None if rel_y is None else [rel_y]
        return self.classify_batch(cell[None, ...], a, r)[0]

    # -- P3 self-learning: append a verified exemplar to the kNN memory ------ #
    def add_exemplar(self, cell_or_feat, char, provenance: str = "auto",
                     cap: int = 20000) -> bool:
        """Grow the kNN store by one verified glyph (OCR_PLAN §3 auto lane).

        ``cell_or_feat`` is either a normalized ``(28, 28)`` cell (featurized
        here) or an already-computed feature row.  ``char`` is the CHARSET
        label.  Provenance is tracked ("synthetic"/"auto"/"human") and the store
        is capped so the memory cannot grow without bound.  Returns True if the
        exemplar was added.  This mutates ONLY the kNN memory — the shipped MLP
        and NCC bank are never touched by the auto lane (drift-safe).
        """
        if char not in self.charset:
            return False
        arr = np.asarray(cell_or_feat, np.float32)
        if arr.ndim == 2:                       # a 28×28 cell → featurize
            feat = self.f.transform(arr[None, ...], None, None)[0]
        else:
            feat = arr.reshape(-1)
        if (self.knn.X is not None and self.knn.X.shape[0] >= cap):
            return False
        self.knn.add(feat, self.charset.index(char))
        if not hasattr(self, "provenance"):
            self.provenance = []
        # backfill provenance for the seeded exemplars the first time
        if self.knn.y is not None and len(self.provenance) < self.knn.y.shape[0] - 1:
            self.provenance = ["synthetic"] * (self.knn.y.shape[0] - 1)
        self.provenance.append(provenance)
        return True

    def exemplar_count(self) -> int:
        """Number of exemplars currently in the kNN memory."""
        return 0 if self.knn.X is None else int(self.knn.X.shape[0])


# --------------------------------------------------------------------------- #
#  Trained-model persistence (train once → model.npz → lazy load)             #
# --------------------------------------------------------------------------- #

_MODEL_PATH = os.path.join(os.path.dirname(__file__), "model.npz")
_ENSEMBLE: Ensemble | None = None


def default_ensemble() -> Ensemble:
    """Process-wide ensemble; loads ``model.npz`` (training it once if absent)."""
    global _ENSEMBLE
    if _ENSEMBLE is None:
        if not os.path.exists(_MODEL_PATH):
            train_and_save(_MODEL_PATH)
        _ENSEMBLE = load_ensemble(_MODEL_PATH)
    return _ENSEMBLE


def load_ensemble(path: str = _MODEL_PATH) -> Ensemble:
    """Reconstruct the :class:`Ensemble` from a saved ``model.npz``."""
    d = np.load(path, allow_pickle=False)
    charset = "".join(chr(c) for c in d["charset"])
    featurizer = _feat.Featurizer(d["pca_mean"], d["pca_components"],
                                  d["pca_scale"],
                                  mode=str(d["feature_mode"]))
    mlp = MLP(d["W1"], d["b1"], d["W2"], d["b2"],
              temperature=float(d["temperature"]))
    knn = KNN(d["knn_X"], d["knn_y"], n_classes=len(charset))
    from .fonts import prototypes
    ncc = NCC().fit(prototypes())
    return Ensemble(featurizer, mlp, knn, ncc, charset, d["loop_min"],
                    d["calib_edges"], d["calib_vals"])


def _seed_knn(feats, labels, n_classes, per_class=8):
    """Pick the most-representative ``per_class`` exemplars per class (compact)."""
    X, y = [], []
    for c in range(n_classes):
        idx = np.where(labels == c)[0]
        if idx.size == 0:
            continue
        mean = feats[idx].mean(0)
        d = np.linalg.norm(feats[idx] - mean, axis=1)
        keep = idx[np.argsort(d)[:per_class]]
        X.append(feats[keep]); y.append(labels[keep])
    return np.vstack(X).astype(np.float32), np.concatenate(y).astype(np.int64)


def train_and_save(path: str | None = None, seed: int = 0, per_class: int = 240,
                   sizes=(20, 28, 36, 48), hidden: int = 256, epochs: int = 130,
                   log=None) -> dict:
    """Train PCA + MLP + kNN + calibration + topology gate, save to ``path``.

    Deterministic (all RNGs seeded).  Returns a small stats dict (held-out
    accuracy, param count, feature dim, timings) for the report.
    """
    import time
    from . import synth, binarize
    from .fonts import prototypes, CHARSET

    path = path or _MODEL_PATH
    t0 = time.time()
    corp = synth.corpus(seed=seed, per_class=per_class, sizes=sizes)
    tr = ~corp.is_test
    te = corp.is_test

    # PCA on the training split only (no leakage)
    raw_tr = _feat.raw_gradient(corp.cells[tr])
    mean, comps, scale = _feat.fit_pca(raw_tr, dim=_feat.PCA_DIM_DEFAULT)
    featurizer = _feat.Featurizer(mean, comps, scale, mode="gradient")
    Xtr = featurizer.transform(corp.cells[tr], corp.aspect[tr], corp.rel_y[tr])
    Xte = featurizer.transform(corp.cells[te], corp.aspect[te], corp.rel_y[te])
    ytr, yte = corp.y[tr], corp.y[te]
    n_classes = len(CHARSET)
    t_feat = time.time() - t0

    mlp = MLP.init(featurizer.dim, hidden, n_classes, seed=seed)
    t1 = time.time()
    mlp.fit(Xtr, ytr, n_classes, epochs=epochs, seed=seed, log=log)
    train_time = time.time() - t1
    mlp.fit_temperature(Xte, yte)
    # the ≥99% "self-classifies the CHARSET" bar is measured on the legible
    # (clean/mild) held-out tier; harsh held-out feeds the robustness metric.
    sev_te = corp.severity[te]
    pred_te = mlp.predict_proba(Xte).argmax(1)
    clean = sev_te == 0
    legible = sev_te <= 1
    harsh = sev_te == 2
    held_acc = float((pred_te[clean] == yte[clean]).mean())      # the ≥99% bar
    held_acc_legible = float((pred_te[legible] == yte[legible]).mean())
    held_acc_all = float((pred_te == yte).mean())
    held_acc_harsh = float((pred_te[harsh] == yte[harsh]).mean()) \
        if harsh.any() else held_acc_all

    knn_X, knn_y = _seed_knn(Xtr, ytr, n_classes, per_class=8)

    # topology gate signature: min loops per class over CLEAN prototypes
    loop_min = np.zeros(n_classes, np.int64)
    protos = prototypes()
    from .normalize import norm_glyph  # noqa: F401
    for ci, ch in enumerate(CHARSET):
        obs = []
        if ch in protos:
            for cell in protos[ch][0]:
                obs.append(_feat.count_loops(cell > 0.3))
        for fid in ("herB0", "herA0"):
            g = None
            from . import fonts as _f
            g = _f._hershey_gray(ch, 40, fid)
            if g is not None:
                ink = binarize.otsu(g)[1]
                obs.append(_feat.count_loops(norm_glyph(ink).cell > 0.3))
        loop_min[ci] = int(min(obs)) if obs else 0

    # build the ensemble to calibrate the FINAL posterior on held-out
    ncc = NCC().fit(protos)
    knn = KNN(knn_X, knn_y, n_classes=n_classes)
    edges0 = np.linspace(0.0, 1.0, 11)
    ens = Ensemble(featurizer, mlp, knn, ncc, CHARSET, loop_min,
                   edges0, np.linspace(0.0, 1.0, 10))
    P, _ncc_top = ens._posterior(corp.cells[te], corp.aspect[te], corp.rel_y[te])
    raw_top = P.max(1)
    pred = P.argmax(1)
    correct = (pred == yte).astype(np.float64)
    # margin-ratio reliability binning (monotone-enforced)
    edges = np.quantile(raw_top, np.linspace(0, 1, 11))
    edges[0], edges[-1] = 0.0, 1.0 + 1e-6
    edges = np.maximum.accumulate(edges)
    vals = np.zeros(len(edges) - 1)
    for b in range(len(edges) - 1):
        m = (raw_top >= edges[b]) & (raw_top < edges[b + 1])
        vals[b] = correct[m].mean() if m.any() else (vals[b - 1] if b else 0.0)
    vals = np.maximum.accumulate(vals)             # monotone calibration
    calib_edges = edges[:-1].copy()

    # domain confusion seed (43×43) from held-out predictions
    confusion = np.zeros((n_classes, n_classes), np.float32)
    for tlab, plab in zip(yte, pred):
        confusion[tlab, plab] += 1.0

    stats = {
        "n_train": int(tr.sum()), "n_test": int(te.sum()),
        "feature_dim": featurizer.dim, "pca_dim": comps.shape[1],
        "hidden": hidden,
        "params": int(mlp.W1.size + mlp.b1.size + mlp.W2.size + mlp.b2.size),
        "held_acc": held_acc, "held_acc_legible": held_acc_legible,
        "held_acc_all": held_acc_all, "held_acc_harsh": held_acc_harsh,
        "n_clean_test": int(clean.sum()), "temperature": mlp.T,
        "train_time_s": round(train_time, 2),
        "feat_time_s": round(t_feat, 2),
        "total_time_s": round(time.time() - t0, 2),
    }
    if path:
        np.savez_compressed(
            path,
            charset=np.array([ord(c) for c in CHARSET], np.int32),
            feature_mode=np.array(featurizer.mode),
            pca_mean=mean, pca_components=comps, pca_scale=scale,
            W1=mlp.W1, b1=mlp.b1, W2=mlp.W2, b2=mlp.b2,
            temperature=np.float32(mlp.T),
            knn_X=knn_X, knn_y=knn_y,
            loop_min=loop_min,
            calib_edges=calib_edges, calib_vals=vals,
            confusion=confusion,
        )
        stats["path"] = path
        stats["bytes"] = os.path.getsize(path)
    return stats
