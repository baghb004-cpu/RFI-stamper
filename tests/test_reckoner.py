"""Self-contained tests for rfi_stamper.reckoner
(run: python3.12 tests/test_reckoner.py)."""
from __future__ import annotations

import csv
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from rfi_stamper.markups import Markup, ScaleCal  # noqa: E402
from rfi_stamper.reckoner import (PriceBook, PriceItem, TakeoffLine,  # noqa: E402
                                  export_csv, price, takeoff, takeoff_pdf)

TD = tempfile.mkdtemp(prefix="reckoner_test_")


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


class FakeStore:
    """Any object with .markups quacks like a MarkupStore for takeoff()."""

    def __init__(self, markups):
        self.markups = markups


def build_store() -> FakeStore:
    ms = []
    # counts: 3 with a subject, 2 subjectless with text "P"
    for i in range(3):
        ms.append(Markup.new(1, "count", [(10 + 5 * i, 20)],
                             subject="Sprinkler Head"))
    for i in range(2):
        ms.append(Markup.new(1, "count", [(50 + 5 * i, 60)], text="P"))
    # lengths, subject "Pipe Run": two on page 1, one on page 2 (no scale)
    ms.append(Markup.new(1, "measure_length", [(0, 0), (100, 0)],
                         subject="Pipe Run"))          # 100 pt -> 10 ft
    ms.append(Markup.new(1, "measure_length", [(0, 0), (0, 50)],
                         subject="Pipe Run"))          # 50 pt  -> 5 ft
    ms.append(Markup.new(2, "measure_length", [(0, 0), (200, 0)],
                         subject="Pipe Run"))          # page 2: skipped
    # subjectless polylength -> "length" fallback group; 30+40 pt -> 7 ft
    ms.append(Markup.new(1, "measure_polylength", [(0, 0), (30, 0), (30, 40)]))
    # area rectangle 100 x 50 pt -> 5000 pt^2 * 0.1^2 = 50 sf
    ms.append(Markup.new(1, "measure_area",
                         [(0, 0), (100, 0), (100, 50), (0, 50)],
                         subject="Slab"))
    return FakeStore(ms)


def cal_for(page):
    return ScaleCal(real_per_pt=0.1, unit="ft") if page == 1 else None


def by_key(lines, kind, subject):
    hits = [ln for ln in lines if ln.kind == kind and ln.subject == subject]
    assert len(hits) == 1, f"expected one {kind}/{subject} line, got {hits}"
    return hits[0]


# ---------------------------------------------------------------- takeoff ---

def test_takeoff():
    store = build_store()
    logs = []
    lines = takeoff(store, cal_for, log=logs.append)

    assert len(lines) == 5, [(ln.kind, ln.subject) for ln in lines]
    # sorted by kind then subject: area < count < length
    assert [ln.kind for ln in lines] == ["area", "count", "count",
                                         "length", "length"]

    heads = by_key(lines, "count", "Sprinkler Head")
    assert approx(heads.qty, 3.0) and heads.unit == "ea"
    assert heads.pages == [1]

    dots = by_key(lines, "count", "P")           # text-prefix grouping
    assert approx(dots.qty, 2.0) and dots.unit == "ea"

    pipe = by_key(lines, "length", "Pipe Run")
    expect = (math.hypot(100, 0) + math.hypot(0, 50)) * 0.1   # page 1 only
    assert approx(pipe.qty, expect), pipe.qty                 # = 15.0 ft
    assert approx(pipe.qty, 15.0)
    assert pipe.unit == "ft"
    assert pipe.pages == [1]                     # page 2 never contributed

    # skip on page 2 logged exactly once, for the Pipe Run group
    skips = [s for s in logs if "skipped: no scale on page 2" in s]
    assert len(skips) == 1, logs
    assert "1 item(s) skipped" in skips[0] and "Pipe Run" in skips[0]

    poly = by_key(lines, "length", "length")     # subjectless fallback
    assert approx(poly.qty, (30 + 40) * 0.1)     # 7.0 ft

    slab = by_key(lines, "area", "Slab")
    assert approx(slab.qty, 100 * 50 * 0.1 * 0.1)   # 50.0
    assert slab.unit == "sf"

    # no calibration at all: counts still count, measures go to zero
    logs2 = []
    bare = takeoff(store, None, log=logs2.append)
    assert approx(by_key(bare, "count", "Sprinkler Head").qty, 3.0)
    p2 = by_key(bare, "length", "Pipe Run")
    assert approx(p2.qty, 0.0) and p2.unit == "" and p2.pages == []
    assert any("no scale" in s for s in logs2)
    print("takeoff: ok")
    return lines


# ------------------------------------------------------------- price book ---

def test_pricebook() -> PriceBook:
    path = os.path.join(TD, "prices.csv")
    # alias headers (Item/Description/UOM/Price), BOM, $ + thousands comma
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write('Item,Description,UOM,Price\n'
                'SH-1,Sprinkler Head pendant,ea,"$1,234.50"\n'
                'PR-2,Pipe Run copper,ft,12.00\n'
                'SLB,Slab on grade,sf,$4.25\n')
    book = PriceBook(path)
    assert len(book.items) == 3
    assert book.load(path) == 3                  # load() reports rows loaded
    book.items = book.items[:3]                  # keep one copy for the rest

    sh = book.items[0]
    assert isinstance(sh, PriceItem)
    assert sh.code == "SH-1" and sh.unit == "ea"
    assert approx(sh.unit_cost, 1234.50)         # $-sign + comma stripped

    assert book.find("sh-1") is sh               # code, case-insensitive
    assert book.find("Slab").code == "SLB"       # unique desc substring
    assert book.find("pipe run").code == "PR-2"
    assert book.find("e") is None                # ambiguous substring
    assert book.find("no such thing") is None
    assert book.find("") is None
    print("pricebook: ok")
    return book


# ----------------------------------------------------------------- pricing ---

def test_price(lines, book):
    summary = price(lines, book, log=lambda *a: None)
    heads = by_key(lines, "count", "Sprinkler Head")
    assert heads.code == "SH-1"
    assert approx(heads.unit_cost, 1234.50)
    assert approx(heads.total, 3 * 1234.50)      # 3703.50
    pipe = by_key(lines, "length", "Pipe Run")
    assert approx(pipe.total, 15.0 * 12.00)      # 180.00
    slab = by_key(lines, "area", "Slab")
    assert approx(slab.total, 50.0 * 4.25)       # 212.50
    # "P" is an ambiguous substring, "length" matches nothing -> unpriced
    assert approx(by_key(lines, "count", "P").total, 0.0)
    assert approx(by_key(lines, "length", "length").total, 0.0)

    assert summary["matched"] == 3 and summary["unmatched"] == 2
    assert approx(summary["total"], 3703.50 + 180.00 + 212.50)   # 4096.00

    # a preset line.code wins even when the subject matches nothing
    coded = TakeoffLine(subject="zzz not in book", kind="count",
                        qty=2.0, unit="ea", code="SH-1")
    s2 = price([coded], book, log=lambda *a: None)
    assert s2["matched"] == 1 and approx(coded.total, 2 * 1234.50)
    print("price: ok")
    return summary


# ----------------------------------------------------------------- exports ---

def test_export_csv(lines):
    out = os.path.join(TD, "takeoff.csv")
    assert export_csv(lines, out, log=lambda *a: None) == len(lines)
    with open(out, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == len(lines)
    byname = {(r["Kind"], r["Subject"]): r for r in rows}
    r = byname[("count", "Sprinkler Head")]
    assert float(r["Qty"]) == 3.0 and r["Unit"] == "ea"
    assert r["Code"] == "SH-1"
    assert float(r["Unit Cost"]) == 1234.50
    assert float(r["Total"]) == 3703.50
    r = byname[("length", "Pipe Run")]
    assert approx(float(r["Qty"]), 15.0) and r["Pages"] == "1"
    r = byname[("area", "Slab")]
    assert approx(float(r["Total"]), 212.50)
    assert not os.path.exists(out + ".part")     # atomic: no temp left behind
    print("export_csv: ok")


def test_takeoff_pdf(lines, summary):
    out = os.path.join(TD, "takeoff.pdf")
    res = takeoff_pdf(lines, out, summary=summary, log=lambda *a: None)
    assert res["rows"] == len(lines) and res["pages"] >= 1
    assert not os.path.exists(out + ".part")
    with fitz.open(out) as doc:
        text = doc[0].get_text()
    assert "QUANTITY TAKEOFF" in text            # title
    assert "Sprinkler Head" in text              # a subject row
    assert "4,096.00" in text                    # grand total in subtitle
    assert "3 matched" in text and "2 unmatched" in text
    print("takeoff_pdf: ok")


def main() -> int:
    lines = test_takeoff()
    book = test_pricebook()
    summary = test_price(lines, book)
    test_export_csv(lines)
    test_takeoff_pdf(lines, summary)
    print("RECKONER TESTS PASSED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError:
        import traceback
        traceback.print_exc()
        raise SystemExit(1)
