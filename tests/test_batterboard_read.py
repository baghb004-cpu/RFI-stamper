"""The Batterboard plan reader on the synthetic sample building.

Intake (classify, never guess), scale + grid + frame, concrete from the
learned fill class, slab edge and openings, shear wall ends, architect
strings, overlays, masonry, provenance, stable ids, and the gates that
BLOCK a level instead of guessing.  Carries the reader-level halves of
regression cases 1, 2, 3, 12 and 15.

Run:  python tests/test_batterboard_read.py
"""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper import dxfread, geom2d as g2                       # noqa: E402
from rfi_stamper.batterboard import intake, planread, slabread      # noqa: E402
from rfi_stamper.batterboard import model as M                      # noqa: E402
from rfi_stamper.batterboard import sampleset as ss                 # noqa: E402
from rfi_stamper.batterboard.names import Names                     # noqa: E402

TMP = tempfile.mkdtemp(prefix="bbread_")
OX, OY = ss.CAD_ORIGIN_FT


def near(a, b, tol=0.01):
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= tol


def same_poly(got, want, tol=0.01):
    return (len(got) == len(want) and all(any(near(g, w, tol) for w in want) for g in got))


def cad(pts):
    return [(OX + e, OY + n) for e, n in pts]


def read(sub, opts=None, names=None, **kw):
    d = os.path.join(TMP, sub)
    f = ss.build_sample_set(d, **kw)
    refs = intake.classify_drop(f["files"], opts)
    return f, refs, planread.read_drop(refs, opts, names)


def t_intake():
    d = os.path.join(TMP, "intake")
    f = ss.build_sample_set(d)
    other = os.path.join(d, "background.dwg")
    with open(other, "wb") as fh:
        fh.write(b"AC1032" + b"\0" * 64)
    refs = intake.classify_drop(f["files"] + [other])
    got = {(r.sheet or r.name, r.prior): (r.cls, r.level, r.revision) for r in refs}
    assert got[("S-001", False)] == (M.PG_NOTES, "", "2")
    assert got[("S-101", False)] == (M.PG_STRUCT, "LEVEL 1", "2")
    assert got[("S-201", False)] == (M.PG_STRUCT, "LEVEL 2", "2")
    assert got[("S-201", True)] == (M.PG_STRUCT, "LEVEL 2", "1"), "the earlier issue is ordered by its revision text"
    assert got[("S-501", False)][0] == M.PG_SCHEDULE
    assert got[("A-201", False)] == (M.PG_ARCH, "LEVEL 2", "3")
    assert got[("M-201", False)][:2] == (M.PG_SHOP, "LEVEL 2")
    assert got[("background_L2.dxf", False)][:2] == (M.PG_CAD, "LEVEL 2")
    rfi = next(r for r in refs if r.cls == M.PG_RFI)
    assert rfi.rfi == ss.RFI_NUMBER and rfi.answer.startswith("ADD AN 8 INCH")
    unk = next(r for r in refs if r.name == "background.dwg")
    assert unk.cls == M.PG_UNKNOWN and unk.why == dxfread.NOT_ASCII_DXF
    # a title no rule matches is listed unknown, with the reason; the user can assign it
    odd = ss.Sheet(ss.SHEET_W, ss.SHEET_H)
    ss._furniture(odd, "X-900", "MISCELLANEOUS")
    p = odd.save(os.path.join(d, "odd.pdf"))
    r = intake.classify_drop([p])[0]
    assert r.cls == M.PG_UNKNOWN and "MISCELLANEOUS" in r.why
    r = intake.classify_drop([p], M.Options(class_of={"X-900": M.PG_NOTES}))[0]
    assert r.cls == M.PG_NOTES and r.why == "set by the user"
    for text, want in (("LEVEL 2 SLAB PLAN", "L2"), ("3RD FLOOR FRAMING PLAN", "L3"),
                       ("SECOND FLOOR PLAN", "L2"), ("ROOF FRAMING PLAN", "RF"),
                       ("FOUNDATION PLAN", "FND"), ("LEVELS 2 AND LEVEL 3 PLAN", "L3")):
        lv = intake.level_from_text(text)
        assert lv and lv[1] == want, (text, lv)
    assert intake.level_from_text("LEVEL 2 AND LEVEL 3 PLAN") is None, "two levels named: none"
    assert intake.level_from_text("TYPICAL DETAILS") is None
    print("PASS intake: every page classified by its title block, the earlier issue "
          "ordered, a non-DXF CAD file gets the plain message, unknown is listed")


def t_level2():
    f, refs, rr = read("full")
    assert rr.frame_kind == "cad" and "background_L2.dxf" in rr.frame_label
    assert "inches" in rr.frame_label and "Not real-world" in rr.frame_label
    l2 = rr.level("L2")
    assert not l2.blocked, l2.blocked
    assert abs(l2.pt_per_ft - 9.0) < 1e-6
    assert [(g.name, round(g.offset_ft, 4)) for g in l2.grid.family("x")] == \
        [(n, e) for n, e in ss.GRID_X]
    assert [(g.name, round(g.offset_ft, 4)) for g in l2.grid.family("y")] == \
        [(n, v) for n, v in ss.GRID_Y]
    assert len(l2.grid.spacing_checks) == 5
    assert all(abs(c["delta_ft"]) < 1e-6 for c in l2.grid.spacing_checks)
    a1 = next(p for p in l2.grid.intersections() if p[0] == "A/1")
    assert near(a1[1:], (OX, OY), 1e-6), "the CAD file's own origin is kept"
    counts = {k: len(l2.of(k)) for k in planread.KIND_ORDER if l2.of(k)}
    assert counts == {"slab_edge": 1, "wall": 6, "column": 3, "round_column": 3,
                      "concrete": 1, "wall_opening": 1, "slab_opening": 2, "sw_end": 8,
                      "penetration": 1, "overlay_opening": 3, "masonry": 2}, counts
    for w in ss.WALLS_L2:
        e = next(v for v in l2.of(M.WALL) if same_poly(v.poly, cad(w["poly"]), 0.01))
        assert e.mark == w["mark"], (w["name"], e.mark)
        assert e.prov.sheet == "S-201" and e.prov.revision == "2" and e.prov.page == 3
        assert e.sources == [M.SRC_STRUCT] and e.single_source
    # case 1 at the reader: the clipped fill comes back as the thin angled wall
    w4 = next(e for e in l2.of(M.WALL) if "clipped" in e.attrs)
    band = cad(ss.WALLS_L2[4]["poly"])
    assert same_poly(w4.poly, band, 0.005) and len(w4.poly) == 4
    assert abs(w4.attrs["thickness_ft"] - 0.75) < 0.002 and w4.prov.method == M.VECTOR
    assert w4.prov.confidence < 1.0
    bx0, by0, bx1, by1 = g2.bbox(w4.poly)
    assert abs(g2.area(w4.poly)) < 0.15 * (bx1 - bx0) * (by1 - by0), "never its bounding block"
    # thickness as drawn
    by_mark = {}
    for e in l2.of(M.WALL):
        by_mark.setdefault(e.mark, []).append(e)
    assert all(abs(e.attrs["thickness_ft"] - 1.0) < 1e-3 for e in by_mark["SW1"] + by_mark["SW2"])
    assert abs(by_mark["SW3"][0].attrs["thickness_ft"] - 8.0 / 12.0) < 1e-3
    # columns, round columns, other concrete
    assert sorted(tuple(round(v, 2) for v in e.centroid()) for e in l2.of(M.COLUMN)) == \
        sorted((OX + e, OY + n) for _n, (e, n) in ss.COLUMNS)
    for e in l2.of(M.ROUND_COLUMN):
        assert abs(e.radius_ft - 1.0) < 0.002
        assert any(near(e.center, (OX + c[0], OY + c[1]), 0.002) for _n, c in ss.ROUND_COLUMNS)
    pad = l2.of(M.CONCRETE)[0]
    assert same_poly(pad.poly, cad(ss.PAD), 0.01)
    # case 12 at the reader: the cloud across the column at A/3 changes nothing
    col = next(e for e in l2.of(M.COLUMN) if near(e.centroid(), (OX, OY + 40.0), 0.02))
    assert col.attrs.get("obscured") and len(col.poly) == 4 and col.prov.method == M.VECTOR
    assert not l2.unread, l2.unread
    # the wall opening and the shear wall ends
    op = l2.of(M.WALL_OPENING)[0]
    assert abs(op.attrs["width_ft"] - 3.0) < 1e-3
    assert near(op.attrs["center"], (OX + 35.5, OY + 20.0), 0.01)
    ends = l2.of(M.SW_END)
    roles = sorted((e.mark, e.attrs["role"]) for e in ends)
    assert roles == [("SW1", "end")] * 2 + [("SW2", "end")] * 2 + [("SW2", "jamb")] * 2 \
        + [("SW3", "end")] * 2, roles
    assert all(e.attrs["host"].startswith("L2-W-") for e in ends)
    assert sorted(e.attrs["opening"] for e in ends if e.attrs["role"] == "jamb") == [op.id] * 2
    # slab: outline with the radius corner, openings with solid and dashed crosses
    edge = l2.of(M.SLAB_EDGE)[0]
    assert edge.closed and edge.attrs["unconfirmed"] == 0 and not edge.attrs["gaps"]
    kinds = [s["kind"] for s in edge.attrs["segments"]]
    assert sorted(kinds) == ["arc", "line", "line", "line", "line"], kinds
    arc = next(s for s in edge.attrs["segments"] if s["kind"] == "arc")
    assert abs(arc["radius_ft"] - 6.0) < 1e-3 and near(arc["center"], (OX + 67.0, OY + 35.0), 1e-3)
    pts = slabread.edge_points(edge)
    assert len(pts["corners"]) == 5 and len(pts["radius_points"]) == 1
    assert sum(1 for c in pts["corners"] if c[2] == "tangent point") == 2
    so = {e.attrs["cross"]: e for e in l2.of(M.SLAB_OPENING)}
    assert same_poly(so["solid"].poly, cad(ss._box_poly(ss.SLAB_OPENINGS_L2[0]["box"])))
    assert same_poly(so["dashed"].poly, cad(ss._box_poly(ss.SLAB_OPENINGS_L2[1]["box"])))
    # case 2 at the reader: the superseded opening is not read; the place is recorded
    assert not any(near(e.centroid(), (OX + 23.0, OY + 30.0), 3.0) for e in l2.of(M.SLAB_OPENING))
    assert len(l2.hidden) == 1 and l2.hidden[0]["strokes"] == 6
    hb = l2.hidden[0]["world_bbox"]
    b = ss.SO1_OLD_BOX
    assert near((hb[0], hb[1]), (OX + b[0], OY + b[1]), 0.05) and \
        near((hb[2], hb[3]), (OX + b[2], OY + b[3]), 0.05)
    rec = next(s for s in rr.sight if s["sheet"] == "S-201")["record"]
    assert rec.counts()["dropped"] == 6 and rec.counts()["clipped"] == 1 \
        and rec.counts()["obscured"] == 1
    # penetration, overlays (approximate, labeled), masonry (CAD only)
    pen = l2.of(M.PENETRATION)[0]
    assert near(pen.center, (OX + 40.0, OY + 10.0), 0.01) and "CORE" in pen.attrs["text"]
    ovl = l2.of(M.OVERLAY_OPENING)
    assert all(e.attrs["approximate"] and e.prov.confidence == 0.5
               and "approximate" in e.prov.note and e.prov.sheet == "M-201"
               and e.sources == [M.SRC_OVERLAY] for e in ovl)
    assert sorted(tuple(round(v, 1) for v in e.centroid()) for e in ovl) == sorted(
        (OX + (o["box"][0] + o["box"][2]) / 2, OY + (o["box"][1] + o["box"][3]) / 2)
        for o in ss.OVERLAY_OPENINGS)
    mas = l2.of(M.MASONRY)
    assert all(e.prov.method == M.CAD and e.sources == [M.SRC_CAD] for e in mas)
    assert same_poly(mas[0].poly, cad(ss.MASONRY[0])) or same_poly(mas[0].poly, cad(ss.MASONRY[1]))
    # architect strings: by their two ends
    cls = sorted(r["cls"] for r in l2.arch_strings)
    assert cls == ["different"] + ["off"] + ["one"] * 2 + ["same"] * 7, cls
    diff = next(r for r in l2.arch_strings if r["cls"] == "different")
    assert diff["text"].startswith("16'-2") and abs(diff["delta_ft"] - 2.0 / 12.0) < 1e-3
    assert sum(1 for r in l2.arch_strings if r["centerline"] is not None) == 1
    assert l2.arch_off == 1 and not l2.arch_note
    # the text sheets and the RFI ride along
    assert len(rr.notes) == 7 and rr.marks() == ["SW1", "SW2", "SW3"]
    assert rr.schedules[0].disagreements() == [("SW2", 2.5, 3.0)]
    assert [r.rfi for r in rr.rfis] == [ss.RFI_NUMBER]
    assert l2.prior is not None and l2.prior.revision == "1"
    print("PASS level 2: scale, grid, CAD frame, concrete as polygons (cases 1, 12), "
          "slab + curve + openings (case 2), shear wall ends, architect strings, "
          "overlays, masonry, provenance")
    return rr


def t_level1(rr):
    l1 = rr.level("L1")
    assert not l1.blocked and l1.frame.kind == "cad", "a level with no CAD file of its own joins the frame by grid names"
    edge = l1.of(M.SLAB_EDGE)[0]
    # case 3 at the reader: the run with only a light line is NOT a confident edge
    assert edge.attrs["unconfirmed"] == 1
    run = next(s for s in edge.attrs["segments"] if s["kind"] == "unconfirmed")
    e0, e1 = ss.L1_LIGHT_RUN
    assert {tuple(round(v, 2) for v in run["a"]), tuple(round(v, 2) for v in run["b"])} == \
        {(OX + e0, OY - 1.0), (OX + e1, OY - 1.0)}
    notes = [c[2] for c in slabread.edge_points(edge)["corners"]]
    assert notes.count("end of an edge run with no heavy plotted line") == 2
    assert len(l1.of(M.WALL)) == 3 and not l1.of(M.SLAB_OPENING)
    print("PASS level 1 (case 3): the edge run whose heavy line is gone stays "
          "unconfirmed; its ends say so")


def t_frames_and_ids():
    names = Names(os.path.join(TMP, "names.json"))
    _f, _refs, a = read("nocad", names=names, cad=False)
    assert a.frame_kind == "grid" and "origin at grid A/1" in a.frame_label
    l2 = a.level("L2")
    w1 = next(e for e in l2.of(M.WALL) if e.mark == "SW1")
    assert same_poly(w1.poly, ss.WALLS_L2[0]["poly"], 0.01), "local frame: A/1 is the origin"
    assert not l2.of(M.MASONRY)
    ids_a = {e.id: tuple(round(v, 2) for v in e.centroid()) for e in l2.elements}
    names.save()
    # the same drop again, a fresh process: every id is the same thing
    names2 = Names(os.path.join(TMP, "names.json"))
    _f, _refs, b = read("nocad2", names=names2, cad=False)
    ids_b = {e.id: tuple(round(v, 2) for v in e.centroid()) for e in b.level("L2").elements}
    assert ids_a == ids_b
    # user control with coordinates: real-world frame, declared
    opts = M.Options(control=[{"at": "A/1", "n": 5000.0, "e": 2000.0},
                              {"at": "D/3", "n": 5040.0, "e": 2072.0}])
    _f, _refs, c = read("control", opts=opts, cad=False)
    assert c.frame_kind == "control" and c.frame_label.startswith("Real-world coordinates")
    w1c = next(e for e in c.level("L2").of(M.WALL) if e.mark == "SW1")
    assert near(w1c.centroid(), (2024.0, 5010.0), 0.01)
    # control that disagrees with the sheet scale blocks the level
    bad = M.Options(control=[{"at": "A/1", "n": 5000.0, "e": 2000.0},
                             {"at": "D/3", "n": 5040.0, "e": 2073.0}])
    _f, _refs, c2 = read("control_bad", opts=bad, cad=False)
    assert c2.level("L2").blocked and "control" in c2.level("L2").blocked[0]
    print("PASS frames: CAD origin when a background exists, else the local grid "
          "frame, real-world only from user control; ids are stable across runs")


def t_blocks():
    d = os.path.join(TMP, "blocks")
    os.makedirs(d, exist_ok=True)
    # two plausible fill classes: the reader does not pick
    s = ss.sheet_struct(2)
    pl = ss.Plan()
    for k in range(5):
        s.fill_poly(pl.poly([(4 + 12 * k, 22.0), (12 + 12 * k, 22.0), (12 + 12 * k, 23.0),
                             (4 + 12 * k, 23.0)]), 0.55)
    p = s.save(os.path.join(d, "two_fills.pdf"))
    refs = intake.classify_drop([p])
    rr = planread.read_drop(refs)
    lv = rr.levels[0]
    assert lv.blocked and "fill colors" in lv.blocked[0], lv.blocked
    assert len(rr.asks) == 1 and rr.asks[0]["kind"] == "fill_class"
    cands = rr.asks[0]["candidates"]
    assert len(cands) == 2 and all(c["crops"] and c["crops"][0][:4] == b"\x89PNG" for c in cands)
    assert not lv.elements, "nothing is read until the user answers"
    rr = planread.read_drop(refs, M.Options(fill_class={"S-201": [191, 191, 191]}))
    assert not rr.levels[0].blocked and len(rr.levels[0].of(M.WALL)) == 6
    # no verified scale: blocked; the user's manual scale unblocks it
    s = ss.sheet_struct(2)
    s.texts = [t for t in s.texts if t[2] != ss.SCALE_NOTE]
    p = s.save(os.path.join(d, "no_note.pdf"))
    refs = intake.classify_drop([p])
    rr = planread.read_drop(refs)
    assert rr.levels[0].blocked and "scale not verified" in rr.levels[0].blocked[0]
    rr = planread.read_drop(refs, M.Options(manual_scale={"S-201": 9.0}))
    assert not rr.levels[0].blocked and "set by the user" in " ".join(rr.levels[0].notes)
    # a printed grid dimension 6 in. off the drawn spacing: blocked, with both numbers
    s = ss.sheet_struct(2)
    hit = [i for i, t in enumerate(s.texts) if t[2] == "24'-0\""][0]
    s.texts[hit] = s.texts[hit][:2] + ("24'-6\"",) + s.texts[hit][3:]
    p = s.save(os.path.join(d, "grid_dim.pdf"))
    rr = planread.read_drop(intake.classify_drop([p]))
    msg = rr.levels[0].blocked[0]
    assert "24'-0\"" in msg and "24'-6\"" in msg and "1/4 in." in msg, msg
    # the CAD grid 1 in. off the sheet grid: blocked
    shapes = ss.cad_shapes(2)
    for sh in shapes:
        if sh["layer"] == "S-GRID" and sh["kind"] == "line" and \
                abs(sh["pts"][0][0] - (OX + 48.0) * 12.0) < 1e-6:
            sh["pts"] = [(x + 1.0, y) for x, y in sh["pts"]]
    plans = ss.sheet_struct(2).save(os.path.join(d, "s201.pdf"))
    dxf = os.path.join(d, "background_L2.dxf")
    dxfread.write_background(dxf, shapes, units="in")
    rr = planread.read_drop(intake.classify_drop([plans, dxf]))
    assert rr.levels[0].blocked and "CAD background" in rr.levels[0].blocked[0], rr.levels[0].blocked
    # a CAD file that declares no units is not used until the user says
    dxfread.write_background(dxf, ss.cad_shapes(2), units="")
    rr = planread.read_drop(intake.classify_drop([plans, dxf]))
    assert rr.frame_kind == "grid" and any("units" in r for r in rr.remarks), rr.remarks
    rr = planread.read_drop(intake.classify_drop([plans, dxf]), M.Options(cad_units="in"))
    assert rr.frame_kind == "cad"
    print("PASS gates: two plausible fill classes ask with picture crops, an "
          "unverified scale, a grid off its printed dimension and a CAD grid off "
          "the sheet each BLOCK the level; undeclared CAD units are not assumed")


def t_rotated():
    _f, _refs, rr = read("rot90", rot=90)
    l2 = rr.level("L2")
    assert not l2.blocked, l2.blocked
    counts = {k: len(l2.of(k)) for k in planread.KIND_ORDER if l2.of(k)}
    assert counts == {"slab_edge": 1, "wall": 6, "column": 3, "round_column": 3,
                      "concrete": 1, "wall_opening": 1, "slab_opening": 2, "sw_end": 8,
                      "penetration": 1, "overlay_opening": 3, "masonry": 2}, counts
    w4 = next(e for e in l2.of(M.WALL) if "clipped" in e.attrs)
    assert same_poly(w4.poly, cad(ss.WALLS_L2[4]["poly"]), 0.005)
    assert len(l2.hidden) == 1 and l2.hidden[0]["strokes"] == 6
    assert rr.level("L1").of(M.SLAB_EDGE)[0].attrs["unconfirmed"] == 1
    assert len(rr.notes) == 7 and rr.schedules[0].disagreements() == [("SW2", 2.5, 3.0)]
    print("PASS case 15 at the reader: the whole set on /Rotate 90 pages reads the "
          "same building in the same CAD frame")


def main():
    t_intake()
    rr = t_level2()
    t_level1(rr)
    t_frames_and_ids()
    t_blocks()
    t_rotated()
    print("BATTERBOARD READ TEST PASSED")


if __name__ == "__main__":
    main()
