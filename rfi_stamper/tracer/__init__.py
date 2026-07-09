"""The Tracer — Planloom's from-scratch, fully-offline OCR engine.

The Tracer traces the lettering off a scanned sheet using nothing but numpy and
PyMuPDF: no external OCR binary, no downloads, no network, no pretrained model.
It is being built to retire the optional Tesseract dependency in staged phases
(see OCR_PLAN.md).  This is **Phase P1** — the preprocess + connected-component
+ searchable-layer scaffold with an **NCC template-matching** classifier over
synthetic base-14 prototypes.  Its promise is narrow and honest: read the large,
isolated lettering of a clean 300-dpi vector-derived raster — title-block sheet
numbers and big tokens.  The gradient-feature MLP/kNN ensemble (P2), the
lexicon/grammar post-correction (P3) and the eval-harness Tesseract cutover (P4)
come later.

Public API — deliberately drop-in compatible with ``rfi_stamper.ocr`` so that a
future P4 can turn ``ocr.py`` into a thin facade over this package with zero
caller changes:

* ``available() -> bool``
* ``info() -> dict``
* ``needs_ocr(path, page_no=None, min_chars=12) -> bool``
* ``read_image(gray, dpi=300) -> [(x0, y0, x1, y1, text, score), ...]``  (px)
* ``read_words(path, page_no, dpi=300) -> [(x0, y0, x1, y1, text), ...]``  (pts)
* ``ocr_page_text(path, page_no, dpi=300, language="eng") -> str``
* ``ocr_pdf(path, out_path, dpi=300, language="eng", skip_text_pages=True,
  log=print) -> {"pages_ocred", "pages_total", "out_path"}``

``read_image`` returns **raster pixel** coordinates (natural for "read this
image"); ``read_words`` maps them to viewer page points via the render scale.
"""
from __future__ import annotations

import math

import numpy as np
import fitz

from . import binarize, classify, components, deskew, linework, normalize, render, segment
from . import lexicon as _lexicon
from .fonts import CHARSET
from .searchable import write_searchable, _visible

__all__ = [
    "CHARSET", "available", "info", "needs_ocr", "read_image", "read_words",
    "ocr_page_text", "ocr_pdf", "write_searchable", "harvest_sheet_hints",
    "tesseract_available", "tesseract_info", "OcrUnavailable",
]

# Confidence anchors (OCR_PLAN §5): below τ_lo a read is garbage, above τ_hi it
# is auto-acceptable.  P1 keeps reads down to a low floor so callers can gate,
# but never *labels* a low-cosine glyph as confident.
TAU_LO = 0.60
TAU_HI = 0.90
_MIN_KEEP = 0.30          # drop words below this mean cosine as noise
_MIN_HINT_CHARS = 12      # a page with ≥ this many visible chars is vector text


class OcrUnavailable(RuntimeError):
    """Kept for drop-in compatibility with ``ocr.py``; never raised in P1.

    The Tracer is built-in, so the engine is always available — but callers
    that catch this (mirroring the Tesseract path) keep working unchanged.
    """


# --------------------------------------------------------------------------- #
#  Engine availability (built-in — always true)                               #
# --------------------------------------------------------------------------- #

def available() -> bool:
    """True — the Tracer is a built-in engine with no external dependency."""
    return True


def info() -> dict:
    """Describe the built-in engine (shape mirrors ``ocr.tesseract_info``)."""
    return {"available": True, "path": "builtin", "tessdata": "builtin",
            "langs": ["eng"]}


# ``ocr.py`` name aliases so P4's facade swap is a rename, not a rewrite.
tesseract_available = available
tesseract_info = info


def needs_ocr(path: str, page_no: int | None = None, min_chars: int = 12) -> bool:
    """True when a page has essentially no extractable text (image-only/scanned).

    Reimplemented here (not imported from ``ocr.py``) so the package stands
    alone.  ``page_no`` is 1-based; ``None`` means "any page lacks real text".
    """
    with fitz.open(path) as doc:
        total = doc.page_count
        if page_no is None:
            indices = range(total)
        else:
            if page_no < 1 or page_no > total:
                raise ValueError(
                    f"page_no {page_no} outside document (has {total} page(s))")
            indices = [page_no - 1]
        for i in indices:
            if len(_visible(doc[i].get_text("text"))) < min_chars:
                return True
        return False


# --------------------------------------------------------------------------- #
#  Core pipeline                                                              #
# --------------------------------------------------------------------------- #

def _word_score(cosines) -> float:
    """Length-normalized geometric mean of per-char cosines (OCR_PLAN §12)."""
    if not cosines:
        return 0.0
    logs = [math.log(max(c, 1e-3)) for c in cosines]
    return math.exp(sum(logs) / len(logs))


def _resolve_ctx(sheet_hints, lexicon, heartwood_path, ctx):
    """Build a P3 post-correction Context iff a hook was supplied (else None).

    With ``sheet_hints``/``lexicon``/``heartwood_path``/``ctx`` all ``None`` this
    returns ``None`` and the pipeline behaves EXACTLY as P2 (byte-identical).
    """
    if ctx is not None:
        return ctx
    if sheet_hints is None and lexicon is None and heartwood_path is None:
        return None
    return _lexicon.Context.build(sheet_hints=sheet_hints, lexicon=lexicon,
                                  heartwood_path=heartwood_path)


def read_image(gray, dpi: int = 300, min_keep: float = _MIN_KEEP,
               sheet_hints=None, lexicon=None, heartwood_path=None, ctx=None):
    """Read one upright grayscale raster → ``[(x0, y0, x1, y1, text, score)]``.

    Coordinates are **raster pixels** (inclusive bbox).  The pipeline:
    binarize → strip long linework → label components → geometric glyph gate →
    lines → words → per-glyph NCC classify → assemble word text + confidence.
    Words whose mean cosine falls below ``min_keep`` are dropped as noise; the
    surviving reads carry an honest score so the caller can apply τ_hi/τ_lo.

    **P3 (optional, transparent):** when ``sheet_hints``/``lexicon``/
    ``heartwood_path`` (or a prebuilt ``ctx``) is supplied, each emitted token is
    routed through :func:`lexicon.correct` — sheet-number index snap, dimension
    grammar repair, word lexicon snap, number-lock, garbage rejection — in this
    pixel frame.  With all of them ``None`` the output is identical to P2.
    """
    ctx = _resolve_ctx(sheet_hints, lexicon, heartwood_path, ctx)
    gray = np.asarray(gray)
    if gray.dtype != np.uint8:
        gray = np.clip(gray, 0, 255).astype(np.uint8)
    ink = binarize.binarize(gray)
    _, boxes0 = components.label(ink)
    if not boxes0:
        return []
    heights = [b.h for b in boxes0]
    strip_h = max(heights)                       # generous: never eat a glyph
    ink2 = linework.strip_lines(ink, strip_h)
    _, boxes = components.label(ink2)
    glyph_h = components._median_glyph_h(boxes, dpi=dpi)
    kept = components.filter_glyphs(boxes, glyph_h, dpi=dpi)
    if not kept:
        return []

    clf = classify.default_ensemble()
    # P3: resolve the correction context to THIS raster's pixel frame (so the
    # sheet-number region prior is right) without mutating a shared Context.
    use_ctx = ctx
    if ctx is not None and ctx.page_wh is None:
        import dataclasses
        use_ctx = dataclasses.replace(
            ctx, page_wh=(int(gray.shape[1]), int(gray.shape[0])))
    results = []
    for line in segment.group_lines(kept):
        line = segment.merge_broken(line)
        # 2-line cap/baseline band for the whole line: the vertical position of
        # each glyph inside it feeds the marks disambiguator (a mid-height
        # hyphen vs. a low period vs. a high apostrophe) — OCR_PLAN §4/§9.
        band_top = min(b.y0 for b in line)
        band_bot = max(b.y1 for b in line)
        band_span = max(1.0, float(band_bot - band_top))
        med_w = segment._median([b.w for b in line]) or 1.0
        for word in segment.group_words(line):
            # P2: split a run of touching glyphs (drop-fall cuts + DP
            # recombination) when a box is wide AND doesn't read as one glyph;
            # normal-width glyphs (M, W, 0) never trigger it.
            cells, aspects, rel_ys, spans = [], [], [], []
            for b in word:
                for (yy0, xx0, yy1, xx1) in segment.split_glyph_boxes(
                        ink2, b, med_w, clf):
                    crop = ink2[yy0:yy1 + 1, xx0:xx1 + 1]
                    if not crop.any():
                        continue
                    ng = normalize.norm_glyph(crop)
                    cells.append(ng.cell)
                    aspects.append(ng.aspect)
                    cy = (yy0 + yy1) / 2.0
                    rel_ys.append((cy - band_top) / band_span)
                    spans.append((yy0, xx0, yy1, xx1))
            if not cells:
                continue
            ranked = clf.classify_batch(np.stack(cells), aspects, rel_ys)
            chars = [r[0][0] for r in ranked]
            cos = [r[0][1] for r in ranked]
            text = "".join(chars)
            score = _word_score(cos)
            if score < min_keep:
                continue
            x0 = min(s[1] for s in spans)
            y0 = min(s[0] for s in spans)
            x1 = max(s[3] for s in spans)
            y1 = max(s[2] for s in spans)
            if use_ctx is not None:
                tok = _lexicon.Tok(text, (int(x0), int(y0), int(x1), int(y1)),
                                   float(score))
                res = _lexicon.correct(tok, ranked, use_ctx)
                if not res["keep"]:
                    continue
                text, score = res["text"], res["conf"]
            results.append((int(x0), int(y0), int(x1), int(y1), text, float(score)))
    return results


def read_words(path: str, page_no: int, dpi: int = 300,
               sheet_hints=None, lexicon=None, heartwood_path=None, ctx=None):
    """Read one PDF page → ``[(x0, y0, x1, y1, text), ...]`` in viewer points.

    Renders through ``render.render_gray`` (polarity + size normalized), runs
    ``read_image``, then maps raster pixels back to viewer page points via the
    render scale (``point = pixel / px_per_pt``).  The optional P3 kwargs are
    forwarded to ``read_image``; with all of them ``None`` the output is
    byte-identical to P2.
    """
    ctx = _resolve_ctx(sheet_hints, lexicon, heartwood_path, ctx)
    gray, meta = render.render_gray(path, page_no, dpi=dpi)
    ppp = meta["px_per_pt"]
    out = []
    for x0, y0, x1, y1, text, _score in read_image(gray, dpi=dpi, ctx=ctx):
        out.append((x0 / ppp, y0 / ppp, x1 / ppp, y1 / ppp, text))
    return out


def ocr_page_text(path: str, page_no: int, dpi: int = 300,
                  language: str = "eng", sheet_hints=None, lexicon=None,
                  heartwood_path=None, ctx=None) -> str:
    """Return the OCR text of one page (1-based) as space-joined words.

    ``language`` is accepted and ignored (the Tracer is single-model); it keeps
    the signature drop-in compatible with ``ocr.ocr_page_text``.  The optional P3
    kwargs are forwarded to ``read_words`` (all ``None`` → P2 behavior).
    """
    with fitz.open(path) as doc:
        total = doc.page_count
        if page_no < 1 or page_no > total:
            raise ValueError(
                f"page_no {page_no} outside document (has {total} page(s))")
    words = read_words(path, page_no, dpi=dpi, sheet_hints=sheet_hints,
                       lexicon=lexicon, heartwood_path=heartwood_path, ctx=ctx)
    return " ".join(w[4] for w in words)


def harvest_sheet_hints(doc) -> list:
    """Harvest the document's OWN sheet tokens from its vector text pages.

    Runs ``fitz.get_text("words")`` on every page carrying real text through
    ``core.SHEET_TOKEN`` (skipping MSDS precaution codes via ``core.GHS_LINE``),
    returning the unique canonical sheet numbers.  This is free self-supervision
    (OCR_PLAN §4): a scanned page's sheet number is cross-checked against the
    set's real numbering with zero user input.  Guarded to work with zero hits.
    """
    from ..core import SHEET_TOKEN, GHS_LINE, canon
    close = False
    if isinstance(doc, str):
        doc = fitz.open(doc)
        close = True
    hints, seen = [], set()
    try:
        for i in range(doc.page_count):
            page = doc[i]
            text = page.get_text("text")
            if len(_visible(text)) < _MIN_HINT_CHARS:
                continue                 # image-only page: nothing vector to mine
            for line in text.splitlines():
                if GHS_LINE.search(line):
                    continue
                for m in SHEET_TOKEN.finditer(line.upper()):
                    tok = canon(m.group(1), m.group(2))
                    if tok not in seen:
                        seen.add(tok)
                        hints.append(tok)
    finally:
        if close:
            doc.close()
    return hints


def ocr_pdf(path: str, out_path: str, dpi: int = 300, language: str = "eng",
            skip_text_pages: bool = True, log=print, sheet_hints=None,
            lexicon=None, heartwood_path=None) -> dict:
    """Write a searchable copy of ``path`` (delegates to ``write_searchable``).

    ``language`` accepted and ignored (compat).  Returns
    ``{"pages_ocred", "pages_total", "out_path"}``.

    **P3 self-supervision (opt-in):** when any of ``sheet_hints``/``lexicon``/
    ``heartwood_path`` is supplied, the document's OWN sheet index is harvested
    from its vector pages and UNIONed with any caller hints, so each scanned
    page's sheet number is cross-checked against the set's real numbering before
    the searchable text is written.  With all three ``None`` the output is
    byte-identical to P2.
    """
    ctx = None
    if sheet_hints is not None or lexicon is not None or heartwood_path is not None:
        harvested = harvest_sheet_hints(path)
        merged = list(dict.fromkeys(list(sheet_hints or []) + harvested))
        ctx = _lexicon.Context.build(sheet_hints=merged, lexicon=lexicon,
                                     heartwood_path=heartwood_path)
        log(f"  · P3 post-correction on ({len(merged)} sheet hint(s))")
    res = write_searchable(path, out_path, dpi=dpi,
                           skip_text_pages=skip_text_pages, log=log, ctx=ctx)
    return {"pages_ocred": res["pages_ocred"], "pages_total": res["pages_total"],
            "out_path": res["out_path"]}
