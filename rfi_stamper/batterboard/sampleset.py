"""Synthetic plan-set builder: every Batterboard fixture comes from here.

No real project document ever goes in the repository (brief section 9).
:class:`Sheet` writes one PDF page from raw content-stream operators, so a
fixture can contain exactly the constructs the Sightline exists for: a
fill limited by a clip path, linework hidden under a later fill, a
revision cloud across an element, a /Rotate page.

All coordinates given to a :class:`Sheet` are VIEWER points for an
unrotated page (top-left origin, y down), the same space the readers work
in; the builder flips them into PDF space itself.

:func:`build_sample_set` (further down) assembles a whole small building:
structural plans, an architectural plan, general notes, a wall schedule,
a CAD background, a shop drawing and an RFI response.  The tests use it,
and the Batterboard tab offers it as a sample set to try the tool on.
"""
from __future__ import annotations

import math

import fitz

_K = 0.5522847498307936          # cubic Bezier circle constant


def _n(v: float) -> str:
    s = f"{float(v):.4f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


class Sheet:
    """One page, drawn with raw operators, in viewer coordinates."""

    def __init__(self, w: float = 792.0, h: float = 612.0):
        self.w, self.h = float(w), float(h)
        self.ops: list = []
        self.texts: list = []        # (x, y, text, size, rgb, rotate)

    # -- helpers ----------------------------------------------------------- #
    def _p(self, pt) -> str:
        return f"{_n(pt[0])} {_n(self.h - pt[1])}"

    def raw(self, op: str) -> "Sheet":
        self.ops.append(op)
        return self

    def _path(self, pts, closed=True) -> str:
        s = f"{self._p(pts[0])} m " + " ".join(f"{self._p(p)} l" for p in pts[1:])
        return s + (" h" if closed else "")

    @staticmethod
    def _rg(color, stroke=False) -> str:
        if isinstance(color, (int, float)):
            return f"{_n(color)} {'G' if stroke else 'g'}"
        r, g, b = color
        return f"{_n(r)} {_n(g)} {_n(b)} {'RG' if stroke else 'rg'}"

    # -- fills --------------------------------------------------------------- #
    def fill_poly(self, pts, color=0.75) -> "Sheet":
        return self.raw(f"{self._rg(color)} {self._path(pts)} f")

    def fill_rect(self, x0, y0, x1, y1, color=0.75) -> "Sheet":
        return self.fill_poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], color)

    def _circle_path(self, c, r) -> str:
        cx, cy = c[0], self.h - c[1]
        k = r * _K
        return (f"{_n(cx + r)} {_n(cy)} m "
                f"{_n(cx + r)} {_n(cy + k)} {_n(cx + k)} {_n(cy + r)} {_n(cx)} {_n(cy + r)} c "
                f"{_n(cx - k)} {_n(cy + r)} {_n(cx - r)} {_n(cy + k)} {_n(cx - r)} {_n(cy)} c "
                f"{_n(cx - r)} {_n(cy - k)} {_n(cx - k)} {_n(cy - r)} {_n(cx)} {_n(cy - r)} c "
                f"{_n(cx + k)} {_n(cy - r)} {_n(cx + r)} {_n(cy - k)} {_n(cx + r)} {_n(cy)} c h")

    def fill_circle(self, c, r, color=0.75) -> "Sheet":
        return self.raw(f"{self._rg(color)} {self._circle_path(c, r)} f")

    # -- strokes ------------------------------------------------------------- #
    def _style(self, width, color, dash) -> str:
        d = "[] 0 d" if not dash else f"[{' '.join(_n(v) for v in dash)}] 0 d"
        return f"{_n(width)} w {self._rg(color, stroke=True)} {d}"

    def line(self, a, b, width=0.5, color=0, dash=None) -> "Sheet":
        return self.raw(f"{self._style(width, color, dash)} "
                        f"{self._p(a)} m {self._p(b)} l S")

    def polyline(self, pts, width=0.5, color=0, closed=False, dash=None) -> "Sheet":
        return self.raw(f"{self._style(width, color, dash)} "
                        f"{self._path(pts, closed)} S")

    def rect(self, x0, y0, x1, y1, width=0.5, color=0, dash=None) -> "Sheet":
        return self.polyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], width,
                             color, closed=True, dash=dash)

    def circle(self, c, r, width=0.5, color=0) -> "Sheet":
        return self.raw(f"{self._style(width, color, None)} {self._circle_path(c, r)} S")

    def arc(self, c, r, a0_deg, a1_deg, width=0.5, color=0) -> "Sheet":
        """Circular arc about ``c`` from ``a0`` to ``a1`` (degrees, measured
        counter-clockwise from +x in the viewer's y-DOWN space, i.e. the
        angle of ``(cos a, -sin a)`` on the page)."""
        return self.raw(f"{self._style(width, color, None)} {self._arc_path(c, r, a0_deg, a1_deg)} S")

    def _arc_path(self, c, r, a0_deg, a1_deg, move=True) -> str:
        cx, cy = c[0], self.h - c[1]
        n = max(1, int(math.ceil(abs(a1_deg - a0_deg) / 90.0)))
        step = math.radians(a1_deg - a0_deg) / n
        a = math.radians(a0_deg)
        out = []
        if move:
            out.append(f"{_n(cx + r * math.cos(a))} {_n(cy + r * math.sin(a))} m")
        for _ in range(n):
            b = a + step
            al = math.sin(b - a) * (math.sqrt(4 + 3 * math.tan((b - a) / 2) ** 2) - 1) / 3
            x1 = cx + r * (math.cos(a) - al * math.sin(a))
            y1 = cy + r * (math.sin(a) + al * math.cos(a))
            x2 = cx + r * (math.cos(b) + al * math.sin(b))
            y2 = cy + r * (math.sin(b) - al * math.cos(b))
            out.append(f"{_n(x1)} {_n(y1)} {_n(x2)} {_n(y2)} "
                       f"{_n(cx + r * math.cos(b))} {_n(cy + r * math.sin(b))} c")
            a = b
        return " ".join(out)

    def cloud(self, pts, bump=9.0, width=0.9, color=(0.8, 0.0, 0.0), closed=True) -> "Sheet":
        """A revision cloud: scallop arcs chained along a polyline."""
        path = []
        seq = list(pts) + ([pts[0]] if closed else [])
        first = True
        for a, b in zip(seq, seq[1:]):
            ln = math.hypot(b[0] - a[0], b[1] - a[1])
            n = max(1, int(round(ln / bump)))
            ux, uy = (b[0] - a[0]) / ln, (b[1] - a[1]) / ln
            ang = math.degrees(math.atan2(-uy, ux))      # PDF-space direction
            for i in range(n):
                m = (a[0] + ux * ln * (i + 0.5) / n, a[1] + uy * ln * (i + 0.5) / n)
                path.append(self._arc_path(m, ln / n / 2.0, ang + 180.0, ang, move=first))
                first = False
        return self.raw(f"{self._style(width, color, None)} {' '.join(path)} S")

    # -- clipping ------------------------------------------------------------ #
    def clip_begin(self, pts) -> "Sheet":
        return self.raw(f"q {self._path(pts)} W n")

    def clip_end(self) -> "Sheet":
        return self.raw("Q")

    # -- text ---------------------------------------------------------------- #
    def text(self, x, y, s, size=8.0, color=(0, 0, 0), rotate=0, bold=False) -> "Sheet":
        """Text with its BASELINE-left at (x, y)."""
        self.texts.append((float(x), float(y), str(s), float(size), color, rotate, bold))
        return self

    def text_c(self, x, y, s, size=8.0, color=(0, 0, 0), bold=False) -> "Sheet":
        """Text centered on x (baseline at y)."""
        w = fitz.get_text_length(str(s), "hebo" if bold else "helv", size)
        return self.text(x - w / 2.0, y, s, size, color, 0, bold)

    # -- output -------------------------------------------------------------- #
    def add_to(self, doc, rot: int = 0):
        """Append this sheet to an open fitz document; returns the page."""
        page = doc.new_page(width=self.w, height=self.h)
        page.insert_text((2, 2), " ", fontsize=1)        # creates the stream + font
        xref = page.get_contents()[0]
        doc.update_stream(xref, ("\n".join(self.ops) + "\n").encode("ascii"))
        for x, y, s, size, color, rotate, bold in self.texts:
            page.insert_text((x, y), s, fontsize=size, color=color, rotate=rotate,
                             fontname="hebo" if bold else "helv")
        if rot:
            page.set_rotation(rot)
        return page

    def build(self, rot: int = 0):
        """-> ``(doc, page)`` in memory."""
        doc = fitz.open()
        page = self.add_to(doc, rot)
        return doc, page

    def save(self, path: str, rot: int = 0) -> str:
        doc, _page = self.build(rot)
        _save(doc, path)
        return path


def _save(doc, path: str) -> None:
    """Save a fixture with EMPTY metadata (no tool names ride along)."""
    doc.set_metadata({})
    doc.save(path, garbage=3, deflate=True)
    doc.close()
