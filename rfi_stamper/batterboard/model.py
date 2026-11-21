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
    Easting (it runs north-south; letters or numbers, as drawn) and ``"y"``
    for constant Northing.  ``offset_ft`` is its coordinate along the
    perpendicular axis in the world frame."""
    name: str = ""
    axis: str = "x"
    offset_ft: float = 0.0
    page_a: tuple = (0.0, 0.0)        # the line's two ends in page points
    page_b: tuple = (0.0, 0.0)
    bubble: tuple | None = None       # page center of its bubble


@dataclass
class Grid:
    lines: list = field(default_factory=list)       # [GridLine]
    #: printed spacing checks: [{"a", "b", "printed_ft", "measured_ft", "delta_ft"}]
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
        """[(name, e, n)] for every x-line / y-line crossing."""
        out = []
        for gx in self.family("x"):
            for gy in self.family("y"):
                out.append((f"{gx.name}/{gy.name}", gx.offset_ft, gy.offset_ft))
        return out

    def nearest(self, e: float, n: float) -> tuple:
        """Ties to the two nearest gridlines: ``((x_line, de), (y_line, dn))``
        with signed offsets (point minus line), or ``None`` entries."""
        bx = min(self.family("x"), key=lambda g: abs(e - g.offset_ft), default=None)
        by = min(self.family("y"), key=lambda g: abs(n - g.offset_ft), default=None)
        return ((bx, e - bx.offset_ft) if bx else None,
                (by, n - by.offset_ft) if by else None)


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
