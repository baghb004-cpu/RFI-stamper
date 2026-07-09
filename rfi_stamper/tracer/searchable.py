"""Searchable-layer writer: raster + invisible OCR text, raster provably intact.

Each page that needs OCR is rebuilt as a brand-new **/Rotate 0** page sized to
the pixmap (``pixmap / (dpi/72)`` points), the raster is placed to fill it, and
one **invisible** text run (``render_mode=3``) is written per recognized word,
anchored at the word's bottom-left baseline in page points.  Rebuilding at
/Rotate 0 with the raster placed full-page means the invisible text lives in the
same upright pixel→point frame as the image, so no rotation transform is needed
here (if we ever wrote onto a rotated *original* we would reuse
``stamp._viewer_to_media`` rather than re-derive it — the CLAUDE.md gotcha).

Guarantees mirrored from ``ocr.py`` and asserted by the tests:
* the input file is never mutated;
* pages already carrying real text are copied through byte-for-byte
  (``skip_text_pages``);
* page count and every page rectangle are preserved (within 1 pt);
* the save is atomic (``out+".part"`` → flush → ``os.fsync`` → ``os.replace``);
* a ``verify.py``-style pixel-diff proves the OCR'd raster is unchanged — only
  invisible text was added.
"""
from __future__ import annotations

import os

import numpy as np
import fitz

# fitz Helvetica cap-height as a fraction of font size — used to size the
# invisible run so its cap height ≈ the imaged word height.
_CAP_RATIO = 0.717
_MIN_CHARS = 12
_VERIFY_DIFF = 25         # gray-level change that counts as a real difference
_VERIFY_MAX_FRAC = 0.002  # ≤ 0.2% of pixels may differ (anti-aliasing slack)


def _visible(text: str) -> str:
    return "".join(text.split())


def _page_has_text(page, min_chars: int = _MIN_CHARS) -> bool:
    return len(_visible(page.get_text("text"))) >= min_chars


def _atomic_save_doc(doc, out_path: str) -> None:
    """House atomic write: temp beside target, fsync, then os.replace."""
    tmp = out_path + ".part"
    doc.save(tmp, garbage=3, deflate=True)
    fd = os.open(tmp, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, out_path)


def _gray_of_page(page, dpi: int):
    """Render ``page`` to a grayscale pixmap; return (pix, gray ndarray)."""
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, alpha=False)
    gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).copy()
    return pix, gray


def _verify_only_text(src_page, out_page, dpi: int) -> dict:
    """Pixel-diff the source raster against the rebuilt page's raster.

    Invisible text must not change any rendered pixel; a handful of anti-alias
    pixels at image edges are tolerated.  Returns ``{"ok", "frac", "changed"}``.
    """
    _, a = _gray_of_page(src_page, dpi)
    _, b = _gray_of_page(out_page, dpi)
    if a.shape != b.shape:
        h = min(a.shape[0], b.shape[0])
        w = min(a.shape[1], b.shape[1])
        a, b = a[:h, :w], b[:h, :w]
    changed = int((np.abs(a.astype(np.int16) - b.astype(np.int16)) > _VERIFY_DIFF).sum())
    frac = changed / float(a.size) if a.size else 0.0
    return {"ok": frac <= _VERIFY_MAX_FRAC, "frac": frac, "changed": changed}


def write_searchable(in_pdf: str, out_pdf: str, dpi: int = 300,
                     skip_text_pages: bool = True, log=print, ctx=None) -> dict:
    """Write a searchable copy of ``in_pdf`` to ``out_pdf`` (see module docstring).

    Returns ``{"pages_ocred", "pages_total", "out_path"}``.  Delegates the
    per-page reading to the package pipeline (``read_image``); imported late to
    avoid an import cycle with the package facade.  The optional ``ctx`` is the
    P3 post-correction Context (``None`` → identical to P2).
    """
    from . import read_image                    # late: breaks the import cycle

    zoom = dpi / 72.0
    src = fitz.open(in_pdf)
    verify_reports = []
    try:
        total = src.page_count
        out = fitz.open()
        try:
            pages_ocred = 0
            for i in range(total):
                page = src[i]
                if skip_text_pages and _page_has_text(page):
                    out.insert_pdf(src, from_page=i, to_page=i)
                    log(f"  = page {i + 1}/{total}: has text, copied unchanged")
                    continue
                pix, gray = _gray_of_page(page, dpi)
                pw, ph = pix.width / zoom, pix.height / zoom
                new = out.new_page(width=pw, height=ph)          # /Rotate 0
                new.insert_image(new.rect, pixmap=pix)
                words = read_image(gray, dpi=dpi, ctx=ctx)
                for (x0, y0, x1, y1, text, _score) in words:
                    if not text:
                        continue
                    # pixel → point (raster placed at zoom on a /Rotate 0 page)
                    px0, py0 = x0 / zoom, y0 / zoom
                    py1 = y1 / zoom
                    cap_pts = max(1.0, (py1 - py0))
                    fs = min(1000.0, cap_pts / _CAP_RATIO)
                    new.insert_text((px0, py1), text, fontname="helv",
                                    fontsize=fs, render_mode=3)
                rep = _verify_only_text(page, out[out.page_count - 1], 90)
                verify_reports.append((i + 1, rep))
                if not rep["ok"]:
                    log(f"  ! page {i + 1}: verify raster drift frac={rep['frac']:.4f}")
                pages_ocred += 1
                log(f"  + page {i + 1}/{total}: OCR'd at {dpi} dpi "
                    f"({len(words)} words)")
            _atomic_save_doc(out, out_pdf)
        finally:
            out.close()
    finally:
        src.close()
    log(f"  wrote {out_pdf} ({pages_ocred}/{total} page(s) OCR'd)")
    return {"pages_ocred": pages_ocred, "pages_total": total,
            "out_path": out_pdf, "verify": verify_reports}
