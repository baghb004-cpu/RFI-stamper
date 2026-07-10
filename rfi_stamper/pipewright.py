"""Pipewright: Planloom's from-scratch piping domain engine (GUI-free).

A wright is a maker — shipwright, millwright, pipewright.  This module is
the deterministic "hands" behind the Weaver's typed drawing commands: pipe
RUNS live in the Loft as ``kind == "pipe"`` entities (polylines in model
feet, y-up), and everything else — nodes, fittings, slopes, invert
elevations, code-minimum checks, takeoff, 3D — is DERIVED from that
geometry by rules a foreman can audit.  Nothing here guesses: fitting
selection is geometry + system rules, slope is arithmetic, and code
minimums are table lookups that WARN and never silently "fix".

Flow convention: a run flows from its FIRST vertex toward its LAST vertex.
``invert_ft`` is the invert elevation at the FIRST vertex; positive
``slope_in_ft`` (inches per foot) falls toward the LAST vertex.  A run is
*downstream of a node* when its first vertex sits on that node — commands
propagate along drawing direction and never backwards into a contributing
branch.  Junctions form where runs meet at VERTICES (endpoints or interior
points), merged within :data:`MERGE_TOL_FT`.

The fitting truth table (locked by tests/test_pipewright.py):

* degree 1, near a plumbing fixture, sanitary: ``ptrap`` for lav/sink/df
  stencils, ``closet-flange`` for a water closet, ``fixture`` otherwise
* degree 1, no fixture: ``cap`` when the run is capped, else ``open``
  (candidate for a cap or cleanout)
* degree 2, differing diameters: ``reducer AxB`` (larger first)
* degree 2, deflection 30–60° / 60°+: ``elbow45`` / ``elbow90``; under 30°
  the vertex passes straight through with no fitting
* degree 3, drainage (san/storm): branch 30–60° off the main -> ``wye``;
  branch 60°+ with no slope context on any adjoining run -> ``santee``
  (assumed vertical branch); with slope context (a horizontal main):
  sanitary -> ``combo`` ("combo/wye+1/8 bend recommended"), storm ->
  ``tee`` with a wye note
* degree 3, pressure (dcw/dhw/gas): ``tee``
* degree 4+: ``cross`` — warned on drainage

Fitting overrides (:func:`replace_fitting`) persist in the FIRST adjoining
pipe entity's ``props["fit_overrides"]`` keyed by the node's canonical xy
(``"x.xxx,y.yyy"``), so they ride the Loft's JSON save/load and its
snapshot undo with zero new serialization machinery.

Every command API returns a report dict — ``{"changed": n, "report": str,
...detail}`` — shaped for the Weaver to echo back in plain words.  Multi-
entity commands mutate inside ONE undo snapshot, so each command is one
Ctrl+Z from gone.

Fully offline, stdlib only; the reckoner / bim bridges import lazily.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .draft import fmt_ftin

# -------------------------------------------------------------- constants ---

#: Pipe vertices closer than this merge into one node.
MERGE_TOL_FT = 0.05

#: A degree-1 node this close to a plumbing-stencil insertion is a
#: fixture connection.
FIXTURE_TOL_FT = 0.6

#: Piping systems.  ``ply`` names the Loft layer (colors mirror
#: draft.DEFAULT_PLIES); ``material`` is the default by generic spec —
#: never a brand; ``dashed`` marks systems drawn dashed in plan (vents);
#: ``dia_in`` is the default trade size for a new run.
SYSTEMS: dict[str, dict] = {
    "san": {"label": "Sanitary", "color": "#1e8449", "ply": "P-SAN",
            "material": "PVC DWV", "dashed": False, "drainage": True,
            "dia_in": 4.0},
    "vent": {"label": "Vent", "color": "#52be80", "ply": "P-VENT",
             "material": "PVC DWV", "dashed": True, "drainage": False,
             "dia_in": 2.0},
    "storm": {"label": "Storm", "color": "#7d6608", "ply": "P-STRM",
              "material": "PVC DWV", "dashed": False, "drainage": True,
              "dia_in": 4.0},
    "dcw": {"label": "Domestic cold water", "color": "#2471a3",
            "ply": "P-DCW", "material": "Copper Type L", "dashed": False,
            "drainage": False, "dia_in": 1.0},
    "dhw": {"label": "Domestic hot water", "color": "#cb4335",
            "ply": "P-DHW", "material": "Copper Type L", "dashed": False,
            "drainage": False, "dia_in": 0.75},
    "gas": {"label": "Fuel gas", "color": "#d4ac0d", "ply": "P-GAS",
            "material": "Black steel Sch 40", "dashed": False,
            "drainage": False, "dia_in": 1.0},
}

#: Sloped (gravity) drainage systems.
DRAINAGE = ("san", "storm")

#: Standard trade sizes, inches.
SIZES_IN = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0,
            6.0, 8.0, 10.0, 12.0]

#: Gravity-drainage slope minimums by diameter (rows apply from
#: ``dia_min_in`` up; the LAST matching row wins).  These are the common
#: table values — the basis strings say so out loud; always verify against
#: the project code.
MIN_SLOPE: list[dict] = [
    {"dia_min_in": 0.0, "min_in_ft": 0.25,
     "basis": 'drainage under 3": 1/4"/ft minimum — '
              'verify against project code'},
    {"dia_min_in": 3.0, "min_in_ft": 0.125,
     "basis": 'drainage 3" and larger: 1/8"/ft minimum — '
              'verify against project code'},
    {"dia_min_in": 8.0, "min_in_ft": 0.0625,
     "basis": 'drainage 8" and larger: 1/16"/ft commonly permitted — '
              'verify against project code'},
]

#: Fitting kinds :func:`replace_fitting` accepts (plus ``reducer AxB``).
OVERRIDE_KINDS = {"elbow45", "elbow90", "tee", "santee", "wye", "combo",
                  "cross", "cap", "cleanout", "ptrap", "closet-flange",
                  "coupling", "open"}

#: Sanitary fixture stencils that take a p-trap at the connection.
_PTRAP_STENCILS = {"lav", "sink_s", "sink_d", "df"}

_SYSTEM_ORDER = tuple(SYSTEMS)


def min_slope(dia_in) -> tuple:
    """(minimum in/ft, basis string) for a gravity-drainage diameter."""
    row = MIN_SLOPE[0]
    for cand in MIN_SLOPE:
        if float(dia_in) >= cand["dia_min_in"] - 1e-9:
            row = cand
    return row["min_in_ft"], row["basis"]


def fmt_dia_in(dia_in) -> str:
    """Trade-size text: 4.0 -> ``4``, 1.5 -> ``1 1/2``, 0.75 -> ``3/4``
    (nearest 1/16, fraction reduced, no unit mark)."""
    v = float(dia_in)
    units = round(abs(v) * 16)
    sign = "-" if (v < 0 and units) else ""
    whole, frac = divmod(units, 16)
    if frac:
        g = math.gcd(frac, 16)
        f = f"{frac // g}/{16 // g}"
        return f"{sign}{whole} {f}" if whole else f"{sign}{f}"
    return f"{sign}{whole}"


def fmt_slope(in_per_ft) -> str:
    """0.125 -> ``1/8"/ft`` — the way it is said on the job."""
    return fmt_dia_in(in_per_ft) + '"/ft'


def _node_key(x, y) -> str:
    """Canonical node key for fit_overrides (stable across save/load and
    undo because entity floats round-trip exactly through JSON)."""
    return f"{float(x) + 0.0:.3f},{float(y) + 0.0:.3f}"


def _fmt_xy(x, y) -> str:
    return f"({fmt_ftin(x)}, {fmt_ftin(y)})"


# ------------------------------------------------------------ the network ---

@dataclass
class Leg:
    """One pipe segment arriving at / leaving a node."""
    ent_id: str
    away: tuple          # unit vector from the node into the segment
    inbound: bool        # True: flow arrives at the node through this leg
    end: str             # "first" | "last" | "mid" position on the run
    system: str
    dia_in: float


@dataclass
class PipeNode:
    xy: tuple
    kind: str = "end"    # end | corner | junction | fixture
    legs: list = field(default_factory=list)
    fixture: str | None = None      # stencil key within FIXTURE_TOL_FT

    @property
    def degree(self) -> int:
        return len(self.legs)

    @property
    def key(self) -> str:
        return _node_key(*self.xy)


@dataclass
class PipeEdge:
    ent_id: str
    a: int               # node index at the segment start (flow a -> b)
    b: int
    length_ft: float
    system: str
    dia_in: float
    material: str


@dataclass
class Net:
    nodes: list = field(default_factory=list)
    edges: list = field(default_factory=list)

    def node_near(self, x, y, tol_ft: float = 0.5) -> int | None:
        """Index of the nearest node within tol_ft, or None."""
        best, best_d = None, None
        for i, n in enumerate(self.nodes):
            d = math.hypot(n.xy[0] - float(x), n.xy[1] - float(y))
            if d <= float(tol_ft) + 1e-9 and (best_d is None or d < best_d):
                best, best_d = i, d
        return best


@dataclass
class Fitting:
    """One derived (or overridden) fitting at a network node.  ``angle_deg``
    is the deflection at a corner, the branch angle off the main at a
    junction, and the leg bearing at an end; ``legs_deg`` are the absolute
    bearings of every leg away from the node (degrees CCW from +x) and
    ``branch_deg`` the branch leg's bearing at degree-3 nodes — both are
    there so the renderer never re-derives geometry."""
    node_xy: tuple
    kind: str
    angle_deg: float
    system: str
    dia_in: float
    note: str = ""
    legs_deg: tuple = ()
    branch_deg: float | None = None
    ent_ids: tuple = ()


def _pipes(model) -> list:
    return [e for e in model.ents if e.kind == "pipe" and len(e.pts) >= 2]


def _run_length(ent) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1])
               for a, b in zip(ent.pts, ent.pts[1:]))


def network(model) -> Net:
    """Nodes from pipe endpoints/vertices (merged within MERGE_TOL_FT),
    edges = run segments (flow first -> last vertex).  Node kinds: "end"
    (degree 1), "corner" (degree 2), "junction" (degree 3+), "fixture"
    (a degree-1 node within FIXTURE_TOL_FT of a plumbing-stencil
    insertion; higher-degree nodes near a fixture keep their degree kind
    but still carry ``.fixture``)."""
    from .draft import STENCILS
    net = Net()
    cells: dict[tuple, list] = {}

    def node_at(x, y) -> int:
        cx = math.floor(x / MERGE_TOL_FT)
        cy = math.floor(y / MERGE_TOL_FT)
        best, best_d = None, None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in cells.get((cx + dx, cy + dy), ()):
                    n = net.nodes[i]
                    d = math.hypot(n.xy[0] - x, n.xy[1] - y)
                    if d <= MERGE_TOL_FT + 1e-12 \
                            and (best_d is None or d < best_d - 1e-15):
                        best, best_d = i, d
        if best is not None:
            return best
        net.nodes.append(PipeNode(xy=(float(x), float(y))))
        cells.setdefault((cx, cy), []).append(len(net.nodes) - 1)
        return len(net.nodes) - 1

    for e in _pipes(model):
        system = str(e.props.get("system", "san"))
        dia = float(e.props.get("dia_in", 4.0))
        mat = str(e.props.get("material", ""))
        last = len(e.pts) - 1
        for i, (p, q) in enumerate(zip(e.pts, e.pts[1:])):
            length = math.hypot(q[0] - p[0], q[1] - p[1])
            if length <= 1e-9:
                continue
            ai, bi = node_at(p[0], p[1]), node_at(q[0], q[1])
            if ai == bi:
                continue
            u = ((q[0] - p[0]) / length, (q[1] - p[1]) / length)
            net.edges.append(PipeEdge(e.id, ai, bi, length,
                                      system, dia, mat))
            net.nodes[ai].legs.append(Leg(
                e.id, u, False, "first" if i == 0 else "mid", system, dia))
            net.nodes[bi].legs.append(Leg(
                e.id, (-u[0], -u[1]), True,
                "last" if i + 1 == last else "mid", system, dia))

    fixtures = [(str(f.props.get("stencil", "")), f.pts[0])
                for f in model.ents
                if f.kind == "fixture" and f.pts
                and STENCILS.get(str(f.props.get("stencil", "")),
                                 {}).get("cat") == "plumbing"]
    for node in net.nodes:
        best, best_d = None, None
        for key, (fx, fy) in fixtures:
            d = math.hypot(fx - node.xy[0], fy - node.xy[1])
            if d <= FIXTURE_TOL_FT + 1e-9 and (best_d is None or d < best_d):
                best, best_d = key, d
        node.fixture = best
        if node.degree == 1:
            node.kind = "fixture" if best else "end"
        elif node.degree == 2:
            node.kind = "corner"
        else:
            node.kind = "junction"
    return net


# ------------------------------------------------------ fitting derivation --

#: Angle-band tolerance: an exactly-drawn 60° corner must not fall out of
#: its band because acos returned 59.999999999999986.
_ANG_EPS = 1e-6


def _bearing(v) -> float:
    return math.degrees(math.atan2(v[1], v[0])) % 360.0


def _included(u, v) -> float:
    """Included angle between two unit vectors, degrees 0..180."""
    dot = max(-1.0, min(1.0, u[0] * v[0] + u[1] * v[1]))
    return math.degrees(math.acos(dot))


def _node_system(legs) -> str:
    """The node's governing system: the most common leg system, ties broken
    by SYSTEMS order (drainage listed first on purpose)."""
    counts: dict[str, int] = {}
    for leg in legs:
        counts[leg.system] = counts.get(leg.system, 0) + 1

    def rank(s):
        order = _SYSTEM_ORDER.index(s) if s in _SYSTEM_ORDER else 99
        return (counts[s], -order)

    return max(counts, key=rank)


def derive_fittings(model, net: Net | None = None) -> list:
    """Every node resolves to at most one :class:`Fitting` by the truth
    table in the module docstring.  A ``fit_overrides`` entry on an
    adjoining run replaces the derived kind (note ``"user override"``);
    a straight-through degree-2 vertex emits nothing unless overridden."""
    if net is None:
        net = network(model)
    pipes = {e.id: e for e in _pipes(model)}
    overrides: dict[str, str] = {}
    for e in pipes.values():
        for k, v in (e.props.get("fit_overrides") or {}).items():
            overrides[str(k)] = str(v)

    out = []
    for node in net.nodes:
        legs = node.legs
        if not legs:
            continue
        system = _node_system(legs)
        dia = max(leg.dia_in for leg in legs)
        ent_ids = tuple(dict.fromkeys(leg.ent_id for leg in legs))
        legs_deg = tuple(_bearing(leg.away) for leg in legs)
        drainage = system in DRAINAGE
        deg = node.degree
        kind, note, angle, branch_deg = "", "", 0.0, None

        if deg == 1:
            angle = legs_deg[0]
            if node.fixture and system == "san":
                if node.fixture == "wc":
                    kind = "closet-flange"
                    note = "closet flange at water closet"
                elif node.fixture in _PTRAP_STENCILS:
                    kind = "ptrap"
                    note = f"p-trap at fixture ({node.fixture})"
                else:
                    kind = "fixture"
                    note = f"fixture connection ({node.fixture})"
            elif node.fixture:
                kind = "fixture"
                note = f"fixture connection ({node.fixture})"
            elif pipes[legs[0].ent_id].props.get("capped"):
                kind = "cap"
            else:
                kind = "open"
                note = "open end: cap or cleanout candidate"
        elif deg == 2:
            defl = 180.0 - _included(legs[0].away, legs[1].away)
            angle = defl
            d0, d1 = legs[0].dia_in, legs[1].dia_in
            if abs(d0 - d1) > 1e-9:
                kind = (f"reducer {fmt_dia_in(max(d0, d1))}"
                        f"x{fmt_dia_in(min(d0, d1))}")
                if defl >= 30.0 - _ANG_EPS:
                    note = f"reducing at a {defl:.0f}° bend"
            elif 30.0 - _ANG_EPS <= defl < 60.0 - _ANG_EPS:
                kind = "elbow45"
            elif defl >= 60.0 - _ANG_EPS:
                kind = "elbow90"
                if defl > 120.0 + _ANG_EPS:
                    note = f"sharp bend ({defl:.0f}° deflection) — verify"
            # under 30° the run passes straight through: no fitting
        elif deg == 3:
            # main = the most nearly opposed leg pair; the third is the
            # branch, and its angle off the nearer main leg is ~45 or ~90
            pairs = ((0, 1), (0, 2), (1, 2))
            main = max(pairs, key=lambda p: _included(legs[p[0]].away,
                                                      legs[p[1]].away))
            bi = ({0, 1, 2} - set(main)).pop()
            branch_deg = legs_deg[bi]
            angle = min(_included(legs[bi].away, legs[main[0]].away),
                        _included(legs[bi].away, legs[main[1]].away))
            if not drainage:
                kind = "tee"
            elif 30.0 - _ANG_EPS <= angle < 60.0 - _ANG_EPS:
                kind = "wye"
            elif angle >= 60.0 - _ANG_EPS:
                has_ctx = any(
                    pipes[leg.ent_id].props.get("slope_in_ft") is not None
                    for leg in legs)
                if not has_ctx:
                    kind = "santee"
                    note = "assumed vertical branch (no slope context) " \
                           "— verify"
                elif system == "san":
                    kind = "combo"
                    note = ("90° drainage branch: combo/wye+1/8 bend "
                            "recommended")
                else:
                    kind = "tee"
                    note = ("90° branch on a horizontal storm main: "
                            "consider wye+1/8 bend")
            else:
                kind = "wye"
                note = f"low-angle branch ({angle:.0f}°)"
        else:
            kind = "cross"
            angle = 90.0
            note = "cross fitting"
            if drainage:
                note += (" — avoid crosses on drainage; "
                         "verify against project code")

        ov = overrides.get(node.key)
        if ov:
            kind, note = ov, "user override"
        if not kind:
            continue
        out.append(Fitting(node.xy, kind, angle, system, dia, note,
                           legs_deg, branch_deg, ent_ids))
    return out


# ------------------------------------------------------------ the commands --
#
# Each command mutates inside ONE undo snapshot (draft's snapshot undo),
# so a whole multi-run edit is a single Ctrl+Z.

def _vertex_nodes(net: Net, ent) -> list:
    """[(node index | None, cumulative length ft)] per vertex of a run."""
    out, cum, prev = [], 0.0, None
    for p in ent.pts:
        if prev is not None:
            cum += math.hypot(p[0] - prev[0], p[1] - prev[1])
        out.append((net.node_near(p[0], p[1], tol_ft=MERGE_TOL_FT), cum))
        prev = p
    return out


def cap_open_ends(model, system=None) -> dict:
    """Cap every uncapped open end (optionally one system's): sets
    ``props["capped"] = True`` on each run owning one, which renders as
    the double-tick cap symbol.  Fixture connections are not open ends.
    Returns ``{"changed": ends capped, "report": str, "capped":
    [{"xy", "ent_id", "system", "dia_in"}]}``; idempotent."""
    want = None if system is None else str(system)
    opens = [f for f in derive_fittings(model)
             if f.kind == "open" and (want is None or f.system == want)]
    if not opens:
        return {"changed": 0, "capped": [],
                "report": "No uncapped open ends"
                          + (f" on {want}" if want else "") + "."}
    snap = model._snapshot()
    for f in opens:
        model.entity(f.ent_ids[0]).props["capped"] = True
    model._commit(snap)
    detail = [{"xy": f.node_xy, "ent_id": f.ent_ids[0],
               "system": f.system, "dia_in": f.dia_in} for f in opens]
    spots = "; ".join(
        f'{_fmt_xy(*f.node_xy)} {fmt_dia_in(f.dia_in)}" {f.system}'
        for f in opens)
    return {"changed": len(opens), "capped": detail,
            "report": f"Capped {len(opens)} open end(s): {spots}."}


def replace_fitting(model, node, kind) -> dict:
    """Force the fitting at a node.  ``node`` is (x, y) in model feet
    (nearest pipe node within 0.5 ft) or an index into
    ``network(model).nodes``; ``kind`` must be in :data:`OVERRIDE_KINDS`
    or a ``reducer AxB`` string.  The override lands in the FIRST
    adjoining run's ``props["fit_overrides"]`` (canonical-xy key), so it
    survives save/load and undo.  Returns ``{"changed": 0|1, "report",
    "node": (x, y)|None, "kind", "ent_id": str|None}``."""
    kind = str(kind).strip()
    if kind not in OVERRIDE_KINDS and not kind.startswith("reducer"):
        return {"changed": 0, "node": None, "kind": kind, "ent_id": None,
                "report": f"Unknown fitting kind {kind!r}; expected one of "
                          f"{sorted(OVERRIDE_KINDS)} or 'reducer AxB'."}
    net = network(model)
    if isinstance(node, int):
        idx = node if 0 <= node < len(net.nodes) else None
    else:
        idx = net.node_near(float(node[0]), float(node[1]), tol_ft=0.5)
    if idx is None:
        return {"changed": 0, "node": None, "kind": kind, "ent_id": None,
                "report": "No pipe node there (within 0'-6\")."}
    n = net.nodes[idx]
    ent = model.entity(n.legs[0].ent_id)     # FIRST adjoining pipe run
    ov = dict(ent.props.get("fit_overrides") or {})
    ov[n.key] = kind
    model.update(ent.id, fit_overrides=ov)
    return {"changed": 1, "node": n.xy, "kind": kind, "ent_id": ent.id,
            "report": f"Fitting at {_fmt_xy(*n.xy)} set to {kind}."}


def slope_run(model, ent_id, in_per_ft, start_invert_ft=None) -> dict:
    """Slope a run and set its invert, then propagate downstream through
    connected same-system drainage runs (a run is downstream when its
    first vertex sits on a node of the sloped run — including mid-run
    takeoffs, whose invert interpolates along the sloped run).  Runs that
    already carry a slope keep it; only their inverts move.  Converging
    (upstream) branches are never touched.  Refuses zero/uphill slopes.
    Falls are monotonic: where two paths reach one run, the lower invert
    wins.  Returns ``{"changed": n, "report": str, "slope_in_ft": v,
    "runs": [{"ent_id", "system", "dia_in", "length_ft", "slope_in_ft",
    "invert_start_ft", "invert_end_ft", "invert_start", "invert_end",
    "fall_ft", "fall"}], "total_fall_ft": f, "total_fall": str,
    "warnings": [str]}`` — the formatted fields are feet-inches text."""
    ent = model.entity(ent_id)
    if ent is None or ent.kind != "pipe" or len(ent.pts) < 2:
        return {"changed": 0, "runs": [], "warnings": [],
                "slope_in_ft": None, "total_fall_ft": 0.0,
                "total_fall": fmt_ftin(0.0),
                "report": f"No pipe run {ent_id!r}."}
    slope = float(in_per_ft)
    if slope <= 0.0:
        return {"changed": 0, "runs": [], "warnings": [],
                "slope_in_ft": slope, "total_fall_ft": 0.0,
                "total_fall": fmt_ftin(0.0),
                "report": "Refused: slope must be positive in/ft "
                          "(downhill toward the run's last vertex) — "
                          "a flat or uphill drain does not drain."}
    if start_invert_ft is not None:
        start_inv = float(start_invert_ft)
    elif ent.props.get("invert_ft") is not None:
        start_inv = float(ent.props["invert_ft"])
    else:
        start_inv = 0.0
    net = network(model)
    system = str(ent.props.get("system", "san"))
    propagate = system in DRAINAGE
    warnings: list = []
    if not propagate:
        warnings.append(f"{system} is not gravity drainage — sloped this "
                        "run only (no propagation).")
    plan: dict[str, tuple] = {}              # ent id -> (invert, slope)
    queue = [(ent.id, start_inv)]
    while queue:
        eid, inv = queue.pop(0)
        run = model.entity(eid)
        s = slope
        if eid != ent.id and run.props.get("slope_in_ft") is not None:
            s = float(run.props["slope_in_ft"])
        if eid in plan and inv >= plan[eid][0] - 1e-12:
            continue                         # keep the lower (monotonic)
        if (eid != ent.id and run.props.get("invert_ft") is not None
                and inv > float(run.props["invert_ft"]) + 1e-9):
            warnings.append(
                f"{eid}: propagated invert {fmt_ftin(inv)} sits above its "
                f"previous {fmt_ftin(float(run.props['invert_ft']))} — "
                "check for an uphill tie-in.")
        plan[eid] = (inv, s)
        if not propagate:
            break
        for n_idx, cum in _vertex_nodes(net, run):
            if n_idx is None:
                continue
            inv_here = inv - s * cum / 12.0
            for leg in net.nodes[n_idx].legs:
                if (leg.end == "first" and not leg.inbound
                        and leg.system == system and leg.ent_id != eid):
                    if (leg.ent_id not in plan
                            or inv_here < plan[leg.ent_id][0] - 1e-12):
                        queue.append((leg.ent_id, inv_here))
    snap = model._snapshot()
    runs, low = [], start_inv
    for eid, (inv, s) in plan.items():
        run = model.entity(eid)
        run.props["invert_ft"] = inv
        run.props["slope_in_ft"] = s
        length = _run_length(run)
        end_inv = inv - s * length / 12.0
        low = min(low, end_inv)
        dia = float(run.props.get("dia_in", 4.0))
        if str(run.props.get("system")) in DRAINAGE:
            need, basis = min_slope(dia)
            if s < need - 1e-9:
                warnings.append(
                    f"{eid}: {fmt_slope(s)} is under the {fmt_slope(need)} "
                    f'minimum for {fmt_dia_in(dia)}" ({basis}).')
        runs.append({
            "ent_id": eid, "system": str(run.props.get("system")),
            "dia_in": dia, "length_ft": length, "slope_in_ft": s,
            "invert_start_ft": inv, "invert_end_ft": end_inv,
            "invert_start": fmt_ftin(inv), "invert_end": fmt_ftin(end_inv),
            "fall_ft": inv - end_inv, "fall": fmt_ftin(inv - end_inv)})
    model._commit(snap)
    total = start_inv - low
    return {"changed": len(runs), "runs": runs, "warnings": warnings,
            "slope_in_ft": slope,
            "total_fall_ft": total, "total_fall": fmt_ftin(total),
            "report": f"Sloped {len(runs)} run(s) at {fmt_slope(slope)} "
                      f"from IE {fmt_ftin(start_inv)}: total fall "
                      f"{fmt_ftin(total)}."}


def resize_run(model, ent_id, dia_in, direction: str = "downstream") -> dict:
    """Set a run's diameter — ``direction="this"`` for the one run,
    ``"downstream"`` to carry the size through every connected
    same-system run below it (drawing-direction flow, as in
    :func:`slope_run`).  Non-standard sizes are set but warned.  Returns
    ``{"changed": n, "report": str, "runs": [ent ids], "dia_in": v,
    "warnings": [str]}``."""
    ent = model.entity(ent_id)
    if ent is None or ent.kind != "pipe":
        return {"changed": 0, "runs": [], "warnings": [], "dia_in": None,
                "report": f"No pipe run {ent_id!r}."}
    if direction not in ("downstream", "this"):
        return {"changed": 0, "runs": [], "warnings": [], "dia_in": None,
                "report": f"Unknown direction {direction!r}; "
                          "use 'downstream' or 'this'."}
    dia = float(dia_in)
    warnings: list = []
    if not any(abs(dia - s) < 1e-9 for s in SIZES_IN):
        warnings.append(f'{fmt_dia_in(dia)}" is not a standard trade size.')
    system = str(ent.props.get("system", "san"))
    targets = [ent.id]
    if direction == "downstream":
        net = network(model)
        queue, seen = [ent.id], {ent.id}
        while queue:
            run = model.entity(queue.pop(0))
            for n_idx, _cum in _vertex_nodes(net, run):
                if n_idx is None:
                    continue
                for leg in net.nodes[n_idx].legs:
                    if (leg.end == "first" and not leg.inbound
                            and leg.system == system
                            and leg.ent_id not in seen):
                        seen.add(leg.ent_id)
                        queue.append(leg.ent_id)
                        targets.append(leg.ent_id)
    snap = model._snapshot()
    for eid in targets:
        model.entity(eid).props["dia_in"] = dia
    model._commit(snap)
    return {"changed": len(targets), "runs": list(targets), "dia_in": dia,
            "warnings": warnings,
            "report": f'Resized {len(targets)} run(s) to {fmt_dia_in(dia)}" '
                      f"({direction})."}


def check(model) -> list:
    """Rule sweep — warns, never fixes.  Returns
    ``[{"code", "level": "warn"|"info", "ent_id": str|None,
    "xy": (x, y)|None, "msg": str}]``.  Codes: ``slope-min`` (drainage run
    sloped under the MIN_SLOPE table), ``open-end`` (uncapped,
    non-fixture), ``cross-drainage``, ``reduce-downstream`` (no reduction
    in the direction of flow), ``vent-slope`` (info: vents pitch back to
    the drain)."""
    out: list = []
    net = network(model)
    for e in _pipes(model):
        system = str(e.props.get("system", "san"))
        s = e.props.get("slope_in_ft")
        dia = float(e.props.get("dia_in", 4.0))
        if system in DRAINAGE and s is not None:
            need, basis = min_slope(dia)
            if float(s) < need - 1e-9:
                out.append({"code": "slope-min", "level": "warn",
                            "ent_id": e.id, "xy": tuple(e.pts[0]),
                            "msg": f'{e.id}: {fmt_dia_in(dia)}" {system} at '
                                   f"{fmt_slope(float(s))} — minimum "
                                   f"{fmt_slope(need)} ({basis})"})
        if system == "vent" and s is not None:
            out.append({"code": "vent-slope", "level": "info",
                        "ent_id": e.id, "xy": tuple(e.pts[0]),
                        "msg": f"{e.id}: vents pitch back to the drain — "
                               "slope noted for information only"})
    for f in derive_fittings(model, net):
        if f.kind == "open":
            out.append({"code": "open-end", "level": "warn",
                        "ent_id": f.ent_ids[0], "xy": f.node_xy,
                        "msg": f"uncapped open end at {_fmt_xy(*f.node_xy)}"
                               f' ({fmt_dia_in(f.dia_in)}" {f.system})'})
        elif f.kind == "cross" and f.system in DRAINAGE:
            out.append({"code": "cross-drainage", "level": "warn",
                        "ent_id": f.ent_ids[0], "xy": f.node_xy,
                        "msg": f"cross fitting on {f.system} at "
                               f"{_fmt_xy(*f.node_xy)} — avoid crosses on "
                               "drainage; verify against project code"})
    for node in net.nodes:
        for system in DRAINAGE:
            ins = [leg.dia_in for leg in node.legs
                   if leg.inbound and leg.system == system]
            outs = [(leg.dia_in, leg.ent_id) for leg in node.legs
                    if not leg.inbound and leg.system == system]
            if not ins or not outs:
                continue
            mx = max(ins)
            for dia, eid in outs:
                if dia < mx - 1e-9:
                    out.append({
                        "code": "reduce-downstream", "level": "warn",
                        "ent_id": eid, "xy": node.xy,
                        "msg": f'{fmt_dia_in(mx)}" {system} reduces to '
                               f'{fmt_dia_in(dia)}" at {_fmt_xy(*node.xy)}'
                               " — no reduction in the direction of flow"})
    return out


# ---------------------------------------------------------------- bridges ---

def takeoff(model, book=None) -> list:
    """Tally -> Reckoner, extending draft.takeoff_lines semantics: pipe LF
    grouped by (system, diameter, material) plus fitting counts by
    kind/size ("open" candidates and bare fixture connections are not
    fittings and are skipped).  With a PriceBook, lines get code /
    unit_cost / total via ``book.find(subject)``."""
    from .reckoner import TakeoffLine
    lf: dict[tuple, float] = {}
    for e in _pipes(model):
        key = (str(e.props.get("system", "san")),
               float(e.props.get("dia_in", 4.0)),
               str(e.props.get("material", "")))
        lf[key] = lf.get(key, 0.0) + _run_length(e)
    lines = []
    for (system, dia, mat), qty in lf.items():
        label = SYSTEMS.get(system, {}).get("label", system)
        subject = f'{label} pipe {fmt_dia_in(dia)}"'
        if mat:
            subject += f", {mat}"
        lines.append(TakeoffLine(subject=subject, kind="length",
                                 qty=qty, unit="lf"))
    counts: dict[tuple, int] = {}
    for f in derive_fittings(model):
        if f.kind in ("open", "fixture"):
            continue
        counts[(f.kind, f.dia_in)] = counts.get((f.kind, f.dia_in), 0) + 1
    lines += [TakeoffLine(subject=f'Fitting: {kind} {fmt_dia_in(dia)}"',
                          kind="count", qty=float(n), unit="ea")
              for (kind, dia), n in counts.items()]
    lines.sort(key=lambda ln: (ln.kind, ln.subject.lower(), ln.subject))
    if book is not None:
        for line in lines:
            item = book.find(line.subject)
            if item is not None:
                line.code = item.code
                line.unit_cost = item.unit_cost
                line.total = line.qty * item.unit_cost
    return lines


def run_z(ent, base_z: float = 0.0) -> list:
    """Vertex z profile of one pipe run — INVERT elevations (pipe bottom),
    one per vertex, interpolating along the run from ``invert_ft`` at the
    commanded fall.  An invert with no slope sits flat at the invert;
    neither sits flat at ``base_z``.  Shared by :func:`to_bim` and the
    clash engine (``clash.capsules``) so the viewer and the checker can
    never disagree about where a pipe is."""
    inv = ent.props.get("invert_ft")
    slope = ent.props.get("slope_in_ft")
    zs, cum, prev = [], 0.0, None
    for p in ent.pts:
        if prev is not None:
            cum += math.hypot(p[0] - prev[0], p[1] - prev[1])
        if inv is None:
            zs.append(float(base_z))
        elif slope is None:
            zs.append(float(inv))
        else:
            zs.append(float(inv) - float(slope) * cum / 12.0)
        prev = p
    return zs


def to_bim(model, base_z: float = 0.0):
    """Pipe runs as 3D segments at their invert elevations, so slope is
    visible in the viewer.  Vertex z comes from :func:`run_z`.  Segments
    are colored by system with ``.system`` set to the system label, so
    the 3D legend toggles work; ``model.systems`` lists only the systems
    present.  Each segment carries ``radius`` = dia_in / 24 (trade
    diameter in inches -> radius in feet) so the viewer's shaded mode can
    extrude the run into a pipe solid — wireframe consumers ignore it."""
    from .bim import Model, Segment
    m = Model()
    used: dict[str, tuple] = {}
    for e in _pipes(model):
        system = str(e.props.get("system", "san"))
        spec = SYSTEMS.get(system, {})
        color = spec.get("color", "#8899aa")
        label = spec.get("label", system)
        used.setdefault(system, (label, color))
        zs = run_z(e, base_z)
        dia = float(e.props.get("dia_in", 4.0))
        width = max(1.0, dia / 3.0)
        for (p, zp), (q, zq) in zip(zip(e.pts, zs),
                                    zip(e.pts[1:], zs[1:])):
            m.segments.append(Segment((p[0], p[1], zp), (q[0], q[1], zq),
                                      color=color, width=width,
                                      system=label, radius=dia / 24.0))
    m.systems = [used[s] for s in SYSTEMS if s in used]
    return m
