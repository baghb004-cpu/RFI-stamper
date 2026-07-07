"""Self-contained tests for rfi_stamper.extrude — vector plan linework to a
3D wireframe in the Fieldstitch world frame.  Plain python, no pytest, no
project data.  Exercises:

* extract_segments: lines / rect edges / bezier chords, reversed-duplicate
  dedupe, short-tick dropping, quad edges, max_segments cap (+ log),
  /Rotate 90 pages mapped into viewer space, ValueError on a raster page
* to_world: hand-computed values at rotation 0 and 90 (Fieldstitch numbers)
* build_model: edge/column counts, shared-corner column merge, floors,
  bim axis mapping (x=E, y=N, z up), systems entry
* model_from_plan: end-to-end with a real fieldstitch.LayoutJob (ScaleCal),
  bounds check against page extents * scale around base_world, no-scale
  ValueError

Run:  python3.12 tests/test_extrude.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz                                              # noqa: E402

from rfi_stamper.extrude import (                        # noqa: E402
    Wall2D, build_model, extract_segments, model_from_plan, to_world)
from rfi_stamper.fieldstitch import LayoutJob            # noqa: E402
from rfi_stamper.markups.measure import ScaleCal         # noqa: E402


def expect(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc as e:
        return e
    raise AssertionError(f"expected {exc.__name__} from {fn!r}{args!r}")


def close(a, b, tol=1e-6):
    return abs(a - b) <= tol


# --------------------------------------------------------------- fixtures --

def make_plan(tmp):
    """Three-page fixture: 1 = vector mix, 2 = one quad, 3 = raster only."""
    path = os.path.join(tmp, "plan_mix.pdf")
    doc = fitz.open()

    page = doc.new_page(width=612, height=792)
    page.draw_line((100, 100), (300, 100))               # wall A
    page.draw_line((100, 100), (100, 250))               # wall B (shares pt)
    page.draw_line((300, 100), (100, 100))               # A reversed -> dupe
    page.draw_line((400, 400), (403, 400))               # 3 pt tick -> dropped
    page.draw_rect(fitz.Rect(350, 150, 450, 260))        # 4 edges
    page.draw_rect(fitz.Rect(120, 300, 220, 380))        # 4 edges
    page.draw_bezier((500, 500), (520, 560),
                     (560, 560), (580, 500))             # 3 chords

    page2 = doc.new_page(width=612, height=792)
    page2.draw_quad(fitz.Quad((300, 300), (400, 300),
                              (300, 400), (400, 400)))   # 4 edges

    page3 = doc.new_page(width=612, height=792)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 16, 16), False)
    pix.clear_with(128)
    page3.insert_image(fitz.Rect(100, 100, 300, 300), pixmap=pix)

    doc.save(path)
    doc.close()
    return path


# ------------------------------------------------------- extract_segments --

def test_extract(tmp):
    plan = make_plan(tmp)

    # page 1: 2 lines (dupe deduped, tick dropped) + 8 rect edges + 3 chords
    segs = extract_segments(plan, 1)
    assert all(isinstance(s, Wall2D) for s in segs)
    assert len(segs) == 13, len(segs)
    keys = {tuple(sorted((s.a, s.b))) for s in segs}
    assert ((100.0, 100.0), (300.0, 100.0)) in keys, "wall A missing"
    assert ((100.0, 100.0), (100.0, 250.0)) in keys, "wall B missing"
    # rect corners present as edge endpoints
    ends = {p for s in segs for p in (s.a, s.b)}
    for corner in ((350.0, 150.0), (450.0, 260.0), (120.0, 300.0)):
        assert corner in ends, (corner, sorted(ends))
    # nothing shorter than min_len survives
    for s in segs:
        d = ((s.b[0] - s.a[0]) ** 2 + (s.b[1] - s.a[1]) ** 2) ** 0.5
        assert d >= 6.0, (s, d)

    # min_len_pt=0 readmits the tick; the reversed duplicate stays deduped
    assert len(extract_segments(plan, 1, min_len_pt=0.0)) == 14

    # quad page: exactly the 4 quad edges
    assert len(extract_segments(plan, 2)) == 4

    # cap: truncates and logs
    logged = []
    capped = extract_segments(plan, 1, max_segments=5, log=logged.append)
    assert len(capped) == 5
    assert logged and "capped" in logged[0], logged

    # raster-only page: helpful ValueError
    err = expect(ValueError, extract_segments, plan, 3)
    assert "vector" in str(err).lower(), err
    # out-of-range page too
    expect(ValueError, extract_segments, plan, 9)


def test_extract_rotated(tmp):
    """/Rotate 90: get_drawings returns unrotated media coords; extraction
    must map them into viewer space via page.rotation_matrix."""
    path = os.path.join(tmp, "plan_rot.pdf")
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.draw_line((100, 100), (200, 100))
    page.set_rotation(90)
    doc.save(path)
    doc.close()
    (s,) = extract_segments(path, 1)
    # viewer x' = 792 - y, y' = x  (rotation_matrix for /Rotate 90)
    got = tuple(sorted((s.a, s.b)))
    assert got == ((692.0, 100.0), (692.0, 200.0)), got


# ---------------------------------------------------------------- to_world --

def test_to_world():
    """Fieldstitch numbers: base_page (100,700) = world N 5000 / E 2000,
    1 pt = 0.1 ft; (110,680) is 10 pt right and 20 pt up of the basepoint."""
    segs = [Wall2D((100.0, 700.0), (110.0, 680.0))]

    w = to_world(segs, (100.0, 700.0), (5000.0, 2000.0), 0.0, 0.1)
    assert len(w) == 1
    a, b = w[0]
    assert close(a[0], 2000.0) and close(a[1], 5000.0), a   # basepoint
    assert close(b[0], 2001.0), b                           # E = 2000 + 10*0.1
    assert close(b[1], 5002.0), b                           # N = 5000 + 20*0.1

    # 90 CCW: (east', north') = (10, 20) rotates to (-20, 10)
    a, b = to_world(segs, (100.0, 700.0), (5000.0, 2000.0), 90.0, 0.1)[0]
    assert close(a[0], 2000.0) and close(a[1], 5000.0), a
    assert close(b[0], 1998.0), b                           # E = 2000 - 20*0.1
    assert close(b[1], 5001.0), b                           # N = 5000 + 10*0.1

    assert to_world([], (0, 0), (0, 0), 0.0, 1.0) == []


# ------------------------------------------------------------- build_model --

def _horiz(m):
    return [s for s in m.segments if s.a[2] == s.b[2]]


def _vert(m):
    return [s for s in m.segments if (s.a[0], s.a[1]) == (s.b[0], s.b[1])]


def test_build_model():
    # one wall -> bottom + top edges and 2 corner columns
    m = build_model([((0.0, 0.0), (10.0, 0.0))], wall_height=10.0)
    assert len(m.segments) == 4, len(m.segments)
    h, v = _horiz(m), _vert(m)
    assert len(h) == 2 and len(v) == 2, (len(h), len(v))
    assert sorted(s.a[2] for s in h) == [0.0, 10.0]
    for s in v:
        assert (s.a[2], s.b[2]) == (0.0, 10.0), s
    # bim axis mapping: x = E, y = N
    bottom = min(h, key=lambda s: s.a[2])
    assert bottom.a == (0.0, 0.0, 0.0) and bottom.b == (10.0, 0.0, 0.0)
    assert m.systems == [("walls", "#9aab9e")], m.systems
    assert all(s.system == "walls" for s in m.segments)

    # two walls sharing an endpoint -> ONE column at the shared corner
    two = [((0.0, 0.0), (10.0, 0.0)), ((10.0, 0.0), (10.0, 8.0))]
    m2 = build_model(two, wall_height=10.0)
    assert len(_horiz(m2)) == 4 and len(_vert(m2)) == 3, \
        (len(_horiz(m2)), len(_vert(m2)))
    # near-coincident endpoints (within 0.05) merge too
    near = [((0.0, 0.0), (10.0, 0.0)), ((10.02, 0.01), (10.0, 8.0))]
    assert len(_vert(build_model(near, wall_height=10.0))) == 3

    # floors=2 doubles the horizontal edges and raises max z past one storey
    mf = build_model([((0.0, 0.0), (10.0, 0.0))], wall_height=10.0, floors=2)
    assert len(_horiz(mf)) == 4 and len(_vert(mf)) == 4
    assert close(mf.bounds()[1][2], 20.8), mf.bounds()      # 10 + 0.8 + 10
    zs = sorted({s.a[2] for s in _horiz(mf)})
    assert zs == [0.0, 10.0, 10.8, 20.8], zs

    # custom color flows to segments and the systems legend
    mc = build_model([((0.0, 0.0), (10.0, 0.0))], wall_height=8.0,
                     color="#ff0000")
    assert mc.systems == [("walls", "#ff0000")]
    assert all(s.color == "#ff0000" for s in mc.segments)

    expect(ValueError, build_model, [((0.0, 0.0), (1.0, 0.0))], 0.0)


# --------------------------------------------------------- model_from_plan --

def test_model_from_plan(tmp):
    plan = os.path.join(tmp, "plan_bldg.pdf")
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.draw_rect(fitz.Rect(100, 500, 300, 700))        # building footprint
    page.draw_line((100, 500), (300, 700))               # interior diagonal
    doc.save(plan)
    doc.close()

    job = LayoutJob()                                    # in-memory job
    job.base_page_xy = (100.0, 700.0)                    # footprint SW corner
    job.base_world = (5000.0, 2000.0)
    job.cal = ScaleCal(real_per_pt=0.1, unit="ft")

    model, stats = model_from_plan(plan, job=job, wall_height=12.0)
    assert stats == {"segments": 5, "walls": 5,
                     "height": 12.0, "floors": 1}, stats
    # page x 100..300 -> E 2000..2020; page y 500..700 -> N 5000..5020
    (mnx, mny, mnz), (mxx, mxy, mxz) = model.bounds()
    assert close(mnx, 2000.0) and close(mxx, 2020.0), (mnx, mxx)
    assert close(mny, 5000.0) and close(mxy, 5020.0), (mny, mxy)
    assert close(mnz, 0.0) and close(mxz, 12.0), (mnz, mxz)
    assert model.systems == [("walls", "#9aab9e")]
    # footprint: 5 walls x 2 edges + 5 corner columns (4 shared + diagonal
    # reuses two of them)
    assert len(model.segments) == 14, len(model.segments)

    # floors flow through
    m2, st2 = model_from_plan(plan, job=job, wall_height=12.0, floors=2)
    assert st2["floors"] == 2 and len(m2.segments) == 28
    assert close(m2.bounds()[1][2], 24.8)                # 12 + 0.8 + 12

    # no scale (or no job at all) -> the exact guidance error
    bare = LayoutJob()
    bare.scale = None
    for bad_job in (bare, None):
        err = expect(ValueError, model_from_plan, plan, job=bad_job)
        assert "scale" in str(err).lower(), err


# ------------------------------------------------------------------ runner --

def main():
    tmp = tempfile.mkdtemp(prefix="extrude_")
    test_extract(tmp)
    test_extract_rotated(tmp)
    test_to_world()
    test_build_model()
    test_model_from_plan(tmp)
    print("EXTRUDE TESTS PASSED  (extract lines/rects/quads/beziers + dedupe "
          "+ tick drop + cap + /Rotate 90 + raster ValueError, to_world "
          "rotation 0/90, build_model edges/columns/floors, model_from_plan "
          "end-to-end bounds + no-scale ValueError)")
    print("outputs in", tmp)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("EXTRUDE TEST FAILED:", e)
        sys.exit(1)
