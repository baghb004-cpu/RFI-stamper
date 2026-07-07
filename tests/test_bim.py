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
