"""Self-contained tests for rfi_stamper.backcheck -- the Backcheck peer
checker.  Plain python, no pytest, no project data.  Exercises:

* the RULES registry shape + the honestly-SKIPPED rules with reasons
* Finding / Report dataclasses: to_dict/from_dict round trip, sort order,
  by_category / by_severity, stats tallies
* a tidy defect-free Loft room -> ZERO geometry/dfx/data findings (no false
  positives), then a defect model firing every Loft rule with the right
  code/category/severity
* pipe checks on a DraftModel with pipe runs (slope-min, no-invert, no-trap,
  unsupported span, drainage dead-end)
* PDF checks (sheet dup, sheet gap, dangling reference, no-scale, vague note,
  missing title-block, conflicting material)
* DXF + OBJ degenerate-geometry checks
* the markup bridge (findings -> valid Markups + a real annotated PDF) and
  loft_finding_points
* the Heartwood lessons lane (record -> untrusted no-fire -> trust -> fire)
* dispatch by type/extension and the rules= filter

Run:  python3.12 tests/test_backcheck.py
"""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from rfi_stamper import backcheck as bc                       # noqa: E402
from rfi_stamper.draft import DraftModel                      # noqa: E402
from rfi_stamper.markups import MARKUP_TYPES                  # noqa: E402

TMP = tempfile.mkdtemp(prefix="backcheck_test_")


def codes(report):
    return sorted({f.code for f in report.findings})


def has(report, code):
    return any(f.code == code for f in report.findings)


def first(report, code):
    return next(f for f in report.findings if f.code == code)


# ------------------------------------------------------------- fixtures ----

def clean_room():
    """A tidy, defect-free 12x10 room: 4 closed walls, a door swinging into
    open floor, two clear fixtures, two dimensions, a labeled room, a plain
    note.  Must produce ZERO findings from the geometry/dfx/data rules."""
    m = DraftModel(title="FLOOR PLAN", number="A-101", scale_ratio=48)
    pts = [(0, 0), (12, 0), (12, 10), (0, 10)]
    wids = []
    for a, b in zip(pts, pts[1:] + pts[:1]):
        wids.append(m.add("wall", [a, b], wtype="stud4").id)
    m.add("door", [], host=wids[0], t=0.45, width_in=36.0, swing="in",
          hand="l")
    m.add("fixture", [(2.0, 9.0)], stencil="lav", rot=180.0)
    m.add("fixture", [(10.0, 9.1)], stencil="wc", rot=180.0)
    m.add("dim", [(0, 0), (12, 0), (6, -1.5)])
    m.add("dim", [(0, 0), (0, 10), (-1.5, 5)])
    m.add("room", [(6, 5)], name="OFFICE", number="101")
    m.add("text", [(6, 2)], text="FLOOR PLAN", size="body")
    return m


def gap_model():
    """Two walls with a 0.15 ft corner gap (fires GEO-OPEN-WALL)."""
    m = DraftModel(title="X", number="X-1")
    m.add("wall", [(0, 0), (10, 0)], wtype="stud4")
    m.add("wall", [(10.0, 0.15), (10, 10)], wtype="stud4")
    return m


# ------------------------------------------------------------- registry ----

def test_registry():
    assert bc.SEVERITIES == ("blocker", "major", "minor", "info")
    assert set(bc.CATEGORIES) == {"data", "ambiguity", "geometry",
                                  "standards", "lessons", "dfx"}
    # every registry entry is well formed
    for code, meta in bc.RULES.items():
        assert meta["category"] in bc.CATEGORIES, code
        assert meta["severity"] in bc.SEVERITIES, code
        assert meta["title"] and meta["rule"], code
        assert isinstance(meta["inputs"], set) and meta["inputs"], code
        assert set(meta["inputs"]) <= {"pdf", "loft", "pipe", "dxf", "obj"}
    # the marquee rules are all present
    for code in ("DATA-SHEETDUP", "DATA-DUPDIM", "AMB-DANGLING-REF",
                 "AMB-VAGUE-NOTE", "GEO-OPEN-WALL", "GEO-OVERLAP",
                 "GEO-DEGENERATE", "STD-SLOPE-MIN", "DFX-PINCH",
                 "DFX-DOOR-SWING", "LES-REPEAT", "GEO-UNSUPPORTED-SPAN"):
        assert code in bc.RULES, code
    # soft-norm rules say "verify" out loud
    for code in ("GEO-SHARP-CORNER", "DFX-PINCH", "STD-TEXT-MIN"):
        assert "verify" in bc.RULES[code]["rule"].lower(), code
    # the honestly-skipped rules each carry a reason
    assert set(bc.SKIPPED_RULES) == {"STD-HOLE-GDT", "STD-SLEEVE",
                                     "DFX-DRAFT-ANGLE"}
    for code, meta in bc.SKIPPED_RULES.items():
        assert meta["reason"] and "Not checked" in meta["reason"], code
        assert meta["inputs"], code
    # LES-REPEAT is registry-only (run out of band)
    assert bc.RULES["LES-REPEAT"]["fn"] is None


# --------------------------------------------------- finding / report ------

def test_finding_report_model():
    f = bc.Finding(id="abc", code="GEO-OPEN-WALL", category="geometry",
                   severity="major", title="t", detail="d", suggestion="s",
                   rule="r", page=3, where=(1.0, 2.0), source="loft",
                   ent_ids=["e1"])
    d = f.to_dict()
    f2 = bc.Finding.from_dict(d)
    assert f2.to_dict() == d
    assert f2.where == (1.0, 2.0) and f2.ent_ids == ["e1"]
    # bbox where round-trips as a 4-tuple
    g = bc.Finding.from_dict({"code": "X", "where": [1, 2, 3, 4]})
    assert g.where == (1, 2, 3, 4)
    assert g.id  # a fresh id is minted when missing

    rep = bc.Report(findings=[
        bc.Finding(id="1", code="A", category="geometry", severity="minor",
                   title="", detail="", suggestion="", rule="", page=2),
        bc.Finding(id="2", code="B", category="data", severity="blocker",
                   title="", detail="", suggestion="", rule="", page=5),
        bc.Finding(id="3", code="C", category="dfx", severity="minor",
                   title="", detail="", suggestion="", rule="", page=1),
    ], source="loft")
    rep.sort()
    # blocker first, then minor ordered by page
    assert [f.severity for f in rep.findings] == ["blocker", "minor", "minor"]
    assert rep.findings[0].code == "B"
    assert [f.page for f in rep.findings[1:]] == [1, 2]
    assert len(rep.by_severity("minor")) == 2
    assert len(rep.by_category("data")) == 1
    assert rep.by_category("data")[0].code == "B"


# -------------------------------------------------- clean model (zero) -----

def test_clean_loft_zero():
    r = bc.check_loft(clean_room())
    assert r.findings == [], [f.code for f in r.findings]
    assert r.source == "loft"
    # the full data/geometry/standards/dfx lanes actually RAN (not vacuous)
    for code in ("DATA-DUPDIM", "GEO-OPEN-WALL", "GEO-OVERLAP",
                 "DFX-DOOR-SWING", "DFX-ROOM-NO-DOOR", "STD-TITLEBLOCK-FIELD"):
        assert code in r.stats["checked"], code
    # stats tallies all zero
    assert sum(r.stats["by_severity"].values()) == 0
    assert sum(r.stats["by_category"].values()) == 0
    # the honestly-skipped rules are surfaced even on a clean pass.
    # STD-SLEEVE graduated to a real rule at v4.13.0 (Clash-Lite): it is
    # skipped only for PDF sources now, so a pipe-less loft neither
    # checks nor skips it.
    skipped = {s["code"] for s in r.stats["skipped"]}
    assert {"STD-HOLE-GDT", "DFX-DRAFT-ANGLE"} <= skipped
    assert "STD-SLEEVE" not in skipped
    assert "STD-SLEEVE" not in r.stats["checked"]
    for s in r.stats["skipped"]:
        assert s["reason"]


# ----------------------------------------------- loft data / ambiguity -----

def test_loft_data_ambiguity():
    m = DraftModel(title="D", number="D-1")
    # contradictory dims over one span (major)
    m.add("dim", [(0, 0), (12, 0), (6, -2)], text="12'-0\"")
    m.add("dim", [(0, 0), (12, 0), (6, 2)], text="12'-6\"")
    # redundant dims over one span (minor)
    m.add("dim", [(0, 30), (10, 30), (5, 28)])
    m.add("dim", [(0, 30), (10, 30), (5, 32)])
    # unlabeled grid + room with no name/number
    m.add("grid", [(0, -5), (0, 5)], label="")
    m.add("room", [(100, 100)], name="", number="")
    # vague text: "by others" (major) + "verify in field" (minor)
    m.add("text", [(5, 5)], text="PATCH BY OTHERS, VERIFY IN FIELD")
    r = bc.check_loft(m)

    dups = [f for f in r.findings if f.code == "DATA-DUPDIM"]
    assert len(dups) == 2
    assert {f.severity for f in dups} == {"major", "minor"}
    assert any("12'-0\"" in f.detail and "12'-6\"" in f.detail for f in dups)
    assert first(r, "DATA-DUPDIM").category == "data"

    unl = [f for f in r.findings if f.code == "AMB-UNLABELED"]
    assert len(unl) == 2
    assert all(f.category == "ambiguity" and f.severity == "minor"
               for f in unl)

    vague = [f for f in r.findings if f.code == "AMB-VAGUE-NOTE"]
    sevs = {f.severity for f in vague}
    assert "major" in sevs and "minor" in sevs
    assert any("by others" in f.detail for f in vague)
    assert all(f.where is not None for f in vague)  # located at the text ent


def test_loft_undim_room():
    m = DraftModel(title="R", number="R-1")
    pts = [(0, 0), (12, 0), (12, 10), (0, 10)]
    for a, b in zip(pts, pts[1:] + pts[:1]):
        m.add("wall", [a, b], wtype="stud4")
    m.add("dim", [(50, 0), (60, 0), (55, -2)])   # a dim, but far from the room
    m.add("room", [(6, 5)], name="OFFICE", number="101")
    r = bc.check_loft(m)
    assert has(r, "AMB-UNDIM-ROOM")
    assert first(r, "AMB-UNDIM-ROOM").severity == "minor"


# ----------------------------------------------------- loft geometry -------

def test_loft_geometry():
    m = DraftModel(title="G", number="G-1")
    # sharp corner: two walls at 20 degrees
    m.add("wall", [(0, 0), (10, 0)], wtype="stud4")
    a = math.radians(20)
    m.add("wall", [(0, 0), (10 * math.cos(a), 10 * math.sin(a))],
          wtype="stud4")
    # open wall near-miss (0.15 ft)
    m.add("wall", [(30, 0), (40, 0)], wtype="stud4")
    m.add("wall", [(40.0, 0.15), (40, 10)], wtype="stud4")
    # a fixture buried in a wall + a fixture-fixture overlap
    m.add("wall", [(60, 0), (70, 0)], wtype="stud4")
    m.add("fixture", [(65, 0)], stencil="wc")
    m.add("fixture", [(80, 0)], stencil="lav")
    m.add("fixture", [(80.2, 0)], stencil="lav")
    # degenerate: a zero-length line
    m.add("line", [(90, 0), (90, 0)])
    r = bc.check_loft(m)

    sharp = first(r, "GEO-SHARP-CORNER")
    assert sharp.category == "geometry" and sharp.severity == "minor"
    assert "20 degree" in sharp.detail

    ow = first(r, "GEO-OPEN-WALL")
    assert ow.severity == "major" and ow.where is not None

    ov = [f for f in r.findings if f.code == "GEO-OVERLAP"]
    assert len(ov) >= 2 and all(f.severity == "major" for f in ov)
    assert any("wall body" in f.detail for f in ov)
    assert any("overlap in plan" in f.detail for f in ov)

    deg = first(r, "GEO-DEGENERATE")
    assert deg.category == "geometry"


# ----------------------------------------------- loft standards / dfx ------

def test_loft_standards_dfx():
    m = DraftModel(title="", number="")   # missing title-block fields
    # thin/sliver wall + a wall thinner than its type minimum
    m.add("wall", [(0, 0), (0.3, 0)], wtype="stud4")
    m.add("wall", [(50, 0), (60, 0)], wtype="cmu8", thick_in=2.0)
    # sub-minimum lettering height
    m.add("text", [(6, 6)], text="fine print", size=0.05)
    # corridor pinch: two parallel walls 2 ft apart
    m.add("wall", [(0, 60), (10, 60)], wtype="stud4")
    m.add("wall", [(0, 62), (10, 62)], wtype="stud4")
    r = bc.check_loft(m)

    tb = first(r, "STD-TITLEBLOCK-FIELD")
    assert tb.category == "standards" and "title" in tb.detail

    thin = [f for f in r.findings if f.code == "DFX-THIN-WALL"]
    assert len(thin) == 2
    assert any("long" in f.detail for f in thin)
    assert any("thick" in f.detail for f in thin)

    tm = first(r, "STD-TEXT-MIN")
    assert tm.severity == "minor" and "0.05" in tm.detail

    pinch = first(r, "DFX-PINCH")
    assert pinch.severity == "major" and pinch.category == "dfx"


def test_door_swing_and_room_no_door():
    # door swings into a fixture
    m = DraftModel(title="D", number="D-1")
    w = m.add("wall", [(0, 0), (10, 0)], wtype="stud4")
    m.add("door", [], host=w.id, t=0.4, width_in=48.0, swing="in", hand="l")
    m.add("fixture", [(4.5, 2.0)], stencil="wc")
    r = bc.check_loft(m)
    ds = first(r, "DFX-DOOR-SWING")
    assert ds.severity == "major" and "swing" in ds.detail.lower()

    # a fully enclosed room with no door
    m2 = DraftModel(title="R", number="R-1")
    pts = [(0, 0), (12, 0), (12, 10), (0, 10)]
    for a, b in zip(pts, pts[1:] + pts[:1]):
        m2.add("wall", [a, b], wtype="stud4")
    m2.add("room", [(6, 5)], name="VAULT", number="102")
    r2 = bc.check_loft(m2)
    assert has(r2, "DFX-ROOM-NO-DOOR")
    assert first(r2, "DFX-ROOM-NO-DOOR").severity == "major"


# ------------------------------------------------------- pipe checks -------

def test_pipe_checks():
    m = DraftModel(title="P", number="P-1")
    # 4" san sloped under the 1/8" minimum
    m.add("pipe", [(0, 0), (20, 0)], invert_ft=100.0, slope_in_ft=0.0625)
    # sloped run with no invert
    m.add("pipe", [(0, 30), (10, 30)], slope_in_ft=0.25)
    # a mop sink (sanitary fixture) with no trap
    m.add("fixture", [(50, 0.4)], stencil="mop")
    m.add("pipe", [(50, 0), (50, -8)])
    # PVC run far over its 4 ft hanger stride
    m.add("pipe", [(0, 60), (30, 60)], material="PVC DWV")
    r = bc.check_loft(m)

    # pipe rules only run because the model has pipes
    assert "STD-SLOPE-MIN" in r.stats["checked"]
    sm = first(r, "STD-SLOPE-MIN")
    assert sm.severity == "major"
    assert "1/8" in sm.detail and "1/16" in sm.detail

    ni = first(r, "STD-NO-INVERT")
    assert ni.severity == "major" and "invert" in ni.detail

    nt = first(r, "STD-NO-TRAP")
    assert nt.severity == "major" and nt.category == "standards"

    span = [f for f in r.findings if f.code == "GEO-UNSUPPORTED-SPAN"]
    assert span and any("30.0 ft" in f.detail for f in span)
    assert all("verify against project spec" in f.rule for f in span)

    assert has(r, "DFX-DEADEND-MAIN")
    assert first(r, "DFX-DEADEND-MAIN").severity == "minor"


def test_pipe_rules_absent_without_pipes():
    # a Loft with no pipe ents never runs (or lists) the pipe rules
    r = bc.check_loft(clean_room())
    for code in ("STD-SLOPE-MIN", "STD-NO-TRAP", "DFX-DEADEND-MAIN"):
        assert code not in r.stats["checked"], code


# -------------------------------------------------------- PDF checks -------

def make_pdf():
    src = os.path.join(TMP, "plan.pdf")
    doc = fitz.open()
    p1 = doc.new_page(width=612, height=792)
    p1.insert_text((560, 760), "A-101", fontsize=10)         # title-block no.
    p1.insert_text((60, 60), 'SCALE: 1/8" = 1\'-0"', fontsize=10)
    p1.insert_text((60, 90), "SEE DETAIL 3/A-999 FOR MORE", fontsize=10)
    p1.insert_text((60, 120), "FINISH TO BE DETERMINED, VERIFY IN FIELD",
                   fontsize=10)
    p1.draw_rect(fitz.Rect(60, 220, 300, 400))
    p2 = doc.new_page(width=612, height=792)
    p2.insert_text((560, 760), "A-101", fontsize=10)         # duplicate sheet
    p2.draw_rect(fitz.Rect(60, 220, 300, 400))               # content, no scale
    p3 = doc.new_page(width=612, height=792)
    p3.insert_text((100, 100), "COVER SHEET", fontsize=20)   # no sheet number
    doc.save(src)
    doc.close()
    return src


def test_pdf_checks():
    src = make_pdf()
    r = bc.check_pdf(src, log=lambda *a: None)
    assert r.source == "pdf"

    dup = first(r, "DATA-SHEETDUP")
    assert dup.severity == "major" and "A-101" in dup.detail

    dr = first(r, "AMB-DANGLING-REF")
    assert dr.severity == "major" and "A-999" in dr.detail
    assert dr.page == 1 and dr.where is not None       # located for markup

    ns = first(r, "AMB-NO-SCALE")
    assert ns.page == 2 and ns.severity == "minor"

    vague = [f for f in r.findings if f.code == "AMB-VAGUE-NOTE"]
    assert vague and any(f.severity == "major" for f in vague)   # "TBD"
    assert all(f.source == "pdf" for f in vague)

    tb = first(r, "DATA-TITLEBLOCK")
    assert tb.page == 3 and tb.category == "data"

    # the mechanical-only rules are skipped WITH a reason, never silent
    skipped = {s["code"]: s["reason"] for s in r.stats["skipped"]}
    assert "STD-HOLE-GDT" in skipped and skipped["STD-HOLE-GDT"]


def test_pdf_material_and_gap():
    src = os.path.join(TMP, "mat.pdf")
    doc = fitz.open()
    p = doc.new_page(width=612, height=792)
    p.insert_text((560, 760), "S-100", fontsize=10)
    p.insert_text((60, 60), "WALL TYPE T1 - CMU", fontsize=10)
    p.insert_text((60, 80), "WALL TYPE T1 - CAST-IN-PLACE CONCRETE",
                  fontsize=10)
    p2 = doc.new_page(width=612, height=792)
    p2.insert_text((560, 760), "S-101", fontsize=10)
    p3 = doc.new_page(width=612, height=792)
    p3.insert_text((560, 760), "S-103", fontsize=10)         # gap at S-102
    doc.save(src)
    doc.close()
    r = bc.check_pdf(src, log=lambda *a: None)
    mat = first(r, "DATA-MATERIAL")
    assert "T1" in mat.detail and "concrete" in mat.detail \
        and "masonry" in mat.detail
    assert mat.severity == "info"
    gap = first(r, "DATA-SHEETGAP")
    assert "S-102" in gap.detail and gap.severity == "info"


# ------------------------------------------------------- DXF / OBJ ---------

def test_dxf_degenerate():
    dxf = os.path.join(TMP, "t.dxf")
    pairs = [
        (0, "SECTION"), (2, "ENTITIES"),
        (0, "LINE"), (8, "0"), (10, "0.0"), (20, "0.0"),
        (11, "10.0"), (21, "0.0"),                        # good line
        (0, "LINE"), (8, "0"), (10, "5.0"), (20, "5.0"),
        (11, "5.0"), (21, "5.0"),                          # zero-length line
        (0, "ENDSEC"), (0, "EOF"),
    ]
    with open(dxf, "w") as f:
        for c, v in pairs:
            f.write(f"{c}\r\n{v}\r\n")
    r = bc.check_dxf(dxf)
    assert r.source == "dxf"
    deg = [f for f in r.findings if f.code == "GEO-DEGENERATE"]
    assert len(deg) == 1 and "Zero-length" in deg[0].detail
    assert any(s["code"] == "STD-HOLE-GDT" for s in r.stats["skipped"])


def test_obj_degenerate():
    obj = os.path.join(TMP, "t.obj")
    with open(obj, "w") as f:
        # one triangle (3 open edges) + a disconnected triangle (2 islands)
        f.write("v 0 0 0\nv 1 0 0\nv 0 1 0\n")
        f.write("v 5 0 0\nv 6 0 0\nv 5 1 0\n")
        f.write("f 1 2 3\nf 4 5 6\n")
    r = bc.check_obj(obj)
    assert r.source == "obj"
    details = " ".join(f.detail for f in r.findings if f.code
                       == "GEO-DEGENERATE")
    assert "open edge" in details
    assert "island" in details or "component" in details


# ------------------------------------------------------- markup bridge -----

def test_markup_bridge():
    src = make_pdf()
    r = bc.check_pdf(src, log=lambda *a: None)
    marks = bc.findings_to_markups(r)
    assert marks, "expected markups from located PDF findings"
    # every markup is valid: known type, in-range page, author, subject
    for mk in marks:
        assert mk.type in MARKUP_TYPES
        assert 1 <= mk.page <= 3
        assert mk.author == "Backcheck"
        assert mk.subject.startswith(("DATA-", "AMB-", "GEO-", "STD-", "DFX-"))
    types = {mk.type for mk in marks}
    assert "cloud" in types and "callout" in types

    out = os.path.join(TMP, "marked.pdf")
    n = bc.write_markup_pdf(r, src, out)
    assert n > 0
    assert os.path.exists(out) and not os.path.exists(out + ".part")
    # pypdf can open it and it carries annotations
    from pypdf import PdfReader
    rd = PdfReader(out)
    assert len(rd.pages) == 3
    doc = fitz.open(out)
    total = sum(len(list(doc[i].annots())) for i in range(doc.page_count))
    doc.close()
    assert total >= 1

    # a report with no located findings still yields a copied-through PDF
    empty = bc.Report(findings=[], source="pdf", stats={})
    out2 = os.path.join(TMP, "copy.pdf")
    assert bc.write_markup_pdf(empty, src, out2) == 0
    assert os.path.exists(out2)


def test_loft_finding_points():
    r = bc.check_loft(gap_model())
    pts = bc.loft_finding_points(r)
    assert pts
    for (x, y, sev, code, detail) in pts:
        assert isinstance(x, float) and isinstance(y, float)
        assert sev in bc.SEVERITIES
        assert code and detail
    assert any(code == "GEO-OPEN-WALL" for (_x, _y, _s, code, _d) in pts)
    # the alternate 4-tuple shape
    marks = bc.findings_to_loft_marks(r)
    assert marks and len(marks[0]) == 4
    # PDF findings never leak into the loft overlay
    rp = bc.check_pdf(make_pdf(), log=lambda *a: None)
    assert bc.loft_finding_points(rp) == []


# ---------------------------------------------------------- lessons --------

def test_lessons_lane():
    store = os.path.join(TMP, "lessons.db")
    m = gap_model()
    r0 = bc.check_loft(m)
    open_f = first(r0, "GEO-OPEN-WALL")

    # heartwood-absent guard: a bad path never crashes
    assert bc.lessons_from_heartwood("/no/such/store.db") == []
    # no heartwood_path -> no lessons lane, no crash
    assert not has(bc.check_loft(m), "LES-REPEAT")

    nid = bc.record_lesson(store, open_f, note="unclosed corners bite us")
    assert isinstance(nid, int)
    # recorded UNVERIFIED: it must NOT fire yet
    r1 = bc.check_loft(m, heartwood_path=store)
    assert not has(r1, "LES-REPEAT")
    assert bc.lessons_from_heartwood(store) == []   # only trusted are returned

    from rfi_stamper.heartwood import Heartwood
    hw = Heartwood(store)
    assert hw.trust_note(nid)
    hw.close()

    r2 = bc.check_loft(m, heartwood_path=store)
    les = [f for f in r2.findings if f.code == "LES-REPEAT"]
    assert les, "trusted lesson should fire LES-REPEAT"
    assert les[0].category == "lessons"
    assert "GEO-OPEN-WALL" in les[0].detail
    assert "LES-REPEAT" in r2.stats["checked"]

    lessons = bc.lessons_from_heartwood(store)
    assert lessons and lessons[0]["code"] == "GEO-OPEN-WALL"
    assert lessons[0]["note"] and lessons[0]["cite"]


# --------------------------------------------------------- dispatch --------

def test_dispatch_and_filter():
    # DraftModel -> loft
    m = clean_room()
    assert bc.check(m).source == "loft"
    # .loft.json -> loft
    p = os.path.join(TMP, "clean.loft.json")
    m.save(p)
    assert bc.check(p).source == "loft"
    # .pdf / .dxf / .obj by extension
    assert bc.check(make_pdf(), log=lambda *a: None).source == "pdf"
    # unsupported extension raises
    try:
        bc.check("nope.txt")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # rules= filter by explicit code
    r = bc.check_loft(gap_model(), rules=["GEO-OPEN-WALL"])
    assert r.findings and all(f.code == "GEO-OPEN-WALL" for f in r.findings)
    assert r.stats["checked"] == ["GEO-OPEN-WALL"]

    # rules= filter by category
    dm = DraftModel(title="", number="")
    dm.add("wall", [(0, 0), (0.3, 0)], wtype="stud4")   # dfx thin wall
    dm.add("text", [(1, 1)], text="BY OTHERS")          # ambiguity
    rc = bc.check_loft(dm, rules=["dfx"])
    assert rc.findings and all(f.category == "dfx" for f in rc.findings)
    assert not has(rc, "AMB-VAGUE-NOTE")


# ------------------------------------------------------------- runner ------

def main():
    test_registry()
    print("PASS registry shape + skipped-rule reasons")
    test_finding_report_model()
    print("PASS Finding/Report dataclasses (round trip, sort, filters)")
    test_clean_loft_zero()
    print("PASS clean Loft room -> zero findings (no false positives)")
    test_loft_data_ambiguity()
    print("PASS loft data + ambiguity (dupdim, unlabeled, vague)")
    test_loft_undim_room()
    print("PASS loft undimensioned room")
    test_loft_geometry()
    print("PASS loft geometry (sharp, open-wall, overlap, degenerate)")
    test_loft_standards_dfx()
    print("PASS loft standards + dfx (titleblock, thin, text-min, pinch)")
    test_door_swing_and_room_no_door()
    print("PASS door swing + room-no-door")
    test_pipe_checks()
    print("PASS pipe checks (slope-min, no-invert, no-trap, span, dead-end)")
    test_pipe_rules_absent_without_pipes()
    print("PASS pipe rules absent without pipes")
    test_pdf_checks()
    print("PASS pdf checks (dup, dangling, no-scale, vague, titleblock)")
    test_pdf_material_and_gap()
    print("PASS pdf material conflict + sheet gap")
    test_dxf_degenerate()
    print("PASS dxf degenerate geometry")
    test_obj_degenerate()
    print("PASS obj open edges + islands")
    test_markup_bridge()
    print("PASS markup bridge (valid markups + annotated PDF)")
    test_loft_finding_points()
    print("PASS loft finding points overlay shape")
    test_lessons_lane()
    print("PASS lessons lane (record -> untrusted -> trust -> fire)")
    test_dispatch_and_filter()
    print("PASS dispatch by type/extension + rules= filter")
    print("BACKCHECK TEST PASSED  (the peer checker)")
    print("outputs in", TMP)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("BACKCHECK TEST FAILED:", e)
        sys.exit(1)
