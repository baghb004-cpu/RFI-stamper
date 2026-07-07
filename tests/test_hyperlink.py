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


def _words_under(page, lk) -> list:
    r = fitz.Rect(lk["from"]) * page.derotation_matrix
    return [w[4] for w in page.get_text("words")
            if (fitz.Rect(w[:4]) & r).get_area() > 0]


def test_no_substring_false_positives():
    """P-1, P-10 and P-100 coexist (single/double/triple-digit sheets are the
    norm in real sets).  A reference to P-100 must NOT also spawn wrong-target
    links to P-1 / P-10 over the P-1-0-0 substring, and a phone number that
    merely starts 'P 1...' must not be linked at all.  Regression for the
    substring-match bug in search_for."""
    src = os.path.join(TD, "fp.pdf")
    out = os.path.join(TD, "fp_out.pdf")
    doc = fitz.open()
    for _ in range(4):
        doc.new_page(width=612, height=792)
    doc[0].insert_text((72, 120), "REFER TO P-100 FOR OVERALL", fontsize=12)
    doc[0].insert_text((72, 150), "CALL P 105550100 FOR RFI", fontsize=12)
    doc[0].insert_text((72, 180), "DIMENSION P-1 TYP", fontsize=12)
    doc[1].insert_text((505, 760), "P-1", fontsize=12)      # p2
    doc[2].insert_text((505, 760), "P-10", fontsize=12)     # p3
    doc[3].insert_text((505, 760), "P-100", fontsize=12)    # p4
    doc.save(src)
    doc.close()

    idx = SheetIndex(src, log=lambda *a: None)
    assert idx.by_sheet.get("P-1") == 2
    assert idx.by_sheet.get("P-10") == 3
    assert idx.by_sheet.get("P-100") == 4

    auto_link(src, out, log=lambda *a: None)
    doc = fitz.open(out)
    try:
        p1 = doc[0]
        for lk in _goto_links(p1):
            words = _words_under(p1, lk)
            joined = "".join(words)
            # the phone-number digits must never sit under a link
            assert "105550100" not in joined, f"phone linked: {words}"
            tgt = lk["page"] + 1
            # a link over the P-100 token must target page 4 only -- never P-1/P-10
            if "P-100" in words:
                assert tgt == 4, f"P-100 mis-linked to page {tgt}"
            if "P-1" in words and "P-100" not in words:
                assert tgt == 2, f"P-1 mis-linked to page {tgt}"
        # positive: the real refs are present
        tgts = {tuple(sorted(_words_under(p1, lk))): lk["page"] + 1
                for lk in _goto_links(p1)}
        assert tgts.get(("P-100",)) == 4, tgts
        assert tgts.get(("P-1",)) == 2, tgts
        # exactly the two genuine refs, nothing else
        assert len(_goto_links(p1)) == 2, [_words_under(p1, l) for l in _goto_links(p1)]
    finally:
        doc.close()


def test_decimal_not_confused_with_integer():
    """P-101 and P-1.01 are DIFFERENT sheets; a ref to one must never link to
    the other, and a bare '101' is not a sheet reference."""
    src = os.path.join(TD, "dec.pdf")
    out = os.path.join(TD, "dec_out.pdf")
    doc = fitz.open()
    for _ in range(4):
        doc.new_page(width=612, height=792)
    doc[0].insert_text((72, 110), "SEE P-101 AND P-1.01", fontsize=12)
    doc[0].insert_text((72, 140), "DETAIL 101 ON GRID", fontsize=12)  # bare
    doc[1].insert_text((505, 760), "P-101", fontsize=12)    # p2
    doc[2].insert_text((505, 760), "P-201", fontsize=12)    # p3
    doc[3].insert_text((505, 760), "P-1.01", fontsize=12)   # p4
    doc.save(src)
    doc.close()

    idx = SheetIndex(src, log=lambda *a: None)
    assert idx.by_sheet.get("P-101") == 2
    assert idx.by_sheet.get("P-1.01") == 4

    auto_link(src, out, log=lambda *a: None)
    doc = fitz.open(out)
    try:
        p1 = doc[0]
        seen = {}
        for lk in _goto_links(p1):
            words = _words_under(p1, lk)
            tgt = lk["page"] + 1
            if "P-101" in words:
                assert tgt == 2, f"P-101 -> page {tgt}"
                seen["P-101"] = tgt
            if "P-1.01" in words:
                assert tgt == 4, f"P-1.01 -> page {tgt}"
                seen["P-1.01"] = tgt
            # a link whose only token is the bare '101' must not exist
            assert words != ["101"], "bare 101 linked"
        assert seen.get("P-101") == 2 and seen.get("P-1.01") == 4, seen
    finally:
        doc.close()


def test_rotations_180_270():
    """Link clickable rects land over the token on /Rotate 180 and 270 pages.
    (Sheet page precedes the rotated ref page so detection indexes the real
    P-101 sheet first -- the rotated ref must then link to it, not to itself.)"""
    for rot in (180, 270):
        src = os.path.join(TD, f"rot{rot}.pdf")
        out = os.path.join(TD, f"rot{rot}_out.pdf")
        doc = fitz.open()
        doc.new_page(width=612, height=792).insert_text(
            (505, 760), "P-101", fontsize=12)          # p1 = sheet P-101
        ref = doc.new_page(width=612, height=792)       # p2 = rotated ref page
        ref.insert_text((80, 120), "SEE P-101 HERE", fontsize=14)
        ref.set_rotation(rot)
        doc.save(src)
        doc.close()
        auto_link(src, out, log=lambda *a: None)
        doc = fitz.open(out)
        try:
            p2 = doc[1]
            assert p2.rotation == rot
            lks = [lk for lk in _goto_links(p2) if lk["page"] == 0]
            assert lks, f"no link to page 1 on rot{rot}"
            token = p2.search_for("P-101")            # viewer-space rects
            assert token, f"token vanished on rot{rot}"
            assert any(
                _overlaps(fitz.Rect(lk["from"]) * p2.derotation_matrix, t)
                for lk in lks for t in token), \
                f"rot{rot} link rect does not overlap the token"
        finally:
            doc.close()


def test_chain_does_not_stack():
    """Re-running auto_link on its OWN output must not stack duplicate links
    (existing GoTo links are preserved, never duplicated)."""
    src = os.path.join(TD, "plan.pdf")
    out1 = os.path.join(TD, "chain1.pdf")
    out2 = os.path.join(TD, "chain2.pdf")
    auto_link(src, out1, log=lambda *a: None)
    auto_link(out1, out2, log=lambda *a: None)

    def total(p):
        d = fitz.open(p)
        try:
            return sum(len(_goto_links(d[i])) for i in range(d.page_count))
        finally:
            d.close()
    assert total(out1) == total(out2), (total(out1), total(out2))


def test_image_only_page_survives():
    """An image-only (no text) page is skipped without crashing; page count
    and pre-existing non-GoTo links are preserved."""
    src = os.path.join(TD, "img.pdf")
    out = os.path.join(TD, "img_out.pdf")
    doc = fitz.open()
    a = doc.new_page(width=612, height=792)
    a.insert_text((72, 140), "REFER TO P-101", fontsize=12)
    a.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(400, 400, 450, 420),
                   "uri": "http://example.invalid"})
    b = doc.new_page(width=612, height=792)          # image-only: no text
    b.draw_rect(fitz.Rect(100, 100, 300, 300), fill=(0.5, 0.5, 0.5))
    doc.new_page(width=612, height=792).insert_text((505, 760), "P-101", fontsize=12)
    doc.save(src)
    doc.close()

    stats = auto_link(src, out, log=lambda *a: None)
    assert stats.links_added >= 1
    doc = fitz.open(out)
    try:
        assert doc.page_count == 3
        assert _goto_links(doc[1]) == []             # image page untouched
        uris = [lk for lk in doc[0].get_links() if lk.get("kind") == fitz.LINK_URI]
        assert len(uris) == 1                         # pre-existing link kept
    finally:
        doc.close()


if __name__ == "__main__":
    test_sheet_targets()
    test_link_report()
    test_auto_link_and_outline()
    test_links_survive_reopen()
    test_self_links_when_enabled()
    test_rejects_inplace_write()
    test_no_substring_false_positives()
    test_decimal_not_confused_with_integer()
    test_rotations_180_270()
    test_chain_does_not_stack()
    test_image_only_page_survives()
    print("HYPERLINK TESTS PASSED")
