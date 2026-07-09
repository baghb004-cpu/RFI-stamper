"""Ink/paper separation for the Tracer.

The Tracer reads lettering off a scanned sheet; before anything else it must
decide, per pixel, "is this ink or is this paper?".  Two thresholders cover
the two regimes construction scans arrive in:

* **Global Otsu** for *flat* rasters (vector-derived prints, clean plots) — one
  histogram split, fast and stable.
* **Local Sauvola** for *uneven* rasters (diazo/blueline fades, photocopies,
  camera captures) where a single cut clips faded strokes or floods shadows.

A cheap **flatness score** (std of large-window local means) routes between
them so we never pay Sauvola's cost on a clean sheet nor Otsu's fragility on a
stained one.  Convention throughout the package: the returned boolean array is
``True`` where there is **ink** (dark).  Constants are sourced from
OCR_PLAN.md §5 (Otsu 256-bin/1-pass; Sauvola window 15, k=0.2, R=128).
"""
from __future__ import annotations

import numpy as np

# --- OCR_PLAN §5 constants --------------------------------------------------
SAUVOLA_WINDOW = 15      # px @300 dpi (≈ 2–3× stroke width); odd
SAUVOLA_K = 0.2          # 0.2 clean; raise toward 0.34–0.5 for stained scans
SAUVOLA_R = 128.0        # dynamic range of the local std (8-bit midpoint)
FLATNESS_FLAT = 12.0     # paper-brightness std below which a sheet is "flat"


def _integral(a: np.ndarray) -> np.ndarray:
    """Summed-area table with a zero row/column pad (shape (H+1, W+1))."""
    return np.pad(np.cumsum(np.cumsum(a.astype(np.float64), 0), 1), ((1, 0), (1, 0)))


def _local_mean_std(gray: np.ndarray, win: int):
    """Per-pixel windowed mean and std via two integral images (O(H·W)).

    Windows are clamped at the borders (variable area), which is what keeps
    Sauvola honest near the sheet edge instead of leaking the pad value in.
    """
    H, W = gray.shape
    r = win // 2
    g = gray.astype(np.float64)
    I = _integral(g)
    I2 = _integral(g * g)
    ys = np.arange(H)
    xs = np.arange(W)
    y0 = np.clip(ys - r, 0, H)
    y1 = np.clip(ys + r + 1, 0, H)
    x0 = np.clip(xs - r, 0, W)
    x1 = np.clip(xs + r + 1, 0, W)

    def rect(Im):
        A = Im[y1[:, None], x1[None, :]]
        B = Im[y0[:, None], x1[None, :]]
        C = Im[y1[:, None], x0[None, :]]
        D = Im[y0[:, None], x0[None, :]]
        return A - B - C + D

    area = (y1 - y0)[:, None] * (x1 - x0)[None, :]
    s = rect(I)
    s2 = rect(I2)
    mean = s / area
    var = np.maximum(s2 / area - mean * mean, 0.0)
    return mean, np.sqrt(var)


def otsu(gray: np.ndarray):
    """256-bin, single-pass, maximum between-class-variance threshold.

    Returns ``(thresh, ink_bool)`` with ``ink = gray < thresh`` (dark is ink).
    The classic Otsu recurrence: sweep every cut, track the weight/mean of the
    two classes incrementally, and keep the cut that maximises
    ``w0·w1·(µ0 − µ1)²``.
    """
    g = np.asarray(gray, dtype=np.uint8)
    hist = np.bincount(g.ravel(), minlength=256).astype(np.float64)
    total = g.size
    w0 = np.cumsum(hist)                       # pixels with value <= t
    w1 = total - w0
    levels = np.arange(256, dtype=np.float64)
    csum = np.cumsum(hist * levels)
    total_mean = csum[-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        m0 = csum / w0
        m1 = (total_mean - csum) / w1
        between = w0 * w1 * (m0 - m1) ** 2
    between[~np.isfinite(between)] = -1.0
    t = int(np.argmax(between))
    # ink = the dark class (value <= t).  On a perfectly bimodal histogram the
    # between-class variance is flat across the whole gap between the two modes
    # and argmax lands on the dark mode itself, so the comparison must be `<=`
    # for that dark mode to count as ink.
    return t, g <= t


def sauvola(gray: np.ndarray, window: int = SAUVOLA_WINDOW,
            k: float = SAUVOLA_K, R: float = SAUVOLA_R):
    """Local adaptive threshold ``T = m·(1 + k·(s/R − 1))`` (Sauvola 2000).

    ``m`` and ``s`` are the local mean and std over ``window``.  On faded or
    unevenly lit scans this tracks the paper locally, so a stroke that is only
    slightly darker than its own neighbourhood is still caught while a bright
    but noisy background is not.  Returns an ink boolean (``gray < T``).
    """
    if window % 2 == 0:
        window += 1
    m, s = _local_mean_std(gray, window)
    T = m * (1.0 + k * (s / R - 1.0))
    return np.asarray(gray, dtype=np.float64) < T


def flatness(gray: np.ndarray) -> float:
    """Illumination-flatness score = spread (std) of the *paper* brightness.

    The score must reflect how evenly the sheet is lit, **not** whether it
    carries ink — so we first drop the ink (pixels darker than the global Otsu
    cut) and measure the std of what remains, the paper.  A clean, evenly-lit
    raster has a razor-thin paper peak (near-zero std); a vignetted or shadowed
    scan spreads its paper across many gray levels (large std).  Measuring the
    background directly is what stops a text-heavy clean sheet from being
    misrouted to Sauvola, whose adaptive threshold hollows out the cores of
    thick strokes when there is no local contrast.  Used only to route the
    binarizer, never to threshold.
    """
    t, _ = otsu(gray)
    g = np.asarray(gray)
    paper = g[g > t]
    if paper.size == 0:
        return 0.0
    return float(paper.std())


def binarize(gray: np.ndarray):
    """Flatness-routed binarization: flat → Otsu, uneven → Sauvola.

    Returns the ink boolean array (``True`` = ink).  Routing keeps the fast,
    globally-stable path for the clean vector-derived rasters that are the P1
    target (and preserves solid strokes Otsu never hollows) while still
    recovering degraded, unevenly-lit scans with Sauvola — no code change.
    """
    if flatness(gray) < FLATNESS_FLAT:
        return otsu(gray)[1]
    return sauvola(gray)
