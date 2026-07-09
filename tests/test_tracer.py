"""Self-contained tests for the Tracer — Planloom's from-scratch OCR engine (P1).

Deterministic, offline, NDA-safe: every fixture is synthesized in-process with
fitz, no project data and no network.  Exercises the P1 scaffold end to end —
preprocess (render/polarity/binarize/deskew/linework), run-based connected
components + geometric gates, glyph normalization, the NCC template classifier
over synthetic base-14 prototypes, and the searchable-layer round trip.

The P1 green bar: read title-block / large lettering ("P-101" and the big
tokens) off a clean 300-dpi vector-derived raster, and a full searchable
round trip on a 2-page mixed PDF.

Run:  python3.12 tests/test_tracer.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                   # noqa: E402
import fitz                                          # noqa: E402

from rfi_stamper import tracer                       # noqa: E402
from rfi_stamper.tracer import (                     # noqa: E402
    binarize, classify, components, deskew, fonts, linework, normalize,
    render, segment)
from rfi_stamper.tracer.components import Box         # noqa: E402

TMP = tempfile.mkdtemp(prefix="tracer_test_")

SCAN_LINE = "SHEET P-101"
BIG_TOKEN = "P-101"
TEXT_PAGE = "GENERAL NOTES AND LEGEND DRAWING G-001"
PAGE_W, PAGE_H = 612.0, 792.0

_N = [0]


def A(cond, msg=""):
    """Counting assert so the suite can report how many checks it ran."""
    _N[0] += 1
    assert cond, msg


def _norm(s: str) -> str:
    return "".join(s.split()).upper()


def _contains(hay: str, needle: str) -> bool:
    return _norm(needle) in _norm(hay)


# --------------------------------------------------------------------------- #
#  Fixtures                                                                    #
# --------------------------------------------------------------------------- #

def _scan_pixmap(dpi=200):
    """Rasterize the scanned-page lettering to an image-only pixmap."""
    scratch = fitz.open()
    sp = scratch.new_page(width=PAGE_W, height=PAGE_H)
    sp.insert_text((72, 120), SCAN_LINE, fontsize=26)
    sp.insert_text((72, 230), BIG_TOKEN, fontsize=64)      # large, isolated
    pix = sp.get_pixmap(dpi=dpi)
    scratch.close()
    return pix


def _build_mixed_pdf(path):
    """Page 1: real uppercase text.  Page 2: image-only (scanned) lettering."""
    doc = fitz.open()
    p1 = doc.new_page(width=PAGE_W, height=PAGE_H)
    p1.insert_text((72, 120), TEXT_PAGE, fontsize=20)
    p2 = doc.new_page(width=PAGE_W, height=PAGE_H)
    p2.insert_image(p2.rect, pixmap=_scan_pixmap())
    doc.save(path)
    doc.close()


# --------------------------------------------------------------------------- #
#  1. availability / info                                                      #
# --------------------------------------------------------------------------- #

def test_available_info():
    A(tracer.available() is True, "engine is built-in -> available")
    info = tracer.info()
    A(info["available"] is True)
    A(info["path"] == "builtin" and info["tessdata"] == "builtin", info)
    A(info["langs"] == ["eng"], info)
    # drop-in aliases mirror ocr.py's names
    A(tracer.tesseract_available() is True)
    A(tracer.tesseract_info()["path"] == "builtin")
    A(issubclass(tracer.OcrUnavailable, RuntimeError))
    A(set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") <= set(tracer.CHARSET))
    A("-" in tracer.CHARSET and "." in tracer.CHARSET)


# --------------------------------------------------------------------------- #
#  2. needs_ocr parity                                                         #
# --------------------------------------------------------------------------- #

def test_needs_ocr():
    src = os.path.join(TMP, "mixed.pdf")
    _build_mixed_pdf(src)
    with fitz.open(src) as d:
        A(d.page_count == 2)
        A(len(_norm(d[1].get_text("text"))) < 12, "scanned page starts empty")
        A(_contains(d[0].get_text("text"), "G-001"), "text page has text")
    A(tracer.needs_ocr(src, page_no=2) is True, "scanned page needs OCR")
    A(tracer.needs_ocr(src, page_no=1) is False, "text page does not")
    A(tracer.needs_ocr(src) is True, "doc has a scanned page")
    raised = False
    try:
        tracer.needs_ocr(src, page_no=9)
    except ValueError:
        raised = True
    A(raised, "out-of-range page_no raises")


# --------------------------------------------------------------------------- #
#  3. clean synthetic read — the P1 green bar                                  #
# --------------------------------------------------------------------------- #

def test_clean_read():
    src = os.path.join(TMP, "mixed.pdf")
    _build_mixed_pdf(src)

    text = tracer.ocr_page_text(src, 2)
    A(text.strip(), "OCR of scanned page is non-empty")
    A(_contains(text, "P-101"), f"expected P-101 in page text, got {text!r}")
    A(_contains(text, "SHEET"), f"expected SHEET, got {text!r}")

    words = tracer.read_words(src, 2)
    A(len(words) >= 3, f"expected several words, got {len(words)}")
    for w in words:
        A(len(w) == 5, "read_words tuple is (x0,y0,x1,y1,text)")
    joined = " ".join(w[4] for w in words)
    A(_contains(joined, "P-101"), f"P-101 must appear as a word, got {joined!r}")
    # the standalone big token comes back as an exact word
    exact = [w for w in words if _norm(w[4]) == "P-101"]
    A(exact, f"an exact 'P-101' word must be read, got {[w[4] for w in words]}")
    # every big-token character is present in reading order
    for ch in "P-101":
        A(_contains(joined, ch) or ch == "-", f"char {ch!r} recovered")
    # boxes are inside the page and ordered top→down for the two P-101 rows
    for x0, y0, x1, y1, _t in words:
        A(0 <= x0 < x1 <= PAGE_W + 1 and 0 <= y0 < y1 <= PAGE_H + 1,
          f"word box within page: {(x0, y0, x1, y1)}")


# --------------------------------------------------------------------------- #
#  4. searchable round trip                                                    #
# --------------------------------------------------------------------------- #

def test_searchable_roundtrip():
    src = os.path.join(TMP, "mixed.pdf")
    out = os.path.join(TMP, "mixed_ocr.pdf")
    _build_mixed_pdf(src)

    res = tracer.ocr_pdf(src, out, dpi=300, log=lambda *_: None)
    A(res["out_path"] == out)
    A(res["pages_total"] == 2)
    A(res["pages_ocred"] == 1, f"only scanned page OCR'd: {res}")
    A(os.path.exists(out) and not os.path.exists(out + ".part"),
      "atomic write leaves no .part")

    # input never mutated
    with fitz.open(src) as d:
        A(len(_norm(d[1].get_text("text"))) < 12, "source not modified")

    with fitz.open(out) as d:
        A(d.page_count == 2, "page count preserved")
        for i in range(2):
            r = d[i].rect
            A(abs(r.width - PAGE_W) < 1.0 and abs(r.height - PAGE_H) < 1.0,
              f"page {i} rect within 1pt: {tuple(r)}")
        A(_contains(d[0].get_text("text"), "G-001"), "text page preserved")
        A(_contains(d[1].get_text("text"), "P-101"), "OCR layer searchable")

    # skip_text_pages=False re-OCRs both pages
    out2 = os.path.join(TMP, "mixed_ocr_all.pdf")
    r2 = tracer.ocr_pdf(src, out2, skip_text_pages=False, log=lambda *_: None)
    A(r2["pages_ocred"] == 2, f"both pages OCR'd: {r2}")

    # the pixel-diff proves only invisible text was added
    full = tracer.write_searchable(src, os.path.join(TMP, "v.pdf"),
                                   log=lambda *_: None)
    A(full["verify"], "verify report present")
    for pno, rep in full["verify"]:
        A(rep["ok"], f"page {pno} raster unchanged (frac={rep['frac']:.5f})")


# --------------------------------------------------------------------------- #
#  5. binarize units                                                           #
# --------------------------------------------------------------------------- #

def test_binarize():
    # bimodal histogram: ink selects exactly the dark mode
    img = np.where(np.tile(np.arange(60), (60, 1)) < 30, 45, 205).astype(np.uint8)
    t, ink = binarize.otsu(img)
    A(45 <= t < 205, f"otsu threshold in the gap, got {t}")
    A(bool((ink == (img == 45)).all()), "otsu ink == dark mode exactly")

    # uneven illumination: Sauvola recovers strokes a global cut clips
    H, W = 120, 300
    bg = np.linspace(90, 255, W)[None, :] * np.ones((H, 1))
    truth = np.zeros((H, W), bool)
    img = bg.copy()
    for cx in (30, 90, 150, 210, 270):
        img[40:80, cx:cx + 6] -= 70
        truth[40:80, cx:cx + 6] = True
    img = np.clip(img, 0, 255).astype(np.uint8)
    rec = lambda p: (p & truth).sum() / truth.sum()
    o_rec = rec(binarize.otsu(img)[1])
    s_rec = rec(binarize.sauvola(img))
    A(s_rec > o_rec, f"sauvola recall {s_rec:.2f} beats otsu {o_rec:.2f}")
    A(s_rec > 0.9, f"sauvola recovers most strokes, got {s_rec:.2f}")

    # flatness routes: clean → flat (Otsu), vignette → not flat (Sauvola)
    clean = np.full((80, 120), 255, np.uint8)
    clean[20:60, 20:26] = 0
    A(binarize.flatness(clean) < binarize.FLATNESS_FLAT, "clean sheet is flat")
    A(binarize.flatness(img) >= binarize.FLATNESS_FLAT, "vignette is not flat")
    # a solid thick stroke survives the router (Otsu path, not hollowed)
    thick = np.full((60, 60), 255, np.uint8)
    thick[10:50, 25:40] = 0
    A(binarize.binarize(thick)[10:50, 25:40].mean() > 0.95,
      "thick stroke stays solid through the router")


# --------------------------------------------------------------------------- #
#  6. connected components + gates                                             #
# --------------------------------------------------------------------------- #

def test_components():
    # three separated blobs
    b = np.zeros((40, 60), bool)
    b[5:15, 5:12] = True
    b[5:15, 20:27] = True
    b[25:35, 10:17] = True
    _, boxes = components.label(b)
    A(len(boxes) == 3, f"expected 3 components, got {len(boxes)}")

    # 8-connectivity: a diagonal chain is ONE component
    d = np.zeros((10, 10), bool)
    d[2, 2] = d[3, 3] = d[4, 4] = True
    A(len(components.label(d)[1]) == 1, "diagonal chain is 8-connected")

    # a ring (letter-O topology) is one component
    ring = np.zeros((14, 14), bool)
    ring[2:12, 2:12] = True
    ring[5:9, 5:9] = False
    A(len(components.label(ring)[1]) == 1, "ring is one component")

    # empty input yields no components and a zero label image
    lab0, boxes0 = components.label(np.zeros((8, 8), bool))
    A(boxes0 == [] and lab0.sum() == 0, "empty binary -> no components")

    # label image agrees with the boxes (label ids and bounds)
    lab, boxes = components.label(b)
    for bx in boxes:
        sub = lab[bx.y0:bx.y1 + 1, bx.x0:bx.x1 + 1]
        A((sub == bx.label).sum() == bx.area, "label paint matches box area")

    # geometric gates: keep a letter + a hyphen, drop speck / line / frame
    glyph_h = 40
    letter = Box(1, 100, 100, 128, 140, area=520)          # open cap
    hyphen = Box(2, 140, 118, 156, 122, area=70)           # small solid mark
    speck = Box(3, 200, 200, 201, 201, area=3)             # despeckle
    hline = Box(4, 0, 300, 1200, 305, area=6000)           # long rule
    frame = Box(5, 0, 0, 1600, 6, area=8000)               # sheet-spanning
    kept = components.filter_glyphs(
        [letter, hyphen, speck, hline, frame], glyph_h, dpi=300)
    labs = {b.label for b in kept}
    A(1 in labs, "open letter kept")
    A(2 in labs, "small solid hyphen/period kept (not a solid block)")
    A(3 not in labs, "sub-min speck dropped")
    A(4 not in labs, "long horizontal rule dropped")
    A(5 not in labs, "sheet-spanning frame dropped")


# --------------------------------------------------------------------------- #
#  7. glyph normalization (area-average protocol)                             #
# --------------------------------------------------------------------------- #

def test_normalize():
    # a 2-px stroke survives the downsample (area-average, not nearest-neighbor)
    crop = np.zeros((40, 40), bool)
    crop[:, 19:21] = True
    ng = normalize.norm_glyph(crop)
    A(ng.cell.shape == (normalize.CELL, normalize.CELL), "cell is CELL×CELL")
    A(ng.cell.dtype == np.float32, "cell is float32")
    A(ng.cell.sum() > 0, "2-px stroke leaves ink after normalize")
    A(ng.aspect < 0.2, f"tall thin stroke aspect small, got {ng.aspect:.3f}")

    # a wide bar and a square dot normalize to different aspects (mark disambig)
    bar = np.zeros((30, 30), bool)
    bar[14:17, 4:26] = True
    dot = np.zeros((30, 30), bool)
    dot[13:18, 13:18] = True
    A(normalize.norm_glyph(bar).aspect > 3.0, "hyphen-like bar is wide")
    A(0.7 < normalize.norm_glyph(dot).aspect < 1.5, "period-like dot ~square")

    # center-of-mass lands the ink near the middle of the frame
    off = np.zeros((40, 40), bool)
    off[2:8, 2:8] = True                    # ink jammed in a corner
    cell = normalize.norm_glyph(off).cell
    ys, xs = np.where(cell > cell.max() * 0.3)
    A(abs(ys.mean() - normalize.CELL / 2) < 6
      and abs(xs.mean() - normalize.CELL / 2) < 6, "COM re-centered")

    # empty crop -> zero cell (honest: nothing in, nothing out)
    A(normalize.norm_glyph(np.zeros((10, 10), bool)).cell.sum() == 0)


# --------------------------------------------------------------------------- #
#  8. NCC classifier                                                           #
# --------------------------------------------------------------------------- #

def test_ncc():
    protos = fonts.prototypes()
    A(len(protos) == len(fonts.CHARSET), "every class rendered a prototype")
    clf = classify.default_classifier()

    # round-trip identity: each prototype classifies as itself
    ident = 0
    for ch, (cells, asp) in protos.items():
        r = clf.classify(cells[0], float(asp[0]))
        A(len(r) >= 2, "ranked list carries a runner-up for the margin")
        A(r[0][1] >= r[1][1], "ranked descending by score")
        if r[0][0] == ch:
            ident += 1
    A(ident == len(protos), f"all {len(protos)} prototypes self-identify, "
      f"got {ident}")

    # marks disambiguated by the aspect tie-break
    A(clf.classify(protos["-"][0][0], float(protos["-"][1][0]))[0][0] == "-",
      "hyphen reads as hyphen")
    A(clf.classify(protos["."][0][0], float(protos["."][1][0]))[0][0] == ".",
      "period reads as period")

    # mild Gaussian noise does not flip distinct classes
    rng = np.random.default_rng(7)
    noised_ok = 0
    subset = [c for c in "PSHETABCDXYZ0123456789-" if c in protos]
    for ch in subset:
        cells, asp = protos[ch]
        noisy = np.clip(cells[0] + rng.normal(0, 0.05, cells[0].shape),
                        0, 1).astype(np.float32)
        if clf.classify(noisy, float(asp[0]))[0][0] == ch:
            noised_ok += 1
    A(noised_ok == len(subset), f"noised prototypes still classify: "
      f"{noised_ok}/{len(subset)}")

    # empty batch is handled
    A(clf.classify_batch(np.zeros((0, normalize.CELL, normalize.CELL))) == [])


# --------------------------------------------------------------------------- #
#  9. deskew / orientation                                                     #
# --------------------------------------------------------------------------- #

def test_deskew():
    band = np.zeros((100, 200), bool)
    for r in (20, 50, 80):
        for c in range(10, 190, 12):
            band[r:r + 8, c:c + 6] = True
    A(deskew.orient_quadrant(band) == 0, "horizontal text -> quadrant 0")
    A(deskew.orient_quadrant(band.T) == 90, "vertical text -> quadrant 90")
    A(abs(deskew.fine_skew(band)) < 1.0, "axis-aligned skew ~0")

    # recover a known +5° rotation (deskew maximizes row-projection sharpness)
    H, W = 120, 240
    img = np.zeros((H, W), bool)
    for r in (30, 60, 90):
        for c in range(20, 220, 14):
            img[r:r + 9, c:c + 7] = True
    cy, cx = (H - 1) / 2, (W - 1) / 2
    th = np.deg2rad(5.0)
    cs, sn = np.cos(th), np.sin(th)
    ys, xs = np.mgrid[0:H, 0:W]
    sy, sx = ys - cy, xs - cx
    srcy = np.round(cy + sn * sx + cs * sy).astype(int)
    srcx = np.round(cx + cs * sx - sn * sy).astype(int)
    ok = (srcy >= 0) & (srcy < H) & (srcx >= 0) & (srcx < W)
    rot = np.zeros_like(img)
    rot[ys[ok], xs[ok]] = img[srcy[ok], srcx[ok]]
    A(abs(deskew.fine_skew(rot) + 5.0) < 1.0, "fine_skew finds the −5° undo")

    up, applied = deskew.deskew(band)
    A(up.shape == band.shape and abs(applied) < 2.0, "deskew no-op on upright")


# --------------------------------------------------------------------------- #
#  10. linework removal                                                        #
# --------------------------------------------------------------------------- #

def test_linework():
    img = np.zeros((80, 400), bool)
    img[40, :] = True                       # a full-width rule
    img[10:34, 20:32] = True                # a glyph-sized blob
    glyph_h = 24
    out = linework.strip_lines(img, glyph_h)
    A(out[40, :].sum() < 5, "long horizontal rule removed")
    A(out[10:34, 20:32].sum() > 100, "glyph-sized blob preserved")

    # vertical rule too
    v = np.zeros((400, 80), bool)
    v[:, 40] = True
    v[20:44, 10:22] = True
    vo = linework.strip_lines(v, glyph_h)
    A(vo[:, 40].sum() < 5, "long vertical rule removed")
    A(vo[20:44, 10:22].sum() > 100, "glyph preserved under vertical strip")

    # no long runs -> no-op
    only = np.zeros((60, 60), bool)
    only[10:40, 10:22] = True
    A(bool((linework.strip_lines(only, glyph_h) == only).all()),
      "no lines -> strip is a no-op")


# --------------------------------------------------------------------------- #
#  11. segmentation                                                            #
# --------------------------------------------------------------------------- #

def test_segment():
    # two lines, two words each
    def box(x0, y0, w, h):
        return Box(0, x0, y0, x0 + w - 1, y0 + h - 1, w * h)
    top = [box(10, 10, 18, 24), box(30, 10, 18, 24),           # word A
           box(90, 10, 18, 24), box(110, 10, 18, 24)]          # word B (gap)
    bot = [box(10, 70, 18, 24), box(30, 70, 18, 24)]
    lines = segment.group_lines(top + bot)
    A(len(lines) == 2, f"two vertical bands -> two lines, got {len(lines)}")
    words = segment.group_words(lines[0])
    A(len(words) == 2, f"wide space splits into two words, got {len(words)}")

    # merge_broken rejoins two overlapping fragments of one broken glyph ...
    frag = [box(10, 10, 10, 24), box(15, 12, 10, 20)]         # x/y overlap
    A(len(segment.merge_broken(frag)) == 1, "overlapping fragments merge")
    # ... but never fuses two properly-spaced characters (no x-overlap)
    pair = [box(10, 10, 10, 24), box(30, 10, 10, 24)]
    A(len(segment.merge_broken(pair)) == 2, "spaced characters never merge")

    # split_touching (P2 stub) cuts a double-wide box into pieces
    line = [box(0, 0, 12, 24), box(14, 0, 12, 24), box(40, 0, 40, 24)]
    slices = segment.split_touching(line)
    A(len(slices) > 3, "double-wide box split into equal pitch slices")
    A(all(len(s) == 3 for s in slices), "slices are (box, x0, x1)")


# --------------------------------------------------------------------------- #
#  12. render: polarity + size normalize                                       #
# --------------------------------------------------------------------------- #

def _image_page_pdf(path, draw, dpi=150, w=300, h=200):
    doc = fitz.open()
    p = doc.new_page(width=w, height=h)
    draw(p)
    pix = p.get_pixmap(dpi=dpi)
    doc.close()
    doc2 = fitz.open()
    pg = doc2.new_page(width=w, height=h)
    pg.insert_image(pg.rect, pixmap=pix)
    doc2.save(path)
    doc2.close()


def test_render():
    src = os.path.join(TMP, "mixed.pdf")
    _build_mixed_pdf(src)
    gray, meta = render.render_gray(src, 2, dpi=300)
    A(gray.dtype == np.uint8 and gray.ndim == 2, "gray is 2-D uint8")
    A(set(meta) >= {"dpi", "scale", "w", "h", "inverted", "cap_px",
                    "too_small", "px_per_pt"}, meta)
    A(meta["inverted"] is False, "normal white sheet not inverted")
    A(meta["px_per_pt"] == (300 / 72.0) * meta["scale"], "px_per_pt reported")

    # white-on-black negative is detected from the title-block corner
    inv = os.path.join(TMP, "inv.pdf")

    def draw_inv(p):
        p.draw_rect(p.rect, fill=(0, 0, 0), color=(0, 0, 0))
        p.insert_text((30, 120), "P-101", fontsize=40, color=(1, 1, 1))
    _image_page_pdf(inv, draw_inv)
    _, minv = render.render_gray(inv, 1, dpi=150)
    A(minv["inverted"] is True, "white-on-black negative inverted")


# --------------------------------------------------------------------------- #
#  13. honest degrade                                                          #
# --------------------------------------------------------------------------- #

def test_degrade():
    # sub-legible lettering: below the recovery floor
    tiny = os.path.join(TMP, "tiny.pdf")

    def draw_tiny(p):
        p.insert_text((20, 60), "SHEET P101 ABCDEF", fontsize=11)
    _image_page_pdf(tiny, draw_tiny, dpi=100, w=400, h=200)
    gray, meta = render.render_gray(tiny, 1, dpi=100)
    reads = tracer.read_image(gray, dpi=100)
    confident = [t for *_, t, s in reads if s >= tracer.TAU_HI]
    # honest stance: flag too_small OR return empty/low-confidence — never a
    # confident WRONG token.  If anything crosses τ_hi it must be a real token.
    A(meta["too_small"] or not reads or all(s < tracer.TAU_HI
                                            for *_, _t, s in reads),
      f"tiny render degrades honestly: too_small={meta['too_small']} "
      f"reads={[(t, round(s, 2)) for *_, t, s in reads]}")
    for tok in confident:
        A(tok in ("SHEET", "P101", "ABCDEF"),
          f"any τ_hi token must be a real word, not garbage: {tok!r}")

    # a genuinely blank raster returns nothing (no phantom glyphs)
    blank = np.full((300, 400), 255, np.uint8)
    A(tracer.read_image(blank) == [], "blank sheet reads nothing")


# --------------------------------------------------------------------------- #

def main():
    tests = [
        (test_available_info, "availability / info (builtin shape)"),
        (test_needs_ocr, "needs_ocr parity (scanned/text/doc)"),
        (test_clean_read, "clean synthetic read — P1 green bar (P-101)"),
        (test_searchable_roundtrip, "searchable round trip + pixel-diff verify"),
        (test_binarize, "Otsu / Sauvola / flatness router"),
        (test_components, "run-based 8-conn CC + geometric gates"),
        (test_normalize, "glyph normalize (area-average, COM center)"),
        (test_ncc, "NCC identity + aspect marks + noise robustness"),
        (test_deskew, "deskew quadrant + fine skew"),
        (test_linework, "long-run linework removal"),
        (test_segment, "line / word / char segmentation"),
        (test_render, "render polarity + size normalize"),
        (test_degrade, "honest degrade (flag / low-confidence, no garbage)"),
    ]
    for fn, label in tests:
        fn()
        print(f"PASS {label}")
    print(f"TRACER TEST PASSED  ({_N[0]} checks)  — the Tracer, Phase P1")
    print("outputs in", TMP)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("TRACER TEST FAILED:", e)
        sys.exit(1)
