"""Tests for rfi_stamper.bim (3D math + model) and the Bim3DViewer widget.

Plain python, no pytest.  The math half always runs headless; the GUI half
runs when a display exists, or re-execs itself under xvfb-run (same idea as
tests/run_all.py) so the whole script stays one command:

    python tests/test_bim.py
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                     # noqa: E402

from rfi_stamper import bim                            # noqa: E402


# ------------------------------------------------------------------- math ---

def test_project_points():
    cam = bim.Camera(yaw=0.0, pitch=0.0, dist=60.0, target=(0.0, 0.0, 0.0))
    w, h = 800, 600

    # the target projects to the exact canvas centre, in front of the camera
    p = bim.project_points([(0.0, 0.0, 0.0)], cam, w, h)
    assert p.shape == (1, 3), p.shape
    assert abs(p[0, 0] - 400) < 1e-6 and abs(p[0, 1] - 300) < 1e-6, p
    assert abs(p[0, 2] - 60.0) < 1e-6, p

    # +x (east) lands right of centre; +z (up) lands above centre (y down)
    px = bim.project_points([(10.0, 0.0, 0.0)], cam, w, h)
    assert px[0, 0] > 400 and px[0, 2] > 0, px
    pz = bim.project_points([(0.0, 0.0, 10.0)], cam, w, h)
    assert pz[0, 1] < 300, pz

    # behind the camera (eye sits at y=-60 looking north): depth <= 0
    pb = bim.project_points([(0.0, -100.0, 0.0)], cam, w, h)
    assert pb[0, 2] <= 0, pb
    assert np.all(np.isfinite(pb)), pb                 # NaN/inf-safe

    # garbage input stays finite too
    pg = bim.project_points([(float("nan"), 0.0, 0.0)], cam, w, h)
    assert np.all(np.isfinite(pg)), pg

    # ortho and perspective genuinely differ off-centre
    cam_o = bim.Camera(yaw=0.0, pitch=0.0, dist=60.0, ortho=True)
    a = bim.project_points([(5.0, 10.0, 3.0)], cam, w, h)
    b = bim.project_points([(5.0, 10.0, 3.0)], cam_o, w, h)
    assert abs(a[0, 0] - b[0, 0]) > 0.5 or abs(a[0, 1] - b[0, 1]) > 0.5, (a, b)

    # list input and (N,3) array input agree
    arr = bim.project_points(np.array([[1.0, 2.0, 3.0]]), cam, w, h)
    lst = bim.project_points([(1.0, 2.0, 3.0)], cam, w, h)
    assert np.allclose(arr, lst)

    # empty input -> empty (0,3) result
    assert bim.project_points([], cam, w, h).shape == (0, 3)


def test_orbit_moves_point():
    w, h = 800, 600
    q1 = bim.project_points([(10.0, 4.0, 2.0)], bim.Camera(yaw=10.0), w, h)
    q2 = bim.project_points([(10.0, 4.0, 2.0)], bim.Camera(yaw=40.0), w, h)
    assert abs(q1[0, 0] - q2[0, 0]) > 1.0, (q1, q2)


def test_model_bounds():
    assert bim.Model().bounds() == ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    m = bim.Model(segments=[bim.Segment((0, 1, 2), (3, -1, 5))])
    mn, mx = m.bounds()
    assert mn == (0.0, -1.0, 2.0) and mx == (3.0, 1.0, 5.0), (mn, mx)
    # planes count toward bounds too
    bim.add_sheet_plane(m, "S", 1, 9.0, size=(2.0, 2.0))
    assert m.bounds()[1][2] == 9.0


def test_demo_building():
    m = bim.demo_building()
    assert len(m.segments) > 100, len(m.segments)
    assert len(m.systems) >= 2, m.systems
    for name, color in m.systems:
        assert isinstance(name, str) and color.startswith("#"), (name, color)
    # distinct MEP colors among the systems
    assert len({c for _, c in m.systems}) >= 2
    # planes addable to the demo model
    n0 = len(m.planes)
    pl = bim.add_sheet_plane(m, "P-201", 5, 3.6)
    assert len(m.planes) == n0 + 1 and m.planes[-1] is pl
    # geometry stays finite
    (mnx, mny, mnz), (mxx, mxy, mxz) = m.bounds()
    assert all(math.isfinite(v) for v in (mnx, mny, mnz, mxx, mxy, mxz))
    assert mxz > mnz and mxx > mnx


def test_add_sheet_plane():
    m = bim.demo_building(floors=2, bays_x=2, bays_y=2)
    pl = bim.add_sheet_plane(m, "A-101", 3, 7.2, size=(40.0, 28.0))
    assert pl.label == "A-101" and pl.page_no == 3
    assert len(pl.corners) == 4
    for (_, _, z) in pl.corners:
        assert abs(z - 7.2) < 1e-9, pl.corners          # elevation respected
    xs = [p[0] for p in pl.corners]
    ys = [p[1] for p in pl.corners]
    assert abs(max(xs) - min(xs) - 40.0) < 1e-9
    assert abs(max(ys) - min(ys) - 28.0) < 1e-9
    # centred on the footprint (demo is centred at the origin)
    assert abs(sum(xs) / 4.0) < 1e-9 and abs(sum(ys) / 4.0) < 1e-9


_CUBE_OBJ = """\
# synthetic unit cube
v 0 0 0
v 1 0 0
v 1 1 0
v 0 1 0
v 0 0 1
v 1 0 1
v 1 1 1
v 0 1 1
f 1 2 3 4
f 5 6 7 8
f 1 2 6 5
f 2 3 7 6
f 3 4 8 7
f 4 1 5 8
"""


def test_load_obj():
    tmp = tempfile.mkdtemp(prefix="rfi_bim_")
    path = os.path.join(tmp, "cube.obj")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_CUBE_OBJ)
    m = bim.load_obj(path)
    assert len(m.segments) == 12, len(m.segments)       # shared edges deduped
    mn, mx = m.bounds()
    assert mn == (0.0, 0.0, 0.0) and mx == (1.0, 1.0, 1.0)

    # v/vt/vn face tokens, l polylines, comments, junk lines all tolerated
    path2 = os.path.join(tmp, "mixed.obj")
    with open(path2, "w", encoding="utf-8") as fh:
        fh.write("v 0 0 0\nv 1 0 0\nv 1 1 0\n"
                 "vn 0 0 1\nvt 0 0\nusemtl none\n"
                 "f 1/1/1 2/1/1 3/1/1\n"
                 "l 1 3\n")                             # duplicate of a face edge
    m2 = bim.load_obj(path2)
    assert len(m2.segments) == 3, len(m2.segments)

    # nothing usable -> ValueError
    path3 = os.path.join(tmp, "empty.obj")
    with open(path3, "w", encoding="utf-8") as fh:
        fh.write("# nothing here\nvn 0 0 1\n")
    try:
        bim.load_obj(path3)
        raise AssertionError("load_obj accepted an empty OBJ")
    except ValueError:
        pass
    shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------- picking / interrogation ---

def test_screen_ray():
    """Round-trip: project a point, cast a ray back through that pixel —
    the ray must pass through the point (both projections, varied cams)."""
    w, h = 800, 600
    pts = [(x, y, z) for x in (-8.0, 0.0, 7.0) for y in (-6.0, 0.0, 9.0)
           for z in (0.0, 4.0)]
    for yaw in (0.0, 33.0, 190.0):
        for pitch in (0.0, 25.0, -40.0):
            for ortho in (False, True):
                cam = bim.Camera(yaw=yaw, pitch=pitch, dist=45.0,
                                 target=(1.0, -2.0, 3.0), ortho=ortho)
                scr = bim.project_points(pts, cam, w, h)
                for i, p in enumerate(pts):
                    sx, sy, depth = scr[i]
                    if depth <= 1e-6:
                        continue
                    o, d = bim.screen_ray(cam, sx, sy, w, h)
                    v = np.asarray(p, dtype=float) - o
                    miss = float(np.linalg.norm(np.cross(v, d)))
                    assert miss < 1e-6 * max(depth, 1.0), \
                        (yaw, pitch, ortho, p, miss)


def _scalar_mt(o, d, tri):
    """Textbook scalar Möller-Trumbore (two-sided) — the reference."""
    v0, v1, v2 = (np.asarray(v, dtype=float) for v in tri)
    e1, e2 = v1 - v0, v2 - v0
    p = np.cross(d, e2)
    det = float(e1 @ p)
    if abs(det) < 1e-12:
        return math.inf
    inv = 1.0 / det
    tv = o - v0
    u = float(tv @ p) * inv
    if u < -1e-9 or u > 1 + 1e-9:
        return math.inf
    q = np.cross(tv, e1)
    v = float(d @ q) * inv
    if v < -1e-9 or u + v > 1 + 1e-9:
        return math.inf
    t = float(e2 @ q) * inv
    return t if t > 1e-9 else math.inf


def test_ray_triangles():
    tri = np.array([[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]])
    down = np.array([0.0, 0.0, -1.0])
    bary = np.array([1 / 3, 1 / 3, 5.0])
    t = bim.ray_triangles(bary, down, tri)
    assert abs(t[0] - 5.0) < 1e-9, t
    # outside, parallel, and behind-the-origin all miss
    assert np.isinf(bim.ray_triangles(np.array([2.0, 2.0, 5.0]), down, tri))[0]
    assert np.isinf(bim.ray_triangles(bary, np.array([1.0, 0.0, 0.0]), tri))[0]
    assert np.isinf(bim.ray_triangles(bary, np.array([0.0, 0.0, 1.0]), tri))[0]
    # reversed winding still hits (two-sided |det|)
    assert abs(bim.ray_triangles(bary, down, tri[:, ::-1])[0] - 5.0) < 1e-9
    # empty input
    assert bim.ray_triangles(bary, down, np.zeros((0, 3, 3))).shape == (0,)
    # vectorized equals the scalar reference on seeded rays
    import random
    rnd = random.Random(42)
    tris = np.array([[[rnd.uniform(-5, 5) for _ in range(3)]
                      for _ in range(3)] for _ in range(40)])
    o = np.array([0.0, 0.0, 20.0])
    for _ in range(25):
        d = np.array([rnd.uniform(-1, 1), rnd.uniform(-1, 1), -1.0])
        d = d / np.linalg.norm(d)
        tv = bim.ray_triangles(o, d, tris)
        for i in range(len(tris)):
            ts = _scalar_mt(o, d, tris[i])
            assert (math.isinf(ts) and math.isinf(tv[i])) \
                or abs(ts - tv[i]) < 1e-9, (i, ts, tv[i])
    # fan_tris: quad -> 2, octagon -> 6
    quad = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
    assert len(bim.fan_tris(quad)) == 2
    assert len(bim.fan_tris(list(range(8)) and
                            [(i, i, i) for i in range(8)])) == 6


def test_clip_segment_box():
    mn, mx = (0.0, 0.0, 0.0), (10.0, 10.0, 10.0)
    # fully inside: BITWISE-equal endpoints, no cut flags
    a, b = (1.25, 2.5, 3.75), (9.0, 8.0, 7.0)
    (p0, p1), (ca, cb) = bim.clip_segment_box(a, b, mn, mx)
    assert p0 == a and p1 == b and not ca and not cb
    # fully outside
    assert bim.clip_segment_box((-5, -5, -5), (-1, -1, -1), mn, mx) is None
    # one-plane crossing: manufactured endpoint ON the plane, CUT flag set
    (p0, p1), (ca, cb) = bim.clip_segment_box((-2.0, 5.0, 5.0),
                                              (4.0, 5.0, 5.0), mn, mx)
    assert abs(p0[0]) < 1e-9 and ca and not cb
    assert p1 == (4.0, 5.0, 5.0)
    # axis-parallel segment outside its slab
    assert bim.clip_segment_box((-1, 20, 5), (11, 20, 5), mn, mx) is None
    # zero-length inside kept
    r = bim.clip_segment_box((5, 5, 5), (5, 5, 5), mn, mx)
    assert r is not None and r[0][0] == (5.0, 5.0, 5.0)
    # a segment lying exactly ON a box face survives (inclusive eps)
    r = bim.clip_segment_box((0.0, 2.0, 2.0), (0.0, 8.0, 8.0), mn, mx)
    assert r is not None and not r[1][0] and not r[1][1]


def _shoelace_xy(poly):
    s = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i - 1][0], poly[i - 1][1]
        x2, y2 = poly[i][0], poly[i][1]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def test_clip_poly_box():
    mn, mx = (0.0, 0.0, 0.0), (10.0, 10.0, 10.0)
    # fully inside: identical vertex list
    quad = [(1.0, 1.0, 2.0), (9.0, 1.0, 2.0), (9.0, 9.0, 2.0),
            (1.0, 9.0, 2.0)]
    assert bim.clip_poly_box(quad, mn, mx) == quad
    # diamond with 4 poking corners -> octagon; analytic area 128 - 4*9
    diamond = [(13.0, 5.0, 2.0), (5.0, 13.0, 2.0), (-3.0, 5.0, 2.0),
               (5.0, -3.0, 2.0)]
    out = bim.clip_poly_box(diamond, mn, mx)
    assert len(out) == 8, len(out)
    assert abs(_shoelace_xy(out) - 92.0) < 1e-9, _shoelace_xy(out)
    for p in out:
        assert -1e-9 <= p[0] <= 10.0 + 1e-9 and -1e-9 <= p[1] <= 10.0 + 1e-9
    # fully outside -> []
    assert bim.clip_poly_box([(20, 20, 2), (30, 20, 2), (30, 30, 2)],
                             mn, mx) == []
    # vertices exactly on box planes survive
    tri = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0)]
    assert len(bim.clip_poly_box(tri, mn, mx)) == 3
    # degenerate sliver (collinear "polygon") filtered
    assert bim.clip_poly_box([(1, 1, 1), (2, 2, 2), (3, 3, 3)], mn, mx) == []


def test_box_at_bounds():
    """Section box set exactly to model.bounds() must be a no-op: every
    segment survives bitwise-untouched, no cut flags (inclusive eps)."""
    m = bim.demo_building()
    mn, mx = m.bounds()
    for s in m.segments:
        r = bim.clip_segment_box(s.a, s.b, mn, mx)
        assert r is not None, s
        (a2, b2), (ca, cb) = r
        assert a2 == tuple(float(v) for v in s.a), s
        assert b2 == tuple(float(v) for v in s.b), s
        assert not ca and not cb, s
    faces = bim.wall_faces([((mn[0], mn[1]), (mx[0], mn[1]))],
                           wall_height=max(mx[2], 1.0))
    for f in faces:
        assert bim.clip_poly_box(f.pts, mn, mx) == f.pts


def test_measure3d():
    import random

    from rfi_stamper import fieldpro
    rnd = random.Random(7)
    for _ in range(30):
        a = (rnd.uniform(-100, 100), rnd.uniform(-100, 100),
             rnd.uniform(-20, 20))
        b = (rnd.uniform(-100, 100), rnd.uniform(-100, 100),
             rnd.uniform(-20, 20))
        r = bim.measure3d(a, b)
        rec = fieldpro.deltas((a[1], a[0], a[2]), (b[1], b[0], b[2]))
        assert r["dn"] == rec.dn and r["de"] == rec.de and r["dz"] == rec.dz
        assert r["hd"] == rec.hd and r["azimuth"] == rec.azimuth
        assert abs(r["sd"] ** 2 - (r["hd"] ** 2 + r["dz"] ** 2)) < 1e-6
    # due east (+x) is azimuth 90 from north; flat run
    r = bim.measure3d((0.0, 0.0, 0.0), (10.0, 0.0, 0.0))
    assert abs(r["azimuth"] - 90.0) < 1e-12
    assert r["hd"] == 10.0 and r["sd"] == 10.0 and r["vd"] == 0.0
    # 1 ft rise over 96 ft run = 1/8"-per-foot
    r = bim.measure3d((0.0, 0.0, 100.0), (96.0, 0.0, 101.0))
    assert abs(r["slope_in_ft"] - 0.125) < 1e-12
    assert r["cut_fill"].startswith("C")
    # a run too short to judge reports no slope
    assert bim.measure3d((0, 0, 0), (0.005, 0, 1))["slope_in_ft"] is None


# -------------------------------------------------------------------- GUI ---

def test_gui():
    import tkinter as tk

    from rfi_stamper.gui.bim3d import Bim3DViewer
    from rfi_stamper.gui.theme import DARK, ThemeManager

    root = tk.Tk()
    root.geometry("1000x700")
    theme = ThemeManager(root)
    opened = []
    v = Bim3DViewer(root, theme,
                    on_open_sheet=lambda pno, lbl: opened.append((pno, lbl)))
    v.pack(fill="both", expand=True)
    root.update_idletasks()
    root.update()

    v.set_model(bim.demo_building())
    root.update()
    cv = v.canvas
    assert len(cv.find_all()) > 100, len(cv.find_all())
    assert len(v.legend.winfo_children()) >= 2          # legend populated

    # left-drag orbit changes yaw/pitch (pitch stays clamped)
    yaw0, pitch0 = v.cam.yaw, v.cam.pitch
    cv.event_generate("<ButtonPress-1>", x=300, y=300)
    cv.event_generate("<B1-Motion>", x=345, y=322)
    cv.event_generate("<ButtonRelease-1>", x=345, y=322)
    root.update()
    assert v.cam.yaw != yaw0 and v.cam.pitch != pitch0, (v.cam.yaw, v.cam.pitch)
    assert -89.0 <= v.cam.pitch <= 89.0

    # wheel zoom shrinks dist (MouseWheel where supported, X11 buttons else)
    d0 = v.cam.dist
    try:
        cv.event_generate("<MouseWheel>", x=300, y=300, delta=120)
    except tk.TclError:
        pass
    if v.cam.dist == d0:
        cv.event_generate("<Button-4>", x=300, y=300)
    root.update()
    assert v.cam.dist < d0, (v.cam.dist, d0)

    # middle-drag pans the target in the view plane
    t0 = v.cam.target
    cv.event_generate("<ButtonPress-2>", x=300, y=300)
    cv.event_generate("<B2-Motion>", x=340, y=280)
    cv.event_generate("<ButtonRelease-2>", x=340, y=280)
    root.update()
    assert v.cam.target != t0, v.cam.target

    # projection toggle + fit (fx module absent -> silent instant fallback)
    v.toggle_ortho()
    root.update()
    assert v.cam.ortho and v.proj_btn.cget("text") == "Ortho"
    v.fit()
    root.update()
    assert len(cv.find_all()) > 100

    # sheet chip drawn, and a click near it fires on_open_sheet
    v.add_sheet("A-101", 3, 4.0)
    root.update()
    box = cv.bbox("chip")
    assert box, "sheet chip not drawn"
    mx, my = int((box[0] + box[2]) / 2), int((box[1] + box[3]) / 2)
    cv.event_generate("<ButtonPress-1>", x=mx, y=my)
    cv.event_generate("<ButtonRelease-1>", x=mx, y=my)
    root.update()
    assert opened and opened[-1] == (3, "A-101"), opened

    # theme change recolors the canvas and re-renders the scene
    theme.apply("dark")
    root.update()
    assert cv.cget("background") == DARK["canvas_bg"], cv.cget("background")
    assert len(cv.find_all()) > 100

    # adaptive-detail state exists and sits at full detail when idle
    assert v._lod == 1.0

    # -- True depth (raster z-buffer) ------------------------------------
    # a model WITH faces + raster on: one blitted image, no painter
    # polygons, wireframe overlays above, chip clicks still route
    m2 = bim.Model(segments=[bim.Segment((0, 0, 0), (20, 0, 0))],
                   systems=[("walls", "#9aab9e")])
    m2.faces = bim.wall_faces([((0.0, 0.0), (20.0, 0.0)),
                               ((20.0, 0.0), (20.0, 15.0))], 9.0)
    v.set_model(m2)
    v._cancel_cam_anim()        # kill the fly-in: xvfb frames are slow
    v._fit_instant()            # enough to trip the honest painter fallback
    v.shaded_var.set(True)
    v.raster_var.set(True)
    v._raster_slow = False
    v._lod = 1.0
    v._render()
    root.update()
    assert v._photo is not None, "raster blit missing"
    assert cv.find_withtag("raster"), "raster image item missing"
    assert not cv.find_withtag("face"), "painter polygons in raster mode"
    assert cv.find_withtag("seg"), "wireframe overlay missing above blit"

    opened.clear()
    v.add_sheet("A-102", 7, 4.0)
    root.update()
    box = cv.bbox("chip")
    assert box, "sheet chip not drawn in raster mode"
    mx, my = int((box[0] + box[2]) / 2), int((box[1] + box[3]) / 2)
    cv.event_generate("<ButtonPress-1>", x=mx, y=my)
    cv.event_generate("<ButtonRelease-1>", x=mx, y=my)
    root.update()
    assert opened and opened[-1] == (7, "A-102"), opened

    # toggling raster off restores the painter polygons, drops the image
    v.raster_var.set(False)
    v._on_raster_toggle()
    root.update()
    assert not cv.find_withtag("raster") and v._photo is None
    assert cv.find_withtag("face"), "painter polygons missing after toggle"

    # -- section box + picking + measure ---------------------------------
    v.set_model(bim.demo_building())
    v._cancel_cam_anim()
    v._fit_instant()
    v.shaded_var.set(False)
    v._render()
    root.update()
    n_before = len(cv.find_withtag("seg"))
    assert n_before > 100

    # enable: 6 handles, box at bounds is a no-op on the drawn scene
    v.section_var.set(True)
    v._on_section_toggle()
    root.update()
    assert len(cv.find_withtag("boxhandle")) == 6
    assert cv.find_withtag("sectionbox")
    assert len(cv.find_withtag("seg")) == n_before, "full box changed scene"

    # drag the +z handle downward: plane moves down, geometry drops
    hz = cv.find_withtag("boxface:5")
    assert hz, "+z handle missing"
    x1, y1, x2, y2 = cv.bbox(hz[0])
    hx, hy = (x1 + x2) // 2, (y1 + y2) // 2
    z0 = v.section["mx"][2]
    cv.event_generate("<ButtonPress-1>", x=hx, y=hy)
    cv.event_generate("<B1-Motion>", x=hx, y=hy + 60)
    cv.event_generate("<ButtonRelease-1>", x=hx, y=hy + 60)
    root.update()
    z_cut = v.section["mx"][2]
    assert z_cut < z0, (z0, z_cut)
    assert len(cv.find_withtag("seg")) < n_before

    # picking: a model vertex wins exactly; a clip-manufactured endpoint
    # is NOT a vertex (edge snap reaches it honestly)
    w2, h2 = cv.winfo_width(), cv.winfo_height()
    corner = v.model.segments[0].a
    scr = bim.project_points([corner], v.cam, w2, h2)
    hit = v._pick(scr[0, 0], scr[0, 1])
    assert hit and hit["kind"] == "vertex"
    assert hit["true_pt"] == tuple(float(q) for q in corner)
    cand = next(s for s in v.model.segments
                if s.a[0] == s.b[0] and s.a[1] == s.b[1]
                and min(s.a[2], s.b[2]) < z_cut < max(s.a[2], s.b[2]))
    scr = bim.project_points([(cand.a[0], cand.a[1], z_cut)], v.cam, w2, h2)
    hit = v._pick(scr[0, 0], scr[0, 1])
    assert hit and hit["kind"] != "vertex", hit

    # double-click the handle resets that plane; Section off = full model
    # (tk can't event_generate a Double modifier — call the handler)
    v._on_double(type("E", (), {"x": hx, "y": hy})())
    root.update()
    v.section_var.set(False)
    v._on_section_toggle()
    root.update()
    assert v.section is None and not cv.find_withtag("sectionbox")
    assert len(cv.find_withtag("seg")) == n_before

    # face pick: shaded faces are ray-pickable where no vertex/edge is near
    m3 = bim.Model(segments=[bim.Segment((0, 0, 0), (20, 0, 0))],
                   systems=[("walls", "#9aab9e")])
    m3.faces = bim.wall_faces([((0.0, 0.0), (20.0, 0.0))], 9.0)
    v.set_model(m3)
    v._cancel_cam_anim()
    v._fit_instant()
    v.shaded_var.set(True)
    v.raster_var.set(False)
    v._render()
    root.update()
    scr = bim.project_points([(10.0, 0.0, 4.5)], v.cam, w2, h2)
    hit = v._pick(scr[0, 0], scr[0, 1])
    assert hit and hit["kind"] == "face", hit
    assert abs(hit["true_pt"][1]) < 1e-9, hit       # on the wall plane
    # priority: the face CORNER pixel belongs to the vertex, not the face
    scr = bim.project_points([(0.0, 0.0, 0.0)], v.cam, w2, h2)
    hit = v._pick(scr[0, 0], scr[0, 1])
    assert hit and hit["kind"] == "vertex"

    # measure: two picks -> SD/HD/VD + azimuth label; rubber band lives
    # between them; Esc clears everything
    v.toggle_measure()
    scr = bim.project_points([(0.0, 0.0, 0.0), (20.0, 0.0, 0.0)],
                             v.cam, w2, h2)
    v._measure_click(scr[0, 0], scr[0, 1])
    cv.event_generate("<Motion>", x=int(scr[1, 0]), y=int(scr[1, 1]))
    root.update()
    assert cv.find_withtag("rubber"), "rubber band missing"
    v._measure_click(scr[1, 0], scr[1, 1])
    root.update()
    texts = [cv.itemcget(i, "text") for i in cv.find_withtag("measure")
             if cv.type(i) == "text"]
    assert texts and all(k in texts[0] for k in ("SD", "HD", "VD", "°")), \
        texts
    assert "az 90.0°" in texts[0], texts            # due-east wall
    v._on_escape()
    root.update()
    assert not v.measuring and not cv.find_withtag("measure")
    assert not cv.find_withtag("rubber")

    root.destroy()
    print("-- gui portion ok")


# ------------------------------------------------------------------ runner ---

def main():
    test_project_points()
    test_orbit_moves_point()
    test_model_bounds()
    test_demo_building()
    test_add_sheet_plane()
    test_load_obj()
    test_screen_ray()
    test_ray_triangles()
    test_clip_segment_box()
    test_clip_poly_box()
    test_box_at_bounds()
    test_measure3d()

    if os.environ.get("DISPLAY"):
        test_gui()
    else:
        xvfb = shutil.which("xvfb-run")
        if xvfb and not os.environ.get("_BIM_XVFB"):
            # headless: re-exec this whole script under a virtual display
            env = dict(os.environ, _BIM_XVFB="1")
            r = subprocess.run([xvfb, "-a", sys.executable,
                                os.path.abspath(__file__)], env=env)
            raise SystemExit(r.returncode)
        print("-- no display and no xvfb-run: GUI portion skipped")

    print("BIM TESTS PASSED")


if __name__ == "__main__":
    main()
