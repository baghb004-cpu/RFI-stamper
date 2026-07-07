"""End-to-end pipeline: parse RFIs -> map to sheets -> place -> stamp -> verify."""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field

import fitz

from . import core, layout
from .sheets import SheetIndex
from .stamp import stamp_pdf
from .verify import render_gray, verify


@dataclass
class MapRow:
    record: core.RFIRecord
    pages: list = field(default_factory=list)    # page numbers
    via: str = ""                                # planref / body / manual / unmatched


@dataclass
class Report:
    index: SheetIndex = None
    rows: list = field(default_factory=list)
    placements: dict = field(default_factory=dict)
    appendix: list = field(default_factory=list)
    verify_ok: bool = False
    verify_rows: list = field(default_factory=list)
    out_path: str = ""


# ------------------------------------------------------------------ scan ---

def scan(plan_path, rfi_paths, log=print):
    log("Indexing plan set \u2026")
    index = SheetIndex(plan_path, log=log)
    log("Reading RFI documents \u2026")
    records = core.parse_paths(rfi_paths, log=log)
    rows = []
    for r in records:
        pages, via = [], []
        for tok, v in r.refs:
            if v == "attachment":
                continue        # listed for review, never auto-mapped
            p = index.match(tok)
            if p and p not in pages:
                pages.append(p)
                via.append(v)
        rows.append(MapRow(record=r, pages=sorted(pages),
                           via=("planref" if "planref" in via else
                                "body" if via else "unmatched")))
    return index, rows


def attachment_sheets(index, record):
    """Sheet names for tokens found only in attachment/printout regions."""
    out = []
    for tok, v in record.refs:
        if v != "attachment":
            continue
        p = index.match(tok)
        if p:
            s = index.info(p).sheet
            if s not in out:
                out.append(s)
    return out


def rows_to_csv(index, rows, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rfi", "title", "sheets", "via", "answered", "attachment_refs"])
        for row in rows:
            sheets = ";".join(index.info(p).sheet for p in row.pages)
            w.writerow([row.record.number, row.record.title, sheets,
                        row.via, "yes" if row.record.has_answer else "no",
                        ";".join(attachment_sheets(index, row.record))])


def apply_csv(index, rows, path, log=print):
    """Override mapping from an edited CSV (column `sheets`, `;`-separated)."""
    edits = {}
    with open(path, newline="", encoding="utf-8") as f:
        for line in csv.DictReader(f):
            edits[line["rfi"].strip().zfill(3)] = line.get("sheets", "")
    for row in rows:
        if row.record.number not in edits:
            continue
        pages = []
        for tok in edits[row.record.number].replace(",", ";").split(";"):
            tok = tok.strip().upper()
            if not tok:
                continue
            p = index.match(tok)
            if p:
                pages.append(p)
            else:
                log(f"  !! CSV sheet '{tok}' not found in plan set (RFI {row.record.number})")
        row.pages, row.via = sorted(set(pages)), "manual"
    return rows


# ------------------------------------------------------------------- run ---

def run(plan_path, rfi_paths=None, out_path=None, rows=None, index=None,
        summarizer=None, dpi=90, log=print):
    if index is None or rows is None:
        index, rows = scan(plan_path, rfi_paths, log=log)
    out_path = out_path or os.path.splitext(plan_path)[0] + "_RFI_overlay.pdf"
    rep = Report(index=index, rows=rows, out_path=out_path)

    # group records per page, keep RFI-number order
    per_page = {}
    for row in rows:
        for p in row.pages:
            per_page.setdefault(p, []).append(row.record)
    unmatched = [r.record for r in rows if not r.pages]

    log("Placing note boxes \u2026")
    doc = fitz.open(plan_path)
    scale = dpi / 72.0
    for p in sorted(per_page):
        info = index.info(p)
        recs = sorted(per_page[p], key=lambda r: r.number)
        entries = layout.make_entries(recs, summarizer=summarizer)
        base_w = min(400.0, info.view_w * 0.30)
        max_h = info.view_h * 0.45
        gray = render_gray(doc, p, dpi)
        ii = layout.integral(gray)
        occupied = []
        boxes = []
        for chunk in layout.pack(entries, base_w, max_h):
            placed = None
            for w in (base_w, base_w * 0.85, base_w * 0.72):
                h, _ = layout.layout_entries(chunk, w)
                got = layout.find_spot(ii, gray.shape[1], gray.shape[0],
                                       w, h, scale, occupied)
                if got:
                    x, ytop, occ = got
                    occupied.append(occ)
                    boxes.append(dict(x=x, ytop=ytop, w=w, h=h,
                                      entries=chunk, occ=occ))
                    placed = True
                    break
            if not placed:
                rep.appendix.append((f"Sheet {info.sheet}", chunk))
                log(f"  !! {info.sheet}: no clear space for {len(chunk)} note(s) "
                    "\u2014 moved to appendix page")
        if boxes:
            rep.placements[p] = boxes
            log(f"  {info.sheet}: {len(boxes)} box(es), "
                f"{sum(len(b['entries']) for b in boxes)} RFI note(s)")
    doc.close()

    if unmatched:
        rep.appendix.append(("Unmatched RFIs (no sheet reference found \u2014 review)",
                             layout.make_entries(unmatched, summarizer=summarizer)))
        log(f"  {len(unmatched)} RFI(s) had no sheet match \u2014 listed on appendix page")

    log("Stamping \u2026")
    stamp_pdf(plan_path, out_path, rep.placements, index,
              appendix=rep.appendix or None)

    log("Verifying (pixel-diff every page) \u2026")
    rep.verify_ok, rep.verify_rows = verify(plan_path, out_path,
                                            rep.placements, index, dpi=dpi, log=log)
    write_report(rep, os.path.splitext(out_path)[0] + "_report.txt")
    log(("VERIFICATION PASSED \u2014 " if rep.verify_ok else
         "VERIFICATION **FAILED** \u2014 do not issue \u2014 ") + out_path)
    return rep


def write_report(rep: Report, path: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write("RFI STAMPER RUN REPORT\n======================\n\nMapping:\n")
        for row in rep.rows:
            sheets = ", ".join(rep.index.info(p).sheet for p in row.pages) or "(unmatched)"
            att = attachment_sheets(rep.index, row.record)
            f.write(f"  RFI {row.record.number}  [{row.via:9}] -> {sheets}"
                    f"   answered={'yes' if row.record.has_answer else 'no'}"
                    f"   {row.record.title}"
                    + (f"   [attachment-only refs: {', '.join(att)}]" if att else "")
                    + "\n")
        f.write("\nVerification:\n")
        for pno, st, msg in rep.verify_rows:
            f.write(f"  p{pno:02d} {st:4} {msg}\n")
        f.write(f"\nRESULT: {'PASS' if rep.verify_ok else 'FAIL'}\n")
