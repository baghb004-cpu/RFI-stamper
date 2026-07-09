"""A ``reportlab.pdfgen.canvas.Canvas``-shaped façade over the mini-pdf writer.

Drop-in for the canvas surface the app actually uses, so a call site switches
engines by construction alone (``Canvas(buf, pagesize=...)``) with no other
change.  The façade is a thin translation of reportlab's *stateful* canvas onto
the *stateless* content-stream builder: colours/line-width/dash are emitted into
the graphics state as they are set (and restored automatically by ``q``/``Q``);
the current font is held here and re-emitted inside every text object, exactly
as reportlab does, and pushed/popped across ``saveState``/``restoreState``.

Coordinates match reportlab: points (1/72"), origin bottom-left, +y up.  Text
uses the current fill colour (PDF's non-stroking colour), so ``setFillColorRGB``
before ``drawString`` colours the text — the note-box convention.  ``setTitle``
is accepted and ignored: the writer emits no ``/Info`` (deterministic,
metadata-free output by policy).
"""
from __future__ import annotations

import math

from . import metrics
from .document import Document


class Canvas:
    def __init__(self, filename, pagesize=(612, 792), **_kw):
        self._out = filename                 # path str or writable file-like
        self._pagesize = tuple(pagesize)
        self._doc = Document()
        self._page = self._doc.add_page(*self._pagesize)
        self._c = self._page.content
        self._font = None
        self._fontsize = None
        self._fontstack: list[tuple] = []
        self._saved = False

    # -- graphics state ----------------------------------------------------- #
    def setFillColorRGB(self, r, g, b):
        self._c.fill_rgb(r, g, b)

    def setStrokeColorRGB(self, r, g, b):
        self._c.stroke_rgb(r, g, b)

    def setFillGray(self, gray):
        self._c.fill_gray(gray)

    def setStrokeGray(self, gray):
        self._c.stroke_gray(gray)

    def setLineWidth(self, w):
        self._c.line_width(w)

    def setDash(self, array=(), phase=0):
        if isinstance(array, (int, float)):
            array = [array]
        self._c.set_dash(list(array), phase)

    def setFont(self, name, size, leading=None):
        self._font = name
        self._fontsize = size

    def saveState(self):
        self._c.save()
        self._fontstack.append((self._font, self._fontsize))

    def restoreState(self):
        self._c.restore()
        if self._fontstack:
            self._font, self._fontsize = self._fontstack.pop()

    def translate(self, tx, ty):
        self._c.translate(tx, ty)

    def scale(self, sx, sy):
        self._c.concat(sx, 0, 0, sy, 0, 0)

    def rotate(self, deg):
        a = math.radians(deg)
        ca, sa = math.cos(a), math.sin(a)
        self._c.concat(ca, sa, -sa, ca, 0, 0)

    # -- paths -------------------------------------------------------------- #
    def rect(self, x, y, w, h, stroke=1, fill=0):
        self._c.rect(x, y, w, h)
        if stroke and fill:
            self._c.fill_stroke()
        elif fill:
            self._c.fill()
        elif stroke:
            self._c.stroke()
        else:
            self._c.end_path()

    def line(self, x0, y0, x1, y1):
        self._c.move_to(x0, y0).line_to(x1, y1).stroke()

    # -- text --------------------------------------------------------------- #
    def _need_font(self):
        if self._font is None:
            raise RuntimeError("setFont(...) must precede drawString(...)")

    def drawString(self, x, y, text):
        self._need_font()
        self._c.text(x, y, text, self._font, self._fontsize)

    def drawCentredString(self, x, y, text):
        self._need_font()
        w = self.stringWidth(text)
        self._c.text(x - w / 2.0, y, text, self._font, self._fontsize)

    # reportlab spells it both ways
    drawCenteredString = drawCentredString

    def drawRightString(self, x, y, text):
        self._need_font()
        w = self.stringWidth(text)
        self._c.text(x - w, y, text, self._font, self._fontsize)

    def stringWidth(self, text, fontName=None, fontSize=None):
        return metrics.string_width(text, fontName or self._font,
                                    fontSize if fontSize is not None else self._fontsize)

    # -- document ----------------------------------------------------------- #
    def setTitle(self, title):
        # accepted for API compatibility; intentionally not emitted (the writer
        # produces metadata-free, deterministic output).
        pass

    def setPageSize(self, size):
        self._pagesize = tuple(size)
        self._page.width, self._page.height = self._pagesize

    def showPage(self):
        self._page = self._doc.add_page(*self._pagesize)
        self._c = self._page.content
        self._font = None
        self._fontsize = None
        self._fontstack.clear()

    def getpdfdata(self) -> bytes:
        return self._doc.to_bytes()

    def save(self):
        data = self._doc.to_bytes()
        if hasattr(self._out, "write"):
            self._out.write(data)
        else:
            with open(self._out, "wb") as f:
                f.write(data)
