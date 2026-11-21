"""The Batterboard data model: the contract every stage builds against.

Frames
------
* **page space**: viewer page points of a source PDF page (top-left origin,
  y DOWN) -- the Fieldstitch / markups convention.  ``get_drawings()`` and
  ``get_text("words")`` coordinates are pushed through
  ``page.rotation_matrix`` before they become page-space values.
* **world space**: decimal feet, ``(e, n)`` = (Easting, Northing), y UP --
  the Harvest / Loft / Fieldstitch plan frame.  Which world frame a run
  uses is DECLARED (:class:`Frame.kind`): the CAD background's own origin
  and units when one exists, else a grid-based local frame; real-world
  coordinates only when the user supplies control with coordinates.

Thresholds
----------
Every threshold in the owner's brief lives here, once.  Never weaken one
to make a test pass (brief section 14).

Fully offline, stdlib only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

IN = 1.0 / 12.0                       # one inch in decimal feet

#: structure vs CAD background: match / flag bands (brief section 6)
MATCH_FT = 0.25 * IN                  # within 1/4 in. = match
FLAG_FT = 0.50 * IN                   # over 1/2 in. = flag
#: grid spacing vs printed grid dimensions / CAD: over this blocks the level
GRID_BLOCK_FT = 0.25 * IN
#: architect dimension strings: an end matches a structural line within..
ARCH_END_FT = 1.5 * IN
#: ..and a string is "same" when it agrees with the structure within
ARCH_SAME_FT = 0.5 * IN

#: point layers (Selvage layer-name rules: A-Z 0-9 $ - _)
LAYER_GRID = "BB-GRID"
LAYER_PRE = "BB-BEFORE-POUR"
LAYER_POST = "BB-AFTER-POUR"
LAYER_DIM = "BB-NEEDS-DIM"
LAYER_HOLD = "BB-HOLD"
POINT_LAYERS = (LAYER_GRID, LAYER_PRE, LAYER_POST, LAYER_DIM, LAYER_HOLD)

#: element kinds
WALL, COLUMN, ROUND_COLUMN, CONCRETE = "wall", "column", "round_column", "concrete"
WALL_OPENING, SLAB_EDGE, SLAB_OPENING = "wall_opening", "slab_edge", "slab_opening"
SW_END, PENETRATION, OVERLAY_OPENING, MASONRY = (
    "sw_end", "penetration", "overlay_opening", "masonry")
KIND_CODE = {WALL: "W", COLUMN: "C", ROUND_COLUMN: "RC", CONCRETE: "X",
             WALL_OPENING: "WO", SLAB_EDGE: "SE", SLAB_OPENING: "SO",
             SW_END: "SWE", PENETRATION: "PEN", OVERLAY_OPENING: "OVL",
             MASONRY: "M"}
KIND_NOUN = {WALL: ("wall", "walls"),
             COLUMN: ("rectangular column", "rectangular columns"),
             ROUND_COLUMN: ("round column", "round columns"),
             CONCRETE: ("concrete shape", "concrete shapes"),
             WALL_OPENING: ("wall opening", "wall openings"),
             SLAB_EDGE: ("slab edge run", "slab edge runs"),
             SLAB_OPENING: ("slab opening", "slab openings"),
             SW_END: ("shear wall end", "shear wall ends"),
             PENETRATION: ("noted penetration", "noted penetrations"),
             OVERLAY_OPENING: ("overlay opening", "overlay openings"),
             MASONRY: ("masonry wall", "masonry walls")}

#: source tags an element can carry (``Element.sources``)
SRC_STRUCT, SRC_CAD, SRC_ARCH, SRC_OVERLAY, SRC_RFI = "S", "CAD", "A", "OVL", "RFI"
SOURCE_NAME = {SRC_STRUCT: "structural plan", SRC_CAD: "CAD background",
               SRC_ARCH: "architectural plan", SRC_OVERLAY: "shop drawing overlay",
               SRC_RFI: "answered RFI"}

#: extraction methods (brief section 5.9)
VECTOR, TRACED, OCR, CAD = "vector", "traced", "OCR", "CAD"

#: page classes (brief section 5.1)
PG_STRUCT, PG_ARCH, PG_NOTES, PG_SCHEDULE = "structural plan", "architectural plan", "general notes", "wall schedule"
PG_SHOP, PG_RFI, PG_CAD, PG_UNKNOWN = "shop drawing", "RFI response", "CAD background", "unknown"

#: scope the brief says is NOT built this round; the report must say so
NOT_LAID_OUT = ("elevations", "slab steps and depressions", "curbs and pads",
                "embeds and hold-downs")

#: stage results
PASS, FLAG, BLOCK = "pass", "flag", "block"
STAGES = ("Intake", "Sightline", "Read", "Cross-check", "Register",
          "Points", "Plates", "Self-check", "Deliver")


def fmt_ftin(ft: float, denom: int = 16) -> str:
    """Decimal feet -> ``12'-4 1/2"`` (nearest 1/``denom`` in)."""
    neg = ft < 0
    total = round(abs(float(ft)) * 12.0 * denom)
    feet, rem = divmod(total, 12 * denom)
    inch, frac = divmod(rem, denom)
    s = f"{feet}'-{inch}"
    if frac:
        g = math.gcd(frac, denom)
        s += f" {frac // g}/{denom // g}"
    return ("-" if neg else "") + s + '"'


def fmt_in(ft: float, denom: int = 16) -> str:
    """Decimal feet -> inches text ``1 1/2 in.`` (nearest 1/``denom`` in)."""
    total = round(abs(float(ft)) * 12.0 * denom)
    inch, frac = divmod(total, denom)
    if frac:
        g = math.gcd(frac, denom)
        body = (f"{inch} " if inch else "") + f"{frac // g}/{denom // g}"
    else:
        body = f"{inch}"
    return ("-" if ft < 0 else "") + body + " in."


# ------------------------------------------------------------- provenance --

@dataclass
class Provenance:
    """Where an element came from (brief section 5.9); rides every output."""
    file: str = ""            # source file name (basename)
    page: int = 0             # 1-based page; 0 for non-paged sources
    sheet: str = ""           # sheet number from the title block
    revision: str = ""        # revision text from the title block ("" = none read)
    method: str = VECTOR      # VECTOR | TRACED | OCR | CAD
    confidence: float = 1.0   # 0..1
    note: str = ""            # e.g. "from the CAD background"

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "Provenance":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})

    def cite(self) -> str:
        """Short source citation: ``S-201 rev 2 (vector)``."""
        s = self.sheet or self.file
        if self.revision:
            s += f" rev {self.revision}"
        return f"{s} ({self.method})"


# ----------------------------------------------------------------- frames --

@dataclass
class Frame:
    """Affine (similarity) map page points -> world feet for ONE source page.

    ``e = a*x + b*y + tx``, ``n = c*x + d*y + ty``.  ``kind`` and ``label``
    are the run-wide frame declaration printed on every plate and point
    file (brief section 8)."""
    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = -1.0
    tx: float = 0.0
    ty: float = 0.0
    kind: str = "grid"        # "cad" | "grid" | "control"
    label: str = ""           # the printed declaration

    @property
    def ft_per_pt(self) -> float:
        return math.hypot(self.a, self.c)

    @property
    def pt_per_ft(self) -> float:
        return 1.0 / self.ft_per_pt

    def to_world(self, x: float, y: float) -> tuple:
        return (self.a * x + self.b * y + self.tx,
                self.c * x + self.d * y + self.ty)

    def to_page(self, e: float, n: float) -> tuple:
        det = self.a * self.d - self.b * self.c
        de, dn = e - self.tx, n - self.ty
        return ((self.d * de - self.b * dn) / det,
                (-self.c * de + self.a * dn) / det)

    def poly_to_world(self, poly) -> list:
        return [self.to_world(x, y) for x, y in poly]

    def poly_to_page(self, poly) -> list:
        return [self.to_page(e, n) for e, n in poly]

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "Frame":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


# --------------------------------------------------------------- elements --

@dataclass
class Element:
    """One read thing: a wall, column, slab edge run, opening, ...

    ``poly`` is the WORLD polygon (feet, closed implicitly) and
    ``page_poly`` the same outline in source page points (empty when the
    source is not a PDF page).  An angled element is always a polygon,
    never its bounding box.  Round things carry ``center`` + ``radius_ft``
    (and a many-sided ``poly`` for drawing)."""
    id: str = ""                      # stable: "L2-W-014"
    kind: str = WALL
    level: str = ""
    poly: list = field(default_factory=list)
    page_poly: list = field(default_factory=list)
    center: tuple | None = None       # world (e, n)
    radius_ft: float | None = None
    mark: str = ""                    # wall / column mark as drawn
    attrs: dict = field(default_factory=dict)
    prov: Provenance = field(default_factory=Provenance)
    sources: list = field(default_factory=list)    # SRC_* tags
    proofs: list = field(default_factory=list)     # proof record ids
    items: list = field(default_factory=list)      # Red Tag refs ("L2-3")
    closed: bool = True               # False for open runs (slab edge run)

    @property
    def single_source(self) -> bool:
        return len(set(self.sources)) <= 1

    def source_text(self) -> str:
        names = [SOURCE_NAME.get(s, s) for s in dict.fromkeys(self.sources)]
        return " + ".join(names) if names else "no source"

    def centroid(self) -> tuple:
        if self.center is not None:
            return self.center
        if not self.poly:
            return (0.0, 0.0)
        return (sum(p[0] for p in self.poly) / len(self.poly),
                sum(p[1] for p in self.poly) / len(self.poly))

    def bbox(self) -> tuple:
        pts = self.poly or ([self.center] if self.center else [(0.0, 0.0)])
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        r = self.radius_ft or 0.0
        if self.center is not None and not self.poly:
            return (xs[0] - r, ys[0] - r, xs[0] + r, ys[0] + r)
        return (min(xs), min(ys), max(xs), max(ys))

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["prov"] = self.prov.to_dict()
        d["poly"] = [list(p) for p in self.poly]
        d["page_poly"] = [list(p) for p in self.page_poly]
        d["center"] = list(self.center) if self.center is not None else None
        return d


@dataclass
class GridLine:
    """One named gridline.  ``axis`` is ``"x"`` for a line of constant
    Easting in the grid's own axes (it runs along grid north; letters or
    numbers, as drawn) and ``"y"`` for constant Northing.  ``offset_ft``
    is its position along the family's normal, measured from the first
    line of the family.  ``world_a`` / ``world_b`` are its ends in the
    run's world frame (ties are perpendicular distances to this line, so a
    grid that is rotated in the world frame still ties correctly)."""
    name: str = ""
    axis: str = "x"
    offset_ft: float = 0.0
    page_a: tuple = (0.0, 0.0)        # the line's two ends in page points
    page_b: tuple = (0.0, 0.0)
    bubble: tuple | None = None       # page center of its bubble
    world_a: tuple = (0.0, 0.0)
    world_b: tuple = (0.0, 0.0)

    def dist(self, e: float, n: float) -> float:
        """SIGNED perpendicular distance, feet, from this line to the point
        (positive toward increasing offset of its family)."""
        (x0, y0), (x1, y1) = self.world_a, self.world_b
        dx, dy = x1 - x0, y1 - y0
        ln = math.hypot(dx, dy)
        if ln < 1e-9:
            return math.hypot(e - x0, n - y0)
        d = (dx * (n - y0) - dy * (e - x0)) / ln
        # orient: x-lines positive toward +E of the grid, y-lines toward +N
        if self.axis == "x":
            return -d if dy > 0 else d
        return d if dx > 0 else -d


@dataclass
class Grid:
    lines: list = field(default_factory=list)       # [GridLine]
    #: printed spacing checks: [{"axis", "a", "b", "printed_ft", "measured_ft", "delta_ft", "text"}]
    spacing_checks: list = field(default_factory=list)

    def family(self, axis: str) -> list:
        return sorted((g for g in self.lines if g.axis == axis),
                      key=lambda g: g.offset_ft)

    def by_name(self, name: str):
        for g in self.lines:
            if g.name == name:
                return g
        return None

    def intersections(self) -> list:
        """[(name, e, n)] in the world frame for every x-line / y-line
        crossing (name is ``"A/1"``: x-line first)."""
        out = []
        for gx in self.family("x"):
            for gy in self.family("y"):
                p = _line_x(gx.world_a, gx.world_b, gy.world_a, gy.world_b)
                if p is not None:
                    out.append((f"{gx.name}/{gy.name}", p[0], p[1]))
        return out

    def page_intersections(self) -> dict:
        """{name: (x, y)} page points for every crossing."""
        out = {}
        for gx in self.family("x"):
            for gy in self.family("y"):
                p = _line_x(gx.page_a, gx.page_b, gy.page_a, gy.page_b)
                if p is not None:
                    out[f"{gx.name}/{gy.name}"] = p
        return out

    def nearest(self, e: float, n: float) -> tuple:
        """Ties to the two nearest gridlines: ``((x_line, d), (y_line, d))``
        with signed perpendicular distances in feet (point minus line), or
        ``None`` entries when a family is empty."""
        bx = min(self.family("x"), key=lambda g: abs(g.dist(e, n)), default=None)
        by = min(self.family("y"), key=lambda g: abs(g.dist(e, n)), default=None)
        return ((bx, bx.dist(e, n)) if bx else None,
                (by, by.dist(e, n)) if by else None)

    def tie_text(self, e: float, n: float) -> str:
        """``"2'-0" E of A, 4'-6" N of 1"`` (the two nearest gridlines)."""
        tx, ty = self.nearest(e, n)
        parts = []
        if tx:
            g, d = tx
            parts.append(f"on {g.name}" if abs(d) < 0.5 / 192.0
                         else f"{fmt_ftin(abs(d))} {'E' if d > 0 else 'W'} of {g.name}")
        if ty:
            g, d = ty
            parts.append(f"on {g.name}" if abs(d) < 0.5 / 192.0
                         else f"{fmt_ftin(abs(d))} {'N' if d > 0 else 'S'} of {g.name}")
        return ", ".join(parts)


def _line_x(a1, a2, b1, b2):
    rx, ry = a2[0] - a1[0], a2[1] - a1[1]
    sx, sy = b2[0] - b1[0], b2[1] - b1[1]
    den = rx * sy - ry * sx
    if abs(den) < 1e-12:
        return None
    t = ((b1[0] - a1[0]) * sy - (b1[1] - a1[1]) * sx) / den
    return (a1[0] + rx * t, a1[1] + ry * t)


@dataclass
class SheetRef:
    """One classified page (or CAD file) of the drop."""
    file: str = ""                    # absolute path
    page: int = 0                     # 1-based; 0 for a CAD file
    sheet: str = ""                   # sheet number ("" when none read)
    title: str = ""                   # sheet title as read
    cls: str = PG_UNKNOWN             # PG_*
    level: str = ""                   # level label ("" when none read)
    revision: str = ""                # revision text from the title block
    why: str = ""                     # the rule that classified it / why unknown
    prior: bool = False               # an earlier issue of a sheet that is also in the drop
    rfi: str = ""                     # RFI number, for an RFI response
    answer: str = ""                  # the answer text of an RFI response, as read

    @property
    def name(self) -> str:
        import os
        return os.path.basename(self.file)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Level:
    """Everything read for one level."""
    name: str = ""                    # "LEVEL 2"
    code: str = ""                    # "L2" (ids, point prefixes, file names)
    order: float = 0.0                # sort key, low to high
    struct: SheetRef | None = None
    arch: SheetRef | None = None
    cad: SheetRef | None = None
    pt_per_ft: float | None = None    # witnessed scale of the structural sheet
    scale_label: str = ""
    grid: Grid = field(default_factory=Grid)
    frame: Frame | None = None
    arch_frame: Frame | None = None
    elements: list = field(default_factory=list)
    arch_strings: list = field(default_factory=list)   # [dict] (see archdims)
    blocked: list = field(default_factory=list)        # reasons; non-empty = no layout
    notes: list = field(default_factory=list)          # reader remarks
    prior: SheetRef | None = None                      # an earlier issue of the structural sheet
    shops: list = field(default_factory=list)          # [SheetRef] shop drawings overlaid
    fill_rgb: tuple | None = None                      # the learned concrete fill class
    edge_pen: tuple | None = None                      # the learned slab edge class (rgb, width)
    hidden: list = field(default_factory=list)         # clusters of geometry the Sightline dropped
    unread: list = field(default_factory=list)         # fill-class regions seen but not read
    region: tuple | None = None                        # the plan region on the page (viewer points)
    arch_note: str = ""                                # why the architect strings were not compared ("" = they were)
    arch_off: int = 0                                  # architect strings with no end on the structure

    def of(self, *kinds) -> list:
        return [e for e in self.elements if e.kind in kinds]

    def get(self, eid: str):
        for e in self.elements:
            if e.id == eid:
                return e
        return None


# ---------------------------------------------------------------- options --

DEFAULT_HOLD_POLICY = {"answer": "hold", "confirm": "check", "answered": "release"}

#: which stage each point family belongs to (a preset, user-editable)
DEFAULT_STAGE_OF = {
    "grid": "grid",
    "slab_edge_corner": "pre", "radius_point": "pre", "slab_opening_corner": "pre",
    "wall_corner": "post", "column_corner": "post", "round_column_center": "post",
    "sw_zone_end": "post",
}


@dataclass
class Options:
    """The switches (brief section 10).  JSON-serializable: the CLI twin
    reads exactly this from its options file."""
    hats: list = field(default_factory=lambda: ["concrete"])   # "concrete", "surveyor"
    levels: list | None = None            # level codes; None = every level read
    stage: str = "both"                   # "pre" | "post" | "both"
    out_plates: bool = True
    out_booklet: bool = False
    out_kits: bool = True
    out_legend: bool = True
    out_rfi_drafts: bool = True
    out_report: bool = True
    masonry: bool = True
    overlays: bool = True
    hold_policy: dict = field(default_factory=lambda: dict(DEFAULT_HOLD_POLICY))
    working_range_ft: float | None = None  # a USER setting, never printed as a fact
    kits: list = field(default_factory=lambda: ["bowline"])
    sheet: str = "ANSI B"                 # plate sheet size (draft.SHEET_SIZES)
    issue: bool = False                   # False -> every plate is stamped FOR REVIEW
    proof_only: bool = False              # build only the one-sheet proof
    stage_of: dict = field(default_factory=lambda: dict(DEFAULT_STAGE_OF))
    #: human decisions a gate could not make alone
    fill_class: dict = field(default_factory=dict)     # sheet -> [r, g, b] (0..255)
    manual_scale: dict = field(default_factory=dict)   # sheet -> pt per ft
    level_of: dict = field(default_factory=dict)       # file name or sheet -> level label
    class_of: dict = field(default_factory=dict)       # file name or sheet -> PG_* class
    control: list = field(default_factory=list)        # [{"at": "A/1", "n":, "e":}]
    cad_units: str = ""                                # used ONLY when a CAD file declares no units
    #: user-set register fields (a status prints ONLY from these)
    statuses: dict = field(default_factory=dict)       # item ref -> status text
    answered: list = field(default_factory=list)       # [{"item", "rfi", "quote"}]
    skip_notes: list = field(default_factory=list)     # ["S-001/4"] notes not to quote
    #: project settings: the ONLY source of names and file metadata
    project: dict = field(default_factory=dict)        # project, drawn_by, date, ...
    dpi: int = 150

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "Options":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        unknown = sorted(set(d) - set(cls.__dataclass_fields__))
        o = cls(**known)
        o._unknown = unknown              # surfaced by the CLI, never ignored silently
        return o


@dataclass
class StageResult:
    name: str = ""
    status: str = PASS                # PASS | FLAG | BLOCK
    lines: list = field(default_factory=list)   # plain-words log lines

    def flag(self, msg: str) -> None:
        if self.status == PASS:
            self.status = FLAG
        self.lines.append(msg)

    def block(self, msg: str) -> None:
        self.status = BLOCK
        self.lines.append("BLOCKED: " + msg)

    def say(self, msg: str) -> None:
        self.lines.append(msg)
