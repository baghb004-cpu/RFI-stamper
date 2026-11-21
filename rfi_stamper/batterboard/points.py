"""Points: what was read becomes stakeable points and export kits.

Brief section 8.  Harvest generators (pure, proposals only) are run over
the read elements; the proposals go into one Fieldstitch job per level
with five layers: grid, before the pour, after the pour, needs a
dimension, hold.  Export is through the existing kits plus one DXF
background per level on Selvage-clean layer names.

Rules:

* **Names are stable and unique.**  The number of a point comes from the
  append-only names register, keyed by its place: a point that did not
  move keeps its name on every run, a point that moved gets a new one,
  and a number is never reused.
* **Every description carries its evidence**: stage, Stitch Code, element
  id and ONE note.  The note is not typed: it is the state of the
  element (hold with its item, check with its item, nothing to match in
  the second source, dimensioned by the architect, dimension to the
  concrete missing, or per answered RFI with its number).
* **Nothing is laid out from a disputed length.**  A shear wall end whose
  schedule and detail disagree gets no zone end points until the item is
  answered.
* **The frame is declared** in a file beside every point file and inside
  the DXF background.
* Coordinates are snapped to 0.001 ft before they enter the job, so the
  CSV, the DXF and the job hold exactly the same numbers.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

from .. import dxfread, fieldpro, fieldstitch, harvest
from .. import selvage
from ..markups.measure import ScaleCal
from . import slabread
from . import model as M

SNAP_FT = 0.001
SAME_PLACE_FT = 0.01

#: Stitch Codes this feature adds to the job's own code library
CODES = {
    "GL": ("gridline intersection", "GRIDLINE"),
    "WC": ("wall corner at face of concrete", "GRIDLINE"),
    "CC": ("column corner at face of concrete", "GRIDLINE"),
    "XC": ("concrete corner at face of concrete", "GRIDLINE"),
    "RCC": ("round column center", "GRIDLINE"),
    "SWZ": ("shear wall end zone end", "GRIDLINE"),
    "SEC": ("slab edge corner", "GRIDLINE"),
    "RP": ("radius point of a curved slab edge", "GRIDLINE"),
    "SOC": ("slab opening corner", "SLAB-OPENING-EDGE"),
}
FAMILY_OF = {"GL": "grid", "WC": "wall_corner", "CC": "column_corner", "XC": "wall_corner",
             "RCC": "round_column_center", "SWZ": "sw_zone_end", "SEC": "slab_edge_corner",
             "RP": "radius_point", "SOC": "slab_opening_corner"}
STAGE_WORD = {"grid": "GRID", "pre": "PRE", "post": "POST"}
LAYER_COLOR = {M.LAYER_GRID: "#808080", M.LAYER_PRE: "#0000ff", M.LAYER_POST: "#00ff00",
               M.LAYER_DIM: "#ffff00", M.LAYER_HOLD: "#ff0000"}
NOTE_ORDER = ("hold", "dim", "check", "rfi", "nomatch", "arch", "match", "single")


@dataclass
class PointRow:
    """One produced point (the data every plate, legend and file prints)."""
    name: str = ""                 # composed label: "L2-0007"
    num: int = 0
    level: str = ""
    layer: str = ""
    family: str = ""
    code: str = ""
    stage: str = ""                # GRID | PRE | POST
    element: str = ""              # element id ("" for a grid crossing)
    e: float = 0.0
    n: float = 0.0
    note: str = ""                 # the one evidence note
    note_kind: str = ""            # hold | dim | check | rfi | nomatch | arch | match | single | ""
    item: str = ""                 # Red Tag ref behind the note
    desc: str = ""                 # the description as exported
    tol_class: str = ""
    grid: str = ""                 # grid crossing name, for a grid point
    key: str = ""                  # harvest provenance key

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class LevelPoints:
    level: str = ""
    job: object = None             # fieldstitch.LayoutJob
    rows: list = field(default_factory=list)
    merged: int = 0                # coincident proposals merged into one point
    withheld: list = field(default_factory=list)   # [(element id, why)] nothing proposed
    files: list = field(default_factory=list)


def _snap(v: float) -> float:
    return round(float(v) / SNAP_FT) * SNAP_FT


def _job_for(lv: M.Level) -> "fieldstitch.LayoutJob":
    job = fieldstitch.LayoutJob()
    fr = lv.frame
    job.units = "ft"
    job.pad = 4
    job.prefix = f"{lv.code}-"
    job.cal = ScaleCal(real_per_pt=fr.ft_per_pt, unit="ft")
    job.base_page_xy = (0.0, 0.0)
    job.base_world = (fr.ty, fr.tx)                    # (N, E) of page point (0, 0)
    job.rotation_deg = math.degrees(math.atan2(fr.b, fr.a))
    for name in M.POINT_LAYERS:
        selvage.validate_dxf_layer(name)
        job.add_layer(fieldstitch.PointLayer(name=name, color=LAYER_COLOR[name]))
    for code, (meaning, tol) in CODES.items():
        if code not in fieldpro.SEED_CODES:
            job.stitch_codes[code] = fieldpro.StitchCode(
                code=code, meaning=meaning, default_layer=M.LAYER_POST,
                default_tol_class=tol).to_dict()
    return job


def _items_for(reg, el: M.Element | None, e: float, n: float, extra=()) -> list:
    """The Red Tags that bear on a point of this element."""
    out = []
    if el is None:
        return out
    for ref in list(el.items) + [r for x in extra for r in x.items]:
        tag = reg.get(ref)
        if tag is None or not tag.seen or tag in out:
            continue
        if el.kind == M.SLAB_EDGE and ref in el.items:
            # a slab edge is one element with many points: only the points
            # inside the item's box are touched by it
            box = (tag.boxes[0].get("world") if tag.boxes else None)
            if box and not (box[0] <= e <= box[2] and box[1] <= n <= box[3]):
                continue
        out.append(tag)
    return out


def _note(reg, opts: M.Options, el: M.Element | None, tags, corner_dev=None) -> tuple:
    """-> ``(kind, note text, item ref)``: the ONE note of a point."""
    cands = {}
    for tag in tags:
        act = reg.action(tag, opts.hold_policy)
        if tag.key.startswith("XC-ARCH-ENDS") and act == "hold":
            cands.setdefault("dim", (f"NO DIM TO CONC ITEM {tag.ref}", tag.ref))
        elif act == "hold":
            cands.setdefault("hold", (f"HOLD ITEM {tag.ref}", tag.ref))
        elif act == "check":
            cands.setdefault("check", (f"CHECK ITEM {tag.ref}", tag.ref))
        elif tag.category == "answered" and tag.rfi:
            cands.setdefault("rfi", (f"PER RFI {tag.rfi}", tag.ref))
    if el is not None:
        if el.attrs.get("cad") == "missing" or (corner_dev is not None and corner_dev > 2.0):
            cands.setdefault("nomatch", ("NO MATCH 2ND SRC", ""))
        if el.attrs.get("arch") == "string":
            cands.setdefault("arch", ("DIM BY ARCH", ""))
        if el.attrs.get("cad") == "match":
            cands.setdefault("match", ("MATCHES CAD BG", ""))
        if el.single_source:
            cands.setdefault("single", ("SINGLE SRC", ""))
    for kind in NOTE_ORDER:
        if kind in cands:
            return (kind,) + cands[kind]
    return "", "", ""


def build_level(lv: M.Level, reg, opts: M.Options, names) -> LevelPoints:
    """Harvest one level into a Fieldstitch job."""
    out = LevelPoints(level=lv.code)
    if lv.blocked or lv.frame is None:
        return out
    job = out.job = _job_for(lv)
    by_id = {e.id: e for e in lv.elements}
    props = []                                             # (proposal, element, extras)

    # ---- grid (the Gridiron) ------------------------------------------------ #
    xs = [(g.name, g.world_a, g.world_b) for g in lv.grid.family("x")]
    ys = [(g.name, g.world_a, g.world_b) for g in lv.grid.family("y")]
    grid_props = [(pr, None, ()) for pr in harvest.gridiron(xs, ys, layer=M.LAYER_GRID,
                                                            code="GL")]

    # ---- concrete corners, as polygons ---------------------------------------- #
    conc = lv.of(M.WALL, M.COLUMN, M.CONCRETE)
    code_of = {M.WALL: "WC", M.COLUMN: "CC", M.CONCRETE: "XC"}
    ends = lv.of(M.SW_END)
    for pr in harvest.concrete_corners([(e.id, e.poly) for e in conc]):
        el = by_id[pr["provenance"]["params"]["owner"]]
        pr["code"] = code_of[el.kind]
        # a corner on a shear wall end face also answers to that end's items
        extra = [s for s in ends if s.attrs.get("host") == el.id
                 and min(math.hypot(pr["e"] - p[0], pr["n"] - p[1])
                         for p in (s.attrs["face_a"], s.attrs["face_b"])) <= 1.0 * M.IN]
        props.append((pr, el, extra))
    for pr in harvest.round_centers([(e.id, e.center, e.radius_ft)
                                     for e in lv.of(M.ROUND_COLUMN)]):
        props.append((pr, by_id[pr["provenance"]["params"]["owner"]], ()))

    # ---- shear wall end zone ends: only from an undisputed length ------------- #
    ok_ends = []
    for s in ends:
        z = s.attrs.get("zone_length_ft")
        if s.attrs.get("zone_disputed"):
            out.withheld.append((s.id, "zone length disputed between the schedule and "
                                       "the detail"))
        elif not z:
            out.withheld.append((s.id, "the schedule gives this mark nothing for a zone length"))
        else:
            ok_ends.append((s.id, s.attrs["face_a"], s.attrs["face_b"], s.attrs["inward"], z))
    for pr in harvest.zone_ends(ok_ends):
        props.append((pr, by_id[pr["provenance"]["params"]["owner"]], ()))

    # ---- slab ------------------------------------------------------------------ #
    for e in lv.of(M.SLAB_EDGE):
        pts = slabread.edge_points(e)
        for pr in harvest.slab_edge_points(e.id, pts["corners"], pts["radius_points"]):
            props.append((pr, e, ()))
    for pr in harvest.opening_corners([(e.id, e.poly) for e in lv.of(M.SLAB_OPENING)]):
        props.append((pr, by_id[pr["provenance"]["params"]["owner"]], ()))

    # ---- proposals -> points ----------------------------------------------------- #
    # Elements first, the grid last: when a grid crossing falls on a point an
    # element already owns (a round column centered on the grid), it is ONE
    # physical point and the element's evidence must not be hidden behind a
    # plain grid point.  The grid name rides along on that point instead.
    taken: list = []
    for pr, el, extra in props + grid_props:
        code = pr["code"]
        family = FAMILY_OF[code]
        stage = opts.stage_of.get(family, "post")
        if stage != "grid" and opts.stage in ("pre", "post") and stage != opts.stage:
            continue
        e, n = _snap(pr["e"]), _snap(pr["n"])
        # one physical point per place AND stage: a slab corner (before the
        # pour) and a column corner (after the pour) at the same place are
        # two points, each on its own layer; a grid crossing joins whatever
        # is already there
        hit = next((t for t in taken
                    if math.hypot(e - t[0], n - t[1]) <= SAME_PLACE_FT
                    and (family == "grid" or t[2].stage == STAGE_WORD[stage])), None)
        if hit is not None:
            out.merged += 1
            if family == "grid":
                row, fpt = hit[2], hit[3]
                row.grid = pr.get("name") or ""
                parts = row.desc.split(" ")
                row.desc = " ".join(parts[:3] + [row.grid] + parts[3:])
                fpt.desc = row.desc
            continue
        tags = _items_for(reg, el, e, n, extra)
        if el is not None and el.kind == M.SLAB_EDGE and "no heavy" in \
                str(pr["provenance"]["params"].get("note", "")) and not tags:
            tags = [t for t in (reg.get(r) for r in el.items) if t is not None and t.seen]
        kind, note, item = _note(reg, opts, el, tags)
        layer = {"hold": M.LAYER_HOLD, "dim": M.LAYER_DIM}.get(
            kind, {"grid": M.LAYER_GRID, "pre": M.LAYER_PRE}.get(stage, M.LAYER_POST))
        grid_name = pr.get("name") or "" if family == "grid" else ""
        target = el.id if el is not None else grid_name
        desc = " ".join(x for x in (STAGE_WORD[stage], code, target, note) if x)
        num = names.number(lv.code, "pt:" + stage, e, n, start=1, tol=SAME_PLACE_FT,
                           counter="pt")
        x, y = lv.frame.to_page(e, n)
        tol = CODES[code][1]
        pt = job.add_point(lv.struct.page, x, y, num=num, layer=layer, desc=desc, code=code,
                           tol_class=tol, elev=None, provenance=dict(pr["provenance"]))
        row = PointRow(
            name=job.composed(pt), num=num, level=lv.code, layer=layer, family=family,
            code=code, stage=STAGE_WORD[stage], element=el.id if el is not None else "",
            e=e, n=n, note=note, note_kind=kind, item=item, desc=desc, tol_class=tol,
            grid=grid_name, key=pr["provenance"]["key"])
        out.rows.append(row)
        taken.append((e, n, row, pt))
    out.rows.sort(key=lambda r: r.num)
    return out


def build_points(rr, reg, opts: M.Options, names) -> dict:
    """-> ``{level code: LevelPoints}`` for every readable level."""
    return {lv.code: build_level(lv, reg, opts, names) for lv in rr.levels}


# ------------------------------------------------------------------ export --

def frame_text(lv: M.Level, n_points: int) -> str:
    """The frame declaration written beside every point file."""
    return (f"Frame: {lv.frame.label}\n"
            f"Level: {lv.name}\n"
            f"Units: decimal feet (Northing, Easting). Elevations are not laid out; "
            f"the Z column is not a design elevation.\n"
            f"Points: {n_points}\n")


def export_level(lp: LevelPoints, lv: M.Level, out_dir: str, opts: M.Options) -> list:
    """Write the kits, the job, the frame declaration and the DXF
    background for one level.  Returns the files written."""
    if lp.job is None or not lp.rows:
        return []
    os.makedirs(out_dir, exist_ok=True)
    stem = f"layout_{lv.code}"
    files = []
    for kit in opts.kits or ["bowline"]:
        got = fieldstitch.export_kit(lp.job, out_dir, kit, stem)
        files.extend(f for f in got["files"] if f not in files)
    job_path = os.path.join(out_dir, f"{stem}.stitch.json")
    fieldstitch.export_job_json(lp.job, job_path)
    lp.job.path = None                                   # the job stays an export, not a live sidecar
    files.append(job_path)
    fpath = os.path.join(out_dir, f"{stem}_FRAME.txt")
    with open(fpath, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(frame_text(lv, len(lp.rows)))
    files.append(fpath)
    bpath = os.path.join(out_dir, f"background_{lv.code}.dxf")
    write_background(lv, lp, bpath)
    files.append(bpath)
    lp.files = files
    return files


def background_shapes(lv: M.Level, lp: LevelPoints) -> list:
    """The level as DXF shapes in the declared frame (decimal feet), on
    Selvage-clean layer names; every produced point rides along."""
    L = selvage.dxf_layer_name
    out = []
    for g in lv.grid.lines:
        out.append({"layer": L("BB-GRIDLINES"), "kind": "line", "pts": [g.world_a, g.world_b]})
        out.append({"layer": L("BB-GRIDLINES"), "kind": "text", "at": g.world_a,
                    "text": g.name, "height": 1.0})
    layer_of = {M.WALL: "BB-CONCRETE", M.COLUMN: "BB-CONCRETE", M.CONCRETE: "BB-CONCRETE",
                M.SLAB_OPENING: "BB-SLAB-OPENING", M.WALL_OPENING: "BB-WALL-OPENING",
                M.MASONRY: "BB-MASONRY", M.OVERLAY_OPENING: "BB-OVERLAY-APPROX"}
    for e in lv.elements:
        if e.kind in layer_of and len(e.poly) >= 3:
            name = layer_of[e.kind]
            if e.attrs.get("cad_only"):
                name = "BB-CONCRETE-CAD-ONLY"
            out.append({"layer": L(name), "kind": "poly", "pts": list(e.poly), "closed": True})
        elif e.kind == M.ROUND_COLUMN:
            out.append({"layer": L("BB-CONCRETE"), "kind": "circle", "center": e.center,
                        "radius": e.radius_ft})
        elif e.kind == M.SLAB_EDGE:
            for s in e.attrs.get("segments", []):
                name = "BB-SLAB-EDGE" if s["kind"] != "unconfirmed" else "BB-SLAB-EDGE-UNCONFIRMED"
                if s["kind"] in ("arc", "curve"):
                    out.append({"layer": L(name), "kind": "poly", "pts": s["pts"],
                                "closed": False})
                else:
                    out.append({"layer": L(name), "kind": "line", "pts": [s["a"], s["b"]]})
    for r in lp.rows:
        out.append({"layer": L(r.layer), "kind": "point", "at": (r.e, r.n), "text": r.name})
    x0 = min((g.world_a[0] for g in lv.grid.lines), default=0.0)
    y0 = min((g.world_a[1] for g in lv.grid.lines), default=0.0)
    out.append({"layer": L("BB-FRAME"), "kind": "text", "at": (x0, y0 - 6.0),
                "text": lv.frame.label, "height": 1.0})
    return out


def write_background(lv: M.Level, lp: LevelPoints, path: str) -> int:
    return dxfread.write_background(path, background_shapes(lv, lp), units="ft")


# -------------------------------------------------------------- round trip --

def round_trip(lp: LevelPoints, files) -> list:
    """Self-check 2: the CSV, the DXF and the job hold the same points
    with the same names and coordinates; names are unique.  Returns a list
    of plain problem lines (empty = it holds)."""
    problems = []
    want = {r.name: (round(r.n, 3), round(r.e, 3)) for r in lp.rows}
    if len(want) != len(lp.rows):
        problems.append("point names are not unique")
    seen_any = False
    for f in files:
        low = f.lower()
        got = None
        if low.endswith(".csv"):
            data = fieldstitch.read_point_csv(f)
            if data["bad"]:
                problems.append(f"{os.path.basename(f)}: {len(data['bad'])} unreadable row(s)")
            got = {str(r["id"]): (round(float(r["n"]), 3), round(float(r["e"]), 3))
                   for r in data["rows"]}
        elif low.endswith(".dxf"):
            # both DXF writers put a point's label half a text height up and
            # to the right of it: undo exactly that to find each label's point
            doc = dxfread.read(f)
            pts = [e.pts[0] for e in doc.entities if e.kind == "POINT"]
            got = {}
            for t in doc.entities:
                if t.kind != "TEXT" or t.text not in want or not pts:
                    continue
                ax = t.pts[0][0] - 0.5 * t.height
                ay = t.pts[0][1] - 0.5 * t.height
                near = min(pts, key=lambda q: math.hypot(q[0] - ax, q[1] - ay))
                if math.hypot(near[0] - ax, near[1] - ay) <= 0.002:
                    got[t.text] = (round(near[1], 3), round(near[0], 3))
        elif low.endswith(".stitch.json") or low.endswith(".json"):
            job = fieldstitch.LayoutJob()
            job.load(f)
            job.path = None
            got = {}
            for p in job.points:
                n, e, _z = job.to_world(p)
                got[job.composed(p)] = (round(n, 3), round(e, 3))
        if got is None:
            continue
        seen_any = True
        base = os.path.basename(f)
        if set(got) != set(want):
            miss = sorted(set(want) - set(got))[:3]
            extra = sorted(set(got) - set(want))[:3]
            problems.append(f"{base}: point names differ (missing {miss}, extra {extra})")
            continue
        off = [k for k in want if abs(got[k][0] - want[k][0]) > 0.00051
               or abs(got[k][1] - want[k][1]) > 0.00051]
        if off:
            problems.append(f"{base}: {len(off)} point(s) carry different coordinates "
                            f"(first {off[0]}: {got[off[0]]} against {want[off[0]]})")
    if not seen_any and lp.rows:
        problems.append("no point file was written to check")
    return problems
