"""Tests for rfi_stamper.submittal (register parse + log PDF). Plain python.

Builds a real submittal register as a PDF (so read_document's PDF path is
exercised), parses it, and checks number / spec-section / normalized status.
The log-PDF assertion is skipped ONLY if transmittal.py is not importable;
the parse and normalize assertions always run.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz
from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfgen import canvas

from rfi_stamper.submittal import (CANONICAL_STATUSES, SubmittalRecord,
                                   normalize_status, parse_submittals,
                                   submittal_log_pdf)

quiet = lambda *a, **k: None  # noqa: E731

# lines a document-controls export might produce; different label spellings
_REGISTER_LINES = [
    "SUBMITTAL REGISTER",
    "Submittal No.: 22 11 16-001   Title: Domestic Water Piping   "
    "Spec Section: 22 11 16   Status: Approved as Noted",
    "Sub #: 001   Description: Concrete Mix Design   Section: 03 30 00   "
    "Status: Revise and Resubmit",
    "Submittal No.: 23 05 00-002   Title: HVAC Ductwork Shop Drawings   "
    "Spec Section: 23 05 00   Status: Rejected",
]


def make_register_pdf(path: str) -> str:
    c = canvas.Canvas(path, pagesize=landscape(letter))
    c.setFont("Helvetica", 8)
    y = landscape(letter)[1] - 60
    for line in _REGISTER_LINES:
        c.drawString(36, y, line)
        y -= 22
    c.showPage()
    c.save()
    return path


def _by_number(records, number):
    for r in records:
        if r.number == number:
            return r
    raise AssertionError(f"no record with number {number!r}; "
                         f"got {[r.number for r in records]}")


def test_parse(tmp):
    pdf = make_register_pdf(os.path.join(tmp, "register.pdf"))
    records = parse_submittals([pdf], log=quiet)
    assert len(records) == 3, [r.number for r in records]

    r1 = _by_number(records, "22 11 16-001")
    assert r1.spec_section == "22 11 16", r1.spec_section
    assert r1.status == "Approved as Noted", r1.status
    assert r1.title == "Domestic Water Piping", r1.title

    r2 = _by_number(records, "001")
    assert r2.spec_section == "03 30 00", r2.spec_section
    assert r2.status == "Revise & Resubmit", r2.status
    assert r2.title == "Concrete Mix Design", r2.title

    r3 = _by_number(records, "23 05 00-002")
    assert r3.spec_section == "23 05 00", r3.spec_section
    assert r3.status == "Rejected", r3.status

    # sorted by spec section then number: 03 30 00 first
    assert records[0].spec_section == "03 30 00", [r.spec_section for r in records]

    # bad path is skipped, not raised
    assert parse_submittals([os.path.join(tmp, "nope.pdf")], log=quiet) == []

    # de-dup + backfill: a later answered copy fills the blank status
    dup = os.path.join(tmp, "dup.pdf")
    cc = canvas.Canvas(dup, pagesize=landscape(letter))
    cc.setFont("Helvetica", 8)
    cc.drawString(36, 500, "Submittal No.: 26 05 19-003   "
                           "Title: Building Wire   Spec Section: 26 05 19")
    cc.drawString(36, 470, "Submittal No.: 26 05 19-003   "
                           "Title: Building Wire   Status: For Record")
    cc.showPage()
    cc.save()
    merged = parse_submittals([dup], log=quiet)
    m = _by_number(merged, "26 05 19-003")
    assert m.spec_section == "26 05 19", m.spec_section
    assert m.status == "For Record", m.status
    print("  parse_submittals OK")


def test_normalize():
    cases = {
        "Approved as Noted": "Approved as Noted",
        "APPROVED AS NOTED": "Approved as Noted",
        "Make Corrections Noted": "Approved as Noted",
        "Approved with Comments": "Approved as Noted",
        "Revise and Resubmit": "Revise & Resubmit",
        "revise/resubmit": "Revise & Resubmit",
        "Resubmit": "Revise & Resubmit",
        "No Exceptions Taken": "Approved",
        "Approved": "Approved",
        "Reviewed": "Approved",
        "Rejected": "Rejected",
        "Not Approved": "Rejected",
        "For Record Only": "For Record",
        "No Action Required": "For Record",
        "Under Review": "Pending",
        "Pending": "Pending",
        "Outstanding": "Pending",
        "": "",
        "zzq total nonsense": "",
    }
    for text, want in cases.items():
        got = normalize_status(text)
        assert got == want, f"normalize_status({text!r}) -> {got!r}, want {want!r}"

    # every canonical status maps to itself (idempotent)
    for s in CANONICAL_STATUSES:
        assert normalize_status(s) == s, s
    print("  normalize_status OK")


def test_log_pdf(tmp):
    records = [
        SubmittalRecord(number="22 11 16-001", title="Domestic Water Piping",
                        spec_section="22 11 16", status="Approved as Noted",
                        ball_in_court="GC"),
        SubmittalRecord(number="001", title="Concrete Mix Design",
                        spec_section="03 30 00", status="Revise & Resubmit"),
        SubmittalRecord(number="23 05 00-002", title="HVAC Ductwork",
                        spec_section="23 05 00", status="Rejected"),
    ]
    out = os.path.join(tmp, "submittal_log.pdf")
    try:
        res = submittal_log_pdf(records, out, log=quiet)
    except RuntimeError as e:
        print(f"  submittal_log_pdf SKIPPED (transmittal not importable): {e}")
        return

    assert isinstance(res, dict), res
    assert os.path.exists(out), out
    doc = fitz.open(out)
    text = doc[0].get_text()
    doc.close()
    assert "SUBMITTAL LOG" in text, text[:400]
    assert "22 11 16" in text, "spec section missing from page-1 text"
    assert any(s in text for s in
               ("Approved", "Revise", "Rejected", "Record", "Pending")), \
        "no status word in page-1 text"
    print("  submittal_log_pdf OK")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        test_parse(tmp)
        test_normalize()
        test_log_pdf(tmp)
    print("SUBMITTAL TESTS PASSED")


if __name__ == "__main__":
    main()
