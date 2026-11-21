"""The Batterboard run end to end: nine stages, the self-check gate, the
deliverables rule, the run report, both hats and the command line twin.

Regression cases 7, 8 and 13 at the pipeline level, and the picture
check (the check that was missing) against the original failure: a
bounding block drawn where the sheet shows a thin angled wall.

Run:  python tests/test_batterboard_run.py
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz                                                              # noqa: E402

from rfi_stamper import papertrail as pt, redtags                        # noqa: E402
from rfi_stamper.batterboard import crosscheck as xc                      # noqa: E402
from rfi_stamper.batterboard import intake, planread, plates, points     # noqa: E402
from rfi_stamper.batterboard import model as M                            # noqa: E402
from rfi_stamper.batterboard import run as bbrun                          # noqa: E402
from rfi_stamper.batterboard import sampleset as ss                       # noqa: E402
from rfi_stamper.batterboard import selfcheck                             # noqa: E402
from rfi_stamper.batterboard.names import Names                           # noqa: E402

TMP = tempfile.mkdtemp(prefix="bbrun_")
PROJECT = {"project": "SAMPLE BUILDING", "drawn_by": "LAYOUT", "date": "2026-01-15"}
QUIET = dict(log=lambda *_: None)


def tree(root):
    out = []
    for base, _dirs, files in os.walk(root):
        out += [os.path.relpath(os.path.join(base, f), root) for f in files]
    return sorted(out)


def t_full():
    f = ss.build_sample_set(os.path.join(TMP, "drop"))
    out = os.path.join(TMP, "out")
    opts = M.Options(hats=["concrete", "surveyor"], out_booklet=True,
                     working_range_ft=150.0, project=PROJECT)
    seen = []
    res = bbrun.run(f["files"], out, opts, progress=lambda n, s: seen.append((n, s)), **QUIET)
    assert res.status == bbrun.RESULT_FLAGS, (res.status, [c for c in res.checks if not c["ok"]])
    assert [n for n, _s in seen] == list(M.STAGES), seen
    st = {s.name: s.status for s in res.stages}
    assert st["Intake"] == M.PASS and st["Self-check"] == M.PASS and st["Deliver"] == M.PASS
    assert st["Register"] == M.FLAG and st["Sightline"] == M.FLAG
    assert all(c["ok"] for c in res.checks) and len(res.checks) >= 8
    names = [c["check"] for c in res.checks]
    assert "picture check L1" in names and "picture check L2" in names
    pc2 = next(c for c in res.checks if c["check"] == "picture check L2")["counts"]
    assert pc2["edges"] == pc2["edges_on_sheet"] > 0 and pc2["openings"] == 8
    assert pc2["concrete_differences"] == pc2["explained_by_open_items"] >= 1, \
        "the wall drawn from the CAD background is a difference an open item covers"
    rc = next(c for c in res.checks if c["check"].startswith("key numbers"))["counts"]
    assert rc["grid_spacings"] == 10 and rc["corner_coordinates"] > 20 and rc["zone_lengths"] >= 4
    got = tree(res.deliverables)
    for want in ("plates/Batterboard_plates.pdf", "plates/Batterboard_phone_booklet.pdf",
                 "points/L1/layout_L1.csv", "points/L2/layout_L2.csv", "points/L2/layout_L2.dxf",
                 "points/L2/layout_L2.stitch.json", "points/L2/background_L2.dxf",
                 "points/L2/layout_L2_FRAME.txt", "open_items/open_items.csv",
                 "forms/requested_control.csv", "forms/slab_elevation_survey.pdf",
                 "forms/as_staked_ledger_L2.pdf", "report/run_report.txt",
                 "report/run_report.pdf"):
        assert want in got, (want, got)
    assert sum(1 for g in got if g.startswith("open_items/rfi_drafts/")) == 6
    assert os.path.exists(os.path.join(out, "batterboard.redtags.json"))
    assert os.path.exists(os.path.join(out, "run_log", "proof_records.json"))
    assert not [d for d in os.listdir(os.path.join(out, "run_log")) if d.startswith("staging_")]
    # ---- the plates ---------------------------------------------------------- #
    doc = fitz.open(os.path.join(res.deliverables, "plates", "Batterboard_plates.pdf"))
    titles = [p["title"] for p in res.plate_manifest["pages"]]
    for want in ("LEVEL 2 WALL PLAN", "LEVEL 2 OPEN ITEMS", "LEVEL 2 SLAB EDGES",
                 "LEVEL 2 SHEAR WALL ENDS", "LEVEL 2 ARCHITECT DIMENSIONS TO CHECK",
                 "LEVEL 2 MASONRY", "LEVEL 2 CONTROL REQUEST", "LEVEL 2 SETUP PLAN",
                 "LEVEL 2 POINT LIST", "LEGEND", "INDEX"):
        assert want in titles, (want, titles)
    assert "LEVEL 1 MASONRY" not in titles, "masonry only where the CAD background has it"
    assert doc.page_count == len(titles)
    assert all("FOR REVIEW" in doc[i].get_text() for i in range(doc.page_count))
    assert doc.metadata.get("producer") in (None, "") and doc.metadata.get("creator") in (None, "")
    wall = next(p for p in res.plate_manifest["pages"] if p["title"] == "LEVEL 2 WALL PLAN")
    assert any(t["kind"] == "statement" and "Not real-world coordinates" in t["s"]
               for t in wall["texts"]), "every plate states its frame"
    assert any(t["kind"] == "statement" and "match the CAD background" in t["s"]
               and "Not compared:" in t["s"] for t in wall["texts"])
    # every list row is also on the plan, or counted as list only
    assert wall["notes"]["asked"] == 14 and \
        wall["notes"]["placed"] + wall["notes"]["list_only"] == 14
    plist = next(p for p in res.plate_manifest["pages"] if p["title"] == "LEVEL 2 POINT LIST")
    tol = [t for t in plist["texts"] if t.get("src", "").startswith("tolerance:")]
    assert tol and "Basis:" in tol[0]["s"] and "verify against project spec" in tol[0]["s"], \
        "tolerances print from the job's own classes, with their basis and the disclaimer"
    assert "GRIDLINE" in tol[0]["s"] and "1/8 in." in tol[0]["s"]
    doc.close()
    # ---- regression case 7: the legend is pulled from the produced data ----------- #
    leg = next(p for p in res.plate_manifest["pages"] if p["number"] == "BB-002")
    csv_rows = set()
    for code in ("L1", "L2"):
        for line in open(os.path.join(res.deliverables, "points", code, f"layout_{code}.csv"),
                         encoding="utf-8").read().splitlines()[1:]:
            parts = line.split(",")
            csv_rows.add((parts[0], ",".join(parts[4:])))
    examples = [t["s"] for t in leg["texts"] if t["where"] == "table" and t["kind"] == "data"
                and t.get("src", "").startswith("point:")]
    descs = [s for s in examples if " " in s]
    assert len(descs) >= 9, descs
    for d in descs:
        assert any(d == row[1] for row in csv_rows), ("not verbatim in the point file", d)
    assert selfcheck.check_legend(res.plate_manifest, res.points, res.reg, res.rr)["ok"]
    forged = json.loads(json.dumps(res.plate_manifest))
    fleg = next(p for p in forged["pages"] if p["number"] == "BB-002")
    victim = next(t for t in fleg["texts"] if t["s"] in descs)
    victim["s"] = "POST WC L2-W-999 HOLD ITEM L2-77"
    bad = selfcheck.check_legend(forged, res.points, res.reg, res.rr)
    assert not bad["ok"] and "character for character" in bad["detail"]
    count = next(t for t in fleg["texts"] if t.get("src") == "points:count")
    count["s"] = "999"
    assert any("the data holds" in p for p in
               selfcheck.check_legend(forged, res.points, res.reg, res.rr)["problems"])
    print("PASS full run: PASS WITH FLAGS, every self-check held, deliverables in place; "
          "case 7: every legend example is verbatim in the point file")
    return f, res


def t_report(res):
    text = open(os.path.join(res.deliverables, "report", "run_report.txt"), encoding="utf-8").read()
    for head in ("WHAT WAS CHECKED AGAINST WHAT", "WHAT WAS FOUND", "WHAT WAS NOT CHECKED AND WHY",
                 "SINGLE SOURCE", "HIDDEN GEOMETRY (THE SIGHTLINE)", "NOTES THAT GOVERN LAYOUT",
                 "POINTS", "SELF-CHECK"):
        assert "== " + head in text, head
    assert "Result: PASS WITH FLAGS" in text
    assert "verified" not in text.lower(), "never the bare word; the second source is named instead"
    assert "match within 1/4 in." in text and "the CAD background background_L2.dxf" in text
    assert "Not laid out in this run: elevations, slab steps and depressions, curbs and pads, " \
           "embeds and hold-downs." in text
    assert "is drawn from the CAD background, not from S-201" in text
    assert "6 dropped as not on the plotted sheet" in text and "hidden: under 50%" in text
    assert "a CAD background for this level is not in the drop" in text
    assert '"TOLERANCES FOR CONCRETE CONSTRUCTION SHALL CONFORM TO THE PROJECT SPECIFICATIONS." ' \
           "(S-001, CONCRETE, note 2)" in text
    assert "Working range (a user setting)" in " ".join(
        t["s"] for p in res.plate_manifest["pages"] for t in p["texts"])
    assert "instrument range" not in text.lower()
    assert "flagged" in text and "no valid setup" in text
    assert not pt.lint_text(text, quotes=[q.text for p in res.trail.proofs() for q in p.quotes]
                            + [t.wrong for t in res.reg.items()])
    pdf = fitz.open(os.path.join(res.deliverables, "report", "run_report.pdf"))
    assert pdf.page_count >= 2 and "BATTERBOARD RUN REPORT" in pdf[0].get_text()
    assert pdf.metadata.get("producer") in (None, "")
    pdf.close()
    survey = fitz.open(os.path.join(res.deliverables, "forms", "slab_elevation_survey.pdf"))
    assert "PROVIDE A SLAB ELEVATION SURVEY AFTER EACH ELEVATED SLAB POUR." in \
        " ".join(survey[0].get_text().split())
    survey.close()
    ctl = open(os.path.join(res.deliverables, "forms", "requested_control.csv")).read()
    assert ctl.count("REQUESTED CONTROL") == 10 and "RC-L2-01" in ctl
    print("PASS run report: what was checked against what, found, lines up, not checked "
          "and why, single source, hidden geometry; neutral surveyor wording")


def build_stage(files, opts=None):
    opts = opts or M.Options(project=PROJECT)
    names = Names()
    rr = planread.read_drop(intake.classify_drop(files, opts), opts, names)
    trail, reg = pt.Trail(), redtags.Register()
    reg.begin_run("r")
    ctx = xc.run_checks(rr, trail, reg, opts, names)
    reg.end_run()
    return rr, trail, reg, ctx, points.build_points(rr, reg, opts, names), opts


def t_picture(f):
    """The original failure: a solid block drawn where the sheet shows a
    thin angled wall.  The picture check must refuse it."""
    rr, trail, reg, ctx, allp, opts = build_stage([f["plans"]])
    l2 = rr.level("L2")
    good = os.path.join(TMP, "pic_good.pdf")
    man = plates.build(good, rr, reg, trail, ctx, allp, opts)
    idx = next(k for k, p in enumerate(man["pages"]) if p["title"] == "LEVEL 2 WALL PLAN")
    ok = selfcheck.picture_check(l2, good, idx, man["pages"][idx], reg)
    assert ok["ok"], ok
    w4 = next(e for e in l2.of(M.WALL) if "clipped" in e.attrs)
    xs = [p[0] for p in w4.poly]
    ys = [p[1] for p in w4.poly]
    keep = list(w4.poly)
    w4.poly = [(min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys))]
    w4.items = []
    for t in reg.items("L2"):
        if w4.id in t.elements:
            t.boxes = []                     # no open item covers this place
    bad = os.path.join(TMP, "pic_bad.pdf")
    man2 = plates.build(bad, rr, reg, trail, ctx, allp, opts)
    res = selfcheck.picture_check(l2, bad, idx, man2["pages"][idx], reg)
    assert not res["ok"] and "draws concrete that is not on S-201" in res["detail"], res
    w4.poly = keep
    # the mirror failure: concrete on the sheet that the plate does not draw
    l2.elements = [e for e in l2.elements if not (e.kind == M.WALL and e.mark == "SW3")]
    for t in reg.items("L2"):
        t.boxes = [b for b in t.boxes if "W-003" not in " ".join(t.elements)]
    miss = os.path.join(TMP, "pic_miss.pdf")
    man3 = plates.build(miss, rr, reg, trail, ctx, allp, opts)
    res = selfcheck.picture_check(l2, miss, idx, man3["pages"][idx], reg)
    assert not res["ok"] and "is not on the plate" in res["detail"], res
    # a slab edge drawn where the sheet has no heavy line
    rr, trail, reg, ctx, allp, opts = build_stage([f["plans"]])
    l1 = rr.level("L1")
    edge = l1.of(M.SLAB_EDGE)[0]
    for s in edge.attrs["segments"]:
        if s["kind"] == "unconfirmed":
            s["kind"] = "line"               # pretend the reader trusted the light line
    for t in reg.items("L1"):
        t.boxes = []
    p1 = os.path.join(TMP, "pic_edge.pdf")
    man4 = plates.build(p1, rr, reg, trail, ctx, allp, opts)
    i1 = next(k for k, p in enumerate(man4["pages"]) if p["title"] == "LEVEL 1 WALL PLAN")
    res = selfcheck.picture_check(l1, p1, i1, man4["pages"][i1], reg)
    assert not res["ok"] and "slab edge line" in res["detail"], res
    print("PASS picture check: a bounding block where the sheet shows a thin angled wall, "
          "concrete the plate omits, and an edge with no heavy line are each refused")


def t_gates(f, res0):
    # ---- regression case 8: a dead reference blocks delivery ---------------------- #
    d = os.path.join(TMP, "dead")
    os.makedirs(d, exist_ok=True)
    notes = ss.sheet_notes()
    notes.texts = [(x, y, s.replace("5/S-501", "9/S-900"), size, c, r, b)
                   for x, y, s, size, c, r, b in notes.texts]
    doc = fitz.open()
    notes.add_to(doc)
    ss.sheet_struct(2).add_to(doc)
    ss.sheet_schedule().add_to(doc)
    plan = os.path.join(d, "set.pdf")
    ss._save(doc, plan)
    out = os.path.join(TMP, "out_dead")
    res = bbrun.run([plan], out, M.Options(project=PROJECT), **QUIET)
    assert res.status == bbrun.RESULT_BLOCKED and res.deliverables is None
    assert not os.path.exists(os.path.join(out, "deliverables")), "a blocked run writes nothing there"
    assert res.stage("Self-check").status == M.BLOCK
    why = " ".join(res.stage("Self-check").lines)
    assert "S-900" in why and "not in this run" in why, why
    rep = open(os.path.join(out, "run_log", "run_report.txt"), encoding="utf-8").read()
    assert "Result: BLOCKED" in rep and "S-900" in rep
    # the user can leave that note out, and the run delivers
    res = bbrun.run([plan], out, M.Options(project=PROJECT, skip_notes=["S-001/OPENINGS/1"]),
                    **QUIET)
    assert not res.blocked and os.path.isdir(res.deliverables)
    inv = selfcheck.inventory(res0.rr, res0.reg, ["BB-201"])
    dead = selfcheck.check_references(pt.Trail(), [{"pages": [{"texts": [
        {"s": "SEE S-900 FOR THE WALL", "kind": "data"},
        {"s": "ITEM L2-99 AND RFI 044 AND DETAIL 3/S-201", "kind": "data"},
        {"s": "PLATE BB-201, ITEM L2-1, RFI 012, S-201", "kind": "data"}]}]}], inv)
    assert not dead["ok"] and len(dead["problems"]) == 3, dead["problems"]
    print("PASS case 8: a plate that refers to a sheet, item or RFI that is not in the run "
          "blocks delivery; nothing lands in the deliverables folder")

    # ---- regression case 13: lint on outputs --------------------------------------- #
    d = os.path.join(TMP, "lint")
    os.makedirs(d, exist_ok=True)

    def pdf(name, text, meta=None):
        doc = fitz.open()
        pg = doc.new_page()
        pg.insert_text((72, 72), text)
        doc.set_metadata(meta or {})
        p = os.path.join(d, name)
        doc.save(p)
        doc.close()
        return p

    def mini(name, text):
        # the repo's own writer carries the long dash (WinAnsi 0x97) and no metadata
        from rfi_stamper.minipdf import canvas as mcanvas
        p = os.path.join(d, name)
        c = mcanvas.Canvas(p, pagesize=(612, 792))
        c.setFont("Helvetica", 11)
        c.drawString(72, 700, text)
        c.showPage()
        c.save()
        return p

    clean = pdf("clean.pdf", "LEVEL 2 WALL PLAN")
    assert selfcheck.check_lint([mini("clean2.pdf", "LEVEL 2 WALL PLAN")], PROJECT, [])["ok"]
    assert selfcheck.check_lint([clean], PROJECT, [])["ok"]
    cases = {"metadata": pdf("meta.pdf", "LEVEL 2 WALL PLAN", {"producer": "PyMuPDF 1.28"}),
             "long_dash": mini("dash.pdf", "WALL PLAN — LEVEL 2"),
             "british": pdf("brit.pdf", "COLOUR OF THE CENTRE LINE"),
             "howto": pdf("howto.pdf", "STEP 1: CLICK RUN")}
    for kind, path in cases.items():
        got = selfcheck.check_lint([path], PROJECT, [])
        assert not got["ok"], kind
    assert selfcheck.check_lint([pdf("name.pdf", "PLAN", {"author": "SAMPLE BUILDING"})],
                                PROJECT, [])["ok"], "a project settings value is allowed"
    txt = os.path.join(d, "note.txt")
    open(txt, "w", encoding="utf-8").write("Step 2: click the button – then how to use it.\n")
    assert not selfcheck.check_lint([txt], PROJECT, [])["ok"]
    for fpath in res0.files:
        if pt.can_lint(fpath) and "as_staked_ledger" not in fpath:
            hits = pt.lint_file(fpath, PROJECT, quotes=[q.text for p in res0.trail.proofs()
                                                        for q in p.quotes]
                                + [t["s"] for p in res0.plate_manifest["pages"]
                                   for t in p["texts"] if t["kind"] == "quote"])
            assert not hits, (os.path.basename(fpath), hits[:3])
    print("PASS case 13: a tool name in metadata, a long dash, British spelling or "
          "how-to wording each fail the lint; every delivered output passes it")

    # ---- a failed self-check delivers nothing, and keeps the last good set ------------ #
    out = os.path.join(TMP, "out")
    before = tree(os.path.join(out, "deliverables"))
    real = selfcheck.run_all
    selfcheck.run_all = lambda *a, **k: real(*a, **k) + [
        {"check": "forced failure", "ok": False, "problems": ["forced"], "detail": "forced",
         "counts": {}}]
    try:
        res = bbrun.run(f["files"], out, M.Options(project=PROJECT), **QUIET)
    finally:
        selfcheck.run_all = real
    assert res.status == bbrun.RESULT_BLOCKED and res.deliverables is None and not res.files
    assert tree(os.path.join(out, "deliverables")) == before, "the earlier deliverables are untouched"
    assert "Result: BLOCKED" in open(os.path.join(out, "run_log", "run_report.txt")).read()
    # nothing to lay out
    res = bbrun.run([f["rfi"]], os.path.join(TMP, "out_none"), M.Options(project=PROJECT), **QUIET)
    assert res.blocked and res.stage("Intake").status == M.BLOCK
    assert "nothing to lay out" in " ".join(res.stage("Intake").lines)
    print("PASS gates: a failed self-check or an empty drop is BLOCKED and writes nothing "
          "to the deliverables folder")


def t_switches(f):
    # concrete hat only, no kits, issued: no forms, no points folder, no stamp
    out = os.path.join(TMP, "out_concrete")
    opts = M.Options(hats=["concrete"], out_kits=False, out_rfi_drafts=False, issue=True,
                     project=PROJECT)
    res = bbrun.run(f["files"], out, opts, **QUIET)
    got = tree(res.deliverables)
    assert not any(g.startswith(("forms/", "points/", "open_items/rfi_drafts/")) for g in got), got
    doc = fitz.open(os.path.join(res.deliverables, "plates", "Batterboard_plates.pdf"))
    assert "FOR REVIEW" not in doc[0].get_text() and "ISSUED" in doc[0].get_text()
    doc.close()
    titles = [p["title"] for p in res.plate_manifest["pages"]]
    assert not any("SETUP PLAN" in t or "CONTROL REQUEST" in t for t in titles)
    # the one-sheet proof for sign-off
    out = os.path.join(TMP, "out_proof")
    res = bbrun.run(f["files"], out, M.Options(proof_only=True, project=PROJECT), **QUIET)
    assert not res.blocked
    proof = os.path.join(res.deliverables, "plates", "Batterboard_one_sheet_proof.pdf")
    doc = fitz.open(proof)
    assert doc.page_count == 1 and "WALL PLAN" in doc[0].get_text()
    doc.close()
    assert "plates/Batterboard_plates.pdf" not in tree(res.deliverables)
    # levels switch; a survey form only when a note calls for one
    out = os.path.join(TMP, "out_l2")
    res = bbrun.run(f["files"], out, M.Options(levels=["L2"], project=PROJECT), **QUIET)
    assert [lv.code for lv in res.rr.levels] == ["L2"]
    assert not os.path.isdir(os.path.join(res.deliverables, "points", "L1"))
    d = os.path.join(TMP, "nonotes")
    os.makedirs(d, exist_ok=True)
    one = ss.sheet_struct(2).save(os.path.join(d, "s201.pdf"))
    res = bbrun.run([one], os.path.join(TMP, "out_nonotes"),
                    M.Options(hats=["concrete", "surveyor"], project=PROJECT), **QUIET)
    assert not res.blocked
    assert "forms/slab_elevation_survey.pdf" not in tree(res.deliverables)
    assert any("was not made" in ln for ln in res.surveyor)
    assert any("working range was not set" in t["s"].lower()
               for p in res.plate_manifest["pages"] for t in p["texts"]), \
        "an unset working range is said, never assumed"
    print("PASS switches: hats, kits, drafts, issue, levels, the one-sheet proof; a survey "
          "form only when a general note calls for one")


def t_cli(f):
    out = os.path.join(TMP, "out_cli")
    opt = os.path.join(TMP, "run.json")
    json.dump({"hats": ["concrete"], "project": PROJECT, "files": f["files"], "out": out},
              open(opt, "w"))
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-m", "rfi_stamper", "batterboard", "--options", opt,
                        "--quiet"], cwd=root, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0 and "RESULT: PASS WITH FLAGS" in r.stdout, (r.returncode, r.stdout, r.stderr[-400:])
    assert os.path.exists(os.path.join(out, "deliverables", "plates", "Batterboard_plates.pdf"))
    json.dump({"no_such_switch": True, "files": f["files"], "out": out + "_bad"}, open(opt, "w"))
    r = subprocess.run([sys.executable, "-m", "rfi_stamper", "batterboard", "--options", opt,
                        "--quiet"], cwd=root, capture_output=True, text=True, timeout=600)
    assert r.returncode == 1 and "RESULT: BLOCKED" in r.stdout, (r.returncode, r.stdout)
    r = subprocess.run([sys.executable, "-m", "rfi_stamper", "batterboard"], cwd=root,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 2
    print("PASS command line twin: a JSON options file runs the package; the exit code is "
          "nonzero when the run is blocked")


def main():
    f, res = t_full()
    t_report(res)
    t_picture(f)
    t_gates(f, res)
    t_switches(f)
    t_cli(f)
    print("BATTERBOARD RUN TEST PASSED")
    print("outputs in", TMP)


if __name__ == "__main__":
    main()
