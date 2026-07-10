"""The Cut Ticket — the production order the drawing writes for the Swatchbook.

In the garment trade a cut ticket is the order that travels with a cut of
cloth telling the shop what to make; here it is the pull list the Loft
model writes as you place tagged fixtures: every save takes a tag census
of the drawing and reconciles it into the project store, so the Swatchbook
always has a live manifest of what the model needs cut sheets for.

The discipline (borrowed from the Harvest tray and the Resolution store):

* **Explicit tags only.**  A fixture enters the census when the drafter
  typed a tag on it (``WC-1``).  Tag-shaped TEXT is never scraped — the
  tag pattern collides exactly with sheet references (``2/P-1``) and
  callout bubbles (``A-501``), so scraping would fabricate fixtures.
  Untagged fixtures are COUNTED and surfaced ("3 untagged"), never given
  invented tags.
* **Machine facts refresh; human work is never touched.**  Counts, source
  drawings and stencil identity are overwritten to stay truthful; the
  component callouts, category overrides, notes and status a human typed
  survive every re-census.
* **Orphans are flagged, never deleted.**  A tag that leaves the model is
  marked ``missing_from_model`` and stays on the list until a human
  removes it; putting the fixture back revives it.
* **Proposals only.**  The Cut Ticket feeds the Swatchbook rows to
  confirm; nothing ever builds a PDF without the explicit Build All.
"""
from __future__ import annotations

from . import swatchbook
from .project import PullItem
from .swatchbook import CATEGORIES

#: stencil key -> 0-49 submittal prefix.  Stencils with NO honest entry in
#: the approved table (cleanout, structure columns) map to -1 and surface
#: as "needs category" — the 0-49 table is an office standard; guessing a
#: wrong prefix on a submittal is a rejection, so we never force one.
PREFIX_BY_STENCIL = {
    "wc": 1, "ur": 2, "lav": 3,
    "sink_s": 4, "sink_d": 4, "mop": 4,
    "fd": 8, "shower": 11, "tub": 11,
    "df": 14, "hb": 21, "wh": 38,
}


def census(model) -> dict:
    """Pure tag census over a Loft model — touches nothing, orders stably.

    Returns ``{"rows": [{tag, stencil, label, count, prefix, flags}],
    "untagged": {stencil_label: count}, "conflicts": [msg]}``; rows are
    keyed by :func:`swatchbook.canonical_tag` and sorted by
    ``(prefix, tag)`` so identical models yield identical censuses
    regardless of entity order.
    """
    from .draft import STENCILS
    rows: dict = {}
    untagged: dict = {}
    conflicts: list = []
    for ent in getattr(model, "ents", []):
        if ent.kind != "fixture":
            continue
        stencil = str(ent.props.get("stencil", ""))
        st = STENCILS.get(stencil, {})
        label = st.get("label", stencil or "unknown stencil")
        raw = str(ent.props.get("tag", "")).strip()
        if not raw:
            untagged[label] = untagged.get(label, 0) + 1
            continue
        tag = swatchbook.canonical_tag(raw)
        r = rows.get(tag)
        if r is None:
            prefix = PREFIX_BY_STENCIL.get(stencil, -1)
            flags = []
            if stencil not in STENCILS:
                flags.append(f"unknown stencil {stencil!r}")
            elif prefix < 0:
                flags.append(
                    f"no 0-49 category for '{label}' — set one before "
                    "building (never guessed)")
            rows[tag] = {"tag": tag, "stencil": stencil, "label": label,
                         "count": 1, "prefix": prefix, "flags": flags}
        else:
            r["count"] += 1
            if r["stencil"] != stencil:
                msg = (f"{tag}: tagged on two different stencils "
                       f"({r['stencil']!r} and {stencil!r}) — confirm "
                       "which product this tag means")
                if msg not in conflicts:
                    conflicts.append(msg)
                if msg not in r["flags"]:
                    r["flags"].append(msg)
    ordered = sorted(rows.values(),
                     key=lambda r: (r["prefix"] if r["prefix"] >= 0 else 99,
                                    r["tag"]))
    return {"rows": ordered, "untagged": dict(sorted(untagged.items())),
            "conflicts": conflicts}


def sync_project(project, model, drawing_key: str | None) -> dict:
    """Reconcile one drawing's census into ``project.pull_list``.

    ``drawing_key`` identifies the drawing (its file path); per-drawing
    counts live in each item's ``sources`` so several drawings merge into
    one project-level list.  Saves the store ONCE, and only when something
    actually changed.  Returns ``{"tags", "added", "updated", "orphaned",
    "untagged", "conflicts"}``.
    """
    key = drawing_key or "(unsaved drawing)"
    res = census(model)
    items = project.pull_list
    by_tag = {it.tag: it for it in items}
    changed = False
    added = updated = orphaned = 0
    seen = set()
    for row in res["rows"]:
        seen.add(row["tag"])
        it = by_tag.get(row["tag"])
        if it is None:
            items.append(PullItem.new(
                tag=row["tag"], prefix=row["prefix"],
                category=CATEGORIES.get(row["prefix"], ""),
                stencil=row["stencil"], label=row["label"],
                count=row["count"], sources={key: row["count"]},
                flags=list(row["flags"]), origin="model"))
            added += 1
            changed = True
            continue
        srcs = dict(it.sources)
        srcs[key] = row["count"]
        total = sum(srcs.values())
        if (srcs != it.sources or it.count != total
                or it.stencil != row["stencil"] or it.flags != row["flags"]
                or it.missing_from_model):
            it.sources = srcs
            it.count = total
            it.stencil = row["stencil"]        # machine facts stay truthful;
            it.label = row["label"]            # callouts/notes/status/prefix
            it.flags = list(row["flags"])      # are human-owned — untouched
            it.missing_from_model = False
            updated += 1
            changed = True
    for it in items:
        if it.tag in seen or key not in it.sources:
            continue
        srcs = dict(it.sources)                # the tag left THIS drawing
        srcs.pop(key)
        it.sources = srcs
        it.count = sum(srcs.values())
        if not srcs and it.origin == "model" and not it.missing_from_model:
            it.missing_from_model = True       # tombstone: flagged, NEVER
            orphaned += 1                      # auto-deleted (harvest law)
        changed = True
    if changed and project.path:
        project.save()
    return {"tags": len(res["rows"]), "added": added, "updated": updated,
            "orphaned": orphaned,
            "untagged": sum(res["untagged"].values()),
            "conflicts": res["conflicts"], "changed": changed}


def to_packets(items) -> tuple:
    """Pull items -> Swatchbook recipe packets + the not-buildable leftovers.

    Returns ``(packets, needs_attention)``: packets are ready recipe dicts
    (callouts ride along and re-resolve at build); ``needs_attention`` are
    ``(tag, reason)`` rows that cannot become a packet yet (no 0-49
    category) — surfaced, never silently dropped or force-prefixed.
    """
    packets, needs = [], []
    for it in sorted(items, key=lambda i: (i.prefix if i.prefix >= 0 else 99,
                                           i.tag)):
        if it.prefix < 0:
            needs.append((it.tag, "; ".join(it.flags)
                          or "no 0-49 category assigned"))
            continue
        flags = [f"model-sourced: {it.count} placed (the Cut Ticket)"]
        if it.missing_from_model:
            flags.append("MISSING FROM MODEL — fixture no longer placed; "
                         "remove or re-place before submitting")
        flags += [f for f in it.flags if f not in flags]
        missing = ([] if it.callouts else
                   ["components — enter callouts from the schedule "
                    "paragraph"])
        packets.append({
            "filename": swatchbook.packet_filename(it.prefix, it.tag),
            "tag": it.tag, "prefix": it.prefix,
            "category": it.category or CATEGORIES.get(it.prefix, ""),
            "callouts": list(it.callouts),
            "components": [], "missing": missing, "flags": flags,
            "origin": "model"})
    return packets, needs
