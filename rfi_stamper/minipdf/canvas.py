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
from .colors import Color
from .document import Document

_KAPPA = 0.5522847498307936          # 4-cubic-Bézier circle constant


def _ellipse_curves(cx, cy, rx, ry):
    """Four cubic-Bézier segments approximating a full ellipse.

    Returns ``(start_point, [(c1, c2, end), ...])`` in absolute coordinates.
    """
    kx, ky = rx * _KAPPA, ry * _KAPPA
    start = (cx + rx, cy)
    segs = [
        ((cx + rx, cy + ky), (cx + kx, cy + ry), (cx, cy + ry)),
        ((cx - kx, cy + ry), (cx - rx, cy + ky), (cx - rx, cy)),
        ((cx - rx, cy - ky), (cx - kx, cy - ry), (cx, cy - ry)),
        ((cx + kx, cy - ry), (cx + rx, cy - ky), (cx + rx, cy)),
    ]
    return start, segs


def _arc_curves(cx, cy, rx, ry, start_deg, extent_deg):
    """Cubic-Bézier segments for an elliptical arc (≤90° per segment)."""
    start = math.radians(start_deg)
    total = math.radians(extent_deg)
    n = max(1, int(math.ceil(abs(extent_deg) / 90.0)))
    step = total / n
    p0 = (cx + rx * math.cos(start), cy + ry * math.sin(start))
    segs = []
    a = start
    for _ in range(n):
        b = a + step
        alpha = math.sin(b - a) * (math.sqrt(4 + 3 * math.tan((b - a) / 2) ** 2) - 1) / 3
        ca, sa, cb, sb = math.cos(a), math.sin(a), math.cos(b), math.sin(b)
        c1 = (cx + rx * (ca - alpha * sa), cy + ry * (sa + alpha * ca))
        c2 = (cx + rx * (cb + alpha * sb), cy + ry * (sb - alpha * cb))
        end = (cx + rx * cb, cy + ry * sb)
        segs.append((c1, c2, end))
        a = b
    return p0, segs


class _Path:
    """A reportlab-``beginPath``-style path recorder (used for clipping)."""

    def __init__(self):
        self.ops = []            # list of ("m"/"l"/"c"/"re"/"h", *coords)

    def rect(self, x, y, w, h):
        self.ops.append(("re", x, y, w, h))


def _as_rgb(color):
    if isinstance(color, Color):
        return color.rgb()
    # duck-type reportlab.lib.colors.Color (red/green/blue) so a module's
    # existing reportlab color constants also drive the from-scratch canvas.
    if hasattr(color, "red") and hasattr(color, "green") and hasattr(color, "blue"):
        return (color.red, color.green, color.blue)
    if hasattr(color, "rgb") and callable(color.rgb):
        return tuple(color.rgb())[:3]
    if isinstance(color, (tuple, list)) and len(color) >= 3:
        return tuple(color[:3])
    raise TypeError(f"expected a Color or (r,g,b), got {color!r}")


class Canvas:
    def __init__(self, filename, pagesize=(612, 792), **_kw):
        self._out = filename                 # path str or writable file-like
        self._pagesize = tuple(pagesize)
        self._doc = Document()
        self._page = self._doc.add_page(*self._pagesize)
        self._content = self._page.content
        self._page_pending = False           # True between showPage() and the next draw
        self._font = "Helvetica"             # reportlab's canvas default font
        self._fontsize = 12
        self._fontstack: list[tuple] = []

    @property
    def _c(self):
        """The current page's content stream — reportlab page semantics.

        ``showPage()`` only *ends* the current page; the next page materializes
        here on the first drawing call after it.  That mirrors reportlab, where
        the pervasive trailing ``showPage(); save()`` idiom must NOT leave a
        blank last page (form/report/plate producers all end that way).
        """
        if self._page_pending:
            self._page = self._doc.add_page(*self._pagesize)
            self._content = self._page.content
            self._page_pending = False
        return self._content

    @_c.setter
    def _c(self, content):
        # _MiniNumberedCanvas.save() re-targets committed pages to stamp footers.
        self._content = content
        self._page_pending = False

    # -- graphics state ----------------------------------------------------- #
    def setFillColorRGB(self, r, g, b):
        self._c.fill_rgb(r, g, b)

    def setStrokeColorRGB(self, r, g, b):
        self._c.stroke_rgb(r, g, b)

    def setFillColor(self, color):
        self._c.fill_rgb(*_as_rgb(color))

    def setStrokeColor(self, color):
        self._c.stroke_rgb(*_as_rgb(color))

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
        if not self._fontstack:            # reportlab raises here too — an
            raise ValueError("restoreState with no matching saveState")
        self._c.restore()
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

    def _emit_curves(self, p0, segs, stroke, fill, close=True):
        self._c.move_to(*p0)
        for c1, c2, end in segs:
            self._c.curve_to(c1[0], c1[1], c2[0], c2[1], end[0], end[1])
        if close:
            self._c.close()
        if stroke and fill:
            self._c.fill_stroke()
        elif fill:
            self._c.fill()
        elif stroke:
            self._c.stroke()
        else:
            self._c.end_path()

    def circle(self, x, y, r, stroke=1, fill=0):
        p0, segs = _ellipse_curves(x, y, r, r)
        self._emit_curves(p0, segs, stroke, fill)

    def ellipse(self, x1, y1, x2, y2, stroke=1, fill=0):
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        p0, segs = _ellipse_curves(cx, cy, abs(x2 - x1) / 2.0, abs(y2 - y1) / 2.0)
        self._emit_curves(p0, segs, stroke, fill)

    def arc(self, x1, y1, x2, y2, startAng=0, extent=90, stroke=1, fill=0):
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        p0, segs = _arc_curves(cx, cy, abs(x2 - x1) / 2.0, abs(y2 - y1) / 2.0,
                               startAng, extent)
        self._emit_curves(p0, segs, stroke, fill, close=False)

    def beginPath(self):
        return _Path()

    def clipPath(self, path, stroke=0, fill=0):
        for op in path.ops:                # _Path records rectangles only
            self._c.rect(op[1], op[2], op[3], op[4])
        self._c.clip()          # W n — intersect the clip region, paint nothing

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

    def showPage(self):
        """End the current page (reportlab semantics).

        The next page is created lazily by the first drawing call, so a
        trailing ``showPage()`` before ``save()`` adds no blank page — while an
        explicit second ``showPage()`` with nothing drawn between them commits a
        deliberately blank page, exactly like reportlab.
        """
        if self._fontstack:                  # reportlab raises here too — a
            raise ValueError("showPage with an unbalanced saveState "
                             "(leaked q would corrupt later pages)")
        if self._page_pending:               # showPage(); showPage() -> blank page
            _ = self._c                      # materialize the blank page
        self._page_pending = True
        self._font = "Helvetica"             # per-page state reset, like reportlab
        self._fontsize = 12

    def drawImage(self, *args, **kwargs):
        # Raster image XObjects are intentionally unsupported (MINIPDF_PLAN §6);
        # no shipped code path draws images — this guard catches future misuse.
        raise NotImplementedError("minipdf does not embed raster images")

    def save(self):
        data = self._doc.to_bytes()
        if hasattr(self._out, "write"):
            self._out.write(data)
        else:
            with open(self._out, "wb") as f:
                f.write(data)
