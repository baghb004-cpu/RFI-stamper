"""Self-contained test for the offline OCR text-layer module.

Builds a synthetic two-page PDF: page 1 is a real text page, page 2 is the same
words rasterized to an image with NO text layer (a stand-in for a scanned
sheet).  Exercises the whole public API against the local Tesseract engine.

No project data or network needed.  Run:  python3.12 tests/test_ocr.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz                                             # noqa: E402

from rfi_stamper import ocr                             # noqa: E402

# Distinctive tokens we expect to survive the OCR round-trip.
SCAN_TEXT = "PLUMBING RISER DIAGRAM SHEET P-101"
TEXT_PAGE = "GENERAL NOTES AND LEGEND DRAWING G-001"
PAGE_W, PAGE_H = 612.0, 792.0        # US Letter, portrait


def _build_mixed_pdf(path: str) -> None:
    """Write a 2-page PDF: page 1 real text, page 2 image-only (scanned)."""
    doc = fitz.open()

    # Page 1: genuine, extractable text.
    p1 = doc.new_page(width=PAGE_W, height=PAGE_H)
    p1.insert_text((72, 120), TEXT_PAGE, fontsize=20)

    # Page 2: render the words to a pixmap in a scratch page, then place that
    # pixmap as an image on a fresh page so no text layer remains.
    scratch = fitz.open()
    sp = scratch.new_page(width=PAGE_W, height=PAGE_H)
    sp.insert_text((72, 120), SCAN_TEXT, fontsize=26)
    sp.insert_text((72, 200), "P-101", fontsize=40)     # large, easy for OCR
    pix = sp.get_pixmap(dpi=200)
    scratch.close()

    p2 = doc.new_page(width=PAGE_W, height=PAGE_H)
    p2.insert_image(p2.rect, pixmap=pix)

    doc.save(path)
    doc.close()


def _contains(haystack: str, needle: str) -> bool:
    """Case-insensitive, whitespace-insensitive substring check (OCR noise)."""
    norm = lambda s: "".join(s.split()).upper()
    return norm(needle) in norm(haystack)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="ocr_test_")
    src = os.path.join(tmp, "mixed.pdf")
    out = os.path.join(tmp, "mixed_ocr.pdf")
    _build_mixed_pdf(src)

    # --- sanity: the scanned page really has no text layer to begin with ----
    with fitz.open(src) as d:
        assert d.page_count == 2, "fixture should have 2 pages"
        scanned_text = "".join(d[1].get_text("text").split())
        assert len(scanned_text) < 12, (
            f"scanned page should start empty, got {scanned_text!r}")
        assert _contains(d[0].get_text("text"), "G-001"), "page 1 must have text"

    # --- engine availability -------------------------------------------------
    assert ocr.tesseract_available() is True, "Tesseract must be usable in this env"
    info = ocr.tesseract_info()
    assert info["available"] is True
    assert "eng" in info["langs"], f"eng data expected, got {info['langs']}"
    assert os.path.isdir(info["tessdata"]), "tessdata dir must exist"

    # --- needs_ocr -----------------------------------------------------------
    assert ocr.needs_ocr(src, page_no=2) is True, "scanned page needs OCR"
    assert ocr.needs_ocr(src, page_no=1) is False, "text page does not need OCR"
    assert ocr.needs_ocr(src) is True, "doc has a scanned page -> needs OCR"

    # --- ocr_page_text on the scanned page -----------------------------------
    page_text = ocr.ocr_page_text(src, 2)
    assert page_text.strip(), "OCR of scanned page must be non-empty"
    assert _contains(page_text, "P-101"), (
        f"expected 'P-101' in OCR text, got {page_text!r}")

    # --- ocr_pdf: build searchable copy --------------------------------------
    result = ocr.ocr_pdf(src, out, dpi=300, log=lambda *_: None)
    assert result["out_path"] == out
    assert result["pages_total"] == 2
    assert result["pages_ocred"] == 1, (
        f"only the scanned page should be OCR'd, got {result}")
    assert os.path.exists(out) and not os.path.exists(out + ".part"), \
        "atomic write must leave no .part file"

    # Input is never mutated.
    with fitz.open(src) as d:
        assert len("".join(d[1].get_text("text").split())) < 12, \
            "source file must not be modified"

    with fitz.open(out) as d:
        assert d.page_count == 2, "page count must be preserved"
        # Point-size preserved on both pages.
        for i in range(2):
            r = d[i].rect
            assert abs(r.width - PAGE_W) < 1.0 and abs(r.height - PAGE_H) < 1.0, \
                f"page {i} point-size drifted: {tuple(r)}"
        # skip_text_pages kept the original text page verbatim.
        assert _contains(d[0].get_text("text"), "G-001"), \
            "existing-text page must be preserved intact"
        # The scanned page is now searchable.
        assert _contains(d[1].get_text("text"), "P-101"), (
            f"OCR layer must be searchable, got {d[1].get_text('text')!r}")

    # --- skip_text_pages=False re-OCRs everything ----------------------------
    out2 = os.path.join(tmp, "mixed_ocr_all.pdf")
    r2 = ocr.ocr_pdf(src, out2, dpi=200, skip_text_pages=False, log=lambda *_: None)
    assert r2["pages_ocred"] == 2, f"both pages should be OCR'd, got {r2}"

    # --- OcrUnavailable when Tesseract data cannot be used -------------------
    orig = ocr._discover_tessdata
    ocr._SMOKE_CACHE.clear()
    try:
        ocr._discover_tessdata = lambda: None
        raised = False
        try:
            ocr.ocr_pdf(src, os.path.join(tmp, "never.pdf"), log=lambda *_: None)
        except ocr.OcrUnavailable:
            raised = True
        assert raised, "ocr_pdf must raise OcrUnavailable with no tessdata"

        raised = False
        try:
            ocr.ocr_page_text(src, 2)
        except ocr.OcrUnavailable:
            raised = True
        assert raised, "ocr_page_text must raise OcrUnavailable with no tessdata"

        # A bogus (present but unusable) folder must also fail cleanly.
        ocr._discover_tessdata = lambda: os.path.join(tmp, "not-tessdata")
        ocr._SMOKE_CACHE.clear()
        assert ocr.tesseract_available() is False, \
            "unusable tessdata -> not available"
    finally:
        ocr._discover_tessdata = orig
        ocr._SMOKE_CACHE.clear()

    print("OCR TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
