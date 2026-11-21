"""The Sightline: visibility gate regression (owner brief section 9).

Cases 1, 2, 3, 12 and 15 at the gate level, plus the gate's own rules
(split strokes, dashed lines, the 300 dpi small pass, honest unreadables,
the registration guard, the reverse check and the drop-in).  The reader
level halves of cases 2 and 3 (the slab outline, the open item) live in
test_batterboard_read.py.

Every fixture is synthetic.  Run:  python tests/test_sightline.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz                                                   # noqa: E402

from rfi_stamper import geom2d as g2                          # noqa: E402
from rfi_stamper import sightline as sl                       # noqa: E402
from rfi_stamper.batterboard.sampleset import Sheet           # noqa: E402

GRAY = 0.75
GRAY8 = (191, 191, 191)


def near(a, b, tol):
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= tol


def same_poly(got, want, tol):
    """Same vertex set (any start, either winding)."""
    return (len(got) == len(want)
            and all(any(near(g, w, tol) for w in want) for g in got)
            and all(any(near(g, w, tol) for g in got) for w in want))


def fills_of(res, rgb=GRAY8):
    return [f for f in res.fills if f.rgb == rgb]


# ------------------------------------------------------------------ case 1 --

BAND = [(300.0, 150.0), (309.0, 150.0), (399.0, 258.0), (390.0, 258.0)]   # thin angled wall
BLOCK = (290.0, 150.0, 410.0, 258.0)                                       # the 7 ft x 12 ft block


def sheet_case1():
    s = Sheet()
    s.clip_begin(BAND)
    s.fill_rect(*BLOCK, color=GRAY)
    s.clip_end()
    s.line((60, 500), (700, 500), width=1.0)          # ordinary linework
    return s


def check_band(res, band, where):
    fs = fills_of(res)
    assert len(fs) == 1, (where, [(f.status, f.rgb) for f in res.fills])
    f = fs[0]
    assert f.status == sl.CLIPPED, (where, f.status, f.why)
    assert len(f.polys) == 1 and len(f.polys[0]) == 4, (where, f.polys)
    assert same_poly(f.polys[0], band, 0.05), (where, f.polys[0], band)
    block_area = (BLOCK[2] - BLOCK[0]) * (BLOCK[3] - BLOCK[1])
    band_area = abs(g2.area(band))
    assert abs(f.area_vector - block_area) < 0.5, (where, f.area_vector)
    assert abs(f.area_visible - band_area) < 0.02 * band_area + 0.5, (where, f.area_visible)
    assert f.area_visible < 0.15 * f.area_vector          # never the bounding block
    bx0, by0, bx1, by1 = g2.bbox(f.polys[0])
    assert abs(g2.area(f.polys[0])) < 0.2 * (bx1 - bx0) * (by1 - by0), "a polygon, not its box"
    rec = res.record.clipped
    assert len(rec) == 1 and rec[0]["what"] == "fill", rec
    assert rec[0]["area_vector"] > rec[0]["area_visible"] > 0
    assert not res.record.dropped, res.record.dropped


def case1():
    doc, page = sheet_case1().build()
    res = sl.gate(page)
    assert res.registered
    check_band(res, BAND, "rot0")
    assert fills_of(res)[0].method == "vector"
    # non-convex clip: no exact vector clip exists, the visible part is traced
    chev = [(300, 150), (345, 190), (390, 150), (390, 170), (345, 210), (300, 170)]
    s = Sheet()
    s.clip_begin(chev)
    s.fill_rect(280, 140, 410, 230, color=GRAY)
    s.clip_end()
    doc2, page2 = s.build()
    res2 = sl.gate(page2)
    f = fills_of(res2)[0]
    assert f.status == sl.CLIPPED and f.method == "traced", (f.status, f.method)
    assert len(f.polys) == 1 and len(f.polys[0]) == 6, f.polys
    assert same_poly(f.polys[0], chev, 0.75), f.polys[0]          # within about 1.5 px
    assert abs(f.area_visible - abs(g2.area(chev))) < 0.05 * abs(g2.area(chev))
    # a fill whose clip leaves nothing is dropped and recorded
    s = Sheet()
    s.clip_begin([(10, 10), (40, 10), (40, 40), (10, 40)])
    s.fill_rect(300, 300, 360, 340, color=GRAY)
    s.clip_end()
    _d, page3 = s.build()
    res3 = sl.gate(page3)
    assert not fills_of(res3) and len(res3.record.dropped) == 1
    print("PASS case 1: clipped fill returns the band (exact), traced when the "
          "clip is not convex, both areas recorded")
    return doc, doc2


# ------------------------------------------------------------------ case 2 --

HID_A = ((120.0, 200.0), (300.0, 200.0))        # superseded heavy lines, fully masked
HID_B = ((300.0, 200.0), (300.0, 320.0))
LIVE = ((120.0, 380.0), (520.0, 380.0))         # a live heavy line
PART = ((420.0, 120.0), (420.0, 320.0))         # masked over its lower 35%
MOST = ((480.0, 120.0), (480.0, 320.0))         # masked over 10%: stays whole


def sheet_case2():
    s = Sheet()
    for a, b in (HID_A, HID_B, LIVE, PART, MOST):
        s.line(a, b, width=1.5)
    s.fill_rect(100, 180, 320, 340, color=1.0)                   # the later white fill
    s.fill_rect(400, 250, 440, 330, color=1.0)                   # masks PART from y=250
    s.fill_rect(470, 300, 490, 330, color=1.0)                   # masks MOST from y=300
    s.cloud([(330, 180), (390, 180), (390, 340), (330, 340)])    # revision cloud beside
    return s


def seg_match(st, a, b, tol=0.6):
    p, q = st.pts[0], st.pts[-1]
    return (near(p, a, tol) and near(q, b, tol)) or (near(p, b, tol) and near(q, a, tol))


def check_case2(res, xf=lambda p: p, where="rot0"):
    heavy = [st for st in res.strokes if abs(st.width - 1.5) < 1e-6]
    hidden = res.hidden_strokes
    ha, hb = [xf(p) for p in HID_A], [xf(p) for p in HID_B]
    assert len(hidden) == 2, (where, [(h.pts, h.score) for h in hidden])
    assert any(seg_match(h, *ha) for h in hidden) and any(seg_match(h, *hb) for h in hidden)
    assert all(h.score < sl.STROKE_HIDDEN for h in hidden)
    drops = [d for d in res.record.dropped if d["what"] == "stroke"]
    assert len(drops) == 2, (where, drops)
    assert not any(seg_match(st, *ha) or seg_match(st, *hb) for st in heavy), where
    live = [xf(p) for p in LIVE]
    assert any(seg_match(st, *live) and st.status == sl.VISIBLE for st in heavy), where
    # the partly masked line is split at the mask edge (65% visible)
    pa, pb = xf(PART[0]), xf((420.0, 250.0))
    parts = [st for st in heavy if st.status == sl.SPLIT]
    assert len(parts) == 1 and seg_match(parts[0], pa, pb, 1.6), (where, [p.pts for p in parts])
    assert 0.5 <= parts[0].score < 0.8, parts[0].score
    # 90% visible: kept whole
    ma, mb = [xf(p) for p in MOST]
    assert any(seg_match(st, ma, mb) and st.status == sl.VISIBLE for st in heavy), where
    assert any(c["what"] == "stroke" for c in res.record.clipped), where
    # the cloud is linework like any other: visible, never "dropped"
    cloud = [st for st in res.strokes if st.rgb == (204, 0, 0)]
    assert cloud and all(st.status == sl.VISIBLE for st in cloud), where


def case2():
    doc, page = sheet_case2().build()
    res = sl.gate(page)
    check_case2(res)
    print("PASS case 2 (gate): superseded heavy lines under a white fill are "
          "dropped and recorded; partly masked line split; 90% line whole")
    return doc


# ------------------------------------------------------------------ case 3 --

def case3():
    # a slab edge run whose heavy line is gone and only a light line remains
    s = Sheet()
    s.line((100, 200), (300, 200), width=1.5)             # heavy, still plotted
    s.line((300, 200), (500, 200), width=1.5)             # heavy, in the file...
    s.fill_rect(300, 190, 500, 210, color=1.0)            # ...but masked
    s.line((300, 200), (500, 200), width=0.25)            # only a light line remains
    doc, page = s.build()
    res = sl.gate(page)
    heavy_vis = [st for st in res.strokes if abs(st.width - 1.5) < 1e-6]
    assert len(heavy_vis) == 1 and seg_match(heavy_vis[0], (100, 200), (300, 200)), \
        [(st.pts, st.status) for st in heavy_vis]
    assert len(res.hidden_strokes) == 1 and abs(res.hidden_strokes[0].width - 1.5) < 1e-6
    assert seg_match(res.hidden_strokes[0], (300, 200), (500, 200))
    light = [st for st in res.strokes if abs(st.width - 0.25) < 1e-6]
    assert len(light) == 1 and light[0].status == sl.VISIBLE, [(x.status, x.score) for x in light]
    # the picture-check primitive agrees: heavy ink is absent, light ink present
    rd = res.render
    assert sl.stroke_present(rd, (300, 200), (500, 200), (0, 0, 0), 1.5) < 0.2
    assert sl.stroke_present(rd, (300, 200), (500, 200), (0, 0, 0), 0.25) > 0.9
    assert sl.stroke_present(rd, (100, 200), (300, 200), (0, 0, 0), 1.5) > 0.9
    print("PASS case 3 (gate): a light line does not stand in for a hidden "
          "heavy line (stroke class = color AND weight)")
    return doc


# ----------------------------------------------------------------- case 12 --

COL = [(300.0, 200.0), (318.0, 200.0), (318.0, 218.0), (300.0, 218.0)]
COL2 = [(500.0, 200.0), (518.0, 200.0), (518.0, 218.0), (500.0, 218.0)]


def case12():
    s = Sheet()
    s.fill_poly(COL, GRAY)
    s.fill_poly(COL2, GRAY)
    s.cloud([(270, 185), (309, 185), (309, 240), (270, 240)])     # crosses COL
    s.polyline([(240, 150), (252, 170), (228, 170)], width=0.6, closed=True)   # the tag
    s.text(236, 168, "2", 6)
    s.line((252, 170), (312, 212), width=0.5)                      # a leader over COL
    s.fill_rect(509, 190, 530, 230, color=1.0)                     # a real mask over half of COL2
    doc, page = s.build()
    # a real PDF annotation across the first column too
    page.add_line_annot((295, 206), (325, 214))
    res = sl.gate(page)
    fs = fills_of(res)
    c1 = [f for f in fs if near(g2.centroid(f.vector_polys[0]), (309, 209), 1)][0]
    assert c1.status == sl.VISIBLE and c1.method == "vector", (c1.status, c1.why)
    assert same_poly(c1.polys[0], COL, 1e-6)
    assert c1.obscured > sl.OBSCURED_MIN, c1.obscured
    obs = res.record.obscured
    assert len(obs) == 1 and obs[0]["what"] == "fill", obs
    assert not any(d["what"] == "fill" for d in res.record.dropped), res.record.dropped
    assert not any(near(g2.centroid([(c["bbox"][0], c["bbox"][1]), (c["bbox"][2], c["bbox"][3])]),
                        (309, 209), 2) for c in res.record.clipped), "cloud is not a difference"
    # the half-masked column is a real change: clipped, traced, about half left
    c2 = [f for f in fs if near(g2.centroid(f.vector_polys[0]), (509, 209), 1)][0]
    assert c2.status == sl.CLIPPED and c2.method == "traced", (c2.status, c2.method)
    assert 0.40 < c2.score < 0.60, c2.score
    assert same_poly(c2.polys[0], [(500, 200), (509, 200), (509, 218), (500, 218)], 0.8), c2.polys
    print("PASS case 12: a revision cloud, leader and annotation across a column "
          "leave it whole and list it obscured; a real mask clips")
    return doc


# ----------------------------------------------------------------- case 15 --

def case15():
    for rot in (90, 270, 180):
        doc, page = sheet_case1().build(rot)
        m = page.rotation_matrix

        def xf(p, m=m):
            q = fitz.Point(p) * m
            return (q.x, q.y)

        res = sl.gate(page)
        assert res.registered, rot
        check_band(res, [xf(p) for p in BAND], f"rot{rot}")
        # the drop-in hands back RAW (unrotated) coordinates
        raw = [d for d in res.drawings() if d["type"] == "f"]
        assert len(raw) == 1 and len(raw[0]["items"]) == 4
        got = [(it[1].x, it[1].y) for it in raw[0]["items"]]
        assert same_poly(got, BAND, 0.05), (rot, got)
        doc2, page2 = sheet_case2().build(rot)
        m2 = page2.rotation_matrix

        def xf2(p, m=m2):
            q = fitz.Point(p) * m
            return (q.x, q.y)

        check_case2(sl.gate(page2), xf2, f"rot{rot}")
    print("PASS case 15: cases 1 and 2 hold on /Rotate 90, 270 and 180 pages")


# ------------------------------------------------------------ gate rules --

def rules():
    # dashed line: visible, not chopped into its dashes; hidden dashed: dropped
    s = Sheet()
    s.line((100, 100), (500, 100), width=0.75, dash=[6, 4])
    s.line((100, 140), (500, 140), width=0.75, dash=[6, 4])
    s.fill_rect(90, 130, 510, 150, color=1.0)
    # black line inside a black fill: cannot be judged, kept
    s.fill_rect(100, 200, 300, 260, color=0.0)
    s.line((120, 230), (280, 230), width=1.0)
    # paper-colored stroke: not judged
    s.line((100, 300), (300, 300), width=1.0, color=1.0)
    # transparent-looking fill is never dropped for mismatching (no gs here:
    # a plain light fill stays visible)
    s.fill_rect(400, 200, 460, 260, color=0.9)
    doc, page = s.build()
    res = sl.gate(page)
    dashed = [st for st in res.strokes if st.dashes.startswith("[ 6")]
    assert len(dashed) == 1 and dashed[0].status == sl.VISIBLE, [(d.status, d.score) for d in dashed]
    assert len(res.hidden_strokes) == 1 and res.hidden_strokes[0].dashes.startswith("[ 6")
    un = [st for st in res.strokes if st.status == sl.UNREADABLE]
    assert len(un) == 1 and seg_match(un[0], (120, 230), (280, 230)), [(u.pts, u.why) for u in un]
    assert any(u["what"] == "stroke" for u in res.record.unreadable)
    assert any("paper color" in n for n in res.record.notes), res.record.notes
    light = [f for f in res.fills if 228 <= f.rgb[0] <= 231 and len(set(f.rgb)) == 1]
    assert light and light[0].status == sl.VISIBLE
    print("PASS dashed strokes, own-color unreadables, paper-color strokes")

    # the 300 dpi small pass: a 3 pt tick hidden under white vs a visible one
    s = Sheet()
    s.line((200, 200), (203, 200), width=0.5)
    s.line((240, 200), (243, 200), width=0.5)
    s.fill_rect(236, 196, 247, 204, color=1.0)
    s.fill_rect(300, 200, 301.2, 201.2, color=0.0)               # a 1.2 pt dot
    doc, page = s.build()
    res = sl.gate(page)
    ticks = [st for st in res.strokes if st.length < 4 and st.status == sl.VISIBLE]
    assert len(ticks) == 1 and seg_match(ticks[0], (200, 200), (203, 200), 0.2), \
        [(t.pts, t.status) for t in res.strokes]
    assert len(res.hidden_strokes) == 1
    dot = [f for f in res.fills if f.rgb == (0, 0, 0)]
    assert len(dot) == 1 and dot[0].status == sl.VISIBLE, [(f.status, f.why) for f in res.fills]
    res_ns = sl.gate(page, small=False)
    assert len(res_ns.record.unreadable) >= 2 and not res_ns.hidden_strokes
    res_min = sl.gate(page, min_len_pt=4.0)
    assert not res_min.hidden_strokes and not any(
        u["what"] == "stroke" for u in res_min.record.unreadable)
    print("PASS small elements judged at 300 dpi; unjudged ones are kept, never dropped")

    # registration guard: vectors that are not on this page drop nothing
    s = Sheet()
    for i in range(60):
        s.line((50 + i * 10, 80), (50 + i * 10, 500), width=0.8)
    doc_a, page_a = s.build()
    doc_b, page_b = Sheet().line((40, 40), (700, 40), width=0.8).build()
    res = sl.gate(page_b, drawings=page_a.get_drawings(extended=True))
    assert not res.registered and not res.hidden_strokes and not res.record.dropped
    assert len(res.strokes) == 60 and all(st.status == sl.UNREADABLE for st in res.strokes)
    assert res.record.unreadable and res.record.unreadable[0]["what"] == "page"
    assert len(res.drawings()) == 60
    print("PASS registration guard: a page that does not register is recorded "
          "unreadable and nothing is dropped")

    # reverse check: fill-class pixels no accepted element covers
    s = Sheet()
    s.fill_rect(100, 100, 200, 112, color=GRAY)
    s.fill_rect(300, 100, 360, 112, color=GRAY)
    doc, page = s.build()
    res = sl.gate(page)
    acc = [fills_of(res)[0].polys[0]]
    left = sl.unread_regions(res.render, GRAY8, acc)
    other = fills_of(res)[1].polys[0]
    assert len(left) == 1 and near(left[0]["center"], g2.centroid(other), 1.0), left
    assert abs(left[0]["area"] - abs(g2.area(other))) < 0.15 * abs(g2.area(other))
    assert sl.unread_regions(res.render, GRAY8, [f.polys[0] for f in fills_of(res)]) == []
    print("PASS reverse check: seen but not read")

    # the drop-in: raw shape, switchable, hidden items gone
    doc, page = sheet_case2().build()
    raw = page.get_drawings()
    assert len(sl.get_drawings(page, sightline=False)) == len(raw)
    rec = []
    got = sl.get_drawings(page, record=rec)
    assert rec and rec[0].counts()["dropped"] == 2
    n_raw = sum(len(d["items"]) for d in raw if d["type"] == "s" and d.get("width") == 1.5)
    n_got = sum(len(d["items"]) for d in got if d["type"] == "s" and d.get("width") == 1.5)
    assert (n_raw, n_got) == (5, 3), (n_raw, n_got)
    assert all(isinstance(it[1], fitz.Point) for d in got for it in d["items"] if it[0] == "l")
    was = sl.enabled()
    sl.set_enabled(False)
    assert len(sl.get_drawings(page)) == len(raw)
    sl.set_enabled(was)
    doc0, page0 = Sheet().line((40, 40), (700, 40), width=0.8).rect(100, 100, 300, 200).build()
    assert sl.get_drawings(page0) == page0.get_drawings(), "an all-visible page is untouched"
    print("PASS drop-in get_drawings: raw shape, hidden items removed, switchable")


def retrofit():
    """Every existing vector reader takes its geometry through the gate
    (default on) and still reads raw vectors with the option off."""
    import tempfile
    from rfi_stamper import backcheck, drawdiff, extrude, reedcount, setscale
    tmp = tempfile.mkdtemp(prefix="sightline_retro_")
    hid = os.path.join(tmp, "hidden.pdf")
    sheet_case2().save(hid)

    def has(segs, a, b):
        return any((near((s_[0], s_[1]), a, 0.6) and near((s_[2], s_[3]), b, 0.6))
                   or (near((s_[0], s_[1]), b, 0.6) and near((s_[2], s_[3]), a, 0.6))
                   for s_ in segs)

    # extrude (and the Slipsheet, which reads through it)
    on = [(w.a[0], w.a[1], w.b[0], w.b[1])
          for w in extrude.extract_segments(hid, 1, min_len_pt=6.0, log=lambda *_: None)]
    off = [(w.a[0], w.a[1], w.b[0], w.b[1])
           for w in extrude.extract_segments(hid, 1, min_len_pt=6.0, log=lambda *_: None,
                                             sightline=False)]
    assert has(off, *HID_A) and has(off, *HID_B), "raw reading keeps hidden lines"
    assert not has(on, *HID_A) and not has(on, *HID_B), "gated reading drops them"
    assert has(on, *LIVE) and has(off, *LIVE)

    # the Slipsheet: superseded geometry left in the file is not a difference
    base = os.path.join(tmp, "base.pdf")
    rev = os.path.join(tmp, "rev.pdf")
    b = Sheet()
    b.line((100, 100), (600, 100), width=1.0).line((100, 100), (100, 400), width=1.0)
    b.save(base)
    r = Sheet()
    r.line((100, 100), (600, 100), width=1.0).line((100, 100), (100, 400), width=1.0)
    r.line((200, 250), (500, 250), width=1.0)             # superseded, left in the file
    r.fill_rect(190, 240, 510, 260, color=1.0)            # masked on the plotted sheet
    r.save(rev)
    quiet = dict(align=None, log=lambda *_: None)
    d_on = drawdiff.diff_pages(base, rev, **quiet)
    d_off = drawdiff.diff_pages(base, rev, sightline=False, **quiet)
    assert d_on["totals"]["added"] == 0, d_on["totals"]
    assert d_off["totals"]["added"] >= 1, d_off["totals"]

    doc = fitz.open(hid)
    page = doc[0]
    # the Story Pole
    was = sl.enabled()
    seg_on = [(a[0], a[1], c[0], c[1]) for a, c, _l in setscale._segments(page)]
    prim_on = reedcount._prims_from_page(page)
    sl.set_enabled(False)
    seg_off = [(a[0], a[1], c[0], c[1]) for a, c, _l in setscale._segments(page)]
    prim_off = reedcount._prims_from_page(page)
    sl.set_enabled(was)
    assert has(seg_off, *HID_A) and not has(seg_on, *HID_A)
    assert has(seg_on, *LIVE)
    # the Reed Count

    def prim_has(prims, a, c):
        return any(len(p["pts"]) == 2 and ((near(p["pts"][0], a, 0.6) and near(p["pts"][1], c, 0.6))
                                           or (near(p["pts"][0], c, 0.6) and near(p["pts"][1], a, 0.6)))
                   for p in prims)

    assert prim_has(prim_off, *HID_B) and not prim_has(prim_on, *HID_B)
    assert prim_has(prim_on, *LIVE)
    doc.close()

    # the Backcheck: a sheet whose only linework is hidden has no drawn content
    only_hidden = os.path.join(tmp, "only_hidden.pdf")
    o = Sheet()
    o.line((100, 100), (600, 100), width=1.0)
    o.fill_rect(90, 90, 610, 110, color=1.0)
    o.save(only_hidden)
    rep_on = backcheck.check_pdf(only_hidden, rules=["AMB-NO-SCALE"], log=lambda *_: None)
    was = sl.enabled()
    sl.set_enabled(False)
    rep_off = backcheck.check_pdf(only_hidden, rules=["AMB-NO-SCALE"], log=lambda *_: None)
    sl.set_enabled(was)
    n_on = sum(1 for f in rep_on.findings if f.code == "AMB-NO-SCALE")
    n_off = sum(1 for f in rep_off.findings if f.code == "AMB-NO-SCALE")
    assert (n_on, n_off) == (0, 1), (n_on, n_off)
    print("PASS retrofit: the Slipsheet, the Story Pole, the Backcheck, extrude "
          "and the Reed Count read through the gate by default; raw with it off")


def main():
    case1()
    case2()
    case3()
    case12()
    case15()
    rules()
    retrofit()
    print("SIGHTLINE TEST PASSED")


if __name__ == "__main__":
    main()
