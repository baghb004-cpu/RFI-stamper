"""Tests for rfi_stamper.merge (combine / split / rotate). Plain python, no pytest."""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4, landscape, letter
from reportlab.pdfgen import canvas

from rfi_stamper.merge import (MergeItem, merge_pdfs, parse_page_range,
                               pdf_page_count, rotate_pdf, split_pdf)

quiet = lambda *a, **k: None  # noqa: E731


def make_pdf(path, n_pages, pagesize=letter, tag="doc"):
    c = canvas.Canvas(path, pagesize=pagesize)
    for i in range(1, n_pages + 1):
        c.drawString(72, 72, f"{tag} page {i}")
        c.showPage()
    c.save()
    return path


def make_annotated_pdf(path, n_pages=2):
    make_pdf(path, n_pages, tag="annot")
    doc = fitz.open(path)
    doc[0].add_text_annot(fitz.Point(100, 100), "reviewer note", icon="Comment")
    doc.saveIncr()
    doc.close()
    return path


def expect_value_error(fn, *args):
    try:
        fn(*args)
    except ValueError:
        return
    raise AssertionError(f"expected ValueError from {fn.__name__}{args!r}")


def test_parse_page_range():
    assert parse_page_range("", 4) == [1, 2, 3, 4]
    assert parse_page_range("all", 4) == [1, 2, 3, 4]
    assert parse_page_range(" ALL ", 3) == [1, 2, 3]
    assert parse_page_range("2", 5) == [2]
    assert parse_page_range("1,3-5", 6) == [1, 3, 4, 5]
    assert parse_page_range(" 1 , 3 - 5 ", 6) == [1, 3, 4, 5]
    assert parse_page_range("3-", 5) == [3, 4, 5]
    assert parse_page_range("-3", 5) == [1, 2, 3]
    assert parse_page_range("-2,4-", 5) == [1, 2, 4, 5]
    assert parse_page_range("2,2,1", 3) == [2, 2, 1]      # order kept, dupes allowed
    assert parse_page_range("4-4", 4) == [4]
    for bad in ("0", "6", "1-9", "9-", "-9", "a", "1--2", "-", "1-2-3", "1;2", ","):
        expect_value_error(parse_page_range, bad, 5)
    # regression: unicode digits (Arabic-Indic, full-width) must be rejected,
    # not silently parsed — the grammar is ASCII 0-9 only
    for bad in ("١", "１-２", "1-٢"):
        expect_value_error(parse_page_range, bad, 5)
    # whitespace-only spec behaves like "" (all pages)
    assert parse_page_range("  \t", 4) == [1, 2, 3, 4]
    print("  parse_page_range OK")


def test_merge(tmp):
    a = make_pdf(os.path.join(tmp, "alpha.pdf"), 3, letter, "alpha")
    b = make_pdf(os.path.join(tmp, "bravo.pdf"), 4, A4, "bravo")
    c = make_pdf(os.path.join(tmp, "charlie.pdf"), 2, landscape(letter), "charlie")
    assert pdf_page_count(a) == 3 and pdf_page_count(b) == 4 and pdf_page_count(c) == 2

    out = os.path.join(tmp, "merged.pdf")
    items = [MergeItem(a),                                   # all 3 pages
             MergeItem(b, pages="2-3", rotation=90),         # 2 pages, rotated
             MergeItem(c, pages="2", bookmark="Charlie p2")]  # 1 page
    res = merge_pdfs(items, out, bookmarks=True, log=quiet)
    assert res == {"files": 3, "pages": 6, "out_path": out}

    r = PdfReader(out)
    assert len(r.pages) == 6
    # page boxes preserved: letter, A4, landscape letter widths
    assert abs(float(r.pages[0].mediabox.width) - 612) < 0.5
    assert abs(float(r.pages[3].mediabox.width) - A4[0]) < 0.5
    assert abs(float(r.pages[5].mediabox.width) - 792) < 0.5
    # rotation applied only to the bravo pages
    rots = [int(p.get("/Rotate") or 0) for p in r.pages]
    assert rots == [0, 0, 0, 90, 90, 0], rots
    # outline: one entry per source, pointing at its first included page
    ol = [d for d in r.outline if not isinstance(d, list)]
    got = [(d.title, r.get_destination_page_number(d)) for d in ol]
    assert got == [("alpha", 0), ("bravo", 3), ("Charlie p2", 5)], got

    # no bookmarks when disabled
    out2 = os.path.join(tmp, "merged_nobm.pdf")
    merge_pdfs([MergeItem(a, pages="1")], out2, bookmarks=False, log=quiet)
    assert PdfReader(out2).outline == []

    expect_value_error(merge_pdfs, [], out)
    expect_value_error(merge_pdfs, [MergeItem(a, pages="9")], out)
    expect_value_error(merge_pdfs, [MergeItem(a, rotation=45)], out)
    print("  merge_pdfs OK")


def test_split(tmp):
    src = make_pdf(os.path.join(tmp, "deck.pdf"), 5, letter, "deck")
    # by ranges
    d1 = os.path.join(tmp, "byrange")
    paths = split_pdf(src, d1, ranges="1-2;3-;5", log=quiet)
    assert [os.path.basename(p) for p in paths] == \
        ["deck_part01.pdf", "deck_part02.pdf", "deck_part03.pdf"]
    assert [len(PdfReader(p).pages) for p in paths] == [2, 3, 1]
    # by every=1 -> one file per page
    d2 = os.path.join(tmp, "single")
    paths = split_pdf(src, d2, every=1, prefix="pg", log=quiet)
    assert len(paths) == 5
    assert os.path.basename(paths[0]) == "pg_part01.pdf"
    assert all(len(PdfReader(p).pages) == 1 for p in paths)
    # by every=2 -> 2,2,1
    paths = split_pdf(src, os.path.join(tmp, "pairs"), every=2, log=quiet)
    assert [len(PdfReader(p).pages) for p in paths] == [2, 2, 1]
    # mode errors
    expect_value_error(split_pdf, src, d1)                       # neither mode
    try:
        split_pdf(src, d1, ranges="1", every=1, log=quiet)       # both modes
        raise AssertionError("expected ValueError for both modes")
    except ValueError:
        pass
    print("  split_pdf OK")


def test_rotate(tmp):
    src = make_pdf(os.path.join(tmp, "rot.pdf"), 3, letter, "rot")
    r90 = os.path.join(tmp, "rot90.pdf")
    rotate_pdf(src, r90, 90, pages="2-3", log=quiet)
    r = PdfReader(r90)
    assert len(r.pages) == 3
    assert [int(p.get("/Rotate") or 0) for p in r.pages] == [0, 90, 90]
    # round trip: rotate the same pages by 270 more -> back to 0 (mod 360)
    back = os.path.join(tmp, "rotback.pdf")
    rotate_pdf(r90, back, 270, pages="2-3", log=quiet)
    r = PdfReader(back)
    assert len(r.pages) == 3
    assert [int(p.get("/Rotate") or 0) % 360 for p in r.pages] == [0, 0, 0]
    # default = all pages
    all90 = os.path.join(tmp, "all90.pdf")
    rotate_pdf(src, all90, 90, log=quiet)
    assert [int(p.get("/Rotate") or 0) for p in PdfReader(all90).pages] == [90] * 3
    expect_value_error(rotate_pdf, src, all90, 45)
    print("  rotate_pdf OK")


def test_annotation_preserved(tmp):
    src = make_annotated_pdf(os.path.join(tmp, "notes.pdf"))
    assert "/Annots" in PdfReader(src).pages[0]
    out = os.path.join(tmp, "notes_merged.pdf")
    merge_pdfs([MergeItem(src), MergeItem(src, pages="2")], out, log=quiet)
    r = PdfReader(out)
    assert len(r.pages) == 3
    annots = r.pages[0].get("/Annots")
    assert annots, "annotation lost in merge"
    kinds = [a.get_object().get("/Subtype") for a in annots]
    assert "/Text" in kinds, kinds
    contents = [str(a.get_object().get("/Contents", "")) for a in annots]
    assert any("reviewer note" in s for s in contents), contents
    print("  annotation preservation OK")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        test_parse_page_range()
        test_merge(tmp)
        test_split(tmp)
        test_rotate(tmp)
        test_annotation_preserved(tmp)
    print("MERGE TESTS PASSED")


if __name__ == "__main__":
    main()
