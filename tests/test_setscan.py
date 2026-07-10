"""The Cut Ticket set-scan — tags -> preliminary fixture schedule.

* The fixture-schedule table on a legend sheet parses into tag +
  description + callout rows (wrapped description lines append).
* Symbol lane: tags beside Reed Count-recognized symbols on Story
  Pole-verified sheets count; tag text alone NEVER fabricates a fixture
  ("SEE 2/P-1" decoys, sheet-number collisions, unverified sheets).
* sync_scan reconciles as proposals: pre-filled callouts on create only,
  human edits survive re-scans, orphans tombstone, model + scan lanes
  merge per-source.

Run:  python3.12 tests/test_setscan.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz                                           # noqa: E402

from rfi_stamper import cutticket, draft              # noqa: E402
from rfi_stamper.project import Project               # noqa: E402

_N = [0]


def A(cond, msg=""):
    _N[0] += 1
    assert cond, msg


TMP = tempfile.mkdtemp(prefix="setscan_test_")


def _legend(path):
    doc = fitz.open()
    pg = doc.new_page(width=792, height=612)
    pg.insert_text((200, 60), "PLUMBING FIXTURE SCHEDULE", fontsize=12)
    cols = [(60, "MARK"), (130, "DESCRIPTION"), (420, "MANUFACTURER"),
            (560, "MODEL")]
    for x, t in cols:
        pg.insert_text((x, 100), t, fontsize=9)
    rows = [("WC-1", "WATER CLOSET, FLOOR MOUNT", "MAKER A", "3351.101"),
            ("L-1", "LAVATORY, WALL HUNG", "MAKER A", "0355.012"),
            ("MS-1", "MOP SINK, 24X24 TERRAZZO", "MAKER B", "TSB-100")]
    y = 120
    for tag, desc, mfr, model in rows:
        for (x, _), val in zip(cols, (tag, desc, mfr, model)):
            pg.insert_text((x, y), val, fontsize=8)
        y += 18
    pg.insert_text((130, y), "WITH RIM GUARD", fontsize=8)   # wrapped line
    doc.save(path)
    doc.close()
    return path


def _plan(path):
    """Tagged fixtures + dims + doors through the real plate pipeline."""
    m = draft.DraftModel()
    m.add("wall", [(0, 0), (40, 0)], wtype="stud4")
    m.add("wall", [(0, 0), (0, 30)], wtype="stud4")
    w1 = m.add("wall", [(40, 0), (40, 30)], wtype="stud4")
    for pts in ([(0, 0), (40, 0), (20, -4)], [(0, 0), (0, 30), (-4, 15)],
                [(0, 0), (12, 0), (6, -8)], [(12, 0), (40, 0), (26, -8)],
                [(0, 30), (40, 30), (20, 34)]):
        m.add("dim", pts)
    m.add("door", [], host=w1.id, t=0.5, width_in=36.0)
    m.add("fixture", [(6, 5)], stencil="wc", tag="WC-1")
    m.add("fixture", [(12, 5)], stencil="wc", tag="WC-1")
    m.add("fixture", [(18, 5)], stencil="lav", tag="L-1")
    m.add("fixture", [(30, 5)], stencil="fd", tag="FD-1")
    m.add("fixture", [(24, 16)], stencil="sink_d")        # untagged
    m.add("text", [(20, 22)], text="SEE 2/P-1")           # the classic decoy
    draft.plate_pdf(m, path)
    return path


def _set(path):
    out = fitz.open()
    for p in (_legend(os.path.join(TMP, "legend.pdf")),
              _plan(os.path.join(TMP, "plan.pdf"))):
        src = fitz.open(p)
        out.insert_pdf(src)
        src.close()
    out.save(path)
    out.close()
    return path


def test_schedule_table():
    doc = fitz.open(_legend(os.path.join(TMP, "sched.pdf")))
    rows = cutticket.schedule_rows(doc[0])
    A([r["tag"] for r in rows] == ["WC-1", "L-1", "MS-1"],
      f"schedule tags: {[r['tag'] for r in rows]}")
    A(rows[0]["callout"] == "MAKER A 3351.101", rows[0])
    A("WITH RIM GUARD" in rows[2]["description"],
      f"wrapped description appends: {rows[2]}")
    A(cutticket.schedule_rows(doc[0]) == rows, "deterministic")
    doc.close()
    # a sheet with no schedule header parses to nothing
    d2 = fitz.open()
    p2 = d2.new_page(width=612, height=792)
    p2.insert_text((100, 100), "GENERAL NOTES", fontsize=10)
    A(cutticket.schedule_rows(p2) == [], "no header, no rows")
    d2.close()


def test_scan_lanes_and_rejects():
    scan = cutticket.scan_set(_set(os.path.join(TMP, "set.pdf")))
    rows = {r["tag"]: r for r in scan["rows"]}
    # symbol lane: counts from recognized symbols beside their tags
    A(rows["WC-1"]["count"] == 2 and rows["WC-1"]["stencil"] == "wc"
      and rows["WC-1"]["prefix"] == 1, f"WC-1: {rows.get('WC-1')}")
    A(rows["L-1"]["count"] == 1 and rows["L-1"]["prefix"] == 3, "L-1")
    A(rows["FD-1"]["count"] == 1, "FD-1")
    # schedule lane: MS-1 exists only in the schedule -> count 0, callout
    A(rows["MS-1"]["count"] == 0 and rows["MS-1"]["prefix"] == -1
      and rows["MS-1"]["callouts"] == ["MAKER B TSB-100"],
      f"MS-1 schedule-only: {rows.get('MS-1')}")
    A(any("needs a 0-49 category" in f for f in rows["MS-1"]["flags"]),
      "schedule-only row surfaces its missing category")
    # schedule callouts pre-fill WC-1 / L-1 too
    A(rows["WC-1"]["callouts"] == ["MAKER A 3351.101"], "WC-1 callout")
    # the decoy never fabricates a fixture ("2/P-1" is not word-exact)
    A("P-1" not in rows, f"sheet-ref decoy rejected: {sorted(rows)}")
    # untagged symbol surfaced, never given an invented tag
    A(scan["untagged"].get("sink_d") == 1, f"untagged: {scan['untagged']}")
    # the legend page has no verified scale -> symbol lane skipped LOUDLY
    A(any("symbol lane skipped" in s for s in scan["skipped"]),
      f"unverified sheet skips honestly: {scan['skipped']}")
    A(scan["schedule_pages"] == [1], "schedule found on page 1")


def test_sync_scan_reconcile():
    proj = Project(os.path.join(TMP, "p.ploom.json"))
    scan = cutticket.scan_set(os.path.join(TMP, "set.pdf"))
    res = cutticket.sync_scan(proj, scan, "set.pdf")
    A(res["added"] == 4 and res["changed"], f"first sync adds: {res}")
    by = {it.tag: it for it in proj.pull_list}
    A(by["MS-1"].origin == "set-scan" and by["MS-1"].callouts
      == ["MAKER B TSB-100"], "callouts pre-filled on CREATE")
    A(by["WC-1"].count == 2
      and by["WC-1"].sources == {"set-scan:set.pdf": 2}, "per-source count")
    # human edits callouts + notes; a re-scan must not touch them
    by["MS-1"].callouts = ["MAKER B TSB-100", "stainless rim guard"]
    by["MS-1"].notes = "verified against spec 22 42 00"
    res2 = cutticket.sync_scan(proj, scan, "set.pdf")
    A(not res2["changed"], f"no-op re-scan writes nothing: {res2}")
    A(by["MS-1"].callouts[1] == "stainless rim guard"
      and by["MS-1"].notes.startswith("verified"), "human fields survive")
    # the model lane merges under its own source key
    m = draft.DraftModel()
    m.add("fixture", [(5, 5)], stencil="wc", tag="WC-1")
    cutticket.sync_project(proj, m, "loft.json")
    A(by["WC-1"].count == 3 and by["WC-1"].sources
      == {"set-scan:set.pdf": 2, "loft.json": 1},
      f"lanes merge per-source: {by['WC-1'].sources}")
    # a tag that leaves the scan tombstones (never deleted)
    scan2 = {"rows": [r for r in scan["rows"] if r["tag"] != "MS-1"],
             "untagged": scan["untagged"], "skipped": []}
    cutticket.sync_scan(proj, scan2, "set.pdf")
    A(by["MS-1"].missing_from_model and by["MS-1"] in proj.pull_list,
      "orphan tombstoned, not deleted")
    # to_packets: symbol-corroborated rows build; schedule-only surfaces
    packets, needs = cutticket.to_packets(proj.pull_list)
    names = {p["tag"] for p in packets}
    A("WC-1" in names and "L-1" in names, f"packets: {names}")
    A(any(t == "MS-1" for t, _ in needs), f"MS-1 needs attention: {needs}")


# --------------------------------------------------------------------------- #

def main():
    tests = [
        (test_schedule_table, "fixture-schedule table parses (wrapped rows, "
                              "word-exact tags, determinism)"),
        (test_scan_lanes_and_rejects, "symbol + schedule lanes; decoys and "
                                      "unverified sheets rejected loudly"),
        (test_sync_scan_reconcile, "proposals reconcile: human fields "
                                   "survive, lanes merge, tombstones"),
    ]
    for fn, label in tests:
        fn()
        print(f"PASS {label}")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"SET-SCAN TEST PASSED  ({_N[0]} checks)  — the Cut Ticket "
          "set-scan")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("SET-SCAN TEST FAILED:", e)
        sys.exit(1)
