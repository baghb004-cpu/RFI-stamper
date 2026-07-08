"""Self-contained tests for rfi_stamper.fieldstitch — the layout-points
engine.  Plain python, no pytest, no project data.  Exercises:

* numbering: prefix/suffix/pad composition, auto-increment, renumber
* layers: default creation, rename repoints, export visibility filtering
* to_world basepoint/scale/rotation math (+ no-scale ValueError)
* sidecar save/load round-trip (unicode), atomic (no .part leftovers)
* PNEZD CSV export (header, N/E order) and tolerant import round-trip
* XLSX: real zip, parses as OOXML, header + coordinate cells
* DXF R12: group-code reparse, LAYER colors, POINT/TEXT entities, EOF
* export_kit bundles

Run:  python3.12 tests/test_fieldstitch.py
"""
import csv
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper.fieldstitch import (             # noqa: E402
    ACI_COLORS, KITS, LayoutJob, LayoutPoint, PointLayer, aci_for,
    export_csv_pnezd, export_dxf, export_job_json, export_kit, export_xlsx,
    import_csv)
from rfi_stamper.markups.measure import ScaleCal  # noqa: E402


def expect(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc as e:
        return e
    raise AssertionError(f"expected {exc.__name__} from {fn!r}{args!r}")


def calibrated_job(pdf_path=None):
    """Basepoint at page (100, 700) = world N 5000 / E 2000, 1 pt = 0.1 ft."""
    job = LayoutJob(pdf_path)
    job.base_page_xy = (100.0, 700.0)
    job.base_world = (5000.0, 2000.0)
    job.scale = ScaleCal(real_per_pt=0.1, unit="ft").to_dict()
    return job


# --------------------------------------------------------------- numbering --

def test_numbering():
    job = LayoutJob()
    job.prefix, job.suffix, job.pad = "CP-", "-S", 3
    p1 = job.add_point(1, 110.0, 680.0, elev=12.5, desc="anchor bolt A")
    p2 = job.add_point(1, 120.0, 660.0)
    p3 = job.add_point(2, 300.0, 400.0)
    assert (p1.num, p2.num, p3.num) == (1, 2, 3)
    assert job.next_num == 4
    assert p1.label == "CP-1-S", p1.label                 # un-padded label
    assert job.composed(p1) == "CP-001-S", job.composed(p1)
    assert job.composed(p3) == "CP-003-S"
    job.pad = 5
    assert job.composed(p1) == "CP-00001-S"
    job.pad = 3

    # per-point override sticks; job defaults still applied elsewhere
    p4 = job.add_point(1, 10.0, 10.0, prefix="X", suffix="", num=50)
    assert job.composed(p4) == "X050" and job.next_num == 51

    # get / remove / points_on
    assert job.get(p2.id) is p2 and job.get("nope") is None
    assert job.points_on(1) == [p1, p2, p4]
    assert job.remove(p4.id) and not job.remove(p4.id)
    assert job.points_on(1) == [p1, p2]

    # renumber: stable by (page, created); page-2 point sorts last even
    # though a later page-1 point exists
    p5 = job.add_point(1, 130.0, 640.0)
    job.renumber(start=10)
    assert [p.num for p in (p1, p2, p5, p3)] == [10, 11, 12, 13], \
        [(p.num, p.page) for p in job.points]
    assert job.next_num == 14
    assert job.composed(p3) == "CP-013-S"

    # LayoutPoint.new fills id/created; to_dict/from_dict round-trips
    d = p1.to_dict()
    q = LayoutPoint.from_dict(d)
    assert q == p1 and q is not p1
    assert p1.id and p1.created and p1.id != p2.id


# ------------------------------------------------------------------ layers --

def test_layers():
    job = LayoutJob()
    assert job.layers == []                       # nothing until first point
    p1 = job.add_point(1, 5.0, 5.0)
    assert p1.layer == "Layout"
    assert job.layer("Layout") is not None, "default layer not auto-created"

    job.add_layer(PointLayer("Sleeves", color="#00ff00", category="sleeves"))
    expect(ValueError, job.add_layer, PointLayer("Sleeves"))
    p2 = job.add_point(1, 6.0, 6.0, layer="Sleeves")
    p3 = job.add_point(1, 7.0, 7.0, layer="Hangers")   # auto-created too
    assert job.layer("Hangers") is not None

    # rename repoints
    job.rename_layer("Sleeves", "Sleeves-L2")
    assert job.layer("Sleeves") is None
    assert job.layer("Sleeves-L2").category == "sleeves"
    assert p2.layer == "Sleeves-L2" and p3.layer == "Hangers"
    expect(ValueError, job.rename_layer, "nope", "x")
    expect(ValueError, job.rename_layer, "Hangers", "Layout")

    # PointLayer round-trip
    ly = job.layer("Sleeves-L2")
    assert PointLayer.from_dict(ly.to_dict()) == ly


def test_visibility_filtering(tmp):
    job = calibrated_job()
    pa = job.add_point(1, 110.0, 690.0)
    pb = job.add_point(1, 120.0, 680.0, layer="Ghost")
    job.layer("Ghost").visible = False

    out = os.path.join(tmp, "vis.csv")
    assert export_csv_pnezd(job, out) == 1, "hidden layer leaked into export"
    with open(out, newline="") as f:
        rows = list(csv.reader(f))
    assert len(rows) == 2 and rows[1][0] == job.composed(pa)

    # explicit points bypass the visibility filter
    assert export_csv_pnezd(job, out, points=[pa, pb]) == 2
    with open(out, newline="") as f:
        rows = list(csv.reader(f))
    assert len(rows) == 3 and rows[2][0] == job.composed(pb)

    # same rule holds for xlsx and dxf defaults
    assert export_xlsx(job, os.path.join(tmp, "vis.xlsx")) == 1
    assert export_dxf(job, os.path.join(tmp, "vis.dxf")) == 2  # POINT + TEXT


# ---------------------------------------------------------------- ACI hues --

def test_aci():
    assert aci_for("#ff0000") == 1
    assert aci_for("#ffff00") == 2
    assert aci_for("#00ff00") == 3
    assert aci_for("#00ffff") == 4
    assert aci_for("#0000ff") == 5
    assert aci_for("#ff00ff") == 6
    assert aci_for("#ffffff") == 7 and aci_for("#000000") == 7
    assert aci_for("#808080") == 8
    assert aci_for("#ff7f00") == 30
    assert aci_for("#fe0d05") == 1                    # near-red -> red
    assert aci_for("#d84c3f") == 30                   # default layer -> orange
    assert aci_for("#123ecc") == 5                    # dark blue -> blue
    assert aci_for("fff") == 7                        # bare short hex ok
    expect(ValueError, aci_for, "#12")
    expect(ValueError, aci_for, "not-a-color")
    for h, n in ACI_COLORS.items():                   # anchors map to selves
        assert aci_for(h) == n, (h, n)


# ---------------------------------------------------------------- to_world --

def test_to_world():
    job = calibrated_job()
    # 10 pt right and 20 pt above (page y down) the basepoint
    p = job.add_point(1, 110.0, 680.0, elev=12.5)
    n, e, z = job.to_world(p)
    assert abs(e - 2001.0) < 1e-9, e                  # E = 2000 + 10*0.1
    assert abs(n - 5002.0) < 1e-9, n                  # N = 5000 + 20*0.1
    assert z == 12.5

    # the basepoint itself maps to base_world exactly
    p0 = job.add_point(1, 100.0, 700.0)
    n0, e0, z0 = job.to_world(p0)
    assert abs(n0 - 5000.0) < 1e-9 and abs(e0 - 2000.0) < 1e-9 and z0 == 0.0

    # 90 CCW: (east', north') = (10, 20) rotates to (-20, 10)
    job.rotation_deg = 90.0
    n, e, _ = job.to_world(p)
    assert abs(e - 1998.0) < 1e-9, e                  # E = 2000 - 20*0.1
    assert abs(n - 5001.0) < 1e-9, n                  # N = 5000 + 10*0.1
    # inverse agrees under rotation
    x, y = job.from_world(n, e)
    assert abs(x - p.x) < 1e-9 and abs(y - p.y) < 1e-9
    job.rotation_deg = 0.0

    # bounds over both points: (min_N, min_E, max_N, max_E)
    assert job.bounds_world() == (5000.0, 2000.0, 5002.0, 2001.0)

    # no scale -> clear ValueError; bounds degrade to None
    bare = LayoutJob()
    q = bare.add_point(1, 1.0, 1.0)
    err = expect(ValueError, bare.to_world, q)
    assert "scale" in str(err).lower(), err
    expect(ValueError, bare.from_world, 0.0, 0.0)
    assert bare.bounds_world() is None
    assert LayoutJob().bounds_world() is None         # no points either


# ----------------------------------------------------------------- sidecar --

def test_sidecar(tmp):
    pdf = os.path.join(tmp, "plan.pdf")
    with open(pdf, "wb") as f:                        # job never opens the PDF
        f.write(b"%PDF-fake")
    job = calibrated_job(pdf)
    assert job.path == pdf + LayoutJob.SUFFIX
    job.units, job.prefix, job.suffix, job.pad = "m", "CP-", "-S", 4
    job.rotation_deg = 12.5
    p = job.add_point(3, 110.0, 680.0, elev=3.25,
                      desc="Ø16 ankarbult — östra axeln")   # unicode desc
    job.add_point(1, 50.0, 60.0, layer="Sleeves")

    # autosaved, atomic, versioned
    assert os.path.isfile(job.path), "autosave did not write the sidecar"
    assert not os.path.exists(job.path + ".part"), "temp file left behind"
    with open(job.path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["version"] == 1 and len(data["points"]) == 2

    # a fresh job on the same PDF autoloads everything
    job2 = LayoutJob(pdf)
    assert len(job2.points) == 2 and len(job2.layers) == 2
    assert (job2.units, job2.prefix, job2.suffix, job2.pad) == \
        ("m", "CP-", "-S", 4)
    assert job2.next_num == job.next_num == 3
    assert job2.base_page_xy == (100.0, 700.0)
    assert job2.base_world == (5000.0, 2000.0)
    assert job2.rotation_deg == 12.5
    assert isinstance(job2.scale, dict) and job2.cal.real_per_pt == 0.1
    q = job2.get(p.id)
    assert q is not None and q.desc == "Ø16 ankarbult — östra axeln"
    assert (q.page, q.x, q.y, q.elev) == (3, 110.0, 680.0, 3.25)
    assert q.created == p.created

    # in-memory job cannot autosave but saves to an explicit path
    mem = LayoutJob()
    mem.add_point(1, 0.0, 0.0)
    expect(ValueError, mem.save)
    side = os.path.join(tmp, "explicit.stitch.json")
    mem.save(side)
    mem2 = LayoutJob()
    mem2.load(side)
    assert len(mem2.points) == 1

    # cal property setter stores a plain dict
    mem2.cal = ScaleCal(real_per_pt=0.5, unit="m")
    assert mem2.scale == {"real_per_pt": 0.5, "unit": "m"}
    mem2.cal = None
    assert mem2.scale is None


# --------------------------------------------------------------------- CSV --

def test_csv(tmp):
    job = calibrated_job()
    job.prefix, job.suffix = "CP-", "-S"
    p1 = job.add_point(1, 110.0, 680.0, elev=12.5, desc="anchor bolt")
    p2 = job.add_point(1, 130.0, 720.0, elev=0.0, category="control")
    out = os.path.join(tmp, "points.csv")
    n = export_csv_pnezd(job, out)
    assert n == 2 and not os.path.exists(out + ".part")

    with open(out, newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["Point", "Northing", "Easting", "Elevation",
                       "Description"]
    assert rows[1] == ["CP-001-S", "5002.000", "2001.000", "12.500",
                       "anchor bolt"], rows[1]
    # desc falls back to category, then layer
    assert rows[2] == ["CP-002-S", "4998.000", "2003.000", "0.000",
                       "control"], rows[2]

    # header=False and alternate delimiter
    out2 = os.path.join(tmp, "points.txt")
    assert export_csv_pnezd(job, out2, header=False, delimiter="\t") == 2
    with open(out2, newline="") as f:
        raw = list(csv.reader(f, delimiter="\t"))
    assert len(raw) == 2 and raw[0][0] == "CP-001-S"

    # import round-trip into a fresh job with the same georeference
    back = calibrated_job()
    got = import_csv(back, out, log=lambda m: None)
    assert got == 2 and len(back.points) == 2
    for orig in (p1, p2):
        match = [q for q in back.points if q.num == orig.num]
        assert len(match) == 1, (orig.num, [q.num for q in back.points])
        q = match[0]
        assert abs(q.x - orig.x) < 0.01 and abs(q.y - orig.y) < 0.01, \
            (orig.x, orig.y, q.x, q.y)
        assert (q.prefix, q.suffix) == ("CP-", "-S"), (q.prefix, q.suffix)
        assert abs(q.elev - orig.elev) < 1e-9
    assert back.next_num == 3

    # headerless positional PNEZD is accepted; junk rows are skipped
    raw_csv = os.path.join(tmp, "field.csv")
    with open(raw_csv, "w", newline="") as f:
        f.write("7,5002.000,2001.000,3.500,hanger row\n")
        f.write("XX,not,numbers,at,all\n")
        f.write("\n")
    logged = []
    fresh = calibrated_job()
    assert import_csv(fresh, raw_csv, log=logged.append) == 1
    assert logged and "skipped" in logged[0]
    q = fresh.points[0]
    assert q.num == 7 and abs(q.x - 110.0) < 0.01 and abs(q.y - 680.0) < 0.01
    assert q.desc == "hanger row" and q.elev == 3.5

    # no scale -> ValueError (both directions of the conversion)
    expect(ValueError, import_csv, LayoutJob(), out)
    expect(ValueError, export_csv_pnezd, LayoutJob_with_point(), out)


def LayoutJob_with_point():
    job = LayoutJob()
    job.add_point(1, 1.0, 2.0)
    return job


# -------------------------------------------------------------------- XLSX --

_XLSX_HEADER = ["Point", "Prefix", "Number", "Suffix", "X (Easting)",
                "Y (Northing)", "Z (Elevation)", "Description", "Category",
                "Layer"]


def _cell_text(cell):
    """Inline-string or numeric cell -> its text content."""
    for tag in ("is/t", "v"):
        el = cell.find("./" + "/".join("{*}" + p for p in tag.split("/")))
        if el is not None:
            return el.text or ""
    return ""


def test_xlsx(tmp):
    job = calibrated_job()
    job.prefix, job.suffix = "CP-", "-S"
    job.add_point(1, 110.0, 680.0, elev=12.5, desc="anchor bolt",
                  category="anchors")
    job.add_point(2, 100.0, 700.0)
    out = os.path.join(tmp, "points.xlsx")
    assert export_xlsx(job, out) == 2
    assert not os.path.exists(out + ".part")

    with zipfile.ZipFile(out) as zf:
        assert zf.testzip() is None, "corrupt zip member"
        names = set(zf.namelist())
        for required in ("[Content_Types].xml", "_rels/.rels",
                         "xl/workbook.xml", "xl/_rels/workbook.xml.rels",
                         "xl/worksheets/sheet1.xml"):
            assert required in names, (required, names)
        # every part parses as XML
        for name in names:
            ET.fromstring(zf.read(name))
        ct = ET.fromstring(zf.read("[Content_Types].xml"))
        assert ct.tag.endswith("}Types"), ct.tag
        sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))

    rows = sheet.findall(".//{*}row")
    assert len(rows) == 3                               # header + 2 points
    header = [_cell_text(c) for c in rows[0].findall("{*}c")]
    assert header == _XLSX_HEADER, header
    r2 = [_cell_text(c) for c in rows[1].findall("{*}c")]
    assert r2[0] == "CP-001-S" and r2[1] == "CP-" and r2[3] == "-S"
    assert float(r2[2]) == 1                            # Number is numeric
    assert float(r2[4]) == 2001.0, r2                   # X (Easting)
    assert float(r2[5]) == 5002.0, r2                   # Y (Northing)
    assert float(r2[6]) == 12.5, r2                     # Z (Elevation)
    assert r2[7] == "anchor bolt" and r2[8] == "anchors" and r2[9] == "Layout"
    # numeric cells carry no inline-string marker
    num_cell = rows[1].findall("{*}c")[4]
    assert num_cell.get("t") is None and num_cell.find("{*}v") is not None
    # XML-hostile text survives escaping
    job.add_point(1, 100.0, 700.0, desc='<&"> tie-in')
    assert export_xlsx(job, out) == 3
    with zipfile.ZipFile(out) as zf:
        sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
    descs = [_cell_text(r.findall("{*}c")[7])
             for r in sheet.findall(".//{*}row")[1:]]
    assert '<&"> tie-in' in descs, descs


# --------------------------------------------------------------------- DXF --

def _dxf_pairs(path):
    with open(path, encoding="ascii") as f:
        lines = [ln.rstrip("\r\n") for ln in f]
    assert len(lines) % 2 == 0, "DXF must be (code, value) line pairs"
    return [(int(c), v) for c, v in zip(lines[0::2], lines[1::2])]


def test_dxf(tmp):
    job = calibrated_job()
    job.prefix = "CP-"
    job.add_point(1, 110.0, 680.0, elev=12.5)                # Layout layer
    job.add_layer(PointLayer("Sleeves", color="#00ff00"))
    job.add_point(1, 130.0, 720.0, layer="Sleeves")
    out = os.path.join(tmp, "points.dxf")
    assert export_dxf(job, out) == 4                          # 2 POINT + 2 TEXT
    assert not os.path.exists(out + ".part")

    pairs = _dxf_pairs(out)
    assert pairs[-1] == (0, "EOF"), pairs[-1]
    assert (9, "$ACADVER") in pairs and (1, "AC1009") in pairs

    # LAYER table entries with expected colors: default #d84c3f -> 30,
    # Sleeves #00ff00 -> 3
    layer_colors = {}
    for i, pr in enumerate(pairs):
        if pr == (0, "LAYER"):
            entry = dict(pairs[i + 1:i + 5])
            layer_colors[entry[2]] = int(entry[62])
            assert entry[6] == "CONTINUOUS", entry
    assert layer_colors == {"Layout": 30, "Sleeves": 3}, layer_colors

    # POINT entities at (E, N, Z) on their layer
    points = []
    for i, pr in enumerate(pairs):
        if pr == (0, "POINT"):
            entry = dict(pairs[i + 1:i + 5])
            points.append((entry[8], float(entry[10]), float(entry[20]),
                           float(entry[30])))
    assert len(points) == 2, points
    assert points[0] == ("Layout", 2001.0, 5002.0, 12.5), points[0]
    assert points[1][0] == "Sleeves"

    # TEXT labels: composed ids, positive height, small offset from the point
    texts = []
    for i, pr in enumerate(pairs):
        if pr == (0, "TEXT"):
            entry = dict(pairs[i + 1:i + 7])
            texts.append(entry)
            assert float(entry[40]) > 0, entry
    assert [t[1] for t in texts] == ["CP-001", "CP-002"], texts
    assert 0 < float(texts[0][10]) - 2001.0 <= 1.5, texts[0]


# --------------------------------------------------------------------- kits --

def test_kits(tmp):
    job = calibrated_job()
    job.add_point(1, 110.0, 680.0)
    job.add_point(1, 120.0, 690.0, layer="Ghost")
    job.layer("Ghost").visible = False

    kit_dir = os.path.join(tmp, "kit_ch")
    res = export_kit(job, kit_dir, "clovehitch", stem="bldg7")
    assert res["points"] == 1, res
    assert sorted(os.path.basename(f) for f in res["files"]) == \
        ["bldg7.dxf", "bldg7.xlsx"], res["files"]
    assert sorted(os.listdir(kit_dir)) == ["bldg7.dxf", "bldg7.xlsx"], \
        "clovehitch must write exactly xlsx+dxf"
    for f in res["files"]:
        assert os.path.getsize(f) > 0

    full_dir = os.path.join(tmp, "kit_fs")
    res2 = export_kit(job, full_dir, "fullspool")
    assert len(res2["files"]) == 4 and res2["points"] == 1
    assert sorted(os.listdir(full_dir)) == \
        ["layout.csv", "layout.dxf", "layout.json", "layout.xlsx"]
    # the json is the whole job (both points), and export_job_json says so
    with open(os.path.join(full_dir, "layout.json"), encoding="utf-8") as f:
        blob = json.load(f)
    assert blob["version"] == 1 and len(blob["points"]) == 2
    assert export_job_json(job, os.path.join(tmp, "job.json")) == 2

    expect(ValueError, export_kit, job, tmp, "grannyknot")
    assert set(KITS) == {"bowline", "clovehitch", "fullspool",
                         "sheetbend", "marlinspike"}   # A2 adds wire kits
    # nothing leaves temp files behind
    leftovers = [f for _, _, fs in os.walk(tmp) for f in fs
                 if f.endswith(".part")]
    assert not leftovers, leftovers


def main():
    tmp = tempfile.mkdtemp(prefix="fieldstitch_")
    test_numbering()
    test_layers()
    test_visibility_filtering(tmp)
    test_aci()
    test_to_world()
    test_sidecar(tmp)
    test_csv(tmp)
    test_xlsx(tmp)
    test_dxf(tmp)
    test_kits(tmp)
    print("FIELDSTITCH TESTS PASSED  (numbering/renumber, layers + rename + "
          "visibility, to_world basepoint/rotation/no-scale, sidecar "
          "round-trip atomic, PNEZD CSV export/import round-trip, OOXML xlsx "
          "parse, DXF R12 reparse, kits)")
    print("outputs in", tmp)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("FIELDSTITCH TEST FAILED:", e)
        sys.exit(1)
