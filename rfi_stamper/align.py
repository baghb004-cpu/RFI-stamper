"""Automatic overlay-compare of two drawing PDFs: deterministic raster alignment
(FFT phase correlation + coarse rotation sweep) and colored diff rendering.

Convention (shared by auto_align, comparison_image, make_comparison_pdf):
render both pages top-left origin, y down.  The overlay is rendered with
``AlignResult.rotation`` (degrees, fitz.Matrix prerotation about the overlay
page center; positive follows the fitz convention, i.e. CCW in PDF y-up terms)
and then shifted by (dx, dy) points — dx positive right, dy positive down —
so that it lands on the base render.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import fitz

DARK_THRESH = 200      # gray < this counts as drawn linework
MIN_DARK_PX = 20       # fewer dark pixels than this -> page treated as blank
SWEEP_DPI = 40         # low-res dpi for the coarse rotation sweep
SWEEP_MAX_DEG = 6.0
SWEEP_STEP_DEG = 0.5


@dataclass
class AlignResult:
    dx: float = 0.0        # pt: shift applied to overlay content so it lands on base
    dy: float = 0.0        # pt, positive = down (top-left origin, matching renders)
    rotation: float = 0.0  # degrees, rotation of overlay about its page center
    score: float = 0.0     # 0..1 normalized phase-correlation peak confidence


def render_page_gray(path: str, page_no: int = 1, dpi: int = 110,
                     rotation: float = 0.0) -> np.ndarray:
    """uint8 HxW grayscale render; rotation applied as a fitz.Matrix prerotation
    about the page center before scaling to the requested dpi."""
    doc = fitz.open(path)
    page = doc[page_no - 1]
    scale = dpi / 72.0
    mat = fitz.Matrix(scale, scale)
    if rotation:
        cx, cy = page.rect.width / 2.0, page.rect.height / 2.0
        mat = (fitz.Matrix(1, 0, 0, 1, -cx, -cy)
               * fitz.Matrix(rotation)
               * fitz.Matrix(1, 0, 0, 1, cx, cy)
               * mat)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()
    doc.close()
    return img


def _is_blank(gray: np.ndarray) -> bool:
    return (gray.shape[0] < 8 or gray.shape[1] < 8
            or int((gray < DARK_THRESH).sum()) < MIN_DARK_PX)


def _pad_to(img: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    out = np.zeros(shape, np.float32)
    out[:img.shape[0], :img.shape[1]] = img
    return out


def _signal(gray: np.ndarray) -> np.ndarray:
    return (255.0 - gray.astype(np.float32))


def _hann(shape: tuple[int, int]) -> np.ndarray:
    return (np.hanning(shape[0])[:, None] * np.hanning(shape[1])[None, :]).astype(np.float32)


def _fast_len(n: int) -> int:
    """Smallest 5-smooth integer >= n (fast FFT length; numpy lacks next_fast_len).
    Zero-padding to this size keeps phase correlation exact while avoiding the
    catastrophic FFT slowdown of sizes with large prime factors."""
    while True:
        m = n
        for p in (2, 3, 5):
            while m % p == 0:
                m //= p
        if m == 1:
            return n
        n += 1


def _peak_subpixel(r: np.ndarray) -> tuple[float, float, float]:
    """Locate the correlation peak with per-axis parabolic subpixel refinement.
    Returns (tx, ty, peak) with wrap-around resolved to signed shifts."""
    H, W = r.shape
    py, px = np.unravel_index(int(np.argmax(r)), r.shape)
    peak = float(r[py, px])

    def fit(m, c, p):
        d = m - 2.0 * c + p
        if abs(d) < 1e-12:
            return 0.0
        return float(np.clip(0.5 * (m - p) / d, -1.0, 1.0))

    ty = py + fit(float(r[(py - 1) % H, px]), peak, float(r[(py + 1) % H, px]))
    tx = px + fit(float(r[py, (px - 1) % W]), peak, float(r[py, (px + 1) % W]))
    if ty > H / 2.0:
        ty -= H
    if tx > W / 2.0:
        tx -= W
    return tx, ty, peak


def _corr_peak(Fa: np.ndarray, win: np.ndarray, b: np.ndarray,
               shape: tuple[int, int]) -> tuple[float, float, float]:
    Fb = np.fft.rfft2(b * win)
    R = Fa * np.conj(Fb)
    R /= np.maximum(np.abs(R), 1e-9)
    r = np.fft.irfft2(R, s=shape)
    return _peak_subpixel(r)


def _phase_correlate(a: np.ndarray, b: np.ndarray) -> tuple[float, float, float]:
    """(tx, ty, score) in px such that shifting b by (tx, ty) lands it on a."""
    win = _hann(a.shape)
    return _corr_peak(np.fft.rfft2(a * win), win, b, a.shape)


def _best_rotation(base_path: str, overlay_path: str,
                   base_page: int, overlay_page: int) -> float:
    """Coarse sweep of overlay rotation at low dpi; ties favor smaller angles."""
    base = render_page_gray(base_path, base_page, SWEEP_DPI)
    if _is_blank(base):
        return 0.0
    n = int(round(SWEEP_MAX_DEG / SWEEP_STEP_DEG))
    angles = sorted((i * SWEEP_STEP_DEG for i in range(-n, n + 1)), key=abs)
    renders = [render_page_gray(overlay_path, overlay_page, SWEEP_DPI, rotation=a)
               for a in angles]
    H = _fast_len(max(base.shape[0], *(r.shape[0] for r in renders)))
    W = _fast_len(max(base.shape[1], *(r.shape[1] for r in renders)))
    a_sig = _pad_to(_signal(base), (H, W))
    win = _hann((H, W))
    Fa = np.fft.rfft2(a_sig * win)
    best_angle, best_score = 0.0, -1.0
    for ang, ov in zip(angles, renders):
        if _is_blank(ov):
            continue
        _, _, score = _corr_peak(Fa, win, _pad_to(_signal(ov), (H, W)), (H, W))
        if score > best_score:
            best_angle, best_score = ang, score
    return best_angle


def auto_align(base_path: str, overlay_path: str, base_page: int = 1,
               overlay_page: int = 1, dpi: int = 72,
               try_rotation: bool = True) -> AlignResult:
    """Estimate rotation + translation that maps the overlay render onto the base
    render (see module docstring for the convention).  Blank/tiny pages yield the
    zero result with score 0."""
    base = render_page_gray(base_path, base_page, dpi)
    over = render_page_gray(overlay_path, overlay_page, dpi)
    if _is_blank(base) or _is_blank(over):
        return AlignResult()
    rot = 0.0
    if try_rotation:
        rot = _best_rotation(base_path, overlay_path, base_page, overlay_page)
        if rot:
            over = render_page_gray(overlay_path, overlay_page, dpi, rotation=rot)
    shape = (_fast_len(max(base.shape[0], over.shape[0])),
             _fast_len(max(base.shape[1], over.shape[1])))
    tx, ty, score = _phase_correlate(_pad_to(_signal(base), shape),
                                     _pad_to(_signal(over), shape))
    k = 72.0 / dpi
    return AlignResult(dx=tx * k, dy=ty * k, rotation=rot,
                       score=float(np.clip(score, 0.0, 1.0)))


def comparison_image(base_path: str, overlay_path: str, base_page: int = 1,
                     overlay_page: int = 1, align: AlignResult | None = None,
                     dpi: int = 110, base_color=(200, 30, 30),
                     overlay_color=(30, 80, 200)) -> np.ndarray:
    """HxWx3 uint8 diff on the base render's canvas: white background, base-only
    linework in base_color, overlay-only (after align) in overlay_color, and
    pixels dark in both near-black."""
    a = align or AlignResult()
    base = render_page_gray(base_path, base_page, dpi)
    over = render_page_gray(overlay_path, overlay_page, dpi, rotation=a.rotation)
    scale = dpi / 72.0
    sx, sy = int(round(a.dx * scale)), int(round(a.dy * scale))
    H, W = base.shape
    canvas = np.full((H, W), 255, np.uint8)
    h, w = over.shape
    y0, y1 = max(0, sy), min(H, h + sy)
    x0, x1 = max(0, sx), min(W, w + sx)
    if y1 > y0 and x1 > x0:
        canvas[y0:y1, x0:x1] = over[y0 - sy:y1 - sy, x0 - sx:x1 - sx]
    bdark = base < DARK_THRESH
    odark = canvas < DARK_THRESH
    out = np.full((H, W, 3), 255, np.uint8)
    out[bdark & ~odark] = base_color
    out[odark & ~bdark] = overlay_color
    out[bdark & odark] = (40, 40, 40)
    return out


def make_comparison_pdf(base_path: str, overlay_path: str, out_path: str,
                        base_page: int = 1, overlay_page: int = 1,
                        align: AlignResult | None = None, dpi: int = 150,
                        log=print) -> None:
    """Write a one-page PDF (page size = base page's pt size) embedding the
    comparison image full-page."""
    img = comparison_image(base_path, overlay_path, base_page, overlay_page,
                           align=align, dpi=dpi)
    src = fitz.open(base_path)
    rect = src[base_page - 1].rect
    src.close()
    H, W = img.shape[:2]
    pix = fitz.Pixmap(fitz.csRGB, W, H, np.ascontiguousarray(img).tobytes(), False)
    doc = fitz.open()
    page = doc.new_page(width=rect.width, height=rect.height)
    page.insert_image(page.rect, pixmap=pix)
    doc.save(out_path)
    doc.close()
    log(f"  comparison written: {out_path} ({W}x{H}px @ {dpi}dpi, "
        f"page {rect.width:.0f}x{rect.height:.0f}pt)")
