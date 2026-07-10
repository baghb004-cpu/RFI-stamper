"""The staged field round: PNEZD XLSX in the Bowline kit + the setup advisor.

* export_xlsx(dialect="pnezd") writes Point/Northing/Easting/Elevation
  columns — N BEFORE E, pulled through selvage.WRITER_ORDER — and the
  Bowline kit now carries it beside the PNEZD CSV and DXF.
* fieldpro.station_geometry applies the training rules: 45°–135° good
  triangle, controls at least as far as the work, backsight reminders;
  fewer than two controls is INSUFFICIENT, never guessed around.

Run:  python3.12 tests/test_stagedfield.py
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper import fieldpro                       # noqa: E402
from rfi_stamper import fieldstitch as fs              # noqa: E402
from rfi_stamper.markups.measure import ScaleCal       # noqa: E402
from rfi_stamper.selvage import WRITER_ORDER           # noqa: E402

_N = [0]


def A(cond, msg=""):
    _N[0] += 1
    assert cond, msg


TMP = tempfile.mkdtemp(prefix="stagedfield_test_")


def _job():
    job = fs.LayoutJob(None)
    job.base_page_xy = (100.0, 700.0)
    job.base_world = (5000.0, 2000.0)
    job.units = "ft"
    job.scale = ScaleCal(real_per_pt=0.1, unit="ft").to_dict()
    job.add_point(1, 110.0, 690.0, desc="COL A1", elev=12.5)
    job.add_point(1, 160.0, 640.0, desc="COL B2", elev=None)
    return job


def test_xlsx_pnezd():
    A(WRITER_ORDER["xlsx_pnezd"] == ("n", "e", "z"),
      "PNEZD spreadsheet order lives in the ONE writer table")
    job = _job()
    out = os.path.join(TMP, "pts.xlsx")
    n = fs.export_xlsx(job, out, dialect="pnezd")
    A(n == 2, "two rows exported")
    with zipfile.ZipFile(out) as zf:
        sheet = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
    A(">Northing<" in sheet and ">Easting<" in sheet,
      "PNEZD headers present")
    A(sheet.index("Northing") < sheet.index("Easting"),
      "N column BEFORE E (the swapped import mirrors silently)")
    # first data point: world N = 5000 - 10*... verify N/E values order
    p = job.points[0]
    wn, we, wz = job.to_world(p)
    row = sheet[sheet.index('<row r="2">'):sheet.index('<row r="3">')]
    vals = re.findall(r"<v>([-\d.]+)</v>", row)
    A(vals and abs(float(vals[0]) - wn) < 1e-6
      and abs(float(vals[1]) - we) < 1e-6,
      f"row carries N then E: {vals[:3]} vs ({wn}, {we})")
    A(f"{wz:.3f}" in row, "elevation carried")
    # the grid dialect is untouched (X=Easting first for grid tablets)
    out2 = os.path.join(TMP, "grid.xlsx")
    fs.export_xlsx(job, out2)
    with zipfile.ZipFile(out2) as zf:
        s2 = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
    A("X (Easting)" in s2 and s2.index("X (Easting)")
      < s2.index("Y (Northing)"), "grid dialect unchanged")


def test_bowline_kit_carries_xlsx():
    job = _job()
    d = os.path.join(TMP, "kit_bowline")
    res = fs.export_kit(job, d, "bowline")
    A(sorted(os.listdir(d)) == ["layout.csv", "layout.dxf", "layout.xlsx"],
      f"bowline = PNEZD CSV + PNEZD XLSX + DXF: {sorted(os.listdir(d))}")
    A(res["points"] == 2, "points counted once")
    with zipfile.ZipFile(os.path.join(d, "layout.xlsx")) as zf:
        sheet = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
    A(">Northing<" in sheet, "the kit XLSX is the PNEZD dialect")


def test_station_geometry():
    # a good setup: two controls ~90° apart, farther than the work
    g = fieldpro.station_geometry(
        (0.0, 0.0), [("CP-1", 200.0, 0.0), ("CP-2", 0.0, 220.0)],
        layout=[(50.0, 40.0), (80.0, 10.0)])
    A(g["verdict"] == "GOOD", f"good triangle passes: {g['notes']}")
    A(g["pairs"][0]["ok"] and abs(g["pairs"][0]["angle_deg"] - 90.0) < 0.1,
      f"angle measured: {g['pairs']}")
    A(all(c["ok"] for c in g["controls"]), "distance rule passes")
    A(len(g["reminders"]) == 5 and any("every hour" in r
                                       for r in g["reminders"]),
      "backsight reminders ride along")
    # too flat: controls nearly collinear through the instrument
    flat = fieldpro.station_geometry(
        (0.0, 0.0), [("CP-1", 100.0, 5.0), ("CP-2", -100.0, 5.0)])
    A(flat["verdict"] == "WEAK" and not flat["pairs"][0]["ok"],
      f"flat triangle fails: {flat['pairs']}")
    A(any("too flat" in n for n in flat["notes"]), flat["notes"])
    # too sharp
    sharp = fieldpro.station_geometry(
        (0.0, 0.0), [("CP-1", 100.0, 0.0), ("CP-2", 100.0, 20.0)])
    A(sharp["verdict"] == "WEAK"
      and any("too sharp" in n for n in sharp["notes"]), sharp["notes"])
    # control closer than the farthest layout point: named
    near = fieldpro.station_geometry(
        (0.0, 0.0), [("CP-1", 60.0, 0.0), ("CP-2", 0.0, 70.0)],
        layout=[(150.0, 150.0)])
    A(near["verdict"] == "WEAK"
      and any("extrapolates" in n for n in near["notes"]), near["notes"])
    # one control: INSUFFICIENT, never guessed
    one = fieldpro.station_geometry((0.0, 0.0), [("CP-1", 100.0, 0.0)])
    A(one["verdict"] == "INSUFFICIENT"
      and "needs two" in one["notes"][0], one["notes"])
    # deterministic
    A(g == fieldpro.station_geometry(
        (0.0, 0.0), [("CP-1", 200.0, 0.0), ("CP-2", 0.0, 220.0)],
        layout=[(50.0, 40.0), (80.0, 10.0)]), "deterministic")


# --------------------------------------------------------------------------- #

def main():
    tests = [
        (test_xlsx_pnezd, "PNEZD XLSX dialect: N before E via WRITER_ORDER"),
        (test_bowline_kit_carries_xlsx, "the Bowline kit carries the "
                                        "RTS spreadsheet"),
        (test_station_geometry, "setup advisor: triangle/distance rules, "
                                "reminders, honest INSUFFICIENT"),
    ]
    for fn, label in tests:
        fn()
        print(f"PASS {label}")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"STAGED FIELD TEST PASSED  ({_N[0]} checks)")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("STAGED FIELD TEST FAILED:", e)
        sys.exit(1)
