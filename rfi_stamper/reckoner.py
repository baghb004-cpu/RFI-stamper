"""Reckoner: quantity takeoff and pricing from drawing markups (offline).

Turns the markup layer into an estimate: count dots become "ea" quantities,
length/polylength runs become linear footage (via the per-page scale
calibration), and area takeoffs become square footage.  A local price book
CSV — any spreadsheet export with roughly-named columns — attaches unit
costs.  Everything stays on disk: no cloud pricing, no lookups, no network.

Depends only on the stdlib, :mod:`rfi_stamper.markups.measure` for the
measurement math and :mod:`rfi_stamper.transmittal` for the PDF rendering.
All file writes are atomic (temp file + ``os.replace``).
"""
from __future__ import annotations

import csv
import io
import os
import re
from dataclasses import dataclass, field

from .markups import measure

# ------------------------------------------------------------- price book ---

#: Header aliases accepted by :meth:`PriceBook.load` (all case-insensitive).
_HEADER_ALIASES = {
    "code": "code", "item": "code",
    "desc": "desc", "description": "desc", "name": "desc",
    "unit": "unit", "uom": "unit",
    "cost": "unit_cost", "unit_cost": "unit_cost", "unit cost": "unit_cost",
    "price": "unit_cost", "rate": "unit_cost",
}


@dataclass
class PriceItem:
    code: str
    desc: str = ""
    unit: str = ""
    unit_cost: float = 0.0


def _parse_cost(value, log=None) -> float:
    """Tolerant money parser: strips ``$`` and whitespace, honors accounting
    parentheses as negatives, and disambiguates decimal vs. grouping
    separators for both ``1,234.56`` and EU ``1.234,56``.  A genuinely
    unparseable non-empty value is logged (if ``log`` given) and returns 0.0.
    """
    text = str(value or "").strip().replace("$", "").replace(" ", "")
    if not text:
        return 0.0
    neg = False
    if text.startswith("(") and text.endswith(")"):     # accounting negative
        neg = True
        text = text[1:-1].strip()
    if text.startswith("-"):
        neg = True
        text = text[1:]
    has_comma = "," in text
    has_dot = "." in text
    if has_comma and has_dot:
        # the rightmost separator is the decimal point; the other groups
        if text.rfind(",") > text.rfind("."):           # EU: 1.234,56
            text = text.replace(".", "").replace(",", ".")
        else:                                            # US: 1,234.56
            text = text.replace(",", "")
    elif has_comma:
        # a trailing ,\d{1,2} is a decimal comma; otherwise thousands commas
        if re.search(r",\d{1,2}$", text):
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")
    try:
        val = float(text)
    except ValueError:
        if log is not None:
            log(f"  !! unparseable cost {value!r}, treating as 0")
        return 0.0
    return -val if neg else val


class PriceBook:
    """A local unit-cost catalog loaded from a tolerant CSV file."""

    def __init__(self, path: str | None = None):
        self.items: list[PriceItem] = []
        if path:
            self.load(path)

    def load(self, path: str) -> int:
        """Load price items from ``path``; returns the number of rows loaded.

        Header names are matched case-insensitively against the aliases
        code/item, desc/description/name, unit/uom and
        cost/unit_cost/price/rate; a UTF-8 BOM is tolerated; costs may carry
        ``$`` signs and thousands commas.  Rows with neither a code nor a
        description are skipped.  Loaded items are appended to :attr:`items`.
        """
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            try:
                raw_header = next(reader)
            except StopIteration:
                return 0
            cols: dict[int, str] = {}
            for i, h in enumerate(raw_header):
                key = _HEADER_ALIASES.get(str(h).strip().lstrip("\ufeff").lower())
                if key and key not in cols.values():
                    cols[i] = key
            loaded = 0
            for row in reader:
                rec = {"code": "", "desc": "", "unit": "", "unit_cost": ""}
                for i, key in cols.items():
                    if i < len(row):
                        rec[key] = str(row[i]).strip()
                if not rec["code"] and not rec["desc"]:
                    continue                       # blank / separator row
                self.items.append(PriceItem(
                    code=rec["code"], desc=rec["desc"], unit=rec["unit"],
                    unit_cost=_parse_cost(rec["unit_cost"])))
                loaded += 1
        return loaded

    def find(self, key: str) -> PriceItem | None:
        """Exact code match (case-insensitive) first, then a description
        substring match — but only when exactly one item's description
        contains the key (an ambiguous key matches nothing)."""
        needle = (key or "").strip().lower()
        if not needle:
            return None
        for item in self.items:
            if item.code.strip().lower() == needle:
                return item
        hits = [item for item in self.items if needle in item.desc.lower()]
        return hits[0] if len(hits) == 1 else None


# ----------------------------------------------------------------- takeoff ---

@dataclass
class TakeoffLine:
    subject: str
    kind: str                    # "count" | "length" | "area"
    qty: float = 0.0
    unit: str = ""
    pages: list = field(default_factory=list)
    code: str = ""
    unit_cost: float = 0.0
    total: float = 0.0


def _count_key(m) -> str:
    """Group key for a count markup: subject, else the text prefix before any
    digits (so "P1", "P2"... collapse to "P"), else the literal "count"."""
    subject = (getattr(m, "subject", "") or "").strip()
    if subject:
        return subject
    text = (getattr(m, "text", "") or "").strip()
    head = re.match(r"\D+", text)
    if head:
        prefix = head.group().strip()
        if prefix:
            return prefix
    return "count"


def _area_unit(unit: str) -> str:
    """Squared-unit label: feet read "sf", anything else gets a "2" suffix."""
    unit = (unit or "").strip()
    if unit in ("ft", "ft-in"):
        return "sf"
    return unit + "2" if unit else ""


def takeoff(store, cal_for=None, log=print) -> list[TakeoffLine]:
    """Roll the markups in ``store`` up into quantity takeoff lines.

    ``store`` is a :class:`~rfi_stamper.markups.model.MarkupStore` (or any
    object with a ``.markups`` list).  ``cal_for`` is a callable mapping a
    1-based page number to a :class:`~rfi_stamper.markups.measure.ScaleCal`
    or ``None`` (the per-page scale memory); pass ``None`` for no calibration
    anywhere.

    Grouping: ``count`` markups by subject / text-prefix / "count" (kind
    "count", qty = number of markups, unit "ea"); ``measure_length`` and
    ``measure_polylength`` together by subject (kind "length", qty = sum of
    the measured real lengths); ``measure_area`` by subject (kind "area",
    unit "sf" for feet, "<unit>2" otherwise).  Measure markups on pages with
    no calibration are skipped and logged once per group.  Lines come back
    sorted by kind then subject; each line's ``pages`` lists the sorted
    distinct pages that contributed quantity.
    """
    if cal_for is None:
        cal_for = lambda page: None                              # noqa: E731
    groups: dict[tuple, dict] = {}   # (kind, subject) -> working dict

    def _group(kind: str, subject: str) -> dict:
        return groups.setdefault((kind, subject), {
            "qty": 0.0, "unit": "", "pages": set(), "skipped": {}})

    for m in store.markups:
        mtype = getattr(m, "type", "")
        if mtype == "count":
            g = _group("count", _count_key(m))
            g["qty"] += 1
            g["unit"] = "ea"
            g["pages"].add(m.page)
        elif mtype in ("measure_length", "measure_polylength", "measure_area"):
            kind = "area" if mtype == "measure_area" else "length"
            fallback = "area" if kind == "area" else "length"
            subject = (getattr(m, "subject", "") or "").strip() or fallback
            g = _group(kind, subject)
            cal = cal_for(m.page)
            if cal is None:
                g["skipped"][m.page] = g["skipped"].get(m.page, 0) + 1
                continue
            g["qty"] += measure.compute(m, cal)
            if not g["unit"]:
                g["unit"] = _area_unit(cal.unit) if kind == "area" else cal.unit
            g["pages"].add(m.page)

    lines: list[TakeoffLine] = []
    for (kind, subject), g in groups.items():
        if g["skipped"]:
            n = sum(g["skipped"].values())
            pages = ", ".join(str(p) for p in sorted(g["skipped"]))
            log(f"  !! {subject}: {n} item(s) skipped: no scale on page {pages}")
        lines.append(TakeoffLine(subject=subject, kind=kind, qty=g["qty"],
                                 unit=g["unit"], pages=sorted(g["pages"])))
    lines.sort(key=lambda ln: (ln.kind, ln.subject.lower(), ln.subject))
    return lines


# ----------------------------------------------------------------- pricing ---

def price(lines, book: PriceBook, log=print) -> dict:
    """Attach unit costs from ``book`` to the takeoff ``lines`` in place.

    Each line is looked up by its preset ``code`` first (if any), then by its
    subject via :meth:`PriceBook.find`.  Matched lines get ``code``,
    ``unit_cost`` and ``total = qty * unit_cost`` filled in; unmatched lines
    keep a total of 0.  Returns ``{"total": grand_total, "matched": k,
    "unmatched": m}``.
    """
    lines = list(lines)
    matched = unmatched = 0
    grand = 0.0
    for line in lines:
        item = book.find(line.code) if line.code else None
        if item is None:
            item = book.find(line.subject)
        if item is None:
            line.total = 0.0
            unmatched += 1
            log(f"  !! no price for {line.subject!r} ({line.kind})")
            continue
        line.code = item.code
        line.unit_cost = item.unit_cost
        line.total = line.qty * item.unit_cost
        grand += line.total
        matched += 1
    log(f"  priced {matched} of {len(lines)} line(s); "
        f"estimate total {grand:,.2f}")
    return {"total": grand, "matched": matched, "unmatched": unmatched}


# ----------------------------------------------------------------- exports ---

_HEADERS = ["Subject", "Kind", "Qty", "Unit", "Pages", "Code",
            "Unit Cost", "Total"]


#: Leading characters a spreadsheet may interpret as a formula (CSV injection).
_CSV_INJECT = ("=", "+", "-", "@", "\t", "\r", "\n")


def _csv_safe(v):
    """Prefix a text cell that starts with a formula trigger (= + - @ TAB CR
    LF) with a single quote so spreadsheets treat it as text, not a formula.
    Program-generated numeric columns are passed unchanged by the caller."""
    if isinstance(v, str) and v[:1] in _CSV_INJECT:
        return "'" + v
    return v


def _pages_text(pages) -> str:
    return ", ".join(str(p) for p in pages)


def _fmt_qty(qty: float) -> str:
    """Whole counts print bare; fractional quantities keep two decimals."""
    if abs(qty - round(qty)) < 1e-9:
        return str(int(round(qty)))
    return f"{qty:,.2f}"


def export_csv(lines, out_path: str, log=print) -> int:
    """Write the takeoff lines as CSV (atomic); returns the row count.

    Numbers are machine-readable (no thousands separators): quantities at
    full precision, money at two decimals.
    """
    lines = list(lines)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_HEADERS)
    for line in lines:
        writer.writerow([_csv_safe(line.subject), _csv_safe(line.kind),
                         f"{line.qty:.10g}", _csv_safe(line.unit),
                         _csv_safe(_pages_text(line.pages)), _csv_safe(line.code),
                         f"{line.unit_cost:.2f}", f"{line.total:.2f}"])
    tmp = out_path + ".part"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out_path)
    log(f"  wrote {out_path} ({len(lines)} row(s))")
    return len(lines)


def takeoff_pdf(lines, out_path: str, title: str = "RECKONER — QUANTITY TAKEOFF",
                summary: dict | None = None, log=print) -> dict:
    """Render the takeoff as a table PDF via :func:`transmittal.table_pdf`.

    When a ``summary`` dict from :func:`price` is given, the subtitle shows
    the grand total and the matched/unmatched line counts.  Returns
    :func:`~rfi_stamper.transmittal.table_pdf`'s result dict (atomic write).
    """
    from . import transmittal
    lines = list(lines)
    rows = [[line.subject, line.kind, _fmt_qty(line.qty), line.unit,
             _pages_text(line.pages), line.code,
             f"{line.unit_cost:,.2f}", f"{line.total:,.2f}"]
            for line in lines]
    if summary is not None:
        subtitle = (f"Grand total ${summary.get('total', 0.0):,.2f} — "
                    f"{summary.get('matched', 0)} matched, "
                    f"{summary.get('unmatched', 0)} unmatched")
    else:
        subtitle = f"{len(lines)} takeoff line(s)"
    return transmittal.table_pdf(out_path, _HEADERS, rows, title=title,
                                 subtitle=subtitle, log=log)
