"""Self-contained tests for rfi_stamper.transmittal.

Run: python3.12 tests/test_transmittal.py
Prints "TRANSMITTAL TESTS PASSED" and exits 0 on success, nonzero on failure.
"""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass, field

import fitz

# make the package importable when run directly from the repo root or tests/
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from rfi_stamper import transmittal  # noqa: E402


def _page_text(path: str, page_index: int) -> str:
    doc = fitz.open(path)
    try:
        return doc[page_index].get_text()
    finally:
        doc.close()


def _quiet(*_a, **_k):
    pass


# --------------------------------------------------------------- test 1 ---

def test_table_pdf_paginates():
    headers = ["#", "Description", "Sheet", "Status"]
    rows = [[str(i + 1),
             f"Synthetic line item number {i + 1} with enough text to wrap "
             f"across the column so pagination is exercised",
             f"A-{100 + (i % 20)}",
             "Open" if i % 2 else "Closed"]
            for i in range(80)]

    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "table.pdf")
        result = transmittal.table_pdf(out, headers, rows,
                                       title="TRANSMITTAL LOG",
                                       subtitle="Synthetic 80-row register",
                                       log=_quiet)

        assert os.path.exists(out), "output PDF was not written"
        assert result["out_path"] == out
        assert result["rows"] == 80, result

        doc = fitz.open(out)
        n_pages = doc.page_count
        doc.close()
        assert n_pages > 1, f"expected multiple pages, got {n_pages}"
        assert result["pages"] == n_pages, (result["pages"], n_pages)

        t1 = _page_text(out, 0)
        assert "TRANSMITTAL LOG" in t1, "title missing from page 1"
        assert "Description" in t1, "header cell missing from page 1"
        # header must repeat on a later page
        t_last = _page_text(out, n_pages - 1)
        assert "Description" in t_last, "header did not repeat on later page"
        assert "Page" in t1 and "of" in t1, "page footer missing"

    print("  [1] table_pdf: 80 rows ->", n_pages, "pages, header repeats, ok")


# --------------------------------------------------------------- test 2 ---

@dataclass
class _Rec:
    number: str
    title: str
    answered: bool

    @property
    def has_answer(self) -> bool:
        return self.answered


@dataclass
class _Row:
    record: _Rec
    pages: list = field(default_factory=list)
    via: str = ""


class _PageInfo:
    def __init__(self, sheet: str):
        self.sheet = sheet


class _Index:
    def __init__(self, mapping: dict[int, str]):
        self._m = mapping

    def info(self, page_no: int) -> _PageInfo:
        return _PageInfo(self._m[page_no])


@dataclass
class _Report:
    rows: list
    index: _Index


def test_rfi_log_pdf():
    index = _Index({1: "P-101", 2: "P-102", 3: "M-201"})
    rows = [
        # deliberately out of order to prove sorting
        _Row(_Rec("002", "Waste line reroute at column line", False),
             pages=[2, 3], via="body"),
        _Row(_Rec("001", "Foundation anchor detail clarification", True),
             pages=[1], via="planref"),
        _Row(_Rec("003", "Door hardware set — no sheet reference", False),
             pages=[], via="unmatched"),           # empty -> "(unmatched)"
    ]
    report = _Report(rows=rows, index=index)

    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "rfi_log.pdf")
        result = transmittal.rfi_log_pdf(report, out, log=_quiet)

        assert os.path.exists(out), "RFI log PDF was not written"
        assert result["rows"] == 3, result

        t1 = _page_text(out, 0)
        assert "RFI LOG" in t1, "title 'RFI LOG' missing from page 1"
        assert "001" in t1, "RFI number missing from page 1"
        assert "P-101" in t1, "sheet string missing from page 1"
        assert "M-201" in t1, "joined sheet string missing from page 1"
        assert "(unmatched)" in t1, "'(unmatched)' missing for empty-pages row"
        assert "Answered" in t1, "header column missing"
        # ordering: RFI 001 should appear before 002 in the extracted text
        assert t1.find("001") < t1.find("002"), "rows not sorted by RFI number"

    print("  [2] rfi_log_pdf: 3 rows, sheets joined, (unmatched) shown, ok")


def main() -> int:
    try:
        test_table_pdf_paginates()
        test_rfi_log_pdf()
    except AssertionError as e:
        print("TRANSMITTAL TESTS FAILED:", e)
        return 1
    except Exception as e:                              # pragma: no cover
        import traceback
        traceback.print_exc()
        print("TRANSMITTAL TESTS FAILED (error):", e)
        return 1
    print("TRANSMITTAL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
