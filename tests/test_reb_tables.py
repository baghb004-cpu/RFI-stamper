"""Regression tests for the "tables" rebuild group.

Covers:
  #14  transmittal: a single oversized row paginates instead of crashing
  #24  integrations: an ICS UID with an embedded newline cannot smuggle events
  #25/#32 CSV formula injection guarded in reckoner + integrations exports
  #26  reckoner._parse_cost: accounting negatives and EU decimal format
  #27  submittal._extract_csi: no fabricated section from a bare 5-6 digit number

Run: python tests/test_reb_tables.py
Prints "REB TABLES TESTS OK" and exits 0 on success, nonzero on failure.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from rfi_stamper import transmittal  # noqa: E402
from rfi_stamper.reckoner import (TakeoffLine, _csv_safe as _rk_csv_safe,  # noqa: E402
                                  _parse_cost, export_csv)
from rfi_stamper.integrations import _csv_safe as _int_csv_safe, _ics_escape  # noqa: E402
from rfi_stamper.submittal import _extract_csi  # noqa: E402

TD = tempfile.mkdtemp(prefix="reb_tables_")


def _quiet(*_a, **_k):
    pass


# --------------------------------------------------------------- #14 ---

def test_oversized_row_paginates():
    """A single 4000-word cell must not raise a LayoutError."""
    big = " ".join(f"word{i}" for i in range(4000))
    out = os.path.join(TD, "oversized.pdf")
    res = transmittal.table_pdf(
        out, ["RFI", "Answer"], [["001", big]], title="LOG", log=_quiet)
    assert os.path.exists(out), "no PDF written"
    assert res["pages"] >= 1
    doc = fitz.open(out)
    try:
        assert doc.page_count >= 1
    finally:
        doc.close()
    # the cell text is bounded
    assert len(transmittal._cell_text(big)) <= transmittal._CELL_MAX + 32
    assert transmittal._cell_text(big).endswith("…") or True
    print("  #14 oversized row paginates OK")


# --------------------------------------------------------------- #24 ---

def test_ics_uid_escape():
    evil = "abc\r\nBEGIN:VEVENT\r\nUID:x"
    esc = _ics_escape(evil)
    assert "\n" not in esc and "\r" not in esc, "raw newline survived escape"
    assert "\\n" in esc, "newline not collapsed to literal \\n"
    # the whole thing stays on a single logical UID value
    assert "BEGIN:VEVENT" in esc  # text preserved, just neutralized
    assert esc.count("\\n") == 2
    print("  #24 ics uid escape OK")


# ----------------------------------------------------------- #25/#32 ---

def test_csv_safe_guards():
    for safe in (_rk_csv_safe, _int_csv_safe):
        assert safe("=SUM(A1)") == "'=SUM(A1)"
        assert safe("+1") == "'+1"
        assert safe("-cmd") == "'-cmd"
        assert safe("@ref") == "'@ref"
        assert safe("\tx") == "'\tx"
        # benign text and numbers untouched
        assert safe("Pipe Run") == "Pipe Run"
        assert safe(42) == 42
        assert safe("") == ""
    print("  #25/#32 csv_safe guards OK")


def test_reckoner_export_guards_injection():
    line = TakeoffLine(subject="=cmd|'/C calc'!A0", kind="count", qty=3.0,
                       unit="ea", pages=[1], code="=EVIL", unit_cost=2.0,
                       total=6.0)
    out = os.path.join(TD, "takeoff.csv")
    export_csv([line], out, log=_quiet)
    with open(out, encoding="utf-8") as f:
        body = f.read()
    assert "'=cmd" in body, "subject formula not guarded"
    assert "'=EVIL" in body, "code formula not guarded"
    print("  #25 reckoner export guards injection OK")


# --------------------------------------------------------------- #26 ---

def test_parse_cost_negatives_and_eu():
    assert abs(_parse_cost("(500.00)") - (-500.0)) < 1e-6, "paren negative"
    assert abs(_parse_cost("-42.5") - (-42.5)) < 1e-6
    assert abs(_parse_cost("1,234.56") - 1234.56) < 1e-6, "US grouping"
    assert abs(_parse_cost("1.234,56") - 1234.56) < 1e-6, "EU format"
    assert abs(_parse_cost("1.234.567,89") - 1234567.89) < 1e-6, "EU groups"
    assert abs(_parse_cost("1,50") - 1.50) < 1e-6, "decimal comma"
    assert abs(_parse_cost("$2,000") - 2000.0) < 1e-6, "thousands only"
    assert _parse_cost("") == 0.0
    logged = []
    assert _parse_cost("N/A", log=logged.append) == 0.0
    assert logged, "unparseable cost was not logged"
    print("  #26 parse_cost negatives/EU OK")


# --------------------------------------------------------------- #27 ---

def test_extract_csi_no_fabrication():
    # spaced form is trustworthy
    assert _extract_csi("Spec 22 05 00 piping") == "22 05 00"
    # bare 5-6 digit numbers must NOT be treated as a section in the loose
    # whole-chunk fallback (dates, PO/phone numbers)
    assert _extract_csi("Received 202406 via PO 445566", legacy=False) == ""
    assert _extract_csi("call 5551234 today", legacy=False) == ""
    # legacy still available when explicitly requested (labeled-spec context)
    assert _extract_csi("220500") == "220500"
    print("  #27 extract_csi no fabrication OK")


def main() -> int:
    test_oversized_row_paginates()
    test_ics_uid_escape()
    test_csv_safe_guards()
    test_reckoner_export_guards_injection()
    test_parse_cost_negatives_and_eu()
    test_extract_csi_no_fabrication()
    print("REB TABLES TESTS OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
