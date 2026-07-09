"""Orientation + skew estimation from projection profiles.

Two corrections, in the order OCR_PLAN §2 step 5 prescribes:

* **Quadrant** (0/90/180/270): horizontal machine-print gives a *peaky* row
  projection (dense text rows separated by blank leading) and a flat column
  projection; rotated text swaps that.  ``orient_quadrant`` compares the
  peakiness of the two axes and only reports a non-zero quadrant when the
  vertical axis decisively wins — biased to 0 so a clean upright title block is
  never spuriously rotated (the "never emit garbage" stance).
* **Fine skew** (θ ∈ [−15°, +15°]): the angle that maximizes ``sum(row_sum²)``
  — sharpest when text rows line up — found coarse (0.5°) then refined (0.1°),
  exactly the plan's objective.

``deskew`` applies the quadrant with ``np.rot90`` (exact) and reports the fine
angle it measured.  In P1 the read path only *applies* the exact quadrant turn;
sub-degree resampling is deferred to P2 (it would blur the clean rasters that
are P1's target), but the estimators are implemented and unit-tested now.
"""
from __future__ import annotations

import numpy as np

FINE_RANGE = 15.0        # ± search bound (deg)
FINE_COARSE = 0.5        # coarse step (deg)
FINE_FINE = 0.1          # refine step (deg)
QUADRANT_MARGIN = 1.25   # vertical peakiness must beat horizontal by this


def _peakiness(profile: np.ndarray) -> float:
    """Coefficient-of-variation of a 1-D projection (higher = more banded)."""
    p = profile.astype(np.float64)
    mu = p.mean()
    if mu <= 0:
        return 0.0
    return float(p.std() / mu)


def orient_quadrant(binary: np.ndarray) -> int:
    """Return 0/90/180/270 — the coarse text orientation (biased to 0).

    Compares the row-projection peakiness (horizontal text signature) with the
    column-projection peakiness (vertical text signature).  Only when the
    column axis wins by ``QUADRANT_MARGIN`` is 90 reported; 180/270 (up/down and
    the vertical flip) need the classifier to resolve and are left to P2, so we
    report the axis, not the sign.
    """
    b = binary.astype(np.float64)
    row_peak = _peakiness(b.sum(axis=1))     # variation across rows
    col_peak = _peakiness(b.sum(axis=0))     # variation across columns
    if col_peak > QUADRANT_MARGIN * row_peak:
        return 90
    return 0


def _rot_cost(binary: np.ndarray, deg: float) -> float:
    """Objective ``sum(row_sum²)`` after rotating ``binary`` by ``deg``.

    Rotation is a nearest-neighbour affine sample (cheap; we only need the row
    histogram's sharpness, not a pretty image).
    """
    if abs(deg) < 1e-6:
        rot = binary
    else:
        H, W = binary.shape
        cy, cx = (H - 1) / 2.0, (W - 1) / 2.0
        th = np.deg2rad(deg)
        cos, sin = np.cos(th), np.sin(th)
        ys, xs = np.mgrid[0:H, 0:W]
        sy = (ys - cy)
        sx = (xs - cx)
        src_y = np.round(cy + sin * sx + cos * sy).astype(int)
        src_x = np.round(cx + cos * sx - sin * sy).astype(int)
        ok = (src_y >= 0) & (src_y < H) & (src_x >= 0) & (src_x < W)
        rot = np.zeros_like(binary)
        rot[ys[ok], xs[ok]] = binary[src_y[ok], src_x[ok]]
    rowsum = rot.astype(np.float64).sum(axis=1)
    return float((rowsum * rowsum).sum())


def fine_skew(binary: np.ndarray) -> float:
    """Residual skew in degrees, maximizing ``sum(row_sum²)`` (coarse→fine)."""
    if not binary.any():
        return 0.0
    coarse = np.arange(-FINE_RANGE, FINE_RANGE + FINE_COARSE, FINE_COARSE)
    best = max(coarse, key=lambda d: _rot_cost(binary, d))
    fine = np.arange(best - FINE_COARSE, best + FINE_COARSE + FINE_FINE, FINE_FINE)
    best = max(fine, key=lambda d: _rot_cost(binary, d))
    return float(round(best, 2))


def deskew(binary: np.ndarray):
    """Return ``(upright_binary, applied_deg)`` after the quadrant correction.

    The exact ``np.rot90`` quadrant turn is applied; the fine angle is measured
    and returned for the caller/log but not resampled in P1 (see module note).
    """
    q = orient_quadrant(binary)
    k = (q // 90) % 4
    out = np.rot90(binary, k) if k else binary
    angle = fine_skew(out)
    # signed normalization to (−180, 180] so a near-upright page reports ≈0,
    # not ≈360 (a bare `% 360` wraps small negative residuals up to ~359°).
    total = ((q + angle + 180.0) % 360.0) - 180.0
    return out, float(total)
