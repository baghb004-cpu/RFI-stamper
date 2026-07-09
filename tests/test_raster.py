"""Headless tests for rfi_stamper.raster (numpy z-buffer rasterizer).

Pins the rules the module commits to: pixel-center coverage, inclusive
fill + strict-> depth ties (no cracks, deterministic winner), 1/z
interpolation under perspective, near-plane clipping (not clamping, not
whole-triangle rejection), two-sided fill, painter-parity flat shading,
plus a golden-image hash and a perf tripwire.

Run:  python3 tests/test_raster.py          (PLOOM_REGOLD=1 prints a new
                                             golden hash instead of failing)
"""
import hashlib
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                 # noqa: E402

from rfi_stamper import bim, raster                # noqa: E402

BG = "#10141a"

# fixed scene, ortho camera at yaw=0/pitch=0 (exact trig -> bit-exact basis)
GOLDEN = "4e718b623db8949893cb924ed3b4fc2f33354b73dd44002d81a1010119eb08de"


def _px_cam(w: int = 8, h: int = 8, dist: float = 10.0) -> bim.Camera:
    """Ortho camera with k = exactly 1 px per world unit: sx = w/2 + x,
    sy = h/2 - z (yaw=0/pitch=0: right = +x east, up = +z)."""
    fov = 2.0 * math.degrees(math.atan(h / (2.0 * dist)))
    return bim.Camera(yaw=0.0, pitch=0.0, dist=dist, target=(0.0, 0.0, 0.0),
                      fov=fov, ortho=True)


# ---------------------------------------------------------------- coverage ---

def test_coverage():
    # triangle with screen verts (0,0), (8,0), (0,8) on an 8x8 frame:
    # pixel centers (x+.5, y+.5) are covered exactly where x + y <= 7
    cam = _px_cam()
    face = bim.Face([(-4.0, 0.0, 4.0), (4.0, 0.0, 4.0), (-4.0, 0.0, -4.0)])
    fr = raster.render([face], cam, 8, 8, BG)
    exp = np.array([[x + y <= 7 for x in range(8)] for y in range(8)])
    assert np.array_equal(fr.fid >= 0, exp), fr.fid
    # background pixels keep the bg color and -inf depth
    assert np.all(fr.rgb[~exp] == raster.hex_rgb(BG))
    assert np.all(np.isneginf(fr.invz[~exp]))
    # empty scene and degenerate sizes render pure background
    fr0 = raster.render([], cam, 8, 8, BG)
    assert np.all(fr0.fid == -1)
    assert raster.render([face], cam, 1, 1, BG).fid.shape == (1, 1)


def test_no_cracks():
    # a quad fans into two triangles; the shared diagonal must not leak a
    # single background pixel (inclusive fill rule, never mixed)
    cam = _px_cam()
    quad = bim.Face([(-3.0, 0.0, 3.0), (3.0, 0.0, 3.0),
                     (3.0, 0.0, -3.0), (-3.0, 0.0, -3.0)])
    fr = raster.render([quad], cam, 8, 8, BG)
    inside = fr.fid[1:7, 1:7]                       # strictly interior pixels
    assert np.all(inside == 0), fr.fid


def test_two_sided():
    # wall quads are open, unoriented surfaces: same wall must render from
    # both sides (no backface culling, ever)
    wall = bim.wall_faces([((-3.0, 0.0), (3.0, 0.0))], 5.0)[0]
    for yaw in (0.0, 180.0):
        cam = bim.Camera(yaw=yaw, pitch=0.0, dist=20.0,
                         target=(0.0, 0.0, 2.5), ortho=True)
        fr = raster.render([wall], cam, 64, 48, BG)
        assert (fr.fid >= 0).sum() > 100, (yaw, (fr.fid >= 0).sum())


# ------------------------------------------------- z-buffer beats painter ---

def test_zbuffer_beats_painter():
    # two wall quads crossing in an X (plan view): left half shows A nearer,
    # right half shows B — per-pixel depth resolves it, centroid sort cannot
    w, h = 200, 150
    A = bim.wall_faces([((-5.0, -2.0), (5.0, 2.0))], 6.0, color="#cc2222")[0]
    B = bim.wall_faces([((-5.0, 2.0), (5.0, -2.0))], 6.0, color="#2244cc")[0]
    cam = bim.Camera(yaw=0.0, pitch=0.0, dist=30.0, target=(0.0, 0.0, 3.0),
                     ortho=True)
    faces = [A, B]
    fr = raster.render(faces, cam, w, h, BG)
    p = bim.project_points([(-4.5, 0.0, 3.0), (4.5, 0.0, 3.0)], cam, w, h)
    px1 = (int(p[0, 1]), int(p[0, 0]))              # A nearer here
    px2 = (int(p[1, 1]), int(p[1, 0]))              # B nearer here
    assert fr.fid[px1] == 0, fr.fid[px1]
    assert fr.fid[px2] == 1, fr.fid[px2]

    # a centroid-sorted painter emulation of the same scene gets one wrong —
    # the documented reason this module exists
    depths = []
    for f in faces:
        scr = bim.project_points(f.pts, cam, w, h)
        depths.append(float(scr[:, 2].mean()))
    order = sorted(range(len(faces)), key=lambda i: -depths[i])  # far first
    painted = np.full((h, w), -1, np.int32)
    for i in order:
        m = raster.render([faces[i]], cam, w, h, BG).fid >= 0
        painted[m] = i
    assert painted[px1] != 0 or painted[px2] != 1, "painter emulation won?"


def test_invz_interpolation():
    # large glancing ground quad + small nearer post under perspective:
    # a z-lerp (instead of 1/z) implementation loses the post's pixels
    w, h = 320, 240
    ground = bim.Face([(-60.0, -60.0, 0.0), (60.0, -60.0, 0.0),
                       (60.0, 60.0, 0.0), (-60.0, 60.0, 0.0)],
                      color="#556655")
    post = bim.Face([(-0.5, -15.0, 0.0), (0.5, -15.0, 0.0),
                     (0.5, -15.0, 2.0), (-0.5, -15.0, 2.0)],
                    color="#cc2222")
    cam = bim.Camera(yaw=0.0, pitch=8.0, dist=25.0, target=(0.0, 0.0, 0.0))
    fr = raster.render([ground, post], cam, w, h, BG)
    p = bim.project_points([(0.0, -15.0, 1.0)], cam, w, h)
    assert p[0, 2] > 0
    probe = (int(p[0, 1]), int(p[0, 0]))
    assert fr.fid[probe] == 1, fr.fid[probe]        # the post survives


def test_depth_tie_deterministic():
    # two identical coplanar quads, different colors: strict-> depth test +
    # fixed draw order means the FIRST face wins every tied pixel, always
    cam = _px_cam(64, 64, 10.0)
    pts = [(-3.0, 0.0, 3.0), (3.0, 0.0, 3.0),
           (3.0, 0.0, -3.0), (-3.0, 0.0, -3.0)]
    a = bim.Face(list(pts), color="#cc2222")
    b = bim.Face(list(pts), color="#2244cc")
    f1 = raster.render([a, b], cam, 64, 64, BG)
    covered = f1.fid >= 0
    assert covered.any()
    assert np.all(f1.fid[covered] == 0), "z-tie must go to the first face"
    f2 = raster.render([a, b], cam, 64, 64, BG)
    assert f1.rgb.tobytes() == f2.rgb.tobytes()     # determinism


# ---------------------------------------------------------- near-plane clip ---

def test_near_clip():
    # unit triage on the clipper: 1 front vertex -> 1 triangle, 2 -> 2,
    # all-front untouched, all-behind dropped (camera-space z = index 2)
    fx = np.array([0], np.int32)
    one = np.array([[[0.0, 0.0, 1.0], [0.0, 0.0, -1.0], [1.0, 0.0, -1.0]]])
    t, f = raster._clip_near(one, fx, 0.05)
    assert len(t) == 1 and np.all(t[:, :, 2] >= 0.05 - 1e-12)
    two = np.array([[[0.0, 0.0, 1.0], [1.0, 0.0, 1.0], [1.0, 0.0, -1.0]]])
    t, f = raster._clip_near(two, fx, 0.05)
    assert len(t) == 2 and np.all(f == 0) and np.all(t[:, :, 2] >= 0.049)
    front = np.array([[[0.0, 0.0, 1.0], [1.0, 0.0, 1.0], [1.0, 0.0, 2.0]]])
    t, f = raster._clip_near(front, fx, 0.05)
    assert len(t) == 1 and np.allclose(t, front)
    behind = np.array([[[0.0, 0.0, -1.0], [1.0, 0.0, -1.0], [1.0, 0.0, -2.0]]])
    t, f = raster._clip_near(behind, fx, 0.05)
    assert len(t) == 0

    # a scene entirely behind the camera renders pure background
    cam = bim.Camera(yaw=0.0, pitch=0.0, dist=10.0, target=(0.0, 0.0, 0.0))
    wall = bim.Face([(-2.0, -20.0, 0.0), (2.0, -20.0, 0.0),
                     (2.0, -20.0, 4.0), (-2.0, -20.0, 4.0)])
    fr = raster.render([wall], cam, 320, 240, BG)
    assert np.all(fr.fid == -1) and np.all(fr.rgb == raster.hex_rgb(BG))

    # a straddling triangle renders ONLY its front wedge: apex visible,
    # nothing below the apex row (the clipped edges run up and off-screen),
    # no full-frame smear from behind-camera vertices
    tri = bim.Face([(0.0, -9.0, 0.0),
                    (-1.0, -10.05, 0.3), (1.0, -10.05, 0.3)])
    fr = raster.render([tri], cam, 320, 240, BG)
    assert (fr.fid >= 0).any()
    assert (fr.fid[117:122, 157:164] >= 0).any(), "apex pixel lost"
    assert not (fr.fid[123:, :] >= 0).any(), "smear below the apex"


# ----------------------------------------------------------------- shading ---

def _hex_mix(c1, c2, t):
    """The historical painter mix formula, replicated independently."""
    def rgb(cc):
        s = cc.lstrip("#")
        if len(s) == 3:
            s = "".join(ch * 2 for ch in s)
        n = int(s, 16)
        return ((n >> 16) & 255, (n >> 8) & 255, n & 255)
    a, b = rgb(c1), rgb(c2)
    return "#%02x%02x%02x" % tuple(
        max(0, min(255, int(round(a[i] + (b[i] - a[i]) * t))))
        for i in range(3))


def test_shade_parity():
    # raster.shade IS the painter formula (same buckets, same rounding)
    for color in ("#8f9aa8", "#22a55e", "#c98f2e", "#fff", "#000000"):
        for lamb in range(13):
            exp = _hex_mix(color, BG, 0.12 + 0.5 * (1.0 - lamb / 12.0))
            got = "#%02x%02x%02x" % raster.shade(color, lamb, BG)
            assert got == exp, (color, lamb, got, exp)
    # lambert buckets: facing the light = 12, perpendicular = 0, two-sided
    assert raster.lambert_bucket(raster.LIGHT) == 12
    assert raster.lambert_bucket(-raster.LIGHT) == 12
    perp = np.cross(raster.LIGHT, [0.0, 0.0, 1.0])
    assert raster.lambert_bucket(perp) == 0
    assert raster.lambert_bucket((0.0, 0.0, 0.0)) == 6      # degenerate
    # mix_rgb accepts hex and tuples alike
    assert raster.mix_rgb((255, 0, 0), "#0000ff", 0.5) == (128, 0, 128)


def test_outline_mask():
    cam = _px_cam(64, 64, 10.0)
    wall = bim.Face([(-3.0, 0.0, 3.0), (3.0, 0.0, 3.0),
                     (3.0, 0.0, -3.0), (-3.0, 0.0, -3.0)])
    fr = raster.render([wall], cam, 64, 64, BG)
    m = raster.outline_mask(fr)
    assert m.any(), "silhouette against background missing"
    inside = fr.fid == 0
    assert not m[inside & np.roll(inside, 1, 0) & np.roll(inside, 1, 1)
                 & np.roll(inside, -1, 0) & np.roll(inside, -1, 1)].any(), \
        "outline leaked into the face interior"
    # soft faces (the GUI's ground grid) never outline on their own …
    grid = bim.Face([(-8.0, 0.0, -5.0), (8.0, 0.0, -5.0),
                     (8.0, 0.0, -5.5), (-8.0, 0.0, -5.5)])
    fr3 = raster.render([grid], cam, 64, 64, BG)
    assert not raster.outline_mask(fr3, soft_from=0).any(), \
        "soft face outlined against background"
    # … but a real face above them still gets its silhouette
    fr2 = raster.render([wall, grid], cam, 64, 64, BG)
    assert raster.outline_mask(fr2, soft_from=1).any()


# ------------------------------------------------------------------ golden ---

def _golden_scene():
    faces = bim.wall_faces([((-8.0, 0.0), (8.0, 0.0))], 9.0,
                           color="#9aab9e")
    faces += bim.wall_faces([((-5.0, -5.0), (5.0, 5.0))], 9.0,
                            color="#8f9aa8")
    faces += bim.tube_faces((-6.0, -3.0, 4.0), (6.0, 3.0, 5.0), 0.8,
                            color="#22a55e")
    cam = bim.Camera(yaw=0.0, pitch=0.0, dist=40.0, target=(0.0, 0.0, 4.0),
                     ortho=True)
    return faces, cam


def test_golden():
    faces, cam = _golden_scene()
    fr = raster.render(faces, cam, 320, 240, BG)
    hh = hashlib.sha256(fr.rgb.tobytes()).hexdigest()
    if os.environ.get("PLOOM_REGOLD"):
        print("   REGOLD raster golden:", hh)
        return
    assert hh == GOLDEN, hh
    # the two walls interpenetrate at x=0: both ids must be visible
    seen = set(np.unique(fr.fid))
    assert 0 in seen and 1 in seen, seen


def test_perf_tripwire():
    # catches an accidental per-pixel Python loop, nothing tighter
    segs = [((float(i * 3 - 30), -10.0), (float(i * 3 - 30), 10.0))
            for i in range(20)]
    faces = bim.wall_faces(segs, 9.0, floors=3)
    faces += bim.tube_faces((-30.0, 0.0, 5.0), (30.0, 0.0, 6.0), 0.5)
    cam = bim.Camera(dist=90.0, target=(0.0, 0.0, 10.0))
    t0 = time.perf_counter()
    fr = raster.render(faces, cam, 320, 240, BG)
    dt = time.perf_counter() - t0
    assert (fr.fid >= 0).any()
    assert dt < 2.0, f"{dt:.2f}s for {len(faces)} faces"


# ------------------------------------------------------------------ runner ---

def main():
    test_coverage()
    print("PASS pixel-center coverage + empty/degenerate")
    test_no_cracks()
    print("PASS no cracks across the fan diagonal")
    test_two_sided()
    print("PASS two-sided walls (no backface culling)")
    test_zbuffer_beats_painter()
    print("PASS interpenetrating quads: z-buffer right, painter provably not")
    test_invz_interpolation()
    print("PASS 1/z interpolation (glancing quad vs near post)")
    test_depth_tie_deterministic()
    print("PASS depth-tie determinism (first-drawn wins)")
    test_near_clip()
    print("PASS near-plane clip (1->1, 2->2 tris; no smear; behind = empty)")
    test_shade_parity()
    print("PASS painter-parity shading (buckets, rounding, two-sided)")
    test_outline_mask()
    print("PASS outline mask (silhouette yes, interior no, soft grid quiet)")
    test_golden()
    print("PASS golden image hash")
    test_perf_tripwire()
    print("PASS perf tripwire")
    print("RASTER TESTS PASSED")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("RASTER TEST FAILED:", e)
        sys.exit(1)
