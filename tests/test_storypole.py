"""The Story Pole — dimension-anchored autoscale acceptance.

* A Loft plate at a known scale verifies to the exact pt/ft, doors
  corroborate at their standard leaf sizes, and the title-block note
  agrees (the real-engine integration truth).
* A poisoned dimension (text lies about its line) is outvoted and NAMED.
* A half-size print (lines at half the note's scale) REFUSES with the
  exact ratio instead of silently mismeasuring.
* A dimension-free sheet refuses; self-agreement without an independent
  corroborator (doors or note) refuses.
* Verdicts are deterministic and per-sheet (set_verdicts).

Run:  python3.12 tests/test_storypole.py
"""
from __future__ import annotations


import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz                                           # noqa: E402

from rfi_stamper import draft, setscale               # noqa: E402

_N = [0]


def A(cond, msg=""):
    _N[0] += 1
    assert cond, msg


TMP = tempfile.mkdtemp(prefix="storypole_test_")

_K = 0.5522847498307936          # cubic-bezier quarter-circle constant


# --------------------------------------------------------------------------- #
#  hand-drawn fixtures: full control over lines vs text                       #
# --------------------------------------------------------------------------- #

def _draw_dim(page, x, y, length_pt, text):
    """A horizontal dimension: line + ticks + centered text above."""
    page.draw_line((x, y), (x + length_pt, y))
    for ex in (x, x + length_pt):
        page.draw_line((ex, y - 4), (ex, y + 4))
    page.insert_text((x + length_pt / 2 - 14, y - 4), text, fontsize=7)


def _draw_door(page, cx, cy, r_pt):
    """A door swing: quarter arc + leaf line anchored at the hinge."""
    page.draw_bezier((cx + r_pt, cy), (cx + r_pt, cy + _K * r_pt),
                     (cx + _K * r_pt, cy + r_pt), (cx, cy + r_pt))
    page.draw_line((cx, cy), (cx, cy + r_pt))


def _sheet(path, dims, doors=(), note=None):
    """dims: [(length_pt, text)] stacked rows; doors: [r_pt]."""
    doc = fitz.open()
    page = doc.new_page(width=792, height=612)
    y = 80
    for length_pt, text in dims:
        _draw_dim(page, 60, y, length_pt, text)
        y += 40
    x = 500
    for r in doors:
        _draw_door(page, x, 400, r)
        x += 3 * r
    if note:
        page.insert_text((600, 580), f"SCALE: {note}", fontsize=9)
    doc.save(path)
    doc.close()
    return path


def _open(path):
    return fitz.open(path)


# --------------------------------------------------------------------------- #

def test_loft_plate():
    m = draft.DraftModel()
    w1 = m.add("wall", [(0, 0), (40, 0)], wtype="stud4")
    w2 = m.add("wall", [(0, 0), (0, 30)], wtype="stud4")
    m.add("wall", [(40, 0), (40, 30)], wtype="stud4")
    m.add("dim", [(0, 0), (40, 0), (20, -4)])
    m.add("dim", [(0, 0), (0, 30), (-4, 15)])
    m.add("dim", [(40, 0), (40, 30), (44, 15)])
    m.add("dim", [(0, 0), (12, 0), (6, -8)])
    m.add("dim", [(12, 0), (40, 0), (26, -8)])
    m.add("dim", [(0, 30), (40, 30), (20, 34)])
    m.add("door", [], host=w1.id, t=0.3, width_in=36.0)
    m.add("door", [], host=w2.id, t=0.5, width_in=32.0)
    p = os.path.join(TMP, "plate.pdf")
    res = draft.plate_pdf(m, p)
    A(res["fit"], "plate fits")
    # the plate label tells us the truth: N/8" = 1'-0" -> N*9 pt/ft
    frac = res["scale"].split('"')[0]
    num, den = (frac.split("/") + ["1"])[:2]
    expected = float(num) / float(den) * 72.0
    doc = _open(p)
    v = setscale.sheet_verdict(doc[0])
    A(v["status"] == "PASS", f"plate verdict: {v['reasons']}")
    A(abs(v["pt_per_ft"] - expected) <= 0.005 * expected,
      f"exact scale: {v['pt_per_ft']} != {expected}")
    A(v["label"] == res["scale"], f"ladder label: {v['label']}")
    A(len(v["witnesses"]) >= 5, "enough witnesses")
    A(v["note"] and v["note"]["ppf"] == expected, "title note read")
    leafs = sorted(d["nearest_std_in"] for d in v["door_checks"])
    A(leafs == [32, 36] and all(d["ok"] for d in v["door_checks"]),
      f"doors corroborate at standard sizes: {v['door_checks']}")
    # note-tail tokens never pollute the named-outlier list
    A(all("1'-0" != o["text"] for o in v["outliers"]),
      f"no scale-note debris in outliers: {v['outliers']}")
    doc.close()


def test_poisoned_dimension_named():
    ppf = 18.0
    dims = [(ppf * ft, f"{ft}'-0\"") for ft in (10, 12, 14, 16, 20, 24)]
    dims.append((ppf * 10, "12'-0\""))         # the lie: line is 10 ft
    p = _sheet(os.path.join(TMP, "poison.pdf"), dims,
               doors=(ppf * 3.0,), note='1/4" = 1\'-0"')
    doc = _open(p)
    v = setscale.sheet_verdict(doc[0])
    A(v["status"] == "PASS", f"poison outvoted: {v['reasons']}")
    A(abs(v["pt_per_ft"] - ppf) < 0.01, "scale unpolluted by the lie")
    named = [o for o in v["outliers"] if o["text"] == "12'-0\""
             and abs(o["implied_ratio"] - 10 / 12) < 0.02]
    A(len(named) == 1, f"the mistyped dimension is NAMED: {v['outliers']}")
    doc.close()


def test_half_size_print_refuses():
    ppf = 9.0                                   # drawn half of the note's 18
    dims = [(ppf * ft, f"{ft}'-0\"") for ft in (10, 12, 14, 16, 20)]
    p = _sheet(os.path.join(TMP, "half.pdf"), dims, note='1/4" = 1\'-0"')
    doc = _open(p)
    v = setscale.sheet_verdict(doc[0])
    A(v["status"] == "REFUSED", "half-size print refuses")
    A(any("0.500x" in r for r in v["reasons"]),
      f"the exact ratio is in the refusal: {v['reasons']}")
    doc.close()


def test_refusals():
    # no dimensions at all
    doc = fitz.open()
    page = doc.new_page(width=792, height=612)
    page.insert_text((100, 100), "GENERAL NOTES", fontsize=12)
    v = setscale.sheet_verdict(page)
    A(v["status"] == "REFUSED" and "no dimension witnesses" in v["reasons"][0],
      f"blank sheet refuses: {v['reasons']}")
    doc.close()
    # too few witnesses
    p = _sheet(os.path.join(TMP, "few.pdf"),
               [(18.0 * 10, "10'-0\""), (18.0 * 12, "12'-0\"")])
    doc = _open(p)
    v = setscale.sheet_verdict(doc[0])
    A(v["status"] == "REFUSED" and "need 5" in v["reasons"][0],
      f"two witnesses refuse: {v['reasons']}")
    doc.close()
    # self-agreement without any independent corroborator
    p = _sheet(os.path.join(TMP, "lonely.pdf"),
               [(18.0 * ft, f"{ft}'-0\"") for ft in (10, 12, 14, 16, 20)])
    doc = _open(p)
    v = setscale.sheet_verdict(doc[0])
    A(v["status"] == "REFUSED"
      and any("nothing independent corroborates" in r for r in v["reasons"]),
      f"self-agreement alone refuses: {v['reasons']}")
    doc.close()
    # doors that do NOT land on standard sizes refuse (no note to rescue)
    p = _sheet(os.path.join(TMP, "odd_doors.pdf"),
               [(18.0 * ft, f"{ft}'-0\"") for ft in (10, 12, 14, 16, 20)],
               doors=(18.0 * 3.6, 18.0 * 2.3))   # 43.2" and 27.6" leaves
    doc = _open(p)
    v = setscale.sheet_verdict(doc[0])
    A(v["status"] == "REFUSED"
      and any("do not corroborate" in r for r in v["reasons"]),
      f"off-standard doors refuse: {v['reasons']}")
    doc.close()


def test_doors_alone_corroborate():
    ppf = 18.0
    dims = [(ppf * ft, f"{ft}'-0\"") for ft in (10, 12, 14, 16, 20)]
    p = _sheet(os.path.join(TMP, "doors.pdf"), dims,
               doors=(ppf * 3.0, ppf * 2.5))    # 36" and 30" leaves, no note
    doc = _open(p)
    v = setscale.sheet_verdict(doc[0])
    A(v["status"] == "PASS", f"doors corroborate without a note: {v['reasons']}")
    A(all(d["ok"] for d in v["door_checks"]) and len(v["door_checks"]) == 2,
      f"both doors land on standard: {v['door_checks']}")
    # a full circle (north arrow) must never register as a door
    doc2 = fitz.open()
    page = doc2.new_page(width=792, height=612)
    page.draw_circle((300, 300), 30)
    A(setscale._door_candidates(page) == [], "full circle is not a door")
    doc2.close()
    doc.close()


def test_set_verdicts_and_determinism():
    ppf = 18.0
    p = _sheet(os.path.join(TMP, "set_a.pdf"),
               [(ppf * ft, f"{ft}'-0\"") for ft in (10, 12, 14, 16, 20)],
               doors=(ppf * 3.0, ppf * 3.0), note='1/4" = 1\'-0"')
    # append a refusing page into one document
    doc = fitz.open(p)
    doc.new_page(width=792, height=612)
    p2 = os.path.join(TMP, "set.pdf")
    doc.save(p2)
    doc.close()
    vs = setscale.set_verdicts(p2)
    A([v["page"] for v in vs] == [1, 2], "1-based page numbers")
    A(vs[0]["status"] == "PASS" and vs[1]["status"] == "REFUSED",
      "per-sheet verdicts, never inherited")
    A(vs == setscale.set_verdicts(p2), "deterministic")
    # engineering-style note reads too
    doc = fitz.open()
    page = doc.new_page(width=792, height=612)
    page.insert_text((100, 100), 'SCALE: 1" = 20\'', fontsize=9)
    n = setscale._scale_note(page)
    A(n and abs(n["ppf"] - 72.0 / 20.0) < 1e-9, f"engineering note: {n}")
    doc.close()
    # scale_label snaps to the ladder and flags off-ladder values
    A(setscale.scale_label(18.0) == "1/4\" = 1'-0\"", "ladder label")
    A("non-standard" in setscale.scale_label(11.3), "off-ladder is honest")


def _cad_sheet(path, shrink=1.0, ruler=True, second_view=None):
    """The CAD-sheet conventions: a view-title bar (bubble + name +
    ref + per-view scale) and the margin print-check ruler (rotated
    text + a declared 1-inch bracket line), scaled by ``shrink``."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    if ruler:
        page.insert_text((30, 700), "THIS LINE IS 1 INCH LONG WHEN "
                                    "PRINTED TO FULL SCALE",
                         fontsize=6, rotate=90)
        page.draw_line((40, 700), (40, 700 - 72 * shrink))
        page.draw_line((36, 700), (44, 700))
        page.draw_line((36, 700 - 72 * shrink), (44, 700 - 72 * shrink))
    page.draw_circle((200, 100), 12)
    page.insert_text((196, 105), "1", fontsize=12)
    page.insert_text((220, 103), "DEMO PLAN - FLOOR 1", fontsize=12)
    page.draw_line((190, 112), (460, 112))
    page.insert_text((220, 126), "AD2.10", fontsize=8)
    page.insert_text((270, 126), '1/4" = 1\'-0"', fontsize=8)
    if second_view:
        page.insert_text((220, 503), "ENLARGED RESTROOM", fontsize=12)
        page.draw_line((190, 512), (460, 512))
        page.insert_text((220, 526), "AD2.11", fontsize=8)
        page.insert_text((270, 526), second_view, fontsize=8)
    page.draw_line((200, 300), (400, 300))     # bare walls, no dimensions
    page.draw_line((200, 300), (200, 450))
    doc.save(path)
    doc.close()
    return path


def test_view_title_and_print_check():
    # the dimension-poor CAD sheet: view-title scale + print-check ruler
    # form their own two-family PASS
    p = _cad_sheet(os.path.join(TMP, "cad.pdf"))
    doc = _open(p)
    v = setscale.sheet_verdict(doc[0])
    A(v["status"] == "PASS" and abs(v["pt_per_ft"] - 18.0) < 1e-6,
      f"declared 1/4\" verified by the ruler: {v['reasons']}")
    A(v["label"] == "1/4\" = 1'-0\"", v["label"])
    A(v["check_line"]["ratio"] == 1.0 and v["check_line"]["len_pt"] == 72.0,
      f"ruler measured: {v['check_line']}")
    vn = v["view_notes"]
    A(len(vn) == 1 and vn[0]["title"] == "DEMO PLAN - FLOOR 1"
      and vn[0]["ref"] == "AD2.10", f"view title read: {vn}")
    # the ref's digits never leak into the note ("AD2.10" + "1/4\"" once
    # read as ten-and-a-quarter inches — the per-line law)
    A(abs(vn[0]["ppf"] - 18.0) < 1e-6, f"note value exact: {vn[0]}")
    doc.close()
    # the half-size print CALIBRATES CORRECTLY (ratio known), not refused
    p2 = _cad_sheet(os.path.join(TMP, "cad_half.pdf"), shrink=0.5)
    doc = _open(p2)
    v2 = setscale.sheet_verdict(doc[0])
    A(v2["status"] == "PASS" and abs(v2["pt_per_ft"] - 9.0) < 1e-6,
      f"half print calibrated via the ruler: {v2['reasons']}")
    A(v2["check_line"]["ratio"] == 0.5, v2["check_line"])
    A("0.5x print" in v2["reasons"][0], v2["reasons"])
    doc.close()
    # note WITHOUT the ruler: refuses and says exactly what is missing
    p3 = _cad_sheet(os.path.join(TMP, "cad_noruler.pdf"), ruler=False)
    doc = _open(p3)
    v3 = setscale.sheet_verdict(doc[0])
    A(v3["status"] == "REFUSED"
      and "no print-check line" in v3["reasons"][0], v3["reasons"])
    doc.close()
    # two views declaring DIFFERENT scales: surfaced, never picked from
    p4 = _cad_sheet(os.path.join(TMP, "cad_two.pdf"),
                    second_view='1/2" = 1\'-0"')
    doc = _open(p4)
    v4 = setscale.sheet_verdict(doc[0])
    A(v4["status"] == "REFUSED"
      and "declared scales" in v4["reasons"][0]
      and "distinguishes" in v4["reasons"][0], v4["reasons"])
    A(len(v4["view_notes"]) == 2, v4["view_notes"])
    doc.close()
    # deterministic
    doc = _open(p)
    A(setscale.sheet_verdict(doc[0]) == v, "deterministic")
    doc.close()


def test_print_check_reconciles_witnesses():
    # witnesses + note + ruler all present on a half print: the ratio
    # EXPLAINS the disagreement and the sheet passes at the measured scale
    ppf = 9.0                                   # drawn at half of 1/4"
    dims = [(ppf * ft, f"{ft}'-0\"") for ft in (10, 12, 14, 16, 20)]
    p = _sheet(os.path.join(TMP, "half_ruled.pdf"), dims,
               note='1/4" = 1\'-0"')
    doc = fitz.open(p)
    page = doc[0]
    page.insert_text((30, 560), "THIS LINE IS 1 INCH LONG WHEN PRINTED "
                                "TO FULL SCALE", fontsize=6, rotate=90)
    page.draw_line((40, 560), (40, 560 - 36))   # the ruler shrank too
    doc.saveIncr()
    v = setscale.sheet_verdict(doc[0])
    A(v["status"] == "PASS" and abs(v["pt_per_ft"] - 9.0) < 0.01,
      f"the ruler explains the half print: {v['reasons']}")
    A("0.5" in v["reasons"][0], v["reasons"])
    doc.close()
    # but a note x ratio that STILL disagrees with witnesses refuses
    p2 = _sheet(os.path.join(TMP, "conflict.pdf"), dims,
                note='1/8" = 1\'-0"')           # note says 9 @ full size
    doc = fitz.open(p2)
    page = doc[0]
    page.insert_text((30, 560), "THIS LINE IS 1 INCH LONG WHEN PRINTED "
                                "TO FULL SCALE", fontsize=6, rotate=90)
    page.draw_line((40, 560), (40, 560 - 36))   # ...yet ruler says half
    doc.saveIncr()
    v2 = setscale.sheet_verdict(doc[0])
    A(v2["status"] == "REFUSED"
      and "conflicting evidence" in v2["reasons"][0], v2["reasons"])
    doc.close()


def test_scale_cells_and_disambiguation():
    # the boxed title-block table: "SCALE" cell + value cell below +
    # a big detail number beside (the other declaration convention)
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((100, 500), "SCALE", fontsize=9)
    page.insert_text((100, 518), '1/8"=1\'-0"', fontsize=9)
    page.insert_text((190, 515), "2", fontsize=22)
    ds = setscale.declared_scales(page)
    A(len(ds) == 1 and abs(ds[0]["ppf"] - 9.0) < 1e-6
      and ds[0]["ref"] == "detail 2", f"SCALE cell + detail number: {ds}")
    doc.close()
    # the P101 case: title block declares one scale, the viewport another
    # — the sheet's own dimensions PICK which one governs
    ppf = 72.0 / 40.0                        # the viewport truth: 1"=40'-0"
    dims = [(ppf * ft, f"{ft}'-0\"") for ft in (40, 60, 80, 100, 120)]
    p = _sheet(os.path.join(TMP, "p101.pdf"), dims)
    doc = fitz.open(p)
    page = doc[0]
    page.insert_text((450, 560), "SCALE", fontsize=8)     # title-block cell
    page.insert_text((450, 574), '1/8"=1\'-0"', fontsize=8)
    page.insert_text((100, 520), "SITE PLAN", fontsize=12)
    page.draw_line((90, 528), (300, 528))
    page.insert_text((100, 542), "P101", fontsize=8)
    page.insert_text((150, 542), '1"=40\'-0"', fontsize=8)
    doc.saveIncr()
    v = setscale.sheet_verdict(doc[0])
    A(v["status"] == "PASS" and abs(v["pt_per_ft"] - ppf) < 0.01,
      f"dimensions pick the governing declaration: {v['reasons']}")
    A("1\"=40'" in v["reasons"][0]
      and "details/insets" in v["reasons"][0], v["reasons"])
    A(len({d["ppf"] for d in v["view_notes"]}) == 2, v["view_notes"])
    doc.close()


def test_fingerprint_and_paper():
    from rfi_stamper.sheets import paper_name
    A(paper_name(42 * 72, 30 * 72) == "ARCH E1", "42x30 landscape = E1")
    A(paper_name(30 * 72, 42 * 72) == "ARCH E1", "orientation-blind")
    A(paper_name(8.5 * 72, 11 * 72) == "ANSI A (letter)", "letter")
    A(paper_name(36 * 72, 24 * 72) == "ARCH D", "arch d")
    A(paper_name(500, 500) is None, "off-chart is honest")
    # the learning fingerprint: layout-only, salted, untraceable
    def make(title):
        doc = fitz.open()
        page = doc.new_page(width=42 * 72, height=30 * 72)
        page.insert_text((2800, 500), title, fontsize=14)   # firm content
        page.insert_text((2800, 800), "SCALE", fontsize=9)
        page.insert_text((2800, 818), '1/4"=1\'-0"', fontsize=9)
        page.draw_line((2700, 50), (2700, 2100))            # tb edge
        return doc
    d1, d2 = make("SOMETHING ELEMENTARY SCHOOL"), make("A DIFFERENT JOB")
    f1 = setscale.fingerprint(d1[0], "saltA")
    f2 = setscale.fingerprint(d2[0], "saltA")
    A(f1 == f2, "same LAYOUT, different content -> same fingerprint")
    A(setscale.fingerprint(d1[0], "saltB") != f1,
      "a different install salt changes everything (untraceable)")
    A(len(f1) == 16 and all(c in "0123456789abcdef" for c in f1),
      "opaque hex only — nothing readable is stored")
    d3 = fitz.open()
    d3.new_page(width=36 * 72, height=24 * 72)              # other layout
    A(setscale.fingerprint(d3[0], "saltA") != f1,
      "different layout -> different fingerprint")
    d1.close()
    d2.close()
    d3.close()


# --------------------------------------------------------------------------- #

def main():
    tests = [
        (test_loft_plate, "Loft plate at known scale -> exact pt/ft, "
                          "doors + note corroborate"),
        (test_poisoned_dimension_named, "a mistyped dimension is outvoted "
                                        "and NAMED"),
        (test_half_size_print_refuses, "half-size print refuses with the "
                                       "exact ratio"),
        (test_refusals, "blank / thin / uncorroborated / odd-door refusals"),
        (test_doors_alone_corroborate, "door openings corroborate without "
                                       "a note; circles never doors"),
        (test_set_verdicts_and_determinism, "set-level verdicts, notes, "
                                            "labels, determinism"),
        (test_view_title_and_print_check, "view-title scales + the print-"
                                          "check ruler (the CAD-sheet "
                                          "conventions)"),
        (test_print_check_reconciles_witnesses, "the ruler explains a "
                                                "reduced print; conflicts "
                                                "still refuse"),
        (test_scale_cells_and_disambiguation, "SCALE cells parse; "
                                              "dimensions pick between "
                                              "conflicting declarations"),
        (test_fingerprint_and_paper, "paper names + the salted layout "
                                     "fingerprint (untraceable learning)"),
    ]
    for fn, label in tests:
        fn()
        print(f"PASS {label}")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"STORY POLE TEST PASSED  ({_N[0]} checks)  — the Story Pole")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("STORY POLE TEST FAILED:", e)
        sys.exit(1)
