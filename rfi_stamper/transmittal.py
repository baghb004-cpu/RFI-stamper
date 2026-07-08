"""Transmittal / log & cover-sheet PDF generation (offline, reportlab only).

Produces clean, paginated table PDFs — an RFI log, a transmittal register, or
any generic tabular document — on US-letter portrait pages.  Each page carries
a repeating column-header row and a "Page X of Y" footer; long cell text wraps
inside its column.  The visual language matches the rest of the toolkit: a red
accent (RGB 0.84, 0.06, 0.06) on the title, rules and header row, generous
whitespace and a large title.

The module depends only on reportlab.  It never touches the network and never
imports the pipeline: the ``report`` argument to :func:`rfi_log_pdf` is treated
purely by duck typing, so there is no import cycle.
"""
from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass, field
from xml.sax.saxutils import escape as _xml_escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen import canvas as _canvas
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Table,
    TableStyle,
)

# ---------------------------------------------------------------- palette ---

#: The toolkit's signature red — used for the title, rules and header row.
ACCENT = colors.Color(0.84, 0.06, 0.06)
_INK = colors.Color(0.12, 0.12, 0.12)
_SUBTLE = colors.Color(0.34, 0.34, 0.34)
_ZEBRA = colors.Color(0.960, 0.960, 0.962)
_GRIDLINE = colors.Color(0.80, 0.80, 0.82)
_BOXLINE = colors.Color(0.70, 0.70, 0.72)

_MARGIN = 54.0                     # 0.75 in on every side
_FOOTER_Y = 40.0                   # baseline of the footer rule
USABLE_WIDTH = letter[0] - 2 * _MARGIN


@dataclass
class TableSpec:
    """A fully resolved table ready to render (mostly for internal use/tests)."""

    headers: list[str]
    rows: list[list] = field(default_factory=list)
    title: str = ""
    subtitle: str = ""
    col_widths: list[float] | None = None


# ------------------------------------------------------------- text utils ---

#: Hard cap on cell characters.  A single cell taller than one usable page
#: raises reportlab's LayoutError and aborts the whole PDF; bounding the text
#: keeps any one row paginatable.  Well above any legitimate note length.
_CELL_MAX = 2000


def _cell_text(value) -> str:
    """Escape a cell value for reportlab and keep intentional line breaks.

    Text longer than :data:`_CELL_MAX` is truncated with an ellipsis so no
    single row can grow taller than a page and crash the build.
    """
    text = "" if value is None else str(value)
    if len(text) > _CELL_MAX:
        text = text[:_CELL_MAX].rstrip() + "…"
    return _xml_escape(text).replace("\r\n", "\n").replace("\n", "<br/>")


def _rfi_sort_key(row):
    """Order rows by the numeric part of the RFI number, then lexically."""
    num = getattr(getattr(row, "record", None), "number", "") or ""
    m = re.search(r"\d+", str(num))
    return (0, int(m.group()), str(num)) if m else (1, 0, str(num))


def _auto_widths(headers: list[str], rows: list[list],
                 usable: float) -> list[float]:
    """Distribute ``usable`` points across columns weighted by content length."""
    ncol = len(headers)
    if ncol == 0:
        return []
    weights: list[float] = []
    for c in range(ncol):
        longest = len(str(headers[c]))
        for row in rows:
            if c < len(row) and row[c] is not None:
                longest = max(longest, len(str(row[c])))
        # cap so one very long column cannot starve the others
        weights.append(float(max(1, min(longest, 60))))
    total = sum(weights)
    widths = [usable * w / total for w in weights]
    # enforce a sane minimum, then rescale back down if we overshot
    min_w = min(48.0, usable / ncol)
    widths = [max(min_w, w) for w in widths]
    over = sum(widths)
    if over > usable:
        widths = [w * usable / over for w in widths]
    return widths


# ------------------------------------------------------------ atomic write ---

def _atomic_write_bytes(data: bytes, out_path: str) -> None:
    """Write ``data`` beside ``out_path``, fsync, then atomically replace so a
    crash or kill can never leave a truncated PDF at the final path."""
    tmp = out_path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out_path)


# ---------------------------------------------------------- numbered canvas ---

class _NumberedCanvas(_canvas.Canvas):
    """Canvas that defers the footer until ``save`` so it can print the total
    page count as "Page X of Y", and draws a thin red baseline rule."""

    def __init__(self, *args, footer_note: str = "",
                 count_holder: dict | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_states: list[dict] = []
        self._footer_note = footer_note
        self._count_holder = count_holder if count_holder is not None else {}

    def showPage(self):
        self._saved_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved_states)
        self._count_holder["pages"] = total
        for i, state in enumerate(self._saved_states, 1):
            self.__dict__.update(state)
            self._draw_footer(i, total)
            _canvas.Canvas.showPage(self)
        _canvas.Canvas.save(self)

    def _draw_footer(self, page_no: int, total: int) -> None:
        w, _h = self._pagesize
        self.saveState()
        self.setStrokeColor(ACCENT)
        self.setLineWidth(0.75)
        self.line(_MARGIN, _FOOTER_Y, w - _MARGIN, _FOOTER_Y)
        self.setFont("Helvetica", 8)
        self.setFillColor(_SUBTLE)
        if self._footer_note:
            self.drawString(_MARGIN, _FOOTER_Y - 12, self._footer_note)
        self.drawRightString(w - _MARGIN, _FOOTER_Y - 12,
                             f"Page {page_no} of {total}")
        self.restoreState()


# ------------------------------------------------------------ paragraph mold ---

def _styles():
    title = ParagraphStyle(
        "TransmittalTitle", fontName="Helvetica-Bold", fontSize=24,
        leading=27, textColor=ACCENT, spaceAfter=2)
    subtitle = ParagraphStyle(
        "TransmittalSubtitle", fontName="Helvetica", fontSize=11,
        leading=14, textColor=_SUBTLE, spaceAfter=4)
    header = ParagraphStyle(
        "TransmittalHeader", fontName="Helvetica-Bold", fontSize=9,
        leading=11, textColor=colors.white)
    body = ParagraphStyle(
        "TransmittalCell", fontName="Helvetica", fontSize=8.5,
        leading=11, textColor=_INK)
    return title, subtitle, header, body


# ------------------------------------------------------------------ public ---

def table_pdf(out_path: str, headers: list[str], rows: list[list],
              title: str = "", subtitle: str = "",
              col_widths: list[float] | None = None, log=print) -> dict:
    """Render a professional, auto-paginated table PDF (US-letter portrait).

    A large red title (and optional subtitle) sits above a red rule, followed
    by a zebra-striped table whose header row repeats on every page.  Every
    page carries a "Page X of Y" footer over a thin red baseline.  Cell text
    wraps within its column.

    ``col_widths`` is a list of column widths in points (their sum should be
    about the usable page width, ~504 pt); pass ``None`` to auto-size columns
    by content.  The write is atomic.

    Returns ``{"out_path": ..., "rows": len(rows), "pages": int}``.
    """
    headers = list(headers)
    rows = [list(r) for r in rows]
    ncol = len(headers)
    if ncol == 0:
        raise ValueError("headers must contain at least one column")

    title_style, subtitle_style, header_style, body_style = _styles()

    # --- assemble the table data with wrapped paragraphs -----------------
    data: list[list] = [[Paragraph(_cell_text(h), header_style) for h in headers]]
    for row in rows:
        cells = list(row)
        if len(cells) < ncol:
            cells += [""] * (ncol - len(cells))       # pad short rows
        cells = cells[:ncol]                           # trim long rows
        data.append([Paragraph(_cell_text(c), body_style) for c in cells])

    if col_widths is not None:
        widths = [float(w) for w in col_widths]
        if len(widths) != ncol:
            raise ValueError(
                f"col_widths has {len(widths)} entries, expected {ncol}")
    else:
        widths = _auto_widths(headers, rows, USABLE_WIDTH)

    style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, 0), 0.9, ACCENT),
        ("GRID", (0, 1), (-1, -1), 0.4, _GRIDLINE),
        ("BOX", (0, 0), (-1, -1), 0.7, _BOXLINE),
    ])
    for i in range(1, len(data)):
        if i % 2 == 0:                                 # zebra striping
            style.add("BACKGROUND", (0, i), (-1, i), _ZEBRA)

    table = Table(data, colWidths=widths, repeatRows=1, style=style)

    # --- flowable story --------------------------------------------------
    story: list = []
    if title:
        story.append(Paragraph(_cell_text(title), title_style))
    if subtitle:
        story.append(Paragraph(_cell_text(subtitle), subtitle_style))
    if title or subtitle:
        story.append(HRFlowable(width="100%", thickness=1.5, color=ACCENT,
                                spaceBefore=2, spaceAfter=12))
    story.append(table)

    # --- build to memory, then atomic-write ------------------------------
    holder: dict = {}
    footer_note = title.strip() or subtitle.strip()

    def _canvasmaker(*args, **kwargs):
        return _NumberedCanvas(*args, footer_note=footer_note,
                               count_holder=holder, **kwargs)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter, leftMargin=_MARGIN, rightMargin=_MARGIN,
        topMargin=_MARGIN, bottomMargin=_MARGIN + 18,
        title=(title or "Table"))
    doc.build(story, canvasmaker=_canvasmaker)

    _atomic_write_bytes(buf.getvalue(), out_path)
    pages = int(holder.get("pages", 1))
    log(f"  wrote {out_path} ({len(rows)} row(s), {pages} page(s))")
    return {"out_path": out_path, "rows": len(rows), "pages": pages}


def rfi_log_pdf(report, out_path: str, title: str = "RFI LOG",
                subtitle: str = "", log=print) -> dict:
    """Render an RFI log PDF from a pipeline ``Report`` (duck-typed).

    Columns: ``RFI``, ``Title``, ``Sheet(s)``, ``Via``, ``Answered``.  Sheets
    are the comma-joined canonical sheet names for the row's pages (via
    ``report.index.info(page).sheet``), or ``"(unmatched)"`` when the row has
    no pages.  ``Answered`` is ``"Yes"``/``"No"`` from ``record.has_answer``.
    Rows are sorted by RFI number.

    Returns :func:`table_pdf`'s result dict.
    """
    headers = ["RFI", "Title", "Sheet(s)", "Via", "Answered"]
    rows: list[list] = []
    for row in sorted(list(report.rows), key=_rfi_sort_key):
        rec = row.record
        pages = list(row.pages or [])
        if pages:
            sheets = ", ".join(report.index.info(p).sheet for p in pages)
        else:
            sheets = "(unmatched)"
        rows.append([
            rec.number,
            rec.title,
            sheets,
            row.via,
            "Yes" if rec.has_answer else "No",
        ])

    # Tuned so headers never wrap and Title gets the room; sum ~= usable width.
    col_widths = [44.0, 218.0, 116.0, 58.0, 68.0]
    return table_pdf(out_path, headers, rows, title=title, subtitle=subtitle,
                     col_widths=col_widths, log=log)
