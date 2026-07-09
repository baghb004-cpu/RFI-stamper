"""A from-scratch flow/table layout engine — the drop-in for the slice of
``reportlab.platypus`` the app's table/report PDFs use.

Provides ``ParagraphStyle``, ``Paragraph`` (word-wrap + ``<br/>`` + XML entities),
``Spacer``, ``HRFlowable``, ``TableStyle``, ``Table`` (auto row heights from
wrapped cells, zebra/grid/box/line styling, header-repeating pagination) and a
``SimpleDocTemplate`` that frames the story into pages and calls a per-page
footer hook with the final page count (the "Page X of Y" the app wants) — no
reportlab-internal snapshot trickery, because this template knows the total
page count from its own layout pass.

Layout math is PDF-native (points, y-up); widths come from :mod:`metrics` so
wrapping matches the rest of the app.  This is a layout engine of its own, so
its output is clean and correct but NOT pixel-identical to platypus (report
PDFs are not verify.py-gated).
"""
from __future__ import annotations

import re

from . import metrics
from .canvas import Canvas
from .colors import Color, black, white

TA_LEFT, TA_CENTER, TA_RIGHT = 0, 1, 2


# --------------------------------------------------------------------------- #
#  Paragraph                                                                   #
# --------------------------------------------------------------------------- #

class ParagraphStyle:
    def __init__(self, name="", fontName="Helvetica", fontSize=10, leading=12,
                 textColor=black, alignment=TA_LEFT, spaceBefore=0, spaceAfter=0,
                 **_kw):
        self.name = name
        self.fontName = fontName
        self.fontSize = fontSize
        self.leading = leading
        self.textColor = textColor
        self.alignment = alignment
        self.spaceBefore = spaceBefore
        self.spaceAfter = spaceAfter


_BR = re.compile(r"<br\s*/?>", re.I)
_TAG = re.compile(r"<[^>]+>")
_ENT = [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
        ("&#39;", "'"), ("&apos;", "'")]


def _plain_lines(text):
    """Split intentional ``<br/>`` breaks, strip other tags, unescape entities."""
    out = []
    for part in _BR.split(str(text)):
        part = _TAG.sub("", part)
        for a, b in _ENT:
            part = part.replace(a, b)
        out.append(part)
    return out


def wrap_text(text, font, size, width):
    """Greedy first-fit word wrap; returns a list of lines that fit ``width``."""
    lines, line = [], ""
    for word in text.split():
        trial = (line + " " + word).strip()
        if metrics.string_width(trial, font, size) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


class Flowable:
    spaceBefore = 0
    spaceAfter = 0

    def wrap(self, availWidth, availHeight):
        return (0, 0)

    def drawOn(self, canvas, x, y):
        pass

    def split(self, availWidth, availHeight):
        return [self]


class Paragraph(Flowable):
    def __init__(self, text, style):
        self.style = style
        self._hard = _plain_lines(text)
        self.lines = []
        self.width = 0.0
        self.height = 0.0
        self.spaceBefore = style.spaceBefore
        self.spaceAfter = style.spaceAfter

    def wrap(self, availWidth, availHeight=0):
        st = self.style
        self.lines = []
        for hard in self._hard:
            wl = wrap_text(hard, st.fontName, st.fontSize, availWidth)
            self.lines.extend(wl if wl else [""])
        self.width = availWidth
        self.height = len(self.lines) * st.leading
        return (availWidth, self.height)

    def drawOn(self, canvas, x, y):
        st = self.style
        canvas.setFont(st.fontName, st.fontSize)
        canvas.setFillColor(st.textColor)
        top = y + self.height
        for i, ln in enumerate(self.lines):
            by = top - st.fontSize - i * st.leading      # baseline of line i
            if st.alignment == TA_CENTER:
                canvas.drawCentredString(x + self.width / 2.0, by, ln)
            elif st.alignment == TA_RIGHT:
                canvas.drawRightString(x + self.width, by, ln)
            else:
                canvas.drawString(x, by, ln)


class Spacer(Flowable):
    def __init__(self, width, height):
        self._w, self._h = width, height

    def wrap(self, availWidth, availHeight=0):
        return (self._w, self._h)


class HRFlowable(Flowable):
    def __init__(self, width="100%", thickness=1, color=black,
                 spaceBefore=0, spaceAfter=0, **_kw):
        self._width = width
        self.thickness = thickness
        self.color = color
        self.spaceBefore = spaceBefore
        self.spaceAfter = spaceAfter

    def wrap(self, availWidth, availHeight=0):
        if isinstance(self._width, str) and self._width.endswith("%"):
            self.width = availWidth * float(self._width[:-1]) / 100.0
        else:
            self.width = float(self._width)
        self.height = self.thickness
        return (availWidth, self.height)

    def drawOn(self, canvas, x, y):
        canvas.setStrokeColor(self.color)
        canvas.setLineWidth(self.thickness)
        yc = y + self.thickness / 2.0
        canvas.line(x, yc, x + self.width, yc)


# --------------------------------------------------------------------------- #
#  Table                                                                       #
# --------------------------------------------------------------------------- #

_DEFAULT_PAD = {"LEFT": 6.0, "RIGHT": 6.0, "TOP": 3.0, "BOTTOM": 3.0}


class TableStyle:
    def __init__(self, cmds=None):
        self._cmds = [tuple(c) for c in (cmds or [])]

    def add(self, *cmd):
        self._cmds.append(tuple(cmd))

    def getCommands(self):
        return self._cmds


def _norm(idx, n):
    """Resolve a possibly-negative row/col index against a count of n."""
    return idx if idx >= 0 else n + idx


def _spans(c0, r0, c1, r1, ncols, nrows):
    c0, c1 = _norm(c0, ncols), _norm(c1, ncols)
    r0, r1 = _norm(r0, nrows), _norm(r1, nrows)
    return range(min(c0, c1), max(c0, c1) + 1), range(min(r0, r1), max(r0, r1) + 1)


class Table(Flowable):
    def __init__(self, data, colWidths=None, rowHeights=None, repeatRows=0,
                 style=None):
        self.data = [list(r) for r in data]
        self.nrows = len(self.data)
        self.ncols = len(self.data[0]) if self.data else 0
        self.colWidths = list(colWidths) if colWidths else None
        self._rowHeights = list(rowHeights) if rowHeights else None
        self.repeatRows = repeatRows
        self.style = style if isinstance(style, TableStyle) else TableStyle(style)
        self.width = 0.0
        self.height = 0.0

    def setStyle(self, style):
        """Merge additional style commands (reportlab.Table.setStyle-compatible)."""
        cmds = style.getCommands() if isinstance(style, TableStyle) else list(style)
        self.style = TableStyle(self.style.getCommands() + cmds)

    # -- per-cell style resolution ----------------------------------------- #
    def _cell_pad(self, r, c):
        pad = dict(_DEFAULT_PAD)
        for cmd in self.style.getCommands():
            name = cmd[0].upper()
            if name in ("LEFTPADDING", "RIGHTPADDING", "TOPPADDING", "BOTTOMPADDING"):
                cols, rows = _spans(cmd[1][0], cmd[1][1], cmd[2][0], cmd[2][1],
                                    self.ncols, self.nrows)
                if c in cols and r in rows:
                    pad[name[:-7]] = float(cmd[3])
        return pad

    def _cell_bg(self, r, c):
        color = None
        for cmd in self.style.getCommands():
            if cmd[0].upper() == "BACKGROUND":
                cols, rows = _spans(cmd[1][0], cmd[1][1], cmd[2][0], cmd[2][1],
                                    self.ncols, self.nrows)
                if c in cols and r in rows:
                    color = cmd[3]
        return color

    def _cell_valign(self, r, c):
        va = "TOP"
        for cmd in self.style.getCommands():
            if cmd[0].upper() == "VALIGN":
                cols, rows = _spans(cmd[1][0], cmd[1][1], cmd[2][0], cmd[2][1],
                                    self.ncols, self.nrows)
                if c in cols and r in rows:
                    va = str(cmd[3]).upper()
        return va

    def _as_para(self, cell):
        return cell if isinstance(cell, Flowable) else Paragraph(
            "" if cell is None else str(cell),
            ParagraphStyle("_cell", fontName="Helvetica", fontSize=10, leading=12))

    def _content_height(self, r, c):
        pad = self._cell_pad(r, c)
        para = self._as_para(self.data[r][c])
        w = self.colWidths[c] - pad["LEFT"] - pad["RIGHT"]
        _, h = para.wrap(max(1.0, w))
        return h, pad, para

    def _compute(self):
        if self.colWidths is None:                     # equal split fallback
            self.colWidths = [self.width / max(1, self.ncols)] * self.ncols
        heights = []
        for r in range(self.nrows):
            hmax = 0.0
            for c in range(self.ncols):
                h, pad, _ = self._content_height(r, c)
                hmax = max(hmax, h + pad["TOP"] + pad["BOTTOM"])
            heights.append(hmax)
        self._rowHeights = heights

    def wrap(self, availWidth, availHeight=0):
        if self.colWidths is None:
            self.width = availWidth
        else:
            self.width = sum(self.colWidths)
        self._compute()
        self.height = sum(self._rowHeights)
        return (self.width, self.height)

    # -- pagination -------------------------------------------------------- #
    def split(self, availWidth, availHeight):
        if self._rowHeights is None:
            self.wrap(availWidth, availHeight)
        head = self.repeatRows
        # rows that fit after the repeated header
        used = sum(self._rowHeights[:head])
        cut = head
        for r in range(head, self.nrows):
            if used + self._rowHeights[r] > availHeight and cut > head:
                break
            used += self._rowHeights[r]
            cut = r + 1
        if cut >= self.nrows:
            return [self]
        if cut <= head:                                # not even one body row fits
            cut = head + 1                             # force progress (overflow)
        keep = list(range(head)) + list(range(cut, self.nrows))  # remainder rows
        first_rows = list(range(cut))
        return [self._subtable(first_rows), self._subtable(keep)]

    def _subtable(self, rows):
        remap = {old: new for new, old in enumerate(rows)}
        data = [self.data[r] for r in rows]
        cmds = []
        for cmd in self.style.getCommands():
            name = cmd[0]
            (c0, r0), (c1, r1) = cmd[1], cmd[2]
            rr0, rr1 = _norm(r0, self.nrows), _norm(r1, self.nrows)
            lo, hi = min(rr0, rr1), max(rr0, rr1)
            kept = [remap[r] for r in rows if lo <= r <= hi]
            if not kept:
                continue
            cmds.append((name, (c0, min(kept)), (c1, max(kept))) + tuple(cmd[3:]))
        t = Table(data, colWidths=self.colWidths,
                  rowHeights=[self._rowHeights[r] for r in rows],
                  repeatRows=self.repeatRows, style=TableStyle(cmds))
        return t

    # -- drawing ----------------------------------------------------------- #
    def drawOn(self, canvas, x, y):
        if self._rowHeights is None:
            self.wrap(self.width or sum(self.colWidths))
        top = y + self.height
        # column left edges
        xl = [x]
        for w in self.colWidths:
            xl.append(xl[-1] + w)
        # row top edges
        yt = [top]
        for h in self._rowHeights:
            yt.append(yt[-1] - h)

        # 1) cell backgrounds
        for r in range(self.nrows):
            for c in range(self.ncols):
                bg = self._cell_bg(r, c)
                if isinstance(bg, Color):
                    canvas.setFillColor(bg)
                    canvas.rect(xl[c], yt[r + 1], self.colWidths[c],
                                self._rowHeights[r], stroke=0, fill=1)

        # 2) cell content
        for r in range(self.nrows):
            for c in range(self.ncols):
                h, pad, para = self._content_height(r, c)
                va = self._cell_valign(r, c)
                cell_top, cell_bot = yt[r], yt[r + 1]
                inner_top = cell_top - pad["TOP"]
                inner_bot = cell_bot + pad["BOTTOM"]
                if va == "MIDDLE":
                    cy = inner_bot + ((inner_top - inner_bot) - h) / 2.0
                elif va == "BOTTOM":
                    cy = inner_bot
                else:                                   # TOP
                    cy = inner_top - h
                para.drawOn(canvas, xl[c] + pad["LEFT"], cy)

        # 3) lines (grid / box / linebelow / lineabove / inner)
        self._draw_lines(canvas, xl, yt)

    def _draw_lines(self, canvas, xl, yt):
        for cmd in self.style.getCommands():
            name = cmd[0].upper()
            if name not in ("GRID", "BOX", "OUTLINE", "INNERGRID", "LINEBELOW",
                            "LINEABOVE", "LINEBEFORE", "LINEAFTER"):
                continue
            cols, rows = _spans(cmd[1][0], cmd[1][1], cmd[2][0], cmd[2][1],
                                self.ncols, self.nrows)
            width = float(cmd[3])
            color = cmd[4] if len(cmd) > 4 else black
            canvas.setStrokeColor(color if isinstance(color, Color) else black)
            canvas.setLineWidth(width)
            c0, c1 = min(cols), max(cols)
            r0, r1 = min(rows), max(rows)
            # clamp to real bounds: a line command whose range runs past the
            # rows/cols that exist (e.g. GRID (0,1)-(-1,-1) on a header-only,
            # empty-body table) must draw within the grid, never index past it.
            c0, c1 = max(0, min(c0, self.ncols - 1)), max(0, min(c1, self.ncols - 1))
            r0, r1 = max(0, min(r0, self.nrows - 1)), max(0, min(r1, self.nrows - 1))
            left, right = xl[c0], xl[c1 + 1]
            hi, lo = yt[r0], yt[r1 + 1]
            if name in ("GRID", "INNERGRID"):
                for c in range(c0, c1 + 2):
                    canvas.line(xl[c], lo, xl[c], hi)
                for r in range(r0, r1 + 2):
                    canvas.line(left, yt[r], right, yt[r])
            elif name in ("BOX", "OUTLINE"):
                canvas.rect(left, lo, right - left, hi - lo, stroke=1, fill=0)
            elif name == "LINEBELOW":
                canvas.line(left, yt[r1 + 1], right, yt[r1 + 1])
            elif name == "LINEABOVE":
                canvas.line(left, yt[r0], right, yt[r0])
            elif name == "LINEBEFORE":
                canvas.line(xl[c0], lo, xl[c0], hi)
            elif name == "LINEAFTER":
                canvas.line(xl[c1 + 1], lo, xl[c1 + 1], hi)


# --------------------------------------------------------------------------- #
#  SimpleDocTemplate                                                          #
# --------------------------------------------------------------------------- #

class SimpleDocTemplate:
    def __init__(self, buf, pagesize=(612.0, 792.0), leftMargin=72.0,
                 rightMargin=72.0, topMargin=72.0, bottomMargin=72.0,
                 title=None, **_kw):
        self.buf = buf
        self.pagesize = tuple(pagesize)
        self.leftMargin = leftMargin
        self.rightMargin = rightMargin
        self.topMargin = topMargin
        self.bottomMargin = bottomMargin

    def build(self, story, canvasmaker=None, footer=None, onPage=None):
        pw, ph = self.pagesize
        fx = self.leftMargin
        fw = pw - self.leftMargin - self.rightMargin
        ftop = ph - self.topMargin
        fbot = self.bottomMargin

        # PASS 1 — lay the story out into pages (list of (flowable, x, y_ll))
        pages, cur = [], []
        y = ftop
        queue = list(story)
        prev_after = 0.0
        while queue:
            fl = queue.pop(0)
            sb = max(getattr(fl, "spaceBefore", 0) or 0, 0)
            if cur:
                y -= max(prev_after, sb)
            avail = y - fbot
            w, h = fl.wrap(fw, avail)
            if h > avail and hasattr(fl, "split") and not isinstance(fl, (Paragraph, Spacer, HRFlowable)):
                parts = fl.split(fw, avail)
                if len(parts) > 1:
                    first = parts[0]
                    fw_, fh = first.wrap(fw, avail)
                    cur.append((first, fx, y - fh))
                    pages.append(cur)
                    cur, y, prev_after = [], ftop, 0.0
                    queue[0:0] = parts[1:]
                    continue
            if h > avail and cur:                       # start a fresh page
                pages.append(cur)
                cur, y = [], ftop
                avail = y - fbot
                w, h = fl.wrap(fw, avail)
            cur.append((fl, fx, y - h))
            y -= h
            prev_after = max(getattr(fl, "spaceAfter", 0) or 0, 0)
        if cur or not pages:
            pages.append(cur)
        total = len(pages)

        # PASS 2 — emit, drawing the footer with the known total per page
        maker = canvasmaker or Canvas
        canvas = maker(self.buf, pagesize=self.pagesize)
        for pi, pg in enumerate(pages):
            for fl, x, yy in pg:
                fl.drawOn(canvas, x, yy)
            if onPage:
                onPage(canvas, pi + 1)
            if footer:
                footer(canvas, pi + 1, total)
            if pi < total - 1:
                canvas.showPage()
        canvas.save()
        return total
