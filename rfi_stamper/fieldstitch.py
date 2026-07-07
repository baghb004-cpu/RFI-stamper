"""Fieldstitch: the layout-points engine — plan pixels to stakeable points.

The office user drops numbered points on the plan PDF (anchor bolts, sleeves,
hangers, control points); field crews stake them from whatever data file
their tablet ingests.  This module owns the whole path:

* :class:`LayoutPoint` / :class:`PointLayer` — the data model, persisted to a
  versioned JSON sidecar (``<plan.pdf>.stitch.json``, same convention as the
  markups and resolution sidecars) by :class:`LayoutJob`;
* page-to-world math (:meth:`LayoutJob.to_world`): survey-style Northing /
  Easting / elevation from a basepoint, a plan-north rotation, and the
  measure tool's :class:`~rfi_stamper.markups.measure.ScaleCal`;
* exporters — PNEZD CSV, a hand-rolled minimal XLSX (stdlib :mod:`zipfile`
  OOXML, no new deps), ASCII DXF R12, and the raw job JSON — plus
  :func:`export_kit` bundles matched to what a crew's tablet reads;
* a tolerant PNEZD CSV importer (:func:`import_csv`) for round-tripping
  points staked or edited in the field.

Coordinate conventions (same as the markups layer): page coordinates are
viewer page **points**, top-left origin, y **down**.  World coordinates are
Northing (+north), Easting (+east) and Z elevation in the job's real units.
Fully offline; all writes are atomic (temp file + fsync + ``os.replace``).
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import uuid
import zipfile
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from .markups.measure import ScaleCal

_VERSION = 1


def _now_iso() -> str:
    # microseconds: renumber() sorts by (page, created) and must stay stable
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _atomic_bytes(data: bytes, out_path: str) -> None:
    """Write beside out_path, fsync, then atomically replace: a killed process
    or crash can never leave a truncated file at the final path."""
    tmp = out_path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out_path)


# ------------------------------------------------------------- DXF colors --

#: Small palette of hex anchors -> classic DXF color-index integers
#: (group code 62).  Black and white share index 7 by convention.
ACI_COLORS = {
    "#ff0000": 1,      # red
    "#ffff00": 2,      # yellow
    "#00ff00": 3,      # green
    "#00ffff": 4,      # cyan
    "#0000ff": 5,      # blue
    "#ff00ff": 6,      # magenta
    "#ffffff": 7,      # white
    "#000000": 7,      # black (renders as 7 on either screen background)
    "#808080": 8,      # gray
    "#ff7f00": 30,     # orange
}


def _rgb(hex_color: str) -> tuple[int, int, int]:
    s = str(hex_color or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise ValueError(f"bad hex color {hex_color!r} (use #rrggbb)")
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        raise ValueError(f"bad hex color {hex_color!r} (use #rrggbb)") from None


_ACI_ANCHORS = [(_rgb(h), n) for h, n in ACI_COLORS.items()]


def aci_for(hex_color: str) -> int:
    """Nearest DXF color index for a hex color, by straight RGB distance to
    the :data:`ACI_COLORS` anchors."""
    r, g, b = _rgb(hex_color)
    best, best_d = 7, None
    for (ar, ag, ab), n in _ACI_ANCHORS:
        d = (r - ar) ** 2 + (g - ag) ** 2 + (b - ab) ** 2
        if best_d is None or d < best_d:
            best, best_d = n, d
    return best


# -------------------------------------------------------------- data model --

@dataclass
class PointLayer:
    """A named group of layout points; drives visibility and export color."""
    name: str
    color: str = "#d84c3f"
    visible: bool = True
    locked: bool = False
    category: str = ""       # e.g. anchor bolts / sleeves / hangers / control

    def to_dict(self) -> dict:
        return {"name": self.name, "color": self.color,
                "visible": self.visible, "locked": self.locked,
                "category": self.category}

    @classmethod
    def from_dict(cls, d: dict) -> "PointLayer":
        return cls(name=str(d.get("name", "Layout")),
                   color=str(d.get("color", "#d84c3f")),
                   visible=bool(d.get("visible", True)),
                   locked=bool(d.get("locked", False)),
                   category=str(d.get("category", "")))


@dataclass
class LayoutPoint:
    """One stakeable point, placed on a plan page in viewer points."""
    id: str                            # uuid4 hex (use LayoutPoint.new())
    num: int = 1
    prefix: str = ""
    suffix: str = ""
    page: int = 1
    x: float = 0.0                     # page pts, top-left origin, y down
    y: float = 0.0
    elev: float = 0.0                  # real units (job.units)
    desc: str = ""
    category: str = ""
    layer: str = "Layout"
    created: str = ""

    @classmethod
    def new(cls, **kw) -> "LayoutPoint":
        kw.setdefault("id", uuid.uuid4().hex)
        kw.setdefault("created", _now_iso())
        return cls(**kw)

    @property
    def label(self) -> str:
        """Un-padded composed id (``CP-1-S``); zero-padded form is
        :meth:`LayoutJob.composed`."""
        return f"{self.prefix}{self.num}{self.suffix}"

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, d: dict) -> "LayoutPoint":
        return cls(id=str(d.get("id") or uuid.uuid4().hex),
                   num=int(d.get("num", 1)),
                   prefix=str(d.get("prefix", "")),
                   suffix=str(d.get("suffix", "")),
                   page=int(d.get("page", 1)),
                   x=float(d.get("x", 0.0)), y=float(d.get("y", 0.0)),
                   elev=float(d.get("elev", 0.0)),
                   desc=str(d.get("desc", "")),
                   category=str(d.get("category", "")),
                   layer=str(d.get("layer", "Layout")),
                   created=str(d.get("created", "")))


# --------------------------------------------------------------------- job --

class LayoutJob:
    """All layout points for one plan set, persisted to a JSON sidecar.

    Construct with the plan PDF's path and the sidecar
    ``<plan>.stitch.json`` is loaded automatically if present; every mutation
    autosaves.  Construct with no path for an in-memory job (call
    :meth:`save` with an explicit path to persist).

    Georeference: ``base_page_xy`` is the page point that equals
    ``base_world`` (a survey-style false origin, ``(Northing, Easting)``);
    ``rotation_deg`` rotates plan north about the basepoint (CCW positive);
    ``scale`` is a stored :class:`ScaleCal` dict (``cal`` property)."""

    SUFFIX = ".stitch.json"

    def __init__(self, pdf_path: str | None = None):
        self.pdf_path = pdf_path
        self.path = (pdf_path + self.SUFFIX) if pdf_path else None
        self.points: list[LayoutPoint] = []
        self.layers: list[PointLayer] = []
        self.units: str = "ft"                    # "ft" | "m"
        self.next_num: int = 1
        self.pad: int = 3                         # composed() zero-pad width
        self.prefix: str = ""                     # defaults for new points
        self.suffix: str = ""
        self.base_page_xy: tuple = (0.0, 0.0)
        self.base_world: tuple = (1000.0, 1000.0)   # (N, E) at the basepoint
        self.rotation_deg: float = 0.0
        self.scale = None                         # ScaleCal dict or None
        if self.path and os.path.exists(self.path):
            self.load()

    # ------------------------------------------------------------- scale --

    @property
    def cal(self) -> ScaleCal | None:
        return ScaleCal.from_dict(self.scale) if self.scale else None

    @cal.setter
    def cal(self, value) -> None:
        if value is None:
            self.scale = None
        elif isinstance(value, ScaleCal):
            self.scale = value.to_dict()
        else:
            self.scale = dict(value)

    # ------------------------------------------------------------ points --

    def add_point(self, page, x, y, **kw) -> LayoutPoint:
        """Place a point: job prefix/suffix and the running number are
        applied (pass ``num=`` to override; ``next_num`` is bumped past it),
        the layer (default ``"Layout"``) is auto-created if missing, and the
        sidecar autosaves."""
        kw.setdefault("prefix", self.prefix)
        kw.setdefault("suffix", self.suffix)
        kw.setdefault("layer", "Layout")
        if "num" in kw:
            num = int(kw.pop("num"))
            self.next_num = max(self.next_num, num + 1)
        else:
            num = self.next_num
            self.next_num += 1
        p = LayoutPoint.new(num=num, page=int(page), x=float(x), y=float(y),
                            **kw)
        if self.layer(p.layer) is None:
            self.layers.append(PointLayer(p.layer))
        self.points.append(p)
        self._autosave()
        return p

    def get(self, id) -> LayoutPoint | None:
        for p in self.points:
            if p.id == id:
                return p
        return None

    def remove(self, id) -> bool:
        p = self.get(id)
        if p is None:
            return False
        self.points.remove(p)
        self._autosave()
        return True

    def points_on(self, page) -> list:
        return [p for p in self.points if p.page == int(page)]

    def renumber(self, start: int = 1) -> None:
        """Renumber every point, stable by (page, created) order."""
        for i, p in enumerate(sorted(self.points,
                                     key=lambda p: (p.page, p.created))):
            p.num = start + i
        self.next_num = start + len(self.points)
        self._autosave()

    def composed(self, p: LayoutPoint) -> str:
        """Point label with the number zero-padded to ``pad``: ``CP-001-S``."""
        return f"{p.prefix}{str(p.num).zfill(self.pad)}{p.suffix}"

    # ------------------------------------------------------------ layers --

    def layer(self, name) -> PointLayer | None:
        for ly in self.layers:
            if ly.name == name:
                return ly
        return None

    def add_layer(self, layer: PointLayer) -> None:
        if self.layer(layer.name) is not None:
            raise ValueError(f"layer {layer.name!r} already exists")
        self.layers.append(layer)
        self._autosave()

    def rename_layer(self, old, new) -> None:
        """Rename a layer and repoint every point on it."""
        ly = self.layer(old)
        if ly is None:
            raise ValueError(f"no layer named {old!r}")
        if new != old and self.layer(new) is not None:
            raise ValueError(f"layer {new!r} already exists")
        ly.name = new
        for p in self.points:
            if p.layer == old:
                p.layer = new
        self._autosave()

    # -------------------------------------------------------- world math --

    def to_world(self, p: LayoutPoint) -> tuple[float, float, float]:
        """Page point -> (Northing, Easting, Z) in real units.

        The page vector from the basepoint is flipped to survey axes
        (east' = +x, north' = -y since page y runs down), rotated by
        ``rotation_deg`` (CCW positive), scaled by the calibration, and
        offset by ``base_world``.  Z is the point's elevation, already in
        real units."""
        cal = self.cal
        if cal is None:
            raise ValueError(
                "no scale set: calibrate the plan (ScaleCal) and store it in "
                "job.scale before converting points to world coordinates")
        vx = float(p.x) - float(self.base_page_xy[0])
        vy = float(p.y) - float(self.base_page_xy[1])
        east, north = vx, -vy                        # y-down page -> y-up N
        th = math.radians(self.rotation_deg)
        c, s = math.cos(th), math.sin(th)
        e_rot = east * c - north * s
        n_rot = east * s + north * c
        n = float(self.base_world[0]) + n_rot * cal.real_per_pt
        e = float(self.base_world[1]) + e_rot * cal.real_per_pt
        return (n, e, float(p.elev))

    def from_world(self, n: float, e: float) -> tuple[float, float]:
        """Inverse of :meth:`to_world`: world (N, E) -> page (x, y) pts."""
        cal = self.cal
        if cal is None:
            raise ValueError(
                "no scale set: calibrate the plan (ScaleCal) and store it in "
                "job.scale before converting world coordinates to the page")
        dn = (float(n) - float(self.base_world[0])) / cal.real_per_pt
        de = (float(e) - float(self.base_world[1])) / cal.real_per_pt
        th = math.radians(self.rotation_deg)
        c, s = math.cos(th), math.sin(th)
        east = de * c + dn * s                       # rotate back by -theta
        north = -de * s + dn * c
        return (float(self.base_page_xy[0]) + east,
                float(self.base_page_xy[1]) - north)

    def bounds_world(self) -> tuple | None:
        """(min_N, min_E, max_N, max_E) over every point, or None when there
        are no points or no scale is set."""
        if not self.points or not self.scale:
            return None
        ne = [self.to_world(p)[:2] for p in self.points]
        ns = [t[0] for t in ne]
        es = [t[1] for t in ne]
        return (min(ns), min(es), max(ns), max(es))

    # ------------------------------------------------------- persistence --

    def to_dict(self) -> dict:
        return {
            "version": _VERSION,
            "units": self.units, "next_num": self.next_num, "pad": self.pad,
            "prefix": self.prefix, "suffix": self.suffix,
            "base_page_xy": list(self.base_page_xy),
            "base_world": list(self.base_world),
            "rotation_deg": self.rotation_deg,
            "scale": dict(self.scale) if self.scale else None,
            "layers": [ly.to_dict() for ly in self.layers],
            "points": [p.to_dict() for p in self.points],
        }

    def save(self, path: str | None = None) -> None:
        """Atomically write the versioned JSON sidecar."""
        path = path or self.path
        if not path:
            raise ValueError("no sidecar path; construct with pdf_path or "
                             "pass an explicit path")
        blob = json.dumps(self.to_dict(), indent=2,
                          sort_keys=True).encode("utf-8")
        _atomic_bytes(blob, path)

    def load(self, path: str | None = None) -> None:
        """Load the sidecar; malformed entries are dropped rather than
        crashing (the sidecar is user-visible and hand-editable)."""
        path = path or self.path
        if not path:
            raise ValueError("no sidecar path; construct with pdf_path or "
                             "pass an explicit path")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
        self.units = str(data.get("units", "ft"))
        self.next_num = int(data.get("next_num", 1))
        self.pad = int(data.get("pad", 3))
        self.prefix = str(data.get("prefix", ""))
        self.suffix = str(data.get("suffix", ""))
        self.base_page_xy = tuple(data.get("base_page_xy", (0.0, 0.0)))
        self.base_world = tuple(data.get("base_world", (1000.0, 1000.0)))
        self.rotation_deg = float(data.get("rotation_deg", 0.0))
        scale = data.get("scale")
        self.scale = dict(scale) if isinstance(scale, dict) else None
        layers, points = [], []
        for d in data.get("layers") or []:
            try:
                layers.append(PointLayer.from_dict(d))
            except Exception:
                continue
        for d in data.get("points") or []:
            try:
                points.append(LayoutPoint.from_dict(d))
            except Exception:
                continue
        self.layers, self.points = layers, points

    def _autosave(self) -> None:
        if self.path:
            self.save()


# --------------------------------------------------------------- exporters --

def _export_points(job: LayoutJob, points=None) -> list:
    """Default export set: every point on a visible layer (points whose layer
    is untracked count as visible).  An explicit ``points`` list bypasses the
    visibility filter entirely."""
    if points is not None:
        return list(points)
    hidden = {ly.name for ly in job.layers if not ly.visible}
    return [p for p in job.points if p.layer not in hidden]


def _pnezd_desc(p: LayoutPoint) -> str:
    return p.desc or p.category or p.layer


def export_csv_pnezd(job: LayoutJob, out_path: str, points=None,
                     header: bool = True, delimiter: str = ",") -> int:
    """PNEZD CSV: composed point id, Northing, Easting, Elevation (3
    decimals), description.  Returns the data-row count."""
    pts = _export_points(job, points)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\r\n")
    if header:
        w.writerow(["Point", "Northing", "Easting", "Elevation",
                    "Description"])
    for p in pts:
        n, e, z = job.to_world(p)
        w.writerow([job.composed(p), f"{n:.3f}", f"{e:.3f}", f"{z:.3f}",
                    _pnezd_desc(p)])
    _atomic_bytes(buf.getvalue().encode("utf-8"), out_path)
    return len(pts)


# -- XLSX (hand-rolled minimal OOXML; stdlib zipfile, no new dependencies) ---

_XLSX_HEADER = ["Point", "Prefix", "Number", "Suffix", "X (Easting)",
                "Y (Northing)", "Z (Elevation)", "Description", "Category",
                "Layer"]
_XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS_DOC_REL = ("http://schemas.openxmlformats.org/officeDocument/2006/"
               "relationships")
_NS_CTYPES = "http://schemas.openxmlformats.org/package/2006/content-types"


def _xlsx_cell(col: int, row: int, value, numeric: bool) -> str:
    ref = f"{chr(ord('A') + col)}{row}"
    if numeric:
        return f'<c r="{ref}"><v>{value}</v></c>'
    return (f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">'
            f"{escape(str(value))}</t></is></c>")


def export_xlsx(job: LayoutJob, out_path: str, points=None) -> int:
    """Minimal real XLSX workbook (one sheet, inline strings, numeric
    coordinate cells).  Returns the data-row count."""
    pts = _export_points(job, points)
    rows_xml = ["<row r=\"1\">" + "".join(
        _xlsx_cell(c, 1, h, False) for c, h in enumerate(_XLSX_HEADER))
        + "</row>"]
    for i, p in enumerate(pts, start=2):
        n, e, z = job.to_world(p)
        cells = [
            (job.composed(p), False), (p.prefix, False), (p.num, True),
            (p.suffix, False), (f"{e:.3f}", True), (f"{n:.3f}", True),
            (f"{z:.3f}", True), (p.desc, False), (p.category, False),
            (p.layer, False),
        ]
        rows_xml.append("<row r=\"%d\">%s</row>" % (
            i, "".join(_xlsx_cell(c, i, v, num)
                       for c, (v, num) in enumerate(cells))))
    sheet = (_XML_DECL
             + f'<worksheet xmlns="{_NS_MAIN}"><sheetData>'
             + "".join(rows_xml) + "</sheetData></worksheet>")
    content_types = (_XML_DECL + f'<Types xmlns="{_NS_CTYPES}">'
                     '<Default Extension="rels" ContentType="application/vnd.'
                     'openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/xl/workbook.xml" ContentType='
                     '"application/vnd.openxmlformats-officedocument.'
                     'spreadsheetml.sheet.main+xml"/>'
                     '<Override PartName="/xl/worksheets/sheet1.xml" '
                     'ContentType="application/vnd.openxmlformats-'
                     'officedocument.spreadsheetml.worksheet+xml"/></Types>')
    root_rels = (_XML_DECL + f'<Relationships xmlns="{_NS_PKG_REL}">'
                 f'<Relationship Id="rId1" Type="{_NS_DOC_REL}/officeDocument"'
                 ' Target="xl/workbook.xml"/></Relationships>')
    workbook = (_XML_DECL
                + f'<workbook xmlns="{_NS_MAIN}" xmlns:r="{_NS_DOC_REL}">'
                '<sheets><sheet name="Layout Points" sheetId="1" '
                'r:id="rId1"/></sheets></workbook>')
    wb_rels = (_XML_DECL + f'<Relationships xmlns="{_NS_PKG_REL}">'
               f'<Relationship Id="rId1" Type="{_NS_DOC_REL}/worksheet"'
               ' Target="worksheets/sheet1.xml"/></Relationships>')
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    _atomic_bytes(zbuf.getvalue(), out_path)
    return len(pts)


# -- DXF R12 -----------------------------------------------------------------

_DXF_TEXT_H = 1.5              # label height in drawing (real) units


def export_dxf(job: LayoutJob, out_path: str, points=None) -> int:
    """ASCII DXF R12: a LAYER table (color from :func:`aci_for`), one POINT
    entity per point at (Easting, Northing, Z) plus a TEXT label at a small
    offset.  Returns the entity count (POINT + TEXT, i.e. 2 per point)."""
    pts = _export_points(job, points)
    layer_colors: dict[str, int] = {
        ly.name: aci_for(ly.color) for ly in job.layers}
    for p in pts:                              # never reference a missing layer
        layer_colors.setdefault(p.layer, 7)
    pairs: list[tuple[int, str]] = [
        (0, "SECTION"), (2, "HEADER"),
        (9, "$ACADVER"), (1, "AC1009"),
        (0, "ENDSEC"),
        (0, "SECTION"), (2, "TABLES"),
        (0, "TABLE"), (2, "LAYER"), (70, str(len(layer_colors))),
    ]
    for name, color in layer_colors.items():
        pairs += [(0, "LAYER"), (2, name), (70, "0"), (62, str(color)),
                  (6, "CONTINUOUS")]
    pairs += [(0, "ENDTAB"), (0, "ENDSEC"),
              (0, "SECTION"), (2, "ENTITIES")]
    off = _DXF_TEXT_H * 0.5
    entities = 0
    for p in pts:
        n, e, z = job.to_world(p)
        pairs += [(0, "POINT"), (8, p.layer),
                  (10, f"{e:.4f}"), (20, f"{n:.4f}"), (30, f"{z:.4f}")]
        pairs += [(0, "TEXT"), (8, p.layer),
                  (10, f"{e + off:.4f}"), (20, f"{n + off:.4f}"),
                  (30, f"{z:.4f}"), (40, f"{_DXF_TEXT_H:.2f}"),
                  (1, job.composed(p))]
        entities += 2
    pairs += [(0, "ENDSEC"), (0, "EOF")]
    text = "".join(f"{code}\r\n{value}\r\n" for code, value in pairs)
    _atomic_bytes(text.encode("ascii", errors="replace"), out_path)
    return entities


def export_job_json(job: LayoutJob, out_path: str) -> int:
    """The whole job as versioned JSON (identical to the sidecar), for
    archival or hand-off.  Returns the point count."""
    job.save(out_path)
    return len(job.points)


# ---------------------------------------------------------------- importer --

_IMPORT_ALIASES = {
    "point": {"point", "points", "name", "pt", "id", "pointid", "point id",
              "number", "pnt", "no"},
    "n": {"n", "northing", "north", "y"},
    "e": {"e", "easting", "east", "x"},
    "z": {"z", "elev", "elevation", "el", "height"},
    "d": {"d", "desc", "description", "code", "note", "notes"},
}
_LABEL_SPLIT = re.compile(r"^(\D*)(\d+)(\D*)$")
_LABEL_SPLIT_LAST = re.compile(r"^(.*?)(\d+)(\D*)$")


def _canon_header(cell: str) -> str:
    return re.sub(r"\(.*?\)", "", str(cell)).strip().lower()


def _header_map(row: list) -> dict | None:
    """Column index per field if the row reads as a header, else None."""
    hits: dict[str, int] = {}
    for i, cell in enumerate(row):
        key = _canon_header(cell)
        for fieldname, aliases in _IMPORT_ALIASES.items():
            if key in aliases and fieldname not in hits:
                hits[fieldname] = i
    return hits if "n" in hits and "e" in hits else None


def _split_label(label: str) -> tuple[str, int | None, str]:
    """'CP-001-S' -> ('CP-', 1, '-S'); no digits -> (label, None, '')."""
    s = str(label).strip()
    m = _LABEL_SPLIT.match(s) or _LABEL_SPLIT_LAST.match(s)
    if not m:
        return s, None, ""
    return m.group(1), int(m.group(2)), m.group(3)


def import_csv(job: LayoutJob, path: str, log=print) -> int:
    """Tolerant PNEZD CSV reader: header detected and mapped when present
    (point/name, n/northing/y, e/easting/x, z/elev, d/desc), positional
    P-N-E-Z-D otherwise.  World coordinates are converted back to page points
    through the inverse of :meth:`LayoutJob.to_world` (a scale must be set);
    imported points land on page 1 — PNEZD files carry no page.  Rows that
    do not parse are logged and skipped.  Returns how many points were
    added."""
    if job.cal is None:
        raise ValueError(
            "no scale set: calibrate the plan (ScaleCal) and store it in "
            "job.scale before importing world coordinates")
    with open(path, encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            reader = csv.reader(f, csv.Sniffer().sniff(sample,
                                                       delimiters=",;\t"))
        except csv.Error:
            reader = csv.reader(f)          # default comma dialect
        rows = [r for r in reader if any(str(c).strip() for c in r)]
    if not rows:
        return 0
    cols = _header_map(rows[0])
    if cols is not None:
        rows = rows[1:]
    else:
        cols = {"point": 0, "n": 1, "e": 2, "z": 3, "d": 4}

    def cell(row, fieldname):
        i = cols.get(fieldname)
        return row[i].strip() if i is not None and i < len(row) else ""

    added = 0
    for lineno, row in enumerate(rows, 1):
        try:
            n = float(cell(row, "n"))
            e = float(cell(row, "e"))
        except ValueError:
            log(f"  !! row {lineno}: bad N/E {row!r}, skipped")
            continue
        try:
            z = float(cell(row, "z") or 0.0)
        except ValueError:
            z = 0.0
        prefix, num, suffix = _split_label(cell(row, "point"))
        if num is None:
            num, prefix, suffix = job.next_num, prefix or job.prefix, ""
        x, y = job.from_world(n, e)
        if job.layer("Layout") is None:
            job.layers.append(PointLayer("Layout"))
        job.points.append(LayoutPoint.new(
            num=num, prefix=prefix, suffix=suffix, page=1, x=x, y=y,
            elev=z, desc=cell(row, "d"), layer="Layout"))
        job.next_num = max(job.next_num, num + 1)
        added += 1
    if added:
        job._autosave()
    return added


# --------------------------------------------------------------- field kits --

#: Export bundles matched to what a crew's tablet ingests, keyed by rigging
#: knot (no vendor names): a simple CSV+DXF rig, an XLSX+DXF rig, and the
#: everything bundle.
KITS = {
    "bowline": ("csv", "dxf"),
    "clovehitch": ("xlsx", "dxf"),
    "fullspool": ("csv", "xlsx", "dxf", "json"),
}


def export_kit(job: LayoutJob, out_dir: str, kit: str,
               stem: str = "layout") -> dict:
    """Write every format in the kit as ``<out_dir>/<stem>.<ext>``; returns
    ``{"files": [paths], "points": n}`` (n = visible points exported)."""
    if kit not in KITS:
        raise ValueError(f"unknown kit {kit!r}; expected one of "
                         f"{sorted(KITS)}")
    os.makedirs(out_dir, exist_ok=True)
    pts = _export_points(job)
    files: list[str] = []
    for ext in KITS[kit]:
        out = os.path.join(out_dir, f"{stem}.{ext}")
        if ext == "csv":
            export_csv_pnezd(job, out, points=pts)
        elif ext == "xlsx":
            export_xlsx(job, out, points=pts)
        elif ext == "dxf":
            export_dxf(job, out, points=pts)
        else:
            export_job_json(job, out)
        files.append(out)
    return {"files": files, "points": len(pts)}
