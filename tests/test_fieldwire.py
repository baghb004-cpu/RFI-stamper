"""Self-contained tests for the Fieldstitch Pro A2 wire layer — the
exchange formats (rfi_stamper.fieldwire) and the fieldpro coordinate /
error-budget / station-log / stake-package additions.  Plain python, no
pytest, no project data.

Exercises:

* the ONE shared coordinate-order writer table (CSV/XML N-first, GSI
  E-first, DXF X=E)
* LandXML 1.2 CgPoints: exact element text (N E [Z] INSIDE the element),
  Units Imperial/Metric (linearUnit says WHICH foot), state proposed/
  existing <-> kind, 2D points omit Z, namespace-agnostic import
* GSI-8: exact line vs the brief's example for pt 1001 (feet digits),
  E-N-Z word order, null-Z omits the 83 word, meter digit, GSI-16
  auto-switch on big eastings AND long ids (whole file, never mixed),
  reader unit factors / sign / leading-zero strip / station-words-as-
  control
* SP records: exact JB/MO/SP lines (UN0|UN1|UN2 encodes the foot, SF the
  CSF, the single space after N and E), null-EL exclusion with count
  warning + EL0.000 opt-in, import scans only SP and ignores every
  observation record type
* DXF attribute-block tier: BLOCKS/LAYPT with 3 ATTDEFs, INSERT+ATTRIBx3+
  SEQEND per point alongside the plain POINT, reparse round trip, layer
  name rules enforced at creation
* kits sheetbend (LandXML+CSV) and marlinspike (GSI+SP)
* the two feet: exact conversion through meters only, round trips, the
  2,000,000 ft tripwire (~4.0 ft shift)
* CSF math, sidecar persistence of survey_anchor/csf
* fit_from_control: exact 2-point, Helmert 3+ residuals ~0 on synthetic
  data, swapped-pair detection, azimuth-of-plan-north vs a hand-computed
  example
* tape_check diagnosis bands; point_sigma vs the brief's worked example
  (~2.9 mm 1-sigma / ~0.22 in 95%); budget_check coloring
* StationLog validation + QAStore round trip; export_package contents +
  manifest PDF opens in pypdf

Run:  python3.12 tests/test_fieldwire.py
"""
import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper.fieldstitch import (            # noqa: E402
    KITS, LayoutJob, export_kit, frame_hash)
from rfi_stamper import fieldwire as fw          # noqa: E402
from rfi_stamper import fieldpro as fp           # noqa: E402
from rfi_stamper.markups.measure import ScaleCal  # noqa: E402


def expect(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc as e:
        return e
    raise AssertionError(f"expected {exc.__name__} from {fn!r}{args!r}")


def calibrated_job(pdf_path=None, units="ft"):
    """Basepoint at page (100, 700) = world N 5000 / E 2000, 1 pt = 0.1."""
    job = LayoutJob(pdf_path)
    job.base_page_xy = (100.0, 700.0)
    job.base_world = (5000.0, 2000.0)
    job.units = units
    job.scale = ScaleCal(real_per_pt=0.1, unit=units).to_dict()
    return job


def brief_job(units="ft"):
    """The brief's example point 1001: E 2000.375, N 5000.125, Z 100.25,
    desc 'ABOLT A1' — plus 1002 with no elevation."""
    job = calibrated_job(units=units)
    job.add_point(1, 103.75, 698.75, num=1001, elev=100.25,
                  desc="ABOLT A1", code="ABOLT")
    job.add_point(1, 110.0, 690.0, num=1002, elev=None, desc="no z")
    return job


def _read_wire(path):
    with open(path, "rb") as f:
        raw = f.read()
    assert not raw.startswith(b"\xef\xbb\xbf"), "BOM in wire format"
    assert b"\r\n" in raw, "wire format must be CRLF"
    raw.decode("ascii")                          # ASCII only
    assert not os.path.exists(path + ".part")
    return raw.decode("ascii").split("\r\n")


# ------------------------------------------------------------ writer table --

def test_writer_table():
    # three formats, three orders — all from ONE table
    assert fw.WRITER_ORDER["csv"] == ("n", "e", "z")
    assert fw.WRITER_ORDER["landxml"] == ("n", "e", "z")
    assert fw.WRITER_ORDER["gsi"] == ("e", "n", "z"), \
        "GSI is E-first (WI 81 = Easting) — the reverse of PNEZD"
    assert fw.WRITER_ORDER["sp"] == ("n", "e", "z")
    assert fw.WRITER_ORDER["dxf"] == ("e", "n", "z")
    assert fw.ordered("gsi", 1.0, 2.0, 3.0) == (2.0, 1.0, 3.0)
    assert fw.ordered("landxml", 1.0, 2.0) == (1.0, 2.0, None)
    expect(ValueError, fw.ordered, "morse", 1, 2, 3)


# ---------------------------------------------------------------- LandXML --

def test_landxml_export(tmp):
    job = brief_job()
    out = os.path.join(tmp, "pts.xml")
    assert fw.export_landxml(job, out) == 2
    lines = _read_wire(out)
    text = "\r\n".join(lines)
    assert 'xmlns="http://www.landxml.org/schema/LandXML-1.2"' in text
    assert 'version="1.2"' in text and 'readOnly="false"' in text
    assert '<Imperial areaUnit="squareFoot" linearUnit="foot"' in text
    assert 'temperatureUnit="fahrenheit"' in text
    assert 'pressureUnit="inHG"' in text
    # the brief's example point, byte-exact: N E Z INSIDE the element
    assert ('<CgPoint name="1001" code="ABOLT" desc="ABOLT A1" '
            'state="proposed">5000.1250 2000.3750 100.250</CgPoint>'
            in text), text
    # 2D point: exactly two tokens, no Z, no code attribute
    assert ('<CgPoint name="1002" desc="no z" state="proposed">'
            '5001.0000 2001.0000</CgPoint>' in text), text

    # control exports state="existing"
    job.add_point(1, 100.0, 700.0, num=90, kind="CONTROL", elev=100.0,
                  desc="CP")
    fw.export_landxml(job, out)
    text = "\r\n".join(_read_wire(out))
    assert 'state="existing">5000.0000 2000.0000 100.000' in text, text

    # metric job -> Metric element; usft -> the survey-foot spelling
    mj = calibrated_job(units="m")
    mj.add_point(1, 110.0, 680.0, elev=1.0)
    fw.export_landxml(mj, out)
    text = "\r\n".join(_read_wire(out))
    assert '<Metric areaUnit="squareMeter" linearUnit="meter"' in text
    uj = calibrated_job(units="usft")
    uj.add_point(1, 110.0, 680.0, elev=1.0)
    fw.export_landxml(uj, out)
    text = "\r\n".join(_read_wire(out))
    assert 'linearUnit="USSurveyFoot"' in text, \
        "linearUnit is how this format tells the two feet apart"


def test_landxml_import(tmp):
    job = brief_job()
    job.add_point(1, 100.0, 700.0, num=90, kind="CONTROL", elev=100.0,
                  desc="CP", code="CP")
    out = os.path.join(tmp, "rt.xml")
    fw.export_landxml(job, out)

    data = fw.read_landxml(out)
    assert data["version"] == "1.2" and data["units"] == "ft"
    assert len(data["rows"]) == 3 and data["bad"] == []
    by = {r["id"]: r for r in data["rows"]}
    r = by["1001"]
    assert abs(r["n"] - 5000.125) < 1e-9 and abs(r["e"] - 2000.375) < 1e-9
    assert abs(r["z"] - 100.25) < 1e-9 and r["code"] == "ABOLT"
    assert r["desc"] == "ABOLT A1" and r["kind"] == "DESIGN"
    assert by["1002"]["z"] is None
    assert by["090"]["kind"] == "CONTROL"

    j2 = calibrated_job()
    assert fw.import_landxml(j2, out, log=lambda m: None) == 3
    p = j2.find_by_num(1001)
    n, e, z = j2.to_world(p)
    assert abs(n - 5000.125) < 1e-6 and abs(e - 2000.375) < 1e-6
    assert z == 100.25 and p.code == "ABOLT"
    assert j2.find_by_num(1002).elev is None
    ctl = j2.find_by_num(90)
    assert ctl.kind == "CONTROL" and ctl.locked

    # namespace-agnostic + any version: strip the namespace, bump version
    with open(out, encoding="ascii") as f:
        raw = f.read()
    raw = raw.replace(' xmlns="http://www.landxml.org/schema/LandXML-1.2"',
                      "").replace('version="1.2"', 'version="1.0"')
    alien = os.path.join(tmp, "alien.xml")
    with open(alien, "w", encoding="ascii") as f:
        f.write(raw)
    data2 = fw.read_landxml(alien)
    assert data2["version"] == "1.0" and len(data2["rows"]) == 3
    assert data2["rows"][0]["n"] == data["rows"][0]["n"]

    # bad coordinate text lands in bad, never crashes
    with open(alien, "w", encoding="ascii") as f:
        f.write('<LandXML><CgPoints><CgPoint name="X">junk</CgPoint>'
                "</CgPoints></LandXML>")
    data3 = fw.read_landxml(alien)
    assert data3["rows"] == [] and data3["bad"] == [("X", "junk")]


# --------------------------------------------------------------------- GSI --

def test_gsi_export(tmp):
    job = brief_job()
    out = os.path.join(tmp, "pts.gsi")
    assert fw.export_gsi(job, out) == 2
    lines = [ln for ln in _read_wire(out) if ln]
    # the brief's example line, byte-exact (feet digit 1, E-N-Z order)
    assert lines[0] == ("110001+00001001 81..11+02000375 "
                       "82..11+05000125 83..11+00100250 "), repr(lines[0])
    # second block: sequence 0002, null Z -> NO 83 word
    assert lines[1] == "110002+00001002 81..11+02001000 82..11+05001000 ", \
        repr(lines[1])
    assert "83" not in lines[1].split("82")[1]
    # every GSI-8 word is exactly 16 chars incl. its trailing blank
    for ln in lines:
        assert len(ln) % 16 == 0, repr(ln)

    # meter job writes units digit 0 (meter / mm)
    mj = calibrated_job(units="m")
    mj.add_point(1, 103.75, 698.75, num=7, elev=2.5)
    fw.export_gsi(mj, out)
    ln = [x for x in _read_wire(out) if x][0]
    assert "81..10+" in ln and "82..10+" in ln, ln
    # 5000.125 m at mm resolution = 5000125
    assert "82..10+05000125" in ln, ln


def test_gsi16_autoswitch(tmp):
    # a state-plane easting (~2,000,000 ft) overflows 8 data digits:
    # 2,000,010.000 ft / 0.001 = 2,000,010,000 (10 digits) -> GSI-16
    job = LayoutJob()
    job.base_page_xy = (0.0, 0.0)
    job.base_world = (500000.0, 2000000.0)
    job.scale = ScaleCal(real_per_pt=1.0, unit="ft").to_dict()
    job.add_point(1, 10.0, -10.0, num=5, elev=1.5)
    job.add_point(1, 20.0, -20.0, num=6, elev=None)   # small id, no Z
    out = os.path.join(tmp, "big.gsi")
    assert fw.export_gsi(job, out) == 2
    lines = [ln for ln in _read_wire(out) if ln]
    # the WHOLE file switches — never mix widths
    assert all(ln.startswith("*") for ln in lines), lines
    assert lines[0] == ("*110001+0000000000000005 "
                       "81..11+0000002000010000 "
                       "82..11+0000000500010000 "
                       "83..11+0000000000001500 "), repr(lines[0])
    for ln in lines:
        assert (len(ln) - 1) % 24 == 0, repr(ln)

    # a long id alone (> 8 chars) also flips the file to GSI-16
    j2 = calibrated_job()
    j2.prefix = "ABCDEF-"                        # 7 + 3-digit pad = 10 chars
    j2.add_point(1, 110.0, 680.0, elev=1.0)
    fw.export_gsi(j2, out)
    lines = [ln for ln in _read_wire(out) if ln]
    assert lines[0].startswith("*") and "ABCDEF-001" in lines[0], lines[0]

    # read-back of the wide file
    rows = fw.read_gsi(out)["rows"]
    assert rows[0]["id"] == "ABCDEF-001"
    assert abs(rows[0]["n"] - 5002.0) < 1e-9
    assert abs(rows[0]["e"] - 2001.0) < 1e-9


def test_gsi_read(tmp):
    # hand-written file: digit 7 (feet/0.0001), digit 0 (meter/mm),
    # negative sign, leading-zero id strip, station words 84/85/86
    path = os.path.join(tmp, "hand.gsi")
    with open(path, "w", newline="") as f:
        f.write("110001+00000007 81..17+02000375 82..17+05000125 \r\n")
        f.write("110002+00000008 81..10-00001500 82..10+00002500 \r\n")
        f.write("110003+00000009 84..11+02000000 85..11+05000000 "
                "86..11+00100000 \r\n")
    data = fw.read_gsi(path)
    assert data["unit"] == "ft"                  # first coordinate word wins
    rows = {r["id"]: r for r in data["rows"]}
    r7 = rows["7"]                               # zeros stripped
    assert abs(r7["e"] - 200.0375) < 1e-9, r7    # 0.0001 ft factor
    assert abs(r7["n"] - 500.0125) < 1e-9
    assert r7["z"] is None and r7["kind"] == "DESIGN"
    r8 = rows["8"]
    assert abs(r8["e"] + 1.5) < 1e-12, "sign at pos 7 must apply"
    assert abs(r8["n"] - 2.5) < 1e-12
    r9 = rows["9"]
    assert r9["kind"] == "CONTROL", "84/85/86 station words import as control"
    assert abs(r9["e"] - 2000.0) < 1e-9 and abs(r9["n"] - 5000.0) < 1e-9
    assert abs(r9["z"] - 100.0) < 1e-9

    # full export -> import round trip through a job
    job = brief_job()
    rt = os.path.join(tmp, "rt.gsi")
    fw.export_gsi(job, rt)
    j2 = calibrated_job()
    assert fw.import_gsi(j2, rt, log=lambda m: None) == 2
    p = j2.find_by_num(1001)
    n, e, z = j2.to_world(p)
    assert abs(n - 5000.125) < 1e-6 and abs(e - 2000.375) < 1e-6
    assert z == 100.25
    assert j2.find_by_num(1002).elev is None

    # junk lines land in bad, never crash
    with open(path, "a", newline="") as f:
        f.write("81..11+garbage__ \r\n")
    data2 = fw.read_gsi(path)
    assert len(data2["rows"]) == 3 and len(data2["bad"]) == 1


# ---------------------------------------------------------------------- SP --

def test_sp_export(tmp):
    job = brief_job()
    out = os.path.join(tmp, "pts.rw5")
    warnings = []
    # 1002 has no elevation -> EXCLUDED with a count warning
    assert fw.export_sp(job, out, name="jobname", log=warnings.append) == 1
    assert any("1 point(s) with no elevation" in w for w in warnings)
    lines = [ln for ln in _read_wire(out) if ln]
    assert lines[0].startswith("JB,NMjobname,DT") and ",TM" in lines[0]
    assert lines[1] == "MO,AD0,UN2,SF1.00000000,EC0,EO0.0", lines[1]
    # the brief's example line, byte-exact (single space after N and E)
    assert lines[2] == ("SP,PN1001,N 5000.1250,E 2000.3750,EL100.250,"
                        "--ABOLT A1"), lines[2]
    assert len(lines) == 3

    # EL0.000 only on explicit opt-in
    assert fw.export_sp(job, out, log=lambda m: None,
                        include_null_z=True) == 2
    lines = [ln for ln in _read_wire(out) if ln]
    assert lines[3].endswith("EL0.000,--no z"), lines[3]

    # UN encodes WHICH foot; SF carries the job CSF
    uj = calibrated_job(units="usft")
    uj.add_point(1, 110.0, 680.0, elev=1.0)
    uj.csf = 0.99984731
    fw.export_sp(uj, out, log=lambda m: None)
    lines = [ln for ln in _read_wire(out) if ln]
    assert lines[1] == "MO,AD0,UN0,SF0.99984731,EC0,EO0.0", lines[1]
    mj = calibrated_job(units="m")
    mj.add_point(1, 110.0, 680.0, elev=1.0)
    fw.export_sp(mj, out, log=lambda m: None)
    assert ",UN1," in [ln for ln in _read_wire(out) if ln][1]


def test_sp_import(tmp):
    # a raw log: observation records MUST be ignored (never re-reduce)
    path = os.path.join(tmp, "raw.rw5")
    with open(path, "w", newline="") as f:
        f.write("JB,NMfield,DT07-08-2026,TM07:00:00\r\n")
        f.write("MO,AD0,UN0,SF0.99987000,EC0,EO0.0\r\n")
        f.write("--this is a comment line\r\n")
        f.write("OC,OP1,N 5000.0000,E 2000.0000,EL100.000\r\n")
        f.write("BK,OP1,BP2,BS315.00000,BC0.00000\r\n")
        f.write("LS,HI5.2000,HR6.0000\r\n")
        f.write("SS,OP1,FP7,AR90.00000,ZE90.00000,SD100.0000,--shot\r\n")
        f.write("TR,OP1,FP8,AR12.00000\r\n")
        f.write("GPS,PN9,LA37.000000,LN-122.000000\r\n")
        f.write("BD,OP1,FP7\r\nBR,OP1,FP7\r\n")
        f.write("SP,PN1001,N 5002.0000,E 2001.0000,EL3.500,--HGR, row A\r\n")
        f.write("SP,PN1002,N5003.0000,E2001.5000,--no space, no EL\r\n")
    data = fw.read_sp(path)
    assert data["units"] == "usft" and data["job"] == "field"
    assert abs(data["sf"] - 0.99987) < 1e-9
    assert len(data["rows"]) == 2, [r["id"] for r in data["rows"]]
    r1, r2 = data["rows"]
    assert r1["id"] == "1001" and r1["z"] == 3.5
    assert r1["desc"] == "HGR, row A", "commas are legal after --"
    assert r2["id"] == "1002" and r2["z"] is None      # tolerant, no space
    assert abs(r2["n"] - 5003.0) < 1e-9 and abs(r2["e"] - 2001.5) < 1e-9

    job = calibrated_job()
    assert fw.import_sp(job, path, log=lambda m: None) == 2
    n, e, z = job.to_world(job.find_by_num(1001))
    assert abs(n - 5002.0) < 1e-6 and z == 3.5
    assert len(job.points) == 2, "an observation record leaked in!"


# --------------------------------------------------------------- DXF blocks --

def test_dxf_blocks(tmp):
    job = brief_job()
    out = os.path.join(tmp, "pts.dxf")
    assert fw.export_dxf_blocks(job, out) == 2
    _read_wire(out)
    pairs = fw.read_dxf_pairs(out)
    text = [(c, v) for c, v in pairs]
    # block definition: LAYPT, attributes-follow flag 70=2
    bi = text.index((0, "BLOCK"))
    assert (2, "LAYPT") in text[bi:bi + 6] and (70, "2") in text[bi:bi + 6]
    attdefs = [i for i, cv in enumerate(text) if cv == (0, "ATTDEF")]
    assert len(attdefs) == 3
    tags = []
    for i in attdefs:
        seg = text[i:i + 12]
        tags.append([v for c, v in seg if c == 2][0])
        assert (40, "1.50") in seg and (70, "0") in seg
        assert any(c == 3 for c, _ in seg), "ATTDEF needs its prompt (3)"
    assert tags == ["PT", "ELEV", "DESC"], tags
    assert (0, "ENDBLK") in text

    # entities: plain POINT kept + INSERT(66=1)/ATTRIBx3/SEQEND per point
    assert sum(1 for cv in text if cv == (0, "POINT")) == 2
    inserts = [i for i, cv in enumerate(text) if cv == (0, "INSERT")]
    assert len(inserts) == 2
    seg = text[inserts[0]:inserts[0] + 40]
    assert (66, "1") in seg and (2, "LAYPT") in seg
    # coordinates: 10 = X = Easting, 20 = Y = Northing (writer table)
    assert (10, "2000.3750") in seg and (20, "5000.1250") in seg, seg
    assert (30, "100.2500") in seg
    assert sum(1 for cv in text if cv == (0, "ATTRIB")) == 6
    assert sum(1 for cv in text if cv == (0, "SEQEND")) == 2
    # ATTRIB values: id, elevation, description; null elev writes '-'
    vals = [v for c, v in text if c == 1 and v]
    assert "1001" in vals and "100.250" in vals and "ABOLT A1" in vals
    assert "-" in vals, "null elevation must write '-' (ASCII), never 0"
    # layer names sanitized on the way out ("Layout" -> "LAYOUT")
    assert any(v == "LAYOUT" for c, v in text if c == 8), \
        sorted({v for c, v in text if c == 8})

    # reparse harvests INSERT origins + attributes
    rows = fw.read_dxf_points(out)["rows"]
    by = {r["id"]: r for r in rows}
    assert abs(by["1001"]["n"] - 5000.125) < 1e-9
    assert abs(by["1001"]["e"] - 2000.375) < 1e-9
    assert by["1001"]["z"] == 100.25 and by["1001"]["desc"] == "ABOLT A1"
    assert by["1002"]["z"] is None


def test_dxf_layer_rules(tmp):
    # conformant names pass; enforcement is at CREATION
    fw.validate_dxf_layer("A-FLOOR_2$")
    fw.validate_dxf_layer("S-GRID")
    e = expect(ValueError, fw.validate_dxf_layer, "Layout")
    assert "Layout" in str(e), e
    expect(ValueError, fw.validate_dxf_layer, "MY LAYER")     # space
    expect(ValueError, fw.validate_dxf_layer, "X" * 32)       # > 31
    expect(ValueError, fw.validate_dxf_layer, "")
    expect(ValueError, fw.validate_dxf_layer, "PIPE*RUN")
    # sanitizer for legacy names
    assert fw.dxf_layer_name("Layout") == "LAYOUT"
    assert fw.dxf_layer_name("my layer") == "MY_LAYER"
    assert fw.dxf_layer_name("a" * 40) == "A" * 31
    assert fw.dxf_layer_name("Fixeés") == "FIXE_S"
    # creation-time enforcement
    job = calibrated_job()
    ly = fw.add_cad_layer(job, "S-ANCHOR", color="#ff0000")
    assert job.layer("S-ANCHOR") is ly
    expect(ValueError, fw.add_cad_layer, job, "bad name")
    expect(ValueError, fw.add_cad_layer, job, "S-ANCHOR")     # duplicate


# -------------------------------------------------------------------- kits --

def test_kits(tmp):
    assert set(KITS) == {"bowline", "clovehitch", "fullspool",
                         "sheetbend", "marlinspike"}
    assert KITS["sheetbend"] == ("landxml", "csv")
    assert KITS["marlinspike"] == ("gsi", "sp")
    job = brief_job()

    d1 = os.path.join(tmp, "kit_sb")
    res = export_kit(job, d1, "sheetbend")
    assert res["points"] == 2
    assert sorted(os.listdir(d1)) == ["layout.csv", "layout.xml"]
    assert len(fw.read_landxml(os.path.join(d1, "layout.xml"))["rows"]) == 2

    d2 = os.path.join(tmp, "kit_ms")
    res2 = export_kit(job, d2, "marlinspike")
    assert res2["points"] == 2
    assert sorted(os.listdir(d2)) == ["layout.gsi", "layout.rw5"]
    assert len(fw.read_gsi(os.path.join(d2, "layout.gsi"))["rows"]) == 2
    # the SP file drops the null-Z point (count warning is opt-out only)
    assert len(fw.read_sp(os.path.join(d2, "layout.rw5"))["rows"]) == 1


def test_wire_refusals(tmp):
    # duplicate ids and mixed witness offsets refuse on EVERY wire format
    from rfi_stamper.fieldstitch import LayoutPoint
    dup = calibrated_job()
    dup.points.append(LayoutPoint.new(num=1, page=1, x=110.0, y=680.0))
    dup.points.append(LayoutPoint.new(num=1, page=1, x=120.0, y=690.0))
    out = os.path.join(tmp, "refuse.any")
    for exporter in (fw.export_landxml, fw.export_gsi, fw.export_dxf_blocks):
        e = expect(ValueError, exporter, dup, out)
        assert "duplicate" in str(e), (exporter, e)
    e = expect(ValueError, fw.export_sp, dup, out, log=lambda m: None)
    assert "duplicate" in str(e), e

    mixed = calibrated_job()
    p1 = mixed.add_point(1, 110.0, 680.0)
    p2 = mixed.add_point(1, 150.0, 640.0)
    mixed.add_witness(p1, offset_ft=2.0, offset_azimuth=0.0)
    mixed.add_witness(p2, offset_ft=5.0, offset_azimuth=0.0)
    for exporter in (fw.export_landxml, fw.export_gsi, fw.export_dxf_blocks):
        e = expect(ValueError, exporter, mixed, out)
        assert "witness" in str(e).lower(), (exporter, e)


# ------------------------------------------------------------- the two feet --

def test_units():
    assert fp.FT_INTL == 0.3048
    assert fp.FT_US == 1200.0 / 3937.0
    # exact through meters, both directions
    assert fp.convert_units(1.0, "usft", "m") == 1200.0 / 3937.0
    assert fp.convert_units(1.0, "ift", "m") == 0.3048
    assert fp.convert_units(0.3048, "m", "ift") == 1.0
    assert fp.convert_units(5.0, "ift", "ift") == 5.0
    assert fp.convert_units(1.0, "ft", "ift") == 1.0     # legacy alias
    # round trips are exact (never chained approximate ratios)
    v = 2_000_000.0
    assert fp.convert_units(fp.convert_units(v, "usft", "m"),
                            "m", "usft") == v
    # the classic bust: a 2,000,000 ft easting misread shifts ~4.0 ft
    usft_in_m = fp.convert_units(v, "usft", "m")
    ift_in_m = fp.convert_units(v, "ift", "m")
    shift_ft = (usft_in_m - ift_in_m) / fp.FT_INTL
    assert abs(shift_ft - 4.0) < 0.005, shift_ft
    expect(ValueError, fp.convert_units, 1.0, "cubit", "m")
    expect(ValueError, fp.convert_units, 1.0, "m", "furlong")

    # tripwire: shift = 2e-6 x max(|N|,|E|); block above 0.05 ft
    t = fp.unit_shift_tripwire(500000.0, 2000000.0)
    assert abs(t["shift_ft"] - 4.0) < 1e-9 and t["block"]
    assert "which foot" in t["message"]
    small = fp.unit_shift_tripwire(5000.0, 2000.0)
    assert abs(small["shift_ft"] - 0.01) < 1e-9
    assert not small["block"] and small["message"] == ""
    # boundary: exactly 0.05 proceeds (block is strictly above)
    assert not fp.unit_shift_tripwire(0.0, 25000.0)["block"]


# --------------------------------------------------------------------- CSF --

def test_csf(tmp):
    # EF = R / (R + h): at h = 1609 m the brief quotes ~0.999747
    ef = fp.elevation_factor(1609.0, radius=fp.EARTH_R_M)
    assert abs(ef - 0.999747) < 1e-6, ef
    assert fp.elevation_factor(0.0) == 1.0
    csf = fp.combined_scale_factor(0.9999, ef)
    assert abs(csf - 0.9999 * ef) < 1e-15
    # ground = grid / CSF about the origin; round trip exact-ish
    org = (5000.0, 2000.0)
    gn, ge = fp.ground_to_grid(6000.0, 3000.0, 0.99984731, org)
    bn, be = fp.grid_to_ground(gn, ge, 0.99984731, org)
    assert abs(bn - 6000.0) < 1e-9 and abs(be - 3000.0) < 1e-9
    # 100 ppm x 1000 ft = 0.1 ft — the brief's magnitude check
    g2n, _ = fp.grid_to_ground(6000.0, 2000.0, 0.9999, org)
    assert abs((g2n - 6000.0) - 0.1) < 1e-4, g2n
    expect(ValueError, fp.grid_to_ground, 1, 1, 0.0)
    expect(ValueError, fp.ground_to_grid, 1, 1, -1.0)

    # persistence: CSF != 1 REQUIRES its origin; sidecar round trip
    pdf = os.path.join(tmp, "csfplan.pdf")
    with open(pdf, "wb") as f:
        f.write(b"%PDF-fake")
    job = calibrated_job(pdf)
    e = expect(ValueError, fp.set_job_csf, job, 0.99984731)
    assert "origin" in str(e), e
    fp.set_job_csf(job, 0.99984731, origin="CP-001", k=0.9999, ef=0.999947)
    job.survey_anchor = {"n": 604000.0, "e": 1886000.0, "z": 12.5,
                         "h_datum": "SPCS zone", "v_datum": "project",
                         "unit": "usft"}
    job._autosave()
    back = LayoutJob(pdf)
    assert back.csf == 0.99984731 and back.csf_origin == "CP-001"
    assert back.csf_parts == {"k": 0.9999, "ef": 0.999947}
    assert back.survey_anchor["h_datum"] == "SPCS zone"
    # a fresh job's sidecar carries none of the new keys (lean)
    clean_pdf = os.path.join(tmp, "clean.pdf")
    with open(clean_pdf, "wb") as f:
        f.write(b"%PDF-fake")
    clean = calibrated_job(clean_pdf)
    clean.add_point(1, 110.0, 680.0)
    with open(clean_pdf + LayoutJob.SUFFIX, encoding="utf-8") as f:
        blob = json.load(f)
    for key in ("csf", "csf_origin", "csf_parts", "survey_anchor"):
        assert key not in blob, key


# ------------------------------------------------------------- control fit --

def test_fit(tmp):
    # synthetic exact frame: rotation 30 CCW, scale 0.1 ft/pt
    ref = LayoutJob()
    ref.base_page_xy = (100.0, 700.0)
    ref.base_world = (5000.0, 2000.0)
    ref.rotation_deg = 30.0
    ref.scale = ScaleCal(real_per_pt=0.1, unit="ft").to_dict()
    pagepts = [(120.0, 650.0), (300.0, 700.0), (200.0, 400.0),
               (90.0, 710.0)]
    pairs = []
    for x, y in pagepts:
        p = ref.add_point(1, x, y)
        n, e, _ = ref.to_world(p)
        pairs.append(((x, y), (n, e)))

    # 4-point Helmert: residuals ~0, frame recovered
    fit = fp.fit_from_control(pairs)
    assert abs(fit["rotation_deg"] - 30.0) < 1e-9
    assert abs(fit["real_per_pt"] - 0.1) < 1e-12
    assert fit["rms_ft"] < 1e-9 and fit["max_ft"] < 1e-9
    assert len(fit["residuals"]) == 4
    for r in fit["residuals"]:
        assert abs(r["dn"]) < 1e-9 and abs(r["de"]) < 1e-9
        assert r["hmiss"] < 1e-9
    assert abs(fit["azimuth_plan_north_deg"] - 330.0) < 1e-9

    # 2-point fit is exact too
    fit2 = fp.fit_from_control(pairs[:2])
    assert abs(fit2["rotation_deg"] - 30.0) < 1e-9
    assert abs(fit2["real_per_pt"] - 0.1) < 1e-12
    assert fit2["rms_ft"] < 1e-9

    # apply_fit reproduces the source frame end to end (sign traps stack
    # three deep here — numeric test, not eyeballing)
    job2 = LayoutJob()
    fp.apply_fit(job2, fit)
    q = job2.add_point(1, 120.0, 650.0)
    n, e, _ = job2.to_world(q)
    assert abs(n - pairs[0][1][0]) < 1e-9 and abs(e - pairs[0][1][1]) < 1e-9

    # hand-computed azimuth: page-up from the base at rotation 30 lands on
    # world bearing 330 (dN = +cos30, dE = -sin30)
    up = job2.add_point(1, fit["base_page_xy"][0],
                        fit["base_page_xy"][1] - 10.0)
    un, ue, _ = job2.to_world(up)
    dn, de = un - fit["base_world"][0], ue - fit["base_world"][1]
    az = math.degrees(math.atan2(de, dn)) % 360.0
    assert abs(az - 330.0) < 1e-9, az
    assert fp.azimuth_of_plan_north(0.0) == 0.0
    assert fp.azimuth_of_plan_north(30.0) == 330.0
    assert fp.azimuth_of_plan_north(-90.0) == 90.0

    # a swapped N/E pair (the classic PENZD bust) blows the residuals up
    bad = list(pairs)
    bad[2] = (bad[2][0], (bad[2][1][1], bad[2][1][0]))
    fit3 = fp.fit_from_control(bad)
    assert fit3["rms_ft"] > 100.0, fit3["rms_ft"]
    assert fit3["max_ft"] == max(r["hmiss"] for r in fit3["residuals"])

    # degenerate inputs refuse
    expect(ValueError, fp.fit_from_control, pairs[:1])
    expect(ValueError, fp.fit_from_control,
           [((10.0, 10.0), (5.0, 6.0)), ((10.0, 10.0), (7.0, 8.0))])

    # DMS helpers
    assert abs(fp.dms(330, 30, 36) - 330.51) < 1e-12
    assert fp.format_dms(330.51) == "330-30'36\""
    assert fp.format_dms(-30.0) == "330-00'00\""


# -------------------------------------------------------------- tape check --

def test_tape_check():
    r = fp.tape_check(1000.0, 1000.0005)
    assert r["band"] == "agree" and abs(r["ppm"] - 0.5) < 1e-9
    # ~2 ppm: survey-foot vs international-foot smell
    r = fp.tape_check(1000.0, 1000.002)
    assert r["band"] == "foot" and abs(r["ppm"] - 2.0) < 1e-9
    assert "foot" in r["diagnosis"]
    # misfit matching the job CSF: grid coordinates used as ground
    r = fp.tape_check(1000.0, 1000.15, csf=0.99985)
    assert r["band"] == "csf", r
    assert "grid" in r["diagnosis"]
    # same magnitude WITHOUT a csf declared: unexplained, not csf
    r = fp.tape_check(1000.0, 1000.15)
    assert r["band"] == "unexplained"
    # gross: wrong point / wrong datum
    r = fp.tape_check(1000.0, 1002.0)
    assert r["band"] == "gross" and abs(r["ppm"] - 2000.0) < 1e-9
    expect(ValueError, fp.tape_check, 0.0, 1.0)


# ------------------------------------------------------------ error budget --

def test_point_sigma():
    # the brief's worked example: 5", 2mm+2ppm gun @ 100 ft, 6.5 ft pole
    s = fp.point_sigma(100.0, 5.0)
    assert abs(s["e_ang_mm"] - 0.739) < 0.002, s["e_ang_mm"]
    assert abs(s["sigma_mm"] - 2.9) < 0.05, s["sigma_mm"]
    assert abs(s["p95_in"] - 0.22) < 0.01, s["p95_in"]
    # a 5" gun cannot honestly certify 1/8 in at 95%
    assert s["p95_in"] > 0.125
    # component magnitudes from the brief's tables
    assert abs(fp.point_sigma(300.0, 5.0)["e_ang_mm"] - 2.2) < 0.05
    assert abs(fp.point_sigma(100.0, 1.0)["e_ang_mm"]
               - 0.0005 * fp.FT_INTL * 1000.0) < 0.01
    # angular error grows with distance; EDM ppm term barely moves
    near, far = fp.point_sigma(50.0, 5.0), fp.point_sigma(300.0, 5.0)
    assert far["e_ang_mm"] > 5.0 * near["e_ang_mm"]
    assert far["e_edm_mm"] - near["e_edm_mm"] < 0.2
    # a rod at 1.3-1.5 m drops the pole term to zero (the reference height
    # the 1.5 mm target-centering default is specified at)
    assert fp.point_sigma(100.0, 5.0, pole_h_ft=4.5)["e_pole_mm"] == 0.0
    assert s["e_pole_mm"] > 0.0
    # 95% = 1.96 sigma, and the ft/in views agree
    assert abs(s["p95_mm"] - 1.96 * s["sigma_mm"]) < 1e-12
    assert abs(s["p95_ft"] * 12.0 - s["p95_in"] * 1.0000) < 1e-6


def test_budget_check():
    job = calibrated_job()
    near = job.add_point(1, 110.0, 690.0, tol_class="SLEEVE")   # ~1.4 ft out
    far_page = job.from_world(5000.0, 4000.0)                   # 2000 ft out
    far = job.add_point(1, far_page[0], far_page[1], tol_class="SLEEVE")
    rep = fp.budget_check(job, [near, far], (5000.0, 2000.0),
                          profile={"arcsec": 5.0})
    rows = {r["uid"]: r for r in rep["rows"]}
    assert rows[near.id]["ok"], rows[near.id]
    assert not rows[far.id]["ok"], rows[far.id]
    assert rep["over"] == [job.composed(far)] and rep["ok_count"] == 1
    assert rows[far.id]["tol_class"] == "SLEEVE"
    assert abs(rows[far.id]["dist_ft"] - 2000.0) < 1e-6
    # a tighter gun rescues the far point? 1" at 2000 ft: e_ang ~ 3mm — no,
    # still over a 1/2 in class at 95%? 95% ~ 8.5mm < 12.7mm -> ok
    rep2 = fp.budget_check(job, [far], (5000.0, 2000.0),
                           profile={"arcsec": 1.0})
    assert rep2["rows"][0]["ok"], rep2["rows"][0]


# -------------------------------------------------------------- station log --

def test_station_log(tmp):
    assert fp.station_verdict([0.004, 0.009]) == "pass"
    assert fp.station_verdict([0.004, 0.011]) == "warn"
    assert fp.station_verdict([0.021]) == "fail"
    assert fp.station_verdict([]) == ""
    assert fp.station_verdict([0.010]) == "pass"      # <= passes
    assert fp.station_verdict([0.020]) == "warn"

    log = fp.make_station("S1", "occupy+backsight", occupied="CP-001",
                          targets=["CP-002"], residuals=[0.006],
                          expected_ft=212.415, observed_ft=212.409,
                          prism_constant_mm=-30.0, note="am setup",
                          ts="2026-07-08T07:00:00+00:00")
    assert log.verdict == "pass" and log.prism_constant_mm == -30.0
    assert log.method == "occupy+backsight"
    res = fp.make_station("S2", "resection", targets=["1", "2", "3"],
                          residuals=[0.002, 0.004, 0.015])
    assert res.verdict == "warn" and res.occupied == "free"
    expect(ValueError, fp.make_station, "S3", "guesswork", targets=["1"])
    expect(ValueError, fp.make_station, "S3", "occupy+backsight",
           targets=["1", "2"])                        # exactly 1 backsight
    expect(ValueError, fp.make_station, "S3", "resection", targets=["1"])
    expect(ValueError, fp.make_station, "S3", "resection",
           targets=["1", "2", "3", "4", "5"])

    # store round trip + delta linkage through session_id
    pdf = os.path.join(tmp, "stnplan.pdf")
    with open(pdf, "wb") as f:
        f.write(b"%PDF-fake")
    qa = fp.QAStore(pdf)
    qa.add_station(log)
    qa.add_station(res)
    qa.add_delta(fp.DeltaRecord(point_uid="u1", label="P1",
                                session_id="S1", ts="2026-07-08T08:00:00"))
    qa.add_delta(fp.DeltaRecord(point_uid="u2", label="P2",
                                session_id="S2", ts="2026-07-08T09:00:00"))
    back = fp.QAStore(pdf)
    assert len(back.stations) == 2
    assert back.station("S1").note == "am setup"
    assert back.station("S1").targets == ["CP-002"]
    assert back.station("nope") is None
    assert back.session_uids("S1") == ["u1"]
    bad = fp.StationLog(session_id="")
    expect(ValueError, qa.add_station, bad)


# ------------------------------------------------------------ stake package --

def test_package(tmp):
    import fitz
    pdf = os.path.join(tmp, "plan.pdf")
    doc = fitz.open()
    doc.new_page(width=612, height=792)
    doc.save(pdf)
    doc.close()

    job = calibrated_job(pdf)
    a = job.add_point(1, 110.0, 680.0, elev=1.0, desc="HGR 1", code="HGR",
                      tol_class="MEP-HANGER")
    b = job.add_point(1, 150.0, 640.0, elev=2.0, desc="HGR 2", code="HGR",
                      tol_class="MEP-HANGER")
    c = job.add_point(1, 130.0, 660.0, elev=None, desc="SLV-4", code="SLV",
                      tol_class="SLEEVE")
    job.add_point(1, 100.0, 700.0, kind="CONTROL", num=90, elev=100.0,
                  monument="rebar+cap", where_note="NW column")
    qa = fp.QAStore()
    dn, de = 0.004, -0.003
    n, e, z = job.to_world(a)
    rec = fp.deltas((n, e, z), (n + dn, e + de, z), 0.0208, None,
                    point_uid=a.id, label=job.composed(a),
                    tol_class="MEP-HANGER", session_id="S1")
    qa.add_delta(rec)

    out_dir = os.path.join(tmp, "bundle")
    route = [b, c, a]                              # a saved walk order
    res = fp.export_package(job, qa, out_dir, "L2-MECH", [a, b, c],
                            route=route, log=lambda m: None)
    assert res["points"] == 3 and res["name"] == "L2-MECH"
    names = sorted(os.path.basename(f) for f in res["files"])
    assert names == ["L2-MECH.csv", "L2-MECH.dxf", "L2-MECH.json",
                     "L2-MECH_qa.csv", "L2-MECH_sheet.pdf"], names
    for f in res["files"]:
        assert os.path.getsize(f) > 0 and not os.path.exists(f + ".part")

    # csv: headerless wire in ROUTE order, frame hash in the comments
    lines = _read_wire(os.path.join(out_dir, "L2-MECH.csv"))
    data_rows = [ln for ln in lines if ln and not ln.startswith("#")]
    assert [r.split(",")[0] for r in data_rows] == \
        [job.composed(b), job.composed(c), job.composed(a)]
    assert any("frame: " + frame_hash(job) in ln for ln in lines)

    # qa csv: only this package's governing rows
    qlines = _read_wire(os.path.join(out_dir, "L2-MECH_qa.csv"))
    qbody = [ln for ln in qlines[1:] if ln]
    assert len(qbody) == 1 and qbody[0].startswith(job.composed(a))

    # json: route, tolerances in play, control list, frame snapshot + hash
    with open(os.path.join(out_dir, "L2-MECH.json"), encoding="utf-8") as f:
        pkg = json.load(f)
    assert pkg["route"] == [job.composed(b), job.composed(c),
                            job.composed(a)]
    assert set(pkg["tolerances"]) == {"MEP-HANGER", "SLEEVE"}
    assert pkg["frame"]["hash"] == frame_hash(job)
    assert pkg["frame"]["rotation_deg"] == 0.0
    assert pkg["csf"] == 1.0 and pkg["units"] == "ft"
    assert "international foot" in pkg["foot"]
    assert len(pkg["control"]) == 1
    assert pkg["control"][0]["monument"] == "rebar+cap"
    assert pkg["ritual"] == fp.CHECK_SHOT_RITUAL
    assert "TWO known control points" in fp.CHECK_SHOT_RITUAL
    assert pkg["layers"] == {"Layout": 3}

    # dxf: the attribute-block tier
    rows = fw.read_dxf_points(os.path.join(out_dir, "L2-MECH.dxf"))["rows"]
    assert len(rows) == 3
    assert {r["id"] for r in rows} == {job.composed(p) for p in (a, b, c)}

    # the paper sheet: one page, opens in pypdf
    from pypdf import PdfReader
    reader = PdfReader(os.path.join(out_dir, "L2-MECH_sheet.pdf"))
    assert len(reader.pages) == 1

    # route must be a permutation of the package points
    e2 = expect(ValueError, fp.export_package, job, qa, out_dir, "X",
                [a, b], route=[a])
    assert "route" in str(e2), e2
    expect(ValueError, fp.export_package, job, qa, out_dir, "X", [])

    # no (or fake) plan: the manifest still renders with a placeholder
    fake = calibrated_job()
    fa = fake.add_point(1, 110.0, 680.0, elev=1.0)
    res2 = fp.export_package(fake, fp.QAStore(), out_dir, "NOPLAN", [fa],
                             log=lambda m: None)
    reader2 = PdfReader(os.path.join(out_dir, "NOPLAN_sheet.pdf"))
    assert len(reader2.pages) == 1 and res2["points"] == 1


def main():
    tmp = tempfile.mkdtemp(prefix="fieldwire_")
    test_writer_table()
    test_landxml_export(tmp)
    test_landxml_import(tmp)
    test_gsi_export(tmp)
    test_gsi16_autoswitch(tmp)
    test_gsi_read(tmp)
    test_sp_export(tmp)
    test_sp_import(tmp)
    test_dxf_blocks(tmp)
    test_dxf_layer_rules(tmp)
    test_kits(tmp)
    test_wire_refusals(tmp)
    test_units()
    test_csf(tmp)
    test_fit(tmp)
    test_tape_check()
    test_point_sigma()
    test_budget_check()
    test_station_log(tmp)
    test_package(tmp)
    leftovers = [f for _, _, fs in os.walk(tmp) for f in fs
                 if f.endswith(".part")]
    assert not leftovers, leftovers
    print("FIELDWIRE TESTS PASSED  (one writer table, LandXML exact doc + "
          "namespace-agnostic import, GSI-8 exact line + GSI-16 auto-switch "
          "+ unit-factor read-back, SP records + observation-record "
          "immunity, DXF attribute blocks + layer rules, sheetbend/"
          "marlinspike kits, exact two-feet math + tripwire, CSF + sidecar "
          "extras, Helmert fit + hand-computed azimuth + swapped-pair "
          "detection, tape-check bands, point_sigma vs the worked example, "
          "budget check, station log, stake packages)")
    print("outputs in", tmp)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("FIELDWIRE TEST FAILED:", e)
        sys.exit(1)
