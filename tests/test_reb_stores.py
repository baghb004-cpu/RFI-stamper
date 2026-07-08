"""Regression tests for the rebuild "stores" group. Plain python, no pytest:
assertions raise, so any failure exits nonzero.

Covers:
* #10 project.save() merges a concurrent writer's records instead of clobbering
* #11 project.summary() survives a numeric field stored as a string in JSON
* #12 resolution.load() merges zfill-equivalent keys ("1" vs "001") instead of
      dropping/downgrading history
* #37 spec-import dedup keyed on (section, source) — no dupes on re-import
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper import project as project_mod
from rfi_stamper.project import (BudgetLine, ChangeOrder, Project, SpecSection,
                                 Task, _num)
from rfi_stamper.resolution import ResolutionStore


# ---------------------------------------------------- #11 numeric coercion ---

def test_summary_survives_string_numbers(tmp):
    path = os.path.join(tmp, "coerce" + Project.SUFFIX)
    p = Project(path)
    p.add("change_orders", ChangeOrder.new(number="CO-1", status="approved",
                                           amount=1000.0))
    p.add("budget", BudgetLine.new(code="01", budget=500.0, spent=100.0))

    # simulate a legacy / hand-edited file: numeric fields stored as strings,
    # including a thousands-comma and a currency symbol and pure junk.
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["change_orders"][0]["amount"] = "2,000"          # comma string
    data["budget"][0]["budget"] = "$750"                  # currency string
    data["budget"][0]["spent"] = "not a number"           # unparseable -> 0.0
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    p2 = Project(path)
    # from_dict coerced the strings to floats at load
    assert isinstance(p2.change_orders[0].amount, float)
    assert p2.change_orders[0].amount == 2000.0, p2.change_orders[0].amount
    assert p2.budget[0].budget == 750.0
    assert p2.budget[0].spent == 0.0, "junk numeric must coerce to 0.0"

    got = p2.summary()                     # must NOT raise TypeError
    assert got["co_approved_amount"] == 2000.0, got
    assert got["budget_total"] == 750.0 and got["budget_spent"] == 0.0, got

    # _num helper edge cases
    assert _num(None) == 0.0 and _num("") == 0.0 and _num("x") == 0.0
    assert _num(5) == 5.0 and _num("3.5") == 3.5 and _num("1,234.5") == 1234.5
    print("  #11 summary survives string numbers OK")


# ------------------------------------------------- #10 concurrent-writer merge ---

def test_concurrent_save_merges(tmp):
    path = os.path.join(tmp, "shared" + Project.SUFFIX)
    a = Project(path)
    ta = a.add("tasks", Task.new(title="from writer A"))

    # a second process opens the same store and adds its own record
    b = Project(path)
    tb = b.add("tasks", Task.new(title="from writer B"))

    # writer A, holding a stale view, adds another record and saves. Without
    # the merge this clobbers B's record (last writer wins). With the fix A
    # reloads B's record and keeps both.
    ta2 = a.add("tasks", Task.new(title="from writer A second"))

    final = Project(path)
    ids = {t.id for t in final.tasks}
    assert ta.id in ids, "writer A's first record lost"
    assert tb.id in ids, "#10: concurrent writer B's record was clobbered"
    assert ta2.id in ids, "writer A's own new record lost"
    assert len(final.tasks) == 3, [t.title for t in final.tasks]

    # our edits win for ids we hold: A edits its own record's title then saves
    # after B has also changed the file.
    a2 = Project(path)
    held = a2.tasks[0]
    b2 = Project(path)
    b2.add("tasks", Task.new(title="writer B again"))
    held.title = "edited by A"
    a2.save()
    reread = Project(path)
    edited = next(t for t in reread.tasks if t.id == held.id)
    assert edited.title == "edited by A", "our edit should win for our id"
    assert len(reread.tasks) == 4, "B's extra record should have merged in"
    print("  #10 concurrent save merges OK")


def test_normal_roundtrip_unaffected(tmp):
    """The merge must not disturb the ordinary single-writer path."""
    path = os.path.join(tmp, "solo" + Project.SUFFIX)
    p = Project(path)
    t = p.add("tasks", Task.new(title="solo"))
    p.add("tasks", Task.new(title="solo two"))
    p.remove("tasks", t.id)
    again = Project(path)
    assert [x.title for x in again.tasks] == ["solo two"], again.tasks
    # save-as to a new path is a plain write (no merge from an unrelated file)
    other = os.path.join(tmp, "soloB" + Project.SUFFIX)
    p.save(other)
    assert [x.title for x in Project(other).tasks] == ["solo two"]
    print("  #10 normal round-trip unaffected OK")


# --------------------------------------------- #12 zfill-key history merge ---

def test_resolution_load_merges_zfill_keys(tmp):
    path = os.path.join(tmp, "plan.pdf.rfistatus.json")
    # hand-authored sidecar with the SAME RFI under two zfill-equivalent keys.
    # "1" carries the later (fixed) step; "001" the earlier (answered) step.
    blob = {
        "version": 1,
        "rfis": {
            "001": [{"status": "answered", "ts": "2026-01-01T00:00:00+00:00",
                     "note": "answer received", "author": "pm"}],
            "1": [{"status": "in_work", "ts": "2026-02-01T00:00:00+00:00",
                   "note": "", "author": ""},
                  {"status": "fixed", "ts": "2026-03-01T00:00:00+00:00",
                   "note": "", "author": ""}],
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(blob, f)

    st = ResolutionStore()
    st.load(path)
    hist = st.history("001")
    # both histories preserved, ordered by timestamp
    assert [e["status"] for e in hist] == ["answered", "in_work", "fixed"], hist
    # current status is the latest step, not whichever key loaded last
    assert st.get("001") == "fixed", st.get("001")
    assert st.get("1") == "fixed", "zfill-equivalent lookup"
    assert list(st.statuses().keys()) == ["001"], "keys must be normalized"
    print("  #12 resolution load merges zfill keys OK")


def test_resolution_seed_never_downgrades_after_merge(tmp):
    """The merge must not break the 'seed never downgrades' promise."""
    path = os.path.join(tmp, "plan2.pdf.rfistatus.json")
    blob = {"version": 1, "rfis": {
        "1": [{"status": "fixed", "ts": "2026-03-01T00:00:00+00:00",
               "note": "", "author": ""}]}}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(blob, f)
    st = ResolutionStore()
    st.load(path)

    class _Rec:
        def __init__(self, number, has_answer):
            self.number = number
            self.has_answer = has_answer

    added = st.seed_from_records([_Rec("1", True), _Rec("2", False)])
    assert added == 1, "001 already tracked, only 002 new"
    assert st.get("001") == "fixed", "seed downgraded a hand-set status"
    assert st.get("002") == "open"
    print("  #12 seed never downgrades OK")


# -------------------------------------------------- #37 spec-import dedup ---

def test_spec_import_dedup():
    """Mirror the tab_project.import_specs dedup: (section, source) key."""
    existing = [SpecSection.new(section="09 91 23", title="PAINT",
                                source="/specs/v1.pdf")]
    reimport = [
        SpecSection.new(section="09 91 23", title="PAINT",
                        source="/specs/v1.pdf"),        # dup -> skipped
        SpecSection.new(section="22 11 16", title="WATER",
                        source="/specs/v1.pdf"),        # new section
        SpecSection.new(section="09 91 23", title="PAINT",
                        source="/specs/v2.pdf"),        # same # diff source
    ]
    have = {(s.section, s.source) for s in existing}
    fresh = [s for s in reimport if (s.section, s.source) not in have]
    assert len(fresh) == 2, [(s.section, s.source) for s in fresh]
    assert all((s.section, s.source) != ("09 91 23", "/specs/v1.pdf")
               for s in fresh)
    print("  #37 spec import dedup OK")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        test_summary_survives_string_numbers(tmp)
        test_concurrent_save_merges(tmp)
        test_normal_roundtrip_unaffected(tmp)
        test_resolution_load_merges_zfill_keys(tmp)
        test_resolution_seed_never_downgrades_after_merge(tmp)
        test_spec_import_dedup()
    print("REB STORES TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
