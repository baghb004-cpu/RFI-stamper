"""The Swatchbook — plumbing cut-sheet submittal builder.

A tailor's swatchbook is the bound book of cloth samples handed to the
client for approval; a submittal package is exactly that — manufacturer
cut sheets for every scheduled component, bound per fixture tag and sent
for the architect's sign-off.  The Swatchbook builds those packets: one
stamped PDF per fixture tag, components merged in spec-paragraph order,
the tag stamped top-right on EVERY page, named per the office 0-49
numbering standard, plus a build log that documents every gap and
substitution (a partial package must never look like a full one).

The submittal standards here are an APPROVED deliverable format (verified
against an accepted reference package) — do not improvise on them; the
stamp geometry, naming and merge-order rules get submittals rejected when
deviated from.  The stamp is its own approved standard, deliberately close
kin to (but distinct from) the RFI note-box law in ``layout.py``.

Fully offline, like everything else in Planloom: components resolve
against the bundled component library plus user-imported sheets.  The
upstream spec sketches an optional online fetch module — it is
deliberately NOT built (offline invariant #1 outranks an optional,
default-off module); the manifest's ``source_url`` fields are provenance
data for sourcing sheets OUTSIDE the app, and missing products surface as
"request from rep / import manually".
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
from dataclasses import dataclass, field

import fitz

from .fsutil import atomic_write_bytes

# ---- the office numbering standard: category -> two-digit prefix ---------- #
CATEGORIES = {
    0: "Medical gas",             17: "Sand oil interceptor",
    1: "Water closets",           18: "Grease interceptor",
    2: "Urinals",                 19: "Downspout nozzle",
    3: "Lavatories",              20: "Expansion tank",
    4: "Sinks",                   21: "Hose bibbs",
    5: "Wash fountains",          22: "Recessed hose bibbs",
    6: "Trap primers",            23: "Gas pressure regulator",
    7: "Water hammer arrestor",   24: "Seismic gas valve",
    8: "Floor drains",            25: "Can wash",
    9: "Area drains",             26: "Gas cock",
    10: "Roof overflow drains",   27: "Solenoid valve",
    11: "Showers",                28: "Control station",
    12: "Floor sinks",            29: "Pressure reducing valve",
    13: "Roof receptor",          30: "Backflow preventer",
    14: "Drinking fountains",     31: "Emergency shower",
    15: "Electric water cooler",  32: "Emergency eye wash",
    16: "Washing box",            33: "Emergency eye spray",
    34: "Mixing valve",           42: "Soft water",
    35: "Acid neutralizer tank",  43: "RO water",
    36: "Septic system components", 44: "DI water",
    37: "Back water valve",       45: "Bottle filler",
    38: "Water heaters",          46: "Air compressor",
    39: "Fuel storage tanks",     47: "Above ground fuel tank",
    40: "Ice maker box",          48: "Gutter drains",
    41: "Circ pump",              49: "Hose reel",
}

# ---- the approved stamp (NON-NEGOTIABLE — deviations get rejected) -------- #
STAMP_RED = (0.80, 0.05, 0.05)
STAMP_FS = 10.5
STAMP_H = 16.0
STAMP_MARGIN = 10.0
STAMP_MIN_W = 40.0
STAMP_LINE_W = 0.9

#: T6 heuristic: a fixture-tag-shaped token already sitting in the
#: top-right corner region of page 1 means the sheet was stamped before.
#: The hyphen is load-bearing — model numbers legally live in that corner
#: on clean sheets, and only the hyphenated tag form is ours.
_TAG_RE = re.compile(r"\b[A-Z]{1,4}-\d+\b")
_CLEAN_REGION_W, _CLEAN_REGION_H = 150.0, 40.0


def canonical_tag(tag: str) -> str:
    """The approved hyphenated tag form: ``WC1`` -> ``WC-1`` (the standard
    hyphenates even where a schedule renders it unhyphenated — and only
    hyphenated tags are visible to the never-restamp guard)."""
    t = tag.strip().upper()
    return re.sub(r"^([A-Z]{1,4})(\d+)$", r"\1-\2", t)


def stamp(page, tag: str) -> None:
    """Thin red-outlined, white-filled rectangle w/ red bold tag, top-right.

    Exact approved geometry: 10 pt corner margins, 16 pt tall, width
    ``max(text_width + 12, 40)``, 0.9 pt outline in RGB(0.80, 0.05, 0.05),
    Helvetica-Bold 10.5 pt tag centered.  White fill is opaque by design —
    minor overlap of a manufacturer's corner header is accepted.
    ``insert_text`` embeds real extractable text (the acceptance tests
    read it back).
    """
    tw = fitz.get_text_length(tag, fontname="Helvetica-Bold",
                              fontsize=STAMP_FS)
    w = max(tw + 12, STAMP_MIN_W)
    r = fitz.Rect(page.rect.x1 - STAMP_MARGIN - w,
                  page.rect.y0 + STAMP_MARGIN,
                  page.rect.x1 - STAMP_MARGIN,
                  page.rect.y0 + STAMP_MARGIN + STAMP_H)
    page.draw_rect(r, color=STAMP_RED, fill=(1, 1, 1), width=STAMP_LINE_W)
    ty = r.y0 + (STAMP_H + STAMP_FS * 0.72) / 2 - 1
    page.insert_text((r.x0 + (w - tw) / 2, ty), tag,
                     fontname="Helvetica-Bold", fontsize=STAMP_FS,
                     color=STAMP_RED)


def looks_stamped(path: str) -> str | None:
    """The never-restamp guard: the tag found in page 1's VISUAL top-right
    corner region, or None for a clean sheet.  Double-stamping is a
    rejection — a previously stamped PDF must never be used as a component.

    On a /Rotate page, text extraction reports UNROTATED media
    coordinates, so the visual corner region is ALSO checked through the
    page's derotation matrix — a foreign stamp written in media coords on
    a rotated page still renders visual top-right and must still refuse.
    """
    with fitz.open(path) as doc:
        if not doc.page_count:
            return None
        pg = doc[0]
        region = fitz.Rect(pg.rect.x1 - _CLEAN_REGION_W, pg.rect.y0,
                           pg.rect.x1, pg.rect.y0 + _CLEAN_REGION_H)
        text = pg.get_text(clip=region)
        if pg.rotation:
            media = (region * pg.derotation_matrix).normalize()
            text += pg.get_text(clip=media)
        m = _TAG_RE.search(text)
        return m.group(0) if m else None


def build_packet(out_path: str, tag: str, component_paths: list,
                 page_ranges: dict | None = None,
                 check_clean: bool = True, chalk: str = "off",
                 chalk_models: list | None = None,
                 chalk_log: list | None = None) -> int:
    """Merge ``component_paths`` (spec-paragraph order) and stamp every page.

    Entries are paths, or ``(path, (start, end))`` tuples for per-
    OCCURRENCE page ranges — a booklet scheduled twice with different
    models needs two different ranges, which a path-keyed dict cannot
    express.  ``page_ranges``: optional ``{path: (start, end)}`` 1-based
    inclusive (the original surface; tuple entries win).  Returns the page
    count; writes atomically.

    Each source page is re-rendered onto a fresh unrotated page via
    ``show_pdf_page``: several manufacturer sheets carry /Rotate 90 or 270,
    and the page's fitz rect is the VISUAL (rotated) rect — so a plain
    top-right stamp lands in the visual top-right with zero rotation math.
    Never stamp in-place on rotated pages.  Mixed page sizes are normal —
    each source page keeps its own size, never normalized.

    Annotation and form-widget appearances are BAKED into the page content
    first: ``show_pdf_page`` embeds only the content stream, and many
    manufacturer sheets are fillable forms whose checkboxes exist ONLY as
    widget annotations — without the bake those checkboxes silently vanish
    from the delivered packet.  Interactivity is dropped (correct for a
    submittal); the visual sheet ships complete.
    """
    spans = []
    for item in component_paths:
        if isinstance(item, (tuple, list)):
            path, rng = item[0], (tuple(item[1]) if item[1] else None)
        else:
            path, rng = item, (page_ranges or {}).get(item)
        spans.append((path, rng))
    if check_clean:
        for path in dict.fromkeys(p for p, _ in spans):
            found = looks_stamped(path)
            if found:
                raise ValueError(
                    f"{os.path.basename(path)} already carries a tag stamp "
                    f"({found}) — source a clean manufacturer sheet instead "
                    "(double-stamping is a rejection)")
    out = fitz.open()
    offsets = []                       # (first_out_page, n_pages) per span
    for path, rng in spans:
        src = fitz.open(path)
        src.bake(annots=True, widgets=True)
        pages = range(rng[0] - 1, rng[1]) if rng else range(src.page_count)
        offsets.append((out.page_count, len(pages)))
        for pno in pages:
            sp = src[pno]
            np_ = out.new_page(width=sp.rect.width, height=sp.rect.height)
            np_.show_pdf_page(np_.rect, src, pno)
        src.close()
    # the Chalk Mark runs on the ASSEMBLED pages (final visual space —
    # extraction sees through the embedded pages), BEFORE the tag stamps
    if chalk in ("report", "mark") and chalk_models:
        for (p0, n), cm in zip(offsets, chalk_models):
            if not cm:
                continue
            comp_id, models = cm
            for e in _chalk_component(out, p0, p0 + n, comp_id, models,
                                      mark=(chalk == "mark")):
                e["packet"] = os.path.basename(out_path)
                if chalk_log is not None:
                    chalk_log.append(e)
    for page in out:
        stamp(page, tag)
    n = out.page_count
    data = out.tobytes(garbage=3, deflate=True)
    out.close()
    # deliver Planloom-clean bytes: the renderer stamps a /Producer and a
    # random /ID into its output — one pass through the Shuttle drops /Info
    # structurally and makes the bytes deterministic (content-hash /ID),
    # the same NDA posture as every merge/stamp deliverable
    from .minipdf.io import Reader, Writer
    r = Reader(io.BytesIO(data))
    w = Writer()
    for pg in r.pages:
        w.add_page(pg)
    buf = io.BytesIO()
    w.write(buf)
    atomic_write_bytes(buf.getvalue(), out_path)
    return n


def packet_filename(prefix: int, tag: str) -> str:
    return f"{int(prefix):02d}-{tag}.pdf"


# --------------------------------------------------------------------------- #
#  The Chalk Mark — model-number checkbox marking (SETSCAN Phase 4)           #
# --------------------------------------------------------------------------- #
# A tailor's chalk mark tells the shop exactly where to cut.  During a
# packet build the specified model number is searched on its OWN
# component's pages and the empty checkbox in that row is marked with a
# red X.  This marks a LEGAL SUBMITTAL, so the certainty contract is the
# strictest in the module: mark only when the model matches exactly one
# checkbox row, that row holds exactly one visual box, and the box is
# pixel-EMPTY — everything else is skipped into the build log.  Modes:
# "off" (byte-identical to pre-chalk builds), "report" (detect + log,
# draw nothing), "mark".

_BOX_MIN, _BOX_MAX = 3.5, 15.0      # visual checkbox side, in points
_BOX_INSET = 0.18                   # X inset from the box border


def _visual_boxes(page) -> list:
    """Small near-square vector candidates, merged into VISUAL boxes —
    one drawn checkbox is routinely several overlapping paths."""
    cands = []
    for d in page.get_drawings():
        r = d["rect"]
        if (_BOX_MIN <= r.width <= _BOX_MAX
                and _BOX_MIN <= r.height <= _BOX_MAX
                and abs(r.width - r.height) <= 2.5):
            cands.append(fitz.Rect(r))
    merged: list = []
    for r in sorted(cands, key=lambda r: (r.y0, r.x0)):
        for m in merged:
            if m.intersects(r):
                m.include_rect(r)
                break
        else:
            merged.append(fitz.Rect(r))
    for _ in range(3):                       # close transitive overlaps
        out: list = []
        for r in merged:
            for m in out:
                if m.intersects(r):
                    m.include_rect(r)
                    break
            else:
                out.append(r)
        if len(out) == len(merged):
            break
        merged = out
    return [m for m in merged
            if m.width <= _BOX_MAX and m.height <= _BOX_MAX]


def _model_spans(page, models: list) -> list:
    """Word-exact (normalized) matches of any model string -> [rects].

    Joins up to three adjacent words on a line so "CX 300" finds
    "CX-300"; matching is EXACT on the normalized form — substring
    matching would let Z100 swallow Z1000 (the search_for gotcha)."""
    targets = {_norm(str(m)) for m in models if _norm(str(m))}
    if not targets:
        return []
    lines: dict = {}
    for w in page.get_text("words"):
        lines.setdefault((w[5], w[6]), []).append(w)
    spans = []
    for ws in lines.values():
        ws.sort(key=lambda w: w[7])
        for i in range(len(ws)):
            joined = ""
            rect = fitz.Rect(ws[i][:4])
            for k in range(i, min(i + 3, len(ws))):
                joined += _norm(ws[k][4])
                rect.include_rect(fitz.Rect(ws[k][:4]))
                if joined in targets:
                    spans.append(fitz.Rect(rect))
                    break
                if len(joined) > max(len(t) for t in targets):
                    break
    return spans


def _box_is_empty(page, box, dpi: int = 150) -> bool:
    """No ink strictly inside the box (border excluded) — a pre-checked
    box is never marked again (idempotence, and honesty about sheets
    that arrive with factory-checked options)."""
    inner = fitz.Rect(box)
    dx, dy = box.width * 0.28, box.height * 0.28
    inner.x0 += dx
    inner.x1 -= dx
    inner.y0 += dy
    inner.y1 -= dy
    if inner.is_empty:
        return False
    pix = page.get_pixmap(clip=inner, dpi=dpi, colorspace=fitz.csGRAY,
                          alpha=False)
    import numpy as _np
    a = _np.frombuffer(pix.samples, _np.uint8)
    return bool(a.size == 0 or a.min() > 200)


def _chalk_component(doc, p0: int, p1: int, comp_id: str, models: list,
                     mark: bool) -> list:
    """Run the Chalk Mark gates over one component's pages [p0, p1).

    Returns build-log entries; draws the X only when ``mark`` and every
    gate passes.  Gate order: occurrences WITHOUT a box in their row band
    (titles, footers) are ignored; among checkbox rows there must be
    exactly ONE, holding exactly ONE visual box, and it must be empty.
    """
    entries: list = []
    rows = []                           # (pno, span_rect, [boxes])
    found_text = False
    for pno in range(p0, p1):
        page = doc[pno]
        spans = _model_spans(page, models)
        if not spans:
            continue
        found_text = True
        boxes = _visual_boxes(page)
        for sr in spans:
            # row membership = box vertical CENTER in the text band; mere
            # rect intersection grazes the next option row's box (checkbox
            # columns stack ~10 pt apart) and fabricates 2-box refusals
            inband = [b for b in boxes
                      if sr.y0 - 2 <= (b.y0 + b.y1) / 2 <= sr.y1 + 2
                      and not b.intersects(sr)]
            if inband:
                rows.append((pno, sr, inband))

    def entry(action, reason, pno=None):
        entries.append({"component": comp_id, "page": (pno + 1) if
                        pno is not None else None, "action": action,
                        "reason": reason})

    if not rows:
        entry("skip", "model text not found on the sheet" if not found_text
              else "model found, but no checkbox row — nothing to mark")
        return entries
    if len(rows) > 1:
        entry("skip", f"model sits in {len(rows)} checkbox rows — marked "
                      "none; check by hand", rows[0][0])
        return entries
    pno, sr, inband = rows[0]
    if len(inband) > 1:
        entry("skip", f"{len(inband)} boxes in the model's row — marked "
                      "none; check by hand", pno)
        return entries
    box = inband[0]
    if not _box_is_empty(doc[pno], box):
        entry("skip", "box already carries a mark — left alone", pno)
        return entries
    if mark:
        page = doc[pno]
        dx, dy = box.width * _BOX_INSET, box.height * _BOX_INSET
        a = fitz.Point(box.x0 + dx, box.y0 + dy)
        b = fitz.Point(box.x1 - dx, box.y1 - dy)
        c = fitz.Point(box.x0 + dx, box.y1 - dy)
        d = fitz.Point(box.x1 - dx, box.y0 + dy)
        page.draw_line(a, b, color=STAMP_RED, width=STAMP_LINE_W)
        page.draw_line(c, d, color=STAMP_RED, width=STAMP_LINE_W)
        entry("marked", f"X in the box at ({box.x0:.0f}, {box.y0:.0f})",
              pno)
    else:
        entry("would-mark", f"single empty box at ({box.x0:.0f}, "
                            f"{box.y0:.0f})", pno)
    return entries


def model_strings(c) -> list:
    """The searchable model designations for a component: id + alias base
    forms (a substitution alias contributes only its model part)."""
    out = [c.id]
    for a in c.aliases:
        m = re.match(r"(.+?)\s*\(.*\)", str(a))
        out.append(m.group(1).strip() if m else str(a))
    return out


# --------------------------------------------------------------------------- #
#  The offline component library                                              #
# --------------------------------------------------------------------------- #

def _norm(s: str) -> str:
    """Callout normalization: case-insensitive, hyphen/space/period blind
    (``45LKABCP`` = ``45-LKABCP``, ``1119.14`` = ``1119_14``)."""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def bundled_kit_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "data", "cutsheet_library")


def ensure_user_library(user_dir: str | None = None,
                        src: str | None = None) -> str:
    """Install/SYNC the bundled kit into the user data dir; return the root.

    First run copies everything.  Later runs still SYNC: any bundled seed
    sheet or manifest entry missing from the user library is added —
    the seed kit legitimately lands (and grows) AFTER a user dir already
    exists, and a first-run-only gate would strand that user on an empty
    library forever.  Manual imports are never touched (append-only merge
    by component id); nothing is ever overwritten.
    """
    root = user_dir or os.path.join(os.path.expanduser("~"), ".planloom",
                                    "cutsheet_library")
    src = src or bundled_kit_dir()
    os.makedirs(os.path.join(root, "seed_library"), exist_ok=True)
    for name in ("manifest.json", "recipes_reference.json"):
        p = os.path.join(src, name)
        if os.path.exists(p) and not os.path.exists(os.path.join(root, name)):
            shutil.copy2(p, os.path.join(root, name))
    seeds = os.path.join(src, "seed_library")
    if os.path.isdir(seeds):
        for f in sorted(os.listdir(seeds)):
            dst = os.path.join(root, "seed_library", f)
            if not os.path.exists(dst):
                shutil.copy2(os.path.join(seeds, f), dst)
    _merge_new_seed_entries(src, root)
    return root


def _merge_new_seed_entries(src: str, root: str) -> None:
    """Append bundled manifest components the user manifest lacks (an
    updated kit adds sheets after approval); user entries never change."""
    try:
        with open(os.path.join(src, "manifest.json"), encoding="utf-8") as fh:
            bundled = json.load(fh)
        with open(os.path.join(root, "manifest.json"),
                  encoding="utf-8") as fh:
            user = json.load(fh)
    except (OSError, ValueError):
        return
    have = {c.get("id") for c in user.get("components", [])}
    new = [c for c in bundled.get("components", [])
           if c.get("id") not in have]
    if not new:
        return
    user.setdefault("components", []).extend(new)
    atomic_write_bytes(json.dumps(user, indent=2).encode("utf-8"),
                       os.path.join(root, "manifest.json"))


@dataclass
class Component:
    id: str
    manufacturer: str = ""
    aliases: list = field(default_factory=list)
    file: str = ""                  # relative to the library root
    pages: int = 0
    sha256: str = ""
    source_url: str = ""            # provenance only — never fetched by the app
    fetched: str = ""
    notes: str = ""
    source: str = "seed"            # "seed" | "manual_import"


class Library:
    """The manifest-indexed cut-sheet library (bundled seeds + imports).

    sha256 is verified for every present file at load; a mismatch means a
    corrupted or replaced sheet — the entry is refused (excluded from
    resolution) and surfaced in ``issues``.  Entries whose files are not
    installed yet (the seed kit arrives separately) resolve as gaps.
    """

    def __init__(self, root: str | None = None):
        self.root = root or bundled_kit_dir()
        self.components: list = []
        self.wanted: list = []
        self.issues: list = []
        self._ok: dict = {}             # id -> file verified present + intact
        self._load()

    def _manifest_path(self) -> str:
        return os.path.join(self.root, "manifest.json")

    def _load(self):
        try:
            with open(self._manifest_path(), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            self.issues.append(f"manifest unreadable: {e}")
            return
        blank = Component(id="")
        for raw in data.get("components", []):
            # explicit JSON nulls fall back to defaults too — one entry
            # with "aliases": null must never poison resolution
            kw = {}
            for k in Component.__dataclass_fields__:
                v = raw.get(k)
                kw[k] = getattr(blank, k) if v is None else v
            c = Component(**kw)
            self.components.append(c)
            path = self.path_of(c)
            if not os.path.exists(path):
                self._ok[c.id] = False
                self.issues.append(
                    f"{c.id}: sheet not installed ({c.file})")
                continue
            digest = _sha256(path)
            if c.sha256 and digest != c.sha256:
                self._ok[c.id] = False
                self.issues.append(
                    f"{c.id}: sha256 mismatch — corrupted or replaced "
                    "file; refusing to use it")
            else:
                self._ok[c.id] = True
        self.wanted = list(data.get("wanted", []))

    def path_of(self, c: Component) -> str:
        return os.path.join(self.root, c.file)

    def get(self, comp_id: str):
        for c in self.components:
            if c.id == comp_id:
                return c
        return None

    def usable(self, c: Component) -> bool:
        return bool(self._ok.get(c.id))

    def resolve(self, callout: str):
        """Schedule callout -> usable Component, else None (a GAP)."""
        return self.resolve_ex(callout)[0]

    def resolve_ex(self, callout: str) -> tuple:
        """Schedule callout -> ``(Component | None, substitution_note | None)``.

        Alias-aware and punctuation/case tolerant.  Exact normalized match
        against id / alias / "manufacturer alias" (each manufacturer word
        of a multi-brand string counts) wins; a suffixed callout that
        EXTENDS exactly one known alias also resolves (series sheets cover
        suffixed models).  Anything ambiguous is a gap — never a silent
        substitute.  An alias marked ``(DISCONTINUED - substitution)``
        matches its model EXACTLY only, and the match carries a LOUD
        engineer-confirm note — resolving a dead model to its nearest
        current sheet is a substitution, never a plain match.
        """
        q = _norm(callout)
        if not q:
            return None, None
        exact, prefixed = [], []
        for c in self.components:
            if not self.usable(c):
                continue
            makers = [c.manufacturer] + re.split(
                r"\s*[/,]\s*|\s+&\s+", c.manufacturer or "")
            keys = []                    # (normalized key, substitution note)
            for a in [c.id] + list(c.aliases):
                m = re.match(r"(.+?)\s*\(.*discontinued.*\)", str(a),
                             re.IGNORECASE)
                note = (f"SUBSTITUTION: {str(a).strip()} -> {c.id} — "
                        "engineer must confirm before submitting"
                        ) if m else None
                base = m.group(1) if m else str(a)
                keys.append((_norm(base), note))
                for mk in makers:
                    if mk:
                        keys.append((_norm(f"{mk} {base}"), note))
            for k, note in keys:
                if not k:
                    continue
                if q == k:
                    exact.append((c, note))
                elif note is None and (q.startswith(k) or k.startswith(q)) \
                        and min(len(q), len(k)) >= 4:
                    prefixed.append((c, None))
        if exact:
            ids = {c.id for c, _ in exact}
            if len(ids) != 1:
                return None, None
            # a plain exact match outranks a substitution alias hit on the
            # same component — only a pure substitution match carries the note
            if any(n is None for _, n in exact):
                return exact[0][0], None
            return exact[0][0], exact[0][1]
        uniq = {c.id: (c, n) for c, n in prefixed}
        return next(iter(uniq.values())) if len(uniq) == 1 else (None, None)

    def verify(self) -> list:
        """Full sha256 sweep -> list of issue strings (empty = healthy)."""
        issues = []
        for c in self.components:
            path = self.path_of(c)
            if not os.path.exists(path):
                issues.append(f"{c.id}: missing file {c.file}")
            elif c.sha256 and _sha256(path) != c.sha256:
                issues.append(f"{c.id}: sha256 mismatch")
        return issues

    def import_pdf(self, path: str, comp_id: str, manufacturer: str = "",
                   aliases: list | None = None, notes: str = "") -> Component:
        """Manual import (the rep-request path): clean-sheet check, copy
        into the library, hash, append to the manifest."""
        if any("manifest unreadable" in i for i in self.issues):
            # importing over a manifest that failed to LOAD would rewrite
            # it from the empty in-memory list — refuse instead of
            # clobbering the on-disk file
            raise ValueError("the library manifest is unreadable — fix it "
                             "before importing sheets")
        found = looks_stamped(path)
        if found:
            raise ValueError(
                f"{os.path.basename(path)} already carries a tag stamp "
                f"({found}) — request a clean sheet (double-stamping is a "
                "rejection)")
        if self.get(comp_id) is not None:
            raise ValueError(f"component id {comp_id!r} already exists")
        os.makedirs(os.path.join(self.root, "seed_library"), exist_ok=True)
        rel = os.path.join("seed_library", f"{comp_id}.pdf")
        dst = os.path.join(self.root, rel)
        shutil.copy2(path, dst)
        with fitz.open(dst) as doc:
            pages = doc.page_count
        c = Component(id=comp_id, manufacturer=manufacturer,
                      aliases=list(aliases or []), file=rel, pages=pages,
                      sha256=_sha256(dst), notes=notes,
                      source="manual_import")
        self.components.append(c)
        self._ok[c.id] = True
        self._save_manifest()
        return c

    def _save_manifest(self):
        try:
            with open(self._manifest_path(), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        data["components"] = [
            {k: getattr(c, k) for k in Component.__dataclass_fields__}
            for c in self.components]
        data.setdefault("wanted", self.wanted)
        atomic_write_bytes(
            json.dumps(data, indent=2).encode("utf-8"),
            self._manifest_path())


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
#  Recipes + the build                                                        #
# --------------------------------------------------------------------------- #

def load_recipes(path: str | None = None) -> dict:
    """The recipe file: packets (ordered component ids + gaps + flags),
    gap_fillers, not_built.  Defaults to the bundled reference project —
    the acceptance baseline."""
    p = path or os.path.join(bundled_kit_dir(), "recipes_reference.json")
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def build_all(recipes: dict, library: Library, out_dir: str,
              gap_fillers: bool = True, log=print,
              chalk: str = "off") -> dict:
    """Build every packet in the recipe file into ``out_dir``.

    Returns ``{"built": [(filename, pages)], "gapped": {...}, "skipped":
    [...], "flags": [...], "log_path": ...}``.  A missing component never
    blocks its packet — the gap is recorded with its insertion position and
    the packet builds with what exists.  ``gap_fillers=True`` (production)
    applies the recipe's gap_fillers; the acceptance rebuild runs WITHOUT
    them so page counts match the approved golden set.
    """
    os.makedirs(out_dir, exist_ok=True)
    fillers = {}
    if gap_fillers:
        for gf in recipes.get("gap_fillers", []):
            fillers.setdefault(gf["packet"], []).append(gf)

    built, gapped, skipped, flags = [], {}, [], []
    chalk_entries: list = []
    packets = sorted(recipes.get("packets", []),
                     key=lambda p: (int(p["prefix"]), p["tag"]))
    for pk in packets:
        comp_ids = list(pk["components"])

        def _cid(entry):                      # entries: id or {"id":..,
            return entry["id"] if isinstance(entry, dict) else entry

        gaps = list(pk.get("missing", []))
        for gf in fillers.get(pk["filename"], []):
            ids = [_cid(e) for e in comp_ids]
            if gf["component"] in ids:
                continue                      # already filled by hand
            try:
                at = ids.index(gf["insert_after"]) + 1
            except ValueError:
                at = len(comp_ids)
                flags.append(
                    f"{pk['tag']}: gap filler {gf['component']} — "
                    f"insert_after {gf['insert_after']!r} not in the "
                    "recipe; APPENDED AT END — check the merge order")
            comp_ids.insert(at, gf["component"])
            fills = gf.get("fills", "")
            if fills:                         # the gap is filled: clear its
                keep = [g for g in gaps       # note, say so loudly
                        if _norm(fills) not in _norm(g)]
                if len(keep) != len(gaps):
                    gaps = keep
                flags.append(f"{pk['tag']}: gap '{fills}' filled by "
                             f"{gf['component']} at its recorded position "
                             "(packet exceeds the originally approved "
                             "page count — expected and correct)")
        spans = []                            # (path, range|None) per
        span_models = []                      # OCCURRENCE — a booklet may
        for entry in comp_ids:                # appear twice w/ two ranges
            rng = None
            if isinstance(entry, dict):
                rng = entry.get("page_range")
                if rng:
                    rng = (int(rng[0]), int(rng[1]))
            cid = _cid(entry)
            c = library.get(cid)
            if c is None or not library.usable(c):
                gaps.append(f"{cid} (library entry unusable or missing)")
                continue
            spans.append((library.path_of(c), rng))
            span_models.append((cid, model_strings(c)))
        if not spans:
            skipped.append((pk["filename"],
                            "no usable components — " + "; ".join(gaps)))
            log(f"  !! {pk['filename']}: nothing to build")
            continue
        out_path = os.path.join(out_dir, pk["filename"])
        n = build_packet(out_path, pk["tag"], spans, chalk=chalk,
                         chalk_models=span_models, chalk_log=chalk_entries)
        built.append((pk["filename"], n))
        if gaps:
            gapped[pk["filename"]] = gaps
        for fl in pk.get("flags", []):
            flags.append(f"{pk['tag']}: {fl}")
        log(f"  + {pk['filename']} ({n} page(s))"
            + (f"  [{len(gaps)} gap(s)]" if gaps else ""))

    log_path = os.path.join(out_dir, "00-BUILD-LOG.md")
    atomic_write_bytes(_build_log_md(
        recipes, built, gapped, skipped, flags,
        chalk=(chalk, chalk_entries)).encode("utf-8"), log_path)
    return {"built": built, "gapped": gapped, "skipped": skipped,
            "flags": flags, "chalk": chalk_entries, "log_path": log_path}


def _build_log_md(recipes, built, gapped, skipped, flags,
                  chalk=("off", ())) -> str:
    """00-BUILD-LOG.md — complete packets, gapped packets, not-built tags
    with reasons, engineer flags (the approved log format)."""
    lines = [f"# {recipes.get('project', 'Cut sheet submittal')}",
             "## Plumbing Cut Sheet Submittal - Build Log", ""]
    if recipes.get("plan_set"):
        lines += [f"Plan set: {recipes['plan_set']}"]
    lines += ["Standard: office submittal standard (0-49 numbering, "
              "red-outline tag stamp top-right, every page)", ""]
    complete = [f for f, _ in built if f not in gapped]
    lines += ["## Delivered - complete packets",
              ", ".join(f[:-4] for f in complete) or "(none)", ""]
    lines += ["## Delivered - packets with a gap"]
    if gapped:
        for f, gaps in gapped.items():
            for g in gaps:
                lines.append(f"- {f[:-4]}: missing {g}")
    else:
        lines.append("(none)")
    lines += ["", "## Not built"]
    entries = list(skipped) + [
        (f"{nb.get('prefix', 0):02d}-{nb['tag']}", nb.get("reason", ""))
        for nb in recipes.get("not_built", [])]
    if entries:
        for name, reason in entries:
            lines.append(f"- {name}: {reason}")
    else:
        lines.append("(none)")
    lines += ["", "## Engineer flags"]
    if flags:
        for i, fl in enumerate(flags, 1):
            lines.append(f"{i}. {fl}")
    else:
        lines.append("(none)")
    mode, entries = chalk
    if mode in ("report", "mark"):
        lines += ["", f"## Chalk marks ({mode} mode)"]
        if entries:
            for e in entries:
                where = (f"{e['packet']} p{e['page']}" if e.get("page")
                         else e["packet"])
                lines.append(f"- {where}: {e['component']} — "
                             f"{e['action']}: {e['reason']}")
        else:
            lines.append("(no model rows found)")
    return "\n".join(lines) + "\n"
