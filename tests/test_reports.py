"""Self-contained tests for rfi_stamper.reports.

Run: python3.12 tests/test_reports.py
Prints "REPORTS TESTS PASSED" and exits 0 on success, nonzero on failure.
"""
from __future__ import annotations

import os
import sys
import tempfile

import fitz

# make the package importable when run directly from the repo root or tests/
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from rfi_stamper import reports  # noqa: E402
from rfi_stamper.reports import (  # noqa: E402
    BUILTIN_TEMPLATES,
    FormField,
    FormTemplate,
)


def _quiet(*_a, **_k):
    pass


def _page_text(path: str, page_index: int) -> str:
    doc = fitz.open(path)
    try:
        return doc[page_index].get_text()
    finally:
        doc.close()


def _by_name(name: str) -> FormTemplate:
    for t in BUILTIN_TEMPLATES:
        if t.name == name:
            return t
    raise AssertionError(f"builtin template {name!r} not found")


# --------------------------------------------------------------- test 1 ---

def test_builtin_templates():
    names = {t.name for t in BUILTIN_TEMPLATES}
    for expected in ("Daily Field Report", "Safety Inspection",
                     "QC Punch Walk", "RFI Follow-Up"):
        assert expected in names, f"missing builtin template {expected!r}"

    for t in BUILTIN_TEMPLATES:
        assert t.id, f"template {t.name!r} has no id"
        assert t.fields, f"template {t.name!r} has no fields"
        for f in t.fields:
            assert isinstance(f, FormField)
            assert f.key and f.label, (t.name, f)
            assert f.kind in ("text", "multiline", "check", "choice"), f
            if f.kind == "choice":
                assert len(f.choices) >= 2, f
    # id uniqueness matters for lookups
    ids = [t.id for t in BUILTIN_TEMPLATES]
    assert len(ids) == len(set(ids)), f"duplicate template ids: {ids}"

    daily = _by_name("Daily Field Report")
    kinds = {f.kind for f in daily.fields}
    assert {"text", "multiline", "check", "choice"} <= kinds, kinds
    assert any(f.kind == "choice" and f.key == "weather"
               for f in daily.fields), "daily report needs a weather choice"

    safety = _by_name("Safety Inspection")
    n_checks = sum(1 for f in safety.fields if f.kind == "check")
    assert n_checks >= 8, f"safety inspection has {n_checks} checks, want >= 8"
    assert any(f.kind == "multiline" and "corrective" in f.label.lower()
               for f in safety.fields), "corrective actions block missing"

    punch = _by_name("QC Punch Walk")
    assert any(f.kind == "check" for f in punch.fields)
    assert any(f.kind == "multiline" for f in punch.fields)

    followup = _by_name("RFI Follow-Up")
    status = [f for f in followup.fields if f.kind == "choice"]
    assert status, "RFI Follow-Up needs a resolution status choice"
    lowered = {str(c).lower() for c in status[0].choices}
    for want in ("open", "answered", "in work", "fixed", "verified"):
        assert want in lowered, (want, lowered)

    print("  [1] builtin templates: 4 named forms, sane fields, ok")


# --------------------------------------------------------------- test 2 ---

def test_template_roundtrip():
    original = _by_name("Daily Field Report")
    data = original.to_dict()
    assert isinstance(data, dict) and isinstance(data["fields"], list)
    assert all(isinstance(f, dict) for f in data["fields"])
    rebuilt = FormTemplate.from_dict(data)
    assert rebuilt == original, "to_dict/from_dict round trip changed template"

    custom = FormTemplate.new("Site Visit Notes", [
        FormField("date", "Date"),
        FormField("notes", "Notes", "multiline"),
        FormField("followup", "Follow-Up Needed", "check"),
    ])
    assert custom.id and custom.id == custom.id.lower()
    assert " " not in custom.id, custom.id
    assert FormTemplate.from_dict(custom.to_dict()) == custom

    print("  [2] template to/from dict round-trips, new() slugs the id, ok")


# --------------------------------------------------------------- test 3 ---

def test_render_blank_form():
    template = _by_name("Daily Field Report")
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "blank.pdf")
        result = reports.render_blank_form(template, out, log=_quiet)
        assert os.path.exists(out), "blank form PDF was not written"
        assert result["out_path"] == out
        assert result["fields"] == len(template.fields), result
        assert result["pages"] >= 1

        doc = fitz.open(out)
        try:
            assert doc.page_count == result["pages"], (
                doc.page_count, result["pages"])
            text = doc[0].get_text()
            assert template.name.upper() in text, "title missing from page 1"
            assert "Work Performed" in text, "field label missing"
            assert "Weather" in text and "Rain" in text, "choice labels missing"
            assert "Page 1 of" in text, "page footer missing"

            drawings = doc[0].get_drawings()
            assert drawings, "no vector drawings on the blank form"
            rects = sum(1 for path in drawings
                        for item in path["items"] if item[0] == "re")
            # weather has 6 option squares + the safety incidents checkbox
            assert rects >= 7, f"expected checkbox squares, got {rects} rects"
            lines = sum(1 for path in drawings
                        for item in path["items"] if item[0] == "l")
            assert lines >= 8, f"expected ruled lines, got {lines}"
        finally:
            doc.close()

        # a long checklist must paginate
        long_t = FormTemplate.new("Long Walk Checklist", [
            FormField(f"item_{i}", f"Checklist item number {i}", "check")
            for i in range(1, 81)
        ])
        out2 = os.path.join(d, "long.pdf")
        result2 = reports.render_blank_form(long_t, out2, log=_quiet)
        assert result2["pages"] > 1, result2
        doc = fitz.open(out2)
        try:
            assert doc.page_count == result2["pages"]
            last = doc[doc.page_count - 1].get_text()
            assert "(CONTINUED)" in last, "continuation header missing"
            assert f"Page {doc.page_count} of {doc.page_count}" in last
        finally:
            doc.close()

    print("  [3] render_blank_form: labels, squares, rules, paginates, ok")


# --------------------------------------------------------------- test 4 ---

def test_render_filled_form():
    template = _by_name("Daily Field Report")
    values = {
        "date": "2026-07-07",
        "weather": "Rain",
        "temperature": "68F",
        "crew_count": "12",
        "work_performed": ("Set hangers and ran waste line at level 2.\n"
                           "Completed branch rough-in at the east riser."),
        "delays": "",
        "safety_incidents": "yes",
        "safety_note": "",
        "visitors": "Building inspector, morning walk",
    }
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "filled.pdf")
        result = reports.render_filled_form(template, values, out, log=_quiet)
        assert os.path.exists(out)
        assert result["fields"] == len(template.fields)

        text = _page_text(out, 0)
        assert "2026-07-07" in text, "supplied text value missing"
        assert "waste line at level 2" in text, "multiline value missing"
        assert "12" in text, "crew count value missing"
        # the checked box carries the X mark glyph
        assert "X" in text, "checked glyph missing"
        # blank rendering of the same template must NOT carry the mark
        blank = os.path.join(d, "blank_again.pdf")
        reports.render_blank_form(template, blank, log=_quiet)
        blank_text = _page_text(blank, 0)
        assert "X" not in blank_text.replace(template.name.upper(), ""), \
            "blank form should not show a checked mark"
        # the picked choice label is still present (highlighted in render)
        assert "Rain" in text

        # filled squares: the picked choice + the checked box are filled rects
        doc = fitz.open(out)
        try:
            filled_rects = 0
            for path in doc[0].get_drawings():
                if path["fill"] and path["type"] in ("f", "fs"):
                    for item in path["items"]:
                        if item[0] == "re":
                            filled_rects += 1
            assert filled_rects >= 2, (
                f"expected filled squares for check+choice, got {filled_rects}")
        finally:
            doc.close()

    print("  [4] render_filled_form: values printed, mark shown, ok")


# --------------------------------------------------------------- test 5 ---

class _FakeProject:
    """Small duck-typed stand-in mirroring the project object surface."""

    def __init__(self):
        self.tasks = [
            {"title": "Pour slab section 2", "status": "open",
             "due": "2000-01-02"},                       # always overdue
            {"title": "Set anchor bolts", "status": "open",
             "due": "2099-01-01"},                       # never overdue
            {"title": "Rough-in level 1", "status": "done", "due": ""},
        ]
        self.punch = [
            {"title": "Patch wall at corridor", "area": "Level 2",
             "status": "open"},
            {"title": "Adjust door closer", "area": "Level 1",
             "status": "closed"},
        ]
        self.change_orders = [
            {"number": "CO-01", "title": "Added floor drains",
             "status": "pending", "amount": 4200},
            {"number": "CO-02", "title": "Reroute storm line",
             "status": "approved", "amount": 12500},
        ]
        self.budget = [
            {"name": "Underground", "budget": 50000, "spent": 42000},
            {"name": "Above grade", "budget": 80000, "spent": 15000},
        ]
        self.inspections = [
            {"date": "2026-06-01", "kind": "Rough-in", "result": "passed"},
        ]

    def summary(self):
        return {"Tasks": len(self.tasks), "Punch items": len(self.punch),
                "Change orders": len(self.change_orders)}


def test_project_snapshot_pdf():
    project = _FakeProject()
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "snapshot.pdf")
        result = reports.project_snapshot_pdf(project, out, log=_quiet)
        assert os.path.exists(out)
        assert result["out_path"] == out

        doc = fitz.open(out)
        try:
            assert doc.page_count == result["pages"], (
                doc.page_count, result["pages"])
            assert doc.page_count >= 2, "snapshot must include table sections"

            page1 = doc[0].get_text()
            assert "PROJECT SNAPSHOT" in page1, "title missing"
            assert "OPEN TASKS" in page1, "KPI caption missing"
            assert "2" in page1, "KPI number missing"          # 2 open tasks
            assert "1 overdue" in page1, "overdue KPI missing"
            assert "$12,500" in page1, "approved CO dollars missing"
            assert "44%" in page1, "budget percent missing"    # 57k of 130k
            assert "$57,000" in page1 and "$130,000" in page1, \
                "budget spent/total missing"
            # the budget bar is a drawn, filled rectangle
            filled_rects = sum(
                1 for path in doc[0].get_drawings() if path["fill"]
                for item in path["items"] if item[0] == "re")
            assert filled_rects >= 2, "budget bar rectangles missing"

            rest = "".join(doc[i].get_text()
                           for i in range(1, doc.page_count))
            assert "TASKS" in rest, "tasks table section missing"
            assert "Pour slab section 2" in rest, "task row missing"
            assert "PUNCH LIST" in rest and "Patch wall at corridor" in rest
            assert "CHANGE ORDERS" in rest and "CO-02" in rest
            assert "BUDGET" in rest and "Underground" in rest
            assert "INSPECTIONS" in rest and "Rough-in" in rest
        finally:
            doc.close()

    print("  [5] project_snapshot_pdf: KPIs, bar, table sections merged, ok")


# --------------------------------------------------------------- test 6 ---

def test_daily_report_pdf():
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "daily.pdf")
        result = reports.daily_report_pdf(
            {"date": "2026-07-07", "weather": "Clear", "crew_count": "8",
             "work_performed": "Hung and strapped horizontal runs."},
            out, log=_quiet)
        assert os.path.exists(out)
        assert result["pages"] >= 1
        text = _page_text(out, 0)
        assert "DAILY FIELD REPORT" in text
        assert "2026-07-07" in text
        assert "Hung and strapped horizontal runs." in text

    print("  [6] daily_report_pdf: convenience wrapper, ok")


def main() -> int:
    try:
        test_builtin_templates()
        test_template_roundtrip()
        test_render_blank_form()
        test_render_filled_form()
        test_project_snapshot_pdf()
        test_daily_report_pdf()
    except AssertionError as e:
        print("REPORTS TESTS FAILED:", e)
        return 1
    except Exception as e:                              # pragma: no cover
        import traceback
        traceback.print_exc()
        print("REPORTS TESTS FAILED (error):", e)
        return 1
    print("REPORTS TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
