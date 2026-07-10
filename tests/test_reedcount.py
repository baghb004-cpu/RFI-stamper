"""The Reed Count — fixture-symbol recognition acceptance.

* A Loft plate with known stencil placements counts EXACTLY — including
  rotated, flipped and 45°-rotated placements — through the real
  plate-PDF pipeline at the plate's own scale.
* A chair-sized decoy rectangle never counts; it lands in the unknown
  tray honestly.
* Size sanity is a hard gate: a perfect shape at an impossible size is
  rejected WITH its reason (grid bubbles, north arrows).
* Near-identical conventions (mop sink vs single-bowl sink) surface as
  AMBIGUOUS — never silently picked.
* Text-labeled symbols (WH) count only when their label is present.
* A labeled unknown joins the library (human-gated) and counts next run.

Run:  python3.12 tests/test_reedcount.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz                                           # noqa: E402

from rfi_stamper import draft, reedcount              # noqa: E402

_N = [0]


def A(cond, msg=""):
    _N[0] += 1
    assert cond, msg


TMP = tempfile.mkdtemp(prefix="reedcount_test_")


def _plate(fixtures, extras=None):
    """Build a Loft plate with the given fixture placements -> (doc, ppf)."""
    m = draft.DraftModel()
    m.add("wall", [(0, 0), (40, 0)], wtype="stud4")
    m.add("wall", [(0, 0), (0, 30)], wtype="stud4")
    for (x, y), stencil, props in fixtures:
        m.add("fixture", [(x, y)], stencil=stencil, **props)
    for fn in (extras or []):
        fn(m)
    p = os.path.join(TMP, f"plate_{len(os.listdir(TMP))}.pdf")
    res = draft.plate_pdf(m, p)
    frac = res["scale"].split('"')[0]
    num, den = (frac.split("/") + ["1"])[:2]
    ppf = float(num) / float(den) * 72.0
    return fitz.open(p), ppf


def test_exact_counts():
    doc, ppf = _plate([
        ((6, 5), "wc", {}), ((12, 5), "wc", {"rot": 90.0}),
        ((18, 5), "wc", {"flip": True}),
        ((6, 12), "lav", {}), ((12, 12), "lav", {"rot": 180.0}),
        ((30, 5), "fd", {}), ((30, 12), "fd", {"rot": 45.0}),
        ((24, 16), "sink_d", {}), ((34, 20), "wh", {}),
        ((34, 25), "shower", {}), ((14, 20), "ur", {"rot": 270.0}),
        ((6, 25), "tub", {}),
    ])
    rep = reedcount.count_fixtures(doc[0], ppf)
    A(rep["counts"] == {"wc": 3, "lav": 2, "fd": 2, "sink_d": 1, "wh": 1,
                        "shower": 1, "ur": 1, "tub": 1},
      f"exact counts incl. rotated/flipped/45°: {rep['counts']}")
    A(rep["excluded"]["long_linework"] > 0, "walls were filtered as linework")
    A(rep == reedcount.count_fixtures(doc[0], ppf), "deterministic")
    doc.close()


def test_decoy_and_size_gate():
    def chair(m):        # a chair-sized bare rectangle: fixture-scale decoy
        m.add("line", [(20, 22), (21.5, 22), (21.5, 23.5), (20, 23.5),
                       (20, 22)])

    doc, ppf = _plate([((6, 5), "wc", {})], extras=[chair])
    rep = reedcount.count_fixtures(doc[0], ppf)
    A(rep["counts"] == {"wc": 1}, f"decoy never counts: {rep['counts']}")
    A(any(u["size_ft"] == (1.5, 1.5) for u in rep["unknown"]),
      "the decoy landed in the unknown tray")
    # size sanity: the plate's grid bubbles / north arrow shapes match a
    # stencil's SHAPE but not its real footprint -> rejected with reason
    A(any("footprints" in u.get("rejected", "") for u in rep["unknown"]),
      f"size rejections carry their reason: {rep['unknown']}")
    A(rep["excluded"]["size_rejected"] >= 1, "size rejections are counted")
    doc.close()


def test_ambiguity_surfaces():
    doc, ppf = _plate([((24, 8), "mop", {})])
    rep = reedcount.count_fixtures(doc[0], ppf)
    A("mop" not in rep["counts"] and "sink_s" not in rep["counts"],
      f"near-identical conventions are never silently picked: "
      f"{rep['counts']}")
    amb = [u for u in rep["unknown"] if u.get("ambiguous")]
    A(len(amb) == 1 and set(amb[0]["ambiguous"]) == {"mop", "sink_s"},
      f"ambiguity NAMED both candidates: {amb}")
    doc.close()


def test_text_label_gate():
    doc, ppf = _plate([((34, 20), "wh", {})])
    rep = reedcount.count_fixtures(doc[0], ppf)
    A(rep["counts"].get("wh") == 1, "wh with its label counts")
    doc.close()
    # hand-drawn circle at wh size, no WH text anywhere near
    doc2 = fitz.open()
    page = doc2.new_page(width=792, height=612)
    ppf2 = 18.0
    page.draw_circle((300, 300), ppf2 * 1.0)      # 2 ft circle
    page.draw_line((300, 282), (300, 318))        # some innards
    page.draw_line((282, 300), (318, 300))
    rep2 = reedcount.count_fixtures(page, ppf2)
    A("wh" not in rep2["counts"],
      f"a bare circle without 'WH' never counts: {rep2['counts']}")
    A(any("label is missing" in u.get("rejected", "")
          for u in rep2["unknown"]) or rep2["unknown"],
      "the labelless circle is surfaced, not dropped")
    doc2.close()


def test_custom_symbol_learning():
    # a novel symbol (triangle-in-box) is unknown; label it; it counts
    def novel(m):
        m.add("line", [(20, 20), (22, 20), (22, 22), (20, 22), (20, 20)])
        m.add("line", [(20, 20), (21, 22)])
        m.add("line", [(22, 20), (21, 22)])

    doc, ppf = _plate([((6, 5), "wc", {})], extras=[novel])
    rep = reedcount.count_fixtures(doc[0], ppf)
    tray = [u for u in rep["unknown"]
            if abs(u["size_ft"][0] - 2.0) < 0.2
            and abs(u["size_ft"][1] - 2.0) < 0.2]
    A(len(tray) == 1, f"novel symbol lands in the tray: "
                      f"{[u['size_ft'] for u in rep['unknown']]}")
    sym = reedcount.make_symbol(tray[0]["pts"], ppf, "floor sink, custom")
    A(abs(sym["w_in"] - 24.0) < 2.0, f"learned footprint: {sym['w_in']}")
    rep2 = reedcount.count_fixtures(doc[0], ppf,
                                    extra_symbols={"fs_custom": sym})
    A(rep2["counts"].get("fs_custom") == 1,
      f"the labeled shape counts next run: {rep2['counts']}")
    A(rep2["counts"].get("wc") == 1, "existing counts unaffected")
    doc.close()


def test_scale_is_required():
    doc = fitz.open()
    page = doc.new_page(width=792, height=612)
    try:
        reedcount.count_fixtures(page, 0.0)
        A(False, "unscaled count must refuse")
    except ValueError as e:
        A("Story Pole" in str(e), "the refusal points at the Story Pole")
    doc.close()


# --------------------------------------------------------------------------- #

def main():
    tests = [
        (test_exact_counts, "exact counts through the plate pipeline "
                            "(rotated/flipped/45°); deterministic"),
        (test_decoy_and_size_gate, "decoys never count; size gate rejects "
                                   "with reasons"),
        (test_ambiguity_surfaces, "near-identical conventions surface as "
                                  "AMBIGUOUS"),
        (test_text_label_gate, "text-labeled symbols need their label"),
        (test_custom_symbol_learning, "labeled unknowns join the library "
                                      "(human-gated)"),
        (test_scale_is_required, "no verified scale -> honest refusal"),
    ]
    for fn, label in tests:
        fn()
        print(f"PASS {label}")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"REED COUNT TEST PASSED  ({_N[0]} checks)  — the Reed Count")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("REED COUNT TEST FAILED:", e)
        sys.exit(1)
