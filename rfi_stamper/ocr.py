"""Searchable OCR text layer for scanned plan-set PDFs (fully offline).

OCR is Planloom's OWN from-scratch engine — the Tracer (``rfi_stamper/tracer/``,
see OCR_PLAN.md).  There is **no external OCR binary**, no language-data
download, and no network: the engine is pure numpy + PyMuPDF and ships inside
the app (``tracer/model.npz``).  This module is a thin facade that preserves the
historical ``ocr.py`` public API and delegates every call to the Tracer, so
existing callers (the CLI, the GUI PDF-Tools tab, the tests) keep working with
no changes.

The main :func:`ocr_pdf` path runs with the Tracer's P3 post-correction ON: a
default trade/room/CSI lexicon plus the set's OWN sheet index (auto-harvested
from the document's vector pages) cross-check every read, so a smudged ``S-1O1``
snaps to the real ``S-101`` in the set — number-locked so a scanned ``8'`` can
never become ``6'``.

Back-compat: the ``tesseract_*`` names and :class:`OcrUnavailable` remain as
never-firing shims for import stability — the engine is always available.

The input file is never mutated.  Output is written with the house atomic
pattern (write to ``out_path + ".part"``, flush + ``os.fsync``, then
``os.replace``), preserving page count and page point-size within 1 pt — the
Tracer's searchable writer implements exactly this.
"""
from __future__ import annotations

from . import tracer
from .tracer import lexicon as _lexicon


class OcrUnavailable(RuntimeError):
    """Kept for import compatibility with the historical Tesseract wrapper.

    Never raised: the Tracer is a built-in engine, so OCR is always available.
    Callers that still ``except ocr.OcrUnavailable`` keep working unchanged.
    """


def _default_lexicon():
    """The Tracer's built-in trade/room/CSI lexicon (P3 post-correction)."""
    return _lexicon.Lexicon.default()


def tesseract_available() -> bool:
    """True — OCR is Planloom's built-in engine (the Tracer); no external binary.

    Kept under the historical name for caller/import compatibility.
    """
    return tracer.available()


def tesseract_info() -> dict:
    """Describe the built-in engine (historical name; shape unchanged).

    ``{"available": True, "path": "builtin", "tessdata": "builtin",
    "langs": ["eng"]}``.
    """
    return tracer.info()


def needs_ocr(path: str, page_no: int | None = None, min_chars: int = 12) -> bool:
    """True when a page has essentially no extractable text (image-only/scanned).

    ``page_no`` is 1-based; when ``None`` the answer is True if ANY page in the
    document lacks real text.  Delegates to the Tracer.
    """
    return tracer.needs_ocr(path, page_no=page_no, min_chars=min_chars)


def ocr_pdf(path: str, out_path: str, dpi: int = 300, language: str = "eng",
            skip_text_pages: bool = True, log=print, review_sink=None,
            overrides=None) -> dict:
    """Write a searchable copy of ``path`` to ``out_path`` (built-in OCR).

    Each page that already carries real text is copied through untouched when
    ``skip_text_pages`` is set; every other page is rendered at ``dpi`` and
    rebuilt by the Tracer as a searchable page (image + invisible OCR text),
    with P3 post-correction ON (a default lexicon + the document's own sheet
    index) so scanned sheet numbers are cross-checked against the real
    numbering.  Page order and point-size are preserved; the input is never
    mutated; the write is atomic.

    ``review_sink`` (list) collects queue-worthy reads as
    ``tracer.ReviewItem`` for the review deck; ``overrides``
    ``{(page, bbox): text}`` lets a review session re-run the writer with
    the accepted texts.  ``language`` is accepted and ignored (the Tracer
    is single-model).  Returns
    ``{"pages_ocred": int, "pages_total": int, "out_path": str}``.
    """
    return tracer.ocr_pdf(path, out_path, dpi=dpi, language=language,
                          skip_text_pages=skip_text_pages, log=log,
                          lexicon=_default_lexicon(),
                          review_sink=review_sink, overrides=overrides)


def ocr_page_text(path: str, page_no: int, dpi: int = 300,
                  language: str = "eng") -> str:
    """Return only the OCR-extracted text of one page (1-based ``page_no``).

    Delegates to the Tracer; ``language`` is accepted and ignored.
    """
    return tracer.ocr_page_text(path, page_no, dpi=dpi, language=language)
