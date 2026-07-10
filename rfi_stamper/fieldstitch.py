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

Field-grade extensions (all additive; old sidecars load unchanged):

* point ``kind`` (CONTROL / DESIGN / STAKED / CHECK) and a stake-out
  ``status`` lifecycle (PENDING -> STAKED -> VERIFIED | REJECTED) with
  ISO-dated transitions and never-downgrade bulk seeding (mirrors
  :meth:`rfi_stamper.resolution.ResolutionStore.seed_from_records`);
* composed-label validation (hard 16-char cap, collector charset);
* :class:`Spool` reserved number ranges per layer, quarantine spool for
  import collisions, tombstoned (retired) numbers that are never re-minted;
* shadow/witness points hosted on a parent (host-parametric world coords,
  cascade delete — same idiom as the Loft's doors);
* PNEZD/PENZD export profiles (``options=``), ``.tag.txt`` sidecars with a
  content checksum, a frame hash for as-staked round-trip gating, and
  advisory import validators (:func:`validate_import_csv`) that never
  modify anything — the human review table decides.

Coordinate conventions (same as the markups layer): page coordinates are
viewer page **points**, top-left origin, y **down**.  World coordinates are
Northing (+north), Easting (+east) and Z elevation in the job's real units.
Fully offline; all writes are atomic (temp file + fsync + ``os.replace``).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import uuid
import zipfile
from dataclasses import MISSING, dataclass, fields
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from .markups.measure import ScaleCal

_VERSION = 1

# ------------------------------------------------------- kinds & statuses --

#: Point kinds.  CONTROL is write-protected: coordinates are never
#: overwritten by an import and renumber() skips it entirely.
KINDS = ("CONTROL", "DESIGN", "STAKED", "CHECK")

#: DESIGN-point stake-out lifecycle.  REJECTED re-arms to PENDING (or jumps
#: straight to STAKED) on a re-stake.
POINT_STATUSES = ("PENDING", "STAKED", "VERIFIED", "REJECTED")

#: Rank order used by never-downgrade bulk seeding: a bulk operation may
#: only move a point *up* this ladder; direct :meth:`LayoutJob.set_status`
#: is unrestricted (that is how a human re-arms a REJECTED point).
STATUS_RANK = {"PENDING": 0, "REJECTED": 1, "STAKED": 2, "VERIFIED": 3}

#: Statuses whose point number is locked (plus kind CONTROL and the
#: ``locked`` flag): renumber() never re-flows them.
_NUM_LOCK_STATUSES = ("STAKED", "VERIFIED")

# ------------------------------------------------------------ label rules --

#: Hard cap on the composed label (prefix + zero-filled number + suffix) —
#: a data-collector field-width fact, enforced at creation and on strict
#: (options=) exports.
LABEL_MAX = 16

#: Collector-safe label charset.
LABEL_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


def validate_label(label: str) -> None:
    """Raise ``ValueError`` when a composed point label breaks the collector
    rules: hard cap of :data:`LABEL_MAX` characters, charset A-Z 0-9 - _ .
    (uppercase only — lowercase silently fails on classic collectors)."""
    s = str(label)
    if len(s) > LABEL_MAX:
        raise ValueError(
            f"point label {s!r} is {len(s)} chars; composed labels are "
            f"hard-capped at {LABEL_MAX} (collector field width) — shorten "
            "the prefix/suffix or the pad")
    bad = sorted({ch for ch in s if ch not in LABEL_CHARS})
    if bad:
        raise ValueError(
            f"point label {s!r} carries unsupported character(s) "
            f"{''.join(bad)!r}; allowed: A-Z 0-9 - _ . (uppercase only)")


# ----------------------------------------------------------------- spools --

#: Default reserved number ranges ("spools") per layer, all editable.
#: (layer name, start, end) — control 1-99, then the classic survey blocks,
#: then thousand-blocks per trade.
DEFAULT_SPOOLS = (
    ("Control", 1, 99),
    ("Work", 100, 199),
    ("Curve", 200, 399),
    ("Benchmarks", 400, 499),
    ("Ties", 500, 699),
    ("Property", 700, 999),
    ("Concrete", 1000, 1999),
    ("Steel", 2000, 2999),
    ("Mechanical", 3000, 3999),
    ("Electrical", 4000, 4999),
    ("Plumbing", 5000, 5999),
    ("User", 6000, 8999),
    ("AltControl", 9000, 9999),
)

#: Import collisions land here — never silently renumbered into a live block.
QUARANTINE_LAYER = "Quarantine"
QUARANTINE_START = 90000
QUARANTINE_END = 99999


@dataclass
class Spool:
    """A reserved point-number range owned by one layer.  ``next`` is the
    mint counter; it never rewinds (deleted numbers are tombstoned in
    ``LayoutJob.retired`` instead of being re-minted)."""
    layer: str
    start: int
    end: int
    next: int = 0                      # 0 -> start on first mint

    def to_dict(self) -> dict:
        return {"layer": self.layer, "start": self.start, "end": self.end,
                "next": self.next}

    @classmethod
    def from_dict(cls, d: dict) -> "Spool":
        return cls(layer=str(d.get("layer", "")), start=int(d.get("start", 1)),
                   end=int(d.get("end", 1)), next=int(d.get("next", 0)))


def _now_iso() -> str:
    # microseconds: renumber() sorts by (page, created) and must stay stable
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


from .fsutil import atomic_write_bytes as _atomic_bytes  # noqa: E402 -- one shared atomic write


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
    """One stakeable point, placed on a plan page in viewer points.

    The pre-existing fields (through ``created``) are the version-1 sidecar
    shape; everything after is the field-grade extension and is written to
    the sidecar **only when it differs from its default**, so old files load
    unchanged and new files stay lean.  ``elev`` may be ``None`` (a point
    with no elevation) — never conflate ``None`` with ``0.0``."""
    id: str                            # uuid4 hex (use LayoutPoint.new())
    num: int = 1
    prefix: str = ""
    suffix: str = ""
    page: int = 1
    x: float = 0.0                     # page pts, top-left origin, y down
    y: float = 0.0
    elev: float | None = 0.0           # real units (job.units); None = no Z
    desc: str = ""
    category: str = ""
    layer: str = "Layout"
    created: str = ""
    # ---- field-grade extension (all optional; lean in the sidecar) ------
    kind: str = "DESIGN"               # CONTROL | DESIGN | STAKED | CHECK
    status: str = "PENDING"            # PENDING | STAKED | VERIFIED | REJECTED
    status_log: list | None = None     # [{status, ts, note, by}] transitions
    code: str = ""                     # Stitch Code (first token of desc)
    z_ref: str = "FF"                  # FF | TOS | deck-above | datum
    tol_class: str = ""                # tolerance class name ("" = by layer)
    ref_num: int | None = None         # STAKED/CHECK: design point answered
    staked_by: str = ""                # crew initials
    staked_at: str = ""                # ISO-8601
    parent_uid: str = ""               # witness / hosted child: parent's uid
    offset_ft: float = 0.0             # witness offset distance (real units)
    offset_azimuth: float = 0.0        # witness bearing, deg from north
    provenance: dict | None = None     # generator id + rule + params
    # ---- control extras (meaningful when kind == "CONTROL") -------------
    monument: str = ""                 # hub+tack | nail | rebar+cap | ...
    set_by: str = ""
    date_set: str = ""
    last_checked: str = ""             # drives fresh->stale aging
    where_note: str = ""               # one-line "where to find it"
    locked: bool = False               # write-protect (CONTROL default True)

    @classmethod
    def new(cls, **kw) -> "LayoutPoint":
        kw.setdefault("id", uuid.uuid4().hex)
        kw.setdefault("created", _now_iso())
        return cls(**kw)

    @property
    def uid(self) -> str:
        """Immutable internal identity (alias of ``id``) — ALL status/QA
        bookkeeping keys on this, never on the display number."""
        return self.id

    @property
    def label(self) -> str:
        """Un-padded composed id (``CP-1-S``); zero-padded form is
        :meth:`LayoutJob.composed`."""
        return f"{self.prefix}{self.num}{self.suffix}"

    @property
    def is_witness(self) -> bool:
        return bool(self.parent_uid) and self.offset_ft != 0.0

    def to_dict(self) -> dict:
        """Version-1 fields always; extension fields only when non-default
        (keeps sidecars lean and byte-stable for pre-extension jobs)."""
        out: dict = {}
        for f in fields(self):
            v = getattr(self, f.name)
            if f.name in _V1_FIELDS or f.default is MISSING or v != f.default:
                out[f.name] = v
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "LayoutPoint":
        elev = d.get("elev", 0.0)
        ref = d.get("ref_num")
        prov = d.get("provenance")
        slog = d.get("status_log")
        return cls(id=str(d.get("id") or uuid.uuid4().hex),
                   num=int(d.get("num", 1)),
                   prefix=str(d.get("prefix", "")),
                   suffix=str(d.get("suffix", "")),
                   page=int(d.get("page", 1)),
                   x=float(d.get("x", 0.0)), y=float(d.get("y", 0.0)),
                   elev=None if elev is None else float(elev),
                   desc=str(d.get("desc", "")),
                   category=str(d.get("category", "")),
                   layer=str(d.get("layer", "Layout")),
                   created=str(d.get("created", "")),
                   kind=str(d.get("kind", "DESIGN")),
                   status=str(d.get("status", "PENDING")),
                   status_log=([dict(e) for e in slog]
                               if isinstance(slog, list) and slog else None),
                   code=str(d.get("code", "")),
                   z_ref=str(d.get("z_ref", "FF")),
                   tol_class=str(d.get("tol_class", "")),
                   ref_num=None if ref in (None, "") else int(ref),
                   staked_by=str(d.get("staked_by", "")),
                   staked_at=str(d.get("staked_at", "")),
                   parent_uid=str(d.get("parent_uid", "")),
                   offset_ft=float(d.get("offset_ft", 0.0)),
                   offset_azimuth=float(d.get("offset_azimuth", 0.0)),
                   provenance=dict(prov) if isinstance(prov, dict) else None,
                   monument=str(d.get("monument", "")),
                   set_by=str(d.get("set_by", "")),
                   date_set=str(d.get("date_set", "")),
                   last_checked=str(d.get("last_checked", "")),
                   where_note=str(d.get("where_note", "")),
                   locked=bool(d.get("locked", False)))


#: The original sidecar shape — always written, so pre-extension jobs
#: round-trip byte-identically.
_V1_FIELDS = frozenset(("id", "num", "prefix", "suffix", "page", "x", "y",
                        "elev", "desc", "category", "layer", "created"))

_COMPASS_8 = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def _compass(azimuth_deg: float) -> str:
    """Nearest 8-wind compass letter for an azimuth (deg from north)."""
    return _COMPASS_8[int(((azimuth_deg % 360.0) + 22.5) // 45.0) % 8]


def _fmt_ft(v: float) -> str:
    """2.0 -> '2'; 2.5 -> '2.5' (witness auto-description distances)."""
    return f"{v:g}"


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
        self.spools: list[Spool] = []             # reserved ranges per layer
        self.retired: set[int] = set()            # tombstoned numbers
        self.tolerances: dict = {}                # job tolerance overrides
        self.stitch_codes: dict = {}              # job Stitch Code overrides
        # ---- coordinate upgrades (fieldpro; ride the sidecar when used) --
        #: survey/world anchor: {n, e, z, h_datum, v_datum, unit} — the
        #: second origin next to base_world (the building-grid Anchor).
        self.survey_anchor: dict = {}
        #: combined scale factor (grid<->ground), stored to 8 decimals.
        #: When != 1 the scaling origin MUST be persisted (csf_origin) or
        #: the basis is irreproducible.
        self.csf: float = 1.0
        self.csf_origin: str = ""                 # point id/label it scales about
        self.csf_parts: dict = {}                 # {"k": ..., "ef": ...}
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
        sidecar autosaves.

        Field-grade rules: when the target layer owns a :class:`Spool`, the
        number is minted from that spool (``ValueError`` when the spool is
        full); an explicit ``num=`` may never reuse a live or retired
        (tombstoned) number; the composed label is validated at creation
        (:func:`validate_label`); ``kind="CONTROL"`` defaults ``locked``
        to True."""
        kw.setdefault("prefix", self.prefix)
        kw.setdefault("suffix", self.suffix)
        kw.setdefault("layer", "Layout")
        kind = str(kw.get("kind", "DESIGN")).upper()
        if kind not in KINDS:
            raise ValueError(f"unknown point kind {kind!r}; expected one of "
                             f"{KINDS}")
        if "kind" in kw:
            kw["kind"] = kind
        if kind == "CONTROL":
            kw.setdefault("locked", True)
        sp = self.spool(kw["layer"])
        explicit = "num" in kw
        if explicit:
            num = int(kw.pop("num"))
            if num in self.retired:
                raise ValueError(
                    f"point number {num} is retired (tombstoned) — numbers "
                    "are never reused after deletion")
            if any(q.num == num and not q.is_witness for q in self.points):
                raise ValueError(f"point number {num} is already in use — "
                                 "numbers are unique per job")
        elif sp is not None:
            num = self._peek(sp)
        else:
            num = self.next_num
            while num in self.retired or any(
                    q.num == num and not q.is_witness for q in self.points):
                num += 1
        # validate BEFORE committing any counter: a refused point must not
        # burn a number
        validate_label(f"{kw['prefix']}{str(num).zfill(self.pad)}"
                       f"{kw['suffix']}")
        if explicit:
            self.next_num = max(self.next_num, num + 1)
            if sp is not None and sp.start <= num <= sp.end:
                sp.next = max(sp.next, num + 1)
        elif sp is not None:
            sp.next = num + 1
        else:
            self.next_num = num + 1
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

    def find_by_num(self, num) -> LayoutPoint | None:
        """First non-witness point with this number (zero-fill-aware: pass
        an int or a digit string)."""
        try:
            n = int(str(num).strip())
        except (TypeError, ValueError):
            return None
        for p in self.points:
            if p.num == n and not p.is_witness:
                return p
        return None

    def remove(self, id) -> int:
        """Remove a point and every child hosted on it (witnesses cascade,
        like deleting a Loft wall deletes its doors).  Deleted numbers are
        tombstoned in ``retired`` and never re-minted.  Returns the total
        count removed (0 when the id is unknown; truthy on success)."""
        p = self.get(id)
        if p is None:
            return 0
        doomed = [p] + [c for c in self.points if c.parent_uid == p.id]
        for d in doomed:
            self.points.remove(d)
            if not d.is_witness:       # a witness shares its parent's number
                self.retired.add(d.num)
        self._autosave()
        return len(doomed)

    def points_on(self, page) -> list:
        return [p for p in self.points if p.page == int(page)]

    def renumber(self, start: int = 1) -> dict:
        """Re-flow point numbers, stable by (page, created) order.

        CONTROL points, number-locked points (status STAKED/VERIFIED or
        ``locked``) and retired (tombstoned) numbers are never touched;
        points on a spooled layer re-flow **within their own spool** and
        everything else re-flows from ``start``.  Witness points follow
        their parent's number.  Spool mint counters never rewind.  Returns
        ``{"locked": n, "reflowed": m}``."""
        witnesses = [p for p in self.points if p.parent_uid]
        locked = [p for p in self.points
                  if p not in witnesses and self._num_locked(p)]
        movable = [p for p in self.points
                   if p not in witnesses and not self._num_locked(p)]
        taken = {p.num for p in locked} | set(self.retired)
        reflowed = 0

        def assign(seq, begin, end=None, what=""):
            nonlocal reflowed
            n = begin
            for p in sorted(seq, key=lambda p: (p.page, p.created)):
                while n in taken:
                    n += 1
                if end is not None and n > end:
                    raise ValueError(
                        f"spool full during renumber: {what} has no free "
                        f"number left in {begin}-{end}")
                p.num = n
                taken.add(n)
                n += 1
                reflowed += 1

        spooled_layers = set()
        for sp in self.spools:
            group = [p for p in movable if p.layer == sp.layer]
            spooled_layers.add(sp.layer)
            if group:
                assign(group, sp.start, sp.end, f"layer {sp.layer!r}")
                sp.next = max(sp.next, max(p.num for p in group) + 1)
        assign([p for p in movable if p.layer not in spooled_layers], start)
        for w in witnesses:                      # witnesses ride the parent
            parent = self.get(w.parent_uid)
            if parent is not None:
                w.num = parent.num
        nums = [p.num for p in self.points if not p.is_witness]
        self.next_num = (max(nums) + 1) if nums else start
        self._autosave()
        return {"locked": len(locked), "reflowed": reflowed}

    def composed(self, p: LayoutPoint) -> str:
        """Point label with the number zero-padded to ``pad``: ``CP-001-S``."""
        return f"{p.prefix}{str(p.num).zfill(self.pad)}{p.suffix}"

    def _num_locked(self, p: LayoutPoint) -> bool:
        return (p.kind == "CONTROL" or p.status in _NUM_LOCK_STATUSES
                or bool(p.locked))

    # ------------------------------------------------------------ spools --

    def spool(self, layer) -> Spool | None:
        for sp in self.spools:
            if sp.layer == layer:
                return sp
        return None

    def add_spool(self, layer: str, start: int, end: int) -> Spool:
        """Reserve a number range for a layer; ranges may not overlap."""
        start, end = int(start), int(end)
        if start < 1 or end < start:
            raise ValueError(f"bad spool range {start}-{end}")
        if self.spool(layer) is not None:
            raise ValueError(f"layer {layer!r} already owns a spool")
        for sp in self.spools:
            if start <= sp.end and sp.start <= end:
                raise ValueError(
                    f"spool {start}-{end} overlaps layer {sp.layer!r} "
                    f"({sp.start}-{sp.end})")
        sp = Spool(layer=str(layer), start=start, end=end)
        self.spools.append(sp)
        self._autosave()
        return sp

    def add_default_spools(self) -> int:
        """Install the :data:`DEFAULT_SPOOLS` ranges (skipping layers that
        already own one).  Returns how many were added."""
        added = 0
        for layer, start, end in DEFAULT_SPOOLS:
            if self.spool(layer) is None:
                self.spools.append(Spool(layer=layer, start=start, end=end))
                added += 1
        if added:
            self._autosave()
        return added

    def quarantine_spool(self) -> Spool:
        """The import-collision spool (created on first use, 90000+)."""
        sp = self.spool(QUARANTINE_LAYER)
        if sp is None:
            sp = Spool(layer=QUARANTINE_LAYER, start=QUARANTINE_START,
                       end=QUARANTINE_END)
            self.spools.append(sp)
        return sp

    def _peek(self, sp: Spool) -> int:
        """Lowest free number on a spool WITHOUT committing it; skips live
        and retired numbers; never rewinds; raises ``ValueError`` when the
        spool is exhausted."""
        used = {p.num for p in self.points if not p.is_witness}
        n = max(sp.next, sp.start)
        while n in used or n in self.retired:
            n += 1
        if n > sp.end:
            raise ValueError(
                f"spool full: layer {sp.layer!r} has no free number left in "
                f"{sp.start}-{sp.end} — widen the spool or use an overflow "
                "block")
        return n

    def _mint(self, sp: Spool) -> int:
        """:meth:`_peek` + commit the mint counter (which never rewinds)."""
        n = self._peek(sp)
        sp.next = n + 1
        return n

    # ----------------------------------------------------------- statuses --

    def set_status(self, point_or_uid, status: str, note: str = "",
                   by: str = "") -> LayoutPoint:
        """Record a stake-out status transition with a UTC ISO timestamp in
        the point's ``status_log``.  Direct sets are unrestricted within
        :data:`POINT_STATUSES` (this is how a REJECTED point re-arms to
        PENDING); bulk operations go through :meth:`seed_statuses`, which
        never downgrades."""
        p = self._resolve_point(point_or_uid)
        if p is None:
            raise ValueError(f"no such point: {point_or_uid!r}")
        s = str(status or "").strip().upper()
        if s not in POINT_STATUSES:
            raise ValueError(f"unknown status {s!r}; expected one of "
                             f"{POINT_STATUSES}")
        ts = _now_iso()
        p.status = s
        p.status_log = (p.status_log or []) + [
            {"status": s, "ts": ts, "note": str(note or ""),
             "by": str(by or "")}]
        if s == "STAKED":
            p.staked_at = ts
            if by:
                p.staked_by = str(by)
        self._autosave()
        return p

    def seed_statuses(self, mapping: dict, note: str = "bulk seed") -> int:
        """Bulk status seeding that NEVER downgrades (mirror of
        ``resolution.ResolutionStore.seed_from_records``): a point moves
        only up the :data:`STATUS_RANK` ladder.  ``mapping`` keys may be
        uids, numbers, or labels.  Returns how many points changed."""
        applied = 0
        for key, status in mapping.items():
            p = self._resolve_point(key)
            if p is None:
                continue
            s = str(status or "").strip().upper()
            if s not in POINT_STATUSES:
                continue
            if STATUS_RANK[s] <= STATUS_RANK.get(p.status, 0):
                continue                         # never downgrade (or churn)
            p.status = s
            p.status_log = (p.status_log or []) + [
                {"status": s, "ts": _now_iso(), "note": note, "by": ""}]
            if s == "STAKED" and not p.staked_at:
                p.staked_at = p.status_log[-1]["ts"]
            applied += 1
        if applied:
            self._autosave()
        return applied

    def _resolve_point(self, key) -> LayoutPoint | None:
        """Point by object, uid, number (zero-fill-aware) or label."""
        if isinstance(key, LayoutPoint):
            return key if key in self.points else self.get(key.id)
        p = self.get(key)
        if p is not None:
            return p
        s = str(key).strip()
        if s.isdigit():
            return self.find_by_num(s)
        _, num, _ = _split_label(s)
        return self.find_by_num(num) if num is not None else None

    # --------------------------------------------------------- witnesses --

    def add_witness(self, parent_or_uid, offset_ft: float = 2.0,
                    offset_azimuth: float = 0.0, **kw) -> LayoutPoint:
        """Place a shadow/witness point hosted on a parent: world coords
        derive from the parent (recomputed on every parent move; deleting
        the parent cascades).  Name = parent number + ``W`` suffix; the
        auto-description follows the lath grammar (``W 2FT N OF CP-001``)."""
        parent = self._resolve_point(parent_or_uid)
        if parent is None:
            raise ValueError(f"no such parent point: {parent_or_uid!r}")
        if parent.parent_uid:
            raise ValueError("cannot host a witness on another witness")
        offset_ft = float(offset_ft)
        if offset_ft <= 0:
            raise ValueError("witness offset must be a positive distance")
        offset_azimuth = float(offset_azimuth) % 360.0
        suffix = f"{parent.suffix}W"
        validate_label(f"{parent.prefix}{str(parent.num).zfill(self.pad)}"
                       f"{suffix}")
        desc = kw.pop("desc", "") or (
            f"W {_fmt_ft(offset_ft)}FT {_compass(offset_azimuth)} OF "
            f"{self.composed(parent)}")
        layer = kw.pop("layer", parent.layer)
        if self.cal is not None:
            pn, pe, _pz = self.to_world(parent)
            az = math.radians(offset_azimuth)
            x, y = self.from_world(pn + offset_ft * math.cos(az),
                                   pe + offset_ft * math.sin(az))
        else:
            x, y = parent.x, parent.y
        p = LayoutPoint.new(num=parent.num, prefix=parent.prefix,
                            suffix=suffix, page=parent.page, x=x, y=y,
                            elev=parent.elev, desc=desc, layer=layer,
                            parent_uid=parent.id, offset_ft=offset_ft,
                            offset_azimuth=offset_azimuth, **kw)
        if self.layer(p.layer) is None:
            self.layers.append(PointLayer(p.layer))
        self.points.append(p)
        self._autosave()
        return p

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

    def to_world(self, p: LayoutPoint) -> tuple:
        """Page point -> (Northing, Easting, Z) in real units.

        The page vector from the basepoint is flipped to survey axes
        (east' = +x, north' = -y since page y runs down), rotated by
        ``rotation_deg`` (CCW positive), scaled by the calibration, and
        offset by ``base_world``.  Z is the point's elevation, already in
        real units (``None`` when the point has no elevation).

        Witness points are host-parametric: their world position is derived
        from the parent's **current** position plus the stored offset, so a
        parent move carries every witness with it."""
        if p.is_witness:
            parent = self.get(p.parent_uid)
            if parent is not None:
                n, e, z = self.to_world(parent)
                az = math.radians(p.offset_azimuth)
                return (n + p.offset_ft * math.cos(az),
                        e + p.offset_ft * math.sin(az), z)
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
        return (n, e, None if p.elev is None else float(p.elev))

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
        out = {
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
        # field-grade extension keys ride only when used (lean sidecars,
        # byte-stable for pre-extension jobs)
        if self.spools:
            out["spools"] = [sp.to_dict() for sp in self.spools]
        if self.retired:
            out["retired"] = sorted(self.retired)
        if self.tolerances:
            out["tolerances"] = dict(self.tolerances)
        if self.stitch_codes:
            out["stitch_codes"] = dict(self.stitch_codes)
        if self.survey_anchor:
            out["survey_anchor"] = dict(self.survey_anchor)
        if self.csf != 1.0:
            out["csf"] = self.csf
        if self.csf_origin:
            out["csf_origin"] = self.csf_origin
        if self.csf_parts:
            out["csf_parts"] = dict(self.csf_parts)
        return out

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
        layers, points, spools = [], [], []
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
        for d in data.get("spools") or []:
            try:
                spools.append(Spool.from_dict(d))
            except Exception:
                continue
        self.layers, self.points, self.spools = layers, points, spools
        retired = set()
        for v in data.get("retired") or []:
            try:
                retired.add(int(v))
            except (TypeError, ValueError):
                continue
        self.retired = retired
        tol = data.get("tolerances")
        self.tolerances = dict(tol) if isinstance(tol, dict) else {}
        codes = data.get("stitch_codes")
        self.stitch_codes = dict(codes) if isinstance(codes, dict) else {}
        anchor = data.get("survey_anchor")
        self.survey_anchor = dict(anchor) if isinstance(anchor, dict) else {}
        try:
            self.csf = float(data.get("csf", 1.0))
        except (TypeError, ValueError):
            self.csf = 1.0
        self.csf_origin = str(data.get("csf_origin", ""))
        parts = data.get("csf_parts")
        self.csf_parts = dict(parts) if isinstance(parts, dict) else {}

    def _autosave(self) -> None:
        if self.path:
            self.save()


# -------------------------------------------------------------- frame hash --

def frame_hash(job: LayoutJob) -> str:
    """8-hex digest of the job's georeference frame (basepoint, page anchor,
    rotation, scale, units).  Embedded in exports (comment header and
    ``.tag.txt`` sidecar) so an as-staked file coming back can prove it was
    shot against the same frame — otherwise imported deltas would contain
    the frame edit, not crew error."""
    cal = job.cal
    basis = "|".join((
        f"{float(job.base_world[0]):.6f}", f"{float(job.base_world[1]):.6f}",
        f"{float(job.base_page_xy[0]):.6f}",
        f"{float(job.base_page_xy[1]):.6f}",
        f"{float(job.rotation_deg):.6f}",
        f"{cal.real_per_pt:.10f}" if cal else "none",
        str(job.units)))
    return hashlib.sha256(basis.encode("ascii")).hexdigest()[:8]


_FRAME_RE = re.compile(r"frame:\s*([0-9a-f]{8})", re.IGNORECASE)


def _find_frame_hash(comments, path=None) -> str:
    """Frame hash from '#' comment lines and/or a .tag.txt sidecar beside
    ``path``; '' when none is declared."""
    for line in comments or []:
        m = _FRAME_RE.search(line)
        if m:
            return m.group(1).lower()
    if path:
        for cand in (os.path.splitext(path)[0] + ".tag.txt",
                     path + ".tag.txt"):
            if os.path.exists(cand):
                try:
                    with open(cand, encoding="utf-8", errors="replace") as f:
                        m = _FRAME_RE.search(f.read())
                    if m:
                        return m.group(1).lower()
                except OSError:
                    continue
    return ""


# --------------------------------------------------------------- exporters --

def _export_points(job: LayoutJob, points=None) -> list:
    """Default export set: every point on a visible layer (points whose layer
    is untracked count as visible).  An explicit ``points`` list bypasses the
    visibility filter entirely."""
    if points is not None:
        return list(points)
    hidden = {ly.name for ly in job.layers if not ly.visible}
    return [p for p in job.points if p.layer not in hidden]


def lint_witness_offsets(job: LayoutJob, points) -> list:
    """Witness-offset consistency lint: within one layer, every witness must
    sit on the SAME side at the SAME distance (an offset stake mistaken for
    the point itself is a classic bust).  Returns human-readable problem
    strings; empty list = clean.  Exporters refuse when non-empty."""
    per_layer: dict[str, set] = {}
    for p in points:
        if p.is_witness:
            per_layer.setdefault(p.layer, set()).add(
                (round(p.offset_ft, 4), round(p.offset_azimuth, 2)))
    return [
        f"layer {layer!r} mixes witness offsets: "
        + "; ".join(f"{_fmt_ft(d)} ft @ {a:g} deg"
                    for d, a in sorted(combos))
        for layer, combos in sorted(per_layer.items()) if len(combos) > 1]


def _pnezd_desc(p: LayoutPoint) -> str:
    return p.desc or p.category or p.layer


def _csv_safe(v) -> str:
    """Neutralize spreadsheet formula injection: a text cell opening with a
    formula trigger (= + - @ TAB CR) is read as a formula by spreadsheet
    apps, so prefix it with a single apostrophe (the value stays literal).
    Numeric coordinate cells are pre-formatted and must NOT pass through
    here."""
    s = str(v)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


def _apply_desc_commas(desc: str, policy: str) -> str:
    """Comma policy for the D field — many controllers do NOT honor quote
    escaping, so wire profiles strip or replace commas instead."""
    if policy == "keep":
        return desc
    if policy == "strip":
        return desc.replace(",", "")
    if policy == "semicolon":
        return desc.replace(",", ";")
    if policy == "space":
        return desc.replace(",", " ")
    raise ValueError(f"unknown desc_commas policy {policy!r}; expected "
                     "keep | strip | semicolon | space")


def export_csv_pnezd(job: LayoutJob, out_path: str, points=None,
                     header: bool = True, delimiter: str = ",",
                     options: dict | None = None) -> int:
    """PNEZD CSV: composed point id, Northing, Easting, Elevation,
    description.  Returns the data-row count.  Defaults reproduce the
    original export exactly; ``options`` selects a wire profile:

    ``order``          "PNEZD" (default) or "PENZD" (the classic legacy
                       office swap — E before N)
    ``delimiter``      overrides the ``delimiter`` argument
    ``header``         overrides the ``header`` argument (controllers want
                       headerless files — every line is a point)
    ``decimals``       N/E decimals, 3 (default) or 4
    ``z_decimals``     elevation decimals (default 3)
    ``include_code``   insert a Code column between Z and Description
    ``comment_header`` '#'-prefixed metadata lines (units, basepoint,
                       rotation, count, frame hash) — collectors skip them
    ``desc_commas``    "keep" (default) | "strip" | "semicolon" | "space"
    ``tag_sidecar``    write ``<name>.tag.txt`` beside the CSV (units,
                       basepoint, rotation, scale, count, min/max, frame
                       hash, 6-hex content checksum)
    ``strict_labels``  validate every composed label (:func:`validate_label`)
                       — defaults ON whenever ``options`` is passed

    Wire discipline whenever ``options`` is passed: ASCII, CRLF, no BOM.
    Always on (both paths): a ``None`` elevation writes an EMPTY field
    (never 0), duplicate exported ids raise ``ValueError`` listing them,
    and mixed witness offsets within a layer refuse the export
    (:func:`lint_witness_offsets`)."""
    opt = dict(options or {})
    order = str(opt.get("order", "PNEZD")).upper()
    if order not in ("PNEZD", "PENZD"):
        raise ValueError(f"unknown order {order!r}; expected PNEZD | PENZD")
    delimiter = str(opt.get("delimiter", delimiter))
    if "header" in opt:
        header = bool(opt["header"])
    nd = int(opt.get("decimals", 3))
    if nd not in (3, 4):
        raise ValueError("decimals must be 3 or 4 (0.001 ft is instrument "
                         "precision; 0.0001 ft is the tight profile)")
    zd = int(opt.get("z_decimals", 3))
    include_code = bool(opt.get("include_code", False))
    desc_commas = str(opt.get("desc_commas", "keep"))
    strict = bool(opt.get("strict_labels", options is not None))

    pts = _export_points(job, points)
    labels = [job.composed(p) for p in pts]
    seen: dict[str, int] = {}
    for lb in labels:
        seen[lb] = seen.get(lb, 0) + 1
    dupes = sorted(lb for lb, c in seen.items() if c > 1)
    if dupes:
        raise ValueError("duplicate point id(s) in export: "
                         + ", ".join(dupes))
    if strict:
        for lb in labels:
            validate_label(lb)
    problems = lint_witness_offsets(job, pts)
    if problems:
        raise ValueError("witness offsets disagree — refusing export: "
                         + " | ".join(problems))

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\r\n")
    if opt.get("comment_header"):
        for line in (
                "layout points",
                f"date: {_now_iso()}",
                f"units: {job.units}",
                f"order: {order}",
                f"basepoint: N {float(job.base_world[0]):.4f} "
                f"E {float(job.base_world[1]):.4f}",
                f"rotation_deg: {float(job.rotation_deg):.6f}",
                f"count: {len(pts)}",
                f"frame: {frame_hash(job)}"):
            buf.write(f"# {line}\r\n")
    if header:
        ne_cols = (["Northing", "Easting"] if order == "PNEZD"
                   else ["Easting", "Northing"])
        w.writerow(["Point"] + ne_cols + ["Elevation"]
                   + (["Code"] if include_code else []) + ["Description"])
    world = []
    for p, lb in zip(pts, labels):
        n, e, z = job.to_world(p)
        world.append((n, e, z))
        ne = ([f"{n:.{nd}f}", f"{e:.{nd}f}"] if order == "PNEZD"
              else [f"{e:.{nd}f}", f"{n:.{nd}f}"])
        zs = "" if z is None else f"{z:.{zd}f}"
        desc = _apply_desc_commas(_pnezd_desc(p), desc_commas)
        row = [_csv_safe(lb)] + ne + [zs]
        if include_code:
            row.append(_csv_safe(p.code))
        row.append(_csv_safe(desc))
        w.writerow(row)
    encoding = "ascii" if options is not None else "utf-8"
    data = buf.getvalue().encode(encoding, errors="replace")
    _atomic_bytes(data, out_path)
    if opt.get("tag_sidecar"):
        _write_tag_sidecar(job, data, out_path, order, world)
    return len(pts)


def _write_tag_sidecar(job: LayoutJob, csv_bytes: bytes, out_path: str,
                       order: str, world: list) -> None:
    """Paired ``<name>.tag.txt`` for the strict-collector preset: all the
    metadata the headerless CSV cannot carry, plus a 6-hex content checksum
    so the office can prove which file the crew loaded."""
    ns = [t[0] for t in world]
    es = [t[1] for t in world]
    zs = [t[2] for t in world if t[2] is not None]
    cal = job.cal
    lines = [
        "planloom point-file tag",
        f"file: {os.path.basename(out_path)}",
        f"units: {job.units}",
        f"order: {order}",
        f"basepoint: N {float(job.base_world[0]):.4f} "
        f"E {float(job.base_world[1]):.4f}",
        f"rotation_deg: {float(job.rotation_deg):.6f}",
        f"scale_real_per_pt: {cal.real_per_pt:.10f}" if cal
        else "scale_real_per_pt: none",
        f"count: {len(world)}",
        (f"min: N {min(ns):.4f} E {min(es):.4f} "
         + (f"Z {min(zs):.4f}" if zs else "Z -")) if world else "min: -",
        (f"max: N {max(ns):.4f} E {max(es):.4f} "
         + (f"Z {max(zs):.4f}" if zs else "Z -")) if world else "max: -",
        f"frame: {frame_hash(job)}",
        f"checksum: {hashlib.sha256(csv_bytes).hexdigest()[:6]}",
    ]
    tag_path = os.path.splitext(out_path)[0] + ".tag.txt"
    _atomic_bytes(("\r\n".join(lines) + "\r\n").encode("ascii", "replace"),
                  tag_path)


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
        # A literal 'inf'/'nan' in a <v> cell corrupts the workbook (Excel
        # rejects the sheet).  Non-finite coordinates fall back to 0.
        try:
            fv = float(value)
        except (TypeError, ValueError):
            fv = float("nan")
        if not math.isfinite(fv):
            value = "0"
        return f'<c r="{ref}"><v>{value}</v></c>'
    return (f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">'
            f"{escape(str(value))}</t></is></c>")


def export_xlsx(job: LayoutJob, out_path: str, points=None,
                dialect: str = "grid") -> int:
    """Minimal real XLSX workbook (one sheet, inline strings, numeric
    coordinate cells).  Returns the data-row count.

    Dialects: ``grid`` (the grid-layout tablet columns, X=Easting first)
    and ``pnezd`` (Point/Northing/Easting/Elevation/Description — the
    column order robotic-total-station office suites import directly;
    order pulled through selvage.WRITER_ORDER, never inlined)."""
    from .selvage import ordered
    pts = _export_points(job, points)
    header = (_XLSX_HEADER if dialect == "grid" else
              ["Point", "Northing", "Easting", "Elevation", "Description",
               "Layer"])
    rows_xml = ["<row r=\"1\">" + "".join(
        _xlsx_cell(c, 1, h, False) for c, h in enumerate(header))
        + "</row>"]
    for i, p in enumerate(pts, start=2):
        n, e, z = job.to_world(p)
        zc = (f"{z:.3f}", True) if z is not None else ("", False)
        if dialect == "grid":
            cells = [
                (job.composed(p), False), (p.prefix, False), (p.num, True),
                (p.suffix, False), (f"{e:.3f}", True), (f"{n:.3f}", True),
                # None elevation -> empty text cell, never a fake 0
                zc, (p.desc, False), (p.category, False),
                (p.layer, False),
            ]
        else:
            c1, c2, _ = ordered("xlsx_pnezd", (f"{n:.3f}", True),
                                (f"{e:.3f}", True), zc)
            cells = [(job.composed(p), False), c1, c2, zc,
                     (p.desc, False), (p.layer, False)]
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


def _dxf_clean(v) -> str:
    """DXF is line-oriented: a CR/LF (or any control char) inside a free-text
    value is read as the next group code and corrupts the file (and desyncs
    the (70, count) layer count).  Collapse every control character to a
    space so a layer name or label can never inject group codes."""
    return "".join(" " if ord(ch) < 0x20 else ch for ch in str(v))


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
        pairs += [(0, "LAYER"), (2, _dxf_clean(name)), (70, "0"),
                  (62, str(color)), (6, "CONTINUOUS")]
    pairs += [(0, "ENDTAB"), (0, "ENDSEC"),
              (0, "SECTION"), (2, "ENTITIES")]
    off = _DXF_TEXT_H * 0.5
    entities = 0
    for p in pts:
        n, e, z = job.to_world(p)
        z = 0.0 if z is None else z            # DXF group 30 has no null form
        layer = _dxf_clean(p.layer)
        pairs += [(0, "POINT"), (8, layer),
                  (10, f"{e:.4f}"), (20, f"{n:.4f}"), (30, f"{z:.4f}")]
        pairs += [(0, "TEXT"), (8, layer),
                  (10, f"{e + off:.4f}"), (20, f"{n + off:.4f}"),
                  (30, f"{z:.4f}"), (40, f"{_DXF_TEXT_H:.2f}"),
                  (1, _dxf_clean(job.composed(p)))]
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
    # a file carrying BOTH a Code and a Description column: the description
    # wins the d slot ("code" is also a d alias and may have hit first)
    for i, cell in enumerate(row):
        if _canon_header(cell) in ("description", "desc"):
            hits["d"] = i
            break
    return hits if "n" in hits and "e" in hits else None


def _split_label(label: str) -> tuple[str, int | None, str]:
    """'CP-001-S' -> ('CP-', 1, '-S'); no digits -> (label, None, '')."""
    s = str(label).strip()
    m = _LABEL_SPLIT.match(s) or _LABEL_SPLIT_LAST.match(s)
    if not m:
        return s, None, ""
    return m.group(1), int(m.group(2)), m.group(3)


#: Null-elevation sentinel spellings (case-insensitive) and magic values —
#: a null staked as 0.00 sets sleeves at datum zero, a real recurring
#: incident, so these map to ``None`` and export back as an EMPTY field.
_NULL_Z_TEXT = {"", "?", "NULL"}
_NULL_Z_VALUES = (-99999.0, 9999.999)


def _parse_z(cell: str, zero_is_null: bool = False):
    """Elevation cell -> float | None (sentinels and, optionally, 0.0 map
    to None; unparseable junk keeps the legacy 0.0 fallback)."""
    s = str(cell or "").strip()
    if s.upper() in _NULL_Z_TEXT:
        return None
    try:
        z = float(s)
    except ValueError:
        return 0.0                              # legacy tolerant fallback
    if z in _NULL_Z_VALUES or (zero_is_null and z == 0.0):
        return None
    return z


_POSITIONAL_COLS = {
    "PNEZD": {"point": 0, "n": 1, "e": 2, "z": 3, "d": 4},
    "PENZD": {"point": 0, "e": 1, "n": 2, "z": 3, "d": 4},
}


def read_point_csv(path: str, order: str = "PNEZD") -> dict:
    """Low-level tolerant point-CSV reader shared by :func:`import_csv`,
    :func:`validate_import_csv` and the QA layer's as-staked import.

    Strips a BOM, skips ``#`` comment lines (collecting them), sniffs the
    delimiter, maps a header row when present (any column order) and falls
    back to positional columns per ``order`` (PNEZD default, PENZD for the
    classic swapped dialect).  Returns::

        {"rows": [{"id", "n", "e", "z", "desc"}...],   # z may be None
         "bad": [(lineno, row), ...],                  # unparseable N/E
         "comments": [...], "frame": "8-hex or ''"}
    """
    order = str(order or "PNEZD").upper()
    if order not in _POSITIONAL_COLS:
        raise ValueError(f"unknown order {order!r}; expected PNEZD | PENZD")
    with open(path, encoding="utf-8-sig", newline="") as f:
        raw = f.read()
    lines = raw.splitlines()
    comments = [ln for ln in lines if ln.lstrip().startswith("#")]
    body = "\n".join(ln for ln in lines if not ln.lstrip().startswith("#"))
    try:
        dialect = csv.Sniffer().sniff(body[:4096], delimiters=",;\t")
        reader = csv.reader(io.StringIO(body), dialect)
    except csv.Error:
        reader = csv.reader(io.StringIO(body))     # default comma dialect
    rows = [r for r in reader if any(str(c).strip() for c in r)]
    cols = _header_map(rows[0]) if rows else None
    if cols is not None:
        rows = rows[1:]
    else:
        cols = dict(_POSITIONAL_COLS[order])

    def cell(row, fieldname):
        i = cols.get(fieldname)
        return row[i].strip() if i is not None and i < len(row) else ""

    parsed, bad = [], []
    for lineno, row in enumerate(rows, 1):
        try:
            n = float(cell(row, "n"))
            e = float(cell(row, "e"))
        except ValueError:
            bad.append((lineno, row))
            continue
        parsed.append({"id": cell(row, "point"), "n": n, "e": e,
                       "z": _parse_z(cell(row, "z")),
                       "desc": cell(row, "d")})
    return {"rows": parsed, "bad": bad, "comments": comments,
            "frame": _find_frame_hash(comments, path)}


def import_csv(job: LayoutJob, path: str, log=print, *,
               order: str = "PNEZD", on_collision: str = "quarantine",
               zero_elev_is_null: bool = False) -> int:
    """Tolerant PNEZD CSV reader: header detected and mapped when present
    (point/name, n/northing/y, e/easting/x, z/elev, d/desc), positional
    P-N-E-Z-D otherwise (``order="PENZD"`` for the swapped dialect).  World
    coordinates are converted back to page points through the inverse of
    :meth:`LayoutJob.to_world` (a scale must be set); imported points land
    on page 1 — PNEZD files carry no page.  Rows that do not parse are
    logged and skipped.  Null-elevation sentinels ('', ?, NULL, -99999,
    9999.999 — and 0.0 with ``zero_elev_is_null=True``) become ``None``.

    Incoming ids matching a live point number (zero-fill-aware: '001' ==
    '1') follow ``on_collision``:

    * ``"quarantine"`` (default) — the row lands on the Quarantine layer
      with a fresh number minted from the 90000+ quarantine spool, logged;
      never silently renumbered into a live block;
    * ``"keep"`` — keep the job's point, skip the row (logged);
    * ``"replace"`` — take the incoming coordinates/elevation/description —
      EXCEPT for CONTROL or locked points, whose coordinates are never
      overwritten by an import (those rows quarantine instead);
    * ``"refuse"`` — raise ``ValueError`` listing every colliding id.

    Returns how many rows were applied (added + replaced + quarantined)."""
    if job.cal is None:
        raise ValueError(
            "no scale set: calibrate the plan (ScaleCal) and store it in "
            "job.scale before importing world coordinates")
    if on_collision not in ("quarantine", "keep", "replace", "refuse"):
        raise ValueError(f"unknown on_collision {on_collision!r}; expected "
                         "quarantine | keep | replace | refuse")
    data = read_point_csv(path, order=order)
    for lineno, row in data["bad"]:
        log(f"  !! row {lineno}: bad N/E {row!r}, skipped")
    return apply_import_rows(job, data["rows"], log,
                             on_collision=on_collision,
                             zero_elev_is_null=zero_elev_is_null)


def apply_import_rows(job: LayoutJob, rows, log=print, *,
                      on_collision: str = "quarantine",
                      zero_elev_is_null: bool = False) -> int:
    """Apply already-parsed point rows to a job — the shared back half of
    every importer (:func:`import_csv` and the :mod:`rfi_stamper.selvage`
    wire-format readers all land here, so collision policy, quarantine and
    CONTROL protection behave identically on every dialect).

    Each row is ``{"id", "n", "e", "z", "desc"}`` (``z`` may be ``None``)
    plus two optional extension keys the wire formats carry: ``"kind"``
    (e.g. LandXML ``state="existing"`` / GSI station words import as
    CONTROL) and ``"code"``.  Semantics are exactly those documented on
    :func:`import_csv`; returns how many rows were applied."""
    if job.cal is None:
        raise ValueError(
            "no scale set: calibrate the plan (ScaleCal) and store it in "
            "job.scale before importing world coordinates")
    if on_collision not in ("quarantine", "keep", "replace", "refuse"):
        raise ValueError(f"unknown on_collision {on_collision!r}; expected "
                         "quarantine | keep | replace | refuse")
    if on_collision == "refuse":
        colliding = sorted({r["id"] for r in rows
                            if _split_label(r["id"])[1] is not None
                            and job.find_by_num(_split_label(r["id"])[1])})
        if colliding:
            raise ValueError("import collides with live point number(s): "
                             + ", ".join(colliding))

    added = 0
    for rec in rows:
        n, e = rec["n"], rec["e"]
        z = rec["z"]
        if zero_elev_is_null and z == 0.0:
            z = None
        prefix, num, suffix = _split_label(rec["id"])
        if num is None:
            num, prefix, suffix = job.next_num, prefix or job.prefix, ""
        x, y = job.from_world(n, e)
        target = job.find_by_num(num)
        layer = "Layout"
        if target is not None:
            protected = target.kind == "CONTROL" or target.locked
            if on_collision == "keep":
                log(f"  !! id {rec['id']!r} collides with live point "
                    f"{num} — kept ours, row skipped")
                continue
            if on_collision == "replace" and not protected:
                target.x, target.y, target.elev = x, y, z
                if rec["desc"]:
                    target.desc = rec["desc"]
                added += 1
                continue
            if on_collision == "replace":
                log(f"  !! id {rec['id']!r} collides with protected "
                    f"{target.kind} point {num} — coordinates are never "
                    "overwritten; quarantined instead")
            qnum = job._mint(job.quarantine_spool())
            log(f"  !! id {rec['id']!r} collides with live point {num} — "
                f"quarantined as {qnum} on layer {QUARANTINE_LAYER!r}")
            num, layer = qnum, QUARANTINE_LAYER
        if job.layer(layer) is None:
            job.layers.append(PointLayer(layer))
        kind = str(rec.get("kind", "") or "DESIGN").upper()
        if kind not in KINDS:
            kind = "DESIGN"
        job.points.append(LayoutPoint.new(
            num=num, prefix=prefix, suffix=suffix, page=1, x=x, y=y,
            elev=z, desc=rec["desc"], layer=layer, kind=kind,
            code=str(rec.get("code", "") or ""),
            locked=(kind == "CONTROL")))
        job.next_num = max(job.next_num, num + 1)
        added += 1
    if added:
        job._autosave()
    return added


def _median(values):
    vs = sorted(values)
    if not vs:
        return None
    mid = len(vs) // 2
    return vs[mid] if len(vs) % 2 else (vs[mid - 1] + vs[mid]) / 2.0


def validate_import_csv(job: LayoutJob, path: str,
                        order: str = "PNEZD") -> dict:
    """Advisory pre-import validators — a report dict for the human review
    table; NOTHING is applied or modified (same philosophy as the stamper's
    mapping review).  Keys:

    ``rows``            parseable point-row count
    ``ids``             ids in file order
    ``duplicate_ids``   ids appearing more than once in the file
    ``collisions``      ids whose number is live in the job
    ``range_ok``/``foreign``  ids outside 10x the job bbox ("foreign grid?")
    ``swap_suggested``  True when medians fit better with N/E swapped
                        (PENZD reinterpretation) — suggestion only
    ``unit_hint``       "" or a meters-vs-feet magnitude hint (~3.2808x)
    ``elev_outliers``   ids more than 500 units from the file's median Z
    ``frame_hash``/``frame_hash_ok``  declared frame hash vs this job's
                        (None when the file declares none)
    """
    data = read_point_csv(path, order=order)
    rows = data["rows"]
    ids = [r["id"] for r in rows]
    seen: dict[str, int] = {}
    for i in ids:
        seen[i] = seen.get(i, 0) + 1
    collisions = sorted({r["id"] for r in rows
                         if _split_label(r["id"])[1] is not None
                         and job.find_by_num(_split_label(r["id"])[1])})
    report = {
        "rows": len(rows), "ids": ids,
        "duplicate_ids": sorted(i for i, c in seen.items() if c > 1),
        "collisions": collisions,
        "range_ok": True, "foreign": [],
        "swap_suggested": False, "unit_hint": "",
        "elev_outliers": [],
        "frame_hash": data["frame"], "frame_hash_ok": None,
    }
    if not rows:
        return report
    ns = [r["n"] for r in rows]
    es = [r["e"] for r in rows]
    # range check vs 10x the job bbox (fall back to the basepoint alone)
    bounds = job.bounds_world()
    if bounds is not None:
        min_n, min_e, max_n, max_e = bounds
    else:
        min_n = max_n = float(job.base_world[0])
        min_e = max_e = float(job.base_world[1])
    cn, ce = (min_n + max_n) / 2.0, (min_e + max_e) / 2.0
    half_n = max((max_n - min_n) / 2.0, 100.0) * 10.0
    half_e = max((max_e - min_e) / 2.0, 100.0) * 10.0
    report["foreign"] = [r["id"] for r in rows
                         if abs(r["n"] - cn) > half_n
                         or abs(r["e"] - ce) > half_e]
    report["range_ok"] = not report["foreign"]
    # swap detector: do the medians fit better mirrored about N=E?
    med_n, med_e = _median(ns), _median(es)
    err = abs(med_n - cn) + abs(med_e - ce)
    err_swapped = abs(med_e - cn) + abs(med_n - ce)
    report["swap_suggested"] = err_swapped * 2.0 < err
    # unit sniff: ~3.2808x magnitude mismatch smells like meters vs feet
    mag_file = (abs(med_n) + abs(med_e)) / 2.0
    mag_job = (abs(cn) + abs(ce)) / 2.0
    if mag_job > 0 and mag_file > 0:
        ratio = mag_file / mag_job
        if 0.28 <= ratio <= 0.34:
            report["unit_hint"] = ("file coordinates are ~0.3048x the job "
                                   "frame — meters in a feet job?")
        elif 3.0 <= ratio <= 3.6:
            report["unit_hint"] = ("file coordinates are ~3.2808x the job "
                                   "frame — feet in a metric job?")
    zs = [r["z"] for r in rows if r["z"] is not None]
    med_z = _median(zs)
    if med_z is not None:
        report["elev_outliers"] = [r["id"] for r in rows
                                   if r["z"] is not None
                                   and abs(r["z"] - med_z) > 500.0]
    if data["frame"]:
        report["frame_hash_ok"] = (data["frame"] == frame_hash(job))
    return report


# --------------------------------------------------------------- field kits --

#: Export bundles matched to what a crew's tablet ingests, keyed by rigging
#: knot (no vendor names): a simple CSV+DXF rig, an XLSX+DXF rig, the
#: everything bundle, and the two wire-format rigs from
#: :mod:`rfi_stamper.selvage` — ``sheetbend`` (the knot that joins two
#: different ropes: LandXML + CSV for office suites and modern controllers)
#: and ``marlinspike`` (the rigger's fieldbook spike: GSI + SP-record
#: fieldbook for the classic fixed-width/record collectors).
KITS = {
    # PNEZD-order XLSX rides the robotic-total-station kit (owner ask:
    # the RTS office software reads spreadsheets with coordinates too)
    "bowline": ("csv", "xlsx_pnezd", "dxf"),
    "clovehitch": ("xlsx", "dxf"),
    "fullspool": ("csv", "xlsx", "dxf", "json"),
    "sheetbend": ("landxml", "csv"),
    "marlinspike": ("gsi", "sp"),
}

#: Kit format tag -> file extension where they differ.
_KIT_EXT = {"landxml": "xml", "sp": "rw5", "xlsx_pnezd": "xlsx"}


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
    for fmt in KITS[kit]:
        out = os.path.join(out_dir, f"{stem}.{_KIT_EXT.get(fmt, fmt)}")
        if fmt == "csv":
            export_csv_pnezd(job, out, points=pts)
        elif fmt == "xlsx":
            export_xlsx(job, out, points=pts)
        elif fmt == "xlsx_pnezd":
            export_xlsx(job, out, points=pts, dialect="pnezd")
        elif fmt == "dxf":
            export_dxf(job, out, points=pts)
        elif fmt in ("landxml", "gsi", "sp"):
            from . import selvage            # local import: no cycle at load
            if fmt == "landxml":
                selvage.export_landxml(job, out, points=pts)
            elif fmt == "gsi":
                selvage.export_gsi(job, out, points=pts)
            else:
                selvage.export_sp(job, out, points=pts,
                                    log=lambda m: None)
        else:
            export_job_json(job, out)
        files.append(out)
    return {"files": files, "points": len(pts)}
