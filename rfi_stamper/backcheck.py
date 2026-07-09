"""The Backcheck -- Planloom's instant peer checker (ROADMAP Phase G).

A *backcheck* is the senior reviewer's red-pen pass over a drawing before it
is issued: the peer who has seen every mistake and catches it in seconds.
This module is that reviewer, encoded.  Every finding is DETERMINISTIC -- it
cites the rule that produced it (the Ground Truth promise) -- because there is
no generative model here to guess with.  The Backcheck flags only what it can
PROVE from the geometry, text and structure it can actually read, and it is
honest about what it cannot: where a check genuinely needs data an offline
plan/BIM app does not have (mechanical part solids for GD&T/hole callouts,
injection-molding pull direction for draft angles, wall-penetration sleeve
clash data), the rule is registered as SKIPPED with a plain-words reason that
lands in ``Report.stats["skipped"]`` so the UI can tell the user "not checked,
and why".  That honesty is the product.

Honest input boundary (say this in the GUI too): native proprietary CAD/BIM
containers are closed formats an offline from-scratch engine cannot parse.
The Backcheck reads what Planloom already reads -- vector plan PDFs (fitz
text + drawings), Loft drafts (:class:`draft.DraftModel`, STRUCTURED, the
strongest checks), Pipewright networks on a Loft, DXF (light ASCII parser),
and OBJ (:func:`bim.load_obj`).  "Export to PDF/DXF" is the honest bridge.

Fully offline (invariant #1): stdlib + the bundled ``fitz`` only, with
``draft``/``pipewright``/``sheets``/``hyperlink``/``heartwood``/``markups``
imported lazily.  No networking, no vendor/company/person names (invariant
#7).  Findings can be rendered onto the design as real cloud+callout
annotations through the markup bridge at the bottom of this file.
"""
from __future__ import annotations

import math
import os
import re
import uuid
from dataclasses import dataclass, field

# --------------------------------------------------------------- vocabulary --

#: Severity ranks, worst first -- the sort order for a review pass.
SEVERITIES = ("blocker", "major", "minor", "info")

#: Finding categories (the reviewer's chapters):
#:   data       -- inconsistencies in technical data
#:   ambiguity  -- ambiguous / incomplete drawings
#:   geometry   -- design flaws in the geometry itself
#:   standards  -- non-conformance to standards
#:   lessons    -- conflicts with lessons learned (the Heartwood lane)
#:   dfx        -- design-for-construction / constructability
CATEGORIES = ("data", "ambiguity", "geometry", "standards", "lessons", "dfx")

_SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}

#: Markup cloud color per severity (used by the markup bridge).
SEVERITY_COLORS = {
    "blocker": "#c1121f",
    "major": "#e8590c",
    "minor": "#e0a800",
    "info": "#3f6fe0",
}

# --------------------------------------------------------------- thresholds --
# Each constant carries its basis; the soft norms say "verify against project
# standard" out loud, because a reviewer's rule of thumb is not a code cite.

#: Interior angle below which two walls sharing a corner read as a sharp
#: sliver -- hard to frame, hard to finish.  Verify against project standard.
SHARP_DEG = 30.0

#: An endpoint gap in (0, SNAP_GAP_FT] is an unclosed corner / near-miss:
#: close enough to be meant-to-touch, not actually touching.  1/4 ft.
SNAP_GAP_FT = 0.25

#: Two roughly-parallel walls with less than this clear distance read as a
#: corridor pinch / egress concern.  3 ft is a rule of thumb -- verify against
#: the governing building & accessibility code.
DFX_PINCH_FT = 3.0

#: A wall segment shorter than this is a sliver -- usually a stray or a
#: mis-snapped drag.  Verify against project standard.
DFX_MIN_WALL_FT = 0.5

#: Two walls whose directions are within this of parallel are treated as
#: parallel for the pinch test.
PARALLEL_DEG = 12.0

#: Duplicate-dimension endpoint match tolerance: 1/16 in in feet.
DUP_DIM_TOL_FT = (1.0 / 16.0) / 12.0

#: Minimum plotted lettering height, paper inches (the classic 3/32").
#: Below this a note is unreadable on a print.  Verify against CAD standard.
MIN_TEXT_IN = 3.0 / 32.0

#: Cluster tolerance for coincident wall endpoints (a corner "node").
NODE_TOL_FT = 0.02

#: How far past a room's enclosing walls a dimension may sit and still count
#: as dimensioning that room (dims ride just outside the wall face).
DIM_ASSOC_MARGIN_FT = 2.0

#: A drainage dead-end wants a cleanout within about this spacing -- a common
#: maximum.  Verify against the project plumbing code.
CO_SPACING_FT = 100.0

#: Vague phrases that push a decision downstream / off the drawing.  Value is
#: the finding severity ("by others"/"TBD" are the load-bearing ones).
VAGUE_LEXICON = {
    "by others": "major",
    "tbd": "major",
    "to be determined": "major",
    "as required": "minor",
    "as needed": "minor",
    "verify in field": "minor",
    "v.i.f.": "minor",
    "match existing": "minor",
    "re-verify in field": "minor",
    "similar": "minor",
    "typ.": "minor",
    "typ": "minor",
}

#: Material keyword -> coarse material GROUP.  Two different groups claimed by
#: the SAME tag token is a technical-data conflict.  Deliberately small and
#: conservative: describe assemblies by generic material, never by brand.
MATERIAL_GROUPS = {
    "cmu": "masonry",
    "masonry": "masonry",
    "brick": "masonry",
    "block": "masonry",
    "concrete": "concrete",
    "cast-in-place": "concrete",
    "cast in place": "concrete",
    "cip": "concrete",
    "steel": "steel",
    "wide-flange": "steel",
    "wood": "wood",
    "timber": "wood",
    "gypsum": "gypsum",
    "drywall": "gypsum",
}

#: Pipe material substring -> Harvest stride rule for the unsupported-span
#: check (harvest.STRIDE_RULES carries the basis + verified flag).
_MATERIAL_STRIDE = (
    ("cast iron", "CAST-IRON"),
    ("cpvc", "CPVC"),
    ("pvc", "PVC"),
    ("copper", "COPPER"),
    ("steel", "STEEL-THREADED"),
)

#: A page carries a scale when one of these appears.  N.T.S. counts as an
#: explicit scale declaration (so it is NOT a "no scale" page).
_SCALE_RE = re.compile(
    r"(\d+(?:\s+\d+/\d+|/\d+)?\s*[\"']?\s*=\s*\d+\s*['\-]|"
    r"\bN\.?\s*T\.?\s*S\.?\b|\bSCALE\b|\b1\s*:\s*\d+\b)",
    re.IGNORECASE)

#: A cover/schedule tag that a material keyword may hang off of (W1, C-2, ...).
_TAG_RE = re.compile(r"\b([A-Z]{1,3}-?\d{1,2}[A-Z]?)\b")

_EPS = 1e-9


# ------------------------------------------------------------ finding model --

@dataclass
class Finding:
    """One proven defect.  ``rule`` is the basis text that produced it -- the
    Ground Truth promise -- and ``suggestion`` says what to do about it.
    ``where`` is ``(x, y)`` or ``(x0, y0, x1, y1)`` in the source's native
    coordinates: PDF viewer page points (top-left, y down -- markup space) or
    Loft model feet (y up)."""
    id: str
    code: str
    category: str
    severity: str
    title: str
    detail: str
    suggestion: str
    rule: str
    page: int | None = None
    where: tuple | None = None
    source: str = ""
    ent_ids: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "code": self.code, "category": self.category,
            "severity": self.severity, "title": self.title,
            "detail": self.detail, "suggestion": self.suggestion,
            "rule": self.rule, "page": self.page,
            "where": list(self.where) if self.where is not None else None,
            "source": self.source, "ent_ids": list(self.ent_ids),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Finding":
        where = d.get("where")
        return cls(
            id=str(d.get("id") or uuid.uuid4().hex),
            code=str(d.get("code", "")), category=str(d.get("category", "")),
            severity=str(d.get("severity", "info")),
            title=str(d.get("title", "")), detail=str(d.get("detail", "")),
            suggestion=str(d.get("suggestion", "")),
            rule=str(d.get("rule", "")), page=d.get("page"),
            where=tuple(where) if where is not None else None,
            source=str(d.get("source", "")),
            ent_ids=list(d.get("ent_ids") or []))


@dataclass
class Report:
    """The review pass.  ``stats`` carries by-severity/by-category tallies,
    the list of rule codes that actually ran (``checked``) and the honest
    ``skipped`` list -- each ``{"code", "reason"}`` -- so the UI can show what
    was NOT checked and why."""
    findings: list = field(default_factory=list)
    source: str = ""
    stats: dict = field(default_factory=dict)

    def by_category(self, cat: str) -> list:
        return [f for f in self.findings if f.category == cat]

    def by_severity(self, sev: str) -> list:
        return [f for f in self.findings if f.severity == sev]

    def sort(self) -> "Report":
        """Order blocker -> info, then by page (None last), then code."""
        self.findings.sort(key=lambda f: (
            _SEV_RANK.get(f.severity, 99),
            f.page if f.page is not None else 1 << 30,
            f.code))
        return self

    def to_dict(self) -> dict:
        return {"source": self.source, "stats": self.stats,
                "findings": [f.to_dict() for f in self.findings]}


# ------------------------------------------------------------ rule registry --

#: {code: {"category","severity","title","rule","inputs": set, "fn"}}.  A rule
#: fn is PURE(ctx) -> list[Finding]; the runner wraps each in try/except so one
#: bad rule never sinks the report.  ``inputs`` is the source kinds it applies
#: to: "pdf" | "loft" | "pipe" | "dxf" | "obj".
RULES: dict = {}

#: Rules that CANNOT be honestly evaluated by an offline plan/BIM app.  Each
#: is surfaced in ``stats["skipped"]`` with a plain reason so nothing is
#: silently un-checked.  This is the honesty the product is built on.
SKIPPED_RULES: dict = {
    "STD-HOLE-GDT": {
        "category": "standards", "severity": "info",
        "title": "Hole / GD&T callout conformance",
        "inputs": {"pdf", "loft", "dxf", "obj"},
        "reason": "Not checked: GD&T feature-control frames and hole "
                  "callouts need a mechanical part solid with toleranced "
                  "features. A plan/BIM sheet app has no part model to read "
                  "them from.",
    },
    "STD-SLEEVE": {
        "category": "standards", "severity": "info",
        "title": "Wall-penetration sleeve present",
        "inputs": {"pdf", "loft"},
        "reason": "Not checked: proving a sleeve at every wall penetration "
                  "needs MEP-vs-structure clash data (which pipe crosses "
                  "which rated wall) that an offline 2D draft does not carry.",
    },
    "DFX-DRAFT-ANGLE": {
        "category": "dfx", "severity": "info",
        "title": "Molded-part draft angle / parting line",
        "inputs": {"loft", "obj"},
        "reason": "Not checked: injection-molding draft angles and parting "
                  "lines need a solid part model plus a declared pull "
                  "direction -- out of scope for a plan/BIM app.",
    },
}


def _rule(code, category, severity, title, rule, inputs):
    """Register a rule fn and its metadata."""
    def deco(fn):
        RULES[code] = {"category": category, "severity": severity,
                       "title": title, "rule": rule, "inputs": set(inputs),
                       "fn": fn}
        return fn
    return deco


def _finding(code, detail, suggestion, *, severity=None, title=None,
             page=None, where=None, ent_ids=None, rule=None) -> Finding:
    """Build a Finding, defaulting category/severity/title/rule from the
    registry (per-instance overrides allowed for severity/title/rule)."""
    meta = RULES[code]
    return Finding(
        id=uuid.uuid4().hex, code=code, category=meta["category"],
        severity=severity or meta["severity"], title=title or meta["title"],
        detail=detail, suggestion=suggestion, rule=rule or meta["rule"],
        page=page, where=tuple(where) if where is not None else None,
        ent_ids=list(ent_ids or []))


# --------------------------------------------------------------- the context --

class _Ctx:
    """Everything a rule fn might need, assembled once per check.  Rules pull
    only what their inputs require; the pipe network is built lazily."""

    def __init__(self, source, *, model=None, pdf_path=None, doc=None,
                 index=None, dxf_path=None, obj_path=None, log=print):
        self.source = source
        self.model = model
        self.pdf_path = pdf_path
        self.doc = doc
        self.index = index
        self.dxf_path = dxf_path
        self.obj_path = obj_path
        self.log = log
        self._net = None
        self._fittings = None

    def net(self):
        if self._net is None:
            from . import pipewright
            self._net = pipewright.network(self.model)
        return self._net

    def fittings(self):
        if self._fittings is None:
            from . import pipewright
            self._fittings = pipewright.derive_fittings(self.model, self.net())
        return self._fittings


# --------------------------------------------------------- geometry helpers --

def _walls(model) -> list:
    """[(ent, a, b, u, n, half, L)] for every well-formed wall."""
    out = []
    for e in model.ents:
        if e.kind != "wall" or len(e.pts) != 2:
            continue
        a, b = e.pts
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L <= _EPS:
            continue
        u = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
        n = (-u[1], u[0])
        half = float(e.props.get("thick_in", 4.75)) / 24.0
        out.append((e, tuple(a), tuple(b), u, n, half, L))
    return out


def _point_in_wall(pt, frame, pad=0.0) -> bool:
    """Is pt inside a wall's rectangular body (centerline +/- half)?"""
    _e, a, _b, u, n, half, L = frame
    along = (pt[0] - a[0]) * u[0] + (pt[1] - a[1]) * u[1]
    perp = (pt[0] - a[0]) * n[0] + (pt[1] - a[1]) * n[1]
    return -pad <= along <= L + pad and abs(perp) <= half + pad


def _fixture_aabb(ent):
    """Axis-aligned bbox (x0,y0,x1,y1) of a fixture's nominal footprint in
    model feet, or None when the stencil is unknown / unplaced."""
    from .draft import STENCILS
    if not ent.pts:
        return None
    spec = STENCILS.get(str(ent.props.get("stencil", "")))
    if not spec:
        return None
    hw = float(spec["w_in"]) / 24.0
    hd = float(spec["d_in"]) / 24.0
    cx, cy = ent.pts[0]
    theta = math.radians(float(ent.props.get("rot", 0.0)))
    c, s = math.cos(theta), math.sin(theta)
    xs, ys = [], []
    for lx, ly in ((-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd)):
        xs.append(cx + lx * c - ly * s)
        ys.append(cy + lx * s + ly * c)
    return (min(xs), min(ys), max(xs), max(ys))


def _aabb_overlap(a, b, pad=0.0) -> bool:
    return (a[0] <= b[2] + pad and b[0] <= a[2] + pad
            and a[1] <= b[3] + pad and b[1] <= a[3] + pad)


def _ray_wall_dist(pt, d, walls):
    """Nearest (dist, wall_ent) a ray pt+t*d (t>0) crosses, or None."""
    best = None
    for frame in walls:
        _e, a, b, _u, _n, _half, _L = frame
        rx, ry = d
        sx, sy = b[0] - a[0], b[1] - a[1]
        den = rx * sy - ry * sx
        if abs(den) <= 1e-12:
            continue
        qx, qy = a[0] - pt[0], a[1] - pt[1]
        t = (qx * sy - qy * sx) / den
        u = (qx * ry - qy * rx) / den
        if t > 1e-6 and -1e-9 <= u <= 1.0 + 1e-9:
            if best is None or t < best[0]:
                best = (t, frame[0])
    return best


def _room_zone(model, pt, walls):
    """(bbox, {wall ids the 4 axis rays hit}) for the room whose tag sits at
    pt -- the enclosing cell found by casting rays to the nearest wall in
    +/-x and +/-y.  A direction with no wall falls back to a default reach so
    the box is always defined."""
    px, py = pt
    reach = {}
    hit_ids = set()
    for name, d in (("r", (1, 0)), ("l", (-1, 0)),
                    ("u", (0, 1)), ("dn", (0, -1))):
        h = _ray_wall_dist((px, py), d, walls)
        if h is not None:
            reach[name] = h[0]
            hit_ids.add(h[1].id)
        else:
            reach[name] = 6.0
    bbox = (px - reach["l"], py - reach["dn"],
            px + reach["r"], py + reach["u"])
    return bbox, hit_ids


def _in_sweep(pt, hinge, r, a0, extent) -> bool:
    """Is pt inside the quarter-disk a door leaf sweeps (center hinge, radius
    r, angular span [a0, a0+extent] degrees CCW)?"""
    dx, dy = pt[0] - hinge[0], pt[1] - hinge[1]
    d = math.hypot(dx, dy)
    if d <= 1e-6 or d > r + 1e-9:
        return False
    bearing = math.degrees(math.atan2(dy, dx)) % 360.0
    rel = (bearing - a0) % 360.0
    return rel <= extent + 1e-6


# =====================================================================
#  data -- inconsistencies in technical data
# =====================================================================

@_rule("DATA-SHEETDUP", "data", "major", "Duplicate sheet number",
       "Every sheet in a set carries a unique number; the same number on two "
       "pages breaks the index, the log and every cross-reference.",
       {"pdf"})
def _r_sheetdup(ctx) -> list:
    groups: dict = {}
    for p in ctx.index.pages:
        if re.match(r"^PAGE-\d+$", p.sheet):
            continue
        groups.setdefault(p.sheet, []).append(p.page_no)
    out = []
    for sheet, pages in groups.items():
        if len(pages) > 1:
            out.append(_finding(
                "DATA-SHEETDUP",
                f"Sheet {sheet} is detected on pages "
                f"{', '.join(map(str, pages))}.",
                "Renumber the duplicate so each sheet number is unique.",
                page=pages[1]))
    return out


@_rule("DATA-SHEETGAP", "data", "info", "Sheet-series gap",
       "A missing number in an otherwise continuous sheet series is often a "
       "dropped or mis-numbered sheet -- best-effort, verify against the "
       "issued sheet index.",
       {"pdf"})
def _r_sheetgap(ctx) -> list:
    series: dict = {}
    for p in ctx.index.pages:
        m = re.match(r"^([A-Z]{1,3})-(\d{1,3})$", p.sheet)
        if not m:
            continue
        series.setdefault(m.group(1), set()).add(int(m.group(2)))
    out = []
    for letters, nums in series.items():
        if len(nums) < 3:
            continue
        lo, hi = min(nums), max(nums)
        missing = [n for n in range(lo, hi + 1) if n not in nums]
        if missing and len(missing) <= max(1, (hi - lo) // 2):
            out.append(_finding(
                "DATA-SHEETGAP",
                f"Series {letters}- runs {lo}..{hi} but is missing "
                f"{', '.join(f'{letters}-{n}' for n in missing)}.",
                "Confirm the missing sheet(s) are intentional, not dropped."))
    return out


@_rule("DATA-TITLEBLOCK", "data", "minor", "Missing title-block sheet number",
       "Every sheet needs a sheet number in its title block; a page where "
       "none could be detected is missing that field or uses a non-standard "
       "format.",
       {"pdf"})
def _r_titleblock(ctx) -> list:
    out = []
    for p in ctx.index.pages:
        if re.match(r"^PAGE-\d+$", p.sheet):
            out.append(_finding(
                "DATA-TITLEBLOCK",
                f"Page {p.page_no} has no detectable sheet number in its "
                "title-block corner.",
                "Add / correct the sheet-number field so the set indexes.",
                page=p.page_no))
    return out


@_rule("DATA-DUPDIM", "data", "major", "Duplicate / contradictory dimension",
       "Two dimensions over the same endpoints must agree; different values "
       "over one span is a contradiction (one is wrong), identical values is "
       "redundant clutter.",
       {"loft"})
def _r_dupdim(ctx) -> list:
    dims = []
    for e in ctx.model.ents:
        if e.kind != "dim" or len(e.pts) < 2:
            continue
        a, b = e.pts[0], e.pts[1]
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        text = str(e.props.get("text", "")).strip()
        from .draft import fmt_ftin
        display = text or fmt_ftin(L)
        dims.append((e, a, b, L, display))
    out = []
    seen = set()
    for i in range(len(dims)):
        ei, ai, bi, Li, di = dims[i]
        for j in range(i + 1, len(dims)):
            ej, aj, bj, Lj, dj = dims[j]
            same = (_pt_close(ai, aj) and _pt_close(bi, bj)) or \
                   (_pt_close(ai, bj) and _pt_close(bi, aj))
            if not same:
                continue
            key = tuple(sorted((ei.id, ej.id)))
            if key in seen:
                continue
            seen.add(key)
            mid = ((ai[0] + bi[0]) / 2.0, (ai[1] + bi[1]) / 2.0)
            if di != dj or abs(Li - Lj) > DUP_DIM_TOL_FT:
                out.append(_finding(
                    "DATA-DUPDIM",
                    f"Two dimensions over the same span read {di} and {dj}.",
                    "Reconcile the dimensions -- one is wrong.",
                    severity="major", where=mid, ent_ids=[ei.id, ej.id]))
            else:
                out.append(_finding(
                    "DATA-DUPDIM",
                    f"Two dimensions redundantly repeat {di} over one span.",
                    "Delete the duplicate dimension.",
                    severity="minor", where=mid, ent_ids=[ei.id, ej.id]))
    return out


def _pt_close(a, b) -> bool:
    return (abs(a[0] - b[0]) <= DUP_DIM_TOL_FT
            and abs(a[1] - b[1]) <= DUP_DIM_TOL_FT)


@_rule("DATA-MATERIAL", "data", "info", "Conflicting material for one tag",
       "The same tag/callout token should name one material; two different "
       "material groups on one tag is a technical-data conflict -- verify "
       "against the material legend / wall-type schedule.",
       {"pdf"})
def _r_material(ctx) -> list:
    tag_groups: dict = {}
    for i in range(ctx.doc.page_count):
        text = ctx.doc[i].get_text("text") or ""
        for line in text.splitlines():
            for tm in _TAG_RE.finditer(line):
                tag = tm.group(1)
                tail = line[tm.end(): tm.end() + 28].lower()
                for word, group in MATERIAL_GROUPS.items():
                    if word in tail:
                        tag_groups.setdefault((i + 1, tag), set()).add(group)
    out = []
    for (page, tag), groups in tag_groups.items():
        if len(groups) >= 2:
            out.append(_finding(
                "DATA-MATERIAL",
                f"Tag {tag} on page {page} is associated with conflicting "
                f"materials: {', '.join(sorted(groups))}.",
                "Reconcile the tag to a single material in the schedule.",
                page=page))
    return out


# =====================================================================
#  ambiguity -- ambiguous / incomplete drawings
# =====================================================================

@_rule("AMB-DANGLING-REF", "ambiguity", "major", "Dangling sheet reference",
       "A detail/section/sheet reference must point to a sheet that exists in "
       "the set; a reference to a sheet not in the set is a dead cross-link.",
       {"pdf"})
def _r_dangling(ctx) -> list:
    from . import hyperlink
    tokens = hyperlink._unresolved(ctx.doc, ctx.index)
    out = []
    for tok in tokens:
        page, where = _first_hit(ctx.doc, tok)
        out.append(_finding(
            "AMB-DANGLING-REF",
            f"Reference to sheet {tok} but no sheet {tok} is in this set.",
            "Add the missing sheet, or correct the reference to a real one.",
            page=page, where=where))
    return out


@_rule("AMB-VAGUE-NOTE", "ambiguity", "minor", "Vague / deferred note",
       "Phrases that defer a decision off the drawing ('by others', 'as "
       "required', 'verify in field', 'TBD') leave the work ambiguous -- "
       "resolve them or name who/what governs.",
       {"pdf", "loft"})
def _r_vague(ctx) -> list:
    out = []
    if ctx.source == "pdf":
        for i in range(ctx.doc.page_count):
            page = ctx.doc[i]
            text = page.get_text("text") or ""
            for phrase, sev in _vague_hits(text):
                where = None
                try:
                    rects = page.search_for(phrase)
                    if rects:
                        r = rects[0]
                        where = (r.x0, r.y0, r.x1, r.y1)
                except Exception:
                    where = None
                out.append(_finding(
                    "AMB-VAGUE-NOTE",
                    f'Vague note "{phrase}" on page {i + 1}.',
                    "Replace with a specific value, detail, or a named "
                    "responsible party.",
                    severity=sev, page=i + 1, where=where))
        return out
    for e in ctx.model.ents:
        if e.kind != "text":
            continue
        txt = str(e.props.get("text", ""))
        for phrase, sev in _vague_hits(txt):
            out.append(_finding(
                "AMB-VAGUE-NOTE",
                f'Vague note "{phrase}" in text "{txt[:40]}".',
                "Replace with a specific value or a named responsible party.",
                severity=sev, where=tuple(e.pts[0]) if e.pts else None,
                ent_ids=[e.id]))
    return out


@_rule("AMB-NO-SCALE", "ambiguity", "minor", "Drawing with no scale",
       "A page with drawn content must declare a scale (a '1/8\" = 1'-0\"' "
       "family token, an explicit SCALE label, or N.T.S.); a scaleless "
       "drawing cannot be measured.",
       {"pdf"})
def _r_noscale(ctx) -> list:
    out = []
    for i in range(ctx.doc.page_count):
        page = ctx.doc[i]
        try:
            has_content = bool(page.get_drawings())
        except Exception:
            has_content = False
        if not has_content:
            continue
        text = page.get_text("text") or ""
        if _SCALE_RE.search(text):
            continue
        out.append(_finding(
            "AMB-NO-SCALE",
            f"Page {i + 1} has drawn content but no scale token.",
            "Add the drawing scale (or N.T.S. where genuinely not to scale).",
            page=i + 1))
    return out


@_rule("AMB-UNLABELED", "ambiguity", "minor", "Unlabeled room or grid",
       "A room needs a name and a number; a grid line needs a label. An "
       "unlabeled element cannot be referenced.",
       {"loft"})
def _r_unlabeled(ctx) -> list:
    out = []
    for e in ctx.model.ents:
        if e.kind == "room":
            missing = []
            if not str(e.props.get("name", "")).strip():
                missing.append("name")
            if not str(e.props.get("number", "")).strip():
                missing.append("number")
            if missing:
                out.append(_finding(
                    "AMB-UNLABELED",
                    f"Room is missing its {' and '.join(missing)}.",
                    "Fill in the room name and number.",
                    where=tuple(e.pts[0]) if e.pts else None, ent_ids=[e.id]))
        elif e.kind == "grid" and not str(e.props.get("label", "")).strip():
            out.append(_finding(
                "AMB-UNLABELED", "Grid line has no label.",
                "Give the grid line its number or letter.",
                where=tuple(e.pts[0]) if e.pts else None, ent_ids=[e.id]))
    return out


@_rule("AMB-UNDIM-ROOM", "ambiguity", "minor", "Room not dimensioned",
       "Every room should carry at least one dimension so it can be laid "
       "out; a room with no dimension near its walls is under-defined.",
       {"loft"})
def _r_undim_room(ctx) -> list:
    walls = _walls(ctx.model)
    dims = [e for e in ctx.model.ents if e.kind == "dim" and len(e.pts) >= 2]
    if not walls or not dims:
        return []
    out = []
    for e in ctx.model.ents:
        if e.kind != "room" or not e.pts:
            continue
        zone, _ids = _room_zone(ctx.model, e.pts[0], walls)
        ex = (zone[0] - DIM_ASSOC_MARGIN_FT, zone[1] - DIM_ASSOC_MARGIN_FT,
              zone[2] + DIM_ASSOC_MARGIN_FT, zone[3] + DIM_ASSOC_MARGIN_FT)
        associated = False
        for d in dims:
            xs = [p[0] for p in d.pts]
            ys = [p[1] for p in d.pts]
            dbb = (min(xs), min(ys), max(xs), max(ys))
            if _aabb_overlap(dbb, ex):
                associated = True
                break
        if not associated:
            out.append(_finding(
                "AMB-UNDIM-ROOM",
                f"Room {str(e.props.get('number', '')) or ''} has no "
                "dimension near its enclosing walls.",
                "Add a dimension string to lock the room's size.",
                where=tuple(e.pts[0]), ent_ids=[e.id]))
    return out


# =====================================================================
#  geometry -- design flaws
# =====================================================================

@_rule("GEO-SHARP-CORNER", "geometry", "minor", "Sharp wall corner",
       f"Two walls meeting below {SHARP_DEG:.0f} degrees form a sliver that "
       "is hard to frame and finish -- verify against project standard.",
       {"loft"})
def _r_sharp(ctx) -> list:
    from .pipewright import _included
    nodes = _wall_corner_nodes(ctx.model)
    out = []
    for key, members in nodes.items():
        if len(members) < 2:
            continue
        worst = None
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                ang = _included(members[i][1], members[j][1])
                if worst is None or ang < worst[0]:
                    worst = (ang, members[i][0], members[j][0])
        if worst and worst[0] < SHARP_DEG - _EPS:
            out.append(_finding(
                "GEO-SHARP-CORNER",
                f"Walls meet at {worst[0]:.0f} degrees "
                f"(under {SHARP_DEG:.0f}).",
                "Open the corner or resolve the sliver with a real detail.",
                where=key, ent_ids=[worst[1], worst[2]]))
    return out


@_rule("GEO-OPEN-WALL", "geometry", "major", "Unclosed corner / near-miss",
       f"A wall endpoint within {SNAP_GAP_FT:g} ft of another endpoint but "
       "not touching it is an unclosed corner -- it renders closed but the "
       "geometry has a gap.",
       {"loft"})
def _r_openwall(ctx) -> list:
    walls = _walls(ctx.model)
    ends = []
    for frame in walls:
        ends.append((frame[0].id, frame[1]))
        ends.append((frame[0].id, frame[2]))
    out = []
    seen = set()
    for i in range(len(ends)):
        id_i, pi = ends[i]
        for j in range(i + 1, len(ends)):
            id_j, pj = ends[j]
            if id_i == id_j:
                continue
            d = math.hypot(pi[0] - pj[0], pi[1] - pj[1])
            if 1e-6 < d <= SNAP_GAP_FT:
                key = tuple(sorted((id_i, id_j))) + (round(d, 4),)
                if key in seen:
                    continue
                seen.add(key)
                mid = ((pi[0] + pj[0]) / 2.0, (pi[1] + pj[1]) / 2.0)
                out.append(_finding(
                    "GEO-OPEN-WALL",
                    f"Wall endpoints {d * 12.0:.2f} in apart -- the corner "
                    "does not close.",
                    "Snap the endpoints together (Plumbline endpoint snap).",
                    where=mid, ent_ids=[id_i, id_j]))
    return out


@_rule("GEO-OVERLAP", "geometry", "major", "Overlapping elements",
       "A fixture sitting inside a wall body, or two fixtures whose plan "
       "footprints overlap, is a clash that cannot be built as drawn.",
       {"loft"})
def _r_overlap(ctx) -> list:
    walls = _walls(ctx.model)
    fixtures = [e for e in ctx.model.ents if e.kind == "fixture" and e.pts]
    out = []
    for e in fixtures:
        pt = tuple(e.pts[0])
        for frame in walls:
            if _point_in_wall(pt, frame):
                out.append(_finding(
                    "GEO-OVERLAP",
                    f"Fixture ({e.props.get('stencil', '')}) sits inside a "
                    "wall body.",
                    "Move the fixture clear of the wall thickness.",
                    where=pt, ent_ids=[e.id, frame[0].id]))
                break
    boxes = [(e, _fixture_aabb(e)) for e in fixtures]
    boxes = [(e, b) for e, b in boxes if b is not None]
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            ea, ba = boxes[i]
            eb, bb = boxes[j]
            if _aabb_overlap(ba, bb, pad=-0.01):
                cx = (max(ba[0], bb[0]) + min(ba[2], bb[2])) / 2.0
                cy = (max(ba[1], bb[1]) + min(ba[3], bb[3])) / 2.0
                out.append(_finding(
                    "GEO-OVERLAP",
                    f"Fixtures ({ea.props.get('stencil', '')} and "
                    f"{eb.props.get('stencil', '')}) overlap in plan.",
                    "Separate the fixtures to their required clearances.",
                    where=(cx, cy), ent_ids=[ea.id, eb.id]))
    return out


@_rule("GEO-DEGENERATE", "geometry", "info", "Degenerate geometry",
       "Zero-length segments, stacked duplicate points, open edges and "
       "floating islands are geometry errors that corrupt downstream export.",
       {"loft", "dxf", "obj"})
def _r_degenerate(ctx) -> list:
    if ctx.source == "loft":
        return _degenerate_loft(ctx.model)
    if ctx.source == "dxf":
        return _degenerate_dxf(ctx.dxf_path)
    return _degenerate_obj(ctx.obj_path)


# =====================================================================
#  standards -- non-conformance
# =====================================================================

@_rule("STD-TEXT-MIN", "standards", "minor", "Lettering below minimum",
       f"Plotted lettering under {MIN_TEXT_IN:.4f} in (3/32\") is unreadable "
       "on a print -- verify against the project CAD/lettering standard.",
       {"loft"})
def _r_text_min(ctx) -> list:
    out = []
    for e in ctx.model.ents:
        if e.kind != "text":
            continue
        paper_in = _text_paper_in(e.props.get("size", "body"))
        if paper_in is not None and paper_in < MIN_TEXT_IN - 1e-6:
            out.append(_finding(
                "STD-TEXT-MIN",
                f'Text "{str(e.props.get("text", ""))[:24]}" plots at '
                f"{paper_in:.4f} in, under the {MIN_TEXT_IN:.4f} in minimum.",
                "Raise the lettering height to at least 3/32\".",
                where=tuple(e.pts[0]) if e.pts else None, ent_ids=[e.id]))
    return out


@_rule("STD-TITLEBLOCK-FIELD", "standards", "minor",
       "Missing title-block field",
       "A plate's title block needs a title and a sheet/plate number -- "
       "verify against the project standard.",
       {"loft"})
def _r_titleblock_field(ctx) -> list:
    missing = []
    if not str(ctx.model.title or "").strip():
        missing.append("title")
    if not str(ctx.model.number or "").strip():
        missing.append("sheet/plate number")
    if not missing:
        return []
    return [_finding(
        "STD-TITLEBLOCK-FIELD",
        f"Drawing is missing its {' and '.join(missing)}.",
        "Fill in the title-block fields before issue.")]


@_rule("STD-SLOPE-MIN", "standards", "major", "Drainage below minimum slope",
       "A gravity drainage run sloped under its table minimum will not "
       "scour -- reuses the Pipewright MIN_SLOPE table; verify against the "
       "project plumbing code.",
       {"pipe"})
def _r_slope_min(ctx) -> list:
    from . import pipewright
    out = []
    for w in pipewright.check(ctx.model):
        if w.get("code") != "slope-min":
            continue
        out.append(_finding(
            "STD-SLOPE-MIN", w["msg"],
            "Increase the slope to the table minimum or resize the run.",
            where=tuple(w["xy"]) if w.get("xy") else None,
            ent_ids=[w["ent_id"]] if w.get("ent_id") else None))
    return out


@_rule("STD-NO-INVERT", "standards", "major", "Sloped run with no invert",
       "A gravity drainage run carries a slope but no invert elevation; "
       "every drainage run needs a start invert to lay it out -- verify "
       "against the project datum.",
       {"pipe"})
def _r_no_invert(ctx) -> list:
    out = []
    for e in ctx.model.ents:
        if e.kind != "pipe" or len(e.pts) < 2:
            continue
        if e.props.get("slope_in_ft") is not None \
                and e.props.get("invert_ft") is None:
            out.append(_finding(
                "STD-NO-INVERT",
                f"Pipe run {e.id} is sloped but has no invert elevation.",
                "Set the run's start invert (Pipewright slope command).",
                where=tuple(e.pts[0]), ent_ids=[e.id]))
    return out


@_rule("STD-NO-TRAP", "standards", "major", "Fixture with no trap",
       "Every plumbing fixture on a sanitary drain must be trapped; a "
       "sanitary fixture connection that derives no p-trap or closet flange "
       "is missing its trap -- verify against the project plumbing code.",
       {"pipe"})
def _r_no_trap(ctx) -> list:
    out = []
    for f in ctx.fittings():
        if f.kind == "fixture" and f.system == "san":
            out.append(_finding(
                "STD-NO-TRAP",
                f"Sanitary fixture connection at "
                f"({f.node_xy[0]:.2f}, {f.node_xy[1]:.2f}) derives no trap.",
                "Add a p-trap (or confirm the fixture has an integral trap).",
                where=tuple(f.node_xy),
                ent_ids=list(f.ent_ids)))
    return out


# =====================================================================
#  dfx -- design for construction / constructability
# =====================================================================

@_rule("DFX-THIN-WALL", "dfx", "minor", "Thin / sliver wall",
       f"A wall shorter than {DFX_MIN_WALL_FT:g} ft, or thinner than its "
       "assembly's minimum thickness, is usually a stray or a wrong wall "
       "type -- verify against the wall schedule.",
       {"loft"})
def _r_thin_wall(ctx) -> list:
    from .draft import WALL_TYPES
    out = []
    for frame in _walls(ctx.model):
        e, _a, _b, _u, _n, half, L = frame
        if L < DFX_MIN_WALL_FT - _EPS:
            out.append(_finding(
                "DFX-THIN-WALL",
                f"Wall {e.id} is only {L * 12.0:.1f} in long.",
                "Remove the sliver or extend it to a real length.",
                where=((frame[1][0] + frame[2][0]) / 2.0,
                       (frame[1][1] + frame[2][1]) / 2.0), ent_ids=[e.id]))
            continue
        wtype = str(e.props.get("wtype", ""))
        spec = WALL_TYPES.get(wtype)
        if spec and float(e.props.get("thick_in", 0.0)) \
                < float(spec["thick_in"]) - 1e-6:
            out.append(_finding(
                "DFX-THIN-WALL",
                f"Wall {e.id} is {float(e.props['thick_in']):.3f} in thick, "
                f"under the {spec['thick_in']:.3f} in minimum for "
                f"{spec['label']}.",
                "Correct the thickness or the wall type.",
                where=((frame[1][0] + frame[2][0]) / 2.0,
                       (frame[1][1] + frame[2][1]) / 2.0), ent_ids=[e.id]))
    return out


@_rule("DFX-PINCH", "dfx", "major", "Corridor / clearance pinch",
       f"Two roughly-parallel walls with less than {DFX_PINCH_FT:g} ft clear "
       "read as a corridor pinch or egress concern -- verify against the "
       "governing building & accessibility code.",
       {"loft"})
def _r_pinch(ctx) -> list:
    from .pipewright import _included
    walls = _walls(ctx.model)
    out = []
    seen = set()
    for i in range(len(walls)):
        for j in range(i + 1, len(walls)):
            fa, fb = walls[i], walls[j]
            ang = _included(fa[3], fb[3])
            ang = min(ang, 180.0 - ang)
            if ang > PARALLEL_DEG:
                continue
            clear = _parallel_clear(fa, fb)
            if clear is None:
                continue
            if 0.02 < clear < DFX_PINCH_FT:
                key = tuple(sorted((fa[0].id, fb[0].id)))
                if key in seen:
                    continue
                seen.add(key)
                mid = ((fa[1][0] + fa[2][0] + fb[1][0] + fb[2][0]) / 4.0,
                       (fa[1][1] + fa[2][1] + fb[1][1] + fb[2][1]) / 4.0)
                out.append(_finding(
                    "DFX-PINCH",
                    f"Parallel walls {fa[0].id}/{fb[0].id} are "
                    f"{clear:.2f} ft clear (under {DFX_PINCH_FT:g} ft).",
                    "Confirm the clear width meets egress / accessibility.",
                    where=mid, ent_ids=[fa[0].id, fb[0].id]))
    return out


@_rule("DFX-DOOR-SWING", "dfx", "major", "Door swing obstructed",
       "A door's 90-degree swing arc sweeping into a fixture or a wall is a "
       "clash -- the leaf cannot open.",
       {"loft"})
def _r_door_swing(ctx) -> list:
    from . import draft
    walls = _walls(ctx.model)
    fixtures = [(e, _fixture_aabb(e)) for e in ctx.model.ents
                if e.kind == "fixture" and e.pts]
    fixtures = [(e, b) for e, b in fixtures if b is not None]
    out = []
    for e in ctx.model.ents:
        if e.kind != "door":
            continue
        g = draft.door_geometry(ctx.model, e)
        if not g:
            continue
        host = e.props.get("host")
        cx, cy, r, a0, a1 = g["arc"]
        extent = (a1 - a0) % 360.0
        hinge = (cx, cy)
        hit = None
        # a fixture obstructs if any of its bbox corners/center falls in the
        # swept quarter-disk (leaf radius = door width)
        for fe, bb in fixtures:
            corners = [(bb[0], bb[1]), (bb[2], bb[1]), (bb[2], bb[3]),
                       (bb[0], bb[3]),
                       ((bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0)]
            if any(_in_sweep(p, hinge, r, a0, extent) for p in corners):
                hit = ("fixture", fe.id)
                break
        # a wall (not the host) obstructs if it passes through the disk
        if hit is None:
            for frame in walls:
                if frame[0].id == host:
                    continue
                a, b = frame[1], frame[2]
                if any(_in_sweep(
                        (a[0] + (b[0] - a[0]) * k / 12.0,
                         a[1] + (b[1] - a[1]) * k / 12.0),
                        hinge, r, a0, extent) for k in range(13)):
                    hit = ("wall", frame[0].id)
                    break
        if hit:
            what, oid = hit
            out.append(_finding(
                "DFX-DOOR-SWING",
                f"Door {e.id}'s swing sweeps into a {what}.",
                "Reverse the hand/swing or relocate the obstruction.",
                where=(cx, cy), ent_ids=[e.id, oid]))
    return out


@_rule("DFX-ROOM-NO-DOOR", "dfx", "major", "Room with no door",
       "A room enclosed by walls with no door or opening cannot be entered "
       "-- egress / access failure.",
       {"loft"})
def _r_room_no_door(ctx) -> list:
    from . import draft
    walls = _walls(ctx.model)
    if not walls:
        return []
    out = []
    for e in ctx.model.ents:
        if e.kind != "room" or not e.pts:
            continue
        _zone, wall_ids = _room_zone(ctx.model, e.pts[0], walls)
        if not wall_ids:
            continue
        if any(draft.wall_openings(ctx.model, wid) for wid in wall_ids):
            continue
        out.append(_finding(
            "DFX-ROOM-NO-DOOR",
            f"Room {str(e.props.get('number', '')) or ''} is enclosed by "
            "walls with no door or opening.",
            "Add a door / opening to make the room accessible.",
            where=tuple(e.pts[0]), ent_ids=[e.id]))
    return out


@_rule("DFX-DEADEND-MAIN", "dfx", "minor", "Drainage dead-end w/o cleanout",
       f"A drainage main dead-end wants a cleanout within ~{CO_SPACING_FT:g} "
       "ft for rodding -- verify against the project plumbing code.",
       {"pipe"})
def _r_deadend(ctx) -> list:
    from .pipewright import DRAINAGE
    fittings = ctx.fittings()
    cleanout_runs = set()
    for f in fittings:
        if f.kind in ("cleanout", "cap"):
            cleanout_runs.update(f.ent_ids)
    out = []
    for f in fittings:
        if f.kind != "open" or f.system not in DRAINAGE:
            continue
        if any(eid in cleanout_runs for eid in f.ent_ids):
            continue
        out.append(_finding(
            "DFX-DEADEND-MAIN",
            f"{f.system} dead-end at ({f.node_xy[0]:.2f}, "
            f"{f.node_xy[1]:.2f}) has no cleanout.",
            "Provide a cleanout at the dead-end for rodding access.",
            where=tuple(f.node_xy), ent_ids=list(f.ent_ids)))
    return out


@_rule("GEO-UNSUPPORTED-SPAN", "geometry", "minor", "Unsupported pipe span",
       "A pipe segment longer than its material's hanger stride has no "
       "intermediate support -- reuses harvest.STRIDE_RULES; verify against "
       "the project spec.",
       {"pipe"})
def _r_unsupported_span(ctx) -> list:
    from . import harvest
    out = []
    for e in ctx.model.ents:
        if e.kind != "pipe" or len(e.pts) < 2:
            continue
        rule_key = _stride_rule_for(str(e.props.get("material", "")))
        if rule_key is None:
            continue
        dia = float(e.props.get("dia_in", 4.0))
        try:
            stride = harvest.stride_for(rule_key, size_in=dia)
        except Exception:
            continue
        spec = harvest.STRIDE_RULES.get(rule_key, {})
        basis = spec.get("basis", "")
        verified = spec.get("verified", True)
        for a, b in zip(e.pts, e.pts[1:]):
            seg = math.hypot(b[0] - a[0], b[1] - a[1])
            if seg > stride + 1e-6:
                note = "" if verified else " (UNVERIFIED span)"
                out.append(_finding(
                    "GEO-UNSUPPORTED-SPAN",
                    f"Pipe {e.id}: a {seg:.1f} ft span exceeds the "
                    f"{stride:g} ft stride{note}.",
                    "Add an intermediate hanger/support within the stride.",
                    where=((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0),
                    ent_ids=[e.id],
                    rule=f"{basis} -- verify against project spec"))
    return out


# =====================================================================
#  lessons -- conflicts with lessons learned (Heartwood lane)
# =====================================================================

# LES-REPEAT is registered in the RULES map for the GUI/registry view, but it
# is run out-of-band (it needs the other findings + the Heartwood store), so
# its fn is None and the runner never calls it in the normal loop.
RULES["LES-REPEAT"] = {
    "category": "lessons", "severity": "major",
    "title": "Repeat of a recorded lesson",
    "rule": "This finding matches a trusted lesson recorded in the "
            "Heartwood -- the crew has been bitten by this before.",
    "inputs": {"loft", "pdf"}, "fn": None,
}


def lessons_from_heartwood(store_path) -> list:
    """Trusted lesson notes (origin 'lesson', status 'trusted') from a
    Heartwood store, as ``[{"pattern", "code", "note", "cite"}]``.  Returns
    ``[]`` when Heartwood is absent or the store cannot be opened -- the
    lessons lane never crashes a review."""
    try:
        from .heartwood import Heartwood
    except Exception:
        return []
    if not store_path or not os.path.exists(store_path):
        return []
    hw = None
    out = []
    try:
        hw = Heartwood(store_path)
        for note in hw.notes(status="trusted"):
            if str(note.get("origin")) != "lesson":
                continue
            text = str(note.get("text", ""))
            out.append({
                "pattern": _keywords(text),
                "code": _lesson_code(text),
                "note": text,
                "cite": f"lesson note #{note.get('id')}"})
    except Exception:
        return []
    finally:
        if hw is not None:
            try:
                hw.close()
            except Exception:
                pass
    return out


def record_lesson(store_path, finding, note: str = "") -> int:
    """File a finding as an UNVERIFIED lesson note (lane 2, human-gated -- a
    human trusts it in the Old Hand Manage screen before it ever fires a
    LES-REPEAT).  Returns the new note id.  Requires Heartwood present."""
    from .heartwood import Heartwood
    hw = Heartwood(store_path)
    try:
        body = note or finding.detail
        text = f"[BACKCHECK:{finding.code}] {finding.title}: {body}"
        return hw.teach(text, author="Backcheck", origin="lesson")
    finally:
        try:
            hw.close()
        except Exception:
            pass


def _lesson_matches(findings, lessons) -> list:
    """Pair each finding against any trusted lesson it matches (by code, or by
    all lesson keywords present in the detail) -> LES-REPEAT findings."""
    out = []
    for f in findings:
        if f.category == "lessons":
            continue
        haystack = f"{f.code} {f.detail} {f.title}".lower()
        for les in lessons:
            code = les.get("code")
            matched = bool(code) and code == f.code
            if not matched:
                kws = les.get("pattern") or []
                matched = bool(kws) and all(k in haystack for k in kws)
            if matched:
                out.append(_finding(
                    "LES-REPEAT",
                    f"{f.code} matches a recorded lesson "
                    f"({les.get('cite', '')}): {les.get('note', '')[:120]}",
                    "Review against the recorded lesson before issuing.",
                    page=f.page, where=f.where, ent_ids=list(f.ent_ids)))
                break
    return out


# ------------------------------------------------------------ small helpers --

def _vague_hits(text: str) -> list:
    """[(phrase, severity)] for every vague-lexicon phrase in text (each
    phrase reported once, longest phrases first so 'verify in field' is not
    also flagged as a bare word)."""
    low = text.lower()
    out = []
    for phrase in sorted(VAGUE_LEXICON, key=len, reverse=True):
        pat = r"\b" + re.escape(phrase) + (r"" if phrase.endswith(".")
                                           else r"\b")
        if re.search(pat, low):
            out.append((phrase, VAGUE_LEXICON[phrase]))
    return out


def _text_paper_in(size):
    """Paper lettering height (inches) for a Loft text size key or a numeric
    override, or None when unreadable."""
    from .draft import TEXT_SIZES
    if isinstance(size, (int, float)):
        return float(size)
    key = str(size)
    if key in TEXT_SIZES:
        return TEXT_SIZES[key]
    try:
        return float(key)
    except ValueError:
        return None


def _stride_rule_for(material: str):
    m = material.lower()
    for needle, rule in _MATERIAL_STRIDE:
        if needle in m:
            return rule
    return None


def _wall_corner_nodes(model) -> dict:
    """{(x, y): [(wall_id, away_unit), ...]} clustering wall endpoints; the
    away unit points from the shared node along each wall."""
    nodes: dict = {}
    for frame in _walls(model):
        e, a, b, _u, _n, _half, L = frame
        for pt, other in ((a, b), (b, a)):
            key = (round(pt[0] / NODE_TOL_FT), round(pt[1] / NODE_TOL_FT))
            dx, dy = other[0] - pt[0], other[1] - pt[1]
            d = math.hypot(dx, dy)
            if d <= _EPS:
                continue
            nodes.setdefault(key, {"pt": pt, "legs": []})
            nodes[key]["legs"].append((e.id, (dx / d, dy / d)))
    return {v["pt"]: v["legs"] for v in nodes.values()}


def _parallel_clear(fa, fb):
    """Clear distance (ft) between two roughly-parallel walls' faces, or None
    when they do not overlap along their shared direction."""
    _ea, a0, a1, u, _n, ha, La = fa
    _eb, b0, b1, _ub, _nb, hb, _Lb = fb
    # perpendicular offset of wall b's midpoint from wall a's line
    mb = ((b0[0] + b1[0]) / 2.0, (b0[1] + b1[1]) / 2.0)
    n = (-u[1], u[0])
    perp = abs((mb[0] - a0[0]) * n[0] + (mb[1] - a0[1]) * n[1])
    # require projection overlap along u so they actually face each other
    def proj(p):
        return (p[0] - a0[0]) * u[0] + (p[1] - a0[1]) * u[1]
    sa0, sa1 = 0.0, La
    pb = sorted((proj(b0), proj(b1)))
    if pb[1] < sa0 - 0.05 or pb[0] > sa1 + 0.05:
        return None
    return perp - ha - hb


def _first_hit(doc, token):
    """(page_no, (x0,y0,x1,y1)) of the first place token appears in doc, or
    (None, None).  Uses the sheet-token display variants."""
    from . import hyperlink
    for i in range(doc.page_count):
        page = doc[i]
        for form in hyperlink._variants(token):
            try:
                rects = page.search_for(form)
            except Exception:
                rects = []
            if rects:
                r = rects[0]
                return i + 1, (r.x0, r.y0, r.x1, r.y1)
    return None, None


def _keywords(text: str) -> list:
    """Content words of a lesson note (after stripping the code marker), for
    fuzzy matching."""
    text = re.sub(r"\[BACKCHECK:[^\]]*\]", " ", text).lower()
    words = re.findall(r"[a-z]{4,}", text)
    stop = {"verify", "against", "project", "standard", "code", "with",
            "this", "that", "before", "review", "recorded", "lesson"}
    seen = []
    for w in words:
        if w not in stop and w not in seen:
            seen.append(w)
        if len(seen) >= 4:
            break
    return seen


def _lesson_code(text: str):
    m = re.match(r"\s*\[BACKCHECK:([A-Z0-9\-]+)\]", text)
    return m.group(1) if m else None


# ------------------------------------------------------ degenerate detectors --

def _sev_by_count(n: int) -> str:
    return "info" if n <= 2 else ("minor" if n <= 5 else "major")


def _degenerate_loft(model) -> list:
    hits = []
    for e in model.ents:
        if e.kind in ("wall", "grid", "line", "pipe", "dim") and e.pts:
            for a, b in zip(e.pts, e.pts[1:]):
                if math.hypot(b[0] - a[0], b[1] - a[1]) <= 1e-6:
                    hits.append((e.id, tuple(a), "stacked/zero-length point"))
                    break
            if len(e.pts) == 2 and math.hypot(
                    e.pts[1][0] - e.pts[0][0],
                    e.pts[1][1] - e.pts[0][1]) <= 1e-6:
                hits.append((e.id, tuple(e.pts[0]), "zero-length segment"))
    # de-dup per ent
    seen = set()
    uniq = []
    for eid, pt, why in hits:
        if eid in seen:
            continue
        seen.add(eid)
        uniq.append((eid, pt, why))
    sev = _sev_by_count(len(uniq))
    out = []
    for eid, pt, why in uniq:
        out.append(_finding(
            "GEO-DEGENERATE",
            f"Entity {eid}: {why}.",
            "Delete or repair the degenerate entity.",
            severity=sev, where=pt, ent_ids=[eid]))
    return out


def _degenerate_dxf(path) -> list:
    from . import fieldwire
    pairs = fieldwire.read_dxf_pairs(path)
    lines = []
    cur = None
    kind = None
    for code, value in pairs:
        if code == 0:
            if kind == "LINE" and cur and all(
                    k in cur for k in (10, 20, 11, 21)):
                lines.append(cur)
            kind = value
            cur = {} if value == "LINE" else None
        elif cur is not None:
            try:
                cur[code] = float(value)
            except ValueError:
                pass
    if kind == "LINE" and cur and all(k in cur for k in (10, 20, 11, 21)):
        lines.append(cur)
    degen = []
    for ln in lines:
        if math.hypot(ln[11] - ln[10], ln[21] - ln[20]) <= 1e-6:
            degen.append(ln)
    sev = _sev_by_count(len(degen))
    out = []
    for ln in degen:
        out.append(_finding(
            "GEO-DEGENERATE",
            f"Zero-length LINE at ({ln[10]:.3f}, {ln[20]:.3f}).",
            "Delete the zero-length line.",
            severity=sev, where=(ln[10], ln[20])))
    return out


def _degenerate_obj(path) -> list:
    verts, faces = _parse_obj(path)
    out = []
    # open edges: an edge used by exactly one face
    edge_use: dict = {}
    for face in faces:
        n = len(face)
        for k in range(n):
            a, b = face[k], face[(k + 1) % n]
            if a == b:
                continue
            key = (a, b) if a < b else (b, a)
            edge_use[key] = edge_use.get(key, 0) + 1
    open_edges = [k for k, c in edge_use.items() if c == 1]
    # zero-area faces
    zero_faces = 0
    for face in faces:
        if len(face) >= 3 and _face_area(verts, face) <= 1e-9:
            zero_faces += 1
    # floating islands (connected components over edges)
    islands = _components(len(verts), list(edge_use.keys()), faces)

    if open_edges:
        out.append(_finding(
            "GEO-DEGENERATE",
            f"{len(open_edges)} open edge(s): the mesh is not watertight.",
            "Close the boundary / stitch the open edges.",
            severity=_sev_by_count(len(open_edges))))
    if zero_faces:
        out.append(_finding(
            "GEO-DEGENERATE",
            f"{zero_faces} zero-area face(s).",
            "Remove the degenerate faces.",
            severity=_sev_by_count(zero_faces)))
    if islands > 1:
        out.append(_finding(
            "GEO-DEGENERATE",
            f"{islands} disconnected components (floating islands).",
            "Confirm the model is one connected assembly.",
            severity="minor"))
    return out


def _parse_obj(path):
    """(verts, faces) -- faces are lists of 0-based vertex indices.  Mirrors
    bim.load_obj's tolerant token handling but keeps whole face loops."""
    verts = []
    faces = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            parts = raw.strip().split()
            if not parts or parts[0].startswith("#"):
                continue
            tag = parts[0]
            if tag == "v" and len(parts) >= 4:
                try:
                    verts.append((float(parts[1]), float(parts[2]),
                                  float(parts[3])))
                except ValueError:
                    continue
            elif tag == "f" and len(parts) >= 4:
                idx = []
                for tok in parts[1:]:
                    head = tok.split("/")[0]
                    try:
                        i = int(head)
                    except ValueError:
                        continue
                    idx.append(i - 1 if i > 0 else len(verts) + i)
                if len(idx) >= 3:
                    faces.append(idx)
    return verts, faces


def _face_area(verts, face) -> float:
    """Newell's-method polygon area magnitude (3D)."""
    nx = ny = nz = 0.0
    n = len(face)
    for k in range(n):
        try:
            ax, ay, az = verts[face[k]]
            bx, by, bz = verts[face[(k + 1) % n]]
        except IndexError:
            return 0.0
        nx += (ay - by) * (az + bz)
        ny += (az - bz) * (ax + bx)
        nz += (ax - bx) * (ay + by)
    return 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)


def _components(n_verts, edges, faces) -> int:
    """Count connected components over the vertices actually used by faces."""
    used = set()
    for f in faces:
        used.update(f)
    if not used:
        return 0
    parent = {v: v for v in used}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for a, b in edges:
        if a in parent and b in parent:
            union(a, b)
    return len({find(v) for v in used})


# ---------------------------------------------------------------- the runner --

def _norm_filter(rules):
    if rules is None:
        return None
    if isinstance(rules, str):
        return {rules}
    return {str(r) for r in rules}


def _allowed(code, rules_filter) -> bool:
    if rules_filter is None:
        return True
    if code in rules_filter:
        return True
    meta = RULES.get(code, {})
    return meta.get("category") in rules_filter


def run_rules(ctx, rules=None, heartwood_path=None) -> Report:
    """Run every applicable rule against a prepared context.  Each rule fn is
    wrapped so one failure is logged, recorded in ``stats["skipped"]`` and
    never sinks the report.  ``rules`` filters by rule code or category."""
    rules_filter = _norm_filter(rules)
    wanted = {ctx.source}
    if ctx.source == "loft" and ctx.model is not None \
            and any(e.kind == "pipe" for e in ctx.model.ents):
        wanted.add("pipe")

    findings = []
    checked = []
    skipped = []

    for code, meta in RULES.items():
        if meta.get("fn") is None:                 # LES-REPEAT: run out-of-band
            continue
        if not (meta["inputs"] & wanted):
            continue
        if not _allowed(code, rules_filter):
            continue
        try:
            got = meta["fn"](ctx) or []
            for f in got:
                f.source = ctx.source
            findings.extend(got)
            checked.append(code)
        except Exception as exc:                   # one bad rule never aborts
            ctx.log(f"  !! rule {code} failed: {exc}")
            skipped.append({"code": code, "reason": f"rule error: {exc}"})

    # the honestly-out-of-scope rules, always surfaced
    for code, meta in SKIPPED_RULES.items():
        if ctx.source in meta["inputs"] and _allowed(code, rules_filter):
            skipped.append({"code": code, "reason": meta["reason"]})

    # the lessons lane (human-gated, out-of-band)
    if heartwood_path and _allowed("LES-REPEAT", rules_filter):
        lessons = lessons_from_heartwood(heartwood_path)
        if lessons:
            les = _lesson_matches(findings, lessons)
            for f in les:
                f.source = ctx.source
            findings.extend(les)
            checked.append("LES-REPEAT")

    report = Report(findings=findings, source=ctx.source, stats={})
    report.sort()
    report.stats = {
        "by_severity": {s: len(report.by_severity(s)) for s in SEVERITIES},
        "by_category": {c: len(report.by_category(c)) for c in CATEGORIES},
        "checked": checked,
        "skipped": skipped,
    }
    return report


# ---------------------------------------------------------- entry points -----

def check_loft(model, rules=None, heartwood_path=None) -> Report:
    """Backcheck a :class:`draft.DraftModel` (the strongest, most structured
    checks -- plus the Pipewright pipe checks when the model has pipe runs).
    ``heartwood_path`` enables the trusted-lessons lane."""
    ctx = _Ctx("loft", model=model)
    return run_rules(ctx, rules=rules, heartwood_path=heartwood_path)


def check_pdf(path, index=None, rules=None, log=print,
              heartwood_path=None) -> Report:
    """Backcheck a vector plan PDF (fitz text + drawings).  Builds a
    :class:`sheets.SheetIndex` when one is not supplied; the input file is
    never modified."""
    import fitz
    from .sheets import SheetIndex
    if index is None:
        index = SheetIndex(path, log=log)
    doc = fitz.open(path)
    try:
        ctx = _Ctx("pdf", pdf_path=path, doc=doc, index=index, log=log)
        return run_rules(ctx, rules=rules, heartwood_path=heartwood_path)
    finally:
        doc.close()


def check_dxf(path, rules=None, log=print) -> Report:
    """Backcheck an ASCII DXF via the light shared parser (degenerate-geometry
    checks -- a DXF has no title block or scale to reason about)."""
    ctx = _Ctx("dxf", dxf_path=path, log=log)
    return run_rules(ctx, rules=rules)


def check_obj(path, rules=None, log=print) -> Report:
    """Backcheck an OBJ mesh (open edges, zero-area faces, floating islands)
    via a tolerant local reader -- the honest bridge for exported 3D."""
    ctx = _Ctx("obj", obj_path=path, log=log)
    return run_rules(ctx, rules=rules)


def check(path_or_model, rules=None, log=print, index=None,
          heartwood_path=None) -> Report:
    """Dispatch by type / extension: a DraftModel -> Loft; a ``*.loft.json``
    -> load then Loft; ``.pdf`` -> PDF; ``.dxf`` -> DXF; ``.obj`` -> OBJ."""
    if hasattr(path_or_model, "ents") and hasattr(path_or_model, "plies"):
        return check_loft(path_or_model, rules=rules,
                          heartwood_path=heartwood_path)
    p = str(path_or_model)
    low = p.lower()
    if low.endswith(".loft.json"):
        from .draft import DraftModel
        return check_loft(DraftModel.load(p), rules=rules,
                          heartwood_path=heartwood_path)
    if low.endswith(".pdf"):
        return check_pdf(p, index=index, rules=rules, log=log,
                         heartwood_path=heartwood_path)
    if low.endswith(".dxf"):
        return check_dxf(p, rules=rules, log=log)
    if low.endswith(".obj"):
        return check_obj(p, rules=rules, log=log)
    raise ValueError(f"unsupported input for the Backcheck: {p!r} "
                     "(expected a DraftModel, .loft.json, .pdf, .dxf or .obj)")


# ============================================================ markup bridge ==
# Findings -> real cloud + callout annotations on the design (PDF), and
# canvas overlay points (Loft).  Coordinates are already in the right space.

def _box_from_where(where, pad=12.0):
    """(x0,y0,x1,y1) from a point (padded) or a bbox."""
    if where is None:
        return None
    if len(where) == 2:
        x, y = where
        return (x - pad, y - pad, x + pad, y + pad)
    x0, y0, x1, y1 = where
    if abs(x1 - x0) < 1.0 and abs(y1 - y0) < 1.0:
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        return (cx - pad, cy - pad, cx + pad, cy + pad)
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def findings_to_markups(report, index_or_pageinfo=None) -> list:
    """Each PDF-located finding -> a severity-colored **cloud** rect at
    ``where`` PLUS a **callout** carrying subject=code and the detail +
    suggestion, ready for markups.MarkupStore / apply_to_pdf.  Findings with
    no page or no location are skipped."""
    from .markups import Markup, Style
    out = []
    for f in report.findings:
        if f.source != "pdf" or f.page is None or f.where is None:
            continue
        box = _box_from_where(f.where)
        if box is None:
            continue
        color = SEVERITY_COLORS.get(f.severity, SEVERITY_COLORS["info"])
        x0, y0, x1, y1 = box
        out.append(Markup.new(
            f.page, "cloud", [(x0, y0), (x1, y1)],
            subject=f.code, comment=f"{f.detail} -- {f.suggestion}",
            author="Backcheck", style=Style(color=color, width=2.0)))
        anchor = (x0, max(0.0, y0 - 22.0))
        out.append(Markup.new(
            f.page, "callout", [anchor, (x0, y0)],
            text=f.code, subject=f.code,
            comment=f"{f.detail} -- {f.suggestion}",
            author="Backcheck", style=Style(color=color, width=1.5)))
    return out


def write_markup_pdf(report, in_pdf, out_pdf, index=None) -> int:
    """Build the finding markups and stamp them onto ``in_pdf`` as real
    annotations (atomic write via markups.apply_to_pdf).  Returns the number
    of annotations written; the input file is never modified."""
    from .markups import apply_to_pdf
    marks = findings_to_markups(report, index)
    if not marks:
        # still copy through so the caller always gets an output file
        import fitz
        doc = fitz.open(in_pdf)
        try:
            tmp = out_pdf + ".part"
            doc.save(tmp, garbage=3, deflate=True)
        finally:
            doc.close()
        os.replace(tmp, out_pdf)
        return 0
    res = apply_to_pdf(in_pdf, out_pdf, marks, log=lambda *a: None)
    return int(res.get("annots", 0))


def loft_finding_points(report) -> list:
    """Loft/pipe findings as ``[(x, y, severity, code, detail)]`` in model
    feet (y up) for the GUI to overlay on the canvas -- the model is never
    mutated (the Loft has no QA-annotation entity)."""
    out = []
    for f in report.findings:
        if f.source not in ("loft", "pipe"):
            continue
        w = f.where
        if w is None:
            continue
        if len(w) == 2:
            x, y = w
        else:
            x, y = (w[0] + w[2]) / 2.0, (w[1] + w[3]) / 2.0
        out.append((x, y, f.severity, f.code, f.detail))
    return out


def findings_to_loft_marks(report, model=None) -> list:
    """Convenience alias returning ``[(x, y, text, severity)]`` for a canvas
    overlay (text = the finding code)."""
    return [(x, y, code, sev)
            for (x, y, sev, code, _detail) in loft_finding_points(report)]
