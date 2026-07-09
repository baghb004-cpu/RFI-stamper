"""Fieldpro: the layout QA layer — tolerance classes, Stitch Codes, delta
math, as-staked round-trip, check-shot brackets, the As-Staked Ledger.

Rides on :mod:`rfi_stamper.fieldstitch` (the point engine) and closes the
loop offline: design points go out as CSV, staked shots come back as CSV,
and this module pairs them (human review table first, same idiom as the
stamper's mapping review), computes deltas against job tolerance classes,
advances point statuses (never downgrading in bulk), and prints the signed
As-Staked Ledger PDF — the deliverable, not a nicety: a large share of
construction delays trace to layout disputes, and the signed ledger is what
settles them.

Doctrine baked into the defaults (all editable, all labeled "verify against
project spec"):

* layout budget <= 1/3 (at most 1/2) of the construction tolerance it
  serves — the presets encode the layout share, not the code limit;
* bolts laid out at top-of-concrete are often checked at top-of-bolt — a
  5 deg lean on a 6 in projection reads as 1/2 in of position error that
  isn't real, so every delta record carries ``measured_at``
  (surface | projection);
* verdicts are computed on UNROUNDED values, then rounded for display
  (compare-then-round) — stated in the ledger footer;
* check shots are compared, never averaged, and never overwrite control.

Fully offline; stdlib + Planloom's own minipdf engine; all writes are atomic.
"""
from __future__ import annotations

import io
import json
import math
import os
import re
from dataclasses import dataclass, fields
from datetime import datetime, timezone

from .minipdf import colors
from .minipdf.flow import (
    HRFlowable,
    Paragraph,
    ParagraphStyle,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from .minipdf.pagesizes import landscape, letter

from . import transmittal
from .fieldstitch import (
    LayoutJob,
    LayoutPoint,
    STATUS_RANK,
    _atomic_bytes,
    _split_label,
    export_csv_pnezd,
    frame_hash,
    read_point_csv,
)

_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------- tolerance classes --

#: The brief's ft-per-inch-fraction conversions (decimal feet, as shipped).
EIGHTH_IN_FT = 0.0104
QUARTER_IN_FT = 0.0208
THREE_EIGHTHS_IN_FT = 0.0313
HALF_IN_FT = 0.0417
INCH_FT = 0.0833

#: Printed in the class editor and on reports.
TOLERANCE_DISCLAIMER = ("Editable defaults — verify against project spec.")
LAYOUT_BUDGET_NOTE = (
    "Layout budget <= 1/3 (at most 1/2) of the construction tolerance it "
    "serves — these presets encode the layout share, not the code limit.")

@dataclass
class ToleranceClass:
    """One job-editable tolerance row.  ``h_ft``/``v_ft`` are decimal feet
    (display converts to ft-in fractions); ``None`` = that axis is not
    judged by this class."""
    name: str
    h_ft: float | None = None
    v_ft: float | None = None
    basis: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "h_ft": self.h_ft, "v_ft": self.v_ft,
                "basis": self.basis, "note": self.note}

    @classmethod
    def from_dict(cls, d: dict) -> "ToleranceClass":
        def _f(v):
            return None if v is None else float(v)
        return cls(name=str(d.get("name", "")), h_ft=_f(d.get("h_ft")),
                   v_ft=_f(d.get("v_ft")), basis=str(d.get("basis", "")),
                   note=str(d.get("note", "")))


def _tc(name, h, v, basis, note=""):
    return ToleranceClass(name=name, h_ft=h, v_ft=v, basis=basis, note=note)


#: Ship-with defaults (decimal feet).  Values marked practice/agency are
#: editable defaults labeled "verify against project spec", never hardcoded
#: truths — see :data:`TOLERANCE_DISCLAIMER`.
DEFAULT_TOLERANCES = {tc.name: tc for tc in (
    _tc("CONTROL", 0.005, 0.02,
        "control-network practice (closes 1:20,000+); H 1/16 in (1.6 mm)"),
    _tc("GRIDLINE", EIGHTH_IN_FT, None,
        "working-point practice (gridline / structural line, 1/8 in)"),
    _tc("ANCHOR-S", QUARTER_IN_FT, HALF_IN_FT,
        "bolts <= 7/8 in dia: +/-1/4 in, rod top +/-1/2 in — "
        "diameter-banded, harmonized concrete/steel table"),
    _tc("ANCHOR-M", THREE_EIGHTHS_IN_FT, HALF_IN_FT,
        "bolts 1-1.5 in dia: +/-3/8 in, rod top +/-1/2 in — same table"),
    _tc("ANCHOR-L", HALF_IN_FT, HALF_IN_FT,
        "bolts 1.75-2.5 in dia: +/-1/2 in, rod top +/-1/2 in — same table"),
    _tc("BOLT-IN-GROUP", EIGHTH_IN_FT, None,
        "steel code of standard practice sec 7.5: 1/8 in between any two "
        "rods in a group; 1/4 in between adjacent group centers; 1/4 in "
        "group-to-column-line; accumulation <= 1/4 in per 100 ft, max 1 in "
        "total; column plumb 1:500",
        note="group tolerances are a lineage: judge point-to-point AND "
             "cumulative along the gridline chain"),
    _tc("EMBED", INCH_FT, None,
        "concrete tolerance spec: +/-1 in; 'practical' variant +/-1/4 in "
        "(specs routinely tighten)"),
    _tc("SLEEVE", HALF_IN_FT, None,
        "sleeve / outlet: +/-1/2 in; common spec override +/-1/4 in"),
    _tc("MEP-HANGER", QUARTER_IN_FT, None,
        "practice: +/-1/4 to 3/8 in"),
    _tc("TRACK", QUARTER_IN_FT, None,
        "gypsum standard: track / partition +/-1/4 in (some hold 1/8; "
        "plane 1/8 in in 10 ft)"),
    _tc("SLAB-OPENING-EDGE", HALF_IN_FT, None,
        "opening size +1 / -1/2 in"),
    _tc("SAWCUT", 0.0625, None, "sawcut / joint +/-3/4 in"),
    _tc("SLAB-ELEVATION", None, 0.0625, "slab elevation +/-3/4 in"),
    _tc("FORMWORK-PLUMB", INCH_FT, None,
        "lesser of 0.3% of height or 1 in (1 in up to ~83 ft-4 in, then "
        "H/1000)"),
    _tc("CURTAIN-WALL-EMBED", QUARTER_IN_FT, EIGHTH_IN_FT,
        "facade spec practice: +/-1/4 in H, +/-1/8 in V"),
    _tc("ELEVATOR-RAIL", EIGHTH_IN_FT, None, "rail line +/-1/8 in H"),
    _tc("FINISH-GRADE", None, 0.01,
        "blue top practice +/-0.01 ft (agency ladders 0.02-0.03 ft)"),
    _tc("ROUGH-GRADE", 0.1, 0.1,
        "rough pads: 1.0 ft station / 0.1 ft offset / 0.1 ft elev for cuts "
        "under 10 ft; V +/-0.05-0.1 ft"),
    _tc("CURB-GUTTER", 0.02, 0.02,
        "curb & gutter +/-0.02 ft H, +/-0.01-0.02 ft V"),
)}

#: Fallback class when a point declares none and its code has no default.
DEFAULT_CLASS = "GRIDLINE"


def job_tolerances(job: LayoutJob) -> dict:
    """The effective tolerance table: :data:`DEFAULT_TOLERANCES` overlaid
    with the job's own edits (``job.tolerances``, persisted in the
    sidecar)."""
    out = {name: tc for name, tc in DEFAULT_TOLERANCES.items()}
    for name, d in (job.tolerances or {}).items():
        try:
            out[str(name)] = ToleranceClass.from_dict(d)
        except Exception:
            continue
    return out


def set_job_tolerance(job: LayoutJob, tc: ToleranceClass) -> None:
    """Store a job-level tolerance class (override or new); autosaves."""
    job.tolerances[tc.name] = tc.to_dict()
    job._autosave()


def tolerance_for(job: LayoutJob, p: LayoutPoint) -> ToleranceClass:
    """Resolve a point's tolerance class: its own ``tol_class``, else its
    Stitch Code's default, else :data:`DEFAULT_CLASS`."""
    classes = job_tolerances(job)
    name = p.tol_class
    if not name and p.code:
        sc = job_codes(job).get(str(p.code).upper())
        if sc is not None:
            name = sc.default_tol_class
    return classes.get(name) or classes[DEFAULT_CLASS]


# ------------------------------------------------------------ stitch codes --

#: The 8 utility-marking paint colors (plus doctrine: blue keel marks
#: finish-grade hubs).
PAINT_COLORS = {
    "WHITE": "proposed excavation / layout",
    "PINK": "temporary survey",
    "RED": "electric",
    "YELLOW": "gas / oil / steam",
    "ORANGE": "communications",
    "BLUE": "potable water",
    "GREEN": "sewer / storm",
    "PURPLE": "reclaimed water",
}


@dataclass
class StitchCode:
    """One code-library entry.  ``prompts`` is a tuple of typed attribute
    prompts ``(name, kind, unit, joiner)`` — kind int|decimal|choice|text —
    consumed in order by :func:`compose`.  Grammar: ``CODE [size]
    [reference]`` (e.g. ``AB 1.25 C4``); exports always lead the D field
    with the code."""
    code: str
    meaning: str
    default_layer: str = ""
    default_tol_class: str = ""
    paint_color: str = "WHITE"
    prompts: tuple = ()

    def to_dict(self) -> dict:
        return {"code": self.code, "meaning": self.meaning,
                "default_layer": self.default_layer,
                "default_tol_class": self.default_tol_class,
                "paint_color": self.paint_color,
                "prompts": [list(p) for p in self.prompts]}

    @classmethod
    def from_dict(cls, d: dict) -> "StitchCode":
        return cls(code=str(d.get("code", "")).upper(),
                   meaning=str(d.get("meaning", "")),
                   default_layer=str(d.get("default_layer", "")),
                   default_tol_class=str(d.get("default_tol_class", "")),
                   paint_color=str(d.get("paint_color", "WHITE")),
                   prompts=tuple(tuple(p) for p in d.get("prompts") or ()))


def _sc(code, meaning, layer="", tol="", paint="WHITE", prompts=()):
    return StitchCode(code=code, meaning=meaning, default_layer=layer,
                      default_tol_class=tol, paint_color=paint,
                      prompts=prompts)


#: Seed library (app-level defaults; the job stores overrides/additions in
#: ``job.stitch_codes``).
SEED_CODES = {sc.code: sc for sc in (
    _sc("CP", "control point", "Control", "CONTROL", "PINK"),
    _sc("CTRL", "control point", "Control", "CONTROL", "PINK"),
    _sc("BM", "benchmark", "Benchmarks", "CONTROL", "PINK"),
    _sc("WP", "work point", "Work", "GRIDLINE", "WHITE"),
    _sc("GL", "gridline intersection", "Work", "GRIDLINE", "WHITE",
        (("grid cell", "text", "", "-"),)),
    _sc("COL", "column centerline", "Steel", "GRIDLINE", "WHITE",
        (("grid cell", "text", "", "-"),)),
    _sc("AB", "anchor bolt", "Steel", "ANCHOR-M", "WHITE",
        (("diameter", "decimal", "in", " "), ("reference", "text", "", " "))),
    _sc("ABOLT", "anchor bolt", "Steel", "ANCHOR-M", "WHITE",
        (("diameter", "decimal", "in", " "), ("reference", "text", "", " "))),
    _sc("EMB", "embed plate", "Concrete", "EMBED", "WHITE"),
    _sc("SLV", "sleeve", "Plumbing", "SLEEVE", "GREEN",
        (("diameter", "int", "in", "-"),)),
    _sc("HGR", "hanger", "Mechanical", "MEP-HANGER", "WHITE",
        (("rod diameter", "decimal", "in", "-"),)),
    _sc("TRK", "wall track", "User", "TRACK", "WHITE"),
    _sc("PEN", "penetration", "User", "SLEEVE", "WHITE"),
    _sc("CJ", "control joint", "Concrete", "SAWCUT", "WHITE"),
    _sc("FD", "floor drain", "Plumbing", "SLEEVE", "GREEN"),
    _sc("BOX", "electrical box", "Electrical", "SLEEVE", "RED"),
    _sc("UG", "underground utility", "User", "ROUGH-GRADE", "WHITE"),
    _sc("TBC", "top back of curb", "Property", "CURB-GUTTER", "WHITE"),
    _sc("OS", "offset stake", "Work", "GRIDLINE", "PINK",
        (("distance", "decimal", "ft", " "),)),
)}


def job_codes(job: LayoutJob) -> dict:
    """Effective Stitch Code library: :data:`SEED_CODES` overlaid with the
    job's own entries (``job.stitch_codes``)."""
    out = dict(SEED_CODES)
    for code, d in (job.stitch_codes or {}).items():
        try:
            sc = StitchCode.from_dict(d)
            out[sc.code or str(code).upper()] = sc
        except Exception:
            continue
    return out




def compose(code: str, args=(), codes: dict | None = None) -> str:
    """Compose a code-first description: each argument joins with its
    prompt's joiner (``SLV`` + 4 -> ``SLV-4``; ``COL`` + A1 -> ``COL-A1``;
    ``AB`` + 1.25 + C4 -> ``AB 1.25 C4``).  Unknown codes join with
    spaces."""
    code = str(code or "").strip().upper()
    sc = (codes or SEED_CODES).get(code)
    prompts = sc.prompts if sc is not None else ()
    out = code
    for i, arg in enumerate(args):
        joiner = prompts[i][3] if i < len(prompts) and len(prompts[i]) > 3 \
            else " "
        out += f"{joiner}{arg}"
    return out


def apply_code(job: LayoutJob, p: LayoutPoint, code: str, args=()) -> None:
    """Stamp a Stitch Code onto a point: sets ``code``, the composed
    ``desc``, and fills ``tol_class`` (and layer, when still the default)
    from the code's defaults; autosaves."""
    codes = job_codes(job)
    sc = codes.get(str(code).upper())
    p.code = str(code).upper()
    p.desc = compose(code, args, codes)
    if sc is not None:
        if not p.tol_class:
            p.tol_class = sc.default_tol_class
        if p.layer == "Layout" and sc.default_layer:
            if job.layer(sc.default_layer) is None:
                from .fieldstitch import PointLayer
                job.layers.append(PointLayer(sc.default_layer))
            p.layer = sc.default_layer
    job._autosave()


# -------------------------------------------------------------- delta math --



@dataclass
class DeltaRecord:
    """One staked attempt judged against its design point.  Every attempt is
    kept; the latest governs status; the ledger prints them all."""
    point_uid: str = ""
    label: str = ""
    design_n: float = 0.0
    design_e: float = 0.0
    design_z: float | None = None
    staked_n: float = 0.0
    staked_e: float = 0.0
    staked_z: float | None = None
    dn: float = 0.0
    de: float = 0.0
    dz: float | None = None
    hd: float = 0.0
    azimuth: float = 0.0               # miss azimuth, deg from north, 0-360
    tol_h: float | None = None
    tol_v: float | None = None
    tol_class: str = ""
    verdict: str = ""                  # TIGHT | SNUG | LOOSE
    passed: bool = False
    cut_fill: str = ""                 # "C 1.25" | "F 0.10" | "GRADE" | ""
    measured_at: str = "surface"       # surface | projection
    session_id: str = ""
    ts: str = ""
    staked_by: str = ""
    note: str = ""                     # rod/target height note etc.
    via: str = ""                      # pairing rung that matched

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, d: dict) -> "DeltaRecord":
        kw = {}
        for f in fields(cls):
            if f.name in d:
                kw[f.name] = d[f.name]
        return cls(**kw)


def cut_fill(dz: float | None) -> str:
    """Cut/Fill string, DERIVED, never typed separately: dz > 0 -> C, dz < 0
    -> F, |dz| < 0.005 ft -> GRADE, None -> ''.  Hundredths."""
    if dz is None:
        return ""
    if abs(dz) < 0.005:
        return "GRADE"
    return ("C" if dz > 0 else "F") + f" {abs(dz):.2f}"


def deltas(design, staked, tol_h: float | None = None,
           tol_v: float | None = None, **meta) -> DeltaRecord:
    """The single source of truth for delta math (pure function).

    ``design``/``staked`` are ``(N, E, Z-or-None)`` triples.  dN/dE/dZ are
    staked minus design; ``HD = sqrt(dN^2 + dE^2)``; miss azimuth =
    ``atan2(dE, dN)`` normalized 0-360 from north.  PASS iff ``HD <= tol_h``
    AND ``|dZ| <= tol_v`` — ``<=`` passes, computed on UNROUNDED values
    (compare-then-round).  Verdict: TIGHT <= 50% of tolerance, SNUG <= 100%,
    LOOSE beyond (worst axis governs).  An axis with no tolerance (or no
    dZ) is not judged.  Extra keyword args land on the record
    (``session_id=``, ``staked_by=``, ``measured_at=``, ...)."""
    dn = float(staked[0]) - float(design[0])
    de = float(staked[1]) - float(design[1])
    dz = None
    if len(design) > 2 and len(staked) > 2 \
            and design[2] is not None and staked[2] is not None:
        dz = float(staked[2]) - float(design[2])
    hd = math.hypot(dn, de)
    azimuth = math.degrees(math.atan2(de, dn)) % 360.0
    ratios = []
    if tol_h is not None:
        ratios.append(hd / tol_h if tol_h > 0 else math.inf)
    if tol_v is not None and dz is not None:
        ratios.append(abs(dz) / tol_v if tol_v > 0 else math.inf)
    if ratios:
        worst = max(ratios)
        verdict = ("TIGHT" if worst <= 0.5
                   else "SNUG" if worst <= 1.0 else "LOOSE")
        passed = worst <= 1.0
    else:
        verdict, passed = "", False
    return DeltaRecord(
        design_n=float(design[0]), design_e=float(design[1]),
        design_z=None if len(design) < 3 or design[2] is None
        else float(design[2]),
        staked_n=float(staked[0]), staked_e=float(staked[1]),
        staked_z=None if len(staked) < 3 or staked[2] is None
        else float(staked[2]),
        dn=dn, de=de, dz=dz, hd=hd, azimuth=azimuth,
        tol_h=tol_h, tol_v=tol_v, verdict=verdict, passed=passed,
        cut_fill=cut_fill(dz), ts=meta.pop("ts", "") or _now_iso(), **meta)


def two_state(rec: DeltaRecord) -> str:
    """PASS / NEAR / FAIL banding for the summary strip: PASS <= 1x
    tolerance, NEAR 1x-2x, FAIL beyond (worst axis governs)."""
    ratios = []
    if rec.tol_h:
        ratios.append(rec.hd / rec.tol_h)
    if rec.tol_v and rec.dz is not None:
        ratios.append(abs(rec.dz) / rec.tol_v)
    if not ratios:
        return "FAIL"
    worst = max(ratios)
    return "PASS" if worst <= 1.0 else "NEAR" if worst <= 2.0 else "FAIL"


# ------------------------------------------------------------- check shots --

#: Default check-shot acceptance (building work).  Sitework ledger variant
#: 0.02/0.03; structural 0.01/0.01.  Compared, never averaged.
CHECK_TOL_H = 0.01
CHECK_TOL_V = 0.02


@dataclass
class CheckShot:
    """One shot on a known point: observed vs record.  Never overwrites
    control."""
    date_iso: str = ""
    control: str = ""                  # control point label/number
    n: float = 0.0
    e: float = 0.0
    z: float | None = None             # observed
    dn: float = 0.0
    de: float = 0.0
    dz: float | None = None
    hd: float = 0.0
    tol_h: float = CHECK_TOL_H
    tol_v: float = CHECK_TOL_V
    passed: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, d: dict) -> "CheckShot":
        kw = {f.name: d[f.name] for f in fields(cls) if f.name in d}
        return cls(**kw)


def check_shot(job: LayoutJob, control_key, observed,
               tol_h: float = CHECK_TOL_H, tol_v: float = CHECK_TOL_V,
               note: str = "", ts: str = "") -> CheckShot:
    """Judge an observed (N, E, Z-or-None) against a control/known point.
    Pure comparison — the control's coordinates are never touched."""
    p = job._resolve_point(control_key)
    if p is None:
        raise ValueError(f"no such control point: {control_key!r}")
    n, e, z = job.to_world(p)
    dn = float(observed[0]) - n
    de = float(observed[1]) - e
    oz = observed[2] if len(observed) > 2 else None
    dz = None if (z is None or oz is None) else float(oz) - z
    hd = math.hypot(dn, de)
    passed = hd <= tol_h and (dz is None or abs(dz) <= tol_v)
    return CheckShot(date_iso=ts or _now_iso(), control=job.composed(p),
                     n=float(observed[0]), e=float(observed[1]),
                     z=None if oz is None else float(oz),
                     dn=dn, de=de, dz=dz, hd=hd,
                     tol_h=tol_h, tol_v=tol_v, passed=passed, note=note)


def brackets(checks, records) -> list:
    """Group staked records chronologically between consecutive check shots.

    Returns one dict per bracket: ``{"open": CheckShot|None, "close":
    CheckShot|None, "records": [...], "points": [labels], "flagged": bool,
    "unclosed": bool}``.  A failed CLOSING check flags the whole bracket —
    the report prints the exact point ids to re-shoot; re-importing a
    corrected file clears the flag with history retained."""
    checks = sorted(checks, key=lambda c: c.date_iso)
    records = sorted(records, key=lambda r: r.ts)
    out = []
    ri = 0
    prev = None
    for chk in checks:
        grp = []
        while ri < len(records) and records[ri].ts <= chk.date_iso:
            grp.append(records[ri])
            ri += 1
        out.append({"open": prev, "close": chk, "records": grp,
                    "points": [r.label for r in grp],
                    "flagged": not chk.passed, "unclosed": False})
        prev = chk
    tail = records[ri:]
    if tail or not checks:
        out.append({"open": prev, "close": None, "records": tail,
                    "points": [r.label for r in tail],
                    "flagged": False, "unclosed": True})
    return out


# ------------------------------------------------------------- station log --

#: Station-setup methods.  A resection needs 2-4 known targets (2 minimum
#: for angle+distance, 3+ preferred); occupy+backsight needs 1.
STATION_METHODS = ("occupy+backsight", "resection")

#: Per-target residual verdict bands for building work (decimal feet).
STATION_PASS_FT = 0.010
STATION_WARN_FT = 0.020


def station_verdict(residuals) -> str:
    """pass <= 0.010 ft per target, warn <= 0.020, fail above (worst
    target governs); '' when no residuals were logged."""
    vals = [abs(float(r)) for r in (residuals or ())]
    if not vals:
        return ""
    worst = max(vals)
    if worst <= STATION_PASS_FT:
        return "pass"
    return "warn" if worst <= STATION_WARN_FT else "fail"


@dataclass
class StationLog:
    """One instrument-setup session (brief section 1.7).  Every delta
    committed while the session is open carries its ``session_id``, so a
    bad setup quarantines exactly the points it touched — the ledger
    groups by session for the same reason.

    ``prism_constant_mm`` matters more than it looks: a mismatch
    (0 / -30 / -17.5 mm) is a radial bias bigger than nearly every
    tolerance and invisible per-point."""
    session_id: str = ""
    date_iso: str = ""
    method: str = "occupy+backsight"   # occupy+backsight | resection
    occupied: str = ""                 # point label, or 'free' (resection)
    targets: list = None               # backsight/target point labels
    residuals: list = None             # per-target residual, ft, 3 decimals
    expected_ft: float | None = None   # expected check distance
    observed_ft: float | None = None   # observed check distance
    prism_constant_mm: float = 0.0     # 0 / -30 / -17.5
    verdict: str = ""                  # pass | warn | fail
    note: str = ""

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, d: dict) -> "StationLog":
        kw = {f.name: d[f.name] for f in fields(cls) if f.name in d}
        return cls(**kw)


def make_station(session_id: str, method: str = "occupy+backsight", *,
                 occupied: str = "", targets=(), residuals=(),
                 expected_ft=None, observed_ft=None,
                 prism_constant_mm: float = 0.0, note: str = "",
                 ts: str = "") -> StationLog:
    """Validated StationLog: method checked, target counts enforced
    (occupy+backsight: exactly 1 backsight; resection: 2-4 knowns),
    verdict computed from the residuals (:func:`station_verdict`)."""
    method = str(method).strip().lower()
    if method not in STATION_METHODS:
        raise ValueError(f"unknown station method {method!r}; expected one "
                         f"of {STATION_METHODS}")
    targets = [str(t) for t in targets]
    if method == "occupy+backsight" and len(targets) != 1:
        raise ValueError("occupy+backsight takes exactly 1 backsight "
                         f"target (got {len(targets)})")
    if method == "resection" and not 2 <= len(targets) <= 4:
        raise ValueError("a resection needs 2-4 known targets (2 minimum, "
                         f"3+ preferred; got {len(targets)})")
    residuals = [float(r) for r in residuals]
    return StationLog(
        session_id=str(session_id), date_iso=ts or _now_iso(),
        method=method,
        occupied=str(occupied) or ("free" if method == "resection" else ""),
        targets=targets, residuals=residuals,
        expected_ft=None if expected_ft is None else float(expected_ft),
        observed_ft=None if observed_ft is None else float(observed_ft),
        prism_constant_mm=float(prism_constant_mm),
        verdict=station_verdict(residuals), note=str(note))


# ---------------------------------------------------------------- QA store --

class QAStore:
    """Per-plan QA sidecar (``<plan.pdf>.fieldqa.json``): every staked
    attempt per point uid (chronological — kept forever, latest governs),
    the check-shot ledger, and the station log.  Same conventions as the
    other sidecars: versioned JSON, atomic writes, tolerant load."""

    SUFFIX = ".fieldqa.json"

    def __init__(self, pdf_path: str | None = None):
        self.pdf_path = pdf_path
        self.path = (pdf_path + self.SUFFIX) if pdf_path else None
        self.records: dict[str, list] = {}       # uid -> [DeltaRecord...]
        self.checks: list = []                    # [CheckShot...]
        self.stations: list = []                  # [StationLog...]
        if self.path and os.path.exists(self.path):
            self.load()

    # ------------------------------------------------------------ deltas --

    def add_delta(self, rec: DeltaRecord) -> None:
        if not rec.point_uid:
            raise ValueError("DeltaRecord.point_uid must be set")
        self.records.setdefault(rec.point_uid, []).append(rec)
        self._autosave()

    def attempts(self, uid: str) -> list:
        return list(self.records.get(uid, []))

    def latest(self, uid: str) -> DeltaRecord | None:
        recs = self.records.get(uid)
        return recs[-1] if recs else None

    def governing(self) -> list:
        """Latest attempt per point, chronological by timestamp."""
        return sorted((recs[-1] for recs in self.records.values() if recs),
                      key=lambda r: r.ts)

    def all_records(self) -> list:
        """Every attempt, chronological."""
        return sorted((r for recs in self.records.values() for r in recs),
                      key=lambda r: r.ts)

    # ------------------------------------------------------------ checks --

    def add_check(self, cs: CheckShot) -> None:
        self.checks.append(cs)
        self.checks.sort(key=lambda c: c.date_iso)
        self._autosave()

    # ---------------------------------------------------------- stations --

    def add_station(self, log: StationLog) -> None:
        if not log.session_id:
            raise ValueError("StationLog.session_id must be set")
        self.stations.append(log)
        self.stations.sort(key=lambda s: s.date_iso)
        self._autosave()

    def station(self, session_id: str) -> StationLog | None:
        """Latest station log for a session id (deltas link to it through
        their own ``session_id``)."""
        for log in reversed(self.stations):
            if log.session_id == session_id:
                return log
        return None

    def session_uids(self, session_id: str) -> list:
        """Point uids with at least one attempt in this session — the exact
        set a bad setup quarantines."""
        return [uid for uid, recs in self.records.items()
                if any(r.session_id == session_id for r in recs)]

    # ------------------------------------------------------- persistence --

    def to_dict(self) -> dict:
        out = {"version": _VERSION,
               "records": {uid: [r.to_dict() for r in recs]
                           for uid, recs in self.records.items()},
               "checks": [c.to_dict() for c in self.checks]}
        if self.stations:
            out["stations"] = [s.to_dict() for s in self.stations]
        return out

    def save(self, path: str | None = None) -> None:
        path = path or self.path
        if not path:
            raise ValueError("no sidecar path; construct with pdf_path or "
                             "pass an explicit path")
        blob = json.dumps(self.to_dict(), indent=2,
                          sort_keys=True).encode("utf-8")
        _atomic_bytes(blob, path)

    def load(self, path: str | None = None) -> None:
        path = path or self.path
        if not path:
            raise ValueError("no sidecar path; construct with pdf_path or "
                             "pass an explicit path")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        records: dict[str, list] = {}
        for uid, recs in (data.get("records") or {}).items():
            entries = []
            for d in recs if isinstance(recs, list) else []:
                try:
                    entries.append(DeltaRecord.from_dict(d))
                except Exception:
                    continue
            if entries:
                records[str(uid)] = entries
        checks = []
        for d in data.get("checks") or []:
            try:
                checks.append(CheckShot.from_dict(d))
            except Exception:
                continue
        checks.sort(key=lambda c: c.date_iso)
        stations = []
        for d in data.get("stations") or []:
            try:
                stations.append(StationLog.from_dict(d))
            except Exception:
                continue
        stations.sort(key=lambda s: s.date_iso)
        self.records, self.checks = records, checks
        self.stations = stations

    def _autosave(self) -> None:
        if self.path:
            self.save()


# --------------------------------------------------------- as-staked import --

#: Pairing rungs, in ladder order.  ``manual`` is set by the reviewer;
#: ``unmatched`` rows land in the review bucket and are never guessed.

#: Rungs the commit step takes without a human click.
_AUTO_COMMIT_VIAS = ("id", "block", "desc", "manual")

_STAKE_TOKENS = ("STK", "AS")


def pair_asstaked(job: LayoutJob, path: str, *, order: str = "PNEZD",
                  block_offsets=(1000, 10000),
                  proximity_factor: float = 2.0) -> dict:
    """Pair a field CSV of staked shots against the job's DESIGN points and
    return a review table — nothing commits until :func:`commit_asstaked`.

    The pairing ladder (best rung wins, per shot):

    1. ``id`` — exact number match after prefix/suffix strip, zero-fill
       aware ('001' == '1'); NEVER substring ('1' must not match '1001');
    2. ``block`` — design number +/- a block offset (1000 / 10000);
    3. ``desc`` — a STK/AS token in the id suffix or description names the
       design number;
    4. ``proximity`` — nearest design point within ``proximity_factor``
       (default 2x) of its horizontal tolerance — SUGGESTION ONLY: the row
       carries ``confirmed=False`` and commit skips it until a human
       confirms;
    5. ``unmatched`` — review bucket.

    Each row: ``{"shot_id", "n", "e", "z", "desc", "via", "uid", "label",
    "note", "confirmed"}`` (``via`` as in the stamper's mapping review).

    Frame-hash gate: when the file (comment lines or its .tag.txt sidecar)
    declares a frame hash and it mismatches this job's, the report carries
    ``frame_hash_ok=False`` and a LOUD ``frame_warning`` — deltas computed
    across a frame edit measure the edit, not the crew."""
    data = read_point_csv(path, order=order)
    targets = [p for p in job.points
               if p.kind == "DESIGN" and not p.is_witness]
    by_num: dict[int, LayoutPoint] = {}
    for p in targets:
        by_num.setdefault(p.num, p)

    rows = []
    for shot in data["rows"]:
        pre, num, suf = _split_label(shot["id"])
        via, target, note = "unmatched", None, ""
        suf_token = suf.strip().upper().strip("-_.")
        known_suffix = suf in ("", job.suffix) or any(
            suf == p.suffix for p in targets)
        # 1. id exact (zero-fill aware; suffix must be a known one, so
        #    '101STK' falls through to the desc rung)
        if num is not None and known_suffix and num in by_num:
            via, target = "id", by_num[num]
        # 2. block offset — as-staked shots are numbered UP into the block
        #    (shot = design + offset); never downward, or '1' would "block-
        #    match" design 1001
        if target is None and num is not None and known_suffix:
            for off in block_offsets:
                if num - off >= 1 and num - off in by_num:
                    via, target, note = "block", by_num[num - off], f"-{off}"
                    break
        # 3. STK/AS token in the id suffix or the description
        if target is None:
            if suf_token in _STAKE_TOKENS and num is not None \
                    and num in by_num:
                via, target, note = "desc", by_num[num], suf_token
            else:
                tokens = [t for t in re.split(r"[\s,;/]+",
                                              shot["desc"].upper()) if t]
                if any(t in _STAKE_TOKENS for t in tokens):
                    for t in tokens:
                        if t.isdigit() and int(t) in by_num:
                            via, target, note = "desc", by_num[int(t)], t
                            break
        # 4. proximity suggestion (needs a click)
        if target is None and job.cal is not None and targets:
            best, best_d = None, None
            for p in targets:
                n, e, _z = job.to_world(p)
                d = math.hypot(shot["n"] - n, shot["e"] - e)
                if best_d is None or d < best_d:
                    best, best_d = p, d
            if best is not None:
                tol = tolerance_for(job, best)
                if tol.h_ft and best_d <= proximity_factor * tol.h_ft:
                    via, target = "proximity", best
                    note = f"{best_d:.3f} ft away"
        rows.append({
            "shot_id": shot["id"], "n": shot["n"], "e": shot["e"],
            "z": shot["z"], "desc": shot["desc"], "via": via,
            "uid": target.id if target is not None else "",
            "label": job.composed(target) if target is not None else "",
            "note": note,
            "confirmed": via in _AUTO_COMMIT_VIAS,
        })

    declared = data["frame"]
    ok = None if not declared else (declared == frame_hash(job))
    warning = ""
    if ok is False:
        warning = (f"FRAME MISMATCH: file was exported against frame "
                   f"{declared}, this job is {frame_hash(job)} — the "
                   "basepoint/rotation/scale changed since export; deltas "
                   "would measure the frame edit, not the staking. "
                   "Re-export or restore the frame before committing.")
    return {"rows": rows,
            "unmatched": [r for r in rows if r["via"] == "unmatched"],
            "count": len(rows),
            "frame_hash": declared, "frame_hash_ok": ok,
            "frame_warning": warning}


def commit_asstaked(job: LayoutJob, qa: QAStore, rows, *,
                    session_id: str = "", staked_by: str = "",
                    measured_at: str = "surface",
                    verify_on_pass: bool = False, ts: str = "") -> dict:
    """Commit reviewed pairing rows: create the STAKED delta records,
    judge them against each point's tolerance class, and advance statuses.

    Committed rows: ``via`` in id/block/desc/manual, plus proximity rows a
    human ``confirmed``.  Unmatched/unconfirmed rows are skipped and
    reported.  Every attempt is KEPT (the ledger prints them all); the
    latest attempt governs status: PASS -> STAKED (or VERIFIED with
    ``verify_on_pass``), FAIL -> REJECTED (re-arms on the next stake) —
    but a VERIFIED point is never bulk-downgraded; a later failed attempt
    is recorded and reported without touching the status.

    Returns ``{"committed", "passed", "failed", "skipped": [rows],
    "kept_verified": [labels], "records": [DeltaRecord...]}``."""
    committed = passed = failed = 0
    skipped, kept_verified, out_records = [], [], []
    for row in rows:
        uid = row.get("uid") or ""
        via = row.get("via", "unmatched")
        ok_via = via in _AUTO_COMMIT_VIAS or (
            via == "proximity" and row.get("confirmed"))
        if not uid or not ok_via:
            skipped.append(row)
            continue
        p = job.get(uid)
        if p is None:
            skipped.append(row)
            continue
        design = job.to_world(p)
        tol = tolerance_for(job, p)
        rec = deltas(design, (row["n"], row["e"], row.get("z")),
                     tol.h_ft, tol.v_ft,
                     point_uid=p.id, label=job.composed(p),
                     tol_class=tol.name, measured_at=measured_at,
                     session_id=session_id, staked_by=staked_by,
                     via=via, ts=ts)
        qa.add_delta(rec)
        out_records.append(rec)
        committed += 1
        if rec.passed:
            passed += 1
            target_status = "VERIFIED" if verify_on_pass else "STAKED"
        else:
            failed += 1
            target_status = "REJECTED"
        if p.status == "VERIFIED" \
                and STATUS_RANK[target_status] < STATUS_RANK["VERIFIED"]:
            kept_verified.append(job.composed(p))    # never bulk-downgrade
            continue
        job.set_status(p, target_status,
                       note=f"as-staked import (via {via})", by=staked_by)
    return {"committed": committed, "passed": passed, "failed": failed,
            "skipped": skipped, "kept_verified": kept_verified,
            "records": out_records}


# ---------------------------------------------------------- ledger outputs --

#: Compare-then-round rule, printed in the ledger footer.
ROUNDING_FOOTNOTE = (
    "Verdicts are computed on unrounded values, then rounded for display "
    "(compare-then-round): a 0.1251 ft miss displayed as 0.13 against a "
    "displayed 0.13 tolerance is still a FAIL.")


def _fmt(v, nd=3, empty="-"):
    return empty if v is None else f"{v:.{nd}f}"


def _fmt_tol(tc_h, tc_v):
    return f"{_fmt(tc_h)} / {_fmt(tc_v)}"


def summarize(records) -> dict:
    """Footer summary strip over the GOVERNING records: staked / passed /
    near / failed counts, max HD, RMS HD, max |dZ|."""
    recs = list(records)
    bands = [two_state(r) for r in recs]
    hds = [r.hd for r in recs]
    dzs = [abs(r.dz) for r in recs if r.dz is not None]
    return {
        "staked": len(recs),
        "passed": sum(1 for b in bands if b == "PASS"),
        "near": sum(1 for b in bands if b == "NEAR"),
        "failed": sum(1 for b in bands if b == "FAIL"),
        "max_hd": max(hds) if hds else 0.0,
        "rms_hd": math.sqrt(sum(h * h for h in hds) / len(hds))
        if hds else 0.0,
        "max_abs_dz": max(dzs) if dzs else 0.0,
    }


_LEDGER_HEADERS = ["Pt", "Description", "Class", "Design N/E/Z",
                   "Staked N/E/Z", "dN", "dE", "dZ", "HD", "Az", "C/F",
                   "Tol H/V", "Verdict", "At", "Time"]
_LEDGER_WEIGHTS = [5.2, 8.0, 6.0, 8.4, 8.4, 4.6, 4.6, 4.6, 4.6, 4.4, 5.0,
                   6.4, 5.2, 4.6, 8.0]
_MARGIN = 40.0


def _ledger_styles():
    title = ParagraphStyle(
        "LedgerTitle", fontName="Helvetica-Bold", fontSize=20, leading=23,
        textColor=transmittal.ACCENT, spaceAfter=2)
    meta = ParagraphStyle(
        "LedgerMeta", fontName="Helvetica", fontSize=8, leading=10.5,
        textColor=colors.Color(0.24, 0.24, 0.24))
    session = ParagraphStyle(
        "LedgerSession", fontName="Helvetica-Bold", fontSize=9, leading=12,
        textColor=transmittal.ACCENT, spaceBefore=8, spaceAfter=2)
    header = ParagraphStyle(
        "LedgerHeader", fontName="Helvetica-Bold", fontSize=6.8, leading=8.4,
        textColor=colors.white)
    body = ParagraphStyle(
        "LedgerCell", fontName="Helvetica", fontSize=6.8, leading=8.4,
        textColor=colors.Color(0.12, 0.12, 0.12))
    foot = ParagraphStyle(
        "LedgerFoot", fontName="Helvetica-Oblique", fontSize=7, leading=9,
        textColor=colors.Color(0.34, 0.34, 0.34))
    return title, meta, session, header, body, foot


def _ledger_table(rows, header_style, body_style, usable):
    total_w = sum(_LEDGER_WEIGHTS)
    widths = [usable * w / total_w for w in _LEDGER_WEIGHTS]
    data = [[Paragraph(transmittal._cell_text(h), header_style)
             for h in _LEDGER_HEADERS]]
    for row in rows:
        data.append([Paragraph(transmittal._cell_text(c), body_style)
                     for c in row])
    style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), transmittal.ACCENT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("GRID", (0, 1), (-1, -1), 0.35, transmittal._GRIDLINE),
        ("BOX", (0, 0), (-1, -1), 0.7, transmittal._BOXLINE),
    ])
    for i in range(2, len(data), 2):
        style.add("BACKGROUND", (0, i), (-1, i), transmittal._ZEBRA)
    return Table(data, colWidths=widths, repeatRows=1, style=style)


def ledger_pdf(job: LayoutJob, qa: QAStore, out_path: str, *,
               title: str = "AS-STAKED LEDGER", project: str = "",
               area: str = "", crew: str = "", instrument: str = "",
               control_held: str = "", datum: str = "", foot: str = "",
               tolerance_note: str = "", log=print) -> dict:
    """Render the signed staking report (minipdf, landscape letter, same
    visual language as :func:`rfi_stamper.transmittal.table_pdf`).

    Rows are EVERY staked attempt, grouped by Station-Log session id so a
    bad setup visibly quarantines exactly the points it touched.  Header =
    the provenance strip (project, area/sheet, date, crew, instrument
    profile, control held, datum/units INCLUDING which foot, tolerance
    statement, frame hash); footer = the check-shot/bracket ledger, the
    summary strip (staked/passed/near/failed, max HD, RMS HD, max |dZ|),
    dated signature blocks and the compare-then-round footnote.

    Returns ``{"out_path", "rows", "pages", "summary"}``."""
    (title_style, meta_style, session_style, header_style, body_style,
     foot_style) = _ledger_styles()
    usable = landscape(letter)[0] - 2 * _MARGIN

    all_recs = qa.all_records()
    if not foot:
        foot = FOOT_LABELS.get(str(job.units).lower(), job.units)
    prov = [
        ("Project", project or "-"), ("Area / sheet", area or "-"),
        ("Date", _now_iso()), ("Crew", crew or "-"),
        ("Instrument", instrument or "-"),
        ("Control held", control_held or "-"),
        ("Datum / units", f"{datum or 'project datum'}; units: {job.units} "
                          f"— {foot}"),
        ("Tolerances", tolerance_note or
         f"{TOLERANCE_DISCLAIMER} {LAYOUT_BUDGET_NOTE}"),
        ("Frame hash", frame_hash(job)),
    ]
    story: list = [Paragraph(transmittal._cell_text(title), title_style)]
    for k, v in prov:
        story.append(Paragraph(
            f"<b>{transmittal._cell_text(k)}:</b> "
            f"{transmittal._cell_text(v)}", meta_style))
    story.append(HRFlowable(width="100%", thickness=1.5,
                            color=transmittal.ACCENT, spaceBefore=4,
                            spaceAfter=6))

    # ---- per-session point tables (every attempt) -----------------------
    sessions: dict[str, list] = {}
    order: list[str] = []
    for r in all_recs:
        sid = r.session_id or "(no session)"
        if sid not in sessions:
            sessions[sid] = []
            order.append(sid)
        sessions[sid].append(r)
    total_rows = 0
    for sid in order:
        recs = sessions[sid]
        story.append(Paragraph(
            f"Session {transmittal._cell_text(sid)} — {len(recs)} "
            "attempt(s)", session_style))
        rows = []
        for r in recs:
            rows.append([
                r.label, r.note or "", r.tol_class,
                f"{r.design_n:.2f}\n{r.design_e:.2f}\n"
                + _fmt(r.design_z, 2),
                f"{r.staked_n:.2f}\n{r.staked_e:.2f}\n"
                + _fmt(r.staked_z, 2),
                f"{r.dn:.3f}", f"{r.de:.3f}", _fmt(r.dz),
                f"{r.hd:.3f}", f"{r.azimuth:.0f}", r.cut_fill or "-",
                _fmt_tol(r.tol_h, r.tol_v), r.verdict or "-",
                r.measured_at[:4], r.ts,
            ])
        total_rows += len(rows)
        story.append(_ledger_table(rows, header_style, body_style, usable))

    # ---- check-shot / bracket ledger ------------------------------------
    brs = brackets(qa.checks, qa.governing())
    if qa.checks:
        story.append(Paragraph("Check-shot ledger (compared, never "
                               "averaged)", session_style))
        for c in qa.checks:
            story.append(Paragraph(
                f"{transmittal._cell_text(c.date_iso)} — "
                f"{transmittal._cell_text(c.control)}: dN {c.dn:.3f}  "
                f"dE {c.de:.3f}  dZ {_fmt(c.dz)}  HD {c.hd:.3f}  "
                f"(tol {c.tol_h:.3f}/{c.tol_v:.3f})  "
                f"{'PASS' if c.passed else 'FAIL'}"
                + (f" — {transmittal._cell_text(c.note)}" if c.note else ""),
                meta_style))
        for b in brs:
            if b["flagged"] and b["points"]:
                story.append(Paragraph(
                    "<b>BRACKET FLAGGED</b> — closing check failed; "
                    "re-shoot: "
                    + transmittal._cell_text(", ".join(b["points"])),
                    meta_style))

    # ---- summary strip + signatures + footnote ---------------------------
    summary = summarize(qa.governing())
    story.append(Spacer(0, 8))
    story.append(HRFlowable(width="100%", thickness=1.0,
                            color=transmittal.ACCENT, spaceBefore=2,
                            spaceAfter=4))
    story.append(Paragraph(
        f"<b>Summary:</b> staked {summary['staked']} — "
        f"passed {summary['passed']} / near {summary['near']} / "
        f"failed {summary['failed']} — max HD {summary['max_hd']:.3f} ft — "
        f"RMS HD {summary['rms_hd']:.3f} ft — "
        f"max |dZ| {summary['max_abs_dz']:.3f} ft", meta_style))
    story.append(Spacer(0, 14))
    sig = Table(
        [[Paragraph("Party chief: ______________________    "
                    "Date: ____________", meta_style),
          Paragraph("Reviewer: ______________________    "
                    "Date: ____________", meta_style)]],
        colWidths=[usable / 2.0, usable / 2.0])
    sig.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(sig)
    story.append(Spacer(0, 6))
    story.append(Paragraph(transmittal._cell_text(ROUNDING_FOOTNOTE),
                           foot_style))

    holder: dict = {}

    def _canvasmaker(*args, **kwargs):
        return transmittal._NumberedCanvas(
            *args, footer_note=title.strip(), count_holder=holder, **kwargs)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(letter), leftMargin=_MARGIN,
        rightMargin=_MARGIN, topMargin=_MARGIN, bottomMargin=_MARGIN + 18,
        title=title or "As-Staked Ledger")
    doc.build(story, canvasmaker=_canvasmaker)
    transmittal._atomic_write_bytes(buf.getvalue(), out_path)
    pages = int(holder.get("pages", 1))
    log(f"  wrote {out_path} ({total_rows} attempt row(s), {pages} page(s))")
    return {"out_path": out_path, "rows": total_rows, "pages": pages,
            "summary": summary}


#: The _qa.csv companion columns (day-bundle delta file).
QA_CSV_HEADERS = ["point_id", "layer", "status", "design_n", "design_e",
                  "design_z", "dn", "de", "dhz", "dz", "tolerance_ft",
                  "pass", "reason", "staked_by", "staked_at"]


def export_ledger_csv(job: LayoutJob, qa: QAStore, out_path: str,
                      points=None) -> int:
    """The ``_qa.csv`` companion: one row per staked point (the GOVERNING —
    latest — attempt; the PDF ledger prints every attempt).  ``points``
    optionally filters to a package's point set.  ASCII, CRLF, no BOM,
    atomic.  Returns the row count."""
    keep = None if points is None else {p.id for p in points}
    lines = [",".join(QA_CSV_HEADERS)]
    count = 0
    for rec in qa.governing():
        if keep is not None and rec.point_uid not in keep:
            continue
        p = job.get(rec.point_uid)
        layer = p.layer if p is not None else ""
        status = p.status if p is not None else ""
        reason = ""
        if not rec.passed:
            parts = []
            if rec.tol_h is not None and rec.hd > rec.tol_h:
                parts.append(f"HD {rec.hd:.3f} > {rec.tol_h:.3f}")
            if rec.tol_v is not None and rec.dz is not None \
                    and abs(rec.dz) > rec.tol_v:
                parts.append(f"|dZ| {abs(rec.dz):.3f} > {rec.tol_v:.3f}")
            reason = f"{rec.verdict}: " + "; ".join(parts) if parts \
                else rec.verdict
        cells = [
            rec.label, layer, status,
            f"{rec.design_n:.3f}", f"{rec.design_e:.3f}",
            "" if rec.design_z is None else f"{rec.design_z:.3f}",
            f"{rec.dn:.3f}", f"{rec.de:.3f}", f"{rec.hd:.3f}",
            "" if rec.dz is None else f"{rec.dz:.3f}",
            "" if rec.tol_h is None else f"{rec.tol_h:.3f}",
            "1" if rec.passed else "0",
            reason.replace(",", ";"),
            rec.staked_by, rec.ts,
        ]
        lines.append(",".join(cells))
        count += 1
    _atomic_bytes(("\r\n".join(lines) + "\r\n").encode("ascii", "replace"),
                  out_path)
    return count


# ------------------------------------------------------- walking-route sort --

def route_order(coords, start: int = 0) -> list:
    """Visit order (list of indices) over ``[(n, e), ...]``: greedy
    nearest-neighbor from ``start`` plus ONE 2-opt improvement pass.
    Pure function; O(n^2) — fine for crew-day point counts."""
    n = len(coords)
    if n == 0:
        return []
    start = max(0, min(int(start), n - 1))
    unvisited = set(range(n))
    unvisited.discard(start)
    order = [start]
    cur = start
    while unvisited:
        nxt = min(unvisited, key=lambda i: (
            (coords[i][0] - coords[cur][0]) ** 2
            + (coords[i][1] - coords[cur][1]) ** 2))
        unvisited.discard(nxt)
        order.append(nxt)
        cur = nxt

    def dist(a, b):
        return math.hypot(coords[a][0] - coords[b][0],
                          coords[a][1] - coords[b][1])

    # one 2-opt pass: uncross any pair of legs that shortens the walk
    for i in range(1, n - 1):
        for j in range(i + 1, n):
            a, b = order[i - 1], order[i]
            c, d = order[j], order[j + 1] if j + 1 < n else None
            if d is None:
                if dist(a, c) < dist(a, b):     # tail reversal
                    order[i:] = reversed(order[i:])
                continue
            if dist(a, c) + dist(b, d) < dist(a, b) + dist(c, d):
                order[i:j + 1] = reversed(order[i:j + 1])
    return order


def route_length(coords, order) -> float:
    return sum(math.hypot(coords[order[k + 1]][0] - coords[order[k]][0],
                          coords[order[k + 1]][1] - coords[order[k]][1])
               for k in range(len(order) - 1))


def walk_route(job: LayoutJob, points=None, start=None,
               band_ft: float | None = None) -> list:
    """Walking-route proposal: the given points (default: all), reordered
    by greedy nearest-neighbor + one 2-opt pass in world XY from ``start``
    (a point / uid / label / (N, E) tuple; default: the first point).

    Elevation-band aware: with ``band_ft``, points group into bands of
    ``band_ft`` (by elevation; None banded as 0) and bands route lowest
    first, chaining from the previous band's last point — a straight-line
    nearest route through walls/shafts/levels is worse than useless.

    Returns ORDER ONLY — a new list; stored numbers and the job's point
    list are NEVER mutated (crews deeply distrust renumbering)."""
    pts = list(points) if points is not None else list(job.points)
    if not pts:
        return []
    world = [job.to_world(p) for p in pts]
    start_ne = None
    if start is not None:
        if isinstance(start, (tuple, list)):
            start_ne = (float(start[0]), float(start[1]))
        else:
            sp = job._resolve_point(start)
            if sp is None:
                raise ValueError(f"no such start point: {start!r}")
            start_ne = job.to_world(sp)[:2]

    if band_ft:
        bands: dict[int, list] = {}
        for i, (_n, _e, z) in enumerate(world):
            bands.setdefault(int(math.floor((z or 0.0) / band_ft)),
                             []).append(i)
        band_keys = sorted(bands)
    else:
        band_keys = [0]
        bands = {0: list(range(len(pts)))}

    result: list = []
    anchor = start_ne
    for key in band_keys:
        idxs = bands[key]
        coords = [(world[i][0], world[i][1]) for i in idxs]
        if anchor is None:
            s = 0
        else:
            s = min(range(len(idxs)), key=lambda k: (
                (coords[k][0] - anchor[0]) ** 2
                + (coords[k][1] - anchor[1]) ** 2))
        order = route_order(coords, start=s)
        result.extend(pts[idxs[k]] for k in order)
        last = coords[order[-1]]
        anchor = last
    return result


# ==================================================== coordinate upgrades ===
# Brief section 2: the two feet, grid-to-ground, and the control-fit math.

#: International foot: 0.3048 m EXACTLY.
FT_INTL = 0.3048
#: US survey foot: 1200/3937 m EXACTLY (0.30480060960121924 m).  Deprecated
#: 2023-01-01, but 40 states legislated it and legacy control persists
#: indefinitely.  The two differ by 2 ppm — invisible on distances, fatal
#: on absolute state-plane coordinates.
FT_US = 1200.0 / 3937.0

#: Unit name -> meters-per-unit.  "ft" is accepted as the legacy spelling
#: of the international foot.
_UNIT_TO_M = {"m": 1.0, "ift": FT_INTL, "ft": FT_INTL, "usft": FT_US}

#: Human labels stamped into export headers and reports — every deliverable
#: says WHICH foot.
FOOT_LABELS = {
    "ft": "international foot (0.3048 m exactly)",
    "ift": "international foot (0.3048 m exactly)",
    "usft": "US survey foot (1200/3937 m exactly)",
    "m": "meters",
}


def convert_units(v: float, frm: str, to: str) -> float:
    """Convert a length between m / ift / usft THROUGH THE EXACT METER
    VALUE only — never chained approximate ratios (that is how the 2 ppm
    difference stops being exact)."""
    try:
        f = _UNIT_TO_M[str(frm).lower()]
        t = _UNIT_TO_M[str(to).lower()]
    except KeyError:
        bad = frm if str(frm).lower() not in _UNIT_TO_M else to
        raise ValueError(f"unknown unit {bad!r}; expected one of "
                         f"{sorted(_UNIT_TO_M)}") from None
    return float(v) * f / t


#: Tripwire block threshold (brief 2.2): above this shift the import must
#: stop and ask which foot the file is in.
TRIPWIRE_BLOCK_FT = 0.05


def unit_shift_tripwire(n: float, e: float) -> dict:
    """How far a coordinate moves if the wrong foot is assumed: shift =
    2.0e-6 x max(|N|, |E|) (the ift/usft ratio is 0.999998).  ``block``
    is True above 0.05 ft — an untagged file must then present the chooser
    ("these differ by X ft — which foot is this file in?"); below it,
    proceed and log the assumption."""
    shift = 2.0e-6 * max(abs(float(n)), abs(float(e)))
    block = shift > TRIPWIRE_BLOCK_FT
    msg = ""
    if block:
        msg = (f"the two feet differ by {shift:.2f} ft at this coordinate "
               "magnitude — which foot is this file in? (blocked until "
               "chosen; the file carries no unit tag)")
    return {"shift_ft": shift, "block": block, "message": msg}


# ------------------------------------------------------- grid-to-ground -----

#: Mean earth radius for the elevation factor, in feet and meters.
EARTH_R_FT = 20906000.0
EARTH_R_M = 6371000.0


def elevation_factor(h: float, radius: float = EARTH_R_FT) -> float:
    """EF = R / (R + h) — h is the ellipsoid height in the same unit as
    ``radius`` (feet default; pass EARTH_R_M for meters)."""
    return float(radius) / (float(radius) + float(h))


def combined_scale_factor(k: float, ef: float) -> float:
    """CSF = k x EF (projection grid factor x elevation factor)."""
    return float(k) * float(ef)


def grid_to_ground(n: float, e: float, csf: float, origin=(0.0, 0.0)):
    """ground = grid / CSF, scaled about the declared origin — if CSF != 1
    the origin MUST be persisted (job.csf_origin) or the basis is
    irreproducible.  Apply only in the world frame: local building-grid
    coordinates are ALWAYS ground (plans are dimensioned in ground
    truth)."""
    csf = float(csf)
    if csf <= 0:
        raise ValueError(f"bad CSF {csf!r}")
    on, oe = float(origin[0]), float(origin[1])
    return (on + (float(n) - on) / csf, oe + (float(e) - oe) / csf)


def ground_to_grid(n: float, e: float, csf: float, origin=(0.0, 0.0)):
    """grid = ground x CSF about the same origin (inverse of
    :func:`grid_to_ground`)."""
    csf = float(csf)
    if csf <= 0:
        raise ValueError(f"bad CSF {csf!r}")
    on, oe = float(origin[0]), float(origin[1])
    return (on + (float(n) - on) * csf, oe + (float(e) - oe) * csf)


def set_job_csf(job: LayoutJob, csf: float, origin: str = "", *,
                k: float | None = None, ef: float | None = None) -> None:
    """Persist the job CSF (8 decimals), its scaling origin and optionally
    its k/EF parts in the sidecar.  A CSF != 1 with no origin refuses —
    the factor is meaningless without its pivot."""
    csf = round(float(csf), 8)
    if csf <= 0:
        raise ValueError(f"bad CSF {csf!r}")
    if csf != 1.0 and not origin:
        raise ValueError("CSF != 1 requires csf_origin (the point it "
                         "scales about) or the basis is irreproducible")
    job.csf = csf
    job.csf_origin = str(origin)
    parts = {}
    if k is not None:
        parts["k"] = float(k)
    if ef is not None:
        parts["ef"] = float(ef)
    job.csf_parts = parts
    job._autosave()


# -------------------------------------------------- transform fit (2.4) -----

def azimuth_of_plan_north(rotation_deg: float) -> float:
    """The bearing of the plan's up direction in world azimuth terms.

    ``rotation_deg`` is stored CCW-positive (matching ``to_world``'s
    east = vx / north = -vy axes); azimuths run clockwise from north, so
    ``azimuth = (360 - rotation) mod 360``.  Derivation (unit-tested — the
    /Rotate-90 lesson applies verbatim): page-up = (0, -1) flips to
    (east, north) = (0, 1); rotating CCW by theta gives e' = -sin(theta),
    n' = cos(theta); atan2(e', n') = -theta."""
    return (360.0 - float(rotation_deg)) % 360.0


def dms(d: float, m: float = 0.0, s: float = 0.0) -> float:
    """DDD MM' SS" -> decimal degrees (sign rides on the degrees)."""
    sign = -1.0 if float(d) < 0 else 1.0
    return sign * (abs(float(d)) + float(m) / 60.0 + float(s) / 3600.0)


def format_dms(deg: float) -> str:
    """Decimal degrees -> ``DDD-MM'SS"`` (azimuth style, normalized
    0-360)."""
    v = float(deg) % 360.0
    total = int(round(v * 3600.0)) % (360 * 3600)
    d, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{d:03d}-{m:02d}'{s:02d}\""


def fit_from_control(pairs) -> dict:
    """Fit the page->world frame from control pairs (brief 2.4).

    ``pairs`` is ``[((page_x, page_y), (world_n, world_e)), ...]`` — 2
    pairs give the exact similarity (rotation + translation + scale); 3+
    give the 2D Helmert least-squares fit.  The result plugs straight into
    the job frame (``apply_fit``): base at the centroids, rotation
    CCW-positive per ``to_world``'s convention, ``real_per_pt`` the scale.

    Returns::

        {"base_page_xy", "base_world", "rotation_deg", "real_per_pt",
         "azimuth_plan_north_deg",
         "residuals": [{"dn", "de", "hmiss"}...],   # observed - fitted, ft
         "rms_ft", "max_ft"}

    Residuals of a least-squares fit are NOT verification — an independent
    check shot (a point that did not serve in the fit) still gates the
    frame; 2-point fits stay flagged unverified until a third point is
    shot.  Sign traps stack three deep here (page y-down vs survey y-up,
    CCW math vs clockwise azimuth, N-before-E vs (x, y)) — hence numeric
    unit tests, not eyeballing."""
    pairs = [((float(p[0][0]), float(p[0][1])),
              (float(p[1][0]), float(p[1][1]))) for p in pairs]
    if len(pairs) < 2:
        raise ValueError("fit_from_control needs at least 2 control pairs "
                         "(3+ for a least-squares fit with residuals)")
    cx = sum(p[0][0] for p in pairs) / len(pairs)
    cy = sum(p[0][1] for p in pairs) / len(pairs)
    cn = sum(p[1][0] for p in pairs) / len(pairs)
    ce = sum(p[1][1] for p in pairs) / len(pairs)
    # page vector -> survey axes: u = east' = dx, v = north' = -dy
    us = [p[0][0] - cx for p in pairs]
    vs = [-(p[0][1] - cy) for p in pairs]
    nn = [p[1][0] - cn for p in pairs]
    ee = [p[1][1] - ce for p in pairs]
    S = sum(u * u + v * v for u, v in zip(us, vs))
    if S <= 0:
        raise ValueError("control page points coincide — no scale/rotation "
                         "is recoverable")
    # model (to_world convention): n = b*u + a*v ; e = a*u - b*v
    # with a = s*cos(theta), b = s*sin(theta)
    a = sum(v * n + u * e for u, v, n, e in zip(us, vs, nn, ee)) / S
    b = sum(u * n - v * e for u, v, n, e in zip(us, vs, nn, ee)) / S
    scale = math.hypot(a, b)
    if scale <= 0:
        raise ValueError("degenerate fit: zero scale")
    theta = math.degrees(math.atan2(b, a)) % 360.0
    residuals = []
    for u, v, n, e in zip(us, vs, nn, ee):
        fn = b * u + a * v
        fe = a * u - b * v
        dn, de = n - fn, e - fe
        residuals.append({"dn": dn, "de": de, "hmiss": math.hypot(dn, de)})
    hs = [r["hmiss"] for r in residuals]
    return {
        "base_page_xy": (cx, cy), "base_world": (cn, ce),
        "rotation_deg": theta, "real_per_pt": scale,
        "azimuth_plan_north_deg": azimuth_of_plan_north(theta),
        "residuals": residuals,
        "rms_ft": math.sqrt(sum(h * h for h in hs) / len(hs)),
        "max_ft": max(hs),
    }


def apply_fit(job: LayoutJob, fit: dict, unit: str = "ft") -> None:
    """Install a :func:`fit_from_control` result as the job frame.  The
    rotation pivots about the fitted anchor (the centroid), never page
    (0, 0) — a pivot error masquerades as a translation growing with
    distance."""
    from .markups.measure import ScaleCal
    job.base_page_xy = tuple(fit["base_page_xy"])
    job.base_world = tuple(fit["base_world"])
    job.rotation_deg = float(fit["rotation_deg"])
    job.cal = ScaleCal(real_per_pt=float(fit["real_per_pt"]), unit=unit)
    job._autosave()


# ------------------------------------------------------- tape check (2.4) ---

#: Band edges for the offline diagnosis.
TAPE_AGREE_PPM = 1.0
TAPE_FOOT_PPM = (1.0, 4.0)         # the ift/usft ratio is 2 ppm
TAPE_GROSS_PPM = 1000.0


def tape_check(d_computed: float, d_measured: float,
               csf: float | None = None) -> dict:
    """Inverse-between-knowns vs the taped/record distance, with rule-based
    offline diagnosis (brief 2.4 gate 3).

    Returns ``{"ppm", "band", "diagnosis"}``.  Bands: ``agree`` (<= 1 ppm),
    ``foot`` (~2 ppm — smells like survey-foot vs international-foot),
    ``csf`` (misfit matching the job's own CSF — grid coordinates used as
    ground), ``gross`` (> 1000 ppm — wrong point / wrong datum),
    ``unexplained`` otherwise."""
    dc, dm = float(d_computed), float(d_measured)
    if dc <= 0:
        raise ValueError("computed distance must be positive")
    ppm = (dm - dc) / dc * 1e6
    mag = abs(ppm)
    band, why = "unexplained", ""
    if mag > TAPE_GROSS_PPM:
        band = "gross"
        why = ("gross misfit — wrong point, wrong datum, or a typo; "
               "re-identify both monuments before touching the frame")
    else:
        csf_ppm = None
        if csf and float(csf) > 0 and float(csf) != 1.0:
            csf_ppm = (1.0 / float(csf) - 1.0) * 1e6
        if csf_ppm is not None and (
                abs(ppm - csf_ppm) <= max(10.0, 0.3 * abs(csf_ppm))
                or abs(ppm + csf_ppm) <= max(10.0, 0.3 * abs(csf_ppm))):
            band = "csf"
            why = (f"misfit ({ppm:+.0f} ppm) matches the job CSF "
                   f"({float(csf):.8f}) — these are grid coordinates being "
                   "used as ground (or vice versa); check the GROUND/GRID "
                   "badge and the scaling origin")
        elif mag <= TAPE_AGREE_PPM:
            band = "agree"
            why = "distances agree within 1 ppm"
        elif TAPE_FOOT_PPM[0] < mag <= TAPE_FOOT_PPM[1]:
            band = "foot"
            why = (f"{ppm:+.1f} ppm smells like survey-foot vs "
                   "international-foot (the two differ by exactly 2 ppm); "
                   "check which foot the record distance is in")
    return {"ppm": ppm, "band": band, "diagnosis": why}


# ======================================================== error budget ======
# Brief section 5.5: the pre-flight budget meter.

ARCSEC_PER_RAD = 206264.8

#: The target-centering default (1.5 mm) is SPECIFIED for a hand-held pole
#: with an adjusted 8-arcmin vial at up to this reference height; the pole
#: component below charges only the tilt lever ABOVE it.
POLE_REF_M = 1.5

_MM_PER_FT = FT_INTL * 1000.0


def point_sigma(dist_ft: float, arcsec: float, edm_a_mm: float = 2.0,
                edm_b_ppm: float = 2.0, pole_h_ft: float = 6.5,
                vial_arcmin: float = 8.0, instr_center_mm: float = 1.0,
                target_center_mm: float = 1.5) -> dict:
    """One-shot horizontal error budget at a layout distance (brief 5.5).

    Components (all mm):

    * angular: ``e = D * arcsec / 206264.8`` (5" is 0.74 mm at 100 ft);
    * EDM: ``sigma_D = a + b ppm * D`` (2 mm + 2 ppm default — the ppm
      term is noise at building range);
    * instrument centering (plummet over the point, 1.0 mm default);
    * target centering (1.5 mm default: hand-held pole, adjusted 8' vial
      at <= 1.5 m);
    * pole tilt: the residual-tilt lever ABOVE the 1.5 m reference the
      target-centering number already covers —
      ``(h - 1.5 m) * sin(vial/2)`` (a rod at 1.3-1.5 m drops the pole
      term to zero, the ~30-40 percent reduction the doctrine quotes).

    ``sigma_pt = sqrt(sum of squares)``; the 95 percent value is
    ``1.96 * sigma`` — spec sheets quote 1-sigma, and a "1/16-in" claim at
    1-sigma fails about one shot in three, so reports must state the
    confidence level.  With the defaults at 100 ft this reproduces the
    brief's worked example: ~2.9 mm 1-sigma, ~0.22 in at 95 percent — a
    5-arcsec gun cannot honestly certify 1/8 in at 95 percent."""
    dist_mm = float(dist_ft) * _MM_PER_FT
    e_ang = dist_mm * float(arcsec) / ARCSEC_PER_RAD
    e_edm = float(edm_a_mm) + float(edm_b_ppm) * 1e-6 * dist_mm
    lever_mm = max(0.0, float(pole_h_ft) * FT_INTL - POLE_REF_M) * 1000.0
    tilt = math.radians(float(vial_arcmin) / 2.0 / 60.0)
    e_pole = lever_mm * math.sin(tilt)
    sigma = math.sqrt(e_ang ** 2 + e_edm ** 2
                      + float(instr_center_mm) ** 2
                      + float(target_center_mm) ** 2 + e_pole ** 2)
    p95 = 1.96 * sigma
    return {
        "e_ang_mm": e_ang, "e_edm_mm": e_edm,
        "e_instr_mm": float(instr_center_mm),
        "e_target_mm": float(target_center_mm), "e_pole_mm": e_pole,
        "sigma_mm": sigma, "sigma_ft": sigma / _MM_PER_FT,
        "p95_mm": p95, "p95_ft": p95 / _MM_PER_FT,
        "p95_in": p95 / 25.4,
    }


def budget_check(job: LayoutJob, points, setup_xy, profile=None) -> dict:
    """Pre-flight: from a proposed setup position ``(N, E)`` and a gun
    profile (keyword args for :func:`point_sigma`; ``arcsec`` defaults 5),
    color every point whose 95 percent budget exceeds its tolerance class.

    Returns ``{"rows": [{"uid", "label", "dist_ft", "p95_ft", "p95_in",
    "tol_h", "tol_class", "ok"}...], "over": [labels], "ok_count"}`` —
    points with no horizontal tolerance on their class pass trivially."""
    prof = dict(profile or {})
    prof.setdefault("arcsec", 5.0)
    sn, se = float(setup_xy[0]), float(setup_xy[1])
    rows = []
    for p in points:
        n, e, _z = job.to_world(p)
        d = math.hypot(n - sn, e - se)
        sig = point_sigma(d, **prof)
        tol = tolerance_for(job, p)
        ok = tol.h_ft is None or sig["p95_ft"] <= tol.h_ft
        rows.append({"uid": p.id, "label": job.composed(p), "dist_ft": d,
                     "p95_ft": sig["p95_ft"], "p95_in": sig["p95_in"],
                     "tol_h": tol.h_ft, "tol_class": tol.name, "ok": ok})
    return {"rows": rows,
            "over": [r["label"] for r in rows if not r["ok"]],
            "ok_count": sum(1 for r in rows if r["ok"])}


# ======================================================= stake packages =====
# Brief section 6.4: nobody exports the whole job — per area/level/trade
# bundles with a saved route, own export files, and the paper manifest.

#: Printed on every bundle manifest — the morning ritual.
CHECK_SHOT_RITUAL = (
    "Before staking: occupy, then shoot TWO known control points and log "
    "the residuals (pass <= 0.010 ft each). Stake nothing until both "
    "check.")


def _package_json(job: LayoutJob, pts, route_labels, name: str) -> dict:
    cal = job.cal
    layers: dict[str, int] = {}
    for p in pts:
        layers[p.layer] = layers.get(p.layer, 0) + 1
    classes = {}
    for p in pts:
        tc = tolerance_for(job, p)
        classes[tc.name] = tc.to_dict()
    control = []
    for p in job.points:
        if p.kind == "CONTROL":
            n, e, z = job.to_world(p)
            control.append({"label": job.composed(p), "n": n, "e": e,
                            "z": z, "monument": p.monument,
                            "last_checked": p.last_checked,
                            "where": p.where_note})
    return {
        "package": name,
        "created": _now_iso(),
        "points": len(pts),
        "layers": layers,
        "route": list(route_labels),
        "tolerances": classes,
        "tolerance_note": f"{TOLERANCE_DISCLAIMER} {LAYOUT_BUDGET_NOTE}",
        "control": control,
        "units": job.units,
        "foot": FOOT_LABELS.get(str(job.units).lower(), job.units),
        "csf": job.csf, "csf_origin": job.csf_origin,
        "frame": {
            "base_world": list(job.base_world),
            "base_page_xy": list(job.base_page_xy),
            "rotation_deg": job.rotation_deg,
            "real_per_pt": cal.real_per_pt if cal else None,
            "hash": frame_hash(job),
        },
        "ritual": CHECK_SHOT_RITUAL,
    }


def _package_sheet_pdf(job: LayoutJob, out_path: str,
                       name: str, pts, route_labels, pkg: dict) -> None:
    """The one-page paper manifest — a first-class deliverable for the
    clipboard-and-tape crew, doubling as the morning briefing."""
    from .minipdf import canvas as rl_canvas

    W, H = letter
    m = 40.0
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=letter)
    accent = transmittal.ACCENT

    c.setFillColor(accent)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(m, H - m - 8, f"STAKE PACKAGE — {name}")
    c.setFillColorRGB(0.24, 0.24, 0.24)
    c.setFont("Helvetica", 8)
    y = H - m - 24
    csf_note = ("distances are ground (CSF 1.00000000)"
                if job.csf == 1.0 else
                f"CSF {job.csf:.8f} about {job.csf_origin or '?'} — "
                "GROUND = GRID / CSF")
    for line in (
            f"{len(pts)} point(s) — created {pkg['created']}",
            f"Units: {job.units} — {pkg['foot']};  {csf_note}",
            f"Frame hash: {pkg['frame']['hash']}  (as-staked files must "
            "come back against this frame)"):
        c.drawString(m, y, line)
        y -= 11
    c.setFillColor(colors.Color(0.72, 0.18, 0.12))
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(m, y, CHECK_SHOT_RITUAL)
    c.setFillColorRGB(0.24, 0.24, 0.24)
    y -= 8
    c.setStrokeColor(accent)
    c.setLineWidth(1.2)
    c.line(m, y, W - m, y)
    y -= 10

    # ---- plan-thumbnail band -----------------------------------------------
    thumb_w = W - 2 * m
    # The from-scratch writer embeds no raster images (a deliberate scope
    # cut, MINIPDF_PLAN §6), so the sheet always says so honestly — the pin
    # coordinates live in the DXF tier either way.
    c.setStrokeColorRGB(0.6, 0.6, 0.62)
    c.setLineWidth(0.6)
    c.rect(m, y - 60, thumb_w, 60)
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(m + 8, y - 34, "(no plan thumbnail — raster/absent "
                                "plan; pins live in the DXF)")
    y -= 72
    c.setFillColorRGB(0.24, 0.24, 0.24)

    # ---- control table ----------------------------------------------------
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(accent)
    c.drawString(m, y, "Control held")
    c.setFillColorRGB(0.24, 0.24, 0.24)
    y -= 11
    c.setFont("Helvetica", 7.5)
    if pkg["control"]:
        for ctl in pkg["control"][:6]:
            z = "-" if ctl["z"] is None else f"{ctl['z']:.3f}"
            c.drawString(
                m, y,
                f"{ctl['label']}   N {ctl['n']:.3f}   E {ctl['e']:.3f}   "
                f"Z {z}   {ctl['monument'] or '-'}"
                + (f"   ({ctl['where']})" if ctl["where"] else ""))
            y -= 9.5
        if len(pkg["control"]) > 6:
            c.drawString(m, y, f"... {len(pkg['control']) - 6} more in "
                               f"{name}.json")
            y -= 9.5
    else:
        c.drawString(m, y, "(no control points in the job — set control "
                           "before staking)")
        y -= 9.5
    y -= 4

    # ---- layer legend with counts -----------------------------------------
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(accent)
    c.drawString(m, y, "Layers")
    c.setFillColorRGB(0.24, 0.24, 0.24)
    y -= 11
    c.setFont("Helvetica", 7.5)
    x = m
    for lname, count in sorted(pkg["layers"].items()):
        ly = job.layer(lname)
        try:
            c.setFillColor(colors.HexColor(ly.color if ly else "#d84c3f"))
        except ValueError:
            c.setFillColorRGB(0.85, 0.3, 0.25)
        c.rect(x, y - 1.5, 6, 6, stroke=0, fill=1)
        c.setFillColorRGB(0.24, 0.24, 0.24)
        label = f"{lname} ({count})"
        c.drawString(x + 9, y, label)
        x += 9 + c.stringWidth(label, "Helvetica", 7.5) + 14
        if x > W - m - 90:
            x = m
            y -= 10
    y -= 16

    # ---- checkbox route table ---------------------------------------------
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(accent)
    c.drawString(m, y, "Walk order")
    c.setFillColorRGB(0.24, 0.24, 0.24)
    y -= 12
    c.setFont("Helvetica", 7.5)
    by_label = {job.composed(p): p for p in pts}
    max_rows = max(4, int((y - m - 18) // 9.5))
    shown = list(route_labels)[:max_rows]
    for i, lb in enumerate(shown, 1):
        p = by_label.get(lb)
        c.setStrokeColorRGB(0.35, 0.35, 0.38)
        c.setLineWidth(0.7)
        c.rect(m, y - 1.5, 6, 6, stroke=1, fill=0)
        line = f"{i:>3}.  {lb}"
        if p is not None:
            n, e, z = job.to_world(p)
            line += (f"   {p.desc or p.code or p.layer}   N {n:.3f}  "
                     f"E {e:.3f}" + ("" if z is None else f"  Z {z:.3f}"))
        c.drawString(m + 10, y, line[:118])
        y -= 9.5
    if len(route_labels) > len(shown):
        c.drawString(m + 10, y, f"... {len(route_labels) - len(shown)} "
                                f"more — full route in {name}.csv (route "
                                "order) and {0}.json".format(name))
        y -= 9.5

    c.setFont("Helvetica-Oblique", 7)
    c.setFillColorRGB(0.4, 0.4, 0.42)
    c.drawString(m, m - 14, ROUNDING_FOOTNOTE[:150])
    c.showPage()
    c.save()
    transmittal._atomic_write_bytes(buf.getvalue(), out_path)


def export_package(job: LayoutJob, qa: QAStore, out_dir: str, name: str,
                   points, route=None, log=print) -> dict:
    """Write a stake package (day bundle, brief 6.4) into ``out_dir``:

    * ``<name>.csv`` — PNEZD wire CSV in ROUTE ORDER (headerless, ``#``
      comment header with the frame hash so the as-staked round trip
      gates);
    * ``<name>_qa.csv`` — the delta companion for this package's points;
    * ``<name>.json`` — route, tolerance classes in play, control list,
      frame snapshot + hash, units incl. WHICH foot, CSF + origin;
    * ``<name>.dxf`` — the attribute-block tier (plain POINTs included);
    * ``<name>_sheet.pdf`` — the one-page paper manifest (plan thumbnail
      with pins, control table, layer legend with counts, checkbox route
      table, units + CSF statement, check-shot ritual reminder).

    ``route`` is an optional pre-sorted point list (e.g. from
    :func:`walk_route`); default keeps the given order.  Everything stays
    open-format — office/field round-trip never needs licensed software.
    Returns ``{"files": [...], "points": n, "name": name}``."""
    from . import fieldwire
    pts = list(points)
    if not pts:
        raise ValueError("a stake package needs at least one point")
    route_pts = list(route) if route is not None else pts
    if {p.id for p in route_pts} != {p.id for p in pts}:
        raise ValueError("route must be a reordering of the package points")
    route_labels = [job.composed(p) for p in route_pts]
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, name)
    files = []

    csv_path = base + ".csv"
    export_csv_pnezd(job, csv_path, points=route_pts,
                     options={"header": False, "comment_header": True})
    files.append(csv_path)

    qa_path = base + "_qa.csv"
    export_ledger_csv(job, qa, qa_path, points=pts)
    files.append(qa_path)

    pkg = _package_json(job, pts, route_labels, name)
    json_path = base + ".json"
    _atomic_bytes(json.dumps(pkg, indent=2,
                             sort_keys=True).encode("utf-8"), json_path)
    files.append(json_path)

    dxf_path = base + ".dxf"
    fieldwire.export_dxf_blocks(job, dxf_path, points=pts)
    files.append(dxf_path)

    sheet_path = base + "_sheet.pdf"
    _package_sheet_pdf(job, sheet_path, name, pts, route_labels, pkg)
    files.append(sheet_path)

    log(f"  stake package {name!r}: {len(pts)} point(s), "
        f"{len(files)} file(s) in {out_dir}")
    return {"files": files, "points": len(pts), "name": name}
