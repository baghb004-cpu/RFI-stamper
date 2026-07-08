"""Headless tests for the Phase D 3D-uplift engine work (no tk anywhere):

* bim.Face dataclass + Model.faces (additive; constructor + bounds compat)
* bim.wall_faces: quad-per-wall-per-floor counts and z geometry, matching
  extrude.build_model's floor math exactly
* extrude.build_model(faces=True) / draft.to_bim(faces=True): faces filled,
  SEGMENTS byte-identical to faces=False (the compatibility regression pin)
* pipewright.to_bim: Segment.radius = dia_in / 24 (feet), width unchanged
* bim.tube_faces: 8 sides + 2 caps, ring radius, degenerate input
* bim.exaggerate_z: identity at 1x, midpoint invariant, delta scaling

Run:  python3 tests/test_bim_faces.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper import bim                        # noqa: E402
from rfi_stamper.draft import DraftModel, to_bim   # noqa: E402
from rfi_stamper.extrude import build_model        # noqa: E402
from rfi_stamper.pipewright import SYSTEMS         # noqa: E402
from rfi_stamper.pipewright import to_bim as pipes_to_bim  # noqa: E402


def _seg_key(s):
    return (s.a, s.b, s.color, s.width, s.system, s.radius)


# ----------------------------------------------------------- Face / Model ---

def test_face_model_additive():
    # Segment stays constructible the old ways; radius defaults to 0.0
    s = bim.Segment((0, 0, 0), (1, 0, 0))
    assert s.radius == 0.0
    s5 = bim.Segment((0, 0, 0), (1, 0, 0), "#123456", 2.0, "walls")
    assert s5.system == "walls" and s5.radius == 0.0
    # Face defaults
    f = bim.Face(pts=[(0, 0, 0), (1, 0, 0), (1, 0, 5)])
    assert f.color.startswith("#") and f.system == ""
    # Model: faces default empty, old constructor forms still work
    m = bim.Model()
    assert m.faces == []
    m2 = bim.Model(segments=[s])
    assert m2.faces == [] and len(m2.segments) == 1
    # faces count toward bounds (a faces-only model is frameable)
    m.faces.append(f)
    assert m.bounds()[1][2] == 5.0, m.bounds()


# -------------------------------------------------------------- wall_faces --

def test_wall_faces():
    segs = [((0.0, 0.0), (10.0, 0.0)), ((10.0, 0.0), (10.0, 8.0))]
    faces = bim.wall_faces(segs, wall_height=9.0, floors=2, slab_gap=0.8)
    assert len(faces) == 2 * 2, len(faces)          # wall x floor
    for f in faces:
        assert len(f.pts) == 4
        assert f.system == "walls"
    # floor 0 quad: bottom edge z=0, top edge z=9; drawing order a0 b0 b1 a1
    f0 = faces[0]
    assert [p[2] for p in f0.pts] == [0.0, 0.0, 9.0, 9.0], f0.pts
    assert f0.pts[0][:2] == (0.0, 0.0) and f0.pts[1][:2] == (10.0, 0.0)
    assert f0.pts[2][:2] == (10.0, 0.0) and f0.pts[3][:2] == (0.0, 0.0)
    # floor 1 sits one slab gap above floor 0's top (z0 = h + gap)
    f2 = faces[2]
    assert [p[2] for p in f2.pts] == [9.8, 9.8, 18.8, 18.8], f2.pts
    # guard: non-positive height refused, floors clamped like build_model
    try:
        bim.wall_faces(segs, 0.0)
        raise AssertionError("wall_faces accepted wall_height=0")
    except ValueError:
        pass
    assert len(bim.wall_faces(segs, 9.0, floors=0)) == 2   # clamps to 1


# ------------------------------------------- extrude faces regression pin ---

def test_extrude_faces_flag():
    segs = [((0.0, 0.0), (10.0, 0.0)), ((10.0, 0.0), (10.0, 8.0))]
    plain = build_model(segs, wall_height=9.0, floors=2)
    shaded = build_model(segs, wall_height=9.0, floors=2, faces=True)
    # THE compatibility guarantee: segments identical either way
    assert not plain.faces
    assert [_seg_key(s) for s in plain.segments] == \
        [_seg_key(s) for s in shaded.segments]
    assert plain.systems == shaded.systems
    # faces: quad per wall per floor, wall color/system, same z math
    assert len(shaded.faces) == 2 * 2, len(shaded.faces)
    wall_color = plain.segments[0].color
    for f in shaded.faces:
        assert f.color == wall_color and f.system == "walls"
    zs = sorted({p[2] for f in shaded.faces for p in f.pts})
    assert zs == [0.0, 9.0, 9.8, 18.8], zs
    # faces don't stretch bounds past the wireframe (same corner points)
    assert plain.bounds() == shaded.bounds()


def test_draft_to_bim_faces_flag():
    m = DraftModel()
    m.add("wall", [(0, 0), (20, 0)], wtype="stud4")
    m.add("wall", [(20, 0), (20, 12)], wtype="cmu8")
    plain = to_bim(m, wall_height=9.0, floors=2)
    shaded = to_bim(m, wall_height=9.0, floors=2, faces=True)
    # segment pin from test_draft holds on BOTH: 2 walls x 2 edges x 2
    # floors + columns (3 unique corners x 2 floors)
    assert len(plain.segments) == 2 * 2 * 2 + 3 * 2, len(plain.segments)
    assert [_seg_key(s) for s in plain.segments] == \
        [_seg_key(s) for s in shaded.segments]
    assert not plain.faces
    assert len(shaded.faces) == 2 * 2, len(shaded.faces)


# ------------------------------------------------------- pipewright radius --

def test_pipewright_radius():
    m = DraftModel()
    m.add("pipe", [(0, 0), (8, 0), (16, 0)], invert_ft=100.0,
          slope_in_ft=0.125)                        # san, default 4"
    m.add("pipe", [(0, 5), (10, 5)], system="dcw", dia_in=1.5)
    bm = pipes_to_bim(m)
    san = [s for s in bm.segments if s.system == "Sanitary"]
    assert len(san) == 2
    for s in san:
        assert abs(s.radius - 4.0 / 24.0) < 1e-9, s.radius
        assert abs(s.width - max(1.0, 4.0 / 3.0)) < 1e-9   # width unchanged
    dcw = [s for s in bm.segments if s.system == "Domestic cold water"][0]
    assert abs(dcw.radius - 1.5 / 24.0) < 1e-9, dcw.radius
    assert dcw.color == SYSTEMS["dcw"]["color"]


# ------------------------------------------------------------- tube_faces ---

def test_tube_faces():
    faces = bim.tube_faces((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), 1.0,
                           sides=8, color="#1e8449", system="Sanitary")
    assert len(faces) == 8 + 2, len(faces)          # sides + two caps
    quads = faces[:8]
    caps = faces[8:]
    for q in quads:
        assert len(q.pts) == 4
        assert q.color == "#1e8449" and q.system == "Sanitary"
    for cap in caps:
        assert len(cap.pts) == 8
    # every ring vertex sits exactly radius off the axis, at the right end
    for f in faces:
        for (x, y, z) in f.pts:
            assert abs(math.hypot(y, z) - 1.0) < 1e-9, (x, y, z)
            assert abs(x) < 1e-9 or abs(x - 10.0) < 1e-9, x
    # a near-vertical run picks a stable reference (no NaN, right radius)
    vert = bim.tube_faces((0, 0, 0), (0, 0, 5), 0.5)
    assert len(vert) == 10
    for f in vert:
        for (x, y, z) in f.pts:
            assert abs(math.hypot(x, y) - 0.5) < 1e-9
    # degenerate inputs: zero length or radius -> no faces
    assert bim.tube_faces((1, 1, 1), (1, 1, 1), 1.0) == []
    assert bim.tube_faces((0, 0, 0), (1, 0, 0), 0.0) == []


# ----------------------------------------------------------- exaggerate_z ---

def test_exaggerate_z():
    # identity at 1x
    assert bim.exaggerate_z((3.0, 4.0, 7.0), 5.0, 1.0) == (3.0, 4.0, 7.0)
    # z-delta about the midpoint scales; x/y pass through
    assert bim.exaggerate_z((1.0, 2.0, 7.0), 5.0, 3.0) == (1.0, 2.0, 11.0)
    assert bim.exaggerate_z((1.0, 2.0, 4.0), 5.0, 5.0) == (1.0, 2.0, 0.0)
    # the midpoint itself never moves
    assert bim.exaggerate_z((0.0, 0.0, 5.0), 5.0, 10.0)[2] == 5.0
    # a sloped pipe pair keeps its midpoint and multiplies its fall
    a, b = (0.0, 0.0, 100.0), (40.0, 0.0, 99.0)
    mid = (a[2] + b[2]) / 2.0
    a5 = bim.exaggerate_z(a, mid, 5.0)
    b5 = bim.exaggerate_z(b, mid, 5.0)
    assert abs((a5[2] - b5[2]) - 5.0 * (a[2] - b[2])) < 1e-9
    assert abs((a5[2] + b5[2]) / 2.0 - mid) < 1e-9


# ----------------------------------------------------------------- runner ---

def main():
    test_face_model_additive()
    print("PASS Face/Model additive (constructor, radius, bounds)")
    test_wall_faces()
    print("PASS wall_faces counts + floor z math")
    test_extrude_faces_flag()
    print("PASS extrude faces=True (segments identical — regression pin)")
    test_draft_to_bim_faces_flag()
    print("PASS draft.to_bim faces=True (segment pin holds)")
    test_pipewright_radius()
    print("PASS pipewright radius = dia_in/24 ft")
    test_tube_faces()
    print("PASS tube_faces (8 sides + caps, radius, degenerate)")
    test_exaggerate_z()
    print("PASS exaggerate_z math")
    print("BIM FACES TESTS PASSED")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("BIM FACES TEST FAILED:", e)
        sys.exit(1)
