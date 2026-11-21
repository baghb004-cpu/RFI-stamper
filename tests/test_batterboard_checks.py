"""The Batterboard cross-checks, the Red Tags register and the Paper Trail
working together on the sample building (owner brief sections 6 and 7).

Regression cases 4, 5, 9, 10 and 11 at the pipeline level (their module
level halves live in test_papertrail.py and test_redtags.py; case 6 in
test_batterboard_text.py).

Run:  python tests/test_batterboard_checks.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper import dxfread, papertrail as pt, redtags               # noqa: E402
from rfi_stamper.batterboard import crosscheck as xc                      # noqa: E402
from rfi_stamper.batterboard import intake, planread                      # noqa: E402
from rfi_stamper.batterboard import model as M                            # noqa: E402
from rfi_stamper.batterboard import sampleset as ss                       # noqa: E402
from rfi_stamper.batterboard.names import Names                           # noqa: E402

TMP = tempfile.mkdtemp(prefix="bbchecks_")


def run(files, opts=None, reg=None, names=None, run_id="run"):
    opts = opts or M.Options()
    names = names or Names()
    rr = planread.read_drop(intake.classify_drop(files, opts), opts, names)
    trail = pt.Trail()
    reg = reg if reg is not None else redtags.Register()
    reg.begin_run(run_id)
    ctx = xc.run_checks(rr, trail, reg, opts, names)
    reg.end_run()
    return rr, trail, reg, ctx


def said(trail, ctx, check, level, key="summary"):
    return trail.say(ctx.proofs[(check, level)], key).text


def by_label(reg, level, label):
    return [t for t in reg.items(level) if t.label == label]


def t_checks():
    f = ss.build_sample_set(os.path.join(TMP, "full"))
    rr, trail, reg, ctx = run(f["files"])
    l2 = rr.level("L2")
    # ---- case 11: the round column 1 in. off is flagged ------------------- #
    rc = next(e for e in l2.of(M.ROUND_COLUMN) if e.attrs.get("cad") == "flag")
    assert abs(rc.attrs["cad_dev_ft"] - 1.0 / 12.0) < 1e-3
    tag = next(t for t in reg.items("L2") if rc.id in t.elements)
    assert tag.category == "answer" and "1 in." in tag.wrong and "Round column" in tag.wrong
    corner = said(trail, ctx, "XC-CORNER-CAD", "L2")
    assert "2 of 3 round column centers" in corner and "1 round column center" in corner
    assert "22 of 26 wall corners" in corner and "8 of 12 rectangular column corners" in corner
    # the rectangular column 3/8 in. off is between match and flag: a check, not a hold
    near = next(e for e in l2.of(M.COLUMN) if e.attrs.get("cad") == "near")
    t_near = next(t for t in reg.items("L2") if near.id in t.elements)
    assert t_near.category == "confirm" and "3/8 in." in t_near.wrong
    # the wall 3/4 in. off: flagged, and the face comparison sees it too
    w1 = next(e for e in l2.of(M.WALL) if e.mark == "SW1")
    assert w1.attrs["cad"] == "flag" and abs(w1.attrs["face_dev_ft"] - 0.75 / 12) < 1e-3
    assert "12 of 14 wall faces match" in said(trail, ctx, "XC-FACE-CAD", "L2")
    assert all(e.sources == [M.SRC_STRUCT, M.SRC_CAD] for e in l2.of(M.WALL, M.COLUMN, M.ROUND_COLUMN)
               if e.attrs.get("cad") in ("match", "near", "flag"))
    # ---- case 5: coverage is never wider than the check --------------------- #
    plate = said(trail, ctx, "XC-CORNER-CAD", "L2", "plate")
    assert plate.startswith("22 of 26 wall corners") and "within 1/4 in." in plate
    assert "Not compared: slab edges" in plate and "slab openings" in plate
    assert "slab edge" not in plate.split("Not compared:")[0], "an uncompared class is never in the matches"
    shapes = [s_ for s_ in ss.cad_shapes(2) if s_["layer"] != "S-COLS"]
    d5 = os.path.join(TMP, "nocols")
    os.makedirs(d5, exist_ok=True)
    plans = ss.sheet_struct(2).save(os.path.join(d5, "s201.pdf"))
    dxf = os.path.join(d5, "background_L2.dxf")
    dxfread.write_background(dxf, shapes, units="in")
    rr5, trail5, reg5, ctx5 = run([plans, dxf])
    p5 = said(trail5, ctx5, "XC-CORNER-CAD", "L2", "plate")
    head, tail = p5.split("Not compared:")
    assert "wall corners" in head and "column" not in head, head
    assert "rectangular column corners" in tail and "round column centers" in tail, tail
    assert not any(e.attrs.get("cad") != "not compared" for e in rr5.level("L2").of(M.COLUMN, M.ROUND_COLUMN))
    assert not any("column" in t.wrong.lower() for t in reg5.items("L2")), "nothing is claimed about a class that was not compared"
    # ---- case 4: the sentence carries the counts ---------------------------- #
    ends = said(trail, ctx, "XC-ARCH-ENDS", "L2")
    assert "2 of 8 shear wall ends have an architect dimension string" in ends
    assert "2 shear wall ends are tied by the opening centerline only" in ends
    assert "4 shear wall ends have nothing from the architect" in ends
    assert "no dimension" not in ends.lower() and "none" not in ends.lower()
    assert len(by_label(reg, "L2", "Dimension to the concrete is missing")) == 6
    how = sorted(e.attrs["arch"] for e in l2.of(M.SW_END))
    assert how == ["centerline"] * 2 + ["none"] * 4 + ["string"] * 2
    strings = said(trail, ctx, "XC-ARCH-STRINGS", "L2")
    assert "7 of 10 architect dimension strings are the same" in strings
    assert "1 architect dimension string is different" in strings
    assert "1 string(s) with neither end on the structure" in strings
    diff = by_label(reg, "L2", "Architect dimension differs from the structure")[0]
    assert "16'-2\"" in diff.wrong and "16'-0\"" in diff.wrong and "2 in. apart" in diff.wrong
    assert w1.id in diff.elements
    # ---- case 9: mixed provenance --------------------------------------------- #
    cad_wall = next(e for e in l2.of(M.WALL) if e.attrs.get("cad_only"))
    assert cad_wall.prov.method == M.CAD and cad_wall.sources == [M.SRC_CAD]
    assert cad_wall.single_source and "CAD background" in cad_wall.prov.note
    assert cad_wall.source_text() == "CAD background"
    others = [e for e in l2.of(M.WALL) if not e.attrs.get("cad_only")]
    assert all(e.prov.method == M.VECTOR and e.prov.sheet == "S-201" for e in others)
    only = by_label(reg, "L2", "In the CAD background only")
    assert len(only) == 1 and only[0].elements == [cad_wall.id] and only[0].category == "answer"
    assert "it is not on S-201" in only[0].wrong
    assert "1 of 7 CAD walls" in said(trail, ctx, "XC-CAD-ONLY", "L2")
    single = said(trail, ctx, "SINGLE-SOURCE", "L2")
    assert "1 of 7 walls" in single and "all 2 masonry walls" in single
    # ---- the other checks ------------------------------------------------------ #
    assert "3 of 7 walls sit on a wall below; 4 walls do not" in said(trail, ctx, "XC-LEVEL-BELOW", "L2")
    assert len(by_label(reg, "L2", "Wall does not sit on a wall below")) == 4
    thk = by_label(reg, "L2", "Thickness differs from the schedule")[0]
    assert "SW3" in thk.wrong and "8 in." in thk.wrong and "10 in." in thk.wrong
    zone = by_label(reg, "L2", "Schedule and detail give different zone lengths")[0]
    assert "2'-6\"" in zone.wrong and "3'-0\"" in zone.wrong and len(zone.elements) == 4
    for e in l2.of(M.SW_END):
        if e.mark == "SW2":
            assert e.attrs.get("zone_disputed") == (2.5, 3.0) and "zone_length_ft" not in e.attrs, \
                "a disagreement is never resolved by picking one"
        elif e.mark == "SW1":
            assert e.attrs["zone_length_ft"] == 3.0
    ovl = said(trail, ctx, "XC-OVERLAY", "L2")
    assert "1 of 3 overlay openings" in ovl and "approximate" in ovl
    assert len(by_label(reg, "L2", "Opening inside a tied end zone")) == 1
    assert len(by_label(reg, "L2", "Opening through the slab, not on the structural plan")) == 1
    rev = said(trail, ctx, "XC-REVISION", "L2")
    assert "Revision 2 of S-201 against revision 1" in rev and "all 2 changed places" in rev
    assert sorted(str(c["cloud"]) for c in l2.changes) == ["", "2"], l2.changes
    hid = by_label(reg, "L2", "Superseded linework left in the file")[0]
    assert "6 lines" in hid.wrong and "revision cloud 2 is beside it" in hid.wrong
    assert hid.category == "confirm" and hid.boxes[0]["sheet"] == "S-201"
    # level 1: the unconfirmed edge is an open item; what could not be compared is said
    edge = by_label(reg, "L1", "Slab edge run without a heavy plotted line")[0]
    assert edge.category == "answer" and "20'-0\"" in edge.wrong
    skipped = {c for c, lvl, _why in ctx.skips if lvl == "L1"}
    assert {"XC-CORNER-CAD", "XC-ARCH", "XC-LEVEL-BELOW", "XC-OVERLAY", "XC-REVISION"} <= skipped
    assert all("not in the drop" in why or "only one issue" in why
               for _c, lvl, why in ctx.skips if lvl == "L1")
    # every item text came from a proof record, and every reference resolves
    inv = pt.Inventory(sheets=rr.sheets(), items=reg.refs(), rfis={r.rfi for r in rr.rfis},
                       details=set(), detail_unindexed={"S-501"})
    assert pt.unresolved(trail.statements, inv) == []
    for t in reg.items():
        assert t.proofs and all(trail.get(p) for p in t.proofs)
        assert not pt.lint_text(t.wrong + " " + t.needed + " " + t.label), t.ref
    assert trail.say(ctx.trail_scope.id).text.startswith("Not laid out in this run: elevations")
    print("PASS cross-checks: cases 4, 5, 9 and 11 on the sample building; level "
          "below, schedule, zone lengths, overlays, revisions and the Sightline's "
          "findings raised with evidence")
    return f


def t_register(f):
    # ---- case 10 in the pipeline: numbers never change --------------------- #
    path = os.path.join(TMP, "sample.redtags.json")
    names_path = os.path.join(TMP, "sample.names.json")
    reg = redtags.Register(path)
    names = Names(names_path)
    rr, trail, reg, ctx = run(f["files"], reg=reg, names=names, run_id="run1")
    reg.save()
    names.save()
    first = {t.ref: (t.label, t.wrong) for t in reg.items()}
    ids_first = {e.id: e.kind for lv in rr.levels for e in lv.elements}
    # a later issue of the CAD background: one more wall, and the round column fixed
    d = os.path.join(TMP, "later")
    os.makedirs(d, exist_ok=True)
    shapes = ss.cad_shapes(2)
    for sh in shapes:
        if sh["kind"] == "circle" and abs(sh["center"][0] - (ss.CAD_ORIGIN_FT[0] + 48.0 + 1.0 / 12) * 12) < 1e-6:
            sh["center"] = ((ss.CAD_ORIGIN_FT[0] + 48.0) * 12.0, sh["center"][1])
    ox, oy = ss.CAD_ORIGIN_FT
    shapes.insert(0, {"layer": "S-WALL", "kind": "poly", "closed": True,
                      "pts": [((ox + e) * 12, (oy + n) * 12) for e, n in
                              ((4.0, 18.0), (16.0, 18.0), (16.0, 19.0), (4.0, 19.0))]})
    dxf = os.path.join(d, "background_L2.dxf")
    dxfread.write_background(dxf, shapes, units="in")
    files = [f["plans"], f["old"], dxf, f["rfi"]]
    reg2 = redtags.Register(path)
    names2 = Names(names_path)
    rr2, trail2, reg2, ctx2 = run(files, reg=reg2, names=names2, run_id="run2")
    second = {t.ref: (t.label, t.wrong) for t in reg2.items()}
    gone = [r for r in first if r not in second]
    assert len(gone) == 1 and "Round column" in first[gone[0]][1], gone
    for ref in second:
        if ref in first:
            assert second[ref][0] == first[ref][0], ref
    new = [r for r in second if r not in first]
    top = max(int(r.split("-")[1]) for r in first if r.startswith("L2-"))
    assert new and all(int(r.split("-")[1]) > top for r in new if r.startswith("L2-")), new
    assert any(second[r][0] == "In the CAD background only" for r in new)
    still = reg2.get(gone[0])
    assert still is not None and not still.seen, "a cleared item keeps its number on file"
    ids_second = {e.id: e.kind for lv in rr2.levels for e in lv.elements}
    assert all(ids_second.get(k) == v for k, v in ids_first.items()), "element ids are stable"
    assert len(ids_second) == len(ids_first) + 1
    print("PASS case 10 in the pipeline: a later issue adds items after the last "
          "number; earlier numbers, and every element id, are unchanged")
    # ---- an answered item: the RFI must be in the drop and the quote real ---- #
    only = by_label(reg, "L2", "In the CAD background only")[0]
    good = M.Options(answered=[{"item": only.ref, "rfi": "12",
                                "quote": "ADD AN 8 INCH CONCRETE WALL ON GRID C"}],
                     statuses={"L2-1": "Sent to the engineer on the weekly call"})
    rr3, trail3, reg3, ctx3 = run(f["files"], opts=good, reg=redtags.Register(path),
                                  names=Names(names_path), run_id="run3")
    t = reg3.get(only.ref)
    assert t.category == "answered" and t.rfi == "012" and not ctx3.answer_problems
    assert t.status_text() == "Answered per RFI 012"
    wall = next(e for e in rr3.level("L2").of(M.WALL) if e.attrs.get("cad_only"))
    assert wall.attrs["rfi"] == "012" and "per answered RFI 012" in wall.prov.note
    assert wall.sources == [M.SRC_CAD, M.SRC_RFI] and not wall.single_source
    assert reg3.action(t) == "release" and reg3.action(reg3.get("L2-1")) == "hold"
    assert reg3.get("L2-1").status_text() == "Sent to the engineer on the weekly call"
    assert reg3.get("L2-3").status_text() == "RFI needed", "the default wording; never a claim that one was sent"
    for bad in ({"item": only.ref, "rfi": "099", "quote": "ADD AN 8 INCH"},
                {"item": only.ref, "rfi": "012", "quote": "MOVE THE WALL TWO FEET"},
                {"item": "L2-999", "rfi": "012", "quote": "ADD AN 8 INCH"}):
        rr4, _t4, reg4, ctx4 = run(f["files"], opts=M.Options(answered=[bad]), run_id="run4")
        assert len(ctx4.answer_problems) == 1, bad
        assert not any(x.category == "answered" for x in reg4.items())
    draft = reg.rfi_draft(by_label(reg, "L2", "Dimension to the concrete is missing")[0])
    assert "Subject: Dimension to the concrete is missing" in draft and "A-201" in draft
    assert "Reference: S-201" in draft and draft.rstrip().endswith("This is a draft for review.")
    assert "sent" not in draft.lower() and not pt.lint_text(draft)
    print("PASS register in the pipeline: an answer needs an RFI in the drop and a "
          "quote that is really in it; statuses print only from user-set fields; "
          "neutral RFI drafts")


def main():
    f = t_checks()
    t_register(f)
    print("BATTERBOARD CHECKS TEST PASSED")


if __name__ == "__main__":
    main()
