"""The plan reader: a classified drop in, read levels out.  Brief section 5.

One :class:`ReadResult` holds everything the later stages work from:
levels (scale, grid, frame, elements with provenance), general notes, the
shear wall schedule, CAD backgrounds, RFI responses, the Sightline's
per-page records, and the decisions a gate could not make alone.

The rules that keep this honest:

* every vector element comes in through the Sightline; a page whose
  vectors do not register to its render is not read at all;
* a level with no witnessed scale, no grid, a grid that disagrees with
  its printed dimensions or the CAD background by more than 1/4 in., or
  two plausible concrete fill classes is BLOCKED, with the reason, and
  nothing is laid out on it;
* element ids come from the append-only names register, so they are the
  same on every run;
* nothing is guessed: what was not read is said, in ``remarks``.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

import fitz

from .. import geom2d as g2
from .. import setscale
from .. import sightline as sl
from . import archdims, cadread, concrete, dimread, gridread, overlay, slabread
from . import model as M
from . import notes as notes_mod
from . import schedule as schedule_mod
from .intake import level_code, level_order
from .names import Names

REGION_PAD_FT = 12.0
HIDDEN_JOIN_PT = 14.0
KIND_ORDER = [M.SLAB_EDGE, M.WALL, M.COLUMN, M.ROUND_COLUMN, M.CONCRETE, M.WALL_OPENING,
              M.SLAB_OPENING, M.SW_END, M.PENETRATION, M.OVERLAY_OPENING, M.MASONRY]


@dataclass
class ReadResult:
    refs: list = field(default_factory=list)           # every SheetRef of the drop
    levels: list = field(default_factory=list)         # [Level], low to high
    notes: list = field(default_factory=list)          # every general note read
    schedules: list = field(default_factory=list)      # [Schedule]
    rfis: list = field(default_factory=list)           # [SheetRef] RFI responses
    cads: dict = field(default_factory=dict)           # level code -> CadBackground
    sight: list = field(default_factory=list)          # [{"sheet", "file", "page", "record"}]
    asks: list = field(default_factory=list)           # decisions a gate could not make
    remarks: list = field(default_factory=list)        # plain-words reader lines
    unknown: list = field(default_factory=list)        # SheetRefs no rule classified
    frame_kind: str = "grid"
    frame_label: str = ""

    def level(self, code: str):
        for lv in self.levels:
            if lv.code == code:
                return lv
        return None

    def schedule_row(self, mark: str):
        for s in self.schedules:
            r = s.row(mark)
            if r is not None:
                return r, s
        return None, None

    def marks(self) -> list:
        out = []
        for s in self.schedules:
            out.extend(m for m in s.marks() if m not in out)
        return out

    def sheets(self) -> set:
        return {r.sheet for r in self.refs if r.sheet}


def _region(grid: M.Grid, ppf: float) -> tuple:
    pts = list(grid.page_intersections().values())
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    pad = REGION_PAD_FT * ppf
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def _perimeter(grid: M.Grid) -> float:
    pts = list(grid.page_intersections().values())
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return 2.0 * ((max(xs) - min(xs)) + (max(ys) - min(ys)))


def _scale(page, ref: M.SheetRef, opts: M.Options) -> tuple:
    """-> ``(pt_per_ft | None, label, why)``."""
    manual = opts.manual_scale.get(ref.sheet) or opts.manual_scale.get(ref.name)
    try:
        v = setscale.sheet_verdict(page)
    except Exception as e:   # noqa: BLE001
        v = {"status": "REFUSED", "reasons": [f"the scale check could not run: {e}"]}
    if v.get("status") == "PASS":
        return float(v["pt_per_ft"]), v.get("label") or "", "; ".join(v.get("reasons") or [])
    if manual:
        return float(manual), setscale.scale_label(float(manual)), \
            "scale set by the user (the sheet did not verify one on its own)"
    return None, "", "; ".join(v.get("reasons") or ["no scale could be verified"])


def _hidden_clusters(record, region, frame: M.Frame) -> list:
    """Group what the Sightline dropped inside the plan region into
    places: ``[{"page_bbox", "world_bbox", "strokes", "fills", "heavy"}]``."""
    items = [d for d in record.dropped
             if d["what"] in ("stroke", "fill")
             and region[0] <= (d["bbox"][0] + d["bbox"][2]) / 2 <= region[2]
             and region[1] <= (d["bbox"][1] + d["bbox"][3]) / 2 <= region[3]]
    clusters: list = []
    for d in items:
        bb = list(d["bbox"])
        hit = None
        for c in clusters:
            cb = c["page_bbox"]
            if not (bb[2] < cb[0] - HIDDEN_JOIN_PT or cb[2] < bb[0] - HIDDEN_JOIN_PT
                    or bb[3] < cb[1] - HIDDEN_JOIN_PT or cb[3] < bb[1] - HIDDEN_JOIN_PT):
                hit = c
                break
        if hit is None:
            hit = {"page_bbox": bb, "strokes": 0, "fills": 0, "max_width": 0.0}
            clusters.append(hit)
        else:
            cb = hit["page_bbox"]
            hit["page_bbox"] = [min(cb[0], bb[0]), min(cb[1], bb[1]),
                                max(cb[2], bb[2]), max(cb[3], bb[3])]
        hit["strokes" if d["what"] == "stroke" else "fills"] += 1
        hit["max_width"] = max(hit["max_width"], float(d.get("width", 0.0)))
    for c in clusters:
        x0, y0, x1, y1 = c["page_bbox"]
        w = [frame.to_world(x0, y0), frame.to_world(x1, y1)]
        c["world_bbox"] = [min(w[0][0], w[1][0]), min(w[0][1], w[1][1]),
                           max(w[0][0], w[1][0]), max(w[0][1], w[1][1])]
    return clusters


def _number(elements, lv: M.Level, names: Names) -> None:
    """Stable ids: ``L2-W-003`` from the names register."""
    def key(e):
        c = e.centroid()
        return (KIND_ORDER.index(e.kind) if e.kind in KIND_ORDER else 99,
                -round(c[1], 1), round(c[0], 1))
    for e in sorted(elements, key=key):
        if e.id:
            continue
        c = e.centroid()
        n = names.number(lv.code, "el:" + e.kind, c[0], c[1], tol=0.25)
        e.id = f"{lv.code}-{M.KIND_CODE.get(e.kind, 'X')}-{n:03d}"
        e.level = lv.code


def read_drop(refs, opts: M.Options | None = None, names: Names | None = None,
              log=lambda *_: None) -> ReadResult:
    """Read every classified page of a drop."""
    opts = opts or M.Options()
    names = names or Names()
    out = ReadResult(refs=list(refs))
    out.unknown = [r for r in refs if r.cls == M.PG_UNKNOWN]
    out.rfis = [r for r in refs if r.cls == M.PG_RFI]
    docs: dict = {}

    def page_of(ref):
        if ref.file not in docs:
            docs[ref.file] = fitz.open(ref.file)
        return docs[ref.file][ref.page - 1]

    try:
        # ---- text sheets --------------------------------------------------- #
        for r in refs:
            if r.cls == M.PG_NOTES:
                got = notes_mod.read_notes(page_of(r), r.sheet, r.page)
                for n in got:
                    n.file = r.name if hasattr(n, "file") else None
                out.notes.extend(got)
                log(f"  {r.sheet}: {len(got)} general notes read")
            elif r.cls == M.PG_SCHEDULE:
                s = schedule_mod.read_schedule(page_of(r), r.sheet, r.page)
                out.schedules.append(s)
                out.remarks.extend(f"{r.sheet}: {p}" for p in s.problems)
                log(f"  {r.sheet}: schedule rows {s.marks()}")
        # ---- CAD backgrounds ----------------------------------------------- #
        for r in refs:
            if r.cls != M.PG_CAD:
                continue
            if not r.level:
                out.remarks.append(f"{r.name}: the CAD background names no level and "
                                   "was not used (assign its level to use it)")
                continue
            try:
                bg = cadread.read_cad(r.file, units=opts.cad_units or None)
            except Exception as e:   # noqa: BLE001
                out.remarks.append(f"{r.name}: {e}")
                continue
            out.remarks.extend(bg.problems)
            if bg.feet:
                out.cads[level_code(r.level)] = bg
        # ---- levels ---------------------------------------------------------- #
        by_level: dict = {}
        for r in refs:
            if r.cls == M.PG_STRUCT and r.level and not r.prior:
                by_level.setdefault(level_code(r.level), []).append(r)
        if opts.control:
            out.frame_kind = "control"
        elif any(len(bg.grid.family("x")) >= 2 and len(bg.grid.family("y")) >= 2
                 for bg in out.cads.values()):
            out.frame_kind = "cad"
        for code, rs in sorted(by_level.items(), key=lambda kv: level_order(kv[1][0].level)):
            rs.sort(key=lambda r: r.sheet)
            ref = rs[0]
            lv = M.Level(name=ref.level, code=code, order=level_order(ref.level), struct=ref)
            for extra in rs[1:]:
                out.remarks.append(
                    f"{extra.sheet}: a second structural plan of {ref.level}; only "
                    f"{ref.sheet} was read")
            if "does not order them" in ref.why:
                lv.blocked.append(f"{ref.sheet}: " + ref.why.split("; ")[-1])
            lv.prior = next((r for r in refs if r.cls == M.PG_STRUCT and r.prior
                             and level_code(r.level or "") == code), None)
            lv.arch = next((r for r in refs if r.cls == M.PG_ARCH and not r.prior
                            and level_code(r.level or "") == code), None)
            lv.shops = [r for r in refs if r.cls == M.PG_SHOP
                        and level_code(r.level or "") == code]
            cad_ref = next((r for r in refs if r.cls == M.PG_CAD
                            and level_code(r.level or "") == code), None)
            lv.cad = cad_ref if code in out.cads else None
            _read_struct(lv, page_of(ref), out, opts, names, log)
            if not lv.blocked:
                if lv.arch is not None:
                    _read_arch(lv, page_of(lv.arch), out, opts, log)
                if opts.overlays:
                    for sr in lv.shops:
                        _read_shop(lv, sr, page_of(sr), out, opts, names, log)
                if opts.masonry and code in out.cads:
                    _add_masonry(lv, out.cads[code], out, names)
            out.levels.append(lv)
        if out.levels and not out.frame_label:
            fr = next((lv.frame for lv in out.levels if lv.frame is not None), None)
            if fr is not None:
                out.frame_label = fr.label
                out.frame_kind = fr.kind
        if opts.levels:
            keep = {str(c).upper() for c in opts.levels}
            for lv in out.levels:
                if lv.code not in keep and "not selected" not in " ".join(lv.blocked):
                    lv.notes.append("level not selected for this run")
            out.levels = [lv for lv in out.levels if lv.code in keep]
    finally:
        for d in docs.values():
            d.close()
    return out


# ------------------------------------------------------------ one level --

def _read_struct(lv: M.Level, page, out: ReadResult, opts: M.Options, names: Names,
                 log) -> None:
    ref = lv.struct
    res = sl.gate(page, dpi=opts.dpi)
    out.sight.append({"sheet": ref.sheet, "file": ref.name, "page": ref.page,
                      "record": res.record})
    if not res.registered:
        lv.blocked.append(f"{ref.sheet}: " + (res.record.notes[0] if res.record.notes
                                             else "the vectors do not register to the render"))
        return
    ppf, label, why = _scale(page, ref, opts)
    if ppf is None:
        lv.blocked.append(f"{ref.sheet}: scale not verified ({why})")
        return
    lv.pt_per_ft, lv.scale_label = ppf, label
    lv.notes.append(f"scale {label}: {why}")
    words = sl.words(page)
    lines = sl.text_lines(page)
    grid, problems, axes = gridread.read_grid(res.strokes, words, ppf)
    lv.grid = grid
    lv.notes.extend(problems)
    if any(p.startswith(("grid not read", "no grid bubbles")) for p in problems):
        lv.blocked.append(f"{ref.sheet}: " + "; ".join(problems))
        return
    dims = dimread.dimension_strings(words, res.strokes)
    grid.spacing_checks = gridread.spacing_checks(grid, dims, axes)
    bad = [c for c in grid.spacing_checks if abs(c["delta_ft"]) > M.GRID_BLOCK_FT]
    if bad:
        c = bad[0]
        lv.blocked.append(
            f"{ref.sheet}: grid {c['a']} to {c['b']} measures {M.fmt_ftin(c['measured_ft'])} "
            f"but the printed grid dimension reads {c['text']} (over 1/4 in. apart)")
        return
    # ---- the frame --------------------------------------------------------- #
    local = gridread.local_frame(grid, axes, ppf)
    frame = local
    if out.frame_kind == "control":
        targets = {}
        for c in opts.control:
            try:
                targets[str(c["at"]).upper()] = (float(c["e"]), float(c["n"]))
            except (KeyError, TypeError, ValueError):
                lv.blocked.append("a control entry needs 'at' (a grid crossing such as A/1), "
                                  "'n' and 'e'")
                return
        frame, info = gridread.frame_from_pairs(
            grid, targets, "control",
            "Real-world coordinates from user-supplied control at "
            + ", ".join(sorted(targets)) + ".", ppf)
        if frame is None:
            lv.blocked.append(f"{ref.sheet}: control could not set the frame ({info['problem']})")
            return
        if info["max_ft"] > M.GRID_BLOCK_FT or abs(info["scale_ratio"] - 1.0) > 0.003:
            lv.blocked.append(
                f"{ref.sheet}: the control coordinates do not agree with the grid on the "
                f"sheet (worst {M.fmt_in(info['max_ft'])} off, scale ratio "
                f"{info['scale_ratio']:.4f})")
            return
    elif out.frame_kind == "cad":
        bg = out.cads.get(lv.code) or next(iter(out.cads.values()))
        cmp_rows = gridread.compare_grids(grid, bg.grid)
        worst = max((abs(r["delta_ft"]) for r in cmp_rows if "delta_ft" in r), default=0.0)
        lv.notes.append(
            f"grid against the CAD background {bg.name}: "
            f"{sum(1 for r in cmp_rows if 'delta_ft' in r)} spacing(s) compared, worst "
            f"{M.fmt_in(worst)}")
        lv.grid.cad_checks = cmp_rows
        if worst > M.GRID_BLOCK_FT:
            r = max((r for r in cmp_rows if "delta_ft" in r), key=lambda r: abs(r["delta_ft"]))
            lv.blocked.append(
                f"{ref.sheet}: grid {r['a']} to {r['b']} is {M.fmt_ftin(r['first_ft'])} on the "
                f"sheet and {M.fmt_ftin(r['second_ft'])} in the CAD background {bg.name} "
                "(over 1/4 in. apart)")
            return
        targets = {name: (e, n) for name, e, n in bg.grid.intersections()}
        unit = {"in": "inches", "ft": "feet", "mm": "millimeters", "cm": "centimeters",
                "m": "meters"}.get(bg.units or opts.cad_units, bg.units or opts.cad_units)
        frame, info = gridread.frame_from_pairs(
            grid, targets, "cad",
            f"CAD background frame: the origin of {bg.name}, drawn in {unit}, "
            "shown here in decimal feet. Not real-world coordinates.", ppf)
        if frame is None:
            lv.blocked.append(f"{ref.sheet}: the CAD background could not set the frame "
                              f"({info['problem']})")
            return
        if info["max_ft"] > M.GRID_BLOCK_FT:
            lv.blocked.append(
                f"{ref.sheet}: the grid on the sheet does not fit the CAD background "
                f"{bg.name} (worst crossing {M.fmt_in(info['max_ft'])} off)")
            return
    lv.frame = frame
    gridread.place_in_world(grid, frame)
    region = lv.region = _region(grid, ppf)
    prov = M.Provenance(file=ref.name, page=ref.page, sheet=ref.sheet,
                        revision=ref.revision, method=M.VECTOR, confidence=1.0)

    # ---- concrete ---------------------------------------------------------- #
    classes = concrete.fill_classes(res.fills, ppf, region)
    forced = opts.fill_class.get(ref.sheet) or opts.fill_class.get(ref.name)
    rgb, verdict, cands = concrete.choose_class(classes, forced)
    if verdict == "ask":
        ask = {"kind": "fill_class", "sheet": ref.sheet, "level": lv.code,
               "candidates": [{"rgb": list(c["rgb"]), "shapes": c["shapes"],
                               "area_ft2": round(c["score"], 1),
                               "crops": concrete.class_crops(res.render, res.fills, c["rgb"])}
                              for c in cands]}
        out.asks.append(ask)
        lv.blocked.append(
            f"{ref.sheet}: {len(cands)} fill colors could each be the concrete; choose one "
            "(picture crops are in the run log)")
        return
    elements: list = []
    if rgb is None:
        lv.notes.append("no concrete fill class was found on the sheet: walls and "
                        "columns were not read")
    else:
        lv.fill_rgb = tuple(rgb)
        els, remarks = concrete.read_concrete(res.fills, frame, rgb, prov)
        els = [e for e in els if _in_region(e.page_poly, region)]
        lv.notes.extend(remarks)
        openings = concrete.link_runs(els, res.strokes, frame)
        marks = out.marks()
        lv.notes.extend(concrete.assign_marks(els, [w for w in words if _pt_in(w, region)],
                                              frame, marks))
        concrete.spread_marks(els)
        shear = marks if marks else sorted({e.mark for e in els if e.mark.startswith("SW")})
        ends = concrete.shear_wall_ends(els, openings, shear)
        elements.extend(els + openings + ends)
        # reverse check: fill-class pixels no accepted element covers
        left = sl.unread_regions(res.render, rgb, [e.page_poly for e in els if e.page_poly],
                                 min_area_pt2=max(4.0, (0.5 * ppf) ** 2))
        left = [u for u in left if region[0] <= u["center"][0] <= region[2]
                and region[1] <= u["center"][1] <= region[3]]
        for u in left:
            x0, y0, x1, y1 = u["bbox"]
            w = [frame.to_world(x0, y0), frame.to_world(x1, y1)]
            u["world_bbox"] = [min(w[0][0], w[1][0]), min(w[0][1], w[1][1]),
                               max(w[0][0], w[1][0]), max(w[0][1], w[1][1])]
        lv.unread = left
        res.record.unread_regions = left

    # ---- slab -------------------------------------------------------------- #
    contains = list(grid.page_intersections().values())
    s_els, s_remarks, edge_key = slabread.read_slab(res.strokes, frame, region,
                                                    _perimeter(grid), prov, contains)
    lv.notes.extend(s_remarks)
    lv.edge_pen = edge_key
    elements.extend(s_els)
    elements.extend(overlay.read_penetrations(res.strokes, lines, frame, region, prov))
    lv.hidden = _hidden_clusters(res.record, region, frame)
    _number(elements, lv, names)
    # sw ends point at their host by id from here on
    for e in elements:
        if e.kind == M.SW_END:
            host = e.attrs.pop("host")
            op = e.attrs.pop("opening")
            e.attrs["host"] = host.id
            e.attrs["opening"] = op.id if op is not None else ""
        elif e.kind == M.WALL_OPENING:
            ids = {id(x): x.id for x in elements if x.kind == M.WALL}
            e.attrs["hosts"] = [ids.get(h, "") for h in e.attrs.get("hosts", ())]
    lv.elements = elements
    log(f"  {ref.sheet}: {lv.name} read: "
        + ", ".join(f"{len(lv.of(k))} {M.KIND_NOUN[k][1 if len(lv.of(k)) != 1 else 0]}"
                    for k in KIND_ORDER if lv.of(k)))


def _pt_in(w, region) -> bool:
    cx, cy = (w[0] + w[2]) / 2.0, (w[1] + w[3]) / 2.0
    return region[0] <= cx <= region[2] and region[1] <= cy <= region[3]


def _in_region(poly, region) -> bool:
    if not poly:
        return True
    c = g2.centroid(poly)
    return region[0] <= c[0] <= region[2] and region[1] <= c[1] <= region[3]


def _sheet_frame(lv: M.Level, page, ref: M.SheetRef, out: ReadResult, opts: M.Options,
                 what: str) -> tuple:
    """Register another sheet of the level (architect's, shop drawing) to
    the level's world frame through the grid NAMES.  ->
    ``(frame | None, res, words, lines, region, why)``."""
    res = sl.gate(page, dpi=opts.dpi)
    out.sight.append({"sheet": ref.sheet, "file": ref.name, "page": ref.page,
                      "record": res.record})
    if not res.registered:
        return None, res, [], [], None, "its vectors do not register to its render"
    ppf, _label, why = _scale(page, ref, opts)
    if ppf is None:
        return None, res, [], [], None, f"its scale was not verified ({why})"
    words = sl.words(page)
    grid, problems, _axes = gridread.read_grid(res.strokes, words, ppf)
    if any(p.startswith(("grid not read", "no grid bubbles")) for p in problems):
        return None, res, words, [], None, "its grid was not read"
    targets = {name: (e, n) for name, e, n in lv.grid.intersections()}
    frame, info = gridread.frame_from_pairs(grid, targets, lv.frame.kind, lv.frame.label, ppf)
    if frame is None:
        return None, res, words, [], None, info["problem"]
    if info["max_ft"] > M.GRID_BLOCK_FT:
        return None, res, words, [], None, (
            f"its grid does not fit the structural grid (worst crossing "
            f"{M.fmt_in(info['max_ft'])} off; over 1/4 in.)")
    return frame, res, words, sl.text_lines(page), _region(grid, ppf), ""


def _read_arch(lv: M.Level, page, out: ReadResult, opts: M.Options, log) -> None:
    ref = lv.arch
    frame, res, words, _lines, region, why = _sheet_frame(lv, page, ref, out, opts, "arch")
    if frame is None:
        lv.arch_note = f"{ref.sheet}: {why}"
        lv.notes.append(f"architect dimensions not compared: {lv.arch_note}")
        return
    lv.arch_frame = frame
    dims = [d for d in dimread.dimension_strings(words, res.strokes)
            if region[0] <= d["a"][0] <= region[2] and region[1] <= d["a"][1] <= region[3]]
    rows = archdims.classify_strings(dims, frame, lv)
    lv.arch_off = sum(1 for r in rows if r["cls"] == "off")
    lv.arch_strings = rows
    log(f"  {ref.sheet}: {len(rows)} architect dimension strings read")


def _read_shop(lv: M.Level, ref: M.SheetRef, page, out: ReadResult, opts: M.Options,
               names: Names, log) -> None:
    frame, res, _words, lines, region, why = _sheet_frame(lv, page, ref, out, opts, "shop")
    if frame is None:
        lv.notes.append(f"shop drawing {ref.sheet} not overlaid: {why}")
        return
    prov = M.Provenance(file=ref.name, page=ref.page, sheet=ref.sheet,
                        revision=ref.revision, method=M.VECTOR, confidence=0.5)
    els = overlay.read_overlay(res.strokes, [], lines, frame, region, prov)
    _number(els, lv, names)
    lv.elements.extend(els)
    log(f"  {ref.sheet}: {len(els)} overlay opening(s) read (approximate)")


def _add_masonry(lv: M.Level, bg, out: ReadResult, names: Names) -> None:
    """Masonry lives only in the CAD background: bring it into the level's
    world frame (identity when the run's frame IS the CAD frame)."""
    to_world = _cad_to_world(lv, bg)
    if to_world is None:
        lv.notes.append("masonry from the CAD background was not placed: the CAD grid "
                        "and the level grid share fewer than two named crossings")
        return
    els = []
    for e in bg.of(M.MASONRY):
        poly = [to_world(x, y) for x, y in e.poly]
        els.append(M.Element(kind=M.MASONRY, poly=poly, page_poly=[],
                             attrs=dict(e.attrs), prov=M.Provenance(**e.prov.to_dict()),
                             sources=[M.SRC_CAD]))
    _number(els, lv, names)
    lv.elements.extend(els)


def _cad_to_world(lv: M.Level, bg):
    """CAD feet -> the level's world frame."""
    if lv.frame is not None and lv.frame.kind == "cad":
        return lambda x, y: (x, y)
    world = {name: (e, n) for name, e, n in lv.grid.intersections()}
    cad = {name: (e, n) for name, e, n in bg.grid.intersections()}
    common = [k for k in cad if k in world]
    if len(common) < 2:
        return None
    fit = gridread.fit_rigid([cad[k] for k in common], [world[k] for k in common])
    if fit["max"] > M.GRID_BLOCK_FT:
        return None
    return fit["fn"]
