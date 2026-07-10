"""The Draw-In (ifclite) — IFC/STEP import: parser, units, placement,
sweeps, mapped items, and the coverage-report contract.

Every fixture is a hand-authored IFC string (no binary blobs, no network),
so each assert pins an exactly-known vertex.  The acceptance list follows
BUILDOUT_PLAN Appendix E / the Track-5 dossier: exact vertices in feet,
IFC2X3/IFC4 schema tolerance, rotation + nested placements + Gram-Schmidt,
the unit matrix (milli / metre / conversion-based FOOT), an L-shaped
polyline profile, coverage honesty (imported + skipped == candidates),
grammar torture, zero-usable error, and determinism.

Run:  python3.12 tests/test_ifclite.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                    # noqa: E402

from rfi_stamper import ifclite                       # noqa: E402

_N = [0]


def A(cond, msg=""):
    _N[0] += 1
    assert cond, msg


def _quiet(*a, **k):
    pass


_TMP = tempfile.mkdtemp(prefix="ifclite_test_")


def _load(body, schema="IFC4", name="fix.ifc", **kw):
    text = (f"ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION((''),'2;1');\n"
            f"FILE_NAME('','2026-01-01T00:00:00',(''),(''),'','','');\n"
            f"FILE_SCHEMA(('{schema}'));\nENDSEC;\nDATA;\n{body}\n"
            f"ENDSEC;\nEND-ISO-10303-21;\n")
    path = os.path.join(_TMP, name)
    with open(path, "w", encoding="latin-1") as fh:
        fh.write(text)
    return ifclite.load_ifc(path, log=_quiet, **kw)


_FT = 0.3048

# shared prelude: project + MILLI units + model context (identity WCS)
_PRE = """#1=IFCPROJECT('0000000000000000000001',$,'Proj',$,$,$,$,(#20),#7);
#7=IFCUNITASSIGNMENT((#8));
#8=IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);
#20=IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,1.0E-005,#21,$);
#21=IFCAXIS2PLACEMENT3D(#22,$,$);
#22=IFCCARTESIANPOINT((0.,0.,0.));
"""

# one 4000 x 200 x 3000 mm wall at (1000, 2000, 0), identity rotation
_WALL_GEO = """#30=IFCLOCALPLACEMENT($,#31);
#31=IFCAXIS2PLACEMENT3D(#32,$,$);
#32=IFCCARTESIANPOINT((1000.,2000.,0.));
#40=IFCRECTANGLEPROFILEDEF(.AREA.,$,#41,4000.,200.);
#41=IFCAXIS2PLACEMENT2D(#42,$);
#42=IFCCARTESIANPOINT((0.,0.));
#43=IFCDIRECTION((0.,0.,1.));
#45=IFCEXTRUDEDAREASOLID(#40,$,#43,3000.);
#50=IFCSHAPEREPRESENTATION(#20,'Body','SweptSolid',(#45));
#51=IFCPRODUCTDEFINITIONSHAPE($,$,(#50));
"""

_WALL4 = _PRE + _WALL_GEO + \
    "#60=IFCWALL('0000000000000000000002',$,'W1',$,$,#30,#51,$,$);\n"


def _verts(model):
    """Sorted unique vertex tuples across the model's faces."""
    pts = set()
    for f in model.faces:
        pts.update(tuple(round(c, 9) for c in p) for p in f.pts)
    return sorted(pts)


# --------------------------------------------------------------------------- #
#  1. one IFC4 wall — exact vertices in feet                                   #
# --------------------------------------------------------------------------- #

def test_one_wall_exact():
    model, rep = _load(_WALL4)
    A(rep["imported"] == {"walls": 1, "slabs": 0, "columns": 0},
      f"one wall imported, got {rep['imported']}")
    A(rep["schema"] == "IFC4", rep["schema"])
    A(abs(rep["unit_scale"] - 0.001 / _FT) < 1e-15,
      f"MILLI->ft scale, got {rep['unit_scale']}")
    A(len(model.faces) == 6, f"6 faces (2 caps + 4 sides), got {len(model.faces)}")
    A(len(model.segments) == 12,
      f"12 deduped edges, got {len(model.segments)}")
    xs = sorted({p[0] for p in _verts(model)})
    ys = sorted({p[1] for p in _verts(model)})
    zs = sorted({p[2] for p in _verts(model)})
    for got, want in ((xs, (-1.0 / _FT, 3.0 / _FT)),
                      (ys, (1.9 / _FT, 2.1 / _FT)),
                      (zs, (0.0, 3.0 / _FT))):
        A(len(got) == 2 and all(abs(g - w) < 1e-9
                                for g, w in zip(got, want)),
          f"exact vertices: got {got}, want {want}")
    A(model.systems == [("walls", "#9aab9e")], model.systems)
    # the report keys are a frozen contract (GUI + tests consume them)
    A(sorted(rep) == ["imported", "schema", "skipped", "storeys",
                      "target_unit", "unit_scale", "unsupported_counts",
                      "warnings"], sorted(rep))
    A(rep["skipped"] == [] and rep["unsupported_counts"] == {}, rep)


# --------------------------------------------------------------------------- #
#  2. the same wall as IFC2X3 — schema tolerance                              #
# --------------------------------------------------------------------------- #

def test_ifc2x3_tolerance():
    body = _PRE + """#30=IFCLOCALPLACEMENT($,#31);
#31=IFCAXIS2PLACEMENT3D(#32,$,$);
#32=IFCCARTESIANPOINT((1000.,2000.,0.));
#40=IFCRECTANGLEPROFILEDEF(.AREA.,$,#41,4000.,200.);
#41=IFCAXIS2PLACEMENT2D(#42,$);
#42=IFCCARTESIANPOINT((0.,0.));
#43=IFCDIRECTION((0.,0.,1.));
#44=IFCAXIS2PLACEMENT3D(#22,$,$);
#45=IFCEXTRUDEDAREASOLID(#40,#44,#43,3000.);
#50=IFCSHAPEREPRESENTATION(#20,'Body','SweptSolid',(#45));
#51=IFCPRODUCTDEFINITIONSHAPE($,$,(#50));
#60=IFCWALLSTANDARDCASE('0000000000000000000002',$,'W1',$,$,#30,#51,$);
"""
    m4, _ = _load(_WALL4)
    m3, rep = _load(body, schema="IFC2X3", name="fix2x3.ifc")
    A(rep["schema"] == "IFC2X3", rep["schema"])
    A(rep["imported"]["walls"] == 1, rep["imported"])
    A(_verts(m3) == _verts(m4), "IFC2X3 geometry byte-identical to IFC4")


# --------------------------------------------------------------------------- #
#  3. rotation — RefDirection (0,1,0) puts profile X on world +Y              #
# --------------------------------------------------------------------------- #

def test_rotation():
    body = _WALL4.replace(
        "#31=IFCAXIS2PLACEMENT3D(#32,$,$);",
        "#31=IFCAXIS2PLACEMENT3D(#32,$,#33);\n#33=IFCDIRECTION((0.,1.,0.));")
    model, _ = _load(body, name="rot.ifc")
    xs = sorted({p[0] for p in _verts(model)})
    ys = sorted({p[1] for p in _verts(model)})
    for got, want in ((xs, (0.9 / _FT, 1.1 / _FT)),
                      (ys, (0.0, 4.0 / _FT))):
        A(len(got) == 2 and all(abs(g - w) < 1e-9
                                for g, w in zip(got, want)),
          f"rotated corners: got {got}, want {want}")


# --------------------------------------------------------------------------- #
#  4. nested placement chain + a tilted Axis (the Gram-Schmidt path)          #
# --------------------------------------------------------------------------- #

def test_nested_placement():
    body = _PRE + """#100=IFCLOCALPLACEMENT($,#101);
#101=IFCAXIS2PLACEMENT3D(#102,$,$);
#102=IFCCARTESIANPOINT((10000.,0.,0.));
#110=IFCLOCALPLACEMENT(#100,#101);
#120=IFCLOCALPLACEMENT(#110,#101);
#30=IFCLOCALPLACEMENT(#120,#31);
#31=IFCAXIS2PLACEMENT3D(#32,$,$);
#32=IFCCARTESIANPOINT((1000.,2000.,0.));
""" + _WALL_GEO.split("#32=IFCCARTESIANPOINT((1000.,2000.,0.));\n")[1] + \
        "#60=IFCWALL('0000000000000000000002',$,'W1',$,$,#30,#51,$,$);\n"
    model, _ = _load(body, name="nest.ifc")
    xs = sorted({p[0] for p in _verts(model)})
    A(all(abs(g - w) < 1e-9 for g, w in
          zip(xs, (29.0 / _FT, 33.0 / _FT))),
      f"3 x 10 m chain composes: got {xs}")

    # tilted Axis (1,0,1) with non-perpendicular RefDirection (1,0,0):
    # Gram-Schmidt gives Z=(1,0,1)/sqrt2, X=(1,0,-1)/sqrt2, Y=(0,1,0) — the
    # profile's 4 m X-extent AND the 3 m extrusion both tilt, so the world
    # z-span is (4+3)/sqrt(2) metres
    tilt = _WALL4.replace(
        "#31=IFCAXIS2PLACEMENT3D(#32,$,$);",
        "#31=IFCAXIS2PLACEMENT3D(#32,#34,#35);\n"
        "#34=IFCDIRECTION((1.,0.,1.));\n#35=IFCDIRECTION((1.,0.,0.));")
    model2, _ = _load(tilt, name="tilt.ifc")
    zs = sorted({p[2] for p in _verts(model2)})
    span = zs[-1] - zs[0]
    want = (7.0 / np.sqrt(2.0)) / _FT
    A(abs(span - want) < 1e-9, f"tilted-axis z-span {span} != {want}")


# --------------------------------------------------------------------------- #
#  5. the unit matrix — metre vs conversion-based FOOT, and no units at all   #
# --------------------------------------------------------------------------- #

_GEO_M = """#30=IFCLOCALPLACEMENT($,#31);
#31=IFCAXIS2PLACEMENT3D(#32,$,$);
#32=IFCCARTESIANPOINT((0.6096,0.,0.));
#40=IFCRECTANGLEPROFILEDEF(.AREA.,$,#41,6.096,0.3048);
#41=IFCAXIS2PLACEMENT2D(#42,$);
#42=IFCCARTESIANPOINT((0.,0.));
#43=IFCDIRECTION((0.,0.,1.));
#45=IFCEXTRUDEDAREASOLID(#40,$,#43,3.048);
#50=IFCSHAPEREPRESENTATION(#20,'Body','SweptSolid',(#45));
#51=IFCPRODUCTDEFINITIONSHAPE($,$,(#50));
#60=IFCWALL('0000000000000000000002',$,'W1',$,$,#30,#51,$,$);
"""


def test_unit_matrix():
    metre = _PRE.replace(
        "#8=IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);",
        "#8=IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.);") + _GEO_M
    foot = _PRE.replace(
        "#8=IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);",
        "#8=IFCCONVERSIONBASEDUNIT(#9,.LENGTHUNIT.,'FOOT',#10);\n"
        "#9=IFCDIMENSIONALEXPONENTS(1,0,0,0,0,0,0);\n"
        "#10=IFCMEASUREWITHUNIT(IFCRATIOMEASURE(0.3048),#11);\n"
        "#11=IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.);") + _GEO_M.replace(
        "((0.6096,0.,0.))", "((2.,0.,0.))").replace(
        "6.096,0.3048", "20.,1.").replace("#43,3.048", "#43,10.")
    mm, repm = _load(metre, name="metre.ifc")
    mf, repf = _load(foot, name="foot.ifc")
    A(abs(repm["unit_scale"] - 1.0 / _FT) < 1e-12, repm["unit_scale"])
    A(abs(repf["unit_scale"] - 1.0) < 1e-12, repf["unit_scale"])
    va, vb = _verts(mm), _verts(mf)
    A(len(va) == len(vb) == 8, (len(va), len(vb)))
    A(all(abs(a[k] - b[k]) < 1e-9 for a, b in zip(va, vb) for k in range(3)),
      "metre and FOOT files describe the identical wall")

    # missing unit block -> metres + a loud warning, never a crash
    nounits = _WALL4.replace(_PRE, _PRE.replace(
        "#1=IFCPROJECT('0000000000000000000001',$,'Proj',$,$,$,$,(#20),#7);\n",
        "").replace("#7=IFCUNITASSIGNMENT((#8));\n", "").replace(
        "#8=IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);\n", ""))
    m0, rep0 = _load(nounits, name="nounits.ifc")
    A(any("assuming metres" in w for w in rep0["warnings"]), rep0["warnings"])
    A(abs(rep0["unit_scale"] - 1.0 / _FT) < 1e-12, rep0["unit_scale"])


# --------------------------------------------------------------------------- #
#  6. L-shaped polyline slab (repeated closing point dropped)                 #
# --------------------------------------------------------------------------- #

def test_l_slab():
    body = _PRE + """#30=IFCLOCALPLACEMENT($,#31);
#31=IFCAXIS2PLACEMENT3D(#22,$,$);
#40=IFCARBITRARYCLOSEDPROFILEDEF(.AREA.,$,#46);
#46=IFCPOLYLINE((#401,#402,#403,#404,#405,#406,#407));
#401=IFCCARTESIANPOINT((0.,0.));
#402=IFCCARTESIANPOINT((4000.,0.));
#403=IFCCARTESIANPOINT((4000.,2000.));
#404=IFCCARTESIANPOINT((2000.,2000.));
#405=IFCCARTESIANPOINT((2000.,4000.));
#406=IFCCARTESIANPOINT((0.,4000.));
#407=IFCCARTESIANPOINT((0.,0.));
#43=IFCDIRECTION((0.,0.,1.));
#45=IFCEXTRUDEDAREASOLID(#40,$,#43,150.);
#50=IFCSHAPEREPRESENTATION(#20,'Body','SweptSolid',(#45));
#51=IFCPRODUCTDEFINITIONSHAPE($,$,(#50));
#60=IFCSLAB('0000000000000000000003',$,'S1',$,$,#30,#51,$,$);
"""
    model, rep = _load(body, name="lslab.ifc")
    A(rep["imported"] == {"walls": 0, "slabs": 1, "columns": 0},
      rep["imported"])
    A(len(model.faces) == 8, f"2 caps + 6 sides, got {len(model.faces)}")
    A(len(model.segments) == 18, f"18 deduped edges, got {len(model.segments)}")
    for f in model.faces[2:]:           # no degenerate zero-length side quad
        a, b = np.array(f.pts[0]), np.array(f.pts[1])
        A(float(np.linalg.norm(b - a)) > 1e-9, "degenerate side quad")
    vs = _verts(model)
    A((round(2.0 / _FT, 9), round(4.0 / _FT, 9), round(0.15 / _FT, 9)) in vs,
      "the L's inner corner lands exactly")


# --------------------------------------------------------------------------- #
#  7. coverage contract — imported + skipped == candidates                    #
# --------------------------------------------------------------------------- #

def test_coverage_contract():
    unknowns = "".join(f"#{900 + k}=IFCTHING{k}($);\n" for k in range(20))
    body = _WALL4 + unknowns + """#70=IFCBOOLEANCLIPPINGRESULT($,$,$);
#71=IFCSHAPEREPRESENTATION(#20,'Body','Clipping',(#70));
#72=IFCPRODUCTDEFINITIONSHAPE($,$,(#71));
#73=IFCWALL('0000000000000000000004',$,'W2',$,$,#30,#72,$,$);
#80=IFCFLOWSEGMENT('0000000000000000000005',$,'P1',$,$,#30,#51,$);
"""
    model, rep = _load(body, name="cover.ifc")
    A(rep["imported"] == {"walls": 1, "slabs": 0, "columns": 0},
      rep["imported"])
    A(rep["skipped"] == [(73, "IFCWALL",
                          "body item IFCBOOLEANCLIPPINGRESULT not supported")],
      rep["skipped"])
    A(rep["unsupported_counts"] == {"IFCBOOLEANCLIPPINGRESULT": 1},
      rep["unsupported_counts"])
    n_cand = sum(rep["imported"].values()) + len(rep["skipped"])
    A(n_cand == 2, f"imported + skipped == candidates, got {n_cand}")


# --------------------------------------------------------------------------- #
#  8. grammar torture                                                          #
# --------------------------------------------------------------------------- #

def test_grammar_torture():
    body = _WALL4 + """#90=IFCWALL('0000000000000000000006',$,'a;b''c(#5)',
$,$,
#30,#91,$,$);
#91=IFCPRODUCTDEFINITIONSHAPE($,/* mid-record comment */$,(#92));
#92=IFCSHAPEREPRESENTATION(#20,'\\X2\\00E9\\X0\\','SweptSolid',(#93));
#93=IFCEXTRUDEDAREASOLID(#40,$,#43,1.0E-005);
"""
    path = os.path.join(_TMP, "torture.ifc")
    with open(path, "w", encoding="latin-1") as fh:
        fh.write("ISO-10303-21;\nHEADER;\nFILE_SCHEMA(('IFC4'));\nENDSEC;\n"
                 "DATA;\n" + body + "ENDSEC;\nEND-ISO-10303-21;\n")
    with open(path, encoding="latin-1") as fh:
        text = fh.read()
    idx = ifclite._scan_records(text)
    F = ifclite._File(text, idx)
    A(F.args(90)[2] == "a;b'c(#5)",
      f"string with ; '' ( # survives: {F.args(90)[2]!r}")
    A(F.args(92)[1] == "é", f"\\X2\\ decodes: {F.args(92)[1]!r}")
    A(F.args(91)[2] == [ifclite.Ref(92)], "comment mid-record skipped")
    A(F.args(93)[3] == 1.0e-5, "1.0E-005 parses")
    A(F.args(8)[0] == "*", "IFCSIUNIT's derived * parses")
    A(F.type_of(90) == "IFCWALL", "3-line record indexed")
    # forward reference: #90 (defined above) references #91..#93 lazily — and
    # the whole file still loads through the public API
    model, rep = ifclite.load_ifc(path, log=_quiet)
    A(rep["imported"]["walls"] == 2, rep["imported"])


# --------------------------------------------------------------------------- #
#  9. zero-usable file -> ValueError with the skip summary                    #
# --------------------------------------------------------------------------- #

def test_zero_usable():
    body = _WALL4.replace("'Body'", "'Axis'")     # only an Axis rep: no Body
    try:
        _load(body, name="zero.ifc")
        A(False, "zero-usable import must raise")
    except ValueError as e:
        A("0 of 1 products imported" in str(e), str(e))
        A("no 'Body' representation" in str(e),
          f"skip summary in the message: {e}")


# --------------------------------------------------------------------------- #
#  10. mapped item, circle column, indexed polycurve, ifczip                   #
# --------------------------------------------------------------------------- #

def test_mapped_item():
    body = _PRE + _WALL_GEO + """#70=IFCREPRESENTATIONMAP(#21,#50);
#73=IFCCARTESIANTRANSFORMATIONOPERATOR3D($,$,#74,$,$);
#74=IFCCARTESIANPOINT((500.,0.,0.));
#75=IFCMAPPEDITEM(#70,#73);
#76=IFCSHAPEREPRESENTATION(#20,'Body','MappedRepresentation',(#75));
#77=IFCPRODUCTDEFINITIONSHAPE($,$,(#76));
#60=IFCWALL('0000000000000000000002',$,'W1',$,$,#30,#77,$,$);
"""
    direct, _ = _load(_WALL4)
    mapped, rep = _load(body, name="mapped.ifc")
    A(rep["imported"]["walls"] == 1, rep["imported"])
    va = _verts(direct)
    vb = _verts(mapped)
    dx = 0.5 / _FT
    A(all(abs(b[0] - a[0] - dx) < 1e-9 and abs(b[1] - a[1]) < 1e-9
          and abs(b[2] - a[2]) < 1e-9 for a, b in zip(va, vb)),
      "mapped geometry equals direct geometry translated by the operator")


def test_circle_column():
    body = _PRE + """#30=IFCLOCALPLACEMENT($,#31);
#31=IFCAXIS2PLACEMENT3D(#22,$,$);
#40=IFCCIRCLEPROFILEDEF(.AREA.,$,#41,150.);
#41=IFCAXIS2PLACEMENT2D(#42,$);
#42=IFCCARTESIANPOINT((0.,0.));
#43=IFCDIRECTION((0.,0.,1.));
#45=IFCEXTRUDEDAREASOLID(#40,$,#43,3000.);
#50=IFCSHAPEREPRESENTATION(#20,'Body','SweptSolid',(#45));
#51=IFCPRODUCTDEFINITIONSHAPE($,$,(#50));
#60=IFCCOLUMN('0000000000000000000007',$,'C1',$,$,#30,#51,$,$);
"""
    model, rep = _load(body, name="circle.ifc")
    A(rep["imported"]["columns"] == 1, rep["imported"])
    A(len(model.faces) == 18, f"16-gon: 2 caps + 16 sides, got {len(model.faces)}")
    r = 0.15 / _FT
    for p in _verts(model):
        A(abs(np.hypot(p[0], p[1]) - r) < 1e-9, f"vertex off the circle: {p}")


def test_indexed_polycurve():
    body = _PRE + """#30=IFCLOCALPLACEMENT($,#31);
#31=IFCAXIS2PLACEMENT3D(#22,$,$);
#40=IFCARBITRARYCLOSEDPROFILEDEF(.AREA.,$,#46);
#46=IFCINDEXEDPOLYCURVE(#47,(IFCLINEINDEX((1,2)),IFCLINEINDEX((2,3)),IFCLINEINDEX((3,4)),IFCLINEINDEX((4,1))),$);
#47=IFCCARTESIANPOINTLIST2D(((0.,0.),(3000.,0.),(3000.,2000.),(0.,2000.)));
#43=IFCDIRECTION((0.,0.,1.));
#45=IFCEXTRUDEDAREASOLID(#40,$,#43,150.);
#50=IFCSHAPEREPRESENTATION(#20,'Body','SweptSolid',(#45));
#51=IFCPRODUCTDEFINITIONSHAPE($,$,(#50));
#60=IFCSLAB('0000000000000000000008',$,'S2',$,$,#30,#51,$,$);
"""
    model, rep = _load(body, name="ipc.ifc")
    A(rep["imported"]["slabs"] == 1, rep["imported"])
    A(len(model.faces) == 6, f"rectangle slab: 6 faces, got {len(model.faces)}")
    # an arc segment is an honest skip, not a wrong straight line
    arc = body.replace("IFCLINEINDEX((2,3))", "IFCARCINDEX((2,5,3))")
    try:
        _load(arc, name="arc.ifc")
        A(False, "arc profile must not import")
    except ValueError as e:
        A("IFCARCINDEX" in str(e), str(e))


def test_ifczip():
    text = ("ISO-10303-21;\nHEADER;\nFILE_SCHEMA(('IFC4'));\nENDSEC;\n"
            "DATA;\n" + _WALL4 + "ENDSEC;\nEND-ISO-10303-21;\n")
    zpath = os.path.join(_TMP, "model.ifczip")
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("model.ifc", text)
    model, rep = ifclite.load_ifc(zpath, log=_quiet)
    A(rep["imported"]["walls"] == 1, "zip container sniffed by magic bytes")


# --------------------------------------------------------------------------- #
#  11. storeys label + determinism                                             #
# --------------------------------------------------------------------------- #

def test_storeys_and_determinism():
    body = _WALL4 + """#200=IFCBUILDINGSTOREY('0000000000000000000009',$,'LEVEL 1',$,$,#30,$,$,$,0.);
#201=IFCRELCONTAINEDINSPATIALSTRUCTURE('000000000000000000000A',$,$,$,(#60),#200);
"""
    m1, r1 = _load(body, name="st1.ifc")
    m2, r2 = _load(body, name="st2.ifc")
    A(r1["storeys"] == ["LEVEL 1"], r1["storeys"])
    A(r1 == r2, "reports are deterministic")
    A(_verts(m1) == _verts(m2), "vertex streams are deterministic")


# --------------------------------------------------------------------------- #

def main():
    tests = [
        (test_one_wall_exact, "one IFC4 wall: exact feet vertices + contract"),
        (test_ifc2x3_tolerance, "IFC2X3 wall standard case: same geometry"),
        (test_rotation, "RefDirection rotation: exact rotated corners"),
        (test_nested_placement, "nested placements + tilted-axis Gram-Schmidt"),
        (test_unit_matrix, "unit matrix: metre == FOOT file; missing units warn"),
        (test_l_slab, "L-shaped polyline slab: 8 faces, closing point dropped"),
        (test_coverage_contract, "coverage: imported + skipped == candidates"),
        (test_grammar_torture, "grammar torture: strings/comments/*/E-005"),
        (test_zero_usable, "zero-usable file raises with the skip summary"),
        (test_mapped_item, "mapped item resolves through the operator"),
        (test_circle_column, "circle column: 16-gon"),
        (test_indexed_polycurve, "indexed polycurve slab + arc honest skip"),
        (test_ifczip, "ifczip container by magic bytes"),
        (test_storeys_and_determinism, "storeys label + determinism"),
    ]
    for fn, label in tests:
        fn()
        print(f"PASS {label}")
    print(f"IFCLITE TEST PASSED  ({_N[0]} checks)  — the Draw-In")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("IFCLITE TEST FAILED:", e)
        sys.exit(1)
