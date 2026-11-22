"""The run report: in plain words, as text and as PDF.  Brief section 11.

It says what was checked against what, with counts; what was found; what
lines up; what was not checked and why; which elements are single source;
and what hidden geometry was dropped.  Sentences about checks are the
proof records' own statements.  The second source is always named: the
report does not use the bare word for "checked and found true" at all.
"""
from __future__ import annotations

import io
import os

from .. import papertrail as pt
from ..fsutil import atomic_write_bytes
from . import model as M

HEAD = "=="


def _h(lines, title) -> None:
    lines.append("")
    lines.append(f"{HEAD} {title}")


def _say(run, check, code, key="summary"):
    pid = run.ctx.proofs.get((check, code)) if run.ctx is not None else None
    if not pid:
        return None
    return run.trail.say(pid, key).text


SUMMARY_CHECKS = ["GRID-PRINTED", "GRID-CAD", "XC-CORNER-CAD", "XC-FACE-CAD", "XC-CAD-ONLY",
                  "XC-SCHEDULE", "XC-ZONE", "XC-ARCH-ENDS", "XC-ARCH-STRINGS",
                  "XC-LEVEL-BELOW", "XC-OVERLAY", "XC-REVISION"]


def build_lines(run) -> list:
    """The report as plain lines (headings start with ``==``)."""
    L: list = []
    rr = run.rr
    L.append("BATTERBOARD RUN REPORT")
    L.append(f"Result: {run.status}")
    proj = (run.opts.project or {}).get("project")
    if proj:
        L.append(f"Project: {proj}")
    if rr is not None and rr.frame_label:
        L.append(f"Frame: {rr.frame_label}")
    L.append("Hats: " + ", ".join(run.opts.hats))
    if not run.opts.issue:
        L.append("Every plate is stamped FOR REVIEW.")

    _h(L, "STAGES")
    for st in run.stages:
        L.append(f"{st.name}: {st.status}")
        L.extend("  " + ln for ln in st.lines)

    if rr is None:
        return L
    _h(L, "FILES READ")
    for r in rr.refs:
        if r.cls == M.PG_UNKNOWN:
            continue
        bits = [r.name]
        if r.page:
            bits.append(f"page {r.page}")
        if r.sheet:
            bits.append(r.sheet)
        bits.append(r.cls)
        if r.level:
            bits.append(r.level)
        if r.revision:
            bits.append(f"revision {r.revision}")
        if r.prior:
            bits.append("earlier issue")
        if r.rfi:
            bits.append(f"RFI {r.rfi}")
        L.append("  " + ", ".join(bits))
    if rr.unknown:
        _h(L, "NOT CLASSIFIED AND NOT USED")
        for r in rr.unknown:
            L.append(f"  {r.name}" + (f" page {r.page}" if r.page else "") + f": {r.why}")
    blocked = [lv for lv in rr.levels if lv.blocked]
    if blocked:
        _h(L, "LEVELS NOT LAID OUT")
        for lv in blocked:
            for why in lv.blocked:
                L.append(f"  {lv.name}: {why}")
    if rr.asks:
        _h(L, "DECISIONS NEEDED")
        for a in rr.asks:
            cands = "; ".join(f"color {tuple(c['rgb'])} on {c['shapes']} shape(s), "
                              f"{c['area_ft2']} sq ft" for c in a["candidates"])
            L.append(f"  {a['sheet']}: which fill color is the concrete? {cands}. "
                     "Picture crops are in the run log.")
    if rr.remarks:
        _h(L, "READER REMARKS")
        L.extend("  " + r for r in rr.remarks)

    levels = [lv for lv in rr.levels if not lv.blocked]
    _h(L, "WHAT WAS CHECKED AGAINST WHAT")
    for lv in levels:
        L.append(f"{lv.name} ({lv.struct.sheet}, scale {lv.scale_label})")
        for chk in SUMMARY_CHECKS:
            s = _say(run, chk, lv.code)
            if s:
                L.append("  " + s)

    _h(L, "WHAT WAS FOUND")
    any_item = False
    for lv in levels:
        tags = run.reg.items(lv.code)
        if not tags:
            continue
        any_item = True
        c = run.reg.counts(lv.code)
        L.append(f"{lv.name}: {c['total']} open item(s): {c.get('answer', 0)} answer needed "
                 f"before layout, {c.get('confirm', 0)} confirm before forming, "
                 f"{c.get('answered', 0)} answered.")
        for t in tags:
            L.append(f"  {t.ref} [{t.category_label}] {t.label}. {t.wrong} Needed: {t.needed} "
                     f"Status: {t.status_text()}.")
    if not any_item:
        L.append("  Nothing was raised.")

    _h(L, "WHAT WAS NOT CHECKED AND WHY")
    if run.ctx is not None:
        from .crosscheck import CHECKS
        for cid, code, why in run.ctx.skips:
            L.append(f"  {code}: {CHECKS[cid]['title']}: not compared; {why}.")
        L.append("  " + run.trail.say(run.ctx.trail_scope.id).text)
    for lv in levels:
        if lv.arch_note:
            L.append(f"  {lv.name}: architect dimensions not compared; {lv.arch_note}.")

    _h(L, "SINGLE SOURCE")
    for lv in levels:
        s = _say(run, "SINGLE-SOURCE", lv.code)
        if s:
            L.append(f"{lv.name}: {s}")
        ids = [e.id for e in lv.elements if e.single_source
               and e.kind in (M.WALL, M.COLUMN, M.ROUND_COLUMN, M.CONCRETE, M.MASONRY)]
        if ids:
            L.append("  Concrete and masonry carried by one source only: " + ", ".join(ids) + ".")
        mixed = [e for e in lv.elements if e.prov.method == M.CAD
                 and e.kind in (M.WALL, M.COLUMN, M.ROUND_COLUMN)]
        for e in mixed:
            L.append(f"  {e.id} is drawn from the CAD background, not from {lv.struct.sheet}"
                     + (f"; per answered RFI {e.attrs['rfi']}" if e.attrs.get("rfi") else "") + ".")

    _h(L, "HIDDEN GEOMETRY (THE SIGHTLINE)")
    for s in rr.sight:
        rec = s["record"]
        c = rec.counts()
        L.append(f"{s['sheet']} ({s['file']} page {s['page']}): {c['judged']} elements judged at "
                 f"{rec.dpi} dpi; {c['dropped']} dropped as not on the plotted sheet, "
                 f"{c['clipped']} replaced by the visible part, {c['obscured']} under "
                 f"annotation ink and kept whole, {c['unreadable']} could not be judged and "
                 f"were kept, {c['unread_regions']} fill region(s) seen but not read.")
        for d in rec.dropped[:12]:
            L.append(f"    dropped {d['what']} at page box {d['bbox']}: {pt.plain(d.get('why', ''))}")
        if len(rec.dropped) > 12:
            L.append(f"    and {len(rec.dropped) - 12} more dropped")
        for d in rec.clipped[:8]:
            L.append(f"    replaced {d['what']} at page box {d['bbox']}: {pt.plain(d.get('why', ''))}")
        for d in rec.unreadable[:8]:
            L.append(f"    not judged: {d['what']} at page box {d['bbox']}: {pt.plain(d.get('why', ''))}")
        for n in rec.notes:
            L.append(f"    {pt.plain(n)}")

    quotes = run.note_statements or []
    if quotes:
        _h(L, "NOTES THAT GOVERN LAYOUT")
        L.extend("  " + q for q in quotes)

    if run.points:
        _h(L, "POINTS")
        for code, lp in run.points.items():
            if not lp.rows:
                continue
            by = {}
            for r in lp.rows:
                by[r.layer] = by.get(r.layer, 0) + 1
            L.append(f"{code}: {len(lp.rows)} point(s): "
                     + ", ".join(f"{n} on {k}" for k, n in sorted(by.items())) + ".")
            if lp.merged:
                L.append(f"  {lp.merged} proposal(s) fell on a point already there and were "
                         "joined to it.")
            for eid, why in lp.withheld:
                L.append(f"  Not laid out: {eid}: {why}.")
    if run.plate_manifest:
        _h(L, "PLATES")
        if run.plate_manifest.get("delivered") is False:
            L.append("  The plates output is switched off. The plates below were built for "
                     "the self-check and were not delivered.")
        if not run.opts.out_legend and not run.opts.proof_only:
            L.append("  The legend output is switched off. The LEGEND plate was not built.")
        if "concrete" not in (run.opts.hats or []) and not run.opts.proof_only:
            L.append("  The Concrete hat is off: " + M.CONCRETE_HAT_ONLY + ".")
        for pg in run.plate_manifest["pages"]:
            n = pg["notes"]
            t = pg["tags"]
            tail = ""
            if n["asked"] or t["asked"]:
                tail = (f": {n['placed']} of {n['asked']} note block(s) and {t['placed']} of "
                        f"{t['asked']} tag(s) placed on the plan; "
                        f"{n['list_only'] + t['list_only']} in the list only")
            L.append(f"  {pg['number']} {pg['title']}{tail}.")
            for w in pg.get("warnings", []):
                L.append(f"    {pt.plain(w)}")
        for number, n in run.plate_manifest.get("unfit", []):
            L.append(f"  {number}: {n} statement(s) did not fit on the plate; they are in "
                     "this report.")
    if run.surveyor:
        _h(L, "SURVEYOR HAT")
        L.extend("  " + ln for ln in run.surveyor)

    _h(L, "SELF-CHECK")
    for c in run.checks:
        if c.get("skipped"):
            L.append(f"  not run: {c['check']} ({c['skipped']})")
            continue
        counts = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in c["counts"].items())
        L.append(f"  {'held' if c['ok'] else 'FAILED'}: {c['check']}" + (f" ({counts})" if counts else ""))
        for p in c["problems"]:
            L.append(f"    {p}")
    if not run.checks:
        L.append("  The self-check did not run.")
    return L


def to_text(lines) -> str:
    return "\n".join(lines) + "\n"


def write_text(lines, path: str) -> None:
    atomic_write_bytes(to_text(lines).encode("utf-8"), path)


def write_pdf(lines, path: str) -> None:
    """A plain paginated PDF of the report (letter, metadata-free)."""
    from ..minipdf import canvas as _canvas
    from ..minipdf.metrics import string_width
    W, H, mx, my = 612.0, 792.0, 54.0, 54.0
    buf = io.BytesIO()
    c = _canvas.Canvas(buf, pagesize=(W, H))
    y = H - my

    def wrap(text, font, size, width):
        words = text.split(" ")
        out, cur = [], ""
        for w in words:
            trial = (cur + " " + w) if cur else w
            if string_width(trial, font, size) <= width or not cur:
                cur = trial
            else:
                out.append(cur)
                cur = w
        out.append(cur)
        return out

    for raw in lines:
        text = pt.plain(raw)
        head = text.startswith(HEAD + " ")
        if head:
            text = text[len(HEAD) + 1:]
        font, size = ("Helvetica-Bold", 10.0) if head or raw == lines[0] else ("Helvetica", 8.5)
        indent = (len(text) - len(text.lstrip(" "))) * 4.0
        body = text.strip()
        if not body:
            y -= 6.0
            continue
        if head:
            y -= 6.0
        for ln in wrap(body, font, size, W - 2 * mx - indent):
            if y < my + size:
                c.showPage()
                y = H - my
            c.setFont(font, size)
            c.drawString(mx + indent, y - size, "".join(ch if ord(ch) < 256 else "?" for ch in ln))
            y -= size + 3.0
    c.showPage()
    c.save()
    atomic_write_bytes(buf.getvalue(), path)


def items_csv(reg, rr, path: str) -> None:
    """The open items register as a spreadsheet-ready CSV."""
    import csv
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["Item", "Level", "Category", "Label", "What is wrong", "What is needed",
                "Status", "RFI", "Sheets", "Elements", "Proof records"])
    for t in reg.items():
        w.writerow([t.ref, t.level, t.category_label, t.label, t.wrong, t.needed,
                    t.status_text(), t.rfi, " ".join(t.sheets()), " ".join(t.elements),
                    " ".join(t.proofs)])
    atomic_write_bytes(buf.getvalue().encode("utf-8"), path)
