"""Regression tests for surveyor-export integrity in
rfi_stamper.fieldstitch — the injection/corruption findings:

* #13 / #33 DXF: a CR/LF in a layer name or point label injected group
  codes and desynced the (70, count) layer count.  _dxf_clean() collapses
  every control char so the file stays a clean (code, value) line stream.
* #22 CSV: a point name or description opening with = + - @ TAB CR was a
  spreadsheet formula-injection vector; _csv_safe() apostrophe-guards it.
* #23 XLSX: a non-finite coordinate (inf/nan) wrote a literal 'inf'/'nan'
  into a numeric <v> cell and corrupted the workbook; guarded to 0.

Plain python, no pytest.  Run:  python tests/test_reb_fieldstitch.py
"""
import csv
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper.fieldstitch import (             # noqa: E402
    LayoutJob, LayoutPoint, PointLayer, export_csv_pnezd, export_dxf,
    export_xlsx)
from rfi_stamper.markups.measure import ScaleCal  # noqa: E402


def calibrated_job():
    job = LayoutJob()
    job.base_page_xy = (100.0, 700.0)
    job.base_world = (5000.0, 2000.0)
    job.scale = ScaleCal(real_per_pt=0.1, unit="ft").to_dict()
    return job


def hostile_point(job, num, x, y, **kw):
    """add_point() now hard-validates composed labels at creation, so a
    hostile label can no longer enter through the public API — but it still
    arrives via a hand-edited sidecar or a tolerant import.  Inject one the
    way load() would, so the export guards stay covered."""
    p = LayoutPoint.new(num=num, page=1, x=x, y=y, **kw)
    job.points.append(p)
    return p


def _dxf_lines(path):
    with open(path, encoding="ascii") as f:
        return [ln.rstrip("\r\n") for ln in f]


def _dxf_pairs(path):
    lines = _dxf_lines(path)
    assert len(lines) % 2 == 0, "DXF must stay (code, value) line pairs"
    return [(int(c), v) for c, v in zip(lines[0::2], lines[1::2])]


# ------------------------------------------------------- #13 / #33 DXF ------

def test_dxf_layer_name_injection(tmp):
    """A CR/LF-laced layer name must not inject group codes nor desync the
    LAYER count; the file must still reparse as clean code/value pairs."""
    job = calibrated_job()
    # a layer name carrying a fake group-code injection payload
    evil = "Sleeves\r\n0\r\nLAYER\r\n2\r\nGHOST"
    job.add_layer(PointLayer(evil, color="#00ff00"))
    job.add_point(1, 110.0, 680.0, layer=evil)
    out = os.path.join(tmp, "inj_layer.dxf")
    assert export_dxf(job, out) == 2                     # POINT + TEXT

    # still an even, cleanly re-pairable stream (would raise if desynced)
    pairs = _dxf_pairs(out)
    assert pairs[-1] == (0, "EOF"), pairs[-1]

    # exactly one declared layer, matching the (70, count) and one LAYER row
    n_declared = [v for c, v in pairs if c == 70][0]
    n_layer_rows = sum(1 for pr in pairs if pr == (0, "LAYER"))
    assert n_declared == str(n_layer_rows) == "1", (n_declared, n_layer_rows)

    # the injected "GHOST" token never became a standalone value line
    assert "GHOST" not in [v for _, v in pairs], "injection leaked a token"
    # no raw newline survived inside any value
    for _, v in pairs:
        assert "\r" not in v and "\n" not in v, repr(v)


def test_dxf_label_injection(tmp):
    """A CR/LF in a composed point label (via prefix/suffix) must not inject
    a group code into the TEXT entity."""
    job = calibrated_job()
    hostile_point(job, 1, 110.0, 680.0, elev=1.0,
                  prefix="CP\r\n0\r\nLINE\r\n")
    out = os.path.join(tmp, "inj_label.dxf")
    assert export_dxf(job, out) == 2

    pairs = _dxf_pairs(out)                               # would raise if odd
    assert pairs[-1] == (0, "EOF")
    labels = [v for c, v in pairs if c == 1 and v not in ("AC1009",)]
    assert labels, "no TEXT label emitted"
    for v in labels:
        assert "\r" not in v and "\n" not in v, repr(v)
    assert "LINE" not in [v for _, v in pairs], "label injected a token"


# ---------------------------------------------------------- #22 CSV ---------

def test_csv_formula_injection(tmp):
    """Point name / description opening with a formula trigger get an
    apostrophe guard; numeric coordinate cells are untouched."""
    job = calibrated_job()
    hostile_point(job, 1, 110.0, 680.0, elev=12.5,        # dangerous point id
                  prefix="=cmd",
                  desc="=HYPERLINK(\"http://x\")")        # dangerous desc
    job.add_point(1, 130.0, 720.0, desc="+1+1", prefix="")
    job.add_point(1, 140.0, 660.0, desc="@SUM(A1)", prefix="")
    out = os.path.join(tmp, "inj.csv")
    assert export_csv_pnezd(job, out) == 3

    with open(out, newline="") as f:
        rows = list(csv.reader(f))
    body = rows[1:]
    # point id and desc are apostrophe-guarded
    assert body[0][0].startswith("'="), body[0][0]
    assert body[0][4].startswith("'="), body[0][4]
    assert body[1][4].startswith("'+"), body[1][4]
    assert body[2][4].startswith("'@"), body[2][4]
    # numeric coordinate cells are NOT mangled (still bare numbers)
    for r in body:
        for cell in r[1:4]:
            assert not cell.startswith("'"), cell
            float(cell)                                   # parses cleanly


# ---------------------------------------------------------- #23 XLSX --------

def test_xlsx_non_finite_coord(tmp):
    """A non-finite coordinate must not write a literal inf/nan into a
    numeric <v> cell (which corrupts the workbook)."""
    job = calibrated_job()
    job.add_point(1, 110.0, 680.0, elev=float("inf"))     # bad elevation
    out = os.path.join(tmp, "nonfinite.xlsx")
    assert export_xlsx(job, out) == 1

    with zipfile.ZipFile(out) as zf:
        assert zf.testzip() is None
        sheet_xml = zf.read("xl/worksheets/sheet1.xml")
    # must parse and contain no literal inf/nan in numeric cells
    ET.fromstring(sheet_xml)                               # would raise if bad
    low = sheet_xml.decode("utf-8").lower()
    assert "inf" not in low and "nan" not in low, sheet_xml

    # numeric <v> cells all parse as finite floats
    sheet = ET.fromstring(sheet_xml)
    for v in sheet.findall(".//{*}v"):
        f = float(v.text)
        assert f == f and abs(f) != float("inf"), v.text   # not nan/inf


def main():
    tmp = tempfile.mkdtemp(prefix="reb_fieldstitch_")
    test_dxf_layer_name_injection(tmp)
    test_dxf_label_injection(tmp)
    test_csv_formula_injection(tmp)
    test_xlsx_non_finite_coord(tmp)
    print("REB FIELDSTITCH TESTS PASSED  (DXF layer/label injection, CSV "
          "formula-injection guard, XLSX non-finite coordinate guard) OK")
    print("outputs in", tmp)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("REB FIELDSTITCH TEST FAILED:", e)
        sys.exit(1)
    raise SystemExit(0)
