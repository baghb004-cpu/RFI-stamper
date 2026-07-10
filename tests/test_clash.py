"""Headless tests for rfi_stamper.clash (Clash-Lite) + its Backcheck rules.

Pins the geometry kernel (seg_seg, sd_box, ternary_min), the scene
sources (capsules share pipewright.run_z with the viewer; no-invert runs
honestly excluded), the classification taxonomy (hard / penetration /
concealed / wontfit / duplicate), the false-positive discipline (zero on
a clean model; joined runs never clash at their fitting; ignore-below),
clustering/severity/pins, and the Backcheck wiring incl. the STD-SLEEVE
graduation and the honest no-elevation skip note.

Run:  python3 tests/test_clash.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper import backcheck as bc     # noqa: E402
from rfi_stamper import clash, draft, pipewright  # noqa: E402


# ------------------------------------------------------------------ kernel ---

def test_seg_seg():
    # perpendicular crossing, 4 ft apart in z
    d, s, t, c1, c2 = clash.seg_seg((0, 0, 0), (10, 0, 0),
                                    (5, -3, 4), (5, 3, 4))
    assert abs(d - 4.0) < 1e-12 and abs(s - 0.5) < 1e-12 \
        and abs(t - 0.5) < 1e-12
    assert c1 == (5.0, 0.0, 0.0) and c2 == (5.0, 0.0, 4.0)
    # endpoints clamp (skew, closest at segment ends)
    d, s, t, _c1, _c2 = clash.seg_seg((0, 0, 0), (1, 0, 0),
                                      (5, 0, 3), (6, 0, 3))
    assert abs(d - 5.0) < 1e-12 and s == 1.0 and t == 0.0
    # parallel: continuum of closest pairs -> deterministic s = 0 branch
    d1 = clash.seg_seg((0, 0, 0), (10, 0, 0), (0, 2, 0), (10, 2, 0))
    d2 = clash.seg_seg((0, 0, 0), (10, 0, 0), (0, 2, 0), (10, 2, 0))
    assert d1 == d2 and abs(d1[0] - 2.0) < 1e-12 and d1[1] == 0.0
    # point-degenerate segments
    d, *_ = clash.seg_seg((3, 4, 0), (3, 4, 0), (0, 0, 0), (0, 0, 0))
    assert abs(d - 5.0) < 1e-12


def test_sd_box():
    lo, hi = (0.0, -4.0, 0.0), (10.0, 4.0, 10.0)
    assert abs(clash.sd_box((13.0, 0.0, 5.0), lo, hi) - 3.0) < 1e-12
    # corner: Euclidean, not Chebyshev
    assert abs(clash.sd_box((13.0, 8.0, 5.0), lo, hi) - 5.0) < 1e-12
    # inside: negative depth to the nearest face
    assert abs(clash.sd_box((5.0, 0.0, 5.0), lo, hi) + 4.0) < 1e-12
    assert abs(clash.sd_box((1.0, 0.0, 5.0), lo, hi) + 1.0) < 1e-12
    # on a face: zero
    assert abs(clash.sd_box((10.0, 0.0, 5.0), lo, hi)) < 1e-12


def test_ternary_min():
    t, v = clash.ternary_min(lambda x: (x - 0.3) ** 2 + 1.0)
    assert abs(t - 0.3) < 1e-6 and abs(v - 1.0) < 1e-12
    # boundary minimum (monotone function)
    t, v = clash.ternary_min(lambda x: x + 2.0)
    assert t < 1e-6 and abs(v - 2.0) < 1e-6
    # deterministic: same input, same bracket walk, same answer
    assert clash.ternary_min(lambda x: abs(x - 0.7)) == \
        clash.ternary_min(lambda x: abs(x - 0.7))


# ------------------------------------------------------------ scene sources ---

def test_capsules_share_run_z():
    m = draft.DraftModel()
    m.add("pipe", [(0.0, 0.0), (20.0, 0.0), (20.0, 10.0)], system="san",
          dia_in=4, invert_ft=8.0, slope_in_ft=0.25)
    caps, stats = clash.capsules(m)
    assert len(caps) == 2 and stats["no_elevation"] == 0
    e = m.ents[0]
    zs = pipewright.run_z(e)
    r = 4.0 / 24.0
    # capsule axis z = the SAME invert profile the viewer draws, + r
    assert caps[0].a[2] == zs[0] + r and caps[0].b[2] == zs[1] + r
    assert caps[1].a[2] == zs[1] + r and caps[1].b[2] == zs[2] + r
    # and to_bim's segments sit exactly at run_z (shared source, no drift)
    bm = pipewright.to_bim(m)
    assert bm.segments[0].a[2] == zs[0] and bm.segments[1].b[2] == zs[2]
    # terminal flags: first vertex of seg 0, last of seg 1
    assert caps[0].end_a and not caps[0].end_b
    assert caps[1].end_b and not caps[1].end_a

    # runs with no invert are EXCLUDED and counted, never guessed at z=0
    m.add("pipe", [(0.0, 5.0), (10.0, 5.0)], system="dcw", dia_in=1.5)
    caps2, stats2 = clash.capsules(m)
    assert len(caps2) == 2 and stats2["no_elevation"] == 1


def test_wall_boxes():
    m = draft.DraftModel()
    m.add("wall", [(0.0, 0.0), (30.0, 0.0)], wtype="stud4")
    wbs = clash.wall_boxes(m, wall_height=9.0)
    assert len(wbs) == 1
    wb = wbs[0]
    assert wb.length == 30.0 and wb.height == 9.0
    assert abs(wb.half - float(m.ents[0].props["thick_in"]) / 24.0) < 1e-12
    # local frame: along the centerline, perp signed, z through
    assert wb.to_local((10.0, 0.0, 3.0)) == (10.0, 0.0, 3.0)
    la = wb.to_local((5.0, 2.0, 1.0))
    assert abs(la[0] - 5.0) < 1e-12 and abs(la[1] - 2.0) < 1e-12


# ---------------------------------------------------------------- detection ---

def _pipe(m, pts, **props):
    props.setdefault("system", "san")
    props.setdefault("dia_in", 4)
    props.setdefault("invert_ft", 3.0)
    return m.add("pipe", list(pts), **props)


def test_hard_clash_and_escalation():
    m = draft.DraftModel()
    _pipe(m, [(0, 5), (20, 5)])
    _pipe(m, [(10, 0), (10, 10)], invert_ft=3.1)
    hits, _stats = clash.detect(m)
    assert [h.kind for h in hits] == ["hard"], hits
    # analytic overlap: r1 + r2 - |dz| = 1/6 + 1/6 - 0.1 ft = 2.8"
    assert abs(hits[0].overlap_ft * 12.0 - 2.8) < 1e-9, hits[0].overlap_ft
    # witness sits at the crossing, between the two centerlines
    x, y, z = hits[0].at
    assert abs(x - 10.0) < 1e-9 and abs(y - 5.0) < 1e-9
    assert 3.0 < z < 3.4
    g = clash.group(hits)
    assert len(g) == 1 and g[0].count == 1
    # 2.8" >= half the smaller diameter (2") -> gross bury -> blocker
    assert clash.severity(g[0]) == "blocker"

    # a shallower graze stays major: overlap just over the threshold
    m2 = draft.DraftModel()
    _pipe(m2, [(0, 5), (20, 5)])
    _pipe(m2, [(10, 0), (10, 10)], invert_ft=3.0 + (4.0 - 0.6) / 12.0)
    hits2, _ = clash.detect(m2)
    assert len(hits2) == 1
    assert abs(hits2[0].overlap_ft * 12.0 - 0.6) < 1e-9
    assert clash.severity(clash.group(hits2)[0]) == "major"


def test_ignore_below():
    # overlap 0.3" < HARD_IGNORE_IN 0.5" -> noise, not a finding
    m = draft.DraftModel()
    _pipe(m, [(0, 5), (20, 5)])
    _pipe(m, [(10, 0), (10, 10)], invert_ft=3.0 + (4.0 - 0.3) / 12.0)
    hits, _ = clash.detect(m)
    assert hits == [], hits


def test_clean_model_zero():
    # the contract: parallel mains a foot apart, joined runs, and a wall
    # nowhere near them -> ZERO findings
    m = draft.DraftModel()
    m.add("wall", [(0.0, 20.0), (30.0, 20.0)], wtype="stud4")
    _pipe(m, [(0, 0), (20, 0)])
    _pipe(m, [(0, 1), (20, 1)], system="sv", dia_in=2, invert_ft=3.0)
    # a tee: joined at (20, 0) -> adjacency exclusion, no fitting clash
    _pipe(m, [(20, 0), (20, -8)], dia_in=3)
    hits, _ = clash.detect(m)
    assert hits == [], [(h.kind, h.ent_a, h.ent_b) for h in hits]


def test_wall_classification():
    stud_thick = None
    # transverse crossing -> penetration (sleeve), never a hard clash
    m = draft.DraftModel()
    m.add("wall", [(0.0, 0.0), (30.0, 0.0)], wtype="stud4")
    stud_thick = float(m.ents[0].props["thick_in"])
    _pipe(m, [(15, -6), (15, 6)], dia_in=2)
    hits, _ = clash.detect(m)
    kinds = sorted(h.kind for h in hits)
    assert kinds == ["penetration"], kinds

    # run concealed lengthwise inside the wall: fits -> concealed info
    m2 = draft.DraftModel()
    m2.add("wall", [(0.0, 0.0), (30.0, 0.0)], wtype="stud4")
    _pipe(m2, [(5, 0), (25, 0)], dia_in=2)
    hits2, _ = clash.detect(m2)
    assert [h.kind for h in hits2] == ["concealed"], hits2

    # same run, diameter >= wall thickness -> wontfit (major)
    m3 = draft.DraftModel()
    m3.add("wall", [(0.0, 0.0), (30.0, 0.0)], wtype="stud4")
    _pipe(m3, [(5, 0), (25, 0)], dia_in=stud_thick + 1.0)
    hits3, _ = clash.detect(m3)
    assert [h.kind for h in hits3] == ["wontfit"]
    g3 = clash.group(hits3)
    assert clash.severity(g3[0]) == "major"

    # a degree-1 stub ENDING inside the wall (fixture rough-in) demotes
    # to the penetration bucket — normal, not a conflict
    m4 = draft.DraftModel()
    m4.add("wall", [(0.0, 0.0), (30.0, 0.0)], wtype="stud4")
    _pipe(m4, [(15, -6), (15, 0.05)], dia_in=2)
    hits4, _ = clash.detect(m4)
    assert [h.kind for h in hits4] == ["penetration"], hits4


def test_duplicate_subsumption():
    # same system, near-coaxial (past MERGE_TOL, inside one radius),
    # overlapping extents -> ONE duplicate info, hard hit suppressed
    m = draft.DraftModel()
    _pipe(m, [(0, 0), (20, 0)])
    _pipe(m, [(0, 0.1), (20, 0.1)])
    hits, _ = clash.detect(m)
    assert [h.kind for h in hits] == ["duplicate"]
    # different systems at the same offset are a REAL hard clash
    m2 = draft.DraftModel()
    _pipe(m2, [(0, 0), (20, 0)])
    _pipe(m2, [(0, 0.1), (20, 0.1)], system="sv")
    hits2, _ = clash.detect(m2)
    assert [h.kind for h in hits2] == ["hard"]


def test_grouping_and_pins():
    # two snaking runs clash segment-by-segment: ONE issue, count > 1
    # (offsets kept past MERGE_TOL_FT so the ends never node-merge into
    # the adjacency exclusion)
    m = draft.DraftModel()
    _pipe(m, [(0, 0), (10, 0), (10, 0.1), (20, 0.1)], system="san")
    _pipe(m, [(0, 0.28), (10, 0.28), (10, -0.1), (20, -0.1)], system="sv")
    hits, _ = clash.detect(m)
    assert len(hits) >= 2
    groups = clash.group(hits)
    pairs = {(g.ent_a, g.ent_b, g.kind) for g in groups}
    assert len(groups) == len(pairs)            # one group per pair+kind
    hard = [g for g in groups if g.kind == "hard"]
    assert len(hard) == 1 and hard[0].count == len(
        [h for h in hits if h.kind == "hard"])
    # worst overlap is the group's overlap
    assert hard[0].overlap_ft == max(
        h.overlap_ft for h in hits if h.kind == "hard")
    ps = clash.pins(groups)
    assert len(ps) == len(groups)
    assert ps[0][3] == "C1" and ps[0][4] in bc.SEVERITY_COLORS.values()
    # deterministic: rerun gives the identical report
    hits_b, _ = clash.detect(m)
    assert [(h.kind, h.ent_a, h.ent_b, h.overlap_ft) for h in hits] == \
        [(h.kind, h.ent_a, h.ent_b, h.overlap_ft) for h in hits_b]


# ----------------------------------------------------------- backcheck lane ---

def test_backcheck_rules():
    m = draft.DraftModel(title="T", number="X-1")
    m.add("wall", [(0.0, 0.0), (30.0, 0.0)], wtype="stud4")
    m.add("grid", [(10.0, -20.0), (10.0, 20.0)], label="B")
    m.add("grid", [(-5.0, 5.0), (35.0, 5.0)], label="2")
    _pipe(m, [(5, -8), (5, 8)], dia_in=2)               # penetration
    _pipe(m, [(0, 5), (20, 5)])                          # crossing pair ->
    _pipe(m, [(10, 1), (10, 9)], system="sv", dia_in=3,  # hard clash
          invert_ft=3.05)
    r = bc.check_loft(m)
    codes = {f.code for f in r.findings}
    assert "GEO-CLASH-HARD" in codes and "STD-SLEEVE" in codes, codes
    hard = [f for f in r.findings if f.code == "GEO-CLASH-HARD"]
    # detail carries systems, trade-fraction overlap, location, grid ref
    assert any("hard clash" in f.detail and "near grid B/2" in f.detail
               and 'overlap' in f.detail for f in hard), \
        [f.detail for f in hard]
    assert all(f.where is not None and len(f.where) == 2 for f in hard)
    sleeve = [f for f in r.findings if f.code == "STD-SLEEVE"]
    assert sleeve and "sleeve" in sleeve[0].suggestion.lower()
    # the clash lane really RAN
    for code in ("GEO-CLASH-HARD", "GEO-CLASH-DUP", "GEO-PIPE-IN-WALL",
                 "STD-SLEEVE"):
        assert code in r.stats["checked"], code
    # ... and STD-SLEEVE is no longer in the always-skipped registry for
    # loft sources (it remains skipped for PDF sources, honestly)
    assert "STD-SLEEVE" not in {s["code"] for s in r.stats["skipped"]}
    assert bc.SKIPPED_RULES["STD-SLEEVE"]["inputs"] == {"pdf"}


def test_backcheck_no_elevation_skip():
    # pipes with NO inverts: the clash rules register an honest skip note
    # instead of guessing at z = 0
    m = draft.DraftModel(title="T", number="X-1")
    m.add("wall", [(0.0, 0.0), (30.0, 0.0)], wtype="stud4")
    m.add("pipe", [(5.0, -8.0), (5.0, 8.0)], system="dcw", dia_in=1.5)
    r = bc.check_loft(m)
    skip = {s["code"]: s["reason"] for s in r.stats["skipped"]}
    assert "GEO-CLASH-HARD" in skip and "STD-SLEEVE" in skip, skip
    assert "invert" in skip["GEO-CLASH-HARD"], skip["GEO-CLASH-HARD"]
    assert not any(f.code.startswith("GEO-CLASH") for f in r.findings)


def test_rule_registry():
    for code in ("GEO-CLASH-HARD", "GEO-CLASH-CLEAR", "GEO-CLASH-DUP",
                 "GEO-PIPE-IN-WALL", "STD-SLEEVE"):
        assert code in bc.RULES, code
        assert bc.RULES[code]["inputs"] == {"pipe"}, code
        assert "verify" in bc.RULES[code]["rule"].lower() \
            or "verify" in bc.RULES[code]["title"].lower(), code
    assert bc.RULES["STD-SLEEVE"]["category"] == "standards"
    # thresholds carry their basis in the module, MIN_SLOPE-style
    assert clash.HARD_IGNORE_IN == 0.5
    assert clash.CLEARANCE_IN == 0.0            # off: no silent guess


def test_clearance_knob():
    # the opt-in soft-clash buffer: off by default, honest when on
    m = draft.DraftModel()
    _pipe(m, [(0, 0), (20, 0)])
    # gap: centers 0.5 ft apart, radii 1/6+1/6 -> clear gap = 2"
    _pipe(m, [(0, 0.5), (20, 0.5)], system="sv")
    assert clash.detect(m)[0] == []             # default: off
    hits, _ = clash.detect(m, clearance_in=3.0)
    assert [h.kind for h in hits] == ["clearance"]
    assert hits[0].overlap_ft < 0               # signed: negative = gap
    g = clash.group(hits)
    assert clash.severity(g[0]) == "minor"


def test_perf_tripwire():
    import time
    m = draft.DraftModel()
    for i in range(40):
        _pipe(m, [(0, i * 0.9), (60.0, i * 0.9)],
              invert_ft=3.0 + (i % 3) * 0.02)
    for i in range(10):
        m.add("wall", [(i * 6.0, -5.0), (i * 6.0 + 5.0, -5.0)],
              wtype="stud4")
    t0 = time.perf_counter()
    hits, _stats = clash.detect(m)
    dt = time.perf_counter() - t0
    assert dt < 2.0, f"{dt:.2f}s"
    assert clash.group(hits) is not None


# ------------------------------------------------------------------ runner ---

def main():
    test_seg_seg()
    print("PASS seg_seg (crossing, clamp, parallel-deterministic, points)")
    test_sd_box()
    print("PASS sd_box signed distance (outside, corner, inside, face)")
    test_ternary_min()
    print("PASS ternary_min (convex, boundary, deterministic)")
    test_capsules_share_run_z()
    print("PASS capsules share pipewright.run_z (+r lift, no-invert count)")
    test_wall_boxes()
    print("PASS wall boxes (frame, local transform)")
    test_hard_clash_and_escalation()
    print("PASS hard clash: analytic overlap, blocker escalation")
    test_ignore_below()
    print("PASS ignore-below threshold (0.3\" graze is noise)")
    test_clean_model_zero()
    print("PASS clean model -> ZERO findings (adjacency exclusion)")
    test_wall_classification()
    print("PASS wall taxonomy: penetration/concealed/wontfit/stub demote")
    test_duplicate_subsumption()
    print("PASS duplicate subsumption (and cross-system stays hard)")
    test_grouping_and_pins()
    print("PASS clustering (one issue per pair), pins, determinism")
    test_backcheck_rules()
    print("PASS Backcheck lane: findings, grid ref, STD-SLEEVE graduated")
    test_backcheck_no_elevation_skip()
    print("PASS honest no-elevation skip note")
    test_rule_registry()
    print("PASS rule registry (codes, inputs, verify, threshold bases)")
    test_clearance_knob()
    print("PASS clearance knob (off by default, signed gap when on)")
    test_perf_tripwire()
    print("PASS perf tripwire")
    print("CLASH TESTS PASSED")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("CLASH TEST FAILED:", e)
        sys.exit(1)
