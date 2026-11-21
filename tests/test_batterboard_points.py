"""Batterboard points and kits (owner brief section 8, "Points").

The new pure Harvest generators, the five layers, the one evidence note
on every description, stable unique names, the declared frame, the DXF
background, and regression case 14: the CSV, the DXF and the job hold the
same points with the same names and coordinates.

Run:  python tests/test_batterboard_points.py
"""
import collections
import copy
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper import dxfread, harvest, papertrail as pt, redtags, selvage   # noqa: E402
from rfi_stamper.batterboard import crosscheck as xc                           # noqa: E402
from rfi_stamper.batterboard import intake, planread, points                   # noqa: E402
from rfi_stamper.batterboard import model as M                                 # noqa: E402
from rfi_stamper.batterboard import sampleset as ss                            # noqa: E402
from rfi_stamper.batterboard.names import Names                                # noqa: E402

TMP = tempfile.mkdtemp(prefix="bbpoints_")
OX, OY = ss.CAD_ORIGIN_FT


def pipeline(files, opts=None, names=None, reg=None):
    opts = opts or M.Options()
    names = names if names is not None else Names()
    rr = planread.read_drop(intake.classify_drop(files, opts), opts, names)
    reg = reg if reg is not None else redtags.Register()
    reg.begin_run("run")
    xc.run_checks(rr, pt.Trail(), reg, opts, names)
    reg.end_run()
    return rr, reg, points.build_points(rr, reg, opts, names)


def t_generators():
    # an L drawn as two overlapping pieces plus a piece abutting end to end
    a = [(0, 0), (10, 0), (10, 1), (0, 1)]
    b = [(9, 0), (10, 0), (10, 8), (9, 8)]
    c = [(-5, 0), (0, 0), (0, 1), (-5, 1)]
    shapes = [("A", a), ("B", b), ("C", c)]
    before = copy.deepcopy(shapes)
    props = harvest.concrete_corners(shapes)
    assert shapes == before, "generators are pure"
    got = sorted((round(p["e"], 3), round(p["n"], 3)) for p in props)
    assert got == [(-5, 0), (-5, 1), (9, 1), (9, 8), (10, 0), (10, 8)], got
    deg = {(round(p["e"]), round(p["n"])): p["provenance"]["params"]["inside_deg"] for p in props}
    assert deg[(9, 1)] == 270.0 and deg[(10, 0)] == 90.0, "re-entrant and outside corners"
    assert all(set(p) >= {"n", "e", "elev", "z_ref", "desc", "code", "layer", "provenance"}
               and p["provenance"]["gen"] == "concrete_corners" for p in props)
    keys = [p["provenance"]["key"] for p in props]
    assert len(set(keys)) == len(keys)
    again = harvest.concrete_corners(shapes + [("D", [(20, 20), (22, 20), (22, 22), (20, 22)])])
    assert set(keys) <= {p["provenance"]["key"] for p in again}, "a new shape does not rename old corners"
    rc = harvest.round_centers([("R1", (4.0, 5.0), 1.0)])
    assert (rc[0]["e"], rc[0]["n"]) == (4.0, 5.0) and rc[0]["provenance"]["params"]["radius_ft"] == 1.0
    ze = harvest.zone_ends([("E1", (0.0, 0.0), (0.0, 1.0), (1.0, 0.0), 3.0)])
    assert sorted((p["e"], p["n"]) for p in ze) == [(3.0, 0.0), (3.0, 1.0)]
    try:
        harvest.zone_ends([("E2", (0, 0), (0, 1), (1, 0), None)])
        raise SystemExit("an unknown zone length must be refused")
    except ValueError:
        pass
    se = harvest.slab_edge_points("S", [(0, 0, "corner"), (5, 0, "tangent point")], [(5, 3, 3.0)])
    assert [p["code"] for p in se] == ["SEC", "SEC", "RP"] and "TANGENT" in se[1]["desc"]
    oc = harvest.opening_corners([("O", [(1, 1), (2, 1), (2, 2), (1, 2)])])
    assert len(oc) == 4 and oc[0]["provenance"]["gen"] == "opening_corners"
    print("PASS Harvest generators: corners of the union of polygons at face of concrete, "
          "round centers, zone ends (an unknown length refused), slab edge, openings; pure")


def t_points():
    f = ss.build_sample_set(os.path.join(TMP, "full"))
    names = Names(os.path.join(TMP, "names.json"))
    rr, reg, allp = pipeline(f["files"], names=names)
    names.save()
    l2, lp = rr.level("L2"), allp["L2"]
    rows = lp.rows
    assert len({r.name for r in rows}) == len(rows), "names are unique"
    assert all(r.name.startswith("L2-") and len(r.name) == 7 for r in rows)
    layers = collections.Counter(r.layer for r in rows)
    assert set(layers) == set(M.POINT_LAYERS), layers
    grid = [r for r in rows if r.family == "grid"]
    assert sorted(r.grid for r in rows if r.grid) == sorted(
        f"{a}{b}" for a, _ in ss.GRID_X for b, _ in ss.GRID_Y), "every grid crossing is named once"
    assert all(r.layer == M.LAYER_GRID and r.desc == f"GRID GL {r.grid}" for r in grid)
    # a round column centered on a grid crossing is ONE point: the element's
    # evidence is kept and the grid name rides along
    on_grid = [r for r in rows if r.grid and r.family != "grid"]
    assert sorted(r.grid for r in on_grid) == ["B3", "C1", "C3"] and lp.merged >= 3
    assert all(r.family == "round_column_center" and f" {r.grid} " in f" {r.desc} " for r in on_grid)
    by_el = collections.defaultdict(list)
    for r in rows:
        by_el[r.element].append(r)

    def el(pred):
        return next(e for e in l2.elements if pred(e))

    # every description: stage, Stitch Code, element id, one note
    for r in rows:
        if r.family != "grid":
            parts = r.desc.split()
            assert parts[0] in ("PRE", "POST", "GRID") and parts[1] == r.code and parts[2] == r.element
            assert r.desc.endswith(r.note) and r.note, r.desc
    # hold with item number: the wall 3/4 in. off the CAD background
    w1 = el(lambda e: e.kind == M.WALL and e.mark == "SW1")
    assert len(by_el[w1.id]) == 4
    assert all(r.layer == M.LAYER_HOLD and r.note.startswith("HOLD ITEM L2-") for r in by_el[w1.id])
    assert all(reg.get(r.item) is not None for r in by_el[w1.id])
    # the round column 1 in. off holds; the two that match say so, naming the source
    rcs = [r for r in rows if r.family == "round_column_center"]
    assert sorted(r.note_kind for r in rcs) == ["hold", "match", "match"]
    assert all(r.note == "MATCHES CAD BG" for r in rcs if r.note_kind == "match")
    # check with item number: the column 3/8 in. off, the walls with nothing below
    near = el(lambda e: e.kind == M.COLUMN and e.attrs.get("cad") == "near")
    assert all(r.note.startswith("CHECK ITEM") and r.layer == M.LAYER_POST for r in by_el[near.id])
    # the wall that exists only in the CAD background is on hold until answered
    cad_wall = el(lambda e: e.attrs.get("cad_only"))
    assert len(by_el[cad_wall.id]) == 4 and all(r.layer == M.LAYER_HOLD for r in by_el[cad_wall.id])
    # shear wall ends: needs a dimension / dimensioned by the architect / withheld
    zone = [r for r in rows if r.family == "sw_zone_end"]
    kinds = collections.Counter(r.note_kind for r in zone)
    assert kinds == {"dim": 4, "arch": 2, "hold": 2}, kinds
    assert all(r.layer == M.LAYER_DIM and r.note.startswith("NO DIM TO CONC ITEM") for r in zone
               if r.note_kind == "dim")
    assert all(r.note == "DIM BY ARCH" for r in zone if r.note_kind == "arch")
    sw2 = [e.id for e in l2.of(M.SW_END) if e.mark == "SW2"]
    assert sorted(w[0] for w in lp.withheld) == sorted(sw2), "a disputed length lays out nothing"
    assert not any(r.element in sw2 for r in zone)
    # slab: corners, tangent points, the radius point, opening corners (before the pour)
    slab = [r for r in rows if r.family in ("slab_edge_corner", "radius_point")]
    assert len(slab) == 6 and all(r.layer == M.LAYER_PRE and r.stage == "PRE" for r in slab)
    rp = next(r for r in slab if r.code == "RP")
    assert (round(rp.e, 3), round(rp.n, 3)) == (OX + 67.0, OY + 35.0)
    assert sum(1 for r in rows if r.family == "slab_opening_corner") == 8
    assert all(r.note == "SINGLE SRC" for r in slab)
    # level 1: only the two ends of the unconfirmed edge run are held
    l1p = allp["L1"]
    held = [r for r in l1p.rows if r.family == "slab_edge_corner" and r.note_kind == "hold"]
    assert sorted(round(r.e, 2) for r in held) == [OX + ss.L1_LIGHT_RUN[0], OX + ss.L1_LIGHT_RUN[1]]
    assert sum(1 for r in l1p.rows if r.family == "slab_edge_corner") == 6
    print("PASS points: five layers, one evidence note per description, a disputed "
          "zone length lays out nothing, only the ends of an unconfirmed edge are held")
    return f, rr, reg, allp


def t_round_trip(f, rr, reg, allp):
    # ---- regression case 14 ------------------------------------------------- #
    opts = M.Options(kits=["bowline", "sheetbend"])
    for code, lp in allp.items():
        lv = rr.level(code)
        out = os.path.join(TMP, "kits", code)
        files = points.export_level(lp, lv, out, opts)
        base = sorted(os.path.basename(x) for x in files)
        assert f"layout_{code}.csv" in base and f"layout_{code}.dxf" in base
        assert f"layout_{code}.stitch.json" in base and f"background_{code}.dxf" in base
        assert points.round_trip(lp, files) == [], points.round_trip(lp, files)
        frame = open(os.path.join(out, f"layout_{code}_FRAME.txt"), encoding="utf-8").read()
        assert lv.frame.label in frame and "Elevations are not laid out" in frame
        bg = dxfread.read(os.path.join(out, f"background_{code}.dxf"))
        for name in bg.layers:
            selvage.validate_dxf_layer(name)
        assert bg.units == "ft"
        assert any(e.kind == "TEXT" and e.text == lv.frame.label for e in bg.entities)
    lv, lp = rr.level("L2"), allp["L2"]
    out = os.path.join(TMP, "kits", "L2")
    bg = dxfread.read(os.path.join(out, "background_L2.dxf"))
    assert {"BB-CONCRETE", "BB-CONCRETE-CAD-ONLY", "BB-SLAB-EDGE", "BB-SLAB-OPENING",
            "BB-MASONRY", "BB-OVERLAY-APPROX", "BB-GRIDLINES", "BB-FRAME"} <= set(bg.layers)
    assert {M.LAYER_GRID, M.LAYER_PRE, M.LAYER_POST, M.LAYER_DIM, M.LAYER_HOLD} <= set(bg.layers)
    assert sum(1 for e in bg.entities if e.kind == "POINT") == len(lp.rows), \
        "every point in the CSV has a matching point in the DXF"
    files = lp.files
    csv_path = os.path.join(out, "layout_L2.csv")
    good = open(csv_path, encoding="utf-8").read()
    row = lp.rows[20]
    bad = good.replace(f"{row.name},{row.n:.3f},", f"{row.name},{row.n + 0.02:.3f},")
    assert bad != good
    open(csv_path, "w", encoding="utf-8").write(bad)
    got = points.round_trip(lp, files)
    assert len(got) == 1 and "layout_L2.csv" in got[0] and row.name in got[0], got
    lines = good.splitlines()
    open(csv_path, "w", encoding="utf-8").write("\n".join(lines[:-1]) + "\n")
    got = points.round_trip(lp, files)
    assert len(got) == 1 and "point names differ" in got[0], got
    open(csv_path, "w", encoding="utf-8").write(good)
    assert points.round_trip(lp, files) == []
    print("PASS case 14: the CSV, the kit DXF, the DXF background and the job hold the "
          "same names and coordinates; a moved or missing point is caught")


def t_stable(f, allp):
    first = {(round(r.e, 3), round(r.n, 3)): r.name for lp in allp.values() for r in lp.rows}
    # a later run, a fresh process, with one more wall in the CAD background
    d = os.path.join(TMP, "later")
    os.makedirs(d, exist_ok=True)
    shapes = ss.cad_shapes(2)
    shapes.insert(0, {"layer": "S-WALL", "kind": "poly", "closed": True,
                      "pts": [((OX + e) * 12, (OY + n) * 12) for e, n in
                              ((4.0, 18.0), (16.0, 18.0), (16.0, 19.0), (4.0, 19.0))]})
    dxf = os.path.join(d, "background_L2.dxf")
    dxfread.write_background(dxf, shapes, units="in")
    names = Names(os.path.join(TMP, "names.json"))
    _rr, _reg, later = pipeline([f["plans"], f["old"], dxf, f["rfi"]], names=names)
    second = {(round(r.e, 3), round(r.n, 3)): r.name for lp in later.values() for r in lp.rows}
    assert all(second.get(k) == v for k, v in first.items()), "a point that did not move keeps its name"
    new = [v for k, v in second.items() if k not in first]
    assert len(new) == 4 and min(int(n.split("-")[1]) for n in new) > \
        max(int(n.split("-")[1]) for n in first.values() if n.startswith("L2-"))
    # the stage switch, and the answered RFI releasing its points
    _rr, _reg, pre = pipeline(f["files"], opts=M.Options(stage="pre"))
    assert {r.stage for r in pre["L2"].rows} == {"GRID", "PRE"}
    rr0, reg0, _p0 = pipeline(f["files"])
    ref = next(t.ref for t in reg0.items("L2") if t.label == "In the CAD background only")
    opts = M.Options(answered=[{"item": ref, "rfi": "012",
                                "quote": "ADD AN 8 INCH CONCRETE WALL ON GRID C"}])
    rr, reg, ans = pipeline(f["files"], opts=opts)
    wall = next(e for e in rr.level("L2").of(M.WALL) if e.attrs.get("cad_only"))
    got = [r for r in ans["L2"].rows if r.element == wall.id]
    # it is released from the hold of its own item (the wall-below check still applies)
    assert got and all("HOLD" not in r.note for r in got), [r.desc for r in got]
    assert all(r.layer == M.LAYER_POST for r in got)
    hold_all = M.Options(hold_policy={"answer": "hold", "confirm": "hold", "answered": "release"})
    _rr, _reg, strict = pipeline(f["files"], opts=hold_all)
    assert sum(1 for r in strict["L2"].rows if r.layer == M.LAYER_HOLD) > \
        sum(1 for r in allp["L2"].rows if r.layer == M.LAYER_HOLD), "the hold policy is a switch"
    print("PASS stable names across runs (new points number after the last), the stage "
          "switch, the hold policy, an answered item releases its points")


def main():
    t_generators()
    f, rr, reg, allp = t_points()
    t_round_trip(f, rr, reg, allp)
    t_stable(f, allp)
    print("BATTERBOARD POINTS TEST PASSED")


if __name__ == "__main__":
    main()
