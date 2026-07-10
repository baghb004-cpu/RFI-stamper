"""Headless tests for rfi_stamper.drawdiff (the Slipsheet vector diff).

Pins the interval-algebra core (the collinear split/merge false-diff
killer), counted edits, extensions, rigid-transform registration (the
alignment rotation-sign convention), region clustering + ordering, the
word layer, /Rotate tolerance, the deterministic redline renderer, and
the honest failures (raster page, segment cap).

Run:  python3 tests/test_drawdiff.py
"""
import math
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz                                     # noqa: E402

from rfi_stamper import drawdiff                # noqa: E402
from rfi_stamper.align import AlignResult       # noqa: E402

TMP = tempfile.mkdtemp(prefix="ploom_ddiff_")
W, H = 612, 792


def _quiet(*_a, **_k):
    pass


def _make(name, lines, texts=(), rotate=0, transform=None):
    """Vector fixture: lines = [(x0, y0, x1, y1)], texts = [(x, y, s)].
    ``transform(p) -> p`` warps every endpoint (rigid-transform fixture)."""
    path = os.path.join(TMP, name)
    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    for x0, y0, x1, y1 in lines:
        a, b = (x0, y0), (x1, y1)
        if transform:
            a, b = transform(a), transform(b)
        page.draw_line(fitz.Point(*a), fitz.Point(*b), width=0.7)
    for x, y, s in texts:
        p = (x, y)
        if transform:
            p = transform(p)
        page.insert_text(fitz.Point(*p), s, fontsize=10)
    if rotate:
        page.set_rotation(rotate)
    doc.save(path)
    doc.close()
    return path


_GRID = ([(100.0, 100.0 + 40.0 * i, 500.0, 100.0 + 40.0 * i)
          for i in range(6)]
         + [(100.0 + 80.0 * i, 100.0, 100.0 + 80.0 * i, 300.0)
            for i in range(6)])


def test_exact_echo():
    a = _make("e_a.pdf", _GRID, texts=[(120, 400, "SHEET P-1")])
    b = _make("e_b.pdf", _GRID, texts=[(120, 400, "SHEET P-1")])
    rep = drawdiff.diff_pages(a, b, align=None, log=_quiet)
    assert rep["totals"]["added"] == 0 and rep["totals"]["removed"] == 0
    assert rep["regions"] == [] and rep["words_added"] == []


def test_counted_edits():
    base = list(_GRID)
    rev = list(_GRID)
    del rev[0]                                  # remove one horizontal
    del rev[6]                                  # remove one vertical
    rev.append((100.0, 350.0, 500.0, 350.0))    # add one
    rev.append((520.0, 100.0, 520.0, 300.0))    # add another
    # move one line down 20 pt = 1 removed + 1 added
    rev[0] = (100.0, 160.0, 500.0, 160.0)       # was y=140
    a = _make("c_a.pdf", base)
    b = _make("c_b.pdf", rev)
    rep = drawdiff.diff_pages(a, b, align=None, log=_quiet)
    assert rep["totals"]["removed"] == 3, rep["totals"]
    assert rep["totals"]["added"] == 3, rep["totals"]


def test_collinear_split_merge():
    # THE classic false diff: one line re-exported as two touching pieces
    a = _make("s_a.pdf", [(100.0, 100.0, 300.0, 100.0)] + _GRID[6:])
    b = _make("s_b.pdf", [(100.0, 100.0, 190.0, 100.0),
                          (190.0, 100.0, 300.0, 100.0)] + _GRID[6:])
    rep = drawdiff.diff_pages(a, b, align=None, log=_quiet)
    assert rep["totals"]["added"] == 0 and rep["totals"]["removed"] == 0, \
        rep["totals"]
    # variant: 0.5 pt gap at the joint still merges (GAP_TOL)
    b2 = _make("s_b2.pdf", [(100.0, 100.0, 189.75, 100.0),
                            (190.25, 100.0, 300.0, 100.0)] + _GRID[6:])
    rep = drawdiff.diff_pages(a, b2, align=None, log=_quiet)
    assert rep["totals"]["added"] == 0 and rep["totals"]["removed"] == 0
    # mirror: two pieces merge into one
    rep = drawdiff.diff_pages(b2, a, align=None, log=_quiet)
    assert rep["totals"]["added"] == 0 and rep["totals"]["removed"] == 0


def test_extension():
    a = _make("x_a.pdf", [(100.0, 100.0, 300.0, 100.0)] + _GRID[6:])
    b = _make("x_b.pdf", [(100.0, 100.0, 350.0, 100.0)] + _GRID[6:])
    rep = drawdiff.diff_pages(a, b, align=None, log=_quiet)
    assert rep["totals"]["removed"] == 0, rep["totals"]
    assert rep["totals"]["added"] == 1
    assert abs(rep["totals"]["added_len_pt"] - 50.0) <= 1.0


def test_rigid_transform():
    """Revision re-plotted shifted + rotated: alignment makes it diff to
    zero — this test PINS the rotation-sign convention end to end."""
    dx, dy, deg = 7.3, -4.1, 1.5
    th = math.radians(deg)
    c, s = math.cos(th), math.sin(th)
    ctr = (W / 2.0, H / 2.0)

    def fwd(p):
        # the inverse of drawdiff._apply_align: build the rev so that
        # rotate-then-shift by (dx, dy, deg) lands it back on the base
        vx, vy = p[0] - ctr[0] - dx, p[1] - ctr[1] - dy
        return (ctr[0] + c * vx + s * vy, ctr[1] - s * vx + c * vy)

    a = _make("r_a.pdf", _GRID)
    b = _make("r_b.pdf", _GRID, transform=fwd)
    ar = AlignResult(dx=dx, dy=dy, rotation=deg, score=1.0)
    rep = drawdiff.diff_pages(a, b, align=ar, log=_quiet)
    assert rep["totals"]["added"] == 0 and rep["totals"]["removed"] == 0, \
        rep["totals"]
    assert rep["align"] is not None
    # translation-only through the REAL auto_align pipeline
    def shift(p):
        return (p[0] - 6.0, p[1] + 9.0)
    b2 = _make("r_b2.pdf", _GRID, transform=shift)
    rep2 = drawdiff.diff_pages(a, b2, align="auto", log=_quiet)
    assert rep2["align"] is not None, rep2["warnings"]
    assert rep2["totals"]["added"] == 0 and rep2["totals"]["removed"] == 0, \
        (rep2["totals"], rep2["align"])


def test_region_clustering():
    base = list(_GRID)
    rev = list(_GRID)
    rev.append((60.0, 40.0, 90.0, 40.0))        # top-left corner
    rev.append((540.0, 700.0, 580.0, 700.0))    # bottom-right corner
    rev.append((60.0, 700.0, 60.0, 740.0))      # bottom-left corner
    a = _make("g_a.pdf", base)
    b = _make("g_b.pdf", rev)
    rep = drawdiff.diff_pages(a, b, align=None, log=_quiet)
    assert len(rep["regions"]) == 3, rep["regions"]
    mags = [r["added_len_pt"] + r["removed_len_pt"] for r in rep["regions"]]
    assert mags == sorted(mags, reverse=True)   # Δ1 is the biggest change
    for r in rep["regions"]:
        assert not r["has_text_change"]


def test_word_layer():
    a = _make("w_a.pdf", _GRID, texts=[(120, 400, "27'-6\"")])
    b = _make("w_b.pdf", _GRID, texts=[(120, 400, "29'-0\"")])
    rep = drawdiff.diff_pages(a, b, align=None, log=_quiet)
    assert rep["totals"]["added"] == 0 and rep["totals"]["removed"] == 0
    assert rep["totals"]["words_added"] == 1
    assert rep["totals"]["words_removed"] == 1
    assert len(rep["regions"]) == 1 and rep["regions"][0]["has_text_change"]


def test_rotate90():
    a = _make("q_a.pdf", _GRID)
    b = _make("q_b.pdf", _GRID[1:])             # one line removed
    a90 = _make("q_a90.pdf", _GRID, rotate=90)
    b90 = _make("q_b90.pdf", _GRID[1:], rotate=90)
    r0 = drawdiff.diff_pages(a, b, align=None, log=_quiet)
    r90 = drawdiff.diff_pages(a90, b90, align=None, log=_quiet)
    assert r0["totals"]["removed"] == r90["totals"]["removed"] == 1
    assert r0["totals"]["added"] == r90["totals"]["added"] == 0


def test_renderer():
    base = list(_GRID)
    rev = list(_GRID[1:]) + [(100.0, 350.0, 500.0, 350.0)]
    a = _make("p_a.pdf", base, texts=[(120, 400, "NOTE 1")])
    b = _make("p_b.pdf", rev, texts=[(120, 400, "NOTE 2")])
    out1 = os.path.join(TMP, "red1.pdf")
    out2 = os.path.join(TMP, "red2.pdf")
    rep = drawdiff.redline_pdf(a, b, out1, align=None, log=_quiet)
    drawdiff.redline_pdf(a, b, out2, align=None, log=_quiet)
    with open(out1, "rb") as f1, open(out2, "rb") as f2:
        assert f1.read() == f2.read(), "redline bytes not deterministic"
    doc = fitz.open(out1)
    assert len(doc) == 1
    assert abs(doc[0].rect.width - W) < 0.5
    assert abs(doc[0].rect.height - H) < 0.5
    text = doc[0].get_text()
    assert "SLIPSHEET" in text and "change region" in text
    # region tags present: a drawn delta triangle + a plain number per
    # region (WinAnsi carries no Greek delta glyph)
    words = {w[4] for w in doc[0].get_text("words")}
    for i in range(1, len(rep["regions"]) + 1):
        assert str(i) in words, (i, sorted(words)[:20])
    doc.close()
    assert rep["totals"]["removed"] >= 1 and rep["totals"]["added"] >= 1


def test_honest_failures():
    # raster-only page: extract_segments' ValueError surfaces untouched
    path = os.path.join(TMP, "raster.pdf")
    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    pm = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), False)
    pm.set_rect(pm.irect, (200, 200, 200))
    page.insert_image(fitz.Rect(100, 100, 300, 300), pixmap=pm)
    doc.save(path)
    doc.close()
    a = _make("h_a.pdf", _GRID)
    try:
        drawdiff.diff_pages(a, path, align=None, log=_quiet)
        raise AssertionError("raster page did not raise")
    except ValueError as e:
        assert "vector" in str(e).lower(), e
    # page-size mismatch warns
    small = os.path.join(TMP, "small.pdf")
    doc = fitz.open()
    pg = doc.new_page(width=W / 2, height=H / 2)
    pg.draw_line(fitz.Point(50, 50), fitz.Point(200, 50))
    doc.save(small)
    doc.close()
    rep = drawdiff.diff_pages(a, small, align=None, log=_quiet)
    assert any("size" in w for w in rep["warnings"]), rep["warnings"]


def test_determinism():
    a = _make("d_a.pdf", _GRID)
    b = _make("d_b.pdf", _GRID[1:])
    r1 = drawdiff.diff_pages(a, b, align=None, log=_quiet)
    r2 = drawdiff.diff_pages(a, b, align=None, log=_quiet)
    assert r1 == r2


def main():
    try:
        test_exact_echo()
        print("PASS exact echo -> zero diffs, zero regions")
        test_counted_edits()
        print("PASS counted edits (3 removed / 3 added incl. a move)")
        test_collinear_split_merge()
        print("PASS collinear split/merge/gap -> ZERO false diffs")
        test_extension()
        print("PASS extension -> one added interval, length ~50 pt")
        test_rigid_transform()
        print("PASS rigid transform: rotation-sign pinned; auto_align path")
        test_region_clustering()
        print("PASS region clustering (3 corners -> 3 regions, Δ-ordered)")
        test_word_layer()
        print("PASS word layer (changed dimension -> text region, no lines)")
        test_rotate90()
        print("PASS /Rotate 90 pages diff like their rotation-0 twins")
        test_renderer()
        print("PASS redline renderer (deterministic bytes, size, legend)")
        test_honest_failures()
        print("PASS honest failures (raster ValueError, size warning)")
        test_determinism()
        print("PASS determinism")
        print("DRAWDIFF TESTS PASSED")
    finally:
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("DRAWDIFF TEST FAILED:", e)
        sys.exit(1)
