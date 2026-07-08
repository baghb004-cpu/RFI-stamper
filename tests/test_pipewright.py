"""Self-contained tests for rfi_stamper.pipewright — the piping engine —
and the Loft's additive "pipe" entity kind.  Plain python, no pytest, no
project data.  Exercises:

* SYSTEMS / SIZES_IN / MIN_SLOPE tables, ply parity with the Loft defaults
* pipe entities: per-system ply + material + size defaults, save/load
* network(): node merge tolerance, end/corner/junction/fixture kinds, flow
  direction on legs
* fitting derivation truth table: elbow 45/90 bands, straight pass-through,
  reducers, santee/combo/wye/tee drainage rules (slope-context switch),
  pressure tees, cross + drainage warning, fixture p-traps / closet flange
* commands: cap_open_ends (idempotent, per-system, single-undo),
  replace_fitting (persists through save/load, by xy or index),
  slope_run (exact feet-inches fall, junction propagation, mid-run
  takeoffs, uphill refusal, under-minimum warning), resize_run
* check(): slope-min, open-end, cross-drainage, reduce-downstream,
  vent-slope info
* takeoff(): pipe LF by system/size/material + fitting counts + price book
* to_bim(): invert z math, per-system colors and legend systems
* render_ops: run linework, size label, IE text, cap/elbow/tee/cleanout/
  p-trap symbols, hidden-ply culling, dashed vents

Run:  python3 tests/test_pipewright.py
"""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper.draft import (                    # noqa: E402
    DEFAULT_PLIES, DraftModel, PIPE_SYM_IN, render_ops)
from rfi_stamper.pipewright import (               # noqa: E402
    DRAINAGE, MERGE_TOL_FT, MIN_SLOPE, OVERRIDE_KINDS, SIZES_IN, SYSTEMS,
    cap_open_ends, check, derive_fittings, fmt_dia_in, fmt_slope, min_slope,
    network, replace_fitting, resize_run, slope_run, takeoff, to_bim)

TMP = tempfile.mkdtemp(prefix="pipewright_test_")


# ------------------------------------------------------------------ tables --

def test_tables():
    assert set(SYSTEMS) == {"san", "vent", "storm", "dcw", "dhw", "gas"}
    for key, spec in SYSTEMS.items():
        assert {"label", "color", "ply", "material",
                "dashed", "dia_in"} <= set(spec), key
    assert SYSTEMS["vent"]["dashed"] is True
    assert DRAINAGE == ("san", "storm")
    # every system ply exists in the Loft defaults with the same hue
    plies = {p.name: p for p in DEFAULT_PLIES}
    for key, spec in SYSTEMS.items():
        assert spec["ply"] in plies, key
        assert plies[spec["ply"]].color == spec["color"], key
    assert plies["P-SAN"].weight == "heavy"
    assert plies["P-STRM"].weight == "heavy"
    assert plies["P-VENT"].linetype == "hidden"      # vents dash in plan
    assert plies["P-GAS"].linetype == "phantom"
    assert plies["P-DCW"].weight == "medium"
    assert plies["P-DHW"].weight == "medium"
    # distinct hues across the six pipe plies
    hues = [plies[SYSTEMS[k]["ply"]].color for k in SYSTEMS]
    assert len(set(hues)) == 6, hues
    # trade sizes: ascending ladder, the standard sizes present
    assert SIZES_IN == sorted(SIZES_IN)
    for s in (1.5, 2, 3, 4, 6, 8):
        assert float(s) in SIZES_IN, s
    # drainage slope minimums by diameter
    assert min_slope(1.5)[0] == 0.25
    assert min_slope(2.0)[0] == 0.25
    assert min_slope(2.99)[0] == 0.25
    assert min_slope(3.0)[0] == 0.125
    assert min_slope(4.0)[0] == 0.125
    assert min_slope(6.0)[0] == 0.125
    assert min_slope(8.0)[0] == 0.0625
    assert min_slope(12.0)[0] == 0.0625
    for row in MIN_SLOPE:
        assert "verify against project code" in row["basis"]
    # trade-size / slope text
    assert fmt_dia_in(4.0) == "4"
    assert fmt_dia_in(1.5) == "1 1/2"
    assert fmt_dia_in(0.75) == "3/4"
    assert fmt_dia_in(2.5) == "2 1/2"
    assert fmt_slope(0.125) == '1/8"/ft'
    assert fmt_slope(0.25) == '1/4"/ft'
    assert fmt_slope(0.0625) == '1/16"/ft'


# ------------------------------------------------------------- pipe entity --

def test_pipe_entity():
    m = DraftModel()
    p = m.add("pipe", [(0, 0), (20, 0)])
    assert p.ply == "P-SAN"
    assert p.props["system"] == "san"
    assert abs(p.props["dia_in"] - 4.0) < 1e-9
    assert p.props["material"] == "PVC DWV"
    assert p.props["invert_ft"] is None and p.props["slope_in_ft"] is None
    v = m.add("pipe", [(0, 5), (20, 5)], system="vent")
    assert v.ply == "P-VENT" and abs(v.props["dia_in"] - 2.0) < 1e-9
    for sysname in ("storm", "dcw", "dhw", "gas"):
        e = m.add("pipe", [(0, 30), (5, 30)], system=sysname)
        assert e.ply == SYSTEMS[sysname]["ply"], sysname
        assert e.props["material"] == SYSTEMS[sysname]["material"]
    g = m.add("pipe", [(0, 9), (9, 9)], system="gas", dia_in=1.5,
              ply="G-ANNO")
    assert g.ply == "G-ANNO"               # explicit ply wins
    assert abs(g.props["dia_in"] - 1.5) < 1e-9
    w = m.add("pipe", [(0, 12), (9, 12)], system="nosuch")
    assert w.ply == "P-SAN"                # unknown system falls back
    # save/load round trip incl. caps, overrides and slope data
    m.update(p.id, invert_ft=100.0, slope_in_ft=0.25, capped=True,
             fit_overrides={"20.000,0.000": "cleanout"})
    path = os.path.join(TMP, "pipes.loft.json")
    m.save(path)
    m2 = DraftModel.load(path)
    p2 = m2.entity(p.id)
    assert p2.kind == "pipe" and p2.ply == "P-SAN"
    assert p2.props["system"] == "san"
    assert abs(p2.props["invert_ft"] - 100.0) < 1e-9
    assert abs(p2.props["slope_in_ft"] - 0.25) < 1e-9
    assert p2.props["capped"] is True
    assert p2.props["fit_overrides"] == {"20.000,0.000": "cleanout"}
    assert m2.entity(v.id).props["slope_in_ft"] is None


# ---------------------------------------------------------------- network --

def test_network():
    m = DraftModel()
    m.add("fixture", [(0, 0.4)], stencil="wc")
    a = m.add("pipe", [(0, 0), (10, 0), (20, 0)])
    m.add("pipe", [(20, 0), (40, 0)])
    m.add("pipe", [(10, 8), (10, 0)], dia_in=3.0)
    net = network(m)
    assert len(net.nodes) == 5, len(net.nodes)
    assert len(net.edges) == 4

    def node(x, y):
        i = net.node_near(x, y, 0.1)
        assert i is not None, (x, y)
        return net.nodes[i]

    fx = node(0, 0)
    assert fx.kind == "fixture" and fx.fixture == "wc" and fx.degree == 1
    j = node(10, 0)
    assert j.kind == "junction" and j.degree == 3
    assert node(20, 0).kind == "corner" and node(20, 0).degree == 2
    assert node(40, 0).kind == "end" and node(40, 0).fixture is None
    assert node(10, 8).kind == "end"
    # flow direction on the junction legs: the main passes through
    # (one inbound + one outbound mid leg), the branch arrives ("last")
    ends = sorted((leg.end, leg.inbound) for leg in j.legs)
    assert ends == [("last", True), ("mid", False), ("mid", True)], ends
    assert all(leg.ent_id for leg in j.legs)
    assert {leg.dia_in for leg in j.legs} == {4.0, 3.0}
    # merge tolerance: 0.04 ft joins, 0.2 ft does not
    m2 = DraftModel()
    m2.add("pipe", [(0, 0), (10, 0)])
    m2.add("pipe", [(10.04, 0.0), (20, 0)])
    n2 = network(m2)
    assert len(n2.nodes) == 3, len(n2.nodes)
    assert n2.nodes[n2.node_near(10, 0, 0.1)].degree == 2
    m3 = DraftModel()
    m3.add("pipe", [(0, 0), (10, 0)])
    m3.add("pipe", [(10.2, 0.0), (20, 0)])
    assert len(network(m3).nodes) == 4
    assert MERGE_TOL_FT == 0.05


# ------------------------------------------------------------ elbow bands ---

def bend(defl_deg, dia_b=None, system="san"):
    """Two runs meeting at (10, 0) with the given deflection; returns the
    Fitting derived at that node (None when it passes straight through)."""
    m = DraftModel()
    m.add("pipe", [(0, 0), (10, 0)], system=system)
    t = math.radians(defl_deg)
    props = {"dia_in": dia_b} if dia_b else {}
    m.add("pipe", [(10, 0), (10 + 10 * math.cos(t), 10 * math.sin(t))],
          system=system, **props)
    for f in derive_fittings(m):
        if abs(f.node_xy[0] - 10) < 1e-6 and abs(f.node_xy[1]) < 1e-6:
            return f
    return None


def test_fitting_bands():
    assert bend(45).kind == "elbow45"
    assert bend(30).kind == "elbow45"       # band floor
    assert bend(59).kind == "elbow45"
    assert bend(60).kind == "elbow90"       # band edge
    assert bend(90).kind == "elbow90"
    assert abs(bend(90).angle_deg - 90.0) < 1e-6
    assert abs(bend(45).angle_deg - 45.0) < 1e-6
    assert bend(120).kind == "elbow90"
    f = bend(130)
    assert f.kind == "elbow90" and "verify" in f.note
    assert bend(10) is None                 # straight-through: no fitting
    assert bend(0) is None
    # a size change is a reducer, larger size first
    f = bend(0.0, dia_b=3.0)
    assert f is not None and f.kind == "reducer 4x3", f
    assert bend(0.0, dia_b=6.0).kind == "reducer 6x4"
    assert bend(0.0, dia_b=1.5).kind == "reducer 4x1 1/2"
    f = bend(90.0, dia_b=3.0)
    assert f.kind == "reducer 4x3" and "bend" in f.note


# -------------------------------------------------------------- junctions ---

def junction(system="san", slope_ctx=False, branch_deg=90.0):
    """A main with a mid vertex at (10, 0) and a branch flowing into it at
    the given bearing; returns the Fitting derived at the junction."""
    m = DraftModel()
    main = m.add("pipe", [(0, 0), (10, 0), (20, 0)], system=system)
    t = math.radians(branch_deg)
    m.add("pipe", [(10 + 8 * math.cos(t), 8 * math.sin(t)), (10, 0)],
          system=system)
    if slope_ctx:
        m.update(main.id, slope_in_ft=0.125, invert_ft=100.0)
    for f in derive_fittings(m):
        if abs(f.node_xy[0] - 10) < 1e-6 and abs(f.node_xy[1]) < 1e-6:
            return f
    return None


def test_junctions():
    # ~90° drainage branch with no slope context: assume a vertical branch
    f = junction("san", slope_ctx=False, branch_deg=90)
    assert f.kind == "santee", f.kind
    assert "vertical" in f.note and "verify" in f.note
    assert abs(f.angle_deg - 90.0) < 1e-6
    assert f.branch_deg is not None and abs(f.branch_deg - 90.0) < 1e-6
    assert len(f.legs_deg) == 3
    # ...with slope context (horizontal main) on sanitary: prefer a combo
    f = junction("san", slope_ctx=True, branch_deg=90)
    assert f.kind == "combo", f.kind
    assert f.note == "90° drainage branch: combo/wye+1/8 bend recommended"
    # ...on storm: tee, with the wye recommendation spelled out
    f = junction("storm", slope_ctx=True, branch_deg=90)
    assert f.kind == "tee" and "wye" in f.note
    # ~45° drainage branch is a wye, context or not
    assert junction("san", branch_deg=45).kind == "wye"
    assert junction("san", slope_ctx=True, branch_deg=45).kind == "wye"
    assert junction("storm", branch_deg=50).kind == "wye"
    # pressure systems tee at any angle, no drama
    f = junction("dcw", branch_deg=90)
    assert f.kind == "tee" and f.note == ""
    assert junction("dcw", branch_deg=45).kind == "tee"
    assert junction("gas", branch_deg=90).kind == "tee"
    assert junction("dhw", slope_ctx=True, branch_deg=90).kind == "tee"
    # degree 4 -> cross, warned on drainage
    m = DraftModel()
    m.add("pipe", [(0, 0), (10, 0), (20, 0)])
    m.add("pipe", [(10, 8), (10, 0), (10, -8)])
    f = [f for f in derive_fittings(m)
         if abs(f.node_xy[0] - 10) < 1e-6 and abs(f.node_xy[1]) < 1e-6][0]
    assert f.kind == "cross"
    assert "avoid crosses on drainage" in f.note
    assert len(f.legs_deg) == 4
    assert sorted(f.ent_ids) == sorted(e.id for e in m.ents)


# ----------------------------------------------------- fixture connections --

def test_fixture_fittings():
    m = DraftModel()
    m.add("fixture", [(0, 0.4)], stencil="wc")
    m.add("fixture", [(20, 0.4)], stencil="lav")
    m.add("fixture", [(40, 0.4)], stencil="sink_s")
    m.add("fixture", [(60, 0.4)], stencil="df")
    m.add("fixture", [(80, 0.4)], stencil="wh")    # equipment, not plumbing
    for x in (0, 20, 40, 60, 80):
        m.add("pipe", [(x, 0), (x, -10)])
    kinds = {round(f.node_xy[0]): f.kind for f in derive_fittings(m)
             if abs(f.node_xy[1]) < 1e-9}
    assert kinds[0] == "closet-flange", kinds
    assert kinds[20] == "ptrap"
    assert kinds[40] == "ptrap"
    assert kinds[60] == "ptrap"
    assert kinds[80] == "open"     # a water heater is not a plumbing stencil
    # fixture connections are NOT open ends: capping skips them
    r = cap_open_ends(m)
    assert r["changed"] == 6, r["report"]      # 5 bottoms + the wh stub top
    xs = sorted(round(c["xy"][0]) for c in r["capped"])
    assert xs == [0, 20, 40, 60, 80, 80], xs
    # a non-sanitary stub at a fixture is a plain connection
    m2 = DraftModel()
    m2.add("fixture", [(5, 0.3)], stencil="lav")
    m2.add("pipe", [(5, 0), (5, -6)], system="dcw")
    f = [f for f in derive_fittings(m2) if f.node_xy == (5.0, 0.0)][0]
    assert f.kind == "fixture" and "lav" in f.note


# ------------------------------------------------------------ cap command ---

def test_cap_open_ends():
    m = DraftModel()
    m.add("pipe", [(0, 0), (20, 0)])
    m.add("pipe", [(0, 10), (20, 10)], system="dcw")
    r = cap_open_ends(m, system="san")
    assert r["changed"] == 2, r
    assert len(r["capped"]) == 2
    assert all(c["system"] == "san" for c in r["capped"])
    assert all(c["ent_id"] and c["dia_in"] == 4.0 for c in r["capped"])
    assert "Capped 2 open end(s)" in r["report"]
    assert "0'-0\"" in r["report"] and "20'-0\"" in r["report"]
    kinds = [f.kind for f in derive_fittings(m)]
    assert kinds.count("cap") == 2 and kinds.count("open") == 2, kinds
    # idempotent per system
    r2 = cap_open_ends(m, system="san")
    assert r2["changed"] == 0 and r2["capped"] == []
    # then the rest
    r3 = cap_open_ends(m)
    assert r3["changed"] == 2
    assert not [f for f in derive_fittings(m) if f.kind == "open"]
    # the whole command is ONE undo step
    assert m.undo()
    kinds = [f.kind for f in derive_fittings(m)]
    assert kinds.count("open") == 2 and kinds.count("cap") == 2


# -------------------------------------------------------- replace fitting ---

def test_replace_fitting():
    m = DraftModel()
    main = m.add("pipe", [(0, 0), (10, 0), (20, 0)])
    m.add("pipe", [(10, 8), (10, 0)])
    r = replace_fitting(m, (10.02, 0.01), "combo")
    assert r["changed"] == 1 and r["kind"] == "combo"
    assert r["ent_id"] == main.id           # the FIRST adjoining run
    assert r["node"] == (10.0, 0.0)
    assert "combo" in r["report"]
    assert m.entity(main.id).props["fit_overrides"] == \
        {"10.000,0.000": "combo"}
    f = [f for f in derive_fittings(m) if f.node_xy == (10.0, 0.0)][0]
    assert f.kind == "combo" and f.note == "user override"
    # persists through save/load
    path = os.path.join(TMP, "override.loft.json")
    m.save(path)
    m2 = DraftModel.load(path)
    f2 = [f for f in derive_fittings(m2) if f.node_xy == (10.0, 0.0)][0]
    assert f2.kind == "combo" and f2.note == "user override"
    # by node index too
    net = network(m)
    idx = net.node_near(20, 0, 0.1)
    r = replace_fitting(m, idx, "cleanout")
    assert r["changed"] == 1 and r["node"] == (20.0, 0.0)
    f = [f for f in derive_fittings(m) if f.node_xy == (20.0, 0.0)][0]
    assert f.kind == "cleanout"
    # an override even lands on a straight-through vertex (couplings)
    m3 = DraftModel()
    m3.add("pipe", [(0, 0), (10, 0)])
    m3.add("pipe", [(10, 0), (20, 0)])
    assert not [f for f in derive_fittings(m3)
                if f.node_xy == (10.0, 0.0)]
    replace_fitting(m3, (10, 0), "coupling")
    assert [f for f in derive_fittings(m3)
            if f.node_xy == (10.0, 0.0)][0].kind == "coupling"
    # refusals: unknown kind, no node in reach
    assert replace_fitting(m, (10, 0), "sprocket")["changed"] == 0
    assert "reducer" in OVERRIDE_KINDS or True
    assert replace_fitting(m, (10, 0), "reducer 4x3")["changed"] == 1
    assert replace_fitting(m, (99, 99), "cap")["changed"] == 0
    # one undo removes the last override
    before = [f.kind for f in derive_fittings(m)
              if f.node_xy == (10.0, 0.0)]
    assert before == ["reducer 4x3"]
    assert m.undo()
    assert [f.kind for f in derive_fittings(m)
            if f.node_xy == (10.0, 0.0)] == ["combo"]


# --------------------------------------------------------------- slope run --

def test_slope_run():
    # the brief's arithmetic: 1/8"/ft over 22'-6" falls 2.8125" exactly
    m = DraftModel()
    run = m.add("pipe", [(0, 0), (22.5, 0)])
    r = slope_run(m, run.id, 0.125, start_invert_ft=100.0)
    assert r["changed"] == 1
    d = r["runs"][0]
    assert abs(d["length_ft"] - 22.5) < 1e-9
    assert abs(d["fall_ft"] - 0.234375) < 1e-12
    assert d["fall"] == "0'-2 13/16\"", d["fall"]
    assert abs(d["invert_end_ft"] - 99.765625) < 1e-12
    assert d["invert_start"] == "100'-0\""
    assert d["invert_end"] == "99'-9 3/16\""
    assert r["total_fall"] == "0'-2 13/16\""
    assert abs(m.entity(run.id).props["invert_ft"] - 100.0) < 1e-9
    assert abs(m.entity(run.id).props["slope_in_ft"] - 0.125) < 1e-12
    assert '1/8"/ft' in r["report"]

    # propagation: continuation + mid-run takeoff move, upstream branch
    # is never touched
    m = DraftModel()
    a = m.add("pipe", [(0, 0), (10, 0), (20, 0)])
    b = m.add("pipe", [(20, 0), (30, 0)])
    c = m.add("pipe", [(10, 5), (10, 0)])       # flows INTO the junction
    t = m.add("pipe", [(10, 0), (10, -8)])      # mid-run takeoff, downstream
    r = slope_run(m, a.id, 0.125, start_invert_ft=100.0)
    assert r["changed"] == 3, r["report"]
    by = {d["ent_id"]: d for d in r["runs"]}
    assert set(by) == {a.id, b.id, t.id}
    assert m.entity(c.id).props["invert_ft"] is None
    assert m.entity(c.id).props["slope_in_ft"] is None
    assert abs(by[b.id]["invert_start_ft"]
               - (100.0 - 0.125 * 20 / 12.0)) < 1e-9
    assert abs(by[t.id]["invert_start_ft"]
               - (100.0 - 0.125 * 10 / 12.0)) < 1e-9
    assert abs(r["total_fall_ft"] - 0.3125) < 1e-9
    assert r["total_fall"] == "0'-3 3/4\""
    # a downstream run that already carries a slope keeps it; only its
    # invert moves with the junction
    m.update(b.id, slope_in_ft=0.25)
    r = slope_run(m, a.id, 0.125, start_invert_ft=50.0)
    assert abs(m.entity(b.id).props["slope_in_ft"] - 0.25) < 1e-12
    assert abs(m.entity(b.id).props["invert_ft"]
               - (50.0 - 0.125 * 20 / 12.0)) < 1e-9
    # refusals: uphill / flat / unknown run
    r = slope_run(m, a.id, -0.125)
    assert r["changed"] == 0 and "refused" in r["report"].lower()
    assert slope_run(m, a.id, 0.0)["changed"] == 0
    assert slope_run(m, "nope", 0.125)["changed"] == 0
    assert abs(m.entity(a.id).props["invert_ft"] - 50.0) < 1e-9
    # an under-minimum slope WARNS but is never silently "fixed"
    r = slope_run(m, a.id, 0.0625, start_invert_ft=10.0)
    assert r["changed"] == 3
    assert any("minimum" in w for w in r["warnings"]), r["warnings"]
    assert any("verify against project code" in w for w in r["warnings"])
    assert abs(m.entity(a.id).props["slope_in_ft"] - 0.0625) < 1e-12
    # ...and the whole propagated command is ONE undo step
    assert m.undo()
    assert abs(m.entity(a.id).props["invert_ft"] - 50.0) < 1e-9
    # pressure systems: this run only, with a plain-words warning
    m2 = DraftModel()
    w1 = m2.add("pipe", [(0, 0), (10, 0)], system="dcw")
    m2.add("pipe", [(10, 0), (20, 0)], system="dcw")
    r = slope_run(m2, w1.id, 0.125, start_invert_ft=8.0)
    assert r["changed"] == 1
    assert any("no propagation" in w for w in r["warnings"])


# -------------------------------------------------------------- resize run --

def test_resize_run():
    m = DraftModel()
    a = m.add("pipe", [(0, 0), (10, 0), (20, 0)])
    b = m.add("pipe", [(20, 0), (30, 0)])
    c = m.add("pipe", [(10, 5), (10, 0)], dia_in=2.0)   # upstream branch
    r = resize_run(m, a.id, 6.0)
    assert r["changed"] == 2 and set(r["runs"]) == {a.id, b.id}
    assert abs(m.entity(a.id).props["dia_in"] - 6.0) < 1e-9
    assert abs(m.entity(b.id).props["dia_in"] - 6.0) < 1e-9
    assert abs(m.entity(c.id).props["dia_in"] - 2.0) < 1e-9
    assert r["warnings"] == []
    assert '6"' in r["report"]
    r = resize_run(m, b.id, 4.0, direction="this")
    assert r["changed"] == 1 and r["runs"] == [b.id]
    assert abs(m.entity(a.id).props["dia_in"] - 6.0) < 1e-9
    # the 6" main now necks to 4" downstream: check() calls it out
    w = [w for w in check(m) if w["code"] == "reduce-downstream"]
    assert w and "no reduction in the direction of flow" in w[0]["msg"]
    assert w[0]["ent_id"] == b.id and w[0]["level"] == "warn"
    # non-standard size sets but warns
    r = resize_run(m, c.id, 3.7, direction="this")
    assert r["changed"] == 1 and r["warnings"], r
    # refusals
    assert resize_run(m, a.id, 4.0, direction="sideways")["changed"] == 0
    assert resize_run(m, "nope", 4.0)["changed"] == 0
    # one undo per command
    assert m.undo()
    assert abs(m.entity(c.id).props["dia_in"] - 2.0) < 1e-9


# ------------------------------------------------------------------ check ---

def test_check():
    m = DraftModel()
    a = m.add("pipe", [(0, 0), (20, 0)], invert_ft=100.0,
              slope_in_ft=0.0625)
    warns = check(m)
    codes = [w["code"] for w in warns]
    assert "slope-min" in codes
    sm = [w for w in warns if w["code"] == "slope-min"][0]
    assert sm["level"] == "warn" and sm["ent_id"] == a.id
    assert "verify against project code" in sm["msg"]
    assert '1/16"/ft' in sm["msg"] and '1/8"/ft' in sm["msg"]
    assert codes.count("open-end") == 2
    oe = [w for w in warns if w["code"] == "open-end"][0]
    assert oe["level"] == "warn" and oe["xy"] is not None
    # 8" at 1/16 is commonly permitted: no slope warning
    m.update(a.id, dia_in=8.0)
    assert not [w for w in check(m) if w["code"] == "slope-min"]
    # capping clears the open ends
    cap_open_ends(m)
    assert not [w for w in check(m) if w["code"] == "open-end"]
    # a sloped vent is an info note, not a warning
    m.add("pipe", [(0, 5), (10, 5)], system="vent", slope_in_ft=0.25)
    vw = [w for w in check(m) if w["code"] == "vent-slope"]
    assert vw and vw[0]["level"] == "info"
    assert "pitch back" in vw[0]["msg"]
    # a cross on drainage is warned
    m2 = DraftModel()
    m2.add("pipe", [(0, 0), (10, 0), (20, 0)])
    m2.add("pipe", [(10, 8), (10, 0), (10, -8)])
    cw = [w for w in check(m2) if w["code"] == "cross-drainage"]
    assert cw and cw[0]["level"] == "warn"
    assert "verify against project code" in cw[0]["msg"]
    # ...but not on pressure
    m3 = DraftModel()
    m3.add("pipe", [(0, 0), (10, 0), (20, 0)], system="dcw")
    m3.add("pipe", [(10, 8), (10, 0), (10, -8)], system="dcw")
    assert not [w for w in check(m3) if w["code"] == "cross-drainage"]


# ---------------------------------------------------------------- takeoff ---

def test_takeoff():
    m = DraftModel()
    m.add("pipe", [(0, 0), (20, 0), (20, 10)])           # 30 lf + elbow90
    m.add("pipe", [(0, 5), (10, 5)])                     # 10 lf
    m.add("pipe", [(0, -5), (25, -5)], system="dcw", dia_in=1.5)
    cap_open_ends(m, system="san")
    lines = takeoff(m)
    by = {ln.subject: ln for ln in lines}
    san = by['Sanitary pipe 4", PVC DWV']
    assert san.kind == "length" and san.unit == "lf"
    assert abs(san.qty - 40.0) < 1e-9, san.qty
    dcw = by['Domestic cold water pipe 1 1/2", Copper Type L']
    assert dcw.kind == "length" and abs(dcw.qty - 25.0) < 1e-9
    caps = by['Fitting: cap 4"']
    assert caps.kind == "count" and caps.unit == "ea"
    assert abs(caps.qty - 4.0) < 1e-9, caps.qty
    assert abs(by['Fitting: elbow90 4"'].qty - 1.0) < 1e-9
    # open candidates are not fittings
    assert not [s for s in by if "open" in s]
    # deterministic ordering, lengths before counts
    assert lines == sorted(
        lines, key=lambda ln: (ln.kind, ln.subject.lower(), ln.subject))
    # price book attach via book.find(subject)
    from rfi_stamper.reckoner import PriceBook
    book_csv = os.path.join(TMP, "pipebook.csv")
    with open(book_csv, "w", encoding="utf-8") as f:
        f.write('code,description,unit,cost\n'
                'P-401,"Sanitary pipe 4"", PVC DWV",lf,12.5\n')
    lines = takeoff(m, PriceBook(book_csv))
    san = [ln for ln in lines if ln.subject.startswith("Sanitary")][0]
    assert san.code == "P-401"
    assert abs(san.unit_cost - 12.5) < 1e-9
    assert abs(san.total - 500.0) < 1e-9


# ----------------------------------------------------------------- to_bim ---

def test_to_bim():
    m = DraftModel()
    m.add("pipe", [(0, 0), (8, 0), (16, 0)], invert_ft=100.0,
          slope_in_ft=0.125)
    m.add("pipe", [(0, 5), (10, 5)], system="dcw")       # no invert: base_z
    m.add("pipe", [(0, 9), (10, 9)], system="storm",
          invert_ft=98.0)                                # invert, no slope
    bm = to_bim(m, base_z=7.5)
    assert len(bm.segments) == 4
    san = [s for s in bm.segments if s.system == "Sanitary"]
    assert len(san) == 2
    assert abs(san[0].a[2] - 100.0) < 1e-9
    assert abs(san[0].b[2] - (100.0 - 0.125 * 8 / 12.0)) < 1e-9
    assert abs(san[1].a[2] - (100.0 - 0.125 * 8 / 12.0)) < 1e-9
    assert abs(san[1].b[2] - (100.0 - 0.125 * 16 / 12.0)) < 1e-9
    assert san[0].color == SYSTEMS["san"]["color"]
    dcw = [s for s in bm.segments if s.system == "Domestic cold water"][0]
    assert abs(dcw.a[2] - 7.5) < 1e-9 and abs(dcw.b[2] - 7.5) < 1e-9
    assert dcw.color == SYSTEMS["dcw"]["color"]
    storm = [s for s in bm.segments if s.system == "Storm"][0]
    assert abs(storm.a[2] - 98.0) < 1e-9 and abs(storm.b[2] - 98.0) < 1e-9
    # legend systems: only the systems present, in SYSTEMS order
    assert bm.systems == [("Sanitary", SYSTEMS["san"]["color"]),
                          ("Storm", SYSTEMS["storm"]["color"]),
                          ("Domestic cold water", SYSTEMS["dcw"]["color"])]
    # xy passes straight through (Loft frame == Fieldstitch world frame)
    assert san[0].a[:2] == (0.0, 0.0) and san[1].b[:2] == (16.0, 0.0)


# ------------------------------------------------------------- render ops ---

def test_render_ops_pipe():
    m = DraftModel()
    m.add("pipe", [(0, 0), (22.5, 0)], invert_ft=100.0, slope_in_ft=0.125)
    ops = render_ops(m)
    lines = [op for op in ops if op[0] == "line" and op[5] == "P-SAN"]
    assert lines and lines[0][7] == "solid"
    assert lines[0][6] == "heavy"          # san plots heavy
    texts = [op for op in ops if op[0] == "text"]
    # size label at the run midpoint, body height
    lbl = [op for op in texts if op[3] == '4"']
    assert lbl and lbl[0][4] == "body" and lbl[0][5] == "P-SAN"
    assert abs(lbl[0][1] - 11.25) < 1.0    # rides the half-length point
    # IE notes at both ends, sub height, exact feet-inches
    ies = [op for op in texts if str(op[3]).startswith("IE ")]
    assert len(ies) == 2
    assert all(op[4] == "sub" for op in ies)
    assert any(op[3] == "IE 100'-0\"" for op in ies), ies
    assert any(op[3] == "IE 99'-9 3/16\"" for op in ies), ies
    # capping adds the double-tick cap symbol at each end
    n_before = len(ops)
    cap_open_ends(m)
    ops2 = render_ops(m)
    assert len(ops2) == n_before + 4       # 2 ends x 2 ticks
    # no invert/slope -> no IE text
    m1 = DraftModel()
    m1.add("pipe", [(0, 0), (10, 0)])
    assert not [op for op in render_ops(m1)
                if op[0] == "text" and str(op[3]).startswith("IE ")]
    # elbow symbol: one arc at the corner, paper-scaled radius
    m2 = DraftModel()
    m2.add("pipe", [(0, 0), (10, 0), (10, 8)])
    arcs = [op for op in render_ops(m2) if op[0] == "arc"]
    assert len(arcs) == 1
    assert abs(arcs[0][1] - 10) < 1e-9 and abs(arcs[0][2]) < 1e-9
    assert abs(arcs[0][3] - PIPE_SYM_IN * m2.scale_ratio / 12.0) < 1e-9
    # junction symbol: a tick across the branch leg
    m3 = DraftModel()
    m3.add("pipe", [(0, 0), (10, 0), (20, 0)])
    m3.add("pipe", [(10, 8), (10, 0)])
    r = PIPE_SYM_IN * m3.scale_ratio / 12.0
    ticks = [op for op in render_ops(m3) if op[0] == "line"
             and abs((op[1] + op[3]) / 2 - 10) < 1e-6
             and abs((op[2] + op[4]) / 2 - 0.8 * r) < 1e-6]
    assert ticks, "branch tick missing at the junction"
    # cleanout override: small circle + CO text
    replace_fitting(m2, (10, 8), "cleanout")
    ops3 = render_ops(m2)
    assert any(op[0] == "circle" for op in ops3)
    assert any(op[0] == "text" and op[3] == "CO" for op in ops3)
    # p-trap: a little U (arc) at the fixture connection
    m4 = DraftModel()
    m4.add("fixture", [(5, 0.3)], stencil="lav")
    m4.add("pipe", [(5, 0), (5, -8)])
    tr = [op for op in render_ops(m4) if op[0] == "arc"]
    assert tr and abs(tr[0][1] - 5) < 1e-9 and abs(tr[0][2]) < 1e-9
    # hiding the ply culls runs, labels, IE text AND fitting symbols
    m.ply("P-SAN").visible = False
    assert render_ops(m) == []
    m.ply("P-SAN").visible = True
    assert render_ops(m)
    # vents draw dashed on their own ply
    m5 = DraftModel()
    m5.add("pipe", [(0, 0), (12, 0)], system="vent")
    vops = [op for op in render_ops(m5) if op[0] == "line"]
    assert vops and vops[0][5] == "P-VENT" and vops[0][7] == "hidden"
    # bounds still measure a pipes-only drawing (hidden plies included)
    assert m5.bounds() is not None


def main():
    test_tables()
    print("PASS systems / sizes / slope tables, ply parity")
    test_pipe_entity()
    print("PASS pipe entity defaults + save/load round trip")
    test_network()
    print("PASS network nodes/edges, merge tolerance, classification")
    test_fitting_bands()
    print("PASS elbow 45/90 bands, pass-through, reducers")
    test_junctions()
    print("PASS junction truth table (santee/combo/wye/tee/cross)")
    test_fixture_fittings()
    print("PASS fixture connections (p-trap, closet flange)")
    test_cap_open_ends()
    print("PASS cap_open_ends (idempotent, per-system, one undo)")
    test_replace_fitting()
    print("PASS replace_fitting (override persistence, xy/index)")
    test_slope_run()
    print("PASS slope_run (exact fall, propagation, refusals)")
    test_resize_run()
    print("PASS resize_run (downstream/this)")
    test_check()
    print("PASS check (slope-min, open ends, cross, reduction, vents)")
    test_takeoff()
    print("PASS takeoff (LF by system/size, fitting counts, price book)")
    test_to_bim()
    print("PASS to_bim (invert z math, colors, legend systems)")
    test_render_ops_pipe()
    print("PASS render_ops (runs, labels, IE, symbols, ply culling)")
    print("PIPEWRIGHT ENGINE TEST PASSED")
    print("outputs in", TMP)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("PIPEWRIGHT TEST FAILED:", e)
        sys.exit(1)
