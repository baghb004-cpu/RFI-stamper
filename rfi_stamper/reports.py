"""Printable field forms & project report PDFs (offline, reportlab + pypdf).

Two capabilities, both sharing the toolkit's visual identity (red accent
RGB 0.84, 0.06, 0.06, generous whitespace, "Page X of Y" footers):

* **Forms** — :class:`FormTemplate` describes a form as a list of
  :class:`FormField` (text / multiline / check / choice).
  :func:`render_blank_form` prints a clean fill-by-hand sheet (ruled lines,
  ruled blocks, real checkbox squares, inline option squares);
  :func:`render_filled_form` prints the same layout with values typed in
  (checked boxes get a mark, the picked choice is highlighted).
  :data:`BUILTIN_TEMPLATES` ships the common jobsite forms.

* **Snapshot** — :func:`project_snapshot_pdf` renders a one-look status PDF
  for a duck-typed project object (``.summary()`` plus the ``.tasks`` /
  ``.punch`` / ``.change_orders`` / ``.budget`` / ``.inspections`` lists):
  page 1 is big KPI blocks with a drawn budget bar, then compact table
  sections delegated to :func:`rfi_stamper.transmittal.table_pdf` and merged
  into one PDF.

Everything is generated locally from the inputs given — no network, no
external services — and every output file is written atomically
(tmp + fsync + ``os.replace``) so a crash can never leave a truncated PDF.
"""
from __future__ import annotations

import datetime as _dt
import io
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as _canvas

from .merge import _atomic_write as _atomic_write_writer
from .transmittal import (
    ACCENT,
    _atomic_write_bytes,
    _BOXLINE,
    _GRIDLINE,
    _INK,
    _NumberedCanvas,
    _SUBTLE,
    table_pdf,
)

# ---------------------------------------------------------------- geometry ---

_PAGE_W, _PAGE_H = letter
_MARGIN = 54.0                       # 0.75 in on every side (matches transmittal)
_TOP = _PAGE_H - _MARGIN
_MIN_Y = 76.0                        # keep clear of the footer rule
_USABLE = _PAGE_W - 2 * _MARGIN
_LABEL_W = 150.0                     # left column reserved for field labels
_BAR_BG = colors.Color(0.93, 0.93, 0.94)

#: Mark drawn inside a checked square — renders the classic box-with-X look
#: and survives text extraction as a plain "X" (ZapfDingbats does not).
_CHECK_GLYPH = "X"


def _latin(text) -> str:
    """Make text safe for the base-14 fonts (WinAnsi); replace what won't fit."""
    s = "" if text is None else str(text)
    return s.encode("cp1252", "replace").decode("cp1252")


def _wrap(text, font: str, size: float, width: float) -> list[str]:
    """Wrap text to ``width`` points, honoring embedded newlines."""
    lines: list[str] = []
    for para in _latin(text).replace("\r\n", "\n").split("\n"):
        if para.strip():
            lines.extend(simpleSplit(para, font, size, width) or [""])
        else:
            lines.append("")
    return lines or [""]


# ------------------------------------------------------------------- model ---

@dataclass
class FormField:
    """One labeled entry on a form.

    ``kind`` is one of ``"text"`` (single ruled line), ``"multiline"`` (ruled
    block), ``"check"`` (checkbox square) or ``"choice"`` (inline option
    squares drawn from ``choices``).  ``default`` pre-fills the value when a
    filled form is rendered without an explicit entry for ``key``.
    """

    key: str
    label: str
    kind: str = "text"                       # text / multiline / check / choice
    choices: list = field(default_factory=list)
    default: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "FormField":
        return cls(
            key=str(data.get("key", "")),
            label=str(data.get("label", "")),
            kind=str(data.get("kind", "text")),
            choices=list(data.get("choices", []) or []),
            default=str(data.get("default", "") or ""),
        )


@dataclass
class FormTemplate:
    """A named, ordered collection of :class:`FormField`."""

    id: str
    name: str
    fields: list

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "fields": [f.to_dict() if isinstance(f, FormField) else dict(f)
                       for f in self.fields],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FormTemplate":
        fields_ = [f if isinstance(f, FormField) else FormField.from_dict(f)
                   for f in data.get("fields", []) or []]
        return cls(id=str(data.get("id", "")), name=str(data.get("name", "")),
                   fields=fields_)

    @classmethod
    def new(cls, name: str, fields: list) -> "FormTemplate":
        """Create a template with an id slugged from ``name``."""
        slug = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-") or "form"
        return cls(id=slug, name=str(name), fields=list(fields))


# -------------------------------------------------------- builtin templates ---

BUILTIN_TEMPLATES: list[FormTemplate] = [
    FormTemplate.new("Daily Field Report", [
        FormField("date", "Date"),
        FormField("weather", "Weather", "choice",
                  choices=["Clear", "Partly Cloudy", "Overcast", "Rain",
                           "Snow", "Wind"]),
        FormField("temperature", "Temperature"),
        FormField("crew_count", "Crew Count"),
        FormField("work_performed", "Work Performed", "multiline"),
        FormField("delays", "Delays / Disruptions", "multiline"),
        FormField("safety_incidents", "Safety Incidents Occurred", "check"),
        FormField("safety_note", "Incident Notes", "multiline"),
        FormField("visitors", "Visitors On Site"),
    ]),
    FormTemplate.new("Safety Inspection", [
        FormField("date", "Date"),
        FormField("area", "Area Inspected"),
        FormField("housekeeping", "Housekeeping / Debris Clear", "check"),
        FormField("ppe", "PPE Worn Correctly", "check"),
        FormField("fall_protection", "Fall Protection In Place", "check"),
        FormField("ladders", "Ladders / Scaffolds Sound", "check"),
        FormField("electrical", "Electrical Cords / GFCI OK", "check"),
        FormField("fire", "Fire Extinguishers Accessible", "check"),
        FormField("excavation", "Trenches / Excavations Protected", "check"),
        FormField("signage", "Signage / Barricades Posted", "check"),
        FormField("corrective_actions", "Corrective Actions", "multiline"),
    ]),
    FormTemplate.new("QC Punch Walk", [
        FormField("date", "Date"),
        FormField("area", "Area / Location"),
        FormField("items", "Punch Items", "multiline"),
        FormField("reinspection", "Ready For Re-Inspection", "check"),
    ]),
    FormTemplate.new("RFI Follow-Up", [
        FormField("rfi_number", "RFI Number"),
        FormField("sheet", "Sheet"),
        FormField("question", "Question Recap", "multiline"),
        FormField("status", "Resolution Status", "choice",
                  choices=["Open", "Answered", "In Work", "Fixed", "Verified"]),
        FormField("verified_by", "Verified By"),
        FormField("date", "Date"),
    ]),
]


def _builtin(template_id: str) -> FormTemplate:
    for t in BUILTIN_TEMPLATES:
        if t.id == template_id or t.name == template_id:
            return t
    raise KeyError(f"no builtin template {template_id!r}")


# --------------------------------------------------------------- form paint ---

_TRUE_WORDS = {"1", "x", "y", "yes", "true", "on", "checked", "done", "[x]"}


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_WORDS
    return bool(value)


class _FormPainter:
    """Cursor-tracked direct-canvas form renderer with pagination."""

    def __init__(self, canv, name: str, subtitle: str):
        self.c = canv
        self.name = name
        self.subtitle = subtitle
        self.y = self._header(first=True)

    # -- chrome ------------------------------------------------------------
    def _header(self, first: bool) -> float:
        c = self.c
        if first:
            c.setFillColor(ACCENT)
            c.setFont("Helvetica-Bold", 22)
            c.drawString(_MARGIN, _TOP - 20, _latin(self.name).upper())
            y = _TOP - 20
            if self.subtitle:
                c.setFillColor(_SUBTLE)
                c.setFont("Helvetica", 10.5)
                c.drawString(_MARGIN, y - 15, _latin(self.subtitle))
                y -= 15
            c.setStrokeColor(ACCENT)
            c.setLineWidth(1.5)
            c.line(_MARGIN, y - 9, _PAGE_W - _MARGIN, y - 9)
            return y - 32
        c.setFillColor(ACCENT)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(_MARGIN, _TOP - 10, _latin(self.name).upper() + " (CONTINUED)")
        c.setStrokeColor(ACCENT)
        c.setLineWidth(0.75)
        c.line(_MARGIN, _TOP - 17, _PAGE_W - _MARGIN, _TOP - 17)
        return _TOP - 38

    def need(self, height: float) -> None:
        """Break to a fresh page if ``height`` points will not fit."""
        if self.y - height < _MIN_Y:
            self.c.showPage()
            self.y = self._header(first=False)

    # -- shared bits ---------------------------------------------------------
    def _rule(self, x0: float, x1: float, baseline: float) -> None:
        self.c.setStrokeColor(_GRIDLINE)
        self.c.setLineWidth(0.7)
        self.c.line(x0, baseline - 2.5, x1, baseline - 2.5)

    def _label(self, text: str, baseline: float) -> None:
        self.c.setFillColor(_INK)
        self.c.setFont("Helvetica-Bold", 9)
        self.c.drawString(_MARGIN, baseline, _latin(text))

    def _square(self, x: float, ybot: float, size: float,
                picked: bool = False) -> None:
        c = self.c
        if picked:
            c.setFillColor(ACCENT)
            c.setStrokeColor(ACCENT)
            c.setLineWidth(0.9)
            c.rect(x, ybot, size, size, stroke=1, fill=1)
            font_size = size - 1.5
            glyph_w = stringWidth(_CHECK_GLYPH, "Helvetica-Bold", font_size)
            c.setFillColor(colors.white)
            c.setFont("Helvetica-Bold", font_size)
            c.drawString(x + (size - glyph_w) / 2.0, ybot + size * 0.18,
                         _CHECK_GLYPH)
        else:
            c.setStrokeColor(_INK)
            c.setLineWidth(0.9)
            c.rect(x, ybot, size, size, stroke=1, fill=0)

    # -- field kinds ---------------------------------------------------------
    def text(self, fld: FormField, value: str) -> None:
        x0 = _MARGIN + _LABEL_W
        x1 = _PAGE_W - _MARGIN
        lines = _wrap(value, "Helvetica", 9.5, x1 - x0 - 4) if value else [""]
        for i, line in enumerate(lines):
            self.need(22)
            baseline = self.y - 13
            if i == 0:
                self._label(fld.label, baseline)
            self._rule(x0, x1, baseline)
            if line:
                self.c.setFillColor(_INK)
                self.c.setFont("Helvetica", 9.5)
                self.c.drawString(x0 + 2, baseline, line)
            self.y -= 22

    def multiline(self, fld: FormField, value: str) -> None:
        self.need(18 + 17)                     # label plus at least one rule
        self._label(fld.label, self.y - 12)
        self.y -= 18
        lines = _wrap(value, "Helvetica", 9.5, _USABLE - 4) if value else []
        n_rules = max(4, len(lines)) if not value else max(3, len(lines))
        for i in range(n_rules):
            self.need(17)
            baseline = self.y - 12
            self._rule(_MARGIN, _PAGE_W - _MARGIN, baseline)
            if i < len(lines) and lines[i]:
                self.c.setFillColor(_INK)
                self.c.setFont("Helvetica", 9.5)
                self.c.drawString(_MARGIN + 2, baseline, lines[i])
            self.y -= 17
        self.y -= 5

    def check(self, fld: FormField, value, filled: bool) -> None:
        self.need(22)
        baseline = self.y - 13
        size = 10.0
        self._square(_MARGIN, baseline - 1.5, size,
                     picked=filled and _truthy(value))
        self.c.setFillColor(_INK)
        self.c.setFont("Helvetica-Bold", 9)
        self.c.drawString(_MARGIN + size + 7, baseline, _latin(fld.label))
        self.y -= 22

    def choice(self, fld: FormField, value, filled: bool) -> None:
        self.need(22)
        baseline = self.y - 13
        self._label(fld.label, baseline)
        picked_text = str(value or "").strip().lower()
        x = _MARGIN + _LABEL_W
        size = 9.0
        for opt in fld.choices:
            opt = _latin(opt)
            w = size + 5 + stringWidth(opt, "Helvetica-Bold", 9) + 16
            if x + w > _PAGE_W - _MARGIN and x > _MARGIN + _LABEL_W:
                self.y -= 18                    # wrap options to a new line
                self.need(18)
                baseline = self.y - 13
                x = _MARGIN + _LABEL_W
            picked = filled and bool(picked_text) \
                and opt.strip().lower() == picked_text
            self._square(x, baseline - 1, size, picked=picked)
            self.c.setFillColor(ACCENT if picked else _INK)
            self.c.setFont("Helvetica-Bold" if picked else "Helvetica", 9)
            self.c.drawString(x + size + 5, baseline, opt)
            x += w
        self.y -= 22


def _render_form(template: FormTemplate, values: dict | None,
                 out_path: str, log=print) -> dict:
    filled = values is not None
    vals = dict(values or {})
    holder: dict = {}
    buf = io.BytesIO()
    canv = _NumberedCanvas(buf, pagesize=letter,
                           footer_note=template.name, count_holder=holder)
    canv.setTitle(template.name)
    subtitle = "Completed form" if filled else "Blank form — print and fill in"
    painter = _FormPainter(canv, template.name, subtitle)

    for fld in template.fields:
        raw = vals.get(fld.key, fld.default) if filled else ""
        value = "" if raw is None else raw
        kind = (fld.kind or "text").strip().lower()
        if kind == "multiline":
            painter.multiline(fld, str(value) if filled else "")
        elif kind == "check":
            painter.check(fld, value, filled)
        elif kind == "choice":
            painter.choice(fld, value, filled)
        else:
            painter.text(fld, str(value) if filled else "")
        painter.y -= 4                          # breathing room between fields

    canv.showPage()
    canv.save()
    _atomic_write_bytes(buf.getvalue(), out_path)
    pages = int(holder.get("pages", 1))
    log(f"  wrote {out_path} ({len(template.fields)} field(s), "
        f"{pages} page(s))")
    return {"out_path": out_path, "fields": len(template.fields),
            "pages": pages}


def render_blank_form(template: FormTemplate, out_path: str,
                      log=print) -> dict:
    """Render a clean printable (fill-by-hand) form for ``template``.

    Title bar, then one labeled row per field: text fields get a ruled line,
    multiline fields a ruled block, checks a real checkbox square, choices a
    row of inline option squares.  Paginates with a continuation header and
    "Page X of Y" footers.  Atomic write.
    """
    return _render_form(template, None, out_path, log=log)


def render_filled_form(template: FormTemplate, values: dict,
                       out_path: str, log=print) -> dict:
    """Render the same layout as :func:`render_blank_form` with ``values``
    printed in: text on the rules, checked boxes marked (checked/unchecked
    squares), and the picked choice highlighted in the accent color.
    ``values`` is keyed by field key; missing keys fall back to defaults.
    """
    return _render_form(template, dict(values or {}), out_path, log=log)


def daily_report_pdf(values: dict, out_path: str, log=print) -> dict:
    """Convenience: render a filled builtin "Daily Field Report"."""
    return render_filled_form(_builtin("daily-field-report"), values,
                              out_path, log=log)


# ----------------------------------------------------------- duck-type utils ---

_MISSING = object()


def _get(item, *names, default=None):
    """Fetch the first present attribute or mapping key from ``names``."""
    for name in names:
        if isinstance(item, dict):
            if name in item:
                return item[name]
        else:
            value = getattr(item, name, _MISSING)
            if value is not _MISSING:
                return value
    return default


def _num(value) -> float:
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _s(value) -> str:
    return "" if value is None else str(value)


def _money(value: float) -> str:
    return f"${value:,.0f}"


_DONE_STATES = {"done", "closed", "complete", "completed", "resolved",
                "verified", "fixed", "cancelled", "canceled", "passed",
                "accepted"}


def _is_open(status) -> bool:
    return str(status or "").strip().lower() not in _DONE_STATES


def _co_state(status) -> str:
    s = str(status or "").strip().lower()
    if s.startswith("approv"):
        return "approved"
    if s in {"rejected", "void", "withdrawn", "cancelled", "canceled",
             "denied", "superseded"}:
        return "closed"
    return "pending"


def _past_due(due) -> bool:
    s = str(due or "").strip()
    if not s:
        return False
    try:
        return _dt.date.fromisoformat(s[:10]) < _dt.date.today()
    except ValueError:
        pass
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return _dt.datetime.strptime(s, fmt).date() < _dt.date.today()
        except ValueError:
            continue
    return False


# -------------------------------------------------------------- KPI drawing ---

def _kpi_block(c, x: float, y_top: float, w: float, h: float,
               caption: str, big: str, sub: str = "") -> None:
    """One bordered stat block: accent top edge, caption, big number, note."""
    c.setStrokeColor(_BOXLINE)
    c.setLineWidth(0.8)
    c.rect(x, y_top - h, w, h, stroke=1, fill=0)
    c.setStrokeColor(ACCENT)
    c.setLineWidth(2.0)
    c.line(x, y_top, x + w, y_top)
    c.setFillColor(_SUBTLE)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(x + 10, y_top - 17, _latin(caption).upper())
    c.setFillColor(ACCENT)
    c.setFont("Helvetica-Bold", 26)
    c.drawString(x + 10, y_top - 45, _latin(big))
    if sub:
        c.setFillColor(_SUBTLE)
        c.setFont("Helvetica", 8.5)
        c.drawString(x + 10, y_top - 59, _latin(sub))


def _budget_block(c, x: float, y_top: float, w: float, h: float,
                  spent: float, total: float) -> None:
    """Full-width budget block with a drawn spent-vs-total bar."""
    c.setStrokeColor(_BOXLINE)
    c.setLineWidth(0.8)
    c.rect(x, y_top - h, w, h, stroke=1, fill=0)
    c.setStrokeColor(ACCENT)
    c.setLineWidth(2.0)
    c.line(x, y_top, x + w, y_top)
    c.setFillColor(_SUBTLE)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(x + 10, y_top - 17, "BUDGET — SPENT VS TOTAL")
    pct = (spent / total * 100.0) if total > 0 else 0.0
    c.setFillColor(ACCENT)
    c.setFont("Helvetica-Bold", 26)
    c.drawString(x + 10, y_top - 45, f"{pct:.0f}%" if total > 0 else "—")
    # the bar
    bar_x = x + 120
    bar_w = w - 132
    bar_h = 12.0
    bar_y = y_top - 42
    c.setFillColor(_BAR_BG)
    c.setStrokeColor(_BOXLINE)
    c.setLineWidth(0.7)
    c.rect(bar_x, bar_y, bar_w, bar_h, stroke=1, fill=1)
    if total > 0 and spent > 0:
        frac = min(1.0, spent / total)
        c.setFillColor(ACCENT)
        c.rect(bar_x, bar_y, bar_w * frac, bar_h, stroke=0, fill=1)
    c.setFillColor(_SUBTLE)
    c.setFont("Helvetica", 8.5)
    c.drawString(bar_x, bar_y - 13,
                 f"{_money(spent)} spent of {_money(total)} budgeted")


def _snapshot_page1(project, title: str) -> bytes:
    """Draw the KPI page and return it as PDF bytes."""
    tasks = list(getattr(project, "tasks", None) or [])
    punch = list(getattr(project, "punch", None) or [])
    change_orders = list(getattr(project, "change_orders", None) or [])
    budget = list(getattr(project, "budget", None) or [])

    summary = {}
    summarize = getattr(project, "summary", None)
    if callable(summarize):
        try:
            summary = dict(summarize() or {})
        except Exception:
            summary = {}

    tasks_open = sum(1 for t in tasks if _is_open(_get(t, "status", "state")))
    tasks_overdue = sum(
        1 for t in tasks
        if _is_open(_get(t, "status", "state"))
        and _past_due(_get(t, "due", "due_date", "deadline")))
    punch_open = sum(1 for p in punch if _is_open(_get(p, "status", "state")))
    co_pending = sum(1 for co in change_orders
                     if _co_state(_get(co, "status", "state")) == "pending")
    co_approved = sum(_num(_get(co, "amount", "value", "cost"))
                      for co in change_orders
                      if _co_state(_get(co, "status", "state")) == "approved")
    budget_total = sum(_num(_get(b, "budget", "total", "amount"))
                       for b in budget)
    budget_spent = sum(_num(_get(b, "spent", "actual", "used"))
                       for b in budget)

    today = _dt.date.today().isoformat()
    buf = io.BytesIO()
    c = _canvas.Canvas(buf, pagesize=letter)
    c.setTitle(title)

    # -- title bar ----------------------------------------------------------
    c.setFillColor(ACCENT)
    c.setFont("Helvetica-Bold", 24)
    c.drawString(_MARGIN, _TOP - 22, _latin(title).upper())
    c.setFillColor(_SUBTLE)
    c.setFont("Helvetica", 10.5)
    c.drawString(_MARGIN, _TOP - 38, f"Generated {today}")
    c.setStrokeColor(ACCENT)
    c.setLineWidth(1.5)
    c.line(_MARGIN, _TOP - 47, _PAGE_W - _MARGIN, _TOP - 47)
    y = _TOP - 70

    # -- summary() key/value strip (two columns) -----------------------------
    items = [(str(k), str(v)) for k, v in summary.items()][:8]
    col_w = _USABLE / 2.0
    for i, (key, value) in enumerate(items):
        cx = _MARGIN + (i % 2) * col_w
        cy = y - (i // 2) * 14
        c.setFillColor(_INK)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(cx, cy, _latin(key) + ":")
        c.setFillColor(_SUBTLE)
        c.setFont("Helvetica", 9)
        c.drawString(cx + stringWidth(_latin(key) + ":", "Helvetica-Bold", 9)
                     + 5, cy, _latin(value))
    if items:
        y -= 14 * ((len(items) + 1) // 2) + 14

    # -- KPI blocks -----------------------------------------------------------
    gap = 12.0
    block_w = (_USABLE - 2 * gap) / 3.0
    block_h = 70.0
    _kpi_block(c, _MARGIN, y, block_w, block_h,
               "Open Tasks", str(tasks_open),
               f"{tasks_overdue} overdue" if tasks_overdue else "none overdue")
    _kpi_block(c, _MARGIN + block_w + gap, y, block_w, block_h,
               "Punch Open", str(punch_open), f"of {len(punch)} item(s)")
    _kpi_block(c, _MARGIN + 2 * (block_w + gap), y, block_w, block_h,
               "CO Pending", str(co_pending),
               f"{_money(co_approved)} approved")
    y -= block_h + 16
    _budget_block(c, _MARGIN, y, _USABLE, 70.0, budget_spent, budget_total)

    # -- footer ---------------------------------------------------------------
    c.setStrokeColor(ACCENT)
    c.setLineWidth(0.75)
    c.line(_MARGIN, 40, _PAGE_W - _MARGIN, 40)
    c.setFillColor(_SUBTLE)
    c.setFont("Helvetica", 8)
    c.drawString(_MARGIN, 28, _latin(title))
    c.drawRightString(_PAGE_W - _MARGIN, 28, f"Generated {today}")

    c.showPage()
    c.save()
    return buf.getvalue()


def _snapshot_sections(project) -> list[tuple[str, str, list, list]]:
    """Build (title, subtitle, headers, rows) for each non-empty list."""
    sections: list[tuple[str, str, list, list]] = []

    tasks = list(getattr(project, "tasks", None) or [])
    if tasks:
        rows = [[_s(_get(t, "title", "name", "task")),
                 _s(_get(t, "status", "state")),
                 _s(_get(t, "due", "due_date", "deadline"))] for t in tasks]
        n_open = sum(1 for t in tasks if _is_open(_get(t, "status", "state")))
        sections.append(("TASKS", f"{n_open} open of {len(tasks)}",
                         ["Task", "Status", "Due"], rows))

    punch = list(getattr(project, "punch", None) or [])
    if punch:
        rows = [[_s(_get(p, "title", "description", "item")),
                 _s(_get(p, "area", "location")),
                 _s(_get(p, "status", "state"))] for p in punch]
        n_open = sum(1 for p in punch if _is_open(_get(p, "status", "state")))
        sections.append(("PUNCH LIST", f"{n_open} open of {len(punch)}",
                         ["Item", "Area", "Status"], rows))

    change_orders = list(getattr(project, "change_orders", None) or [])
    if change_orders:
        rows = [[_s(_get(co, "number", "id")),
                 _s(_get(co, "title", "name", "description")),
                 _s(_get(co, "status", "state")),
                 _money(_num(_get(co, "amount", "value", "cost")))]
                for co in change_orders]
        sections.append(("CHANGE ORDERS", f"{len(change_orders)} total",
                         ["CO", "Title", "Status", "Amount"], rows))

    budget = list(getattr(project, "budget", None) or [])
    if budget:
        rows = []
        for b in budget:
            total = _num(_get(b, "budget", "total", "amount"))
            spent = _num(_get(b, "spent", "actual", "used"))
            rows.append([_s(_get(b, "name", "category", "line")),
                         _money(total), _money(spent),
                         _money(total - spent)])
        sections.append(("BUDGET", f"{len(budget)} line(s)",
                         ["Line", "Budget", "Spent", "Remaining"], rows))

    inspections = list(getattr(project, "inspections", None) or [])
    if inspections:
        rows = [[_s(_get(i, "date", "when")),
                 _s(_get(i, "kind", "type", "title", "name")),
                 _s(_get(i, "result", "status", "outcome"))]
                for i in inspections]
        sections.append(("INSPECTIONS", f"{len(inspections)} total",
                         ["Date", "Type", "Result"], rows))
    return sections


def project_snapshot_pdf(project, out_path: str,
                         title: str = "PROJECT SNAPSHOT", log=print) -> dict:
    """Render a one-file status report for a duck-typed project object.

    ``project`` needs ``.summary()`` returning a dict, plus the list
    attributes ``.tasks``, ``.punch``, ``.change_orders``, ``.budget`` and
    ``.inspections`` (each item may be an object or a dict; missing pieces
    are tolerated).  Page 1 shows big KPI blocks — open/overdue tasks, open
    punch items, pending change orders with approved dollars, and a drawn
    budget spent-vs-total bar — followed by one compact table section per
    non-empty list (rendered with :func:`transmittal.table_pdf` and merged
    into this single PDF).  Atomic write.

    Returns ``{"out_path": ..., "pages": int}``.
    """
    writer = PdfWriter()
    kpi_reader = PdfReader(io.BytesIO(_snapshot_page1(project, title)))
    for page in kpi_reader.pages:
        writer.add_page(page)

    sections = _snapshot_sections(project)
    with tempfile.TemporaryDirectory(prefix="snapshot_") as tdir:
        for i, (sec_title, sec_sub, headers, rows) in enumerate(sections):
            part = os.path.join(tdir, f"section_{i:02d}.pdf")
            table_pdf(part, headers, rows, title=sec_title,
                      subtitle=sec_sub, log=lambda *a, **k: None)
            for page in PdfReader(part).pages:
                writer.add_page(page)
        pages = len(writer.pages)
        _atomic_write_writer(writer, out_path)

    log(f"  wrote {out_path} ({len(sections)} section(s), {pages} page(s))")
    return {"out_path": out_path, "pages": pages}
