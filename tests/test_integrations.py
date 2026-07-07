"""Tests for rfi_stamper.integrations (file-based bridges: CSV, ICS, bundle
zip, drop-folder sweep). Plain python, no pytest."""
from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# project.py is developed in parallel with integrations.py: poll briefly so
# a fresh checkout mid-land does not flake this script.
_PROJECT_PY = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "rfi_stamper", "project.py")
for _ in range(30):
    if os.path.exists(_PROJECT_PY):
        break
    time.sleep(2)
else:
    print("rfi_stamper/project.py never appeared; cannot test integrations")
    raise SystemExit(1)

import fitz

from rfi_stamper.integrations import (REGISTRY, export_budget_csv,
                                      export_bundle, export_change_orders_csv,
                                      export_punch_csv, export_schedule_ics,
                                      export_tasks_csv, import_bundle,
                                      import_tasks_csv, scan_drop_folder)
from rfi_stamper.project import (BudgetLine, ChangeOrder, Project, PunchItem,
                                 ScheduleItem, Task)

quiet = lambda *a, **k: None  # noqa: E731

UNICODE_TITLE = "Überprüfung — Schacht Süd (Löschwasser)"
COMMA_TITLE = "Pour slab, level 2; verify embeds"
LONG_TITLE = ("Overhead rough-in — corridors, wings A/B: coordinate "
              "électrical & mechanical trades before the cover inspection, "
              "north stair")


def build_project() -> Project:
    p = Project()
    p.add("tasks", Task.new(title=UNICODE_TITLE, assignee="plumbing-foreman",
                            status="doing", due="2026-07-15", priority="high",
                            desc="verify riser clearance"))
    p.add("tasks", Task.new(title="Chase RFI answer", assignee="pm-desk",
                            status="todo", due="2026-07-09"))
    p.add("tasks", Task.new(title="Re-stamp sheet P-201", status="done"))
    p.add("schedule", ScheduleItem.new(title=COMMA_TITLE, start="2026-07-10",
                                       end="2026-07-12", crew="crew-a",
                                       pct=25.0))
    p.add("schedule", ScheduleItem.new(title=LONG_TITLE, start="2026-07-20",
                                       end="2026-07-24", crew="crew-b"))
    p.add("schedule", ScheduleItem.new(title="unscheduled scope"))  # no dates
    p.add("punch", PunchItem.new(title="Patch core drill", location="Rm 214",
                                 sheet="P-201", assignee="crew-a"))
    p.add("punch", PunchItem.new(title="Missing escutcheon", location="Rm 118",
                                 status="ready"))
    p.add("budget", BudgetLine.new(code="22-05-00", desc="Piping rough-in",
                                   budget=125000.0, committed=90000.0,
                                   spent=41250.5))
    p.add("budget", BudgetLine.new(code="22-40-00", desc="Fixtures",
                                   budget=64000.0))
    p.add("change_orders", ChangeOrder.new(number="CO-001",
                                           title="Reroute at beam conflict",
                                           amount=4820.0, status="approved",
                                           days_impact=2))
    p.add("change_orders", ChangeOrder.new(number="CO-002",
                                           title="Added cleanouts",
                                           amount=1310.75))
    return p


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def test_registry():
    by_key = {c.key: c for c in REGISTRY}
    want = {"csv-tasks": "both", "csv-punch": "export",
            "csv-budget": "export", "csv-change-orders": "export",
            "ics-schedule": "export", "json-bundle": "both",
            "drop-folder": "import"}
    assert set(by_key) == set(want), set(by_key)
    for key, direction in want.items():
        c = by_key[key]
        assert c.direction == direction, (key, c.direction)
        assert c.name and c.formats, key
        assert "no cloud" in c.desc.lower(), f"{key}: desc must say no cloud"
    print("  registry OK")


def test_tasks_roundtrip(tmp):
    proj = build_project()
    out = os.path.join(tmp, "tasks.csv")
    assert export_tasks_csv(proj, out, log=quiet) == 3
    fresh = Project()
    assert import_tasks_csv(fresh, out, log=quiet) == 3
    key = lambda t: (t.title, t.assignee, t.status, t.due)  # noqa: E731
    assert [key(t) for t in fresh.items("tasks")] == \
        [key(t) for t in proj.items("tasks")]
    assert fresh.items("tasks")[0].title == UNICODE_TITLE
    print("  tasks CSV round-trip OK")


def test_tasks_import_tolerant(tmp):
    # foreign header spellings, mixed case; one row without a title -> skipped
    path = os.path.join(tmp, "foreign.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Name", "Owner", "Date", "STATUS", "Priority"])
        w.writerow(["Hang unistrut", "crew-c", "2026-08-01", "todo", "low"])
        w.writerow(["", "nobody", "2026-08-02", "todo", "high"])   # skipped
        w.writerow(["Test riser", "crew-a", "2026-08-03", "doing", "high"])
    proj = Project()
    assert import_tasks_csv(proj, path, log=quiet) == 2
    t = proj.items("tasks")[0]
    assert (t.title, t.assignee, t.due, t.status, t.priority) == \
        ("Hang unistrut", "crew-c", "2026-08-01", "todo", "low")
    assert t.id and t.created            # Task.new filled the rest
    # no recognizable title column -> hard error
    bad = os.path.join(tmp, "bad.csv")
    with open(bad, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(["foo", "bar"])
    try:
        import_tasks_csv(proj, bad, log=quiet)
        raise AssertionError("expected ValueError for missing title column")
    except ValueError:
        pass
    print("  tolerant task import OK")


def test_flat_csv_exports(tmp):
    proj = build_project()
    cases = [
        (export_punch_csv, "punch.csv", 2,
         ["id", "title", "location", "sheet", "status", "assignee",
          "photo_path", "created"]),
        (export_budget_csv, "budget.csv", 2,
         ["id", "code", "desc", "budget", "committed", "spent"]),
        (export_change_orders_csv, "cos.csv", 2,
         ["id", "number", "title", "amount", "status", "days_impact",
          "created"]),
    ]
    for fn, name, want_rows, want_header in cases:
        out = os.path.join(tmp, name)
        assert fn(proj, out, log=quiet) == want_rows, name
        rows = read_csv(out)
        assert rows[0] == want_header, (name, rows[0])
        assert len(rows) == want_rows + 1, name
    rows = read_csv(os.path.join(tmp, "budget.csv"))
    assert float(rows[1][3]) == 125000.0 and float(rows[1][5]) == 41250.5
    rows = read_csv(os.path.join(tmp, "cos.csv"))
    assert rows[1][1] == "CO-001" and int(rows[1][5]) == 2
    print("  punch/budget/CO CSV exports OK")


def test_ics(tmp):
    proj = build_project()
    out = os.path.join(tmp, "schedule.ics")
    assert export_schedule_ics(proj, out, log=quiet) == 2  # undated skipped
    raw = open(out, "rb").read()
    assert raw.endswith(b"\r\n")
    # pure CRLF: every \n is part of a \r\n pair
    assert raw.count(b"\n") == raw.count(b"\r\n"), "found bare LF line ends"
    lines = raw[:-2].split(b"\r\n")
    assert all(len(ln) <= 75 for ln in lines), \
        f"unfolded line of {max(len(ln) for ln in lines)} octets"
    text = raw.decode("utf-8")
    unfolded = text.replace("\r\n ", "").split("\r\n")
    assert unfolded[0] == "BEGIN:VCALENDAR"
    assert unfolded[-2] == "END:VCALENDAR"    # -1 is "" after final CRLF
    assert unfolded.count("BEGIN:VEVENT") == 2
    assert unfolded.count("END:VEVENT") == 2
    summaries = [ln for ln in unfolded if ln.startswith("SUMMARY:")]
    assert len(summaries) == 2, summaries
    assert "SUMMARY:Pour slab\\, level 2\\; verify embeds" in summaries
    # the long unicode title survives folding intact, commas escaped
    want_long = "SUMMARY:" + LONG_TITLE.replace(",", "\\,")
    assert want_long in summaries, summaries
    # all-day events: DATE values, DTEND exclusive (end date + 1)
    assert "DTSTART;VALUE=DATE:20260710" in unfolded
    assert "DTEND;VALUE=DATE:20260713" in unfolded
    assert any(ln.startswith("UID:") for ln in unfolded)
    print("  ICS export OK")


def test_bundle_roundtrip(tmp):
    proj = build_project()
    out = os.path.join(tmp, "proj_bundle.zip")
    assert export_bundle(proj, out, log=quiet) == out
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        assert {"project.json", "manifest.json"} <= names, names
        manifest = json.loads(z.read("manifest.json"))
        data = json.loads(z.read("project.json"))
    assert manifest["version"] == 1
    for kind, n in {"tasks": 3, "schedule": 3, "punch": 2, "budget": 2,
                    "change_orders": 2}.items():
        assert kind in manifest["kinds"], kind
        assert manifest["counts"][kind] == n, (kind, manifest["counts"])
        assert len(data[kind]) == n, kind
    back = import_bundle(out, log=quiet)
    for kind in ("tasks", "schedule", "punch", "budget", "change_orders"):
        assert len(back.items(kind)) == len(proj.items(kind)), kind
    titles = [t.title for t in back.items("tasks")]
    assert UNICODE_TITLE in titles, titles            # unicode survived
    assert back.items("budget")[0].spent == 41250.5
    assert back.items("schedule")[0].title == COMMA_TITLE
    # not-a-bundle zip -> hard error
    junk = os.path.join(tmp, "junk.zip")
    with zipfile.ZipFile(junk, "w") as z:
        z.writestr("readme.txt", "nope")
    try:
        import_bundle(junk, log=quiet)
        raise AssertionError("expected ValueError for zip sans project.json")
    except ValueError:
        pass
    print("  bundle round-trip OK")


def make_pdf(path, n_pages, pad_bytes=0):
    doc = fitz.open()
    for i in range(n_pages):
        page = doc.new_page()
        page.insert_text(fitz.Point(72, 72), f"sheet {i + 1}")
    doc.save(path)
    doc.close()
    if pad_bytes:                        # bloat past the size threshold; the
        with open(path, "ab") as f:      # trailing comment keeps fitz happy
            f.write(b"\n%" + b"x" * pad_bytes)
    return path


def test_scan_drop_folder(tmp):
    drop = os.path.join(tmp, "drop")
    os.makedirs(drop)
    make_pdf(os.path.join(drop, "planset.pdf"), 10)          # pages -> plans
    make_pdf(os.path.join(drop, "big_single.pdf"), 1,
             pad_bytes=2_500_000)                            # size  -> plans
    make_pdf(os.path.join(drop, "rfi_017.pdf"), 1)           # small -> rfis
    with zipfile.ZipFile(os.path.join(drop, "rfi_pkg.pdf"), "w") as z:
        z.writestr("page_1.jpg", b"\xff\xd8\xff\xe0 not a real jpeg")
        z.writestr("page_1.txt", "RFI 017 ocr text")         # zip-as-.pdf
    with open(os.path.join(drop, "rfi_raw.txt"), "w") as f:
        f.write("RFI 018\nQuestion: ...\n")
    with open(os.path.join(drop, "site_photo.png"), "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    os.makedirs(os.path.join(drop, "subdir"))                # ignored
    res = scan_drop_folder(drop, log=quiet)
    assert set(res) == {"rfis", "plans", "other"}
    names = {k: sorted(os.path.basename(p) for p in v) for k, v in res.items()}
    assert names["plans"] == ["big_single.pdf", "planset.pdf"], names
    assert names["rfis"] == ["rfi_017.pdf", "rfi_pkg.pdf", "rfi_raw.txt"], names
    assert names["other"] == ["site_photo.png"], names
    assert all(os.path.isabs(p) for v in res.values() for p in v)
    print("  drop-folder scan OK")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        test_registry()
        test_tasks_roundtrip(tmp)
        test_tasks_import_tolerant(tmp)
        test_flat_csv_exports(tmp)
        test_ics(tmp)
        test_bundle_roundtrip(tmp)
        test_scan_drop_folder(tmp)
    print("INTEGRATIONS TESTS PASSED")


if __name__ == "__main__":
    main()
