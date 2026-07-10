"""The Chalk Mark — model-number checkbox marking on cut sheets.

Marks a LEGAL SUBMITTAL, so the tests pin the strictest contract in the
Swatchbook: a mark lands only when the model string matches exactly one
checkbox row, that row holds exactly one visual box, and the box is
pixel-empty.  Everything else — headers without boxes, option grids,
twin rows, pre-checked boxes, absent models — is skipped into the build
log, and the ONLY rendered change of a mark is inside the box bounds
(verify.py discipline).  Report mode detects identically and draws
nothing (byte-identical output).  The real-kit section runs a reference
report build against the installed seed library.

Run:  python3.12 tests/test_chalkmark.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                    # noqa: E402
import fitz                                           # noqa: E402

from rfi_stamper import swatchbook as sb              # noqa: E402

_N = [0]


def A(cond, msg=""):
    _N[0] += 1
    assert cond, msg


def _quiet(*a, **k):
    pass


TMP = tempfile.mkdtemp(prefix="chalk_test_")
SEEDS = os.path.join(sb.bundled_kit_dir(), "seed_library")
HAVE_SEEDS = os.path.isdir(SEEDS) and any(
    f.endswith(".pdf") for f in os.listdir(SEEDS))


def _box(page, x, y, side=8.0):
    page.draw_rect(fitz.Rect(x, y, x + side, y + side),
                   color=(0, 0, 0), width=0.7)
    return fitz.Rect(x, y, x + side, y + side)


def _sheet(path, rows, header=None, precheck=None):
    """A synthetic spec sheet: ``rows`` = [(model_text, n_boxes)] laid out
    down the page (model rows kept well clear of the stamp corner so the
    never-restamp guard sees a clean sheet); ``header`` puts the model in
    a boxless title line; ``precheck`` X-es the box of that row index."""
    doc = fitz.open()
    pg = doc.new_page(width=612, height=792)
    if header:
        pg.insert_text((60, 70), header, fontsize=14)
    y = 160.0
    boxes = []
    for text, n in rows:
        row_boxes = []
        for k in range(n):
            row_boxes.append(_box(pg, 60 + k * 16, y - 8))
        pg.insert_text((60 + n * 16 + 8, y), text, fontsize=10)
        boxes.append(row_boxes)
        y += 40
    if precheck is not None:
        b = boxes[precheck][0]
        pg.draw_line(fitz.Point(b.x0 + 2, b.y0 + 2),
                     fitz.Point(b.x1 - 2, b.y1 - 2), color=(0, 0, 0),
                     width=0.8)
    doc.save(path)
    doc.close()
    return path, boxes


def _px(path, page=0, dpi=150):
    d = fitz.open(path)
    pix = d[page].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, alpha=False)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height,
                                                     pix.width).copy()
    d.close()
    return a


def _build(sheet, chalk, models=("AX-100",), cid="comp_a", tag="T-1"):
    out = os.path.join(TMP, f"pk_{chalk}_{os.path.basename(sheet)}")
    loges: list = []
    sb.build_packet(out, tag, [sheet], chalk=chalk,
                    chalk_models=[(cid, list(models))], chalk_log=loges)
    return out, loges


# --------------------------------------------------------------------------- #
#  the mark itself                                                             #
# --------------------------------------------------------------------------- #

def test_mark_good_row():
    sheet, boxes = _sheet(os.path.join(TMP, "good.pdf"),
                          [("AX-100 WIDGET BOWL", 1),
                           ("BX-200 OTHER THING", 1)],
                          header="AX-100 SERIES SPECIFICATION")
    off, _ = _build(sheet, "off")
    marked, log = _build(sheet, "mark")
    A(len(log) == 1 and log[0]["action"] == "marked", log)
    A(log[0]["page"] == 1 and log[0]["component"] == "comp_a", log[0])
    # the ONLY rendered change is inside the model's box (row 0, box 0)
    a, b = _px(off), _px(marked)
    A(a.shape == b.shape and (a != b).any(), "the mark changed pixels")
    ys, xs = np.nonzero(a != b)
    br = boxes[0][0]
    sc = 150 / 72.0
    A(xs.min() >= br.x0 * sc - 2 and xs.max() <= br.x1 * sc + 2
      and ys.min() >= br.y0 * sc - 2 and ys.max() <= br.y1 * sc + 2,
      f"mark contained in the box: y {ys.min()}-{ys.max()} "
      f"x {xs.min()}-{xs.max()} vs {br}")
    # header occurrence (no box in band) never confuses the gate — and the
    # OTHER model's box stayed untouched (asserted by containment above)
    d = fitz.open(marked)
    A("T-1" in d[0].get_text(), "tag stamp still lands")
    d.close()

    # determinism: an identical mark build is byte-identical
    marked2, _ = _build(sheet, "mark", tag="T-1")
    A(open(marked, "rb").read() == open(marked2, "rb").read(),
      "mark builds are deterministic")


def test_report_mode():
    sheet, _ = _sheet(os.path.join(TMP, "rep.pdf"),
                      [("AX-100 WIDGET BOWL", 1)])
    off, _ = _build(sheet, "off")
    rep, log = _build(sheet, "report")
    A(len(log) == 1 and log[0]["action"] == "would-mark", log)
    A(open(off, "rb").read() == open(rep, "rb").read(),
      "report mode never changes a byte of the packet")


def test_refusals():
    # two boxes in the row -> refused loudly
    s1, _ = _sheet(os.path.join(TMP, "twobox.pdf"),
                   [("AX-100 WIDGET", 2)])
    out, log = _build(s1, "mark")
    A(log[0]["action"] == "skip" and "2 boxes" in log[0]["reason"], log)
    A(np.array_equal(_px(out), _px(_build(s1, "off")[0])),
      "refusal draws nothing")

    # the model in TWO checkbox rows -> refused
    s2, _ = _sheet(os.path.join(TMP, "tworows.pdf"),
                   [("AX-100 STANDARD", 1), ("AX-100 DELUXE", 1)])
    _, log = _build(s2, "mark")
    A(log[0]["action"] == "skip" and "2 checkbox rows" in log[0]["reason"],
      log)

    # a pre-checked box is left alone (idempotence + factory-checked sheets)
    s3, _ = _sheet(os.path.join(TMP, "prechecked.pdf"),
                   [("AX-100 WIDGET", 1)], precheck=0)
    _, log = _build(s3, "mark")
    A(log[0]["action"] == "skip" and "already" in log[0]["reason"], log)

    # model absent / model present but boxless -> informational skips
    s4, _ = _sheet(os.path.join(TMP, "nomodel.pdf"), [("BX-200 THING", 1)])
    _, log = _build(s4, "mark")
    A("not found" in log[0]["reason"], log)
    s5, _ = _sheet(os.path.join(TMP, "nobox.pdf"), [],
                   header="AX-100 SERIES DATA")
    _, log = _build(s5, "mark")
    A("no checkbox row" in log[0]["reason"], log)


def test_join_and_idempotence():
    # "CX 300" written as two words still matches model CX-300 exactly
    sheet, boxes = _sheet(os.path.join(TMP, "join.pdf"),
                          [("CX 300 PUMP", 1), ("CX 3000 PUMP XL", 1)])
    marked, log = _build(sheet, "mark", models=("CX-300",), cid="pump")
    A(log[0]["action"] == "marked", f"two-word join matches: {log}")
    a = _px(marked)
    br = boxes[1][0]        # the CX-3000 row's box must stay empty — exact
    sc = 150 / 72.0         # normalized match, never substring
    inner = a[int(br.y0 * sc) + 3:int(br.y1 * sc) - 3,
              int(br.x0 * sc) + 3:int(br.x1 * sc) - 3]
    A(inner.min() > 200, "CX-3000's box untouched (no substring bleed)")

    # a marked packet's box is no longer empty: re-running the chalk on it
    # marks nothing ("already carries a mark")
    d = fitz.open(marked)
    entries = sb._chalk_component(d, 0, 1, "pump", ["CX-300"], mark=True)
    d.close()
    A(entries[0]["action"] == "skip" and "already" in entries[0]["reason"],
      f"idempotent: {entries}")


# --------------------------------------------------------------------------- #
#  build_all integration + the reference kit                                   #
# --------------------------------------------------------------------------- #

def test_build_all_integration():
    root = os.path.join(TMP, "lib")
    os.makedirs(os.path.join(root, "seed_library"), exist_ok=True)
    sheet, _ = _sheet(os.path.join(root, "seed_library", "widget_a.pdf"),
                      [("AX-100 WIDGET", 1)])
    import json
    with open(os.path.join(root, "manifest.json"), "w") as fh:
        json.dump({"components": [{
            "id": "widget_a", "manufacturer": "MakerA",
            "aliases": ["AX-100"], "file": "seed_library/widget_a.pdf",
            "pages": 1, "sha256": sb._sha256(sheet), "source_url": "",
            "fetched": "", "notes": "", "source": "seed"}]}, fh)
    lib = sb.Library(root)
    recipes = {"project": "X", "packets": [
        {"filename": "21-HB-1.pdf", "tag": "HB-1", "prefix": 21,
         "category": "Hose bibbs", "components": ["widget_a"],
         "missing": [], "flags": []}], "gap_fillers": [], "not_built": []}
    res = sb.build_all(recipes, lib, os.path.join(TMP, "ba_mark"),
                       gap_fillers=False, log=_quiet, chalk="mark")
    A(len(res["chalk"]) == 1 and res["chalk"][0]["action"] == "marked",
      res["chalk"])
    logmd = open(res["log_path"], encoding="utf-8").read()
    A("## Chalk marks (mark mode)" in logmd and "widget_a — marked"
      in logmd, "chalk section in the build log")
    # chalk="off" (the engine default) keeps the log format pre-chalk
    res0 = sb.build_all(recipes, lib, os.path.join(TMP, "ba_off"),
                        gap_fillers=False, log=_quiet)
    A(res0["chalk"] == [] and "Chalk marks" not in open(
        res0["log_path"], encoding="utf-8").read(),
      "off mode: no section, no entries — pre-chalk logs unchanged")


def test_reference_kit_report():
    if not HAVE_SEEDS:
        print("  (reference chalk report SKIPPED — seed kit not installed)")
        return
    lib = sb.Library()
    recipes = sb.load_recipes()
    out = os.path.join(TMP, "ref_report")
    res = sb.build_all(recipes, lib, out, gap_fillers=False, log=_quiet,
                       chalk="report")
    # T1 stays golden with the chalk in report mode
    import json
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "golden_cutsheets", "golden_pagecounts.json")
              ) as fh:
        golden = json.load(fh)
    A(dict(res["built"]) == golden,
      "report mode never disturbs the golden acceptance")
    A(res["chalk"], "the reference sheets produce chalk entries")
    A(any(e["action"] == "would-mark" for e in res["chalk"]),
      "at least one clean single-box model row exists in the real kit")
    A(all(e["action"] in ("would-mark", "skip") for e in res["chalk"]),
      "report mode never marks")
    res2 = sb.build_all(recipes, lib, os.path.join(TMP, "ref_report2"),
                        gap_fillers=False, log=_quiet, chalk="report")
    A(res2["chalk"] == res["chalk"], "chalk detection is deterministic")
    print(f"  (reference report: {len(res['chalk'])} entries, "
          f"{sum(1 for e in res['chalk'] if e['action'] == 'would-mark')} "
          "would-mark)")


# --------------------------------------------------------------------------- #

def main():
    tests = [
        (test_mark_good_row, "mark lands in the box; pixel containment; "
                             "deterministic"),
        (test_report_mode, "report mode: entries, zero byte changes"),
        (test_refusals, "ambiguity/pre-checked/absent all refuse loudly"),
        (test_join_and_idempotence, "word joins, no substring bleed, "
                                    "idempotent"),
        (test_build_all_integration, "build_all + log section + off compat"),
        (test_reference_kit_report, "reference kit report run (seed-gated)"),
    ]
    for fn, label in tests:
        fn()
        print(f"PASS {label}")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"CHALK MARK TEST PASSED  ({_N[0]} checks)  — the Chalk Mark")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("CHALK MARK TEST FAILED:", e)
        sys.exit(1)
