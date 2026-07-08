"""Regression tests for the "linkalign" rebuild group
(run: python tests/test_reb_linkalign.py).

Covers:
  * hyperlink #07 -- space-separated running text ("TYPE A 1 HR RATED") must
    NOT produce a false GoTo cross-link to an unrelated sheet A-1.
  * hyperlink #21 -- the cheap per-page candidate pre-filter must not change
    link results: a genuinely present reference still links.
  * align   #36 -- comparison_image / make_comparison_pdf must emit a visible
    warning through the log= callback when overlay linework is clipped off the
    base canvas after alignment.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from rfi_stamper.hyperlink import auto_link, _variants  # noqa: E402
from rfi_stamper.sheets import SheetIndex  # noqa: E402
from rfi_stamper.align import (  # noqa: E402
    AlignResult, comparison_image, make_comparison_pdf,
)

TD = tempfile.mkdtemp(prefix="reb_linkalign_")


def _goto_links(page) -> list:
    return [lk for lk in page.get_links() if lk.get("kind") == fitz.LINK_GOTO]


def _words_under(page, lk) -> list:
    r = fitz.Rect(lk["from"]) * page.derotation_matrix
    return [w[4] for w in page.get_text("words")
            if (fitz.Rect(w[:4]) & r).get_area() > 0]


def test_variants_drops_space_form():
    """#07: the space-separated 'A 1' variant is no longer emitted; hyphen and
    no-space forms remain so mid-token splits still match."""
    forms = _variants("A-1")
    assert "A 1" not in forms, forms
    assert "A-1" in forms and "A1" in forms, forms


def test_no_false_link_from_space_text():
    """#07: 'TYPE A 1 HR RATED' contains the words 'A' and '1' as independent
    tokens; it must NOT link to sheet A-1.  A genuine 'A-1' reference on the
    same page still links."""
    src = os.path.join(TD, "space.pdf")
    out = os.path.join(TD, "space_out.pdf")
    doc = fitz.open()
    for _ in range(3):
        doc.new_page(width=612, height=792)
    # p1: fire-rating note (false-positive bait) + a genuine A-1 reference
    doc[0].insert_text((72, 120), "WALL TYPE A 1 HR RATED PARTITION", fontsize=12)
    doc[0].insert_text((72, 160), "SEE A-1 FOR PLAN", fontsize=12)
    doc[1].insert_text((505, 760), "A-1", fontsize=12)   # p2 = sheet A-1
    doc[2].insert_text((505, 760), "A-2", fontsize=12)   # p3 = sheet A-2
    doc.save(src)
    doc.close()

    idx = SheetIndex(src, log=lambda *a: None)
    assert idx.by_sheet.get("A-1") == 2, idx.by_sheet
    assert idx.by_sheet.get("A-2") == 3, idx.by_sheet

    auto_link(src, out, log=lambda *a: None)
    doc = fitz.open(out)
    try:
        p1 = doc[0]
        links = _goto_links(p1)
        # every A-1 link must sit over the hyphenated token, never over the
        # 'A' / '1' words of the fire-rating note
        for lk in links:
            words = _words_under(p1, lk)
            joined = " ".join(words)
            assert "HR" not in joined and "RATED" not in joined and \
                "TYPE" not in joined, f"false link over rating text: {words}"
        # the genuine A-1 reference still links to page 2
        a1 = [lk for lk in links if lk["page"] == 1]
        assert a1, "genuine A-1 reference lost its link"
        for lk in a1:
            assert "A-1" in _words_under(p1, lk), _words_under(p1, lk)
    finally:
        doc.close()


def test_candidate_filter_preserves_present_links():
    """#21: the per-page candidate pre-filter must not drop links for tokens
    that are actually present (hyphen, no-hyphen, and loose/zero-stripped
    forms)."""
    src = os.path.join(TD, "present.pdf")
    out = os.path.join(TD, "present_out.pdf")
    doc = fitz.open()
    for _ in range(3):
        doc.new_page(width=612, height=792)
    # index page references E-101 both hyphenated and as no-hyphen E101
    doc[0].insert_text((72, 120), "REFER TO E-101 AND E101 TYP", fontsize=12)
    doc[1].insert_text((505, 760), "E-101", fontsize=12)   # p2
    doc[2].insert_text((505, 760), "E-201", fontsize=12)   # p3
    doc.save(src)
    doc.close()

    idx = SheetIndex(src, log=lambda *a: None)
    assert idx.by_sheet.get("E-101") == 2, idx.by_sheet

    auto_link(src, out, log=lambda *a: None)
    doc = fitz.open(out)
    try:
        p1 = doc[0]
        e101 = [lk for lk in _goto_links(p1) if lk["page"] == 1]
        # both the hyphenated and no-hyphen forms survive the pre-filter
        assert len(e101) >= 2, [_words_under(p1, l) for l in _goto_links(p1)]
    finally:
        doc.close()


def _make_linework_pdf(path, off=(0.0, 0.0)):
    """A page of dense linework, drawn shifted by off (pt)."""
    ox, oy = off
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    shape = page.new_shape()
    for i in range(20):
        x = 80.0 + i * 20.0 + ox
        shape.draw_line(fitz.Point(x, 80.0 + oy), fitz.Point(x, 700.0 + oy))
    for j in range(12):
        y = 80.0 + j * 50.0 + oy
        shape.draw_line(fitz.Point(80.0 + ox, y), fitz.Point(560.0 + ox, y))
    shape.finish(width=2.0, color=(0, 0, 0))
    shape.commit()
    doc.save(path)
    doc.close()


def test_clipping_warning_emitted():
    """#36: a large shift pushes overlay linework off the base canvas; the diff
    is then incomplete and comparison_image must warn through log=."""
    base = os.path.join(TD, "clip_base.pdf")
    over = os.path.join(TD, "clip_over.pdf")
    _make_linework_pdf(base)
    _make_linework_pdf(over)

    # a big applied shift guarantees overlay content lands off-canvas
    big = AlignResult(dx=300.0, dy=200.0)
    msgs = []
    comparison_image(base, over, align=big, log=lambda m: msgs.append(m))
    assert any("WARNING" in m and "outside" in m for m in msgs), msgs

    # with no shift (identity), nothing is clipped -> no warning
    msgs2 = []
    comparison_image(base, over, align=AlignResult(), log=lambda m: msgs2.append(m))
    assert not any("WARNING" in m for m in msgs2), msgs2

    # make_comparison_pdf threads log= through and surfaces the same warning
    out_pdf = os.path.join(TD, "clip_compare.pdf")
    msgs3 = []
    make_comparison_pdf(base, over, out_pdf, align=big,
                        log=lambda m: msgs3.append(m))
    assert any("WARNING" in m and "outside" in m for m in msgs3), msgs3


if __name__ == "__main__":
    test_variants_drops_space_form()
    test_no_false_link_from_space_text()
    test_candidate_filter_preserves_present_links()
    test_clipping_warning_emitted()
    print("REB LINKALIGN TESTS PASSED")
