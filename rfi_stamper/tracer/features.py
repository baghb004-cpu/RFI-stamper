"""Feature extraction for the Tracer classifier (Phase P2).

The workhorse descriptor is the **8-direction gradient feature (NCFE)** of
OCR_PLAN §2.10/§5: Sobel gradients on the normalized 28×28 cell are soft-binned
into eight orientation planes, each plane is Gaussian-pooled onto an 8×8 grid
(8·8·8 = 512-D), and the corpus-fit **PCA/whitening** basis projects that to a
compact ~142-D code.  Two structural scalars ride alongside every glyph — the
raw ink aspect ratio and the baseline-relative vertical position from
``normalize`` — because shape alone cannot separate marks that reduce to the
same blob (a mid-height hyphen vs. a low period vs. a high apostrophe); the
2-line vertical-position dim is exactly what lets the thin marks read (§4
"vertical mark position is a free disambiguator").  Final feature dim = PCA dim
+ 2 ≈ 144, the MLP input width of OCR_PLAN §5.

A cheaper **fallback** descriptor (direction-zoning 4×4×4 + projection profiles
+ structural/topology counts, no PCA) is kept selectable for the case where the
gradient+PCA path underperforms on a given corpus — it is documented and unit
-tested but the gradient path is the shipped default (it clears the P2 bar).

Also here (they are structural descriptors) are the pure-numpy **Zhang–Suen
skeleton** and the **loop/endpoint/junction topology signature** the classifier
uses as a hard veto gate (a proposed "O" over a no-loop blob is impossible).

Everything is pure numpy and deterministic — no RNG, no external library.
"""
from __future__ import annotations

from typing import NamedTuple

import numpy as np

# --- gradient NCFE geometry (OCR_PLAN §5 "Feature dims") --------------------
N_DIR = 8                 # orientation planes (soft-assigned over [0, 2π))
POOL = 8                  # Gaussian pooling grid → POOL×POOL per plane
RAW_DIM = N_DIR * POOL * POOL          # 512-D dense gradient descriptor
PCA_DIM_DEFAULT = 142                  # → +2 extras = 144-D MLP input
_EXTRA_DIM = 2                         # aspect + baseline-relative y

# Sobel kernels (separable but kept explicit for clarity).
_SOBEL_X = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], np.float64)
_SOBEL_Y = _SOBEL_X.T.copy()


def _pool_matrix(cell: int = 28, pool: int = POOL) -> np.ndarray:
    """Row-normalized Gaussian pooling matrix ``(pool, cell)``.

    ``W @ plane @ W.T`` softly pools a ``cell×cell`` plane onto ``pool×pool``.
    Centers are spread across the cell; the Gaussian width overlaps neighbours
    so a stroke straddling a cell boundary is shared, not dropped.
    """
    centers = np.linspace(0, cell - 1, pool)
    sigma = cell / float(pool) / 1.15
    idx = np.arange(cell)[None, :]
    W = np.exp(-0.5 * ((idx - centers[:, None]) / sigma) ** 2)
    W /= W.sum(axis=1, keepdims=True)
    return W


_POOL_W = _pool_matrix()


def _sobel(cells: np.ndarray):
    """Sobel gx, gy for a batch ``(N, H, W)`` via reflect-padded 3×3 conv."""
    p = np.pad(cells, ((0, 0), (1, 1), (1, 1)), mode="edge")
    gx = np.zeros_like(cells, dtype=np.float64)
    gy = np.zeros_like(cells, dtype=np.float64)
    for dy in range(3):
        for dx in range(3):
            win = p[:, dy:dy + cells.shape[1], dx:dx + cells.shape[2]]
            gx += _SOBEL_X[dy, dx] * win
            gy += _SOBEL_Y[dy, dx] * win
    return gx, gy


def _gradient_planes(cells: np.ndarray) -> np.ndarray:
    """Soft-binned 8-orientation planes for a batch → ``(N, 8, H, W)``."""
    gx, gy = _sobel(cells)
    mag = np.sqrt(gx * gx + gy * gy)
    ang = np.arctan2(gy, gx)                      # [-π, π]
    ang = np.mod(ang, 2 * np.pi)                  # [0, 2π)
    step = 2 * np.pi / N_DIR
    pos = ang / step                              # [0, 8)
    lo = np.floor(pos).astype(np.int64) % N_DIR
    frac = pos - np.floor(pos)
    hi = (lo + 1) % N_DIR
    N, H, W = cells.shape
    planes = np.zeros((N, N_DIR, H, W), np.float64)
    ni, yi, xi = np.meshgrid(np.arange(N), np.arange(H), np.arange(W),
                             indexing="ij")
    np.add.at(planes, (ni, lo, yi, xi), mag * (1.0 - frac))
    np.add.at(planes, (ni, hi, yi, xi), mag * frac)
    return planes


def raw_gradient(cells: np.ndarray, chunk: int = 256) -> np.ndarray:
    """Dense 512-D gradient NCFE for a stack of cells → ``(N, 512)`` float32.

    Chunked so the transient ``(chunk, 8, 28, 28)`` plane tensor stays small.
    """
    cells = np.asarray(cells, np.float64)
    if cells.ndim == 2:
        cells = cells[None, ...]
    N = cells.shape[0]
    out = np.empty((N, RAW_DIM), np.float32)
    for s in range(0, N, chunk):
        e = min(N, s + chunk)
        planes = _gradient_planes(cells[s:e])                 # (n,8,H,W)
        pooled = np.einsum("pi,nkij,qj->nkpq", _POOL_W, planes, _POOL_W,
                           optimize=True)                     # (n,8,8,8)
        block = pooled.reshape(e - s, -1)
        norm = np.linalg.norm(block, axis=1, keepdims=True)
        norm[norm == 0] = 1.0
        out[s:e] = (block / norm).astype(np.float32)          # L2-normalized
    return out


# --------------------------------------------------------------------------- #
#  PCA / whitening                                                             #
# --------------------------------------------------------------------------- #

def fit_pca(raw: np.ndarray, dim: int = PCA_DIM_DEFAULT, whiten: bool = True):
    """Fit PCA on ``(N, RAW_DIM)`` → ``(mean, components (RAW_DIM,dim), scale)``.

    Deterministic (``np.linalg.eigh`` on the covariance).  ``scale`` whitens the
    projected axes to unit variance (with an epsilon floor) so the MLP sees a
    conditioned input; pass ``whiten=False`` to keep raw projection variance.
    """
    raw = np.asarray(raw, np.float64)
    mean = raw.mean(axis=0)
    X = raw - mean
    cov = (X.T @ X) / max(1, X.shape[0] - 1)
    vals, vecs = np.linalg.eigh(cov)              # ascending
    order = np.argsort(vals)[::-1][:dim]
    comps = vecs[:, order]
    ev = np.maximum(vals[order], 0.0)
    if whiten:
        scale = 1.0 / np.sqrt(ev + 1e-6)
    else:
        scale = np.ones(dim)
    return (mean.astype(np.float32), comps.astype(np.float32),
            scale.astype(np.float32))


def _extra_dims(aspects, rel_ys, n: int) -> np.ndarray:
    """Two conditioned structural scalars per glyph → ``(n, 2)``.

    ``aspect`` → ``clip(log aspect, ±2.5)/2.5`` (wide hyphen positive, tall
    stroke negative); ``rel_y`` → ``(rel_y − 0.5)·2`` (top marks negative,
    baseline marks positive).  Both land in ≈[−1, 1] to match whitened dims.
    """
    if aspects is None:
        a = np.ones(n)
    else:
        a = np.asarray(aspects, np.float64).reshape(-1)
    if rel_ys is None:
        r = np.full(n, 0.5)
    else:
        r = np.asarray(rel_ys, np.float64).reshape(-1)
    # aspect carries the O/0 and mark-width signal — weight it so the two
    # extra dims are on par with the whitened gradient dims (a bare log/2.5
    # made the O↔0 gap ≈0.07 and the net under-used it).
    af = np.clip(np.log(np.clip(a, 1e-3, None)) * 2.2, -2.2, 2.2)
    rf = np.clip((r - 0.5) * 2.0, -1.5, 1.5)
    return np.stack([af, rf], axis=1)


class Featurizer:
    """Gradient-NCFE → PCA/whiten → append structural scalars."""

    def __init__(self, mean, components, scale, mode: str = "gradient"):
        self.mean = np.asarray(mean, np.float32)
        self.components = np.asarray(components, np.float32)
        self.scale = np.asarray(scale, np.float32)
        self.mode = mode

    @property
    def dim(self) -> int:
        if self.mode == "fallback":
            return FALLBACK_DIM + _EXTRA_DIM
        return self.components.shape[1] + _EXTRA_DIM

    def transform(self, cells, aspects=None, rel_ys=None) -> np.ndarray:
        """Cells ``(N,28,28)`` (+ optional scalars) → features ``(N, dim)``."""
        cells = np.asarray(cells, np.float64)
        if cells.ndim == 2:
            cells = cells[None, ...]
        n = cells.shape[0]
        if self.mode == "fallback":
            base = raw_fallback(cells)
        else:
            raw = raw_gradient(cells).astype(np.float64)
            base = ((raw - self.mean) @ self.components) * self.scale
        extra = _extra_dims(aspects, rel_ys, n)
        return np.hstack([base, extra]).astype(np.float32)

    def to_dict(self) -> dict:
        return {"pca_mean": self.mean, "pca_components": self.components,
                "pca_scale": self.scale, "feature_mode": np.array(self.mode)}

    @staticmethod
    def from_dict(d) -> "Featurizer":
        mode = str(d["feature_mode"]) if "feature_mode" in d else "gradient"
        return Featurizer(d["pca_mean"], d["pca_components"], d["pca_scale"],
                          mode=mode)


# --------------------------------------------------------------------------- #
#  Cheap fallback feature (direction-zoning + projections + structural)        #
# --------------------------------------------------------------------------- #

_FB_DIRS = 4
_FB_ZONES = 4
FALLBACK_DIM = _FB_DIRS * _FB_ZONES * _FB_ZONES + 2 * 16 + 3   # 64 + 32 + 3 = 99


def raw_fallback(cells: np.ndarray) -> np.ndarray:
    """Documented cheap descriptor: dir-zoning 4×4×4 + profiles + structural.

    Not the shipped path (gradient+PCA clears the bar) but kept selectable and
    tested as the OCR_PLAN §5 "cheap floor" should the gradient path regress.
    """
    cells = np.asarray(cells, np.float64)
    if cells.ndim == 2:
        cells = cells[None, ...]
    N, H, W = cells.shape
    gx, gy = _sobel(cells)
    mag = np.sqrt(gx * gx + gy * gy)
    ang = np.mod(np.arctan2(gy, gx), np.pi)            # undirected [0, π)
    db = np.minimum((ang / (np.pi / _FB_DIRS)).astype(int), _FB_DIRS - 1)
    zy = np.minimum((np.arange(H) * _FB_ZONES // H), _FB_ZONES - 1)
    zx = np.minimum((np.arange(W) * _FB_ZONES // W), _FB_ZONES - 1)
    zone = zy[:, None] * _FB_ZONES + zx[None, :]       # (H,W)
    feats = np.zeros((N, FALLBACK_DIM), np.float64)
    for i in range(N):
        code = (zone * _FB_DIRS + db[i]).ravel()
        hist = np.bincount(code, weights=mag[i].ravel(),
                           minlength=_FB_DIRS * _FB_ZONES * _FB_ZONES)
        rows = cells[i].sum(1)
        cols = cells[i].sum(0)
        rprof = _POOL16 @ rows
        cprof = _POOL16 @ cols
        ep, jn, lp = topo_signature(cells[i])
        struct = np.array([ep / 10.0, jn / 10.0, lp / 3.0])
        vec = np.concatenate([hist, rprof, cprof, struct])
        nrm = np.linalg.norm(vec) or 1.0
        feats[i] = vec / nrm
    return feats.astype(np.float32)


def _pool16_matrix(cell: int = 28, out: int = 16) -> np.ndarray:
    centers = np.linspace(0, cell - 1, out)
    sigma = cell / float(out)
    idx = np.arange(cell)[None, :]
    W = np.exp(-0.5 * ((idx - centers[:, None]) / sigma) ** 2)
    W /= W.sum(axis=1, keepdims=True)
    return W


_POOL16 = _pool16_matrix()


# --------------------------------------------------------------------------- #
#  Structural topology: Zhang–Suen skeleton + loop/endpoint/junction signature #
# --------------------------------------------------------------------------- #

def zhang_suen(binary: np.ndarray) -> np.ndarray:
    """Zhang–Suen thinning (vectorized, pure numpy) → 1-px skeleton bool."""
    img = (np.asarray(binary) > 0).astype(np.uint8)
    if img.sum() == 0:
        return img.astype(bool)
    changed = True
    while changed:
        changed = False
        for step in (0, 1):
            P = img
            Pp = np.pad(P, 1)
            p2 = Pp[0:-2, 1:-1]; p3 = Pp[0:-2, 2:]; p4 = Pp[1:-1, 2:]
            p5 = Pp[2:, 2:]; p6 = Pp[2:, 1:-1]; p7 = Pp[2:, 0:-2]
            p8 = Pp[1:-1, 0:-2]; p9 = Pp[0:-2, 0:-2]
            nb = [p2, p3, p4, p5, p6, p7, p8, p9]
            B = sum(nb)
            seq = nb + [p2]
            A = sum(((seq[i] == 0) & (seq[i + 1] == 1)).astype(np.uint8)
                    for i in range(8))
            if step == 0:
                c1 = (p2 * p4 * p6) == 0
                c2 = (p4 * p6 * p8) == 0
            else:
                c1 = (p2 * p4 * p8) == 0
                c2 = (p2 * p6 * p8) == 0
            cond = (P == 1) & (B >= 2) & (B <= 6) & (A == 1) & c1 & c2
            if cond.any():
                img = img.copy()
                img[cond] = 0
                changed = True
    return img.astype(bool)


def _count_4conn(mask: np.ndarray) -> int:
    """Number of 4-connected components of a small boolean mask (flood loop)."""
    m = np.asarray(mask).copy()
    n = 0
    while m.any():
        ys, xs = np.where(m)
        seed = np.zeros_like(m)
        seed[ys[0], xs[0]] = True
        while True:
            grown = seed.copy()
            grown[1:, :] |= seed[:-1, :]
            grown[:-1, :] |= seed[1:, :]
            grown[:, 1:] |= seed[:, :-1]
            grown[:, :-1] |= seed[:, 1:]
            grown &= m
            if grown.sum() == seed.sum():
                break
            seed = grown
        m &= ~seed
        n += 1
    return n


def count_loops(binary: np.ndarray) -> int:
    """Enclosed regions (holes) of an ink glyph — the loop count for the gate.

    Background is flooded 4-connected from a padded border; unreached background
    pixels are enclosed, and their 4-connected component count is the loops.
    """
    a = (np.asarray(binary) > 0)
    if not a.any():
        return 0
    ys, xs = np.where(a)
    a = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    a = np.pad(a, 1)                              # guarantees a border ring
    bg = ~a
    reach = np.zeros_like(bg)
    reach[0, :] = bg[0, :]; reach[-1, :] = bg[-1, :]
    reach[:, 0] = bg[:, 0]; reach[:, -1] = bg[:, -1]
    while True:
        grown = reach.copy()
        grown[1:, :] |= reach[:-1, :]
        grown[:-1, :] |= reach[1:, :]
        grown[:, 1:] |= reach[:, :-1]
        grown[:, :-1] |= reach[:, 1:]
        grown &= bg
        if grown.sum() == reach.sum():
            break
        reach = grown
    holes = bg & ~reach
    return _count_4conn(holes)


class Topo(NamedTuple):
    endpoints: int
    junctions: int
    loops: int


def topo_signature(cell: np.ndarray, thresh: float = 0.3) -> Topo:
    """(endpoints, junctions, loops) of a normalized cell — structural gate.

    Endpoints/junctions come from the skeleton's per-pixel neighbour count;
    loops from :func:`count_loops` on the thresholded ink.
    """
    binary = np.asarray(cell) > thresh
    if not binary.any():
        return Topo(0, 0, 0)
    sk = zhang_suen(binary)
    Pp = np.pad(sk.astype(np.uint8), 1)
    nc = (Pp[0:-2, 1:-1] + Pp[0:-2, 2:] + Pp[1:-1, 2:] + Pp[2:, 2:]
          + Pp[2:, 1:-1] + Pp[2:, 0:-2] + Pp[1:-1, 0:-2] + Pp[0:-2, 0:-2])
    nc = nc * sk
    endpoints = int((nc == 1).sum())
    junctions = int((nc >= 3).sum())
    return Topo(endpoints, junctions, count_loops(binary))
