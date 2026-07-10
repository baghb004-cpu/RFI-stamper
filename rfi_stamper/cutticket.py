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

import math
import re as _re

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
        flags = [f"{it.origin}-sourced: {it.count} placed (the Cut Ticket)"]
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
            "origin": it.origin})
    return packets, needs


# --------------------------------------------------------------------------- #
#  The set-scan lane (SETSCAN Phase 3): the Cut Ticket reads the plan set     #
# --------------------------------------------------------------------------- #
# Naked tag-shaped text is a false-positive trap (the module's own first
# law), so the scan is CONTEXT-GATED: a tag counts only when it sits
# beside a Reed Count-recognized fixture symbol on a Story Pole-verified
# sheet, or appears as a row of the fixture schedule table itself.  Hard
# rejects: sheet-reference words ("2/P-1", "SEE P-101"), any token equal
# to one of the set's own sheet numbers, and non-word-exact matches.
# Everything skipped is surfaced.

_TAG_WORD = _re.compile(r"^([A-Z]{1,4}-?\d+)[.,;:]?$")
_HDR_TAG = {"MARK", "TAG", "SYMBOL", "FIXTURE"}
_HDR_DESC = "DESCRIPTION"


def _lines(page) -> list:
    """Words grouped into visual text lines -> [(y, [(x0, x1, text)])].

    Grouping is by y-band, NOT the extractor's (block, line) — CAD-
    produced tables emit every cell as its own block, so block grouping
    would shred the table rows."""
    words = sorted(page.get_text("words"), key=lambda w: (w[1] + w[3]) / 2)
    out = []
    for w in words:
        cy = (w[1] + w[3]) / 2
        h = max(w[3] - w[1], 1.0)
        if out and abs(cy - out[-1][0]) <= 0.5 * h:
            out[-1][1].append((w[0], w[2], w[4]))
        else:
            out.append([cy, [(w[0], w[2], w[4])]])
    for row in out:
        row[1].sort()
    return [(y, cells) for y, cells in out]


def schedule_rows(page) -> list:
    """Parse a fixture-schedule table -> [{tag, description, callout}].

    Header detection is word-exact: a line carrying one of MARK / TAG /
    SYMBOL / FIXTURE plus DESCRIPTION defines the columns by its cells'
    x-positions; data rows read until the tag column goes quiet.
    Wrapped description lines (empty tag cell) append to the row above."""
    lines = _lines(page)
    for li, (hy, cells) in enumerate(lines):
        texts = [t.upper().strip() for _, _, t in cells]
        if _HDR_DESC not in texts or not (_HDR_TAG & set(texts)):
            continue
        heads = []
        for (x0, _x1, t) in cells:
            tu = t.upper().strip()
            if tu in _HDR_TAG or tu in (_HDR_DESC, "MANUFACTURER", "MFR",
                                        "MFG", "MODEL", "REMARKS", "NOTES"):
                heads.append((x0, tu))
        heads.sort()
        cols = {}
        for k, (x0, name) in enumerate(heads):
            x1 = heads[k + 1][0] if k + 1 < len(heads) else 1e9
            cols[name] = (x0 - 4.0, x1 - 4.0)
        tag_col = next(c for c in cols if c in _HDR_TAG)

        def cell(row_cells, col):
            lo, hi = cols[col]
            return " ".join(t for x0, _x1, t in row_cells
                            if lo <= x0 < hi).strip()

        rows, dry = [], 0
        for _y, rc in lines[li + 1:]:
            tag_txt = cell(rc, tag_col)
            m = _TAG_WORD.match(tag_txt.upper())
            if m:
                dry = 0
                mfr = cell(rc, "MANUFACTURER") if "MANUFACTURER" in cols \
                    else (cell(rc, "MFR") if "MFR" in cols
                          else cell(rc, "MFG") if "MFG" in cols else "")
                model = cell(rc, "MODEL") if "MODEL" in cols else ""
                rows.append({
                    "tag": swatchbook.canonical_tag(m.group(1)),
                    "description": cell(rc, _HDR_DESC),
                    "callout": " ".join(x for x in (mfr, model) if x)})
            elif rows and not tag_txt and cell(rc, _HDR_DESC):
                rows[-1]["description"] += " " + cell(rc, _HDR_DESC)
                dry = 0
            else:
                dry += 1
                if dry >= 3:
                    break
        return rows
    return []


def scan_set(pdf_path: str, extra_symbols: dict | None = None,
             log=lambda *_: None) -> dict:
    """Scan a whole plan set for fixture tags, context-gated.

    Returns ``{"rows": [...], "untagged": {stencil: n}, "skipped": [...],
    "schedule_pages": [...]}``; rows carry tag / count (symbol-derived) /
    stencil / label / prefix / callouts / flags / pages.  Proposals only —
    nothing touches the project store until :func:`sync_scan`."""
    import fitz
    from . import reedcount, setscale
    from .sheets import SheetIndex

    idx = SheetIndex(pdf_path)
    sheet_tokens = {p.sheet.upper() for p in idx.pages}
    doc = fitz.open(pdf_path)
    rows: dict = {}
    untagged: dict = {}
    skipped: list = []
    schedule_pages: list = []

    def row(tag):
        return rows.setdefault(tag, {
            "tag": tag, "count": 0, "stencil": "", "label": "",
            "prefix": -1, "callouts": [], "flags": [], "pages": []})

    try:
        for pno in range(doc.page_count):
            page = doc[pno]
            sheet = idx.pages[pno].sheet
            # lane 2: the fixture schedule table names the tags directly
            for sr in schedule_rows(page):
                r = row(sr["tag"])
                if sr["description"] and not r["label"]:
                    r["label"] = sr["description"]
                if sr["callout"] and sr["callout"] not in r["callouts"]:
                    r["callouts"].append(sr["callout"])
                note = f"fixture schedule row on {sheet}"
                if note not in r["flags"]:
                    r["flags"].append(note)
                if pno + 1 not in schedule_pages:
                    schedule_pages.append(pno + 1)
            # lane 1: symbols corroborate tags — only at a VERIFIED scale
            v = setscale.sheet_verdict(page)
            if v["status"] != "PASS":
                skipped.append(f"{sheet}: symbol lane skipped — "
                               f"{v['reasons'][0]}")
                continue
            rep = reedcount.count_fixtures(page, v["pt_per_ft"],
                                           extra_symbols=extra_symbols)
            words = []
            for w in page.get_text("words"):
                m = _TAG_WORD.match(w[4].strip().upper())
                if not m:
                    continue
                tag = swatchbook.canonical_tag(m.group(1))
                if tag.upper() in sheet_tokens:
                    skipped.append(f"{sheet}: '{w[4]}' matches a sheet "
                                   "number in this set — rejected")
                    continue
                words.append((tag, (w[0] + w[2]) / 2, (w[1] + w[3]) / 2))
            for h in rep["hits"]:
                x0, y0, x1, y1 = h["bbox"]
                reach = max(0.9 * math.hypot(x1 - x0, y1 - y0), 10.0)
                near = [(tag, wx, wy) for tag, wx, wy in words
                        if x0 - reach <= wx <= x1 + reach
                        and y0 - reach <= wy <= y1 + reach]
                if not near:
                    untagged[h["key"]] = untagged.get(h["key"], 0) + 1
                    continue
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                tag = min(near, key=lambda t: (t[1] - cx) ** 2
                          + (t[2] - cy) ** 2)[0]
                r = row(tag)
                r["count"] += 1
                if not r["stencil"]:
                    r["stencil"] = h["key"]
                    r["prefix"] = PREFIX_BY_STENCIL.get(h["key"], -1)
                    if not r["label"]:
                        r["label"] = h["label"]
                if pno + 1 not in r["pages"]:
                    r["pages"].append(pno + 1)
                note = f"{h['key']} symbol on {sheet}"
                if note not in r["flags"]:
                    r["flags"].append(note)
            log(f"  {sheet}: {len(rep['hits'])} symbol(s), "
                f"{len(rep['unknown'])} unknown")
    finally:
        doc.close()
    for r in rows.values():
        if r["callouts"]:
            r["flags"].append("callouts pre-filled from the fixture "
                              "schedule — confirm against the spec")
        if r["prefix"] < 0:
            r["flags"].append("needs a 0-49 category (schedule row "
                              "without a corroborating symbol)")
    out = sorted(rows.values(),
                 key=lambda r: (r["prefix"] if r["prefix"] >= 0 else 99,
                                r["tag"]))
    return {"rows": out, "untagged": untagged, "skipped": skipped,
            "schedule_pages": schedule_pages}


def sync_scan(project, scan: dict, set_key: str) -> dict:
    """Reconcile a set-scan into ``project.pull_list`` (same field-
    ownership law as :func:`sync_project`): machine facts under THIS
    source key refresh; human fields are never touched — schedule
    callouts pre-fill ONLY on newly created rows; stencil/label only
    fill empty fields (the model lane's facts are stronger)."""
    key = f"set-scan:{set_key}"
    items = project.pull_list
    by_tag = {it.tag: it for it in items}
    changed = False
    added = updated = orphaned = 0
    seen = set()
    for r in scan["rows"]:
        seen.add(r["tag"])
        it = by_tag.get(r["tag"])
        if it is None:
            items.append(PullItem.new(
                tag=r["tag"], prefix=r["prefix"],
                category=CATEGORIES.get(r["prefix"], ""),
                stencil=r["stencil"], label=r["label"], count=r["count"],
                sources={key: r["count"]}, callouts=list(r["callouts"]),
                flags=list(r["flags"]), origin="set-scan"))
            added += 1
            changed = True
            continue
        srcs = dict(it.sources)
        srcs[key] = r["count"]
        total = sum(srcs.values())
        new_flags = [f for f in r["flags"] if f not in it.flags]
        if (srcs != it.sources or it.count != total or new_flags
                or it.missing_from_model):
            it.sources = srcs
            it.count = total
            if not it.stencil and r["stencil"]:
                it.stencil = r["stencil"]
            if not it.label and r["label"]:
                it.label = r["label"]
            it.flags = it.flags + new_flags
            it.missing_from_model = False
            updated += 1
            changed = True
    for it in items:
        if it.tag in seen or key not in it.sources:
            continue
        srcs = dict(it.sources)
        srcs.pop(key)
        it.sources = srcs
        it.count = sum(srcs.values())
        if (not srcs and it.origin in ("model", "set-scan")
                and not it.missing_from_model):
            it.missing_from_model = True
            orphaned += 1
        changed = True
    if changed and project.path:
        project.save()
    return {"tags": len(scan["rows"]), "added": added, "updated": updated,
            "orphaned": orphaned,
            "untagged": sum(scan["untagged"].values()),
            "skipped": list(scan["skipped"]), "changed": changed}
