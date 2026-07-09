"""Synthetic training-corpus generator — no downloads, fully offline.

The classifier is trained on glyphs synthesized in-process (OCR_PLAN §3): each
CHARSET class is rendered across the two base-14 outline faces and the four
single-stroke Hershey styles (Type A/B × 0°/15°) at several cap-heights, then
each clean render is pushed through a **Kanungo/Baird degradation grid** — all
pure numpy — so the model sees the blur, toner spread, sensor noise, skew,
illumination and JPEG-blocking a real scan carries.  Holding out fonts and the
degradation-seed stream keeps train and eval disjoint.

Degradations (each seeded, OCR_PLAN §3/§5): Gaussian blur σ 0.4–1.5 px; additive
Gaussian noise σ 4–18 gray + salt&pepper 0.1–0.8%; 3×3 erode/dilate (toner gain
/loss); affine skew ±2.5° + shear + sub-pixel jitter; low-frequency background
gradient; 8×8 DCT block quantization; random binarization threshold.

``calibrate(gray_page)`` measures stroke width / blur / noise off a real page so
call sites can bias the augmentation toward the user's actual scans (closes the
synthetic-to-real gap of OCR_PLAN §3).  Everything is deterministic: the only
randomness is a numpy Generator seeded per variant.
"""
from __future__ import annotations

from typing import NamedTuple

import numpy as np

from . import binarize, fonts
from .normalize import norm_glyph


# --------------------------------------------------------------------------- #
#  Degradation primitives (pure numpy, deterministic given the rng)           #
# --------------------------------------------------------------------------- #

def _gauss_kernel(sigma: float) -> np.ndarray:
    r = max(1, int(np.ceil(3 * sigma)))
    x = np.arange(-r, r + 1)
    k = np.exp(-0.5 * (x / sigma) ** 2)
    return k / k.sum()


def blur(gray: np.ndarray, sigma: float) -> np.ndarray:
    """Separable Gaussian blur (edge-padded 1-D convolutions)."""
    if sigma <= 0.05:
        return gray.astype(np.float64)
    k = _gauss_kernel(sigma)
    r = len(k) // 2
    g = gray.astype(np.float64)
    p = np.pad(g, ((0, 0), (r, r)), mode="edge")
    out = np.zeros_like(g)
    for i, w in enumerate(k):
        out += w * p[:, i:i + g.shape[1]]
    p2 = np.pad(out, ((r, r), (0, 0)), mode="edge")
    res = np.zeros_like(g)
    for i, w in enumerate(k):
        res += w * p2[i:i + g.shape[0], :]
    return res


def _morph(gray: np.ndarray, dilate_ink: bool) -> np.ndarray:
    """3×3 grayscale erosion/dilation (toner gain/loss on dark ink)."""
    g = gray.astype(np.float64)
    p = np.pad(g, 1, mode="edge")
    stack = np.stack([p[dy:dy + g.shape[0], dx:dx + g.shape[1]]
                      for dy in range(3) for dx in range(3)], axis=0)
    # dark = ink; growing ink means taking the local MIN (darker wins)
    return stack.min(0) if dilate_ink else stack.max(0)


def add_noise(gray, sigma_gray, sp_frac, rng) -> np.ndarray:
    g = gray.astype(np.float64)
    if sigma_gray > 0:
        g = g + rng.normal(0.0, sigma_gray, g.shape)
    if sp_frac > 0:
        m = rng.random(g.shape)
        g = np.where(m < sp_frac / 2.0, 0.0, g)
        g = np.where(m > 1.0 - sp_frac / 2.0, 255.0, g)
    return np.clip(g, 0, 255)


def affine(gray, angle_deg, shear, jitter, rng) -> np.ndarray:
    """Small rotation + horizontal shear + sub-pixel jitter, bilinear, white fill."""
    H, W = gray.shape
    cy, cx = (H - 1) / 2.0, (W - 1) / 2.0
    th = np.deg2rad(angle_deg)
    cos, sin = np.cos(th), np.sin(th)
    jy = rng.uniform(-jitter, jitter)
    jx = rng.uniform(-jitter, jitter)
    ys, xs = np.mgrid[0:H, 0:W]
    dy = ys - cy - jy
    dx = xs - cx - jx
    # inverse map (dest -> source): undo rotation then shear
    sx = cos * dx + sin * dy
    sy = -sin * dx + cos * dy
    sx = sx - shear * sy
    src_x = sx + cx
    src_y = sy + cy
    x0 = np.floor(src_x).astype(int)
    y0 = np.floor(src_y).astype(int)
    wx = src_x - x0
    wy = src_y - y0
    g = gray.astype(np.float64)

    def samp(yy, xx):
        ok = (yy >= 0) & (yy < H) & (xx >= 0) & (xx < W)
        v = np.full((H, W), 255.0)
        yc = np.clip(yy, 0, H - 1)
        xc = np.clip(xx, 0, W - 1)
        v[ok] = g[yc[ok], xc[ok]]
        return v

    top = samp(y0, x0) * (1 - wx) + samp(y0, x0 + 1) * wx
    bot = samp(y0 + 1, x0) * (1 - wx) + samp(y0 + 1, x0 + 1) * wx
    return np.clip(top * (1 - wy) + bot * wy, 0, 255)


def bg_gradient(gray, rng, strength=40.0) -> np.ndarray:
    """Add a low-frequency (linear) brightness ramp (uneven illumination)."""
    H, W = gray.shape
    gy = rng.uniform(-1, 1)
    gx = rng.uniform(-1, 1)
    ys = np.linspace(-1, 1, H)[:, None]
    xs = np.linspace(-1, 1, W)[None, :]
    ramp = strength * (gy * ys + gx * xs)
    return np.clip(gray.astype(np.float64) + ramp, 0, 255)


_DCT8 = None


def _dct_matrix(n=8) -> np.ndarray:
    k = np.arange(n)
    D = np.cos(np.pi * (2 * k[None, :] + 1) * k[:, None] / (2 * n))
    D *= np.sqrt(2.0 / n)
    D[0, :] *= 1 / np.sqrt(2)
    return D


def dct_block(gray, q, rng) -> np.ndarray:
    """8×8 block DCT quantization (JPEG-style blocking artefact)."""
    global _DCT8
    if _DCT8 is None:
        _DCT8 = _dct_matrix(8)
    D = _DCT8
    g = gray.astype(np.float64) - 128.0
    H, W = g.shape
    ph = (-H) % 8
    pw = (-W) % 8
    gp = np.pad(g, ((0, ph), (0, pw)), mode="edge")
    Hp, Wp = gp.shape
    blocks = gp.reshape(Hp // 8, 8, Wp // 8, 8).transpose(0, 2, 1, 3)
    C = np.einsum("ij,abjk,lk->abil", D, blocks, D, optimize=True)
    C = np.round(C / q) * q
    R = np.einsum("ji,abjk,kl->abil", D, C, D, optimize=True)
    out = R.transpose(0, 1, 2, 3).reshape(Hp // 8, Wp // 8, 8, 8)
    out = out.transpose(0, 2, 1, 3).reshape(Hp, Wp)
    return np.clip(out[:H, :W] + 128.0, 0, 255)


def _binarize_rand(gray, rng, severity: int = 2) -> np.ndarray:
    """Otsu threshold nudged by a small random offset → ink boolean."""
    t, _ = binarize.otsu(np.clip(gray, 0, 255).astype(np.uint8))
    off = rng.uniform(-6, 6) if severity == 0 else rng.uniform(-14, 14)
    return np.clip(gray, 0, 255) < (t + off)


# --------------------------------------------------------------------------- #
#  Calibration hook                                                           #
# --------------------------------------------------------------------------- #

def calibrate(gray_page: np.ndarray) -> dict:
    """Measure stroke width / blur / noise off a real page to bias augmentation.

    Returns ``{"stroke_px", "blur_sigma", "noise_sigma"}``.  Stroke width is the
    median horizontal ink-run length; noise is the paper-region std; blur is a
    coarse edge-sharpness proxy (inverse of the mean edge gradient).  Optional
    at call sites — passed to :func:`corpus` as ``calib=`` to center the grid.
    """
    g = np.asarray(gray_page)
    t, ink = binarize.otsu(g.astype(np.uint8))
    # stroke width via horizontal run lengths
    runs = []
    padded = np.zeros((ink.shape[0], ink.shape[1] + 2), np.int8)
    padded[:, 1:-1] = ink.astype(np.int8)
    d = np.diff(padded, axis=1)
    st = np.argwhere(d == 1)
    en = np.argwhere(d == -1)
    if st.size:
        lens = (en[:, 1] - st[:, 1])
        lens = lens[(lens >= 1) & (lens <= 12)]
        if lens.size:
            runs = lens
    stroke = float(np.median(runs)) if len(runs) else 3.0
    paper = g[g > t]
    noise = float(paper.std()) if paper.size else 5.0
    gx = np.abs(np.diff(g.astype(np.float64), axis=1))
    edge = gx[gx > gx.mean() + gx.std()]
    sharp = float(edge.mean()) if edge.size else 60.0
    blur_sigma = float(np.clip(1.4 - sharp / 120.0, 0.3, 1.4))
    return {"stroke_px": stroke, "blur_sigma": blur_sigma, "noise_sigma": noise}


# --------------------------------------------------------------------------- #
#  Glyph sources with baseline-relative vertical position                     #
# --------------------------------------------------------------------------- #

def _hershey_rel_y(ch: str) -> float:
    """Analytic baseline-relative center (image y-down, cap=0..baseline=1)."""
    strokes = fonts.HERSHEY.get(ch)
    if not strokes:
        return 0.5
    ys = [p[1] for s in strokes for p in s]
    c = (min(ys) + max(ys)) / 2.0
    return float((fonts._GRID_CAP - c) / fonts._GRID_CAP)


def _fitz_sources(ch: str, cap: int):
    """Base-14 renders with analytic rel_y from the insert_text baseline."""
    import fitz
    fs = max(8, int(round(cap / 0.70)))
    base_row = fs * 2                      # insert_text baseline
    cap_row = base_row - 0.717 * fs
    span = max(1.0, base_row - cap_row)
    out = []
    for face in fonts._FACES:
        doc = fitz.open()
        try:
            page = doc.new_page(width=fs * 3, height=fs * 3)
            page.insert_text((fs, base_row), ch, fontname=face, fontsize=fs)
            pix = page.get_pixmap(colorspace=fitz.csGRAY, alpha=False)
            g = np.frombuffer(pix.samples, np.uint8).reshape(
                pix.height, pix.width)
        finally:
            doc.close()
        ink = g < 250
        if not ink.any():
            continue
        ys, xs = np.where(ink)
        rel = float(((ys.min() + ys.max()) / 2.0 - cap_row) / span)
        crop = g[ys.min():ys.max() + 1, xs.min():xs.max() + 1].copy()
        out.append((face, crop, float(np.clip(rel, -0.2, 1.2))))
    return out


def glyph_sources(ch: str, sizes):
    """All (font_id, clean_gray, rel_y) sources for a class across sizes."""
    out = []
    rel_h = _hershey_rel_y(ch)
    for cap in sizes:
        out.extend(_fitz_sources(ch, cap))
        for fid in ("herA0", "herA15", "herB0", "herB15"):
            g = fonts._hershey_gray(ch, cap, fid)
            if g is not None:
                out.append((fid, g, rel_h))
    return out


# --------------------------------------------------------------------------- #
#  The corpus                                                                 #
# --------------------------------------------------------------------------- #

class Corpus(NamedTuple):
    cells: np.ndarray      # (N, CELL, CELL) float32
    aspect: np.ndarray     # (N,) float32
    rel_y: np.ndarray      # (N,) float32
    y: np.ndarray          # (N,) int  (index into charset)
    font: np.ndarray       # (N,) int  (index into FONT_IDS)
    severity: np.ndarray   # (N,) int  0 clean · 1 mild · 2 harsh
    is_test: np.ndarray    # (N,) bool held-out split
    charset: str
    fonts: tuple


def _degrade_variant(gray, rng, severity: int, calib) -> np.ndarray:
    """Apply a graded Kanungo/Baird degradation → ink boolean (True = ink).

    ``severity`` 0 = clean (legible; the ≥99% self-classification tier), 1 =
    mild scan wear, 2 = the full harsh photocopy grid (the robustness tier the
    ensemble must beat NCC on).
    """
    g = gray.astype(np.float64)
    if severity >= 1:
        amax = 1.5 if severity == 1 else 2.5
        if rng.random() < (0.4 if severity == 1 else 0.75):
            g = affine(g, rng.uniform(-amax, amax),
                       rng.uniform(-0.06, 0.06) if severity == 1
                       else rng.uniform(-0.12, 0.12), 0.7, rng)
        if severity == 2 and rng.random() < 0.4:
            g = bg_gradient(g, rng, strength=rng.uniform(15, 45))
    if severity == 0:
        sigma = rng.uniform(0.3, 0.55)
    elif severity == 1:
        sigma = float(np.clip(rng.normal(calib["blur_sigma"] if calib else 0.7,
                                         0.25), 0.3, 1.05))
    else:
        sigma = float(np.clip(rng.normal(calib["blur_sigma"] if calib else 0.9,
                                         0.35), 0.3, 1.6))
    g = blur(g, sigma)
    if severity >= 1 and rng.random() < (0.35 if severity == 1 else 0.55):
        g = _morph(g, dilate_ink=rng.random() < 0.5)
    if severity == 0:
        nsig, spf = rng.uniform(1, 3), 0.0
    elif severity == 1:
        nsig, spf = rng.uniform(3, 10), rng.uniform(0.0, 0.003)
    else:
        nc = calib["noise_sigma"] if calib else 12.0
        nsig = float(np.clip(rng.normal(nc, 5.0), 4, 20))
        spf = rng.uniform(0.0, 0.008)
    g = add_noise(g, nsig, spf, rng)
    if severity == 2 and rng.random() < 0.4:
        g = dct_block(g, q=rng.uniform(8, 30), rng=rng)
    return _binarize_rand(g, rng, severity)


def corpus(seed: int = 0, per_class: int = 160, sizes=(20, 30, 44),
           holdout_frac: float = 0.2, exclude_fonts=frozenset(),
           calib=None) -> Corpus:
    """Generate the deterministic labeled corpus (see module docstring).

    ``exclude_fonts`` (a set of FONT_IDS) drops those faces entirely — used to
    demonstrate genuine font holdout.  ``holdout_frac`` of the variants become
    the disjoint eval split (``is_test``), assigned by a seeded per-variant
    stream so train and test never share a rendered glyph.
    """
    charset = fonts.CHARSET
    fid_index = {f: i for i, f in enumerate(fonts.FONT_IDS)}
    cells, asp, rely, ys, fonts_, sev, tst = [], [], [], [], [], [], []
    for ci, ch in enumerate(charset):
        srcs = [(fid, g, r) for (fid, g, r) in glyph_sources(ch, sizes)
                if fid not in exclude_fonts]
        if not srcs:
            continue
        reps = max(3, int(round(per_class / len(srcs))))
        n_clean = max(1, int(round(reps * 0.22)))
        n_mild = max(1, int(round(reps * 0.25)))
        for bi, (fid, g, rel) in enumerate(srcs):
            for rep in range(reps):
                rng = np.random.default_rng([seed, ci, bi, rep])
                severity = (0 if rep < n_clean
                            else 1 if rep < n_clean + n_mild else 2)
                ink = _degrade_variant(g, rng, severity, calib)
                if ink.sum() < 3:
                    ink = _binarize_rand(blur(g.astype(np.float64), 0.5), rng)
                    severity = min(severity, 1)
                if ink.sum() < 2:
                    continue
                ng = norm_glyph(ink)
                cells.append(ng.cell)
                asp.append(ng.aspect)
                rely.append(rel)
                ys.append(ci)
                fonts_.append(fid_index[fid])
                sev.append(severity)
                # deterministic held-out assignment
                is_test = np.random.default_rng([seed, ci, bi, rep, 99]) \
                    .random() < holdout_frac
                tst.append(bool(is_test))
    return Corpus(
        cells=np.stack(cells).astype(np.float32),
        aspect=np.asarray(asp, np.float32),
        rel_y=np.asarray(rely, np.float32),
        y=np.asarray(ys, np.int64),
        font=np.asarray(fonts_, np.int64),
        severity=np.asarray(sev, np.int64),
        is_test=np.asarray(tst, bool),
        charset=charset,
        fonts=fonts.FONT_IDS,
    )
