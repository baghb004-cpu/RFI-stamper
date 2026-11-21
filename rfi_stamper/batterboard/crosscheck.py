"""Cross-checks: the structure against every other drawing in the drop.

Brief section 6.  Each check is registered the way the Backcheck registers
its rules: an id, what it compares with what, and a function.  A check
that cannot be evaluated raises :class:`CheckSkip` with the reason, which
becomes a line of the coverage statement ("not compared, because ...")
instead of silence.

Every check writes a proof record (the Paper Trail) and every sentence it
can print is a template bound to its id, so the words can never claim more
than the counts.  What a check finds wrong is raised in the Red Tags
register with location boxes and the proof ids; the element is tagged
with the item and with what the second source said about it.

Thresholds (model.py, never weakened): structure against CAD background
matches within 1/4 in. and flags over 1/2 in.; an architect string end
matches a structural line within 1 1/2 in.; a string is the same within
1/2 in.
"""
from __future__ import annotations

import math

from .. import geom2d as g2
from .. import papertrail as pt
from . import archdims, planread
from . import model as M

SEARCH_FT = 3.0                # how far a counterpart may sit
MISSING_FT = 2.0               # a corner with nothing nearer than this has no match
SUPPORT_SHARE = 0.8            # share of a wall's line that must sit on a wall below
MIN_CHANGE_PT = 6.0            # a revision difference shorter than this is counted, not placed
TOL, FLAG_TOL = M.fmt_in(M.MATCH_FT), M.fmt_in(M.FLAG_FT)

CHECKS: dict = {}


class CheckSkip(Exception):
    """A check ran, looked at the drop, and could not compare (str(exc) is
    the plain reason)."""


def _check(cid: str, title: str):
    def deco(fn):
        CHECKS[cid] = {"title": title, "fn": fn}
        return fn
    return deco


# ---------------------------------------------------------------- wording --
# literal template text can hold no digit and none of the quantifier
# words; every number and every "all" comes from the proof record.

_T = {
    ("GRID-PRINTED", "summary"):
        "Grid spacing on {v:sheet} against its printed grid dimensions: {n:grid spacing:match} "
        "agree within {v:tol}. {not_compared}",
    ("GRID-CAD", "summary"):
        "Grid spacing on {v:sheet} against {against}: {n:grid spacing:match} agree within "
        "{v:tol}. {not_compared}",
    ("XC-CORNER-CAD", "summary"):
        "Corner test of {compared} against {against}: {classes:match} match within {v:tol}. "
        "Between {v:tol} and {v:flag_tol}: {v:near_list}. Over {v:flag_tol}: {v:flag_list}. "
        "With nothing to match there: {v:missing_list}. {not_compared}",
    ("XC-CORNER-CAD", "plate"):
        "{classes:match} match {against} within {v:tol}. {not_compared}",
    ("XC-FACE-CAD", "summary"):
        "Face comparison along walls, {compared} against {against}: {n:wall face:match} match "
        "within {v:tol}; {c:wall face:near} {s:wall face:near:is|are} between {v:tol} and "
        "{v:flag_tol}; {c:wall face:flag} {s:wall face:flag:is|are} over {v:flag_tol}. "
        "{not_compared}",
    ("XC-CAD-ONLY", "summary"):
        "Shapes in {against} against {compared}: {classes:matched} are on the structural plan; "
        "{classes:cad_only} are in the CAD background only. {not_compared}",
    ("XC-ARCH-ENDS", "summary"):
        "Shear wall ends and jambs on {compared} against the dimension strings on {against}: "
        "{n:shear wall end:string} have an architect dimension string; "
        "{c:shear wall end:centerline} {s:shear wall end:centerline:is|are} tied by the "
        "opening centerline only; {c:shear wall end:without} {s:shear wall end:without:has|have} "
        "nothing from the architect. {not_compared}",
    ("XC-ARCH-STRINGS", "summary"):
        "Architect dimension strings on {against} that land on the structure: "
        "{n:architect dimension string:same} are the same as {compared} within {v:same_tol}; "
        "{c:architect dimension string:different} {s:architect dimension string:different:is|are} "
        "different; {c:architect dimension string:one} "
        "{s:architect dimension string:one:lands|land} on one structural line only. "
        "{not_compared}",
    ("XC-LEVEL-BELOW", "summary"):
        "Walls on {compared} against walls on {against}: {n:wall:on_wall} sit on a wall below; "
        "{c:wall:off_wall} {s:wall:off_wall:does|do} not. {not_compared}",
    ("XC-SCHEDULE", "summary"):
        "Marked walls on {compared} against the schedule on {against}: "
        "{n:scheduled wall:thk_ok} are drawn at the schedule thickness within {v:tol}; "
        "{n:scheduled wall run:len_ok} meet the schedule minimum length. {not_compared}",
    ("XC-ZONE", "summary"):
        "Zone length in the schedule on {v:sheet} against the zone length detail: "
        "{n:scheduled mark:agree} agree. {not_compared}",
    ("XC-OVERLAY", "summary"):
        "Openings overlaid from {against} against {compared} (locations approximate): "
        "{n:overlay opening:marked} are at an opening the structural plan marks; "
        "{c:overlay opening:slab} {s:overlay opening:slab:passes|pass} through the slab where "
        "the plan marks nothing; {c:overlay opening:wall} {s:overlay opening:wall:passes|pass} "
        "through a wall; {c:overlay opening:zone} {s:overlay opening:zone:sits|sit} inside a "
        "tied end zone. {not_compared}",
    ("XC-REVISION", "summary"):
        "Revision {v:rev_new} of {v:sheet} against revision {v:rev_old}: "
        "{c:changed place:total} found; {n:changed place:clouded} "
        "{s:changed place:clouded:is|are} inside a revision cloud. {not_compared}",
    ("SL-HIDDEN", "summary"):
        "Geometry in the file of {v:sheet} that is not on the plotted sheet: "
        "{c:hidden place:total}, {c:hidden stroke:total} and {c:hidden fill:total} dropped. "
        "{c:clipped fill:total} replaced by the visible part. {c:obscured shape:total} under "
        "annotation ink and kept whole.",
    ("SINGLE-SOURCE", "summary"):
        "Single source on {compared}: {classes:single}. {not_compared}",
    ("SCOPE", "summary"): "Not laid out in this run: {v:scope}.",
    ("FRAME", "summary"): "{v:frame}",
    ("NOTE", "summary"): "{q:0}",
    # ---- items ---------------------------------------------------------- #
    ("XC-CORNER-CAD/ITEM", "label"): "Structure and CAD background disagree",
    ("XC-CORNER-CAD/ITEM", "wrong"):
        "{v:what} {v:element} is {v:dev} from its match in {against} (the flag is over "
        "{v:flag_tol}).",
    ("XC-CORNER-CAD/ITEM", "needed"): "Confirm which source governs before layout.",
    ("XC-NEAR/ITEM", "label"): "Close to the CAD background, not a match",
    ("XC-NEAR/ITEM", "wrong"):
        "{v:what} {v:element} is {v:dev} from its match in {against} (a match is within {v:tol}).",
    ("XC-NEAR/ITEM", "needed"): "Check the dimension before forming.",
    ("XC-SOLO/ITEM", "label"): "Not in the CAD background",
    ("XC-SOLO/ITEM", "wrong"):
        "{v:what} {v:element} on {v:sheet} has nothing to match in {against}.",
    ("XC-SOLO/ITEM", "needed"): "Confirm it against a second source before forming.",
    ("XC-CAD-ONLY/ITEM", "label"): "In the CAD background only",
    ("XC-CAD-ONLY/ITEM", "wrong"):
        "{v:what} {v:element} is drawn from {against}; it is not on {v:sheet}.",
    ("XC-CAD-ONLY/ITEM", "needed"): "An answer is needed before this is laid out.",
    ("XC-ARCH-ENDS/ITEM", "label"): "Dimension to the concrete is missing",
    ("XC-ARCH-ENDS/ITEM", "wrong"):
        "The {v:role} of shear wall {v:mark} ({v:element}) is not dimensioned to the concrete "
        "on {against}: {v:how}.",
    ("XC-ARCH-ENDS/ITEM", "needed"):
        "A dimension from a gridline to the face of concrete at this {v:role}.",
    ("XC-ARCH-DIFF/ITEM", "label"): "Architect dimension differs from the structure",
    ("XC-ARCH-DIFF/ITEM", "wrong"):
        "The string {v:text} on {against} measures {v:structure} on {compared}: {v:dev} apart.",
    ("XC-ARCH-DIFF/ITEM", "needed"): "Confirm which dimension governs before layout.",
    ("XC-ARCH-ONE/ITEM", "label"): "Architect dimension lands on one line only",
    ("XC-ARCH-ONE/ITEM", "wrong"):
        "The string {v:text} on {against} has one end on {v:end} and the other on nothing "
        "structural.",
    ("XC-ARCH-ONE/ITEM", "needed"): "Confirm what the string measures before forming.",
    ("XC-LEVEL-BELOW/ITEM", "label"): "Wall does not sit on a wall below",
    ("XC-LEVEL-BELOW/ITEM", "wrong"):
        "Wall {v:element} on {compared} sits on a wall of {against} for {v:share} of its "
        "length.",
    ("XC-LEVEL-BELOW/ITEM", "needed"): "Confirm the support below before forming.",
    ("XC-THK/ITEM", "label"): "Thickness differs from the schedule",
    ("XC-THK/ITEM", "wrong"):
        "Wall {v:element} marked {v:mark} is drawn {v:drawn} thick; the schedule on {against} "
        "gives {v:sched}.",
    ("XC-THK/ITEM", "needed"): "Confirm the thickness before layout.",
    ("XC-LEN/ITEM", "label"): "Shorter than the schedule minimum",
    ("XC-LEN/ITEM", "wrong"):
        "Wall run {v:mark} is {v:drawn} long as drawn; the schedule on {against} gives a "
        "minimum of {v:sched}.",
    ("XC-LEN/ITEM", "needed"): "Confirm the length before layout.",
    ("XC-ZONE/ITEM", "label"): "Schedule and detail give different zone lengths",
    ("XC-ZONE/ITEM", "wrong"):
        "For {v:mark} the schedule on {v:sheet} gives a zone length of {v:sched}; the zone "
        "length detail gives {v:in_detail}.",
    ("XC-ZONE/ITEM", "needed"): "An answer on the zone length before the end zones are laid out.",
    ("XC-OVL-ZONE/ITEM", "label"): "Opening inside a tied end zone",
    ("XC-OVL-ZONE/ITEM", "wrong"):
        "Overlay opening {v:element} from {against} (approximate) sits inside the end zone "
        "of shear wall {v:mark}.",
    ("XC-OVL-ZONE/ITEM", "needed"): "An answer before the wall is formed.",
    ("XC-OVL-WALL/ITEM", "label"): "Opening through a wall, not on the structural plan",
    ("XC-OVL-WALL/ITEM", "wrong"):
        "Overlay opening {v:element} from {against} (approximate) passes through wall "
        "{v:wall}; {compared} marks nothing there.",
    ("XC-OVL-WALL/ITEM", "needed"): "An answer before the wall is formed.",
    ("XC-OVL-SLAB/ITEM", "label"): "Opening through the slab, not on the structural plan",
    ("XC-OVL-SLAB/ITEM", "wrong"):
        "Overlay opening {v:element} from {against} (approximate) passes through the slab; "
        "{compared} marks nothing there.",
    ("XC-OVL-SLAB/ITEM", "needed"): "Confirm the opening before the slab is formed.",
    ("XC-REVISION/ITEM", "label"): "Changed between revisions, not clouded",
    ("XC-REVISION/ITEM", "wrong"):
        "{v:sheet} changed here between revision {v:rev_old} and revision {v:rev_new}; "
        "the change is not inside a revision cloud.",
    ("XC-REVISION/ITEM", "needed"): "Confirm the change is intended before forming.",
    ("SL-HIDDEN/ITEM", "label"): "Superseded linework left in the file",
    ("SL-HIDDEN/ITEM", "wrong"):
        "Linework is in the file of {v:sheet} here that is not on the plotted sheet "
        "({v:what}). It was not read{v:cloud}.",
    ("SL-HIDDEN/ITEM", "needed"): "Confirm what is plotted is what is intended.",
    ("SL-UNREAD/ITEM", "label"): "Concrete fill seen but not read",
    ("SL-UNREAD/ITEM", "wrong"):
        "Concrete fill is plotted here on {v:sheet} that the reader did not read as a shape.",
    ("SL-UNREAD/ITEM", "needed"): "Check this place by eye against the sheet.",
    ("SLAB-EDGE/ITEM", "label"): "Slab edge run without a heavy plotted line",
    ("SLAB-EDGE/ITEM", "wrong"):
        "The slab edge on {v:sheet} has a run of {v:length} with {v:what}.",
    ("SLAB-EDGE/ITEM", "needed"): "An answer on the slab edge here before layout.",
}
for (_c, _k), _txt in _T.items():
    pt.register(_c, _k, _txt)


# ------------------------------------------------------------- the context --

class Ctx:
    """What a check works from."""

    def __init__(self, rr, trail: pt.Trail, register, opts: M.Options, names):
        self.rr = rr
        self.trail = trail
        self.reg = register
        self.opts = opts
        self.names = names
        self.skips: list = []          # (check id, level, reason)
        self.proofs: dict = {}         # (check id, level) -> proof id

    # -- helpers ------------------------------------------------------------- #
    def proof(self, check: str, lv, **kw) -> pt.Proof:
        p = self.trail.add(check, level=lv.code if lv is not None else "", **kw)
        self.proofs[(check, lv.code if lv is not None else "")] = p.id
        return p

    def item(self, lv: M.Level, check: str, category: str, *, at, box, elements=(),
             values=None, against="", compared="", key=None) -> object:
        """One finding -> its own proof record, three statements and a Red
        Tag.  The element(s) are tagged with the item ref."""
        vals = dict(values or {})
        vals.setdefault("sheet", lv.struct.sheet)
        p = self.trail.add(check + "/ITEM", level=lv.code, compared=compared,
                           against=against, values=vals,
                           elements=[e.id for e in elements], result="flag")
        label = self.trail.say(p.id, "label").text
        wrong = self.trail.say(p.id, "wrong").text
        needed = self.trail.say(p.id, "needed").text
        pad = 1.5
        wb = [box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad]
        pg = None
        if lv.frame is not None:
            c = [lv.frame.to_page(wb[0], wb[1]), lv.frame.to_page(wb[2], wb[3])]
            pg = [min(c[0][0], c[1][0]), min(c[0][1], c[1][1]),
                  max(c[0][0], c[1][0]), max(c[0][1], c[1][1])]
        tag = self.reg.raise_item(
            lv.code, key or (check + "|" + (elements[0].kind if elements else "place")),
            category, label, wrong, needed, at=(round(at[0], 3), round(at[1], 3)),
            boxes=[{"sheet": lv.struct.sheet, "page": pg, "world": wb}],
            proofs=[p.id], elements=[e.id for e in elements])
        for e in elements:
            if tag.ref not in e.items:
                e.items.append(tag.ref)
            if p.id not in e.proofs:
                e.proofs.append(p.id)
        return tag


def _nouns(kind: str, what: str) -> list:
    s, p = M.KIND_NOUN[kind]
    return [f"{s} {what}".strip(), f"{s} {what}s".strip()] if what else [s, p]


def _what(e: M.Element) -> str:
    return M.KIND_NOUN[e.kind][0].capitalize()


def _bbox_of(pts) -> list:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def _cad_elements(ctx: Ctx, lv: M.Level):
    """The level's CAD background mapped into the level's world frame."""
    bg = ctx.rr.cads.get(lv.code)
    if bg is None:
        raise CheckSkip("a CAD background for this level is not in the drop")
    fn = planread._cad_to_world(lv, bg)
    if fn is None:
        raise CheckSkip("the CAD background shares fewer than two named grid crossings "
                        "with the sheet")
    out = []
    for e in bg.elements:
        poly = [fn(x, y) for x, y in e.poly]
        c = fn(*e.center) if e.center is not None else None
        out.append((e, poly, c))
    return bg, out


def _band(d: float) -> str:
    if d <= M.MATCH_FT + 1e-9:
        return "match"
    if d <= M.FLAG_FT + 1e-9:
        return "near"
    return "flag"


# ------------------------------------------------------------------ checks --

@_check("GRID-PRINTED", "grid spacing against the printed grid dimensions")
def _grid_printed(ctx: Ctx, lv: M.Level) -> None:
    pairs = sum(max(0, len(lv.grid.family(a)) - 1) for a in ("x", "y"))
    seen = {(c["a"], c["b"]) for c in lv.grid.spacing_checks}
    ok = {(c["a"], c["b"]) for c in lv.grid.spacing_checks
          if abs(c["delta_ft"]) <= M.GRID_BLOCK_FT}
    adj = set()
    for a in ("x", "y"):
        fam = lv.grid.family(a)
        adj.update((g.name, h.name) for g, h in zip(fam, fam[1:]))
    n_cmp = len(adj & seen)
    not_cmp = []
    if n_cmp < pairs:
        not_cmp.append(f"{pairs - n_cmp} grid spacing(s) with nothing printed between them")
    ctx.proof("GRID-PRINTED", lv, compared=f"structural plan {lv.struct.sheet}",
              classes={"grid spacing": {"noun": ["grid spacing", "grid spacings"],
                                        "total": pairs, "compared": n_cmp,
                                        "match": len(adj & ok)}},
              values={"sheet": lv.struct.sheet, "tol": TOL}, not_compared=not_cmp,
              result="match")


@_check("GRID-CAD", "grid spacing against the CAD background")
def _grid_cad(ctx: Ctx, lv: M.Level) -> None:
    bg = ctx.rr.cads.get(lv.code)
    if bg is None:
        raise CheckSkip("a CAD background for this level is not in the drop")
    from . import gridread
    rows = [r for r in gridread.compare_grids(lv.grid, bg.grid) if "delta_ft" in r]
    miss = [r["missing"] for r in gridread.compare_grids(lv.grid, bg.grid) if "missing" in r]
    pairs = sum(max(0, len(lv.grid.family(a)) - 1) for a in ("x", "y"))
    ctx.proof("GRID-CAD", lv, compared=f"structural plan {lv.struct.sheet}",
              against=f"the CAD background {bg.name}",
              classes={"grid spacing": {"noun": ["grid spacing", "grid spacings"],
                                        "total": pairs, "compared": len(rows),
                                        "match": sum(1 for r in rows
                                                     if abs(r["delta_ft"]) <= M.GRID_BLOCK_FT)}},
              values={"sheet": lv.struct.sheet, "tol": TOL},
              not_compared=[f"gridline {m} (in one source only)" for m in miss],
              result="match")


@_check("XC-CORNER-CAD", "corner to corner against the CAD background")
def _corner_cad(ctx: Ctx, lv: M.Level) -> None:
    bg, cad = _cad_elements(ctx, lv)
    against = f"the CAD background {bg.name}"
    compared = f"structural plan {lv.struct.sheet}"
    classes = {
        "wall corner": {"noun": ["wall corner", "wall corners"]},
        "rectangular column corner": {"noun": ["rectangular column corner",
                                               "rectangular column corners"]},
        "round column center": {"noun": ["round column center", "round column centers"]},
        "concrete shape corner": {"noun": ["concrete shape corner", "concrete shape corners"]},
    }
    for c in classes.values():
        c.update(total=0, compared=0, match=0, near=0, flag=0, missing=0)
    cls_of = {M.WALL: "wall corner", M.COLUMN: "rectangular column corner",
              M.ROUND_COLUMN: "round column center", M.CONCRETE: "concrete shape corner"}
    used = set()
    # a class is compared only when the CAD background has a layer that can
    # speak about it; otherwise it is NOT compared, and the statement says so
    role_of = {M.WALL: "wall", M.CONCRETE: "wall", M.COLUMN: "column",
               M.ROUND_COLUMN: "column"}
    silent = {k for k, role in role_of.items() if not bg.roles.get(role)}
    for e in lv.of(M.WALL, M.COLUMN, M.ROUND_COLUMN, M.CONCRETE):
        if M.SRC_STRUCT not in e.sources:
            continue
        cl = classes[cls_of[e.kind]]
        pts = [e.center] if e.kind == M.ROUND_COLUMN else list(e.poly)
        cl["total"] += len(pts)
        if e.kind in silent:
            e.attrs["cad"] = "not compared"
            continue
        ec = e.centroid()
        best = None
        for k, (ce, poly, cc) in enumerate(cad):
            if (ce.kind == M.ROUND_COLUMN) != (e.kind == M.ROUND_COLUMN):
                continue
            ccen = cc if cc is not None else (sum(p[0] for p in poly) / len(poly),
                                              sum(p[1] for p in poly) / len(poly))
            d = math.hypot(ccen[0] - ec[0], ccen[1] - ec[1])
            if d <= SEARCH_FT and (best is None or d < best[0]):
                best = (d, k, poly, cc)
        if best is None:
            # nothing there in the second source: a single source element
            cl["compared"] += len(pts)
            cl["missing"] += len(pts)
            e.attrs["cad"] = "missing"
            ctx.item(lv, "XC-SOLO", "confirm", at=ec, box=_bbox_of(e.poly), elements=[e],
                     values={"what": _what(e), "element": e.id}, against=against,
                     compared=compared)
            continue
        _d, k, poly, cc = best
        used.add(k)
        targets = [cc] if e.kind == M.ROUND_COLUMN else poly
        worst, n_missing = 0.0, 0
        devs = []
        for p in pts:
            d = min(math.hypot(p[0] - q[0], p[1] - q[1]) for q in targets)
            if d > MISSING_FT:
                n_missing += 1
                cl["missing"] += 1
            else:
                cl[_band(d)] += 1
                worst = max(worst, d)
            devs.append(d)
        cl["compared"] += len(pts)
        band = _band(worst)
        e.attrs["cad"] = band
        e.attrs["cad_dev_ft"] = worst
        e.attrs["corner_dev_ft"] = devs
        if M.SRC_CAD not in e.sources:
            e.sources.append(M.SRC_CAD)
        vals = {"what": _what(e), "element": e.id, "dev": M.fmt_in(worst), "tol": TOL,
                "flag_tol": FLAG_TOL}
        if band == "flag":
            ctx.item(lv, "XC-CORNER-CAD", "answer", at=ec, box=_bbox_of(e.poly), elements=[e],
                     values=vals, against=against, compared=compared)
        elif band == "near":
            ctx.item(lv, "XC-NEAR", "confirm", at=ec, box=_bbox_of(e.poly), elements=[e],
                     values=vals, against=against, compared=compared)
    lv.cad_used = used
    # coverage: only the classes that exist AND were compared are claimed
    not_cmp = [c["noun"][1] + " (not on the structural plan)"
               for c in classes.values() if c["total"] == 0]
    not_cmp += [classes[cls_of[k]]["noun"][1]
                + f" (the CAD background has nothing on a {role_of[k]} layer)"
                for k in (M.WALL, M.COLUMN, M.ROUND_COLUMN, M.CONCRETE)
                if k in silent and classes[cls_of[k]]["total"] > 0]
    classes = {k: c for k, c in classes.items() if c["total"] > 0}
    for kind, name in ((M.SLAB_EDGE, "slab edges"), (M.SLAB_OPENING, "slab openings"),
                       (M.WALL_OPENING, "wall openings")):
        if lv.of(kind):
            not_cmp.append(f"{name} (not read from the CAD background)")
    def listing(counter):
        parts = [f"{c[counter]} {c['noun'][0 if c[counter] == 1 else 1]}"
                 for c in classes.values() if c[counter]]
        return ", ".join(parts) if parts else "nothing"

    ctx.proof("XC-CORNER-CAD", lv, compared=compared, against=against, classes=classes,
              values={"tol": TOL, "flag_tol": FLAG_TOL, "sheet": lv.struct.sheet,
                      "near_list": listing("near"), "flag_list": listing("flag"),
                      "missing_list": listing("missing")},
              elements=[e.id for e in lv.of(M.WALL, M.COLUMN, M.ROUND_COLUMN, M.CONCRETE)],
              not_compared=not_cmp,
              result="flag" if any(c["flag"] or c["missing"] for c in classes.values())
              else "match")


@_check("XC-FACE-CAD", "face comparison along every wall")
def _face_cad(ctx: Ctx, lv: M.Level) -> None:
    bg, cad = _cad_elements(ctx, lv)
    cl = {"noun": ["wall face", "wall faces"], "total": 0, "compared": 0, "match": 0,
          "near": 0, "flag": 0}
    walls = [e for e in lv.of(M.WALL) if M.SRC_STRUCT in e.sources]
    for e in walls:
        t = e.attrs.get("thickness_ft", 0.0)
        faces = [(e.poly[i], e.poly[(i + 1) % len(e.poly)]) for i in range(len(e.poly))]
        faces = [f for f in faces if math.hypot(f[1][0] - f[0][0], f[1][1] - f[0][1]) >= 2.0 * t]
        cl["total"] += len(faces)
        ec = e.centroid()
        best = None
        for ce, poly, _cc in cad:
            if ce.kind != M.WALL:
                continue
            c = (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
            d = math.hypot(c[0] - ec[0], c[1] - ec[1])
            if d <= SEARCH_FT and (best is None or d < best[0]):
                best = (d, poly)
        if best is None:
            continue
        worst_all = 0.0
        for a, b in faces:
            worst = 0.0
            for k in range(9):
                s = (k + 0.5) / 9.0
                p = (a[0] + (b[0] - a[0]) * s, a[1] + (b[1] - a[1]) * s)
                worst = max(worst, g2.dist_point_poly(p, best[1]))
            cl["compared"] += 1
            cl[_band(worst)] += 1
            worst_all = max(worst_all, worst)
        e.attrs["face_dev_ft"] = worst_all
    not_cmp = []
    if cl["compared"] < cl["total"]:
        not_cmp.append(f"{cl['total'] - cl['compared']} wall face(s) of walls with nothing "
                       "to match in the CAD background")
    ctx.proof("XC-FACE-CAD", lv, compared=f"structural plan {lv.struct.sheet}",
              against=f"the CAD background {bg.name}", classes={"wall face": cl},
              values={"tol": TOL, "flag_tol": FLAG_TOL, "sheet": lv.struct.sheet},
              elements=[e.id for e in walls], not_compared=not_cmp,
              result="flag" if cl["flag"] else "match")


@_check("XC-CAD-ONLY", "shapes in the CAD background that the structural plan lacks")
def _cad_only(ctx: Ctx, lv: M.Level) -> None:
    bg, cad = _cad_elements(ctx, lv)
    used = getattr(lv, "cad_used", set())
    against = f"the CAD background {bg.name}"
    classes = {}
    new = []
    for k, (ce, poly, cc) in enumerate(cad):
        if ce.kind not in (M.WALL, M.COLUMN, M.ROUND_COLUMN):
            continue
        cl = classes.setdefault(ce.kind, {"noun": ["CAD " + M.KIND_NOUN[ce.kind][0],
                                                   "CAD " + M.KIND_NOUN[ce.kind][1]],
                                          "total": 0, "compared": 0, "matched": 0, "cad_only": 0})
        cl["total"] += 1
        cl["compared"] += 1
        if k in used:
            cl["matched"] += 1
            continue
        cl["cad_only"] += 1
        prov = M.Provenance(**ce.prov.to_dict())
        prov.note = "from the CAD background; not on the structural plan"
        e = M.Element(kind=ce.kind, poly=list(poly),
                      page_poly=[lv.frame.to_page(*p) for p in poly],
                      center=cc, radius_ft=ce.radius_ft, attrs=dict(ce.attrs), prov=prov,
                      sources=[M.SRC_CAD])
        e.attrs["cad_only"] = True
        new.append(e)
    planread._number(new, lv, ctx.names)
    lv.elements.extend(new)
    for e in new:
        ctx.item(lv, "XC-CAD-ONLY", "answer", at=e.centroid(), box=_bbox_of(e.poly),
                 elements=[e], values={"what": _what(e), "element": e.id}, against=against,
                 compared=f"structural plan {lv.struct.sheet}")
    ctx.proof("XC-CAD-ONLY", lv, compared=f"structural plan {lv.struct.sheet}",
              against=against, classes={v["noun"][0]: v for v in classes.values()},
              values={"sheet": lv.struct.sheet}, elements=[e.id for e in new],
              not_compared=[f"{n} {role}-layer item(s) that are not closed shapes"
                            for role, n in sorted(bg.unread.items())],
              result="flag" if new else "match")


@_check("XC-ARCH", "shear wall ends and architect dimension strings")
def _arch(ctx: Ctx, lv: M.Level) -> None:
    ends = lv.of(M.SW_END)
    if lv.arch is None:
        raise CheckSkip("an architectural plan of this level is not in the drop")
    if lv.arch_note:
        raise CheckSkip("the architectural plan could not be compared: " + lv.arch_note)
    against = f"architectural plan {lv.arch.sheet}"
    compared = f"structural plan {lv.struct.sheet}"
    cl = {"noun": ["shear wall end", "shear wall ends"], "total": len(ends),
          "compared": len(ends), "string": 0, "centerline": 0, "without": 0}
    for e in ends:
        how, row = archdims.end_status(e, lv.arch_strings)
        e.attrs["arch"] = how
        role = "jamb" if e.attrs["role"] == "jamb" else "end"
        if how == "string":
            cl["string"] += 1
            e.attrs["arch_text"] = row["text"]
            if M.SRC_ARCH not in e.sources:
                e.sources.append(M.SRC_ARCH)
            continue
        cl["centerline" if how == "centerline" else "without"] += 1
        ctx.item(lv, "XC-ARCH-ENDS", "answer", at=e.center,
                 box=[e.center[0], e.center[1], e.center[0], e.center[1]], elements=[e],
                 values={"role": role, "mark": pt.Verbatim(e.mark), "element": e.id,
                         "how": ("the architect ties the opening centerline only"
                                 if how == "centerline"
                                 else "the architect gives nothing here")},
                 against=against, compared=compared)
    ctx.proof("XC-ARCH-ENDS", lv, compared=compared, against=against,
              classes={"shear wall end": cl}, values={"sheet": lv.struct.sheet,
                                                      "sheet2": lv.arch.sheet},
              elements=[e.id for e in ends],
              result="flag" if cl["centerline"] + cl["without"] else "match")
    rows = [r for r in lv.arch_strings if r["cls"] != "off"]
    sc = {"noun": ["architect dimension string", "architect dimension strings"],
          "total": len(rows), "compared": len(rows),
          "same": sum(1 for r in rows if r["cls"] == "same"),
          "different": sum(1 for r in rows if r["cls"] == "different"),
          "one": sum(1 for r in rows if r["cls"] == "one")}
    for r in rows:
        mid = ((r["a"][0] + r["b"][0]) / 2.0, (r["a"][1] + r["b"][1]) / 2.0)
        box = _bbox_of([r["a"], r["b"]])
        if r["cls"] == "different":
            touched = []
            for m in r["ends"]:
                el = m["line"].get("element") if m else None
                if el is None:
                    continue
                if el not in touched:
                    touched.append(el)
                la, lb = m["line"]["a"], m["line"]["b"]
                for se in ends:
                    if se.attrs["host"] == el.id and (
                            (archdims._same(se.attrs["face_a"], la) and archdims._same(se.attrs["face_b"], lb))
                            or (archdims._same(se.attrs["face_a"], lb) and archdims._same(se.attrs["face_b"], la))):
                        touched.append(se)
            tag = ctx.item(lv, "XC-ARCH-DIFF", "answer", at=mid, box=box, elements=touched,
                           values={"text": pt.Verbatim(r["text"]),
                                   "structure": M.fmt_ftin(r["structure_ft"]),
                                   "dev": M.fmt_in(abs(r["delta_ft"]))},
                           against=against, compared=compared, key="XC-ARCH-DIFF|string")
            r["item"] = tag.ref
        elif r["cls"] == "one" and r["centerline"] is None:
            hit = next(m for m in r["ends"] if m is not None)
            end = ("gridline " + hit["line"]["ref"] if hit["line"]["kind"] == "grid"
                   else hit["line"]["ref"])
            tag = ctx.item(lv, "XC-ARCH-ONE", "confirm", at=mid, box=box,
                           values={"text": pt.Verbatim(r["text"]), "end": end},
                           against=against, compared=compared, key="XC-ARCH-ONE|string")
            r["item"] = tag.ref
    not_cmp = []
    if lv.arch_off:
        not_cmp.append(f"{lv.arch_off} string(s) with neither end on the structure")
    ctx.proof("XC-ARCH-STRINGS", lv, compared=compared, against=against,
              classes={"architect dimension string": sc},
              values={"same_tol": M.fmt_in(M.ARCH_SAME_FT), "sheet": lv.struct.sheet,
                      "sheet2": lv.arch.sheet}, not_compared=not_cmp,
              result="flag" if sc["different"] else "match")


@_check("XC-LEVEL-BELOW", "walls that do not sit on a wall below")
def _level_below(ctx: Ctx, lv: M.Level) -> None:
    below = None
    for other in ctx.rr.levels:
        if other.order < lv.order and not other.blocked:
            if below is None or other.order > below.order:
                below = other
    if below is None:
        raise CheckSkip("a readable level below this one is not in the drop")
    if below.frame is None or lv.frame is None or below.frame.kind != lv.frame.kind:
        raise CheckSkip("the level below is not in the same frame")
    walls = lv.of(M.WALL)
    lower = [e.poly for e in below.of(M.WALL, M.COLUMN, M.CONCRETE)]
    cl = {"noun": ["wall", "walls"], "total": len(walls), "compared": len(walls),
          "on_wall": 0, "off_wall": 0}
    for e in walls:
        r = g2.min_area_rect(e.poly)
        # sample the wall's own area: the middle line of every long face pair
        samples = []
        n = len(e.poly)
        c = e.centroid()
        for i in range(n):
            a, b = e.poly[i], e.poly[(i + 1) % n]
            for k in range(7):
                s = (k + 0.5) / 7.0
                p = (a[0] + (b[0] - a[0]) * s, a[1] + (b[1] - a[1]) * s)
                # pull the sample a little inside the wall
                q = (p[0] + (c[0] - p[0]) * 0.02, p[1] + (c[1] - p[1]) * 0.02)
                samples.append(q)
        hit = sum(1 for p in samples
                  if any(g2.point_in_poly(p[0], p[1], poly)
                         or g2.dist_point_poly(p, poly) <= 1.0 * M.IN for poly in lower))
        share = hit / max(1, len(samples))
        e.attrs["below_share"] = share
        if share >= SUPPORT_SHARE:
            cl["on_wall"] += 1
            continue
        cl["off_wall"] += 1
        ctx.item(lv, "XC-LEVEL-BELOW", "confirm", at=c, box=_bbox_of(e.poly), elements=[e],
                 values={"element": e.id, "share": f"{round(share * 100)} percent",
                         "length": M.fmt_ftin(r["length"])},
                 against=f"structural plan {below.struct.sheet}",
                 compared=f"structural plan {lv.struct.sheet}")
    ctx.proof("XC-LEVEL-BELOW", lv, compared=f"structural plan {lv.struct.sheet}",
              against=f"structural plan {below.struct.sheet}", classes={"wall": cl},
              values={"sheet": lv.struct.sheet, "sheet2": below.struct.sheet},
              elements=[e.id for e in walls], result="flag" if cl["off_wall"] else "match")


@_check("XC-SCHEDULE", "thickness and length against the schedule")
def _schedule(ctx: Ctx, lv: M.Level) -> None:
    if not ctx.rr.schedules:
        raise CheckSkip("a wall schedule is not in the drop")
    marked = [e for e in lv.of(M.WALL) if e.mark and ctx.rr.schedule_row(e.mark)[0]]
    sheet = ctx.rr.schedules[0].sheet
    against = f"wall schedule {sheet}"
    compared = f"structural plan {lv.struct.sheet}"
    tc = {"noun": ["scheduled wall", "scheduled walls"], "total": len(marked), "compared": 0,
          "thk_ok": 0}
    for e in marked:
        row, _s = ctx.rr.schedule_row(e.mark)
        if row.thickness_ft is None:
            continue
        tc["compared"] += 1
        e.attrs["schedule_thickness_ft"] = row.thickness_ft
        if abs(e.attrs["thickness_ft"] - row.thickness_ft) <= M.MATCH_FT:
            tc["thk_ok"] += 1
            continue
        ctx.item(lv, "XC-THK", "answer", at=e.centroid(), box=_bbox_of(e.poly), elements=[e],
                 values={"element": e.id, "mark": pt.Verbatim(e.mark),
                         "drawn": M.fmt_in(e.attrs["thickness_ft"]),
                         "sched": M.fmt_in(row.thickness_ft), "sheet2": sheet},
                 against=against, compared=compared)
    runs: dict = {}
    for e in marked:
        runs.setdefault((e.mark, e.attrs.get("run")), []).append(e)
    rc = {"noun": ["scheduled wall run", "scheduled wall runs"], "total": len(runs),
          "compared": 0, "len_ok": 0}
    for (mark, _run), pieces in runs.items():
        row, _s = ctx.rr.schedule_row(mark)
        if row.min_length_ft is None:
            continue
        rc["compared"] += 1
        pts = [p for e in pieces for p in e.poly]
        length = g2.min_area_rect(pts)["length"]
        for e in pieces:
            e.attrs["run_length_ft"] = length
        if length + M.MATCH_FT >= row.min_length_ft:
            rc["len_ok"] += 1
            continue
        ctx.item(lv, "XC-LEN", "answer", at=pieces[0].centroid(), box=_bbox_of(pts),
                 elements=pieces, values={"mark": pt.Verbatim(mark),
                                          "drawn": M.fmt_ftin(length),
                                          "sched": M.fmt_ftin(row.min_length_ft),
                                          "sheet2": sheet},
                 against=against, compared=compared)
    not_cmp = []
    unmarked = [e for e in lv.of(M.WALL) if not e.mark]
    if unmarked:
        not_cmp.append(f"{len(unmarked)} wall(s) that carry nothing to look up in the schedule")
    ctx.proof("XC-SCHEDULE", lv, compared=compared, against=against,
              classes={"scheduled wall": tc, "scheduled wall run": rc},
              values={"tol": TOL, "sheet": lv.struct.sheet, "sheet2": sheet},
              elements=[e.id for e in marked], not_compared=not_cmp,
              result="match" if tc["thk_ok"] == tc["compared"] and rc["len_ok"] == rc["compared"]
              else "flag")
    # zone lengths: schedule against the detail; a disagreement is never resolved here
    for s in ctx.rr.schedules:
        dis = {m: (a, b) for m, a, b in s.disagreements()}
        marks = [m for m in s.marks() if s.detail(m) is not None
                 and s.row(m).zone_length_ft is not None]
        ends = lv.of(M.SW_END)
        for e in ends:
            row = s.row(e.mark)
            if row is None:
                continue
            if e.mark in dis:
                e.attrs["zone_disputed"] = dis[e.mark]
            elif row.zone_length_ft is not None:
                e.attrs["zone_length_ft"] = row.zone_length_ft
        for mark, (a, b) in dis.items():
            hosts = [e for e in ends if e.mark == mark]
            if not hosts:
                continue
            det = s.detail(mark)
            pts = [e.center for e in hosts]
            ctx.item(lv, "XC-ZONE", "answer", at=hosts[0].center, box=_bbox_of(pts),
                     elements=hosts, values={"mark": pt.Verbatim(mark), "sheet": s.sheet,
                                             "sched": M.fmt_ftin(a), "in_detail": M.fmt_ftin(b),
                                             "detail": (f"{det.detail}/{s.sheet}"
                                                        if det is not None and det.detail else "")},
                     key="XC-ZONE|" + mark)
        if marks:
            ctx.proof("XC-ZONE", lv, compared=f"wall schedule {s.sheet}",
                      classes={"scheduled mark": {"noun": ["scheduled mark", "scheduled marks"],
                                                  "total": len(s.marks()), "compared": len(marks),
                                                  "agree": len(marks) - len(dis)}},
                      values={"sheet": s.sheet},
                      not_compared=[f"mark {m} (the detail gives nothing for it)"
                                    for m in s.marks() if m not in marks],
                      result="flag" if dis else "match")


@_check("XC-OVERLAY", "openings by others against the structure")
def _overlay(ctx: Ctx, lv: M.Level) -> None:
    ovl = lv.of(M.OVERLAY_OPENING)
    if not lv.shops:
        raise CheckSkip("a shop drawing of this level is not in the drop")
    if not ovl:
        raise CheckSkip("the shop drawing of this level carries nothing labeled as an opening")
    against = "shop drawing " + ", ".join(sorted({e.prov.sheet for e in ovl}))
    compared = f"structural plan {lv.struct.sheet}"
    cl = {"noun": ["overlay opening", "overlay openings"], "total": len(ovl),
          "compared": len(ovl), "marked": 0, "slab": 0, "wall": 0, "zone": 0}
    slab_ops = lv.of(M.SLAB_OPENING)
    wall_ops = lv.of(M.WALL_OPENING)
    walls = lv.of(M.WALL)
    zones = []
    for e in lv.of(M.SW_END):
        lens = [e.attrs.get("zone_length_ft")] if e.attrs.get("zone_length_ft") else \
            list(e.attrs.get("zone_disputed") or [])
        if not lens:
            continue
        z = max(v for v in lens if v)
        a, b, u = e.attrs["face_a"], e.attrs["face_b"], e.attrs["inward"]
        zones.append((e, [a, b, (b[0] + u[0] * z, b[1] + u[1] * z),
                          (a[0] + u[0] * z, a[1] + u[1] * z)]))
    for o in ovl:
        c = o.centroid()
        box = _bbox_of(o.poly)
        hit_zone = next((z for z in zones if _polys_touch(o.poly, z[1])), None)
        hit_wall = next((w for w in walls if _polys_touch(o.poly, w.poly)), None)
        marked = any(g2.point_in_poly(c[0], c[1], s.poly) for s in slab_ops + wall_ops)
        vals = {"element": o.id, "sheet2": o.prov.sheet}
        if hit_zone is not None:
            cl["zone"] += 1
            ctx.item(lv, "XC-OVL-ZONE", "answer", at=c, box=box, elements=[o, hit_zone[0]],
                     values=dict(vals, mark=pt.Verbatim(hit_zone[0].mark)),
                     against=against, compared=compared, key="XC-OVL-ZONE|overlay")
        elif hit_wall is not None and not marked:
            cl["wall"] += 1
            ctx.item(lv, "XC-OVL-WALL", "answer", at=c, box=box, elements=[o, hit_wall],
                     values=dict(vals, wall=hit_wall.id), against=against, compared=compared,
                     key="XC-OVL-WALL|overlay")
        elif marked:
            cl["marked"] += 1
        else:
            cl["slab"] += 1
            ctx.item(lv, "XC-OVL-SLAB", "confirm", at=c, box=box, elements=[o], values=vals,
                     against=against, compared=compared, key="XC-OVL-SLAB|overlay")
    ctx.proof("XC-OVERLAY", lv, compared=compared, against=against,
              classes={"overlay opening": cl}, values={"sheet": lv.struct.sheet},
              elements=[e.id for e in ovl],
              result="flag" if cl["slab"] + cl["wall"] + cl["zone"] else "match")


def _polys_touch(a, b) -> bool:
    if any(g2.point_in_poly(p[0], p[1], b) for p in a):
        return True
    if any(g2.point_in_poly(p[0], p[1], a) for p in b):
        return True
    for i in range(len(a)):
        for j in range(len(b)):
            if g2.seg_intersection(a[i], a[(i + 1) % len(a)], b[j], b[(j + 1) % len(b)]):
                return True
    return False


def find_clouds(page) -> list:
    """Revision clouds on a page: ``[{"bbox", "number"}]`` (viewer points).
    A cloud is one stroked path made of many arcs; its number is the text
    inside the nearest small three-sided tag ("" when it has none)."""
    from .. import sightline as sl
    from . import gridread
    res = sl.gate_cached(page)
    by_seq: dict = {}
    for st in res.strokes:
        by_seq.setdefault(st.seq, []).append(st)
    clouds = []
    tags = []
    words = sl.words(page)
    for seq, sts in by_seq.items():
        curves = [s for s in sts if s.curve is not None]
        if len(curves) >= 8 and len(curves) == len(sts):
            pts = [p for s in sts for p in s.pts]
            if not gridread.find_circles(sts):
                clouds.append({"bbox": list(g2.bbox(pts)), "number": ""})
        elif len(sts) == 3 and all(s.curve is None for s in sts):
            pts = [p for s in sts for p in s.pts]
            bb = g2.bbox(pts)
            if max(bb[2] - bb[0], bb[3] - bb[1]) <= 40.0:
                inside = [w[4] for w in words
                          if bb[0] <= (w[0] + w[2]) / 2 <= bb[2] and bb[1] <= (w[1] + w[3]) / 2 <= bb[3]]
                if inside and inside[0].strip().isalnum():
                    tags.append((bb, inside[0].strip()))
    for c in clouds:
        cb = c["bbox"]
        best = None
        for bb, num in tags:
            dx = max(cb[0] - bb[2], 0.0, bb[0] - cb[2])
            dy = max(cb[1] - bb[3], 0.0, bb[1] - cb[3])
            d = math.hypot(dx, dy)
            if d <= 60.0 and (best is None or d < best[0]):
                best = (d, num)
        if best:
            c["number"] = best[1]
    return clouds


def _in_cloud(clouds, pbox):
    cx, cy = (pbox[0] + pbox[2]) / 2.0, (pbox[1] + pbox[3]) / 2.0
    for c in clouds:
        b = c["bbox"]
        if b[0] - 6 <= cx <= b[2] + 6 and b[1] - 6 <= cy <= b[3] + 6:
            return c
    return None


@_check("XC-REVISION", "revision against revision")
def _revision(ctx: Ctx, lv: M.Level) -> None:
    if lv.prior is None:
        raise CheckSkip("only one issue of this sheet is in the drop")
    import fitz
    from .. import drawdiff
    try:
        rep = drawdiff.diff_pages(lv.prior.file, lv.struct.file, lv.prior.page,
                                  lv.struct.page, align=None, log=lambda *_: None)
    except Exception as e:   # noqa: BLE001
        raise CheckSkip(f"the two issues could not be compared ({e})") from None
    doc = fitz.open(lv.struct.file)
    try:
        clouds = find_clouds(doc[lv.struct.page - 1])
    finally:
        doc.close()
    lv.clouds = clouds
    regions = [r for r in rep.get("regions", [])
               if lv.region is None or (lv.region[0] <= (r["bbox"][0] + r["bbox"][2]) / 2 <= lv.region[2]
                                        and lv.region[1] <= (r["bbox"][1] + r["bbox"][3]) / 2 <= lv.region[3])]
    places = []
    small = 0
    for r in regions:
        if (r.get("added_len_pt", 0.0) + r.get("removed_len_pt", 0.0) < MIN_CHANGE_PT
                and not r.get("has_text_change")):
            small += 1                     # too small to place; counted, not hidden
            continue
        places.append((r, False))
    cl = {"noun": ["changed place", "changed places"], "total": len(places),
          "compared": len(places), "clouded": 0}
    vals = {"sheet": lv.struct.sheet, "rev_old": pt.Verbatim(lv.prior.revision or "?"),
            "rev_new": pt.Verbatim(lv.struct.revision or "?")}
    lv.changes = []
    for r, _own in places:
        b = r["bbox"]
        c = _in_cloud(clouds, b)
        w = [lv.frame.to_world(b[0], b[1]), lv.frame.to_world(b[2], b[3])]
        wb = [min(w[0][0], w[1][0]), min(w[0][1], w[1][1]),
              max(w[0][0], w[1][0]), max(w[0][1], w[1][1])]
        lv.changes.append({"page_bbox": list(b), "world_bbox": wb,
                           "cloud": c["number"] if c else None})
        if c is not None:
            cl["clouded"] += 1
            continue
        ctx.item(lv, "XC-REVISION", "confirm", at=((wb[0] + wb[2]) / 2, (wb[1] + wb[3]) / 2),
                 box=wb, values=vals, key="XC-REVISION|place")
    ctx.proof("XC-REVISION", lv, compared=f"structural plan {lv.struct.sheet}",
              classes={"changed place": cl}, values=vals,
              not_compared=([f"{small} difference(s) shorter than {M.fmt_in(MIN_CHANGE_PT / lv.pt_per_ft)} "
                             "on the sheet, too small to place"] if small else []),
              result="flag" if cl["clouded"] < cl["total"] else "match")


@_check("SIGHT", "what the Sightline dropped, clipped and could not read")
def _sight(ctx: Ctx, lv: M.Level) -> None:
    rec = next((s["record"] for s in ctx.rr.sight if s["sheet"] == lv.struct.sheet), None)
    if rec is None:
        raise CheckSkip("the sheet was not gated")
    clouds = getattr(lv, "clouds", None)
    if clouds is None:
        import fitz
        doc = fitz.open(lv.struct.file)
        try:
            clouds = lv.clouds = find_clouds(doc[lv.struct.page - 1])
        finally:
            doc.close()

    def cls(noun, n):
        return {"noun": [noun, noun + "s"], "total": n, "compared": n}

    ctx.proof("SL-HIDDEN", lv, compared=f"structural plan {lv.struct.sheet}",
              classes={"hidden place": cls("hidden place", len(lv.hidden)),
                       "hidden stroke": cls("hidden stroke",
                                            sum(1 for d in rec.dropped if d["what"] == "stroke")),
                       "hidden fill": cls("hidden fill",
                                          sum(1 for d in rec.dropped if d["what"] == "fill")),
                       "clipped fill": cls("clipped fill",
                                           sum(1 for d in rec.clipped if d["what"] == "fill")),
                       "obscured shape": cls("obscured shape", len(rec.obscured))},
              values={"sheet": lv.struct.sheet}, result="flag" if lv.hidden else "info")
    for h in lv.hidden:
        wb = h["world_bbox"]
        parts = []
        if h["strokes"]:
            parts.append(f"{h['strokes']} line{'s' if h['strokes'] != 1 else ''}")
        if h["fills"]:
            parts.append(f"{h['fills']} fill{'s' if h['fills'] != 1 else ''}")
        near = None
        for c in clouds:
            b, p = c["bbox"], h["page_bbox"]
            dx = max(b[0] - p[2], 0.0, p[0] - b[2])
            dy = max(b[1] - p[3], 0.0, p[1] - b[3])
            if math.hypot(dx, dy) <= 90.0:
                near = c
        cloud = ""
        if near is not None:
            cloud = ("; a revision cloud is beside it" if not near["number"]
                     else f"; revision cloud {near['number']} is beside it")
        h["cloud"] = near["number"] if near else None
        ctx.item(lv, "SL-HIDDEN", "confirm", at=((wb[0] + wb[2]) / 2, (wb[1] + wb[3]) / 2),
                 box=wb, values={"what": " and ".join(parts), "cloud": cloud},
                 key="SL-HIDDEN|place")
    for u in lv.unread:
        wb = u["world_bbox"]
        ctx.item(lv, "SL-UNREAD", "confirm", at=((wb[0] + wb[2]) / 2, (wb[1] + wb[3]) / 2),
                 box=wb, key="SL-UNREAD|place")
    for e in lv.of(M.SLAB_EDGE):
        for s in e.attrs.get("segments", []):
            if s["kind"] != "unconfirmed":
                continue
            ln = math.hypot(s["b"][0] - s["a"][0], s["b"][1] - s["a"][1])
            mid = ((s["a"][0] + s["b"][0]) / 2, (s["a"][1] + s["b"][1]) / 2)
            ctx.item(lv, "SLAB-EDGE", "answer", at=mid, box=_bbox_of([s["a"], s["b"]]),
                     elements=[e], values={"length": M.fmt_ftin(ln),
                                           "what": "only a light line where the heavy edge "
                                                   "line should be"},
                     key="SLAB-EDGE|run")
        for g in e.attrs.get("gaps", []):
            ln = math.hypot(g["b"][0] - g["a"][0], g["b"][1] - g["a"][1])
            mid = ((g["a"][0] + g["b"][0]) / 2, (g["a"][1] + g["b"][1]) / 2)
            ctx.item(lv, "SLAB-EDGE", "answer", at=mid, box=_bbox_of([g["a"], g["b"]]),
                     elements=[e], values={"length": M.fmt_ftin(ln),
                                           "what": "nothing plotted across it"},
                     key="SLAB-EDGE|gap")


@_check("SINGLE-SOURCE", "which elements only one source carries")
def _single(ctx: Ctx, lv: M.Level) -> None:
    classes = {}
    for kind in (M.WALL, M.COLUMN, M.ROUND_COLUMN, M.CONCRETE, M.SLAB_EDGE, M.SLAB_OPENING,
                 M.WALL_OPENING, M.SW_END, M.MASONRY, M.OVERLAY_OPENING, M.PENETRATION):
        els = lv.of(kind)
        if not els:
            continue
        classes[M.KIND_NOUN[kind][0]] = {
            "noun": list(M.KIND_NOUN[kind]), "total": len(els), "compared": len(els),
            "single": sum(1 for e in els if e.single_source)}
        for e in els:
            e.attrs["single_source"] = e.single_source
    if not classes:
        raise CheckSkip("nothing was read on this level")
    ctx.proof("SINGLE-SOURCE", lv, compared=f"structural plan {lv.struct.sheet}",
              classes=classes, values={"sheet": lv.struct.sheet},
              elements=[e.id for e in lv.elements if e.single_source], result="info")


ORDER = ["GRID-PRINTED", "GRID-CAD", "XC-CORNER-CAD", "XC-FACE-CAD", "XC-CAD-ONLY",
         "XC-SCHEDULE", "XC-ARCH", "XC-LEVEL-BELOW", "XC-OVERLAY", "XC-REVISION", "SIGHT",
         "SINGLE-SOURCE"]


def run_checks(rr, trail: pt.Trail, register, opts: M.Options, names, log=lambda *_: None) -> Ctx:
    """Run every check on every readable level, low to high."""
    ctx = Ctx(rr, trail, register, opts, names)
    for lv in rr.levels:
        if lv.blocked:
            continue
        for cid in ORDER:
            try:
                CHECKS[cid]["fn"](ctx, lv)
            except CheckSkip as e:
                ctx.skips.append((cid, lv.code, str(e)))
                log(f"  {lv.code} {CHECKS[cid]['title']}: not compared ({e})")
    # run-wide records every plate and report prints from
    ctx.trail_scope = trail.add("SCOPE", values={"scope": ", ".join(M.NOT_LAID_OUT)},
                                not_compared=list(M.NOT_LAID_OUT), result="info")
    if rr.frame_label:
        ctx.trail_frame = trail.add("FRAME", values={"frame": pt.Verbatim(rr.frame_label)},
                                    result="info")
    _apply_answers(ctx)
    return ctx


def _apply_answers(ctx: Ctx) -> None:
    """User-set register fields.  An answer needs an RFI that is IN the
    drop and a quote that is really in its answer text."""
    rfis = {r.rfi: r for r in ctx.rr.rfis}
    ctx.answer_problems = []
    for a in ctx.opts.answered or []:
        ref, num, quote = str(a.get("item", "")), str(a.get("rfi", "")).zfill(3), str(a.get("quote", ""))
        tag = ctx.reg.get(ref)
        if tag is None:
            ctx.answer_problems.append(f"answer for item {ref}: there is no such item")
            continue
        src = rfis.get(num)
        if src is None:
            ctx.answer_problems.append(
                f"answer for item {ref}: RFI {num} is not in the drop")
            continue
        if " ".join(quote.split()).upper() not in " ".join(src.answer.split()).upper() or not quote.strip():
            ctx.answer_problems.append(
                f"answer for item {ref}: the quote is not in the answer text of RFI {num}")
            continue
        ctx.reg.answer(ref, num, quote)
        for lv in ctx.rr.levels:
            for e in lv.elements:
                if ref in e.items:
                    e.attrs["rfi"] = num
                    if M.SRC_RFI not in e.sources:
                        e.sources.append(M.SRC_RFI)
                    if "per answered RFI" not in e.prov.note:
                        e.prov.note = (e.prov.note + "; " if e.prov.note else "") + \
                            f"per answered RFI {num}"
    for ref, text in (ctx.opts.statuses or {}).items():
        if ctx.reg.get(ref) is not None:
            ctx.reg.set_status(ref, str(text))
