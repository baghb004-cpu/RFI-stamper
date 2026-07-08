"""Self-contained tests for the Harvest generators (rfi_stamper.harvest) —
model geometry in, stakeable point PROPOSALS out.  Plain python, no pytest,
no project data.

Exercises:

* purity + the proposal dict contract (n/e or x/y, elev, desc, code,
  layer, provenance {gen, key, rule, params})
* gridiron: explicit labeled line runs (names A1/C7, alpha label first),
  page-frame variant, the draft.DraftModel bridge (Loft grids)
* wall_corners: corners from extrude/draft-style segments, corner inset
  along both walls, witness spec attachment, free ends ignored, the
  draft wall_segments bridge
* along_line: interval stride with remainder center vs end (hand-computed
  positions), sloped-Z linear interpolation (midpoint), divide-N with
  insets, exact-multiple runs, degenerate inputs
* STRIDE_RULES: per-trade table with basis strings, size->spacing ladders
  (never one global interval), unverified rows flagged
* offset_line: signed side (right of travel), O/S lath grammar in the desc
* bolt_cage: 2x2 at 45 deg hand-computed child positions, -A/-B/-C/-D
  naming, parent work point + group-tolerance note, rigid params
* line_intersections: true vs extended crossings, 1/16-in dedupe, label
  composition
* reharvest_diff: unchanged / drifted (dN/dE chips) / orphaned (NEVER
  auto-deleted) / new buckets, matched by provenance

Run:  python3.12 tests/test_harvest.py
"""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper import harvest as hv             # noqa: E402
from rfi_stamper.fieldstitch import LayoutJob     # noqa: E402
from rfi_stamper.markups.measure import ScaleCal  # noqa: E402


def expect(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc as e:
        return e
    raise AssertionError(f"expected {exc.__name__} from {fn!r}{args!r}")


def calibrated_job():
    job = LayoutJob()
    job.base_page_xy = (100.0, 700.0)
    job.base_world = (5000.0, 2000.0)
    job.scale = ScaleCal(real_per_pt=0.1, unit="ft").to_dict()
    return job


def check_shape(pr, world=True):
    """Every proposal honors the contract."""
    keys = {"elev", "z_ref", "name", "desc", "code", "layer", "provenance"}
    assert keys <= set(pr), pr
    if world:
        assert "n" in pr and "e" in pr and "x" not in pr, pr
    else:
        assert "x" in pr and "y" in pr and "n" not in pr, pr
    prov = pr["provenance"]
    assert set(prov) == {"gen", "key", "rule", "params"}, prov
    assert prov["gen"] and prov["key"], prov


# ---------------------------------------------------------------- gridiron --

def test_gridiron_lines():
    rows = [("A", (0.0, 0.0), (100.0, 0.0)),
            ("B", (0.0, 20.0), (100.0, 20.0))]
    cols = [("1", (10.0, -5.0), (10.0, 50.0)),
            ("2", (30.0, -5.0), (30.0, 50.0)),
            ("7", (200.0, -5.0), (200.0, 50.0))]   # off both rows: no hit
    props = hv.gridiron(rows, cols)
    assert len(props) == 4, [p["name"] for p in props]
    for pr in props:
        check_shape(pr)
        assert pr["layer"] == "GRIDLINE" and pr["code"] == "GL"
        assert pr["provenance"]["gen"] == "gridiron"
        assert pr["elev"] is None and pr["z_ref"] == "FF"
    by = {p["name"]: p for p in props}
    assert set(by) == {"A1", "A2", "B1", "B2"}
    assert (by["A1"]["e"], by["A1"]["n"]) == (10.0, 0.0)
    assert (by["B2"]["e"], by["B2"]["n"]) == (30.0, 20.0)
    assert by["A1"]["desc"] == "GL-A1"
    assert by["A1"]["provenance"]["key"] == "A1"
    # alpha label composes FIRST even when the runs are swapped
    props2 = hv.gridiron(cols, rows)
    assert {p["name"] for p in props2} == {"A1", "A2", "B1", "B2"}
    # sorted output: A1, A2, B1, B2
    assert [p["name"] for p in props] == ["A1", "A2", "B1", "B2"]

    # page-frame variant emits x/y
    pp = hv.gridiron(rows, cols, frame="page")
    check_shape(pp[0], world=False)
    assert pp[0]["x"] == 10.0 and pp[0]["y"] == 0.0

    expect(ValueError, hv.gridiron, rows)                # needs both runs
    expect(ValueError, hv.gridiron, rows, cols, frame="sideways")


def test_gridiron_draft():
    from rfi_stamper.draft import DraftModel
    m = DraftModel()
    m.add("grid", [(0.0, 0.0), (60.0, 0.0)], label="A")
    m.add("grid", [(0.0, 25.0), (60.0, 25.0)], label="B")
    m.add("grid", [(10.0, -5.0), (10.0, 40.0)], label="1")
    m.add("grid", [(45.0, -5.0), (45.0, 40.0)], label="2")
    props = hv.gridiron(m)
    assert len(props) == 4
    by = {p["name"]: p for p in props}
    assert set(by) == {"A1", "A2", "B1", "B2"}, sorted(by)
    # Loft model space IS the world frame: x = E, y = N
    assert (by["A1"]["e"], by["A1"]["n"]) == (10.0, 0.0)
    assert (by["B2"]["e"], by["B2"]["n"]) == (45.0, 25.0)
    assert by["A1"]["provenance"]["params"]["source"] == "draft"


# ------------------------------------------------------------ wall corners --

def test_wall_corners():
    # a 20 x 10 room, drawn as 4 wall segments (extrude/draft style: (E, N))
    segs = [((0.0, 0.0), (20.0, 0.0)),
            ((20.0, 0.0), (20.0, 10.0)),
            ((20.0, 10.0), (0.0, 10.0)),
            ((0.0, 10.0), (0.0, 0.0))]
    props = hv.wall_corners(segs)
    assert len(props) == 4
    for pr in props:
        check_shape(pr)
        assert pr["provenance"]["gen"] == "wall_corners"
        assert "witness" not in pr
    corners = sorted((round(p["e"], 6), round(p["n"], 6)) for p in props)
    assert corners == [(0.0, 0.0), (0.0, 10.0), (20.0, 0.0), (20.0, 10.0)]

    # inset moves the corner inward along BOTH walls
    ins = hv.wall_corners(segs, inset_ft=0.5)
    got = sorted((round(p["e"], 6), round(p["n"], 6)) for p in ins)
    assert got == [(0.5, 0.5), (0.5, 9.5), (19.5, 0.5), (19.5, 9.5)], got
    assert "INSET 0.5FT" in ins[0]["desc"]
    assert ins[0]["provenance"]["params"]["inset_ft"] == 0.5

    # witness spec rides every proposal for the commit step
    wit = hv.wall_corners(segs, witness={"offset_ft": 2.0, "azimuth": 90.0})
    for pr in wit:
        assert pr["witness"] == {"offset_ft": 2.0, "azimuth": 90.0}

    # a free-standing wall end is NOT a corner
    open_segs = [((0.0, 0.0), (20.0, 0.0)), ((20.0, 0.0), (20.0, 10.0))]
    props2 = hv.wall_corners(open_segs)
    assert len(props2) == 1
    assert (props2[0]["e"], props2[0]["n"]) == (20.0, 0.0)
    # a T junction (3 legs) stays put even with an inset
    tee = open_segs + [((20.0, 0.0), (30.0, 0.0))]
    props3 = hv.wall_corners(tee, inset_ft=1.0)
    assert (props3[0]["e"], props3[0]["n"]) == (20.0, 0.0)
    assert props3[0]["provenance"]["params"]["legs"] == 3

    # the draft bridge: wall_segments feed straight in
    from rfi_stamper.draft import DraftModel
    m = DraftModel()
    m.add("wall", [(0.0, 0.0), (12.0, 0.0)])
    m.add("wall", [(12.0, 0.0), (12.0, 8.0)])
    props4 = hv.wall_corners(m.wall_segments())
    assert len(props4) == 1
    assert (props4[0]["e"], props4[0]["n"]) == (12.0, 0.0)


# -------------------------------------------------------------- along line --

def test_along_line_interval():
    a, b = (0.0, 0.0), (0.0, 35.0)                # 35 ft due north
    # dump-at-far-end: 0, 10, 20, 30 (slack 5 at the far end)
    end = hv.along_line(a, b, "interval", stride_ft=10.0, remainder="end")
    assert [round(p["n"], 6) for p in end] == [0.0, 10.0, 20.0, 30.0]
    assert all(p["e"] == 0.0 for p in end)
    # center-the-slack: 2.5, 12.5, 22.5, 32.5
    ctr = hv.along_line(a, b, "interval", stride_ft=10.0,
                        remainder="center")
    assert [round(p["n"], 6) for p in ctr] == [2.5, 12.5, 22.5, 32.5]
    for pr in ctr:
        check_shape(pr)
        assert pr["provenance"]["gen"] == "along_line"
        assert pr["elev"] is None
    # walk-order keys are stable and sequential
    assert [p["provenance"]["key"] for p in ctr] == \
        ["0000", "0001", "0002", "0003"]
    # exact multiple: both modes agree and include the far end
    ex_e = hv.along_line(a, (0.0, 30.0), "interval", stride_ft=10.0,
                         remainder="end")
    ex_c = hv.along_line(a, (0.0, 30.0), "interval", stride_ft=10.0,
                         remainder="center")
    assert [round(p["n"], 6) for p in ex_e] == [0.0, 10.0, 20.0, 30.0]
    assert [round(p["n"], 6) for p in ex_c] == [0.0, 10.0, 20.0, 30.0]
    # span shorter than one stride: a single point (start vs centered)
    short_e = hv.along_line(a, (0.0, 6.0), "interval", stride_ft=10.0,
                            remainder="end")
    short_c = hv.along_line(a, (0.0, 6.0), "interval", stride_ft=10.0,
                            remainder="center")
    assert [p["n"] for p in short_e] == [0.0]
    assert [p["n"] for p in short_c] == [3.0]
    # insets clip the span before striding (first hanger 1 ft off the wall)
    insd = hv.along_line(a, b, "interval", stride_ft=10.0,
                         inset_start=1.0, inset_end=2.0, remainder="end")
    assert [round(p["n"], 6) for p in insd] == [1.0, 11.0, 21.0, 31.0]

    expect(ValueError, hv.along_line, a, a, "interval", stride_ft=10.0)
    expect(ValueError, hv.along_line, a, b, "interval")     # no stride
    expect(ValueError, hv.along_line, a, b, "interval", stride_ft=-1.0)
    expect(ValueError, hv.along_line, a, b, "interval", stride_ft=10.0,
           remainder="shrug")
    expect(ValueError, hv.along_line, a, b, "zigzag", stride_ft=10.0)
    expect(ValueError, hv.along_line, a, (0.0, 3.0), "interval",
           stride_ft=1.0, inset_start=2.0, inset_end=2.0)


def test_along_line_slope_divide():
    a, b = (0.0, 0.0), (0.0, 35.0)
    # sloped-Z: linear between the FULL endpoints, never copied down
    ctr = hv.along_line(a, b, "interval", stride_ft=10.0,
                        remainder="center", z_interp=(100.0, 101.0))
    mid = ctr[1]                                   # at 12.5 of 35
    assert abs(mid["elev"] - (100.0 + 12.5 / 35.0)) < 1e-12
    zs = [p["elev"] for p in ctr]
    assert zs == sorted(zs) and zs[0] > 100.0 and zs[-1] < 101.0
    # the exact midpoint of a divide-2 run reads (za+zb)/2
    div2 = hv.along_line(a, b, "divide", n=2, z_interp=(100.0, 101.0))
    assert abs(div2[1]["elev"] - 100.5) < 1e-12
    assert div2[1]["n"] == 17.5

    # divide-N with insets: n+1 points across the inset span
    div = hv.along_line(a, b, "divide", n=4, inset_start=1.0, inset_end=1.0)
    assert [round(p["n"], 6) for p in div] == [1.0, 9.25, 17.5, 25.75, 34.0]
    assert len(div) == 5
    expect(ValueError, hv.along_line, a, b, "divide")
    expect(ValueError, hv.along_line, a, b, "divide", n=0)

    # a diagonal run interpolates along the true length
    diag = hv.along_line((0.0, 0.0), (30.0, 40.0), "divide", n=2,
                         z_interp=(10.0, 12.0))                 # L = 50
    assert abs(diag[1]["e"] - 15.0) < 1e-12
    assert abs(diag[1]["n"] - 20.0) < 1e-12
    assert abs(diag[1]["elev"] - 11.0) < 1e-12


def test_stride_rules():
    # spacing is a size->spacing lookup per material, never one global
    assert hv.stride_for("STEEL-THREADED") == 12.0
    assert hv.stride_for("COPPER", 1.0) == 6.0      # <= 1-1/4 in
    assert hv.stride_for("COPPER", 1.25) == 6.0
    assert hv.stride_for("COPPER", 1.5) == 10.0
    assert hv.stride_for("CPVC", 1.0) == 3.0
    assert hv.stride_for("CPVC", 2.0) == 4.0
    assert hv.stride_for("PVC") == 4.0
    assert hv.stride_for("CAST-IRON") == 5.0
    assert hv.stride_for("STEEL-BY-SIZE", 3.0) == 12.0
    assert hv.stride_for("STEEL-BY-SIZE", 8.0) == 19.0
    assert hv.stride_for("DUCT") == 9.0
    assert hv.stride_for("steel-threaded") == 12.0  # case-tolerant
    # size-banded rules refuse a blanket call
    e = expect(ValueError, hv.stride_for, "COPPER")
    assert "size" in str(e), e
    expect(ValueError, hv.stride_for, "UNOBTAINIUM")
    # every rule states its basis; unverified rows say so
    for name, rule in hv.STRIDE_RULES.items():
        assert rule.get("basis"), name
        assert "verified" in rule, name
    assert not hv.STRIDE_RULES["STEEL-BY-SIZE"]["verified"]
    assert "UNVERIFIED" in hv.STRIDE_RULES["STEEL-BY-SIZE"]["basis"]
    assert hv.STRIDE_RULES["STEEL-THREADED"]["v_stride_ft"] == 15.0
    assert hv.STRIDE_RULES["DUCT"]["wide_over_in"] == 60.0


# -------------------------------------------------------------- offset line --

def test_offset_line():
    # baseline due north: positive offset = RIGHT of travel = east
    a, b = (0.0, 0.0), (0.0, 30.0)
    right = hv.offset_line(a, b, 5.0, "divide", n=3)
    assert all(abs(p["e"] - 5.0) < 1e-12 for p in right), \
        [(p["e"], p["n"]) for p in right]
    assert [round(p["n"], 6) for p in right] == [0.0, 10.0, 20.0, 30.0]
    left = hv.offset_line(a, b, -5.0, "divide", n=3)
    assert all(abs(p["e"] + 5.0) < 1e-12 for p in left)
    # the lath grammar rides the desc; provenance says offset_line
    assert right[0]["desc"].startswith("O/S 5.00 R -> ")
    assert left[0]["desc"].startswith("O/S 5.00 L -> ")
    pr = right[0]["provenance"]
    assert pr["gen"] == "offset_line"
    assert pr["params"]["offset_ft"] == 5.0
    assert pr["params"]["baseline"] == ((0.0, 0.0), (0.0, 30.0))
    assert "offset 5 ft" in pr["rule"]
    # east-heading baseline: right of travel = south
    s = hv.offset_line((0.0, 0.0), (30.0, 0.0), 2.0, "divide", n=1)
    assert all(abs(p["n"] + 2.0) < 1e-12 for p in s)
    # stride mode passes through (remainder honored)
    st = hv.offset_line(a, b, 5.0, "interval", stride_ft=12.0,
                        remainder="end")
    assert [round(p["n"], 6) for p in st] == [0.0, 12.0, 24.0]
    expect(ValueError, hv.offset_line, a, a, 5.0, "divide", n=2)


# ---------------------------------------------------------------- bolt cage --

def test_bolt_cage():
    # 2x2 at 45 deg, 4 in gauges: children land exactly sqrt(2)/6 ft from
    # the work point, rotated onto the cardinals (hand-computed)
    r = math.sqrt(2.0) / 6.0
    props = hv.bolt_cage((100.0, 200.0), 2, 2, 4.0, 4.0, 45.0, name="C4")
    assert len(props) == 5
    parent, kids = props[0], props[1:]
    check_shape(parent)
    assert (parent["e"], parent["n"]) == (100.0, 200.0)
    assert parent["name"] == "C4"
    assert parent["note"] == hv.BOLT_GROUP_NOTE
    assert "template jig" in hv.BOLT_GROUP_NOTE
    assert "1/8 in" in hv.BOLT_GROUP_NOTE and "1/4 in" in hv.BOLT_GROUP_NOTE
    assert "ROT 45" in parent["desc"]
    assert [k["name"] for k in kids] == ["C4-A", "C4-B", "C4-C", "C4-D"]
    got = {k["name"]: (round(k["e"] - 100.0, 9), round(k["n"] - 200.0, 9))
           for k in kids}
    r9 = round(r, 9)
    # local NW/NE/SW/SE rotated 45 deg clockwise -> N/E/W/S
    assert got["C4-A"] == (0.0, r9), got
    assert got["C4-B"] == (r9, 0.0), got
    assert got["C4-C"] == (-r9, 0.0), got
    assert got["C4-D"] == (0.0, -r9), got
    # rigid body: every child carries the SAME pattern params
    for k in kids:
        assert k["parent"] == "C4"
        assert k["provenance"]["params"] == parent["provenance"]["params"]
        assert k["provenance"]["params"]["rot_deg"] == 45.0

    # unrotated 2x2 at 6x8 in: half-gauges 0.25 ft NS, 1/3 ft EW
    sq = hv.bolt_cage((0.0, 0.0), 2, 2, 6.0, 8.0, 0.0)
    ks = {k["provenance"]["key"]: (round(k["e"], 9), round(k["n"], 9))
          for k in sq[1:]}
    third = round(1.0 / 3.0, 9)
    assert ks["CAGE-A"] == (-third, 0.25)         # NW
    assert ks["CAGE-B"] == (third, 0.25)          # NE
    assert ks["CAGE-C"] == (-third, -0.25)        # SW
    assert ks["CAGE-D"] == (third, -0.25)         # SE
    # 1x3 row names A, B, C west->east
    row = hv.bolt_cage((0.0, 0.0), 1, 3, 4.0, 10.0, 0.0)
    names = [k["name"] for k in row[1:]]
    assert names == [None, None, None]            # no cage name given
    keys = [k["provenance"]["key"] for k in row[1:]]
    assert keys == ["CAGE-A", "CAGE-B", "CAGE-C"]
    es = [k["e"] for k in row[1:]]
    assert es == sorted(es)
    expect(ValueError, hv.bolt_cage, (0, 0), 0, 2, 4, 4, 0)
    expect(ValueError, hv.bolt_cage, (0, 0), 2, 0, 4, 4, 0)


# -------------------------------------------------------- line intersections --

def test_line_intersections():
    grid = [("A", (0.0, 5.0), (30.0, 5.0))]
    walls = [((10.0, 0.0), (10.0, 10.0)),
             ((10.003, 0.0), (10.003, 10.0)),     # within 1/16 in: dedupe
             ((20.0, 0.0), (20.0, 4.0))]          # stops short of the grid
    props = hv.line_intersections(grid, walls)
    assert len(props) == 1, [(p["e"], p["n"]) for p in props]
    check_shape(props[0])
    assert (props[0]["e"], props[0]["n"]) == (10.0, 5.0)
    assert props[0]["name"] is None               # wall carries no label
    # extend=True finds the apparent crossing of the short wall too
    ext = hv.line_intersections(grid, walls, extend=True)
    assert len(ext) == 2
    assert {(round(p["e"], 6), round(p["n"], 6)) for p in ext} == \
        {(10.0, 5.0), (20.0, 5.0)}
    assert "extended" in ext[0]["provenance"]["rule"]
    # labels on BOTH sides compose the name (alpha first)
    both = hv.line_intersections(grid,
                                 [("1", (10.0, 0.0), (10.0, 10.0))])
    assert both[0]["name"] == "A1" and both[0]["desc"] == "WP-A1"
    assert both[0]["provenance"]["key"] == "A1"
    # a wider dedupe window collapses near-crossings
    wide = hv.line_intersections(grid, walls[:2], dedupe_ft=0.0001)
    assert len(wide) == 2                          # tighter window keeps both
    # parallel lines never intersect (even extended)
    para = hv.line_intersections(grid, [((0.0, 7.0), (30.0, 7.0))],
                                 extend=True)
    assert para == []


# ------------------------------------------------------------ re-harvest ----

def test_reharvest(tmp):
    job = calibrated_job()
    rows = [("A", (2000.0, 5000.0), (2100.0, 5000.0)),
            ("B", (2000.0, 5020.0), (2100.0, 5020.0))]
    cols = [("1", (2010.0, 4990.0), (2010.0, 5040.0)),
            ("2", (2030.0, 4990.0), (2030.0, 5040.0))]
    props = hv.gridiron(rows, cols)
    assert len(props) == 4
    # commit them (what the GUI's commit lever does)
    for pr in props:
        x, y = job.from_world(pr["n"], pr["e"])
        job.add_point(1, x, y, desc=pr["desc"], code=pr["code"],
                      layer=pr["layer"], provenance=pr["provenance"])
    assert len(job.points) == 4

    # the model changes: column 2 shifts 0.5 ft east, row B disappears,
    # a new column 3 arrives
    cols2 = [("1", (2010.0, 4990.0), (2010.0, 5040.0)),
             ("2", (2030.5, 4990.0), (2030.5, 5040.0)),
             ("3", (2050.0, 4990.0), (2050.0, 5040.0))]
    props2 = hv.gridiron(rows[:1], cols2)
    diff = hv.reharvest_diff(job, props2)
    assert sorted(diff) == ["drifted", "new", "orphaned", "unchanged"]
    assert len(diff["unchanged"]) == 1
    assert diff["unchanged"][0]["proposal"]["name"] == "A1"
    assert len(diff["drifted"]) == 1
    d = diff["drifted"][0]
    assert d["proposal"]["name"] == "A2"
    assert abs(d["de"] - 0.5) < 1e-9 and abs(d["dn"]) < 1e-9, d
    assert abs(d["hd"] - 0.5) < 1e-9
    # orphans: B1 and B2 — reported, NEVER deleted
    orphan_keys = sorted(p.provenance["key"] for p in diff["orphaned"])
    assert orphan_keys == ["B1", "B2"], orphan_keys
    assert len(job.points) == 4, "reharvest_diff mutated the job!"
    assert len(diff["new"]) == 1
    assert diff["new"][0]["name"] == "A3"

    # drift below 1/16 in stays UNCHANGED; dz counts as drift
    tiny = hv.gridiron(rows[:1], cols[:1])
    tiny[0]["n"] += 0.004                          # < 0.005 ft
    diff2 = hv.reharvest_diff(job, tiny)
    assert len(diff2["unchanged"]) == 1 and not diff2["drifted"]
    lifted = hv.gridiron(rows[:1], cols[:1], elev=101.0)
    a1 = job.points[0]
    a1.elev = 100.0
    diff3 = hv.reharvest_diff(job, lifted)
    assert len(diff3["drifted"]) == 1
    assert abs(diff3["drifted"][0]["dz"] - 1.0) < 1e-9
    a1.elev = 0.0

    # page-frame proposals convert through the job frame (fresh labels —
    # provenance keys are identity, so P9 must not collide with A1)
    page_props = hv.gridiron(
        [("P", (100.0, 700.0), (200.0, 700.0))],
        [("9", (110.0, 650.0), (110.0, 750.0))], frame="page")
    assert "x" in page_props[0]
    x, y = job.from_world(5000.0, 2001.0)
    assert (page_props[0]["x"], page_props[0]["y"]) == (x, y)
    committed = job.add_point(1, page_props[0]["x"], page_props[0]["y"],
                              provenance=page_props[0]["provenance"])
    diff4 = hv.reharvest_diff(job, page_props)
    assert any(en["point"] is committed for en in diff4["unchanged"])

    # points without provenance never appear in any bucket
    hand = job.add_point(1, 300.0, 300.0)
    diff5 = hv.reharvest_diff(job, [])
    assert hand not in diff5["orphaned"]
    assert committed in diff5["orphaned"]          # its generator ran empty

    # provenance survives the sidecar round trip
    side = os.path.join(tmp, "prov.stitch.json")
    job.save(side)
    back = LayoutJob()
    back.load(side)
    back.base_page_xy = job.base_page_xy
    back.base_world = job.base_world
    back.scale = dict(job.scale)
    keys = {p.provenance["key"] for p in back.points if p.provenance}
    assert {"A1", "A2", "B1", "B2"} <= keys


def main():
    tmp = tempfile.mkdtemp(prefix="harvest_")
    test_gridiron_lines()
    test_gridiron_draft()
    test_wall_corners()
    test_along_line_interval()
    test_along_line_slope_divide()
    test_stride_rules()
    test_offset_line()
    test_bolt_cage()
    test_line_intersections()
    test_reharvest(tmp)
    print("HARVEST TESTS PASSED  (proposal contract + purity, gridiron "
          "lines/page/Loft bridge, wall corners + inset + witness spec, "
          "along-line stride remainder center/end + sloped-Z + divide "
          "insets, per-trade stride rules, offset-line side + lath "
          "grammar, bolt cage 2x2@45 hand-computed + group note, "
          "intersections dedupe/extend/labels, reharvest "
          "unchanged/drifted/orphaned/new)")
    print("outputs in", tmp)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("HARVEST TEST FAILED:", e)
        sys.exit(1)
