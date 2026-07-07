"""Self-contained tests for rfi_stamper.hyperlink
(run: python3.12 tests/test_hyperlink.py).

Builds a small plan set with fitz and exercises the whole cross-linking path:
sheet-number detection, reference discovery over hyphen/space/zero variants,
native GoTo link creation (including on a /Rotate 90 page), the sheet-index
outline, self-link suppression, and durability across a save/reopen cycle.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from rfi_stamper.core import canon_loose  # noqa: E402
from rfi_stamper.hyperlink import (  # noqa: E402
    LinkStats, auto_link, link_report, sheet_targets,
)
from rfi_stamper.sheets import SheetIndex  # noqa: E402

TD = tempfile.mkdtemp(prefix="hyperlink_test_")


def _build_plan(path: str) -> None:
    """4-page plan set:
      p1  GENERAL INDEX listing P-101, P-201, plus a no-hyphen "P101" ref
      p2  title block reads P-101 (bottom-right)  -> detected sheet P-101
      p3  title block reads P-201 (bottom-right)  -> detected sheet P-201
      p4  /Rotate 90 page carrying a "P-101" reference (mid page, not a corner)
    """
    doc = fitz.open()
    for _ in range(4):
        doc.new_page(width=612, height=792)
    # p1 -- index page (references live in the body, so page stays PAGE-1)
    doc[0].insert_text((72, 90), "GENERAL INDEX", fontsize=16)
    doc[0].insert_text((72, 140), "P-101   FLOOR PLAN", fontsize=12)
    doc[0].insert_text((72, 170), "P-201   DETAILS", fontsize=12)
    doc[0].insert_text((72, 210), "coordinate with P101 typical", fontsize=12)
    # p2 -- sheet P-101 (title-block corner)
    doc[1].insert_text((72, 90), "FLOOR PLAN", fontsize=16)
    doc[1].insert_text((505, 760), "P-101", fontsize=12)
    # p3 -- sheet P-201 (title-block corner)
    doc[2].insert_text((72, 90), "DETAILS", fontsize=16)
    doc[2].insert_text((505, 760), "P-201", fontsize=12)
    # p4 -- rotated reference page
    doc[3].insert_text((72, 120), "SEE P-101 FOR PLAN", fontsize=14)
    doc[3].set_rotation(90)
    doc.save(path)
    doc.close()


def _overlaps(a, b) -> bool:
    inter = fitz.Rect(a) & fitz.Rect(b)
    return not inter.is_empty and inter.get_area() > 0


def _goto_links(page) -> list:
    return [lk for lk in page.get_links() if lk.get("kind") == fitz.LINK_GOTO]


def test_sheet_targets():
    src = os.path.join(TD, "plan.pdf")
    _build_plan(src)
    idx = SheetIndex(src, log=lambda *a: None)

    # detection landed the sheet numbers on the right pages
    assert idx.by_sheet.get("P-101") == 2
    assert idx.by_sheet.get("P-201") == 3

    st = sheet_targets(idx)
    assert st["P-101"] == 2 and st["P-201"] == 3
    # placeholder (undetected) pages are NOT sheet targets
    assert "PAGE-1" not in st and "PAGE-4" not in st
    # loose (zero-stripped) keys are present and resolve
    assert st[canon_loose("P-101")] == 2
    assert st[canon_loose("P-201")] == 3
    # SheetIndex.match resolves leading-zero / loose forms
    assert idx.match("P-101") == 2
    assert idx.match("P-0101") == 2       # leading zero tolerated
    assert idx.match("P-201") == 3


def test_link_report():
    src = os.path.join(TD, "plan.pdf")
    idx = SheetIndex(src, log=lambda *a: None)
    hits = link_report(src, index=idx, log=lambda *a: None)

    # tuple shape
    for src_pg, token, tgt_pg, rect in hits:
        assert isinstance(src_pg, int) and isinstance(tgt_pg, int)
        assert isinstance(token, str) and len(rect) == 4

    p1 = [h for h in hits if h[0] == 1]
    p1_101 = [h for h in p1 if h[1] == "P-101"]
    p1_201 = [h for h in p1 if h[1] == "P-201"]
    # index page references point to the right target pages
    assert p1_101 and all(h[2] == 2 for h in p1_101)
    assert p1_201 and all(h[2] == 3 for h in p1_201)
    # both the hyphenated "P-101" and the no-hyphen "P101" text were found
    assert len(p1_101) >= 2, f"expected >=2 P-101 refs on page 1, got {p1_101}"

    # each reported rect actually sits over some display form of its token
    # (P-101 references include the no-hyphen "P101" occurrence)
    forms = {"P-101": ("P-101", "P101", "P 101"),
             "P-201": ("P-201", "P201", "P 201")}
    doc = fitz.open(src)
    try:
        for src_pg, token, _tgt, rect in p1_101 + p1_201:
            found = []
            for form in forms[token]:
                found += doc[src_pg - 1].search_for(form)
            assert any(_overlaps(rect, f) for f in found), \
                f"rect {rect} not over any {token} hit"
    finally:
        doc.close()

    # a sheet's own number is NOT reported as a link (self-links suppressed)
    assert not [h for h in hits if h[0] == 2 and h[2] == 2]
    assert not [h for h in hits if h[0] == 3 and h[2] == 3]

    # the rotated page's reference is discovered and targets page 2
    p4_101 = [h for h in hits if h[0] == 4 and h[1] == "P-101"]
    assert p4_101 and all(h[2] == 2 for h in p4_101)


def test_auto_link_and_outline():
    src = os.path.join(TD, "plan.pdf")
    out = os.path.join(TD, "linked.pdf")
    stats = auto_link(src, out, log=lambda *a: None)
    assert isinstance(stats, LinkStats)
    assert stats.sheets_indexed == 2
    assert stats.links_added >= 3           # >=2 on p1 + >=1 on p4
    assert stats.pages_touched >= 2

    # input untouched, temp file cleaned up
    assert not os.path.exists(out + ".part")
    assert os.path.exists(out)

    doc = fitz.open(out)
    try:
        # page 1: GoTo links to page 2 (P-101) and page 3 (P-201), over tokens
        p1_links = _goto_links(doc[0])
        tgt_pages = {lk["page"] for lk in p1_links}   # 0-based
        assert 1 in tgt_pages and 2 in tgt_pages, tgt_pages

        hits_101 = doc[0].search_for("P-101")
        assert any(
            lk["page"] == 1 and any(
                _overlaps(fitz.Rect(lk["from"]) * doc[0].derotation_matrix, h)
                for h in hits_101)
            for lk in p1_links), "no P-101 link over the token on page 1"

        # self-links absent by default: sheet pages carry no GoTo links
        assert _goto_links(doc[1]) == []       # page 2 (P-101), only own number
        assert _goto_links(doc[2]) == []       # page 3 (P-201)

        # rotated page round-trip: its link lands on the P-101 token location
        p4 = doc[3]
        assert p4.rotation == 90
        p4_links = [lk for lk in _goto_links(p4) if lk["page"] == 1]
        assert p4_links, "rotated page has no link to page 2"
        search4 = p4.search_for("P-101")       # viewer-space rects
        assert search4, "token vanished on rotated page"
        assert any(
            _overlaps(fitz.Rect(lk["from"]) * p4.derotation_matrix, s)
            for lk in p4_links for s in search4), \
            "rotated link rect does not overlap the token"

        # outline rebuilt as a sheet index -> pages 2 and 3
        toc = doc.get_toc()
        entries = {title: page for _lvl, title, page in toc}
        assert entries.get("P-101") == 2
        assert entries.get("P-201") == 3
    finally:
        doc.close()


def test_links_survive_reopen():
    """Re-open a copy of the linked file and confirm links + outline persist."""
    src = os.path.join(TD, "plan.pdf")
    out = os.path.join(TD, "linked.pdf")
    if not os.path.exists(out):
        auto_link(src, out, log=lambda *a: None)

    # round-trip through a fresh save (simulates downstream re-processing)
    copy = os.path.join(TD, "reopened.pdf")
    d = fitz.open(out)
    d.save(copy)
    d.close()

    doc = fitz.open(copy)
    try:
        total = sum(len(_goto_links(doc[i])) for i in range(doc.page_count))
        assert total >= 3, f"links did not survive reopen: {total}"
        entries = {t: p for _l, t, p in doc.get_toc()}
        assert entries.get("P-101") == 2 and entries.get("P-201") == 3
    finally:
        doc.close()


def test_self_links_when_enabled():
    """link_self=True adds a sheet's own number as a (self) link."""
    src = os.path.join(TD, "plan.pdf")
    out = os.path.join(TD, "linked_self.pdf")
    auto_link(src, out, link_self=True, log=lambda *a: None)
    doc = fitz.open(out)
    try:
        # page 2 (P-101) now links to itself (target 0-based == 1)
        assert any(lk["page"] == 1 for lk in _goto_links(doc[1]))
    finally:
        doc.close()


def test_rejects_inplace_write():
    src = os.path.join(TD, "plan.pdf")
    try:
        auto_link(src, src, log=lambda *a: None)
        raise AssertionError("in-place write was not rejected")
    except ValueError:
        pass


if __name__ == "__main__":
    test_sheet_targets()
    test_link_report()
    test_auto_link_and_outline()
    test_links_survive_reopen()
    test_self_links_when_enabled()
    test_rejects_inplace_write()
    print("HYPERLINK TESTS PASSED")
