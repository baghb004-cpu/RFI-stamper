"""Plates: which plate shows what.  Brief section 8.

Per level: wall plan, open items, slab edges, shear wall ends, architect
dimensions to check, masonry (when the CAD background has it) and the
point list.  Plus one legend and an index.  The drawing itself is the
plate kit's job; this module only decides content, and it obeys three
rules:

* **Nothing is typed.**  A cell is a value of the produced data (an id, a
  mark as drawn, a measured size, a point name, a coordinate), a sentence
  is a statement rendered from a proof record, a status is the register's
  own ``status_text``.  The only free words are column headings and plate
  titles.
* **Every list row is also on the plan**, in a small note block with a
  thin leader, when the measured-empty finder has a free spot within
  reach.  When it has none the row stays in the list only, and the count
  is reported.
* **The legend is pulled from the produced data**: every example on it is
  a description that exists, character for character, in the point file.

Returns the kit's manifest (every string, with its kind and proof) for
the self-check gate.
"""
from __future__ import annotations

from .. import fieldpro
from .. import papertrail as pt
from . import model as M
from . import platekit as pk

PAD_FT = 4.0
#: one plate number block per level: BB-101 ... (the hundreds digit is the
#: level's place in the set, low to high)
PLATE_KINDS = [("1", "WALL PLAN"), ("2", "OPEN ITEMS"), ("3", "SLAB EDGES"),
               ("4", "SHEAR WALL ENDS"), ("5", "ARCHITECT DIMENSIONS TO CHECK"),
               ("6", "MASONRY"), ("7", "CONTROL REQUEST"), ("8", "SETUP PLAN"),
               ("9", "POINT LIST")]
NOTE_KIND_LABEL = {"hold": "hold, with its item", "dim": "dimension to the concrete missing",
                   "check": "check, with its item", "rfi": "per an answered RFI",
                   "nomatch": "nothing to match in the second source",
                   "arch": "dimensioned by the architect",
                   "match": "matches the CAD background", "single": "single source"}


def plate_number(level_index: int, kind: str, part: int = 0) -> str:
    base = f"BB-{level_index}0{kind}"
    return base if not part else f"{base}.{part}"


def _bbox(lv: M.Level) -> tuple:
    pts = []
    for e in lv.of(M.SLAB_EDGE):
        pts.extend(e.poly)
    if not pts:
        for g in lv.grid.lines:
            pts.extend([g.world_a, g.world_b])
    for e in lv.of(M.WALL, M.COLUMN, M.CONCRETE, M.MASONRY):
        pts.extend(e.poly)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs) - PAD_FT, min(ys) - PAD_FT, max(xs) + PAD_FT, max(ys) + PAD_FT)


def _d(v, src="", proof="") -> pk.T:
    return pk.data(str(v), src=src, proof=proof)


def _ftin(ft) -> str:
    return "" if ft is None else M.fmt_ftin(ft)


def _base(pl, lv: M.Level, *, masonry=False, light=False, table_w: float = 330.0,
          band_h: float = 92.0) -> None:
    """The level as read: grid, slab edge, concrete, openings."""
    pl.set_plan(_bbox(lv), table_w=table_w, band_h=band_h)
    pl.grid(lv.grid)
    for e in lv.of(M.SLAB_EDGE):
        for s in e.attrs.get("segments", []):
            if s["kind"] == "unconfirmed":
                pl.line(s["a"], s["b"], weight="light", dash=[4, 3], accent=True,
                        role="slab_edge_unconfirmed")
            elif s["kind"] in ("arc", "curve"):
                pl.poly(s["pts"], closed=False, weight="heavy", role="slab_edge")
            else:
                pl.line(s["a"], s["b"], weight="heavy", role="slab_edge")
    for e in lv.of(M.WALL, M.COLUMN, M.CONCRETE):
        held = bool(e.items) and not light
        pl.poly(e.poly, fill=pk.CONCRETE_GRAY, stroke=0.0, weight="light",
                dash=[3, 2] if e.attrs.get("cad_only") else None, accent=held,
                role="concrete_cad_only" if e.attrs.get("cad_only") else "concrete")
    for e in lv.of(M.ROUND_COLUMN):
        pl.circle(e.center, e.radius_ft, fill=pk.CONCRETE_GRAY, stroke=0.0, weight="light",
                  accent=bool(e.items) and not light, role="concrete")
    for e in lv.of(M.SLAB_OPENING):
        dash = [3, 2] if e.attrs.get("cross") == "dashed" else None
        pl.poly(e.poly, weight="medium", role="slab_opening")
        pl.line(e.poly[0], e.poly[2], weight="light", dash=dash)
        pl.line(e.poly[1], e.poly[3], weight="light", dash=dash)
    for e in lv.of(M.WALL_OPENING):
        pl.poly(e.poly, weight="light", dash=[2, 2])
    if masonry:
        for e in lv.of(M.MASONRY):
            pl.poly(e.poly, fill=0.93, stroke=0.0, weight="medium", role="masonry")


def _table(ps, first, title_fn, columns, rows, draw_first, statements) -> list:
    """Draw a table beside a plan, continuing on list-only plates."""
    plates = [first]
    n = first.table(columns, rows, title=None)
    draw_first(first)
    first.furniture()
    fit = first.statements(statements)
    if fit < len(statements):
        # never silently dropped: the run report prints every statement, and
        # says which plate could not carry all of its own
        if not hasattr(ps, "unfit"):
            ps.unfit = []
        ps.unfit.append((first.number.s if hasattr(first.number, "s") else str(first.number),
                         len(statements) - fit))
    rest = rows[n:]
    part = 1
    while rest:
        number, title = title_fn(part)
        pl = ps.plate(number, title + " (CONTINUED)")
        k = pl.table(columns, rest, where="full")
        if k <= 0:
            break
        rest = rest[k:]
        plates.append(pl)
        part += 1
    return plates


def _cad_cell(e: M.Element) -> str:
    band = e.attrs.get("cad")
    if e.attrs.get("cad_only"):
        return "CAD only"
    if band in ("near", "flag"):
        return f"{band} {M.fmt_in(e.attrs.get('cad_dev_ft', 0.0))}"
    if band == "missing":
        return "not in CAD"
    return band or ""


def _size(e: M.Element) -> str:
    if e.kind == M.ROUND_COLUMN:
        return f"dia {M.fmt_in(e.attrs.get('diameter_ft', 0.0))}"
    t, ln = e.attrs.get("thickness_ft"), e.attrs.get("length_ft")
    if t is None:
        return ""
    return f"{M.fmt_in(t)} x {_ftin(ln)}"


def _tol_lines(lp) -> list:
    """Tolerance classes of the produced points, from Fieldpro, with their
    basis; the existing disclaimer when a class has none."""
    out = []
    if lp is None or lp.job is None:
        return out
    table = fieldpro.job_tolerances(lp.job)
    for name in sorted({r.tol_class for r in lp.rows if r.tol_class}):
        tc = table.get(name)
        if tc is None:
            continue
        val = M.fmt_in(tc.h_ft) if tc.h_ft is not None else "not judged"
        basis = tc.basis.strip() or fieldpro.TOLERANCE_DISCLAIMER
        out.append(pk.data(f"Layout tolerance class {name}: {val} horizontal. Basis: "
                           f"{pt.plain(basis)}. {pt.plain(fieldpro.TOLERANCE_DISCLAIMER)}",
                           src=f"tolerance:{name}"))
    return out


def build(path: str, rr, reg, trail, ctx, points, opts: M.Options, *, proof_only=False,
          extra=None) -> dict:
    """Write the plate set and return the kit manifest (plus
    ``manifest["plates"]``: number, title, level, kind for the index and
    the reference resolver)."""
    ps = pk.PlateSet(sheet=opts.sheet, project=opts.project, issue=opts.issue)
    listing = []
    levels = [lv for lv in rr.levels if not lv.blocked and lv.frame is not None]
    # The Concrete hat's own plates: open items, slab edges, shear wall
    # ends, architect dimensions and masonry.  The wall plan is built
    # under either hat: it is the plate the picture check compares with
    # the source sheet, and the plan every other plate refers to.
    concrete_hat = "concrete" in (opts.hats or [])
    frame_st = None
    if getattr(ctx, "trail_frame", None) is not None:
        frame_st = pk.statement(trail.say(ctx.trail_frame.id))
    scope_st = pk.statement(trail.say(ctx.trail_scope.id))

    def say(check, lv, key="summary"):
        pid = ctx.proofs.get((check, lv.code))
        return pk.statement(trail.say(pid, key)) if pid else None

    def band(*items):
        return [x for x in ((frame_st,) + items) if x is not None]

    for i, lv in enumerate(levels, start=1):
        lp = points.get(lv.code)
        rows_pts = lp.rows if lp is not None else []

        def start(kind, title, lv=lv, i=i):
            number = plate_number(i, kind)
            listing.append({"number": number, "title": f"{lv.name} {title}", "level": lv.code,
                            "kind": title})
            return ps.plate(number, f"{lv.name} {title}"), \
                (lambda part, kind=kind, title=title: (plate_number(i, kind, part),
                                                       f"{lv.name} {title}"))

        # ---- wall plan --------------------------------------------------------- #
        pl, cont = start("1", "WALL PLAN")
        _base(pl, lv)
        conc = lv.of(M.WALL, M.COLUMN, M.ROUND_COLUMN, M.CONCRETE)
        cols = [(pk.label("ID"), 1.5, "l"), (pk.label("MARK"), 0.8, "c"),
                (pk.label("AS DRAWN"), 1.9, "l"), (pk.label("SOURCE"), 2.2, "l"),
                (pk.label("CAD"), 1.3, "l"), (pk.label("ITEM"), 1.2, "l")]
        rows = []
        for e in conc:
            src = e.source_text() + (" (single source)" if e.single_source else "")
            rows.append([_d(e.id, f"element:{e.id}"), _d(e.mark, f"element:{e.id}"),
                         _d(_size(e), f"element:{e.id}"), _d(src, f"element:{e.id}"),
                         _d(_cad_cell(e), f"element:{e.id}"),
                         _d(", ".join(e.items), f"element:{e.id}")])

        def wall_notes(p, conc=conc):
            for e in conc:
                c = e.centroid()
                lines = [_d(" ".join(x for x in (e.id, e.mark) if x), f"element:{e.id}"),
                         _d(_size(e), f"element:{e.id}")]
                if e.items:
                    lines.append(_d("item " + ", ".join(e.items), f"element:{e.id}"))
                if e.single_source:
                    lines.append(_d("single source: " + e.source_text(), f"element:{e.id}"))
                p.note(c[0], c[1], lines, accent=bool(e.items))

        st = band(say("XC-CORNER-CAD", lv, "plate"), scope_st, say("SINGLE-SOURCE", lv))
        _table(ps, pl, cont, cols, rows, wall_notes, st)
        if proof_only:
            break

        # ---- open items ---------------------------------------------------------- #
        if concrete_hat:
            tags = reg.items(lv.code)
            pl, cont = start("2", "OPEN ITEMS")
            _base(pl, lv, light=True, table_w=500.0)
            for t in tags:
                for b in t.boxes:
                    if b.get("world"):
                        pl.box(tuple(b["world"]), _d(str(t.number), f"item:{t.ref}"))
            cols = [(pk.label("ITEM"), 0.9, "l"), (pk.label("CATEGORY"), 1.8, "l"),
                    (pk.label("WHAT IS WRONG"), 4.2, "l"), (pk.label("WHAT IS NEEDED"), 3.0, "l"),
                    (pk.label("STATUS"), 1.5, "l")]
            rows = []
            for t in tags:
                pid = t.proofs[0] if t.proofs else ""
                rows.append([_d(t.ref, f"item:{t.ref}"),
                             _d(t.category_label, f"item:{t.ref}"),
                             pk.T(t.wrong, "statement", proof=pid, src=f"item:{t.ref}"),
                             pk.T(t.needed, "statement", proof=pid, src=f"item:{t.ref}"),
                             _d(t.status_text(), f"item:{t.ref}")])

            def item_notes(p, tags=tags):
                for t in tags:
                    if not t.at:
                        continue
                    pid = t.proofs[0] if t.proofs else ""
                    p.note(t.at[0], t.at[1],
                           [_d(f"{t.ref}  {t.status_text()}", f"item:{t.ref}"),
                            pk.T(t.label, "statement", proof=pid, src=f"item:{t.ref}")],
                           accent=True)

            _table(ps, pl, cont, cols, rows, item_notes, band(say("SL-HIDDEN", lv), scope_st))

        # ---- slab edges ------------------------------------------------------------ #
        slab_rows = [r for r in rows_pts if r.family in ("slab_edge_corner", "radius_point",
                                                         "slab_opening_corner")]
        if not concrete_hat:
            slab_rows = []            # every point stays on the point list plate
        elif lv.of(M.SLAB_EDGE) or slab_rows:
            pl, cont = start("3", "SLAB EDGES")
            _base(pl, lv, light=True)
            _points_plate(ps, pl, cont, slab_rows, band(*_tol_lines(lp), scope_st))

        # ---- shear wall ends --------------------------------------------------------- #
        ends = lv.of(M.SW_END)
        if concrete_hat and ends:
            pl, cont = start("4", "SHEAR WALL ENDS")
            _base(pl, lv, light=True)
            cols = [(pk.label("END"), 1.5, "l"), (pk.label("MARK"), 0.8, "c"),
                    (pk.label("ROLE"), 0.8, "l"), (pk.label("ZONE"), 1.6, "l"),
                    (pk.label("END BARS"), 1.2, "l"), (pk.label("TIES"), 1.6, "l"),
                    (pk.label("ARCHITECT"), 1.5, "l"), (pk.label("ITEM"), 1.3, "l")]
            rows = []
            for e in ends:
                row, sched = rr.schedule_row(e.mark)
                if e.attrs.get("zone_disputed"):
                    a, b = e.attrs["zone_disputed"]
                    zone = f"schedule {_ftin(a)} / detail {_ftin(b)}"
                else:
                    zone = _ftin(e.attrs.get("zone_length_ft"))
                arch = {"string": "dimension string", "centerline": "centerline only",
                        "none": "nothing"}.get(e.attrs.get("arch"), "not compared")
                src = f"schedule:{sched.sheet}" if sched else f"element:{e.id}"
                rows.append([_d(e.id, f"element:{e.id}"), _d(e.mark, f"element:{e.id}"),
                             _d(e.attrs["role"], f"element:{e.id}"), _d(zone, src),
                             _d(row.end_bars if row else "", src),
                             _d(row.ties if row else "", src),
                             _d(arch, f"element:{e.id}"),
                             _d(", ".join(e.items), f"element:{e.id}")])

            def end_notes(p, ends=ends):
                for e in ends:
                    p.marker(e.center[0], e.center[1], "point", accent=bool(e.items))
                    lines = [_d(f"{e.id} {e.mark} {e.attrs['role']}", f"element:{e.id}")]
                    if e.items:
                        lines.append(_d("item " + ", ".join(e.items), f"element:{e.id}"))
                    p.note(e.center[0], e.center[1], lines, accent=bool(e.items))

            _table(ps, pl, cont, cols, rows, end_notes,
                   band(say("XC-ARCH-ENDS", lv), say("XC-ZONE", lv), say("XC-SCHEDULE", lv)))

        # ---- architect dimensions to check ---------------------------------------------- #
        check = [r for r in lv.arch_strings if r["cls"] in ("different", "one")]
        if concrete_hat and lv.arch is not None and not lv.arch_note:
            pl, cont = start("5", "ARCHITECT DIMENSIONS TO CHECK")
            _base(pl, lv, light=True)
            cols = [(pk.label("STRING"), 1.4, "l"), (pk.label("READS AS"), 2.2, "l"),
                    (pk.label("ON THE STRUCTURE"), 1.6, "l"), (pk.label("APART"), 1.0, "l"),
                    (pk.label("ITEM"), 1.0, "l")]
            rows = []
            for r in check:
                kind = "different" if r["cls"] == "different" else (
                    "opening centerline only" if r["centerline"] is not None
                    else "one structural line only")
                rows.append([pk.T(r["text"], "quote", src=f"sheet:{lv.arch.sheet}"),
                             _d(kind, f"sheet:{lv.arch.sheet}"),
                             _d(_ftin(r["structure_ft"]), f"sheet:{lv.struct.sheet}"),
                             _d("" if r["delta_ft"] is None else M.fmt_in(abs(r["delta_ft"])),
                                f"sheet:{lv.struct.sheet}"),
                             _d(r.get("item", ""), f"sheet:{lv.arch.sheet}")])

            def dim_notes(p, check=check, lv=lv):
                for r in check:
                    p.line(r["a"], r["b"], weight="light", accent=True)
                    mid = ((r["a"][0] + r["b"][0]) / 2.0, (r["a"][1] + r["b"][1]) / 2.0)
                    lines = [pk.T(r["text"], "quote", src=f"sheet:{lv.arch.sheet}")]
                    if r.get("item"):
                        lines.append(_d("item " + r["item"], f"item:{r['item']}"))
                    p.note(mid[0], mid[1], lines, accent=True)

            _table(ps, pl, cont, cols, rows, dim_notes,
                   band(say("XC-ARCH-STRINGS", lv), say("XC-ARCH-ENDS", lv)))

        # ---- masonry ----------------------------------------------------------------------- #
        mas = lv.of(M.MASONRY)
        if concrete_hat and mas and opts.masonry:
            pl, cont = start("6", "MASONRY")
            _base(pl, lv, masonry=True, light=True)
            cols = [(pk.label("ID"), 1.4, "l"), (pk.label("AS DRAWN"), 2.2, "l"),
                    (pk.label("SOURCE"), 3.0, "l")]
            rows = [[_d(e.id, f"element:{e.id}"), _d(_size(e), f"element:{e.id}"),
                     _d(e.source_text() + " (single source)", f"element:{e.id}")] for e in mas]

            def mas_notes(p, mas=mas):
                for e in mas:
                    c = e.centroid()
                    p.note(c[0], c[1], [_d(e.id, f"element:{e.id}"),
                                        _d("single source: " + e.source_text(),
                                           f"element:{e.id}")])

            _table(ps, pl, cont, cols, rows, mas_notes, band(say("SINGLE-SOURCE", lv)))

        # ---- the surveyor hat's plates (built by surveyor.py) -------------------------------- #
        if extra is not None:
            extra(ps, lv, i, start, band, listing)

        # ---- the point list -------------------------------------------------------------------- #
        if rows_pts:
            pl, cont = start("9", "POINT LIST")
            _base(pl, lv, light=True)
            conc_rows = [r for r in rows_pts if r not in slab_rows]
            _points_plate(ps, pl, cont, conc_rows, band(*_tol_lines(lp), scope_st),
                          tags_for=[r for r in conc_rows if r.family != "grid"])

    if not proof_only:
        if opts.out_legend:
            _legend(ps, rr, reg, points, listing, frame_st)
        _index(ps, listing, rr)
    man = ps.save(path)
    man["plates"] = listing
    man["unfit"] = list(getattr(ps, "unfit", []))
    return man


def _points_plate(ps, pl, cont, rows_pts, statements, tags_for=None) -> None:
    cols = [(pk.label("POINT"), 1.3, "l"), (pk.label("LAYER"), 2.0, "l"),
            (pk.label("NORTHING"), 1.4, "r"), (pk.label("EASTING"), 1.4, "r"),
            (pk.label("DESCRIPTION"), 4.6, "l")]
    rows = [[_d(r.name, f"point:{r.name}"), _d(r.layer, f"point:{r.name}"),
             _d(f"{r.n:.3f}", f"point:{r.name}"), _d(f"{r.e:.3f}", f"point:{r.name}"),
             _d(r.desc, f"point:{r.name}")] for r in rows_pts]
    marks = rows_pts if tags_for is None else tags_for

    def draw(p, marks=marks):
        for r in marks:
            held = r.note_kind in ("hold", "dim")
            p.marker(r.e, r.n, "radius" if r.code == "RP" else "point", accent=held)
            p.tag(r.e, r.n, _d(r.name, f"point:{r.name}"), accent=held)

    _table(ps, pl, cont, cols, rows, draw, statements)


def legend_examples(points) -> list:
    """One example per note kind, pulled from the produced points:
    ``[(kind, PointRow)]`` (the first point, by name, that carries it)."""
    out = []
    rows = sorted((r for lp in points.values() for r in lp.rows), key=lambda r: r.name)
    for kind in ("hold", "dim", "check", "rfi", "nomatch", "arch", "match", "single"):
        hit = next((r for r in rows if r.note_kind == kind), None)
        if hit is not None:
            out.append((kind, hit))
    return out


def _legend(ps, rr, reg, points, listing, frame_st) -> None:
    pl = ps.plate("BB-002", "LEGEND")
    listing.append({"number": "BB-002", "title": "LEGEND", "level": "", "kind": "LEGEND"})
    rows = []
    all_rows = [r for lp in points.values() for r in lp.rows]
    for layer in M.POINT_LAYERS:
        on = sorted((r for r in all_rows if r.layer == layer), key=lambda r: r.name)
        if not on:
            continue
        rows.append([_d(layer, "points"), _d(str(len(on)), "points:count"),
                     _d(on[0].name, f"point:{on[0].name}"),
                     _d(on[0].desc, f"point:{on[0].name}")])
    for kind, r in legend_examples(points):
        rows.append([_d(NOTE_KIND_LABEL[kind], "points"),
                     _d(str(sum(1 for x in all_rows if x.note_kind == kind)), "points:count"),
                     _d(r.name, f"point:{r.name}"), _d(r.desc, f"point:{r.name}")])
    cols = [(pk.label("LAYER OR NOTE"), 3.2, "l"), (pk.label("POINTS"), 0.9, "r"),
            (pk.label("EXAMPLE POINT"), 1.5, "l"), (pk.label("ITS DESCRIPTION, AS EXPORTED"), 5.0, "l")]
    pl.table(cols, rows, title=pk.label("POINTS"), where="full")
    cats = []
    for lv in rr.levels:
        c = reg.counts(lv.code)
        if c.get("total"):
            cats.append(_d(f"{lv.name}: {c['total']} open item(s): {c.get('answer', 0)} answer "
                           f"needed before layout, {c.get('confirm', 0)} confirm before forming, "
                           f"{c.get('answered', 0)} answered.", f"register:{lv.code}"))
    pl.statements([x for x in ([frame_st] + cats) if x is not None])


def _index(ps, listing, rr) -> None:
    pl = ps.plate("BB-001", "INDEX")
    rows = [[_d("BB-001", "plates"), _d("INDEX", "plates"), _d("", "plates")]]
    for p in sorted(listing, key=lambda p: p["number"]):
        rows.append([_d(p["number"], "plates"), _d(p["title"], "plates"),
                     _d(p["level"], "plates")])
    listing.append({"number": "BB-001", "title": "INDEX", "level": "", "kind": "INDEX"})
    pl.table([(pk.label("PLATE"), 1.2, "l"), (pk.label("TITLE"), 5.0, "l"),
              (pk.label("LEVEL"), 1.0, "l")], rows, where="full")
    src = sorted({(lv.struct.sheet, lv.struct.revision) for lv in rr.levels})
    pl.statements([_d("Read from: " + ", ".join(f"{s} rev {r}" if r else s for s, r in src) + ".",
                      "intake")])


def build_booklet(path: str, rr, reg, opts: M.Options) -> dict | None:
    """The phone booklet: one open item per letter-size page, a crop of
    the plan with its text."""
    bk = pk.Booklet(project=opts.project, issue=opts.issue)
    n = 0
    for lv in rr.levels:
        if lv.blocked or lv.frame is None:
            continue
        for t in reg.items(lv.code):
            box = next((b["world"] for b in t.boxes if b.get("world")), None)
            if box is None:
                continue
            pad = 10.0
            crop = (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)
            pid = t.proofs[0] if t.proofs else ""

            def draw(view, lv=lv, box=box, t=t):
                view.grid(lv.grid)
                for e in lv.of(M.SLAB_EDGE):
                    for s in e.attrs.get("segments", []):
                        if s["kind"] in ("arc", "curve"):
                            view.poly(s["pts"], closed=False, weight="heavy")
                        else:
                            view.line(s["a"], s["b"], weight="heavy")
                for e in lv.of(M.WALL, M.COLUMN, M.CONCRETE):
                    view.poly(e.poly, fill=pk.CONCRETE_GRAY, weight="light")
                for e in lv.of(M.ROUND_COLUMN):
                    view.circle(e.center, e.radius_ft, fill=pk.CONCRETE_GRAY, weight="light")
                view.box(tuple(box), _d(str(t.number), f"item:{t.ref}"))

            bk.item(_d(t.ref, f"item:{t.ref}"),
                    pk.T(t.label, "statement", proof=pid, src=f"item:{t.ref}"), crop, draw,
                    [_d(f"{lv.name}. {t.category_label}. Status: {t.status_text()}.",
                        f"item:{t.ref}"),
                     pk.T(t.wrong, "statement", proof=pid, src=f"item:{t.ref}"),
                     pk.T(t.needed, "statement", proof=pid, src=f"item:{t.ref}")])
            n += 1
    if not n:
        return None
    return bk.save(path)
