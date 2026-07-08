"""Regression tests for rfi_stamper.markups.model rebuild fixes.

Covers: FreeText text survives on /Rotate 90 AND 270 (#15), stale page-index
skip (#16), empty/short points defensiveness (#28, #38), and CSV formula-
injection guard in to_csv (#29). Run: python tests/test_reb_markups.py
"""
from __future__ import annotations

import csv
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from rfi_stamper.markups import (Markup, MarkupStore, Style,  # noqa: E402
                                 apply_to_pdf)

TD = tempfile.mkdtemp(prefix="reb_markups_test_")


def _make_pdf(path, rotation):
    doc = fitz.open()
    p = doc.new_page(width=612, height=792)
    if rotation:
        p.set_rotation(rotation)
    doc.save(path)
    doc.close()


def test_freetext_text_survives_rotation():
    # #15: FreeText rect was sized in viewer coords then rotated through mat,
    # swapping width/height on /Rotate 90/270 and clipping the text. The full
    # string must survive get_text() on BOTH rotations.
    long_text = "LONG NOTE TEXT THAT MUST NOT BE CLIPPED"
    for rot in (90, 270):
        src = os.path.join(TD, f"rot{rot}.pdf")
        _make_pdf(src, rot)
        mks = [
            Markup.new(1, "text", [(120, 300)], text=long_text,
                       style=Style(color="#D01414")),
            Markup.new(1, "callout", [(120, 400), (300, 500)], text=long_text,
                       style=Style(color="#D01414")),
        ]
        out = os.path.join(TD, f"rot{rot}_out.pdf")
        apply_to_pdf(src, out, mks, log=lambda *a: None)
        doc = fitz.open(out)
        fts = [a.get_text() for a in doc[0].annots()
               if a.type[1] == "FreeText"]
        doc.close()
        joined = "".join(fts)
        assert long_text in joined, (
            f"/Rotate {rot}: text clipped, got {fts!r}")


def test_stale_page_index_skipped():
    # #16: a markup referencing a page beyond the PDF must be skipped-and-logged,
    # not crash the whole batch.
    src = os.path.join(TD, "onepage.pdf")
    _make_pdf(src, 0)
    logged = []
    mks = [
        Markup.new(1, "rect", [(10, 10), (60, 60)], style=Style(color="#D01414")),
        Markup.new(9, "rect", [(10, 10), (60, 60)], style=Style(color="#D01414")),
        Markup.new(0, "rect", [(10, 10), (60, 60)], style=Style(color="#D01414")),
    ]
    out = os.path.join(TD, "onepage_out.pdf")
    res = apply_to_pdf(src, out, mks, log=logged.append)
    assert res["annots"] == 1, res
    assert any("out of range" in s for s in logged), logged
    doc = fitz.open(out)
    assert doc.page_count == 1
    doc.close()


def test_empty_and_short_points_skipped():
    # #28 / #38: empty points list and too-few-points markups must not crash;
    # bbox() is defensive and the loop skips-and-logs them.
    empty = Markup.new(1, "rect", [])
    empty.points = []                       # force empty
    assert empty.bbox() == (0.0, 0.0, 0.0, 0.0)

    src = os.path.join(TD, "short.pdf")
    _make_pdf(src, 0)
    logged = []
    mks = [
        Markup.new(1, "rect", []),                       # empty
        Markup.new(1, "callout", [(50, 50)]),            # 1 pt, needs 2
        Markup.new(1, "line", [(10, 10)]),               # 1 pt, needs 2
        Markup.new(1, "rect", [(10, 10), (60, 60)],
                   style=Style(color="#D01414")),        # valid
    ]
    out = os.path.join(TD, "short_out.pdf")
    res = apply_to_pdf(src, out, mks, log=logged.append)
    assert res["annots"] == 1, res
    assert sum("skipped" in s for s in logged) >= 3, logged


def test_csv_formula_injection_guarded():
    # #29: to_csv must neutralize leading formula characters in string cells.
    store = MarkupStore(os.path.join(TD, "inj.pdf"))
    store.add(Markup.new(1, "text", [(10, 10)],
                         subject="=cmd()", comment="+SUM(A1)",
                         text="-2+3", author="@evil"))
    path = os.path.join(TD, "inj.csv")
    store.to_csv(path)
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    row = rows[1]
    # cols: page,type,subject,comment,text,status,author,...
    assert row[2] == "'=cmd()", row
    assert row[3] == "'+SUM(A1)", row
    assert row[4] == "'-2+3", row
    assert row[6] == "'@evil", row


def main():
    test_freetext_text_survives_rotation()
    test_stale_page_index_skipped()
    test_empty_and_short_points_skipped()
    test_csv_formula_injection_guarded()
    print("REB MARKUPS TESTS OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
