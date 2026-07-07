"""Tests for rfi_stamper.project (shared local project store). Plain python,
no pytest: assertions raise, so any failure exits nonzero."""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from rfi_stamper import project as project_mod
from rfi_stamper.project import (KINDS, BudgetLine, ChangeOrder, DocEntry,
                                 Inspection, Project, PunchItem, ScheduleItem,
                                 SpecSection, Task, parse_spec)

quiet = lambda *a, **k: None  # noqa: E731


def expect(exc, fn, *args):
    try:
        fn(*args)
    except exc:
        return
    raise AssertionError(f"expected {exc.__name__} from {fn!r}{args!r}")


# ------------------------------------------------------------ dataclasses ---

SAMPLES = [
    (Task, dict(title="Vérifier l'étanchéité — niveau 2", desc="配管の確認",
                assignee="crew-α", status="doing", due="2026-08-01",
                priority="high", linked_sheet="P-101")),
    (ScheduleItem, dict(title="Rough-in ünderground", start="2026-07-01",
                        end="2026-07-15", crew="β", pct=42.5, color="#ff8800",
                        depends=["a1", "b2"])),
    (PunchItem, dict(title="Türdichtung fehlt", location="Ñivel 3",
                     sheet="A-501", status="ready", assignee="crew",
                     photo_path="/photos/ω.jpg")),
    (Inspection, dict(title="Rough-in – plomberie", date="2026-07-20",
                      inspector="AHJ", status="passed",
                      checklist=[{"item": "pressure test ✓", "ok": True,
                                  "note": "80 psi / 15 min"},
                                 {"item": "hangers", "ok": None, "note": ""}],
                      notes="naïve résumé")),
    (ChangeOrder, dict(number="CO-007", title="Añadir válvulas",
                       amount=12345.67, status="submitted", days_impact=3)),
    (BudgetLine, dict(code="22-1000", desc="Tuberías y accesorios",
                      budget=50000.0, committed=42000.0, spent=17500.5)),
    (DocEntry, dict(path="/docs/plan—set.pdf", title="Jeu de plans Δ2",
                    category="drawings", rev="Δ2")),
    (SpecSection, dict(section="22 11 16", title="DOMESTIC WATER PIPING",
                       division="22", source="/specs/vol-1.pdf",
                       text="PART 1 — GÉNÉRAL …")),
]

_STAMPED = {"created", "updated", "added"}


def test_dataclasses():
    for cls, kw in SAMPLES:
        obj = cls.new(**kw)
        assert len(obj.id) == 32 and int(obj.id, 16) >= 0, obj.id
        names = {f.name for f in type(obj).__dataclass_fields__.values()}
        for auto in _STAMPED & names:
            assert getattr(obj, auto), f"{cls.__name__}.{auto} not stamped"
        # to_dict -> real JSON -> from_dict round-trips exactly (incl. unicode)
        wire = json.dumps(obj.to_dict(), ensure_ascii=False)
        back = cls.from_dict(json.loads(wire))
        assert back == obj, f"{cls.__name__} round-trip mismatch"
        # two new() calls never share an id
        assert cls.new(**kw).id != obj.id
    # explicit id / timestamp are honored, not overwritten
    t = Task.new(id="fixed", title="x", created="2020-01-01T00:00:00")
    assert t.id == "fixed" and t.created == "2020-01-01T00:00:00" and t.updated
    # from_dict ignores unknown keys (forward compatibility)
    t2 = Task.from_dict({"id": "a", "title": "b", "bogus_future_field": 1})
    assert t2.id == "a" and t2.title == "b"
    # from_dict deep-copies: editing the result never mutates the source dict
    src = {"id": "s1", "title": "t", "depends": ["a"]}
    s = ScheduleItem.from_dict(src)
    s.depends.append("b")
    assert src["depends"] == ["a"], "from_dict shared a mutable value"
    # to_dict deep-copies: editing the dict never mutates the object
    insp = Inspection.new(title="i", checklist=[{"item": "x", "ok": None,
                                                 "note": ""}])
    d = insp.to_dict()
    d["checklist"].append({"item": "y", "ok": True, "note": ""})
    assert len(insp.checklist) == 1, "to_dict shared a mutable value"
    print("  dataclasses OK")


# ------------------------------------------------------------------ CRUD ---

def test_project_crud():
    p = Project()                          # in-memory
    assert p.path is None and p.name == ""
    for kind in KINDS:
        assert getattr(p, kind) == [] and p.items(kind) == []
    for bad in ("task", "rfi", "", "TASKS"):
        expect(ValueError, p.add, bad, Task.new(title="x"))
        expect(ValueError, p.remove, bad, "id")
        expect(ValueError, p.get, bad, "id")
        expect(ValueError, p.items, bad)
    expect(TypeError, p.add, "tasks", PunchItem.new(title="wrong shelf"))
    expect(ValueError, p.save)             # in-memory, no path
    old_now = project_mod._now
    project_mod._now = lambda: "2026-02-02T02:02:02"
    try:
        t = p.add("tasks", Task.new(title="hang pipe"))
        assert t.updated == "2026-02-02T02:02:02", "add() must touch updated"
    finally:
        project_mod._now = old_now
    assert p.get("tasks", t.id) is t
    assert p.get("tasks", "nope") is None
    assert p.items("tasks") == [t]
    p.items("tasks").clear()               # items() is a copy...
    assert p.tasks == [t]                  # ...store unaffected
    assert p.remove("tasks", t.id) is True
    assert p.remove("tasks", t.id) is False
    assert p.tasks == []
    print("  crud + validation OK")


def test_autosave_roundtrip(tmp):
    path = os.path.join(tmp, "jobsite" + Project.SUFFIX)
    p = Project(path)
    assert p.name == "jobsite" and not os.path.exists(path)
    t = p.add("tasks", Task.new(title="pressure test — zone β", due="2026-08-01"))
    s = p.add("schedule", ScheduleItem.new(title="underground", pct=10.0,
                                           depends=[t.id]))
    co = p.add("change_orders", ChangeOrder.new(number="CO-001", amount=980.5))
    assert os.path.exists(path), "add() must autosave when a path is set"

    p2 = Project(path)                     # reopen: full dataclass equality
    assert p2.name == "jobsite"
    assert p2.tasks == [t] and p2.schedule == [s] and p2.change_orders == [co]
    assert p2.schedule[0].depends == [t.id]

    assert p2.remove("change_orders", co.id) is True    # remove autosaves too
    assert Project(path).change_orders == []
    # save-as keeps content and re-targets the store
    other = os.path.join(tmp, "copy" + Project.SUFFIX)
    p2.save(other)
    assert p2.path == other and Project(other).tasks == [t]
    print("  autosave + reopen round-trip OK")


def test_atomic(tmp):
    path = os.path.join(tmp, "atomic" + Project.SUFFIX)
    p = Project(path)
    for i in range(3):                     # repeated overwrites stay clean
        p.add("punch", PunchItem.new(title=f"item {i}"))
    assert not os.path.exists(path + ".part"), "temp file left behind"
    assert not [f for f in os.listdir(tmp) if f.endswith(".part")]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)                # file at final path is valid JSON
    assert data["version"] == 1 and data["name"] == "atomic"
    assert len(data["punch"]) == 3
    assert all(data[k] == [] for k in KINDS if k != "punch")
    print("  atomic save OK")


# --------------------------------------------------------------- summary ---

def test_summary():
    p = Project()
    p.add("tasks", Task.new(title="late", due="2026-06-14", status="todo"))
    p.add("tasks", Task.new(title="late but done", due="2026-06-14",
                            status="done"))
    p.add("tasks", Task.new(title="future", due="2026-06-16", status="doing"))
    p.add("tasks", Task.new(title="no due", status="blocked"))
    p.add("schedule", ScheduleItem.new(title="behind", end="2026-06-10",
                                       pct=50.0))
    p.add("schedule", ScheduleItem.new(title="finished", end="2026-06-10",
                                       pct=100.0))
    p.add("schedule", ScheduleItem.new(title="future", end="2026-07-01",
                                       pct=0.0))
    p.add("schedule", ScheduleItem.new(title="undated", pct=0.0))
    p.add("punch", PunchItem.new(title="a", status="open"))
    p.add("punch", PunchItem.new(title="b", status="ready"))
    p.add("punch", PunchItem.new(title="c", status="closed"))
    p.add("inspections", Inspection.new(title="i1", status="scheduled"))
    p.add("inspections", Inspection.new(title="i2", status="failed"))
    p.add("inspections", Inspection.new(title="i3", status="failed"))
    p.add("change_orders", ChangeOrder.new(title="d", status="draft",
                                           amount=1000.0))
    p.add("change_orders", ChangeOrder.new(title="s", status="submitted",
                                           amount=2000.0))
    p.add("change_orders", ChangeOrder.new(title="a1", status="approved",
                                           amount=3000.0))
    p.add("change_orders", ChangeOrder.new(title="a2", status="approved",
                                           amount=500.0))
    p.add("change_orders", ChangeOrder.new(title="r", status="rejected",
                                           amount=999.0))
    p.add("budget", BudgetLine.new(code="01", budget=100.0, spent=40.0))
    p.add("budget", BudgetLine.new(code="02", budget=50.5, committed=20.0,
                                   spent=10.25))
    p.add("documents", DocEntry.new(title="doc a"))
    p.add("documents", DocEntry.new(title="doc b"))
    p.add("specs", SpecSection.new(section="22 11 16", title="PIPING"))

    old_today = project_mod._today
    project_mod._today = lambda: "2026-06-15"      # freeze the clock
    try:
        got = p.summary()
    finally:
        project_mod._today = old_today
    want = {
        "tasks_open": 3,            # everything not done
        "tasks_overdue": 1,         # due < today and not done
        "punch_open": 2,            # open + ready (everything not closed)
        "inspections_failed": 2,
        "co_pending": 2,            # draft + submitted
        "co_approved_amount": 3500.0,
        "budget_total": 150.5,
        "budget_spent": 50.25,
        "docs": 2,
        "specs": 1,
        "schedule_behind": 1,       # end < today and pct < 100
    }
    assert got == want, f"\n  got  {got}\n  want {want}"
    print("  summary math OK")


# -------------------------------------------------------------- parse_spec ---

def make_spec_pdf(path):
    """Two CSI sections; the first spans a page break with number-only
    header/footer lines that must merge, not split."""
    pages = [
        ["SECTION 09 91 23 - INTERIOR PAINTING",
         "PART 1 - GENERAL",
         "1.1 SUMMARY",
         "A. Surface preparation and field application of paints.",
         "09 91 23 - 1"],                          # page footer
        ["09 91 23 - 2",                           # page header
         "PART 2 - PRODUCTS",
         "2.1 Water-based acrylic finishes only."],
        ["SECTION 22 11 16",                       # title on the next line
         "DOMESTIC WATER PIPING",
         "PART 1 - GENERAL",
         "A. Pipe, fittings, and joining for domestic water systems."],
    ]
    c = canvas.Canvas(path, pagesize=letter)
    for lines in pages:
        y = 720
        for ln in lines:
            c.drawString(72, y, ln)
            y -= 18
        c.showPage()
    c.save()
    return path


def test_parse_spec(tmp):
    pdf = make_spec_pdf(os.path.join(tmp, "specbook.pdf"))
    secs = parse_spec([pdf], log=quiet)
    assert len(secs) == 2, [s.section for s in secs]
    a, b = secs
    assert a.section == "09 91 23" and a.division == "09"
    assert a.title == "INTERIOR PAINTING"
    assert a.source == pdf and a.id and len(a.id) == 32
    assert "field application of paints" in a.text
    assert "Water-based acrylic" in a.text, "page-break continuation lost"
    assert b.section == "22 11 16" and b.division == "22"
    assert b.title == "DOMESTIC WATER PIPING", b.title
    assert "domestic water systems" in b.text
    assert not b.text.startswith("DOMESTIC"), "title line leaked into body"
    # a single path (not a list) is accepted too
    assert len(parse_spec(pdf, log=quiet)) == 2
    # bad files never raise: logged and skipped
    missing = os.path.join(tmp, "missing.pdf")
    logged = []
    assert parse_spec([missing], log=logged.append) == []
    assert logged and "missing.pdf" in logged[0]
    # a mixed batch still yields the good file's sections
    assert len(parse_spec([missing, pdf], log=quiet)) == 2
    print("  parse_spec OK")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        test_dataclasses()
        test_project_crud()
        test_autosave_roundtrip(tmp)
        test_atomic(tmp)
        test_summary()
        test_parse_spec(tmp)
    print("PROJECT TESTS PASSED")


if __name__ == "__main__":
    main()
