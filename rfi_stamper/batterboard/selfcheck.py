"""The self-check gate: automatic, fully recorded.  Brief section 11.

Nothing is delivered until every check here holds; one failure and the
run is BLOCKED and writes nothing to the deliverables folder.

1. **Picture check** (the check that was missing).  Each produced wall
   plan plate is RENDERED and compared with the render of its source
   sheet: concrete drawn on the plate against the fill on the sheet, slab
   edge and slab openings against the strokes on the sheet.  A difference
   is acceptable only where an open item already covers it.
2. Round trip of the point files; names unique; the frame declared.
3. Reference resolution, proof coverage for every printed statement,
   text lint, file metadata.
4. Counts on the legend and summaries equal the data; every legend
   example exists verbatim in the produced point file.
5. Key numbers (grid offsets, a sample of corner coordinates, zone
   lengths) recomputed by a second code path and compared.

Every check returns ``{"check", "ok", "detail", "counts"}``; the run
report prints them all, pass or fail.
"""
from __future__ import annotations

import math
import os

import fitz
import numpy as np

from .. import geom2d as g2
from .. import papertrail as pt
from .. import sightline as sl
from ..draft import parse_ftin
from . import model as M
from . import platekit as pk
from . import plates as plates_mod
from . import points as points_mod

MIN_DIFF_FT2 = 0.25           # a picture difference smaller than this is edge noise
EDGE_SHARE = 0.8              # share of a drawn edge that must be on the sheet
RECOMPUTE_TOL_FT = 0.0055     # about 1/16 in.
DARK = 150


def _result(check, problems, **counts) -> dict:
    return {"check": check, "ok": not problems, "detail": "; ".join(problems),
            "problems": list(problems), "counts": counts}


def _in_item_box(reg, level: str, e: float, n: float, pad: float = 0.5) -> bool:
    for t in reg.items(level):
        for b in t.boxes:
            w = b.get("world")
            if w and w[0] - pad <= e <= w[2] + pad and w[1] - pad <= n <= w[3] + pad:
                return True
    return False


def _dark_share(rd, a, b) -> float:
    """Share of the segment a-b (viewer points) with dark ink within 2 px."""
    ax, ay = rd.px([a[0]], [a[1]])
    bx, by = rd.px([b[0]], [b[1]])
    ln = math.hypot(bx[0] - ax[0], by[0] - ay[0])
    n = max(4, int(ln / 2.0))
    hit = 0
    gray = rd.rgb.min(axis=2)
    for i in range(n):
        t = (i + 0.5) / n
        x = int(round(ax[0] + (bx[0] - ax[0]) * t - 0.5))
        y = int(round(ay[0] + (by[0] - ay[0]) * t - 0.5))
        x0, x1 = max(0, x - 2), min(rd.w, x + 3)
        y0, y1 = max(0, y - 2), min(rd.h, y + 3)
        if x1 > x0 and y1 > y0 and (gray[y0:y1, x0:x1] < DARK).any():
            hit += 1
    return hit / n


def picture_check(lv: M.Level, plate_pdf: str, page_index: int, pg: dict, reg) -> dict:
    """Compare one produced wall plan plate with its source sheet."""
    problems = []
    counts = {"concrete_differences": 0, "explained_by_open_items": 0, "edges": 0,
              "edges_on_sheet": 0, "openings": 0}
    tr = pg.get("transform")
    if not tr or lv.frame is None:
        return _result(f"picture check {lv.code}", ["the plate has no plan to compare"])
    src = fitz.open(lv.struct.file)
    try:
        rd = sl.Render(src[lv.struct.page - 1], 150)
    finally:
        src.close()
    src_ppf = lv.frame.pt_per_ft * rd.zoom                 # source pixels per foot
    dpi_p = int(min(300, max(100, round(72.0 * src_ppf / tr["k"]))))
    doc = fitz.open(plate_pdf)
    try:
        pix = doc[page_index].get_pixmap(dpi=dpi_p, colorspace=fitz.csRGB, alpha=False)
    finally:
        doc.close()
    plate = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
    zp = dpi_p / 72.0
    g = int(round(pk.CONCRETE_GRAY * 255))
    conc_p = (np.abs(plate.astype(np.int16) - g).max(axis=2) <= 6)
    conc_p = g2.closing(conc_p, 2)
    # ---- concrete: the plate's fill, resampled onto the source sheet -------- #
    if lv.fill_rgb is not None and lv.region is not None:
        x0, y0, x1, y1 = lv.region
        (c0,), (r0,) = rd.px([x0], [y0])
        (c1,), (r1,) = rd.px([x1], [y1])
        c0, r0 = max(0, int(c0)), max(0, int(r0))
        c1, r1 = min(rd.w, int(c1)), min(rd.h, int(r1))
        cols = (np.arange(c0, c1) + 0.5) / rd.zoom + rd.ox
        rows = (np.arange(r0, r1) + 0.5) / rd.zoom + rd.oy
        X, Y = np.meshgrid(cols, rows)
        f = lv.frame
        E = f.a * X + f.b * Y + f.tx
        N = f.c * X + f.d * Y + f.ty
        pc = np.rint((tr["ox"] + E * tr["k"]) * zp - 0.5).astype(np.int64)
        prw = np.rint((pg["page_h"] - (tr["oy"] + N * tr["k"])) * zp - 0.5).astype(np.int64)
        ok = (pc >= 0) & (pc < plate.shape[1]) & (prw >= 0) & (prw < plate.shape[0])
        drawn = np.zeros(X.shape, dtype=bool)
        drawn[ok] = conc_p[prw[ok], pc[ok]]
        # the sheet's fill: drop one-pixel bands first (the gray anti-aliased
        # rim of black text and linework is not concrete), then bridge the
        # thin overlays that cross real fill
        fill = g2.closing(g2.opening(rd.class_mask(lv.fill_rgb)[r0:r1, c0:c1], 1), 2)
        extra = g2.opening(drawn & ~g2.dilate(fill, 3), 2)        # drawn, not on the sheet
        lost = g2.opening(fill & ~g2.dilate(drawn, 3), 2)         # on the sheet, not drawn
        min_px = MIN_DIFF_FT2 * src_ppf * src_ppf
        for mask, what in ((extra, "the plate draws concrete that is not on"),
                           (lost, "concrete on")):
            if not mask.any():
                continue
            _labels, boxes = g2.label(mask)
            for b in boxes:
                if b.area < min_px:
                    continue
                counts["concrete_differences"] += 1
                cx = ((b.x0 + b.x1 + 1) / 2.0 + c0) / rd.zoom + rd.ox
                cy = ((b.y0 + b.y1 + 1) / 2.0 + r0) / rd.zoom + rd.oy
                e, n = f.to_world(cx, cy)
                if _in_item_box(reg, lv.code, e, n):
                    counts["explained_by_open_items"] += 1
                    continue
                tail = (f"{lv.struct.sheet} at {lv.grid.tie_text(e, n)}"
                        if what.startswith("the plate")
                        else f"{lv.struct.sheet} at {lv.grid.tie_text(e, n)} is not on the plate")
                problems.append(f"{what} {tail}, and no open item covers it")
    # ---- slab edge and openings: the plate's linework against sheet strokes --- #
    pen = lv.edge_pen
    for gm in pg.get("geometry", []):
        role = gm.get("role")
        if role not in ("slab_edge", "slab_opening"):
            continue
        if gm["kind"] == "line":
            segs = [(gm["a"], gm["b"])]
        else:
            pts = gm.get("pts") or []
            segs = list(zip(pts, pts[1:]))
            if role == "slab_opening" and len(pts) > 2:
                segs.append((pts[-1], pts[0]))
        for a, b in segs:
            pa, pb = lv.frame.to_page(*a), lv.frame.to_page(*b)
            if math.hypot(pb[0] - pa[0], pb[1] - pa[1]) < 2.0:
                continue
            if role == "slab_edge":
                counts["edges"] += 1
                share = (sl.stroke_present(rd, pa, pb, pen[0], pen[1]) if pen
                         else _dark_share(rd, pa, pb))
            else:
                counts["openings"] += 1
                share = _dark_share(rd, pa, pb)
            if share >= EDGE_SHARE:
                if role == "slab_edge":
                    counts["edges_on_sheet"] += 1
                continue
            mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
            if _in_item_box(reg, lv.code, mid[0], mid[1]):
                counts["explained_by_open_items"] += 1
                continue
            problems.append(
                f"the plate draws a {'slab edge' if role == 'slab_edge' else 'slab opening'} "
                f"line at {lv.grid.tie_text(*mid)} that is on {lv.struct.sheet} for only "
                f"{round(share * 100)} percent of its length, and no open item covers it")
    return _result(f"picture check {lv.code}", problems, **counts)


def check_points(rr, points) -> dict:
    problems = []
    n = 0
    names = []
    for code, lp in points.items():
        lv = rr.level(code)
        if not lp.rows:
            continue
        n += len(lp.rows)
        names.extend(r.name for r in lp.rows)
        problems.extend(f"{code}: {p}" for p in points_mod.round_trip(lp, lp.files))
        frame_file = next((f for f in lp.files if f.endswith("_FRAME.txt")), None)
        if frame_file is None or lv.frame.label not in open(frame_file, encoding="utf-8").read():
            problems.append(f"{code}: the frame is not declared beside the point files")
    if len(set(names)) != len(names):
        problems.append("a point name is used twice in the run")
    return _result("point files round trip, unique names, frame declared", problems, points=n)


def inventory(rr, reg, plate_numbers=()) -> pt.Inventory:
    """Everything a printed reference may point at in this run."""
    sheets = set(rr.sheets()) | set(plate_numbers)
    details = set()
    indexed = set()
    for s in rr.schedules:
        for d in s.details:
            if d.detail:
                details.add(f"{d.detail}/{s.sheet}")
                indexed.add(s.sheet)
    return pt.Inventory(sheets=sheets, items=set(reg.refs()),
                        rfis={r.rfi for r in rr.rfis if r.rfi}, details=details,
                        detail_unindexed=sheets - indexed)


def check_references(trail, manifests, inv, extra_texts=()) -> dict:
    """One unresolved reference blocks delivery (brief 7.4)."""
    problems = []
    seen = set()
    things = list(trail.statements)
    texts = [t["s"] for man in manifests for pg in man.get("pages", [])
             for t in pg.get("texts", []) if t.get("kind") != "label"]
    for text, ref in pt.unresolved(things + texts + list(extra_texts), inv):
        key = (ref.kind, ref.value)
        if key in seen:
            continue
        seen.add(key)
        problems.append(f"{ref.kind} {ref.value} is referred to but is not in this run "
                        f"(in: {text[:70]})")
    return _result("every printed reference resolves", problems,
                   statements=len(things), plate_texts=len(texts))


def check_coverage(trail, manifests, files) -> dict:
    """Every printed statement traces to a proof record, every string on
    a page is in the manifest, and nothing else is on the page."""
    problems = []
    n_stmt = n_words = 0
    for man in manifests:
        path = man.get("file")
        doc = fitz.open(path) if path and os.path.exists(path) else None
        try:
            for i, pg in enumerate(man.get("pages", [])):
                known = set()
                for t in pg.get("texts", []):
                    known.update(t["s"].split())
                    for ln in t.get("lines", []) or []:
                        known.update(ln.split())
                    if t.get("full"):
                        known.update(str(t["full"]).split())
                    if t["kind"] == "statement":
                        n_stmt += 1
                        try:
                            trail.get(t.get("proof") or "")
                        except KeyError:
                            problems.append(f"plate {pg['number']}: a statement has no proof "
                                            f"record behind it ({t['s'][:50]})")
                    elif t["kind"] == "data" and not t.get("src"):
                        problems.append(f"plate {pg['number']}: a value has no source "
                                        f"({t['s'][:50]})")
                if doc is not None and i < doc.page_count:
                    for w in doc[i].get_text("words"):
                        n_words += 1
                        tok = w[4]
                        if tok not in known and tok.rstrip(".") not in known \
                                and not tok.endswith("...") and tok != "...":
                            problems.append(f"plate {pg['number']}: the word {tok!r} is on the "
                                            "page but not in the print manifest")
                            break
        finally:
            if doc is not None:
                doc.close()
    return _result("proof coverage: every statement has its proof record", problems,
                   statements=n_stmt, words=n_words)


def check_lint(files, project, manifests, quotes=()) -> dict:
    """Text lint and metadata on every output (brief 7.8, 7.9)."""
    problems = []
    quotes = list(quotes)
    for man in manifests:
        for pg in man.get("pages", []):
            for t in pg.get("texts", []):
                if t["kind"] == "quote":
                    quotes.append(t["s"])
    n = 0
    for f in files:
        if not pt.can_lint(f):
            continue
        n += 1
        for h in pt.lint_file(f, project, quotes=quotes):
            problems.append(f"{os.path.basename(f)}: {h.kind} ({h.token})")
            if len(problems) > 12:
                break
    return _result("text lint and file metadata", problems, files=n)


def check_legend(manifest, points, reg, rr) -> dict:
    """Counts on the legend equal the data; every example exists verbatim
    in the produced point file (brief 7.5, case 7)."""
    problems = []
    pg = next((p for p in manifest.get("pages", []) if p["number"] == "BB-002"), None)
    if pg is None:
        return _result("legend against the data", [], rows=0)
    rows_all = [r for lp in points.values() for r in lp.rows]
    exported = set()
    for lp in points.values():
        for f in lp.files:
            if f.lower().endswith(".csv"):
                with open(f, encoding="utf-8") as fh:
                    for line in fh.read().splitlines():
                        parts = line.split(",")
                        if len(parts) >= 5:
                            exported.add((parts[0], ",".join(parts[4:])))
    cells = [t for t in pg["texts"] if t["where"] == "table" and t["kind"] == "data"]
    by_y: dict = {}
    for t in cells:
        by_y.setdefault(round(t["bbox"][1], 0), []).append(t)
    n_rows = 0
    for _y, row in sorted(by_y.items(), reverse=True):
        row.sort(key=lambda t: t["bbox"][0])
        if len(row) != 4:
            continue
        n_rows += 1
        what, count, name, desc = (t["s"] for t in row)
        if what in M.POINT_LAYERS:
            actual = sum(1 for r in rows_all if r.layer == what)
        else:
            kind = next((k for k, v in plates_mod.NOTE_KIND_LABEL.items() if v == what), None)
            actual = sum(1 for r in rows_all if r.note_kind == kind)
        if str(actual) != count:
            problems.append(f"the legend prints {count} for {what!r} but the data holds {actual}")
        if exported and (name, desc) not in exported:
            problems.append(f"the legend example {name} {desc!r} is not in the produced "
                            "point file, character for character")
    return _result("legend against the data", problems, rows=n_rows)


def check_recompute(rr, points) -> dict:
    """Key numbers recomputed by a second code path."""
    problems = []
    n_grid = n_corner = n_zone = 0
    for lv in rr.levels:
        if lv.blocked or lv.frame is None:
            continue
        ppf = lv.pt_per_ft
        # grid offsets: perpendicular page distance between neighbours, not the projection
        for axis in ("x", "y"):
            fam = lv.grid.family(axis)
            for a, b in zip(fam, fam[1:]):
                mid = ((b.page_a[0] + b.page_b[0]) / 2.0, (b.page_a[1] + b.page_b[1]) / 2.0)
                d = g2.dist_point_line(mid, a.page_a, a.page_b) / ppf
                n_grid += 1
                if abs(d - (b.offset_ft - a.offset_ft)) > RECOMPUTE_TOL_FT:
                    problems.append(f"{lv.code}: grid {a.name} to {b.name} recomputes to "
                                    f"{M.fmt_ftin(d)}, not {M.fmt_ftin(b.offset_ft - a.offset_ft)}")
        # corner coordinates: by ties to the nearest gridlines, not by the frame
        aligned = all(abs(g.world_a[0] - g.world_b[0]) < 1e-6 for g in lv.grid.family("x")) and \
            all(abs(g.world_a[1] - g.world_b[1]) < 1e-6 for g in lv.grid.family("y"))
        if aligned and lv.grid.family("x") and lv.grid.family("y"):
            els = [e for e in lv.of(M.WALL, M.COLUMN, M.CONCRETE, M.SLAB_OPENING)
                   if e.page_poly and len(e.page_poly) == len(e.poly) and not e.attrs.get("cad_only")]
            for e in els[::2]:
                for (px, py), (we, wn) in zip(e.page_poly, e.poly):
                    gx = min(lv.grid.family("x"),
                             key=lambda g: g2.dist_point_line((px, py), g.page_a, g.page_b))
                    gy = min(lv.grid.family("y"),
                             key=lambda g: g2.dist_point_line((px, py), g.page_a, g.page_b))
                    dx = g2.dist_point_line((px, py), gx.page_a, gx.page_b) / ppf
                    dy = g2.dist_point_line((px, py), gy.page_a, gy.page_b) / ppf
                    e2 = gx.world_a[0] + (dx if we >= gx.world_a[0] else -dx)
                    n2 = gy.world_a[1] + (dy if wn >= gy.world_a[1] else -dy)
                    n_corner += 1
                    if abs(e2 - we) > RECOMPUTE_TOL_FT or abs(n2 - wn) > RECOMPUTE_TOL_FT:
                        problems.append(
                            f"{lv.code}: a corner of {e.id} recomputes to "
                            f"({e2:.3f}, {n2:.3f}) by grid ties, not ({we:.3f}, {wn:.3f})")
                        break
        # zone lengths: re-read from the schedule cell as printed, and measured on the points
        lp = points.get(lv.code)
        for e in lv.of(M.SW_END):
            z = e.attrs.get("zone_length_ft")
            if not z:
                continue
            row, _s = rr.schedule_row(e.mark)
            cell = next((v for k, v in (row.cells or {}).items()
                         if "ZONE" in k.upper()), None) if row else None
            again = parse_ftin(cell) if cell else None
            n_zone += 1
            if again is None or abs(again - z) > 1e-6:
                problems.append(f"{lv.code}: the zone length of {e.mark} re-reads as "
                                f"{cell!r}, not {M.fmt_ftin(z)}")
            for r in (lp.rows if lp else []):
                if r.element == e.id and r.family == "sw_zone_end":
                    d = g2.dist_point_line((r.e, r.n), e.attrs["face_a"], e.attrs["face_b"])
                    if abs(d - z) > RECOMPUTE_TOL_FT:
                        problems.append(f"{lv.code}: point {r.name} sits {M.fmt_ftin(d)} from "
                                        f"the end face, not the zone length {M.fmt_ftin(z)}")
    return _result("key numbers recomputed by a second method", problems,
                   grid_spacings=n_grid, corner_coordinates=n_corner, zone_lengths=n_zone)


def run_all(rr, reg, trail, points, manifests, files, opts: M.Options, *, plate_pdf=None,
            plate_manifest=None, extra_texts=(), quotes=()) -> list:
    """Run the whole gate.  ``files`` are the staged outputs."""
    out = []
    if plate_pdf and plate_manifest:
        levels = [lv for lv in rr.levels if not lv.blocked and lv.frame is not None]
        for i, lv in enumerate(levels, start=1):
            number = plates_mod.plate_number(i, "1")
            idx = next((k for k, p in enumerate(plate_manifest["pages"])
                        if p["number"] == number), None)
            if idx is None:
                continue
            out.append(picture_check(lv, plate_pdf, idx, plate_manifest["pages"][idx], reg))
    out.append(check_points(rr, points))
    numbers = [p["number"] for man in manifests for p in man.get("pages", [])]
    out.append(check_references(trail, manifests, inventory(rr, reg, numbers), extra_texts))
    out.append(check_coverage(trail, manifests, files))
    out.append(check_lint(files, opts.project, manifests, quotes))
    if plate_manifest:
        out.append(check_legend(plate_manifest, points, reg, rr))
    out.append(check_recompute(rr, points))
    return out
