"""Markup data model, JSON/CSV persistence and PDF annotation writing.

GUI-free data layer: a tkinter canvas editor drives these objects, and
apply_to_pdf() writes them out as real PDF annotations with PyMuPDF.
All coordinates are page points, origin top-left, y down, in VIEWER
(rotation-aware) space; apply_to_pdf derotates as needed.
"""
from __future__ import annotations

import csv
import json
import math
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime

import fitz  # PyMuPDF

MARKUP_TYPES = ("pen", "highlighter", "line", "arrow", "rect", "ellipse", "cloud",
                "callout", "text", "image", "measure_length", "measure_polylength",
                "measure_area", "count")
STATUSES = ("none", "accepted", "rejected", "completed", "cancelled")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Style:
    color: str = "#D01414"   # stroke, hex
    fill: str = ""           # "" = no fill
    width: float = 1.5
    opacity: float = 1.0
    font_size: float = 11.0

    def to_dict(self) -> dict:
        return {"color": self.color, "fill": self.fill, "width": self.width,
                "opacity": self.opacity, "font_size": self.font_size}

    @classmethod
    def from_dict(cls, d: dict) -> "Style":
        return cls(**{k: d[k] for k in
                      ("color", "fill", "width", "opacity", "font_size") if k in d})


@dataclass
class Markup:
    id: str
    page: int                # 1-based
    type: str
    points: list             # [(x, y), ...] page pt coords, top-left origin, y down
    text: str = ""
    subject: str = ""
    comment: str = ""
    author: str = ""
    status: str = "none"
    status_history: list = field(default_factory=list)   # [(status, iso_timestamp)]
    style: Style = field(default_factory=Style)
    caption_template: str = ""   # e.g. "{subject}: {value}"
    measure_value: float = 0.0
    measure_unit: str = ""
    image_path: str = ""
    created: str = ""

    @classmethod
    def new(cls, page: int, type: str, points, **kw) -> "Markup":
        if type not in MARKUP_TYPES:
            raise ValueError(f"unknown markup type: {type}")
        return cls(id=uuid.uuid4().hex, page=page, type=type,
                   points=[(float(x), float(y)) for x, y in points],
                   created=_now(), **kw)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "page": self.page, "type": self.type,
            "points": [[x, y] for x, y in self.points],
            "text": self.text, "subject": self.subject, "comment": self.comment,
            "author": self.author, "status": self.status,
            "status_history": [[s, t] for s, t in self.status_history],
            "style": self.style.to_dict(),
            "caption_template": self.caption_template,
            "measure_value": self.measure_value, "measure_unit": self.measure_unit,
            "image_path": self.image_path, "created": self.created,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Markup":
        d = dict(d)
        d["points"] = [(float(x), float(y)) for x, y in d.get("points", [])]
        d["status_history"] = [(s, t) for s, t in d.get("status_history", [])]
        d["style"] = Style.from_dict(d.get("style", {}))
        known = {f for f in cls.__dataclass_fields__}  # tolerate future keys
        return cls(**{k: v for k, v in d.items() if k in known})

    def bbox(self) -> tuple:
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return (min(xs), min(ys), max(xs), max(ys))

    def translated(self, dx: float, dy: float) -> "Markup":
        m = Markup.from_dict(self.to_dict())
        m.id = uuid.uuid4().hex
        m.points = [(x + dx, y + dy) for x, y in m.points]
        return m


class MarkupStore:
    """In-memory markup list with a JSON sidecar next to the PDF."""

    def __init__(self, pdf_path: str | None = None):
        self.pdf_path = pdf_path
        self.markups: list[Markup] = []
        if pdf_path and os.path.exists(self.sidecar_path(pdf_path)):
            self.load()

    @staticmethod
    def sidecar_path(pdf_path: str) -> str:
        return pdf_path + ".markups.json"

    def add(self, m: Markup):
        self.markups.append(m)

    def remove(self, id: str):
        self.markups = [m for m in self.markups if m.id != id]

    def get(self, id: str) -> Markup | None:
        for m in self.markups:
            if m.id == id:
                return m
        return None

    def for_page(self, page: int) -> list:
        return [m for m in self.markups if m.page == page]

    def set_status(self, id: str, status: str):
        if status not in STATUSES:
            raise ValueError(f"unknown status: {status}")
        m = self.get(id)
        if m is None:
            raise KeyError(id)
        m.status = status
        m.status_history.append((status, _now()))

    def search(self, query: str) -> list:
        q = query.lower()
        return [m for m in self.markups
                if any(q in s.lower() for s in
                       (m.subject, m.comment, m.text, m.type, m.status, m.author))]

    def save(self, path: str | None = None):
        path = path or (self.pdf_path and self.sidecar_path(self.pdf_path))
        if not path:
            raise ValueError("no path to save to")
        data = {"version": 1, "markups": [m.to_dict() for m in self.markups]}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)

    def load(self, path: str | None = None):
        path = path or (self.pdf_path and self.sidecar_path(self.pdf_path))
        if not path:
            raise ValueError("no path to load from")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.markups = [Markup.from_dict(d) for d in data.get("markups", [])]

    def to_csv(self, path: str, latest_status_only: bool = True):
        from .measure import caption_for
        cols = ["page", "type", "subject", "comment", "text", "status", "author",
                "created", "measure_value", "measure_unit", "caption"]
        if not latest_status_only:
            cols.append("status_history")
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for m in self.markups:
                row = [m.page, m.type, m.subject, m.comment, m.text, m.status,
                       m.author, m.created, m.measure_value, m.measure_unit,
                       caption_for(m)]
                if not latest_status_only:
                    row.append("; ".join(f"{s}@{t}" for s, t in m.status_history))
                w.writerow(row)


def cloud_path_points(x0: float, y0: float, x1: float, y1: float,
                      r: float = 8.0) -> list:
    """Closed scalloped-rectangle outline as a dense polyline; arcs bulge OUTWARD."""
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)
    sides = (  # (start, end, outward normal), clockwise in y-down coords
        ((x0, y0), (x1, y0), (0.0, -1.0)),
        ((x1, y0), (x1, y1), (1.0, 0.0)),
        ((x1, y1), (x0, y1), (0.0, 1.0)),
        ((x0, y1), (x0, y0), (-1.0, 0.0)),
    )
    pts: list = []
    steps = 8  # samples per arc
    for (ax, ay), (bx, by), (nx, ny) in sides:
        length = math.hypot(bx - ax, by - ay)
        if length <= 1e-9:
            continue  # degenerate side (zero-width/height rect)
        n_scal = max(1, round(length / (2.0 * max(r, 1e-6))))
        for i in range(n_scal):
            sx, sy = ax + (bx - ax) * i / n_scal, ay + (by - ay) * i / n_scal
            ex, ey = ax + (bx - ax) * (i + 1) / n_scal, ay + (by - ay) * (i + 1) / n_scal
            mx, my = (sx + ex) / 2.0, (sy + ey) / 2.0
            rad = math.hypot(ex - sx, ey - sy) / 2.0
            ux, uy = (ex - sx) / (2.0 * rad), (ey - sy) / (2.0 * rad)
            for k in range(steps):
                t = k / steps
                c, s = math.cos(math.pi * t), math.sin(math.pi * t)
                pts.append((mx - rad * c * ux + rad * s * nx,
                            my - rad * c * uy + rad * s * ny))
    if not pts:                 # fully degenerate rect (a single point)
        pts = [(x0, y0)]
    pts.append(pts[0])  # close
    return pts


# ----------------------------------------------------------- PDF writing ---

def _hex_rgb(h: str):
    h = h.lstrip("#")
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return None


def _finish(annot, stroke, fill, width, opacity):
    try:
        annot.set_colors(stroke=stroke, fill=fill)
    except Exception:
        annot.set_colors(stroke=stroke)
    annot.set_border(width=width)
    annot.set_opacity(opacity)


def _label(page, mat, x, y, text, color, fontsize):
    """Small freetext label near a point (viewer coords)."""
    w = max(30.0, 0.62 * fontsize * len(text) + 8)
    rect = fitz.Rect(x, y - fontsize, x + w, y + fontsize * 0.9) * mat
    a = page.add_freetext_annot(rect, text, fontsize=fontsize, text_color=color)
    a.update()
    return a


def _freetext(page, rect, text, color, fontsize, border_width):
    # Plain (non-richtext) freetext: PyMuPDF/MuPDF renders both the text AND
    # the border in the DA color, so text and border match the stroke color.
    # (The richtext=True border_color path is unreliable in PyMuPDF 1.28: its
    # RC template's leading whitespace wraps/clips the text out of view in
    # auto-sized rects, and it ignores fontsize/text_color.)
    a = page.add_freetext_annot(rect, text, fontsize=fontsize, text_color=color)
    a.set_border(width=max(border_width, 0.5))
    a.update()
    return a


def apply_to_pdf(pdf_path: str, out_path: str, markups: list,
                 flatten: bool = False, log=print) -> dict:
    """Write markups into pdf_path as real PDF annotations, save to out_path.

    Annotation-creating APIs in PyMuPDF take UNROTATED page coordinates, while
    the model stores viewer (rotation-aware) coordinates -- so every point/rect
    is mapped through page.derotation_matrix (identity for unrotated pages).
    """
    from .measure import caption_for
    doc = fitz.open(pdf_path)
    n = 0
    for m in markups:
        page = doc[m.page - 1]
        mat = page.derotation_matrix
        P = lambda p: fitz.Point(p[0], p[1]) * mat  # noqa: E731
        stroke = _hex_rgb(m.style.color) or (0, 0, 0)
        fill = _hex_rgb(m.style.fill)
        width, opacity, fs = m.style.width, m.style.opacity, m.style.font_size
        bx0, by0, bx1, by1 = m.bbox()
        rect = fitz.Rect(bx0, by0, bx1, by1) * mat  # normalized by the multiply
        caption = caption_for(m)
        a = None

        if m.type in ("pen", "highlighter"):
            a = page.add_ink_annot([[tuple(P(p)) for p in m.points]])
            if m.type == "highlighter":
                width, opacity = width * 3.0, 0.35
            _finish(a, stroke, None, width, opacity)
        elif m.type in ("line", "arrow", "measure_length"):
            a = page.add_line_annot(P(m.points[0]), P(m.points[1]))
            if m.type == "arrow":
                a.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_CLOSED_ARROW)
            _finish(a, stroke, fill, width, opacity)
        elif m.type == "rect":
            a = page.add_rect_annot(rect)
            _finish(a, stroke, fill, width, opacity)
        elif m.type == "ellipse":
            a = page.add_circle_annot(rect)
            _finish(a, stroke, fill, width, opacity)
        elif m.type == "cloud":
            pts = cloud_path_points(bx0, by0, bx1, by1)
            a = page.add_polygon_annot([P(p) for p in pts])
            try:
                a.set_border(clouds=2, width=width)
            except Exception:
                pass  # older PyMuPDF: polygon outline already looks scalloped
            _finish(a, stroke, fill, width, opacity)
        elif m.type == "text":
            x, y = m.points[0]
            w = max(80.0, 0.62 * fs * len(m.text) + 10)
            a = _freetext(page, fitz.Rect(x, y, x + w, y + fs * 1.8) * mat,
                          m.text, stroke, fs, width)
            a.set_opacity(opacity)
        elif m.type == "callout":
            x, y = m.points[0]
            tip = m.points[1]
            knee = m.points[2] if len(m.points) > 2 else None
            w = max(80.0, 0.62 * fs * len(m.text) + 10)
            a = _freetext(page, fitz.Rect(x, y, x + w, y + fs * 1.8) * mat,
                          m.text, stroke, fs, width)
            segs = [(m.points[0], knee), (knee, tip)] if knee else [(m.points[0], tip)]
            for i, (p0, p1) in enumerate(segs):
                la = page.add_line_annot(P(p0), P(p1))
                if i == len(segs) - 1:
                    la.set_line_ends(fitz.PDF_ANNOT_LE_NONE,
                                     fitz.PDF_ANNOT_LE_CLOSED_ARROW)
                _finish(la, stroke, None, width, opacity)
                la.set_info(title=m.author, subject=m.subject,
                            content=m.comment or caption)
                la.update()
                n += 1
        elif m.type == "image":
            if m.image_path and os.path.exists(m.image_path):
                page.insert_image(rect, filename=m.image_path)
            else:
                log(f"  !! image not found for markup {m.id}: {m.image_path}")
            continue  # page content, no annot object
        elif m.type == "measure_polylength":
            a = page.add_polyline_annot([P(p) for p in m.points])
            _finish(a, stroke, None, width, opacity)
        elif m.type == "measure_area":
            a = page.add_polygon_annot([P(p) for p in m.points])
            _finish(a, stroke, fill, width, opacity)
        elif m.type == "count":
            x, y = m.points[0]
            a = page.add_circle_annot(fitz.Rect(x - 6, y - 6, x + 6, y + 6) * mat)
            _finish(a, stroke, stroke, width, opacity)  # filled dot
            if m.text:
                la = _label(page, mat, x + 8, y, m.text, stroke, fs)
                la.set_info(title=m.author, subject=m.subject, content=m.text)
                la.update()
                n += 1
        else:
            log(f"  !! unsupported markup type skipped: {m.type}")
            continue

        if a is not None:
            if m.type in ("text", "callout"):
                # A FreeText annot DISPLAYS its /Contents entry: overwriting it
                # with the comment would clobber the visible text on update().
                a.set_info(title=m.author, subject=m.subject)
            else:
                a.set_info(title=m.author, subject=m.subject,
                           content=m.comment or caption)
            a.update()
            n += 1

        if m.type in ("measure_length", "measure_polylength") and caption:
            mid = m.points[len(m.points) // 2 - 1] if len(m.points) > 2 else None
            if mid is None:
                mid = ((m.points[0][0] + m.points[-1][0]) / 2,
                       (m.points[0][1] + m.points[-1][1]) / 2)
            la = _label(page, mat, mid[0], mid[1] - 3, caption, stroke, fs)
            la.set_info(title=m.author, subject=m.subject, content=caption)
            la.update()
            n += 1
        elif m.type == "measure_area" and caption:
            cx = sum(p[0] for p in m.points) / len(m.points)
            cy = sum(p[1] for p in m.points) / len(m.points)
            la = _label(page, mat, cx, cy, caption, stroke, fs)
            la.set_info(title=m.author, subject=m.subject, content=caption)
            la.update()
            n += 1

    if flatten:
        if hasattr(doc, "bake"):
            doc.bake()
        else:
            log("  note: this PyMuPDF cannot flatten; annotations left live")
    doc.save(out_path, garbage=3, deflate=True)
    doc.close()
    return {"annots": n, "out_path": out_path}
