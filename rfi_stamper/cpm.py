"""The Tautline — critical-path scheduling over the project store.

Textbook precedence-diagram (activity-on-node) CPM over the EXISTING
``project.ScheduleItem`` records — no schema migration: ``depends``
already carries prerequisite ids; an optional ``+N`` / ``-N`` suffix on
an entry is a finish-to-start lag in workdays (a bare id is lag 0).

Forward pass (ES/EF), backward pass (LS/LF), Total Float, Free Float,
critical chain = the zero-total-float path (the taut line — the one
chain with no slack).  FS links only: they are ~90% of real construction
logic; SS/FF live in full-time schedulers' tools (SKIP).  Workday math,
not calendar days: weekends are non-working (mask configurable; holiday
calendars are per-project data entry — SKIP).

The convention that kills the classic off-by-one, stated ONCE: ES/EF are
MORNING indices.  An activity of duration ``dur`` starting index ``s``
occupies workdays ``s .. s+dur-1``; ``EF = ES + dur``; its finish DATE is
``from_index(EF - 1)``.  An item's entered ``start`` acts as
start-no-earlier-than in the forward pass — without it the computed
schedule contradicts the user's own bars.

Dirty-data tolerance (the store is hand-editable JSON): junk dates and
dangling predecessor ids skip-with-warning per record; a dependency
CYCLE refuses the whole analysis loudly and NAMES the loop by title —
never a hang, never a silent drop.  Read-only: analyze() never writes to
the store.  Pure stdlib, deterministic (stored order breaks all ties).
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field

#: non-working weekdays (Mon=0 .. Sun=6); the standard site week.
WEEKEND = frozenset((5, 6))

_LAG = re.compile(r"^(.*?)([+-]\d+)$")


# ------------------------------------------------------------ workday math ---

def to_index(d: _dt.date, d0: _dt.date, weekend=WEEKEND) -> int:
    """Workdays strictly before ``d``, counting from anchor ``d0`` (which
    need not itself be a workday)."""
    if d <= d0:
        return 0
    n, cur = 0, d0
    while cur < d:
        if cur.weekday() not in weekend:
            n += 1
        cur += _dt.timedelta(days=1)
    return n


def from_index(k: int, d0: _dt.date, weekend=WEEKEND) -> _dt.date:
    """The (k+1)-th workday on/after ``d0`` — the inverse of to_index for
    workdays: from_index(to_index(d)) == d whenever d is a workday."""
    cur = d0
    while cur.weekday() in weekend:
        cur += _dt.timedelta(days=1)
    n = 0
    while n < int(k):
        cur += _dt.timedelta(days=1)
        while cur.weekday() in weekend:
            cur += _dt.timedelta(days=1)
        n += 1
    return cur


def workdays_between(a: _dt.date, b: _dt.date, weekend=WEEKEND) -> int:
    """Inclusive workday count of [a, b]."""
    if b < a:
        return 0
    n, cur = 0, a
    while cur <= b:
        if cur.weekday() not in weekend:
            n += 1
        cur += _dt.timedelta(days=1)
    return n


def parse_depend(entry) -> tuple:
    """One depends entry -> (predecessor_id, lag_workdays).  A bare id is
    lag 0; ``"<id>+3"`` / ``"<id>-1"`` carry an FS lag (negative legal)."""
    s = str(entry)
    m = _LAG.match(s)
    if m and m.group(1):
        return m.group(1), int(m.group(2))
    return s, 0


# ---------------------------------------------------------------- analysis ---

@dataclass
class CpmResult:
    """One analysis pass.  ``by_id`` maps item id -> {es, ef, ls, lf, tf,
    ff, dur, critical, es_date, ef_date, ls_date, lf_date}.  ``cycle`` is
    the named dependency loop (titles, in order) when one exists — floats
    are then NOT computed (honest refusal, never a hang)."""
    by_id: dict = field(default_factory=dict)
    anchor: _dt.date | None = None
    project_finish: _dt.date | None = None
    critical_ids: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    cycle: list = field(default_factory=list)


def analyze(items, weekend=WEEKEND) -> CpmResult:
    """CPM over ScheduleItem-shaped records (needs .id, .title, .start,
    .end, .depends).  See the module docstring for every convention."""
    res = CpmResult()
    good: list = []
    for it in items:
        try:
            s = _dt.date.fromisoformat(str(it.start))
            e = _dt.date.fromisoformat(str(it.end))
        except (ValueError, TypeError):
            res.warnings.append(
                f"{it.title or it.id}: bad date(s) — skipped from CPM")
            continue
        dur = workdays_between(s, e, weekend)
        if dur < 1:
            res.warnings.append(
                f"{it.title or it.id}: start/end fall on non-workdays — "
                "duration clamped to 1")
            dur = 1
        good.append((it, s, e, dur))
    if not good:
        return res

    d0 = min(s for _it, s, _e, _d in good)
    res.anchor = d0
    ids = {it.id for it, *_ in good}
    order = [it.id for it, *_ in good]           # stored order = tie order
    node = {it.id: {"item": it, "dur": dur,
                    "snet": to_index(s, d0, weekend), "preds": []}
            for it, s, _e, dur in good}
    succs: dict = {i: [] for i in ids}
    for it, *_ in good:
        for entry in (it.depends or []):
            pid, lag = parse_depend(entry)
            if pid == it.id:
                res.warnings.append(f"{it.title}: depends on itself — "
                                    "link ignored")
                continue
            if pid not in ids:
                res.warnings.append(
                    f"{it.title}: unknown predecessor '{pid}' — "
                    "link ignored")
                continue
            node[it.id]["preds"].append((pid, lag))
            succs[pid].append((it.id, lag))

    # Kahn topological sort; leftovers mean a cycle — name it by title
    indeg = {i: len(node[i]["preds"]) for i in order}
    queue = [i for i in order if indeg[i] == 0]
    topo: list = []
    qi = 0
    while qi < len(queue):
        i = queue[qi]
        qi += 1
        topo.append(i)
        for sid, _lag in succs[i]:
            indeg[sid] -= 1
            if indeg[sid] == 0:
                queue.append(sid)
    if len(topo) < len(order):
        left = [i for i in order if i not in set(topo)]
        cur = left[0]
        seen: list = []
        while cur not in seen:                   # walk preds until a repeat
            seen.append(cur)
            cur = next(p for p, _lg in node[cur]["preds"] if p in left)
        loop = seen[seen.index(cur):] + [cur]
        res.cycle = [node[i]["item"].title or i for i in reversed(loop)]
        return res

    # forward pass: morning indices; entered start = start-no-earlier-than
    es: dict = {}
    ef: dict = {}
    for i in topo:
        pred_es = [ef[p] + lag for p, lag in node[i]["preds"]]
        es[i] = max(max(pred_es, default=0), node[i]["snet"], 0)
        ef[i] = es[i] + node[i]["dur"]
    finish = max(ef[i] for i in order)

    # backward pass + floats
    ls: dict = {}
    lf: dict = {}
    for i in reversed(topo):
        outs = [ls[s] - lag for s, lag in succs[i]]
        lf[i] = min(outs, default=finish)
        ls[i] = lf[i] - node[i]["dur"]
    for i in order:
        outs = [es[s] - lag for s, lag in succs[i]]
        ff = min(outs, default=finish) - ef[i]
        tf = ls[i] - es[i]
        res.by_id[i] = {
            "dur": node[i]["dur"], "es": es[i], "ef": ef[i],
            "ls": ls[i], "lf": lf[i], "tf": tf, "ff": ff,
            "critical": tf == 0,
            "es_date": from_index(es[i], d0, weekend),
            "ef_date": from_index(ef[i] - 1, d0, weekend),
            "ls_date": from_index(ls[i], d0, weekend),
            "lf_date": from_index(lf[i] - 1, d0, weekend),
        }
    res.critical_ids = [i for i in order if res.by_id[i]["critical"]]
    res.project_finish = from_index(finish - 1, d0, weekend)
    return res
