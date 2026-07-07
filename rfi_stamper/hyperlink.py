"""Automatic sheet cross-linking for plan-set PDFs.

Turn every in-drawing reference to a sheet number into a native PDF GoTo link
annotation, and (optionally) rebuild the document outline as a sheet index, so
that any viewer -- commercial or open-source -- gains click-to-jump navigation
between sheets plus a bookmark-panel sheet navigator.

Design notes:
  * Links are native /Link annotations with a GoTo action (``fitz.LINK_GOTO``);
    they render in every conforming reader, unlike named-destination tricks.
  * The input file is never modified.  Output is written to ``out_path`` via an
    atomic temp-file + fsync + os.replace, mirroring ``merge._atomic_write``.
  * Fully offline: this module imports no networking of any kind.

Coordinate handling: ``page.search_for`` returns hit rectangles in the page's
rotated (viewer) coordinate space, which is exactly what ``page.insert_link``
expects for its ``"from"`` rectangle -- so search hits feed straight into link
creation with no transform, on rotated and unrotated pages alike.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field

import fitz  # PyMuPDF

from .core import GHS_LINE, SHEET_TOKEN, canon, canon_loose
from .sheets import SheetIndex

# a page whose sheet number could not be detected is indexed as "PAGE-<n>"
_PLACEHOLDER = re.compile(r"^PAGE-\d+$")
# canonical sheet token -> (letters, number); mirrors sheets.TOKEN_FULL shape
_TOKEN_PARTS = re.compile(r"^([A-Z]{1,3})-?(\d{1,3}(?:\.\d{1,2})?)$")


@dataclass
class LinkStats:
    """Outcome of an :func:`auto_link` run."""

    links_added: int = 0
    pages_touched: int = 0
    sheets_indexed: int = 0
    unresolved: list = field(default_factory=list)  # tokens found, no sheet


# ---------------------------------------------------------------- helpers ---

def _loose_num(num: str) -> str:
    """Zero-stripped integer part, matching ``core.canon_loose`` semantics:
    ``002`` -> ``2``, ``10.10`` -> ``10.10``."""
    m = re.match(r"(\d+)(\.\d+)?$", num)
    if not m:
        return num
    return f"{int(m.group(1))}{m.group(2) or ''}"


def _variants(token: str) -> list:
    """Display forms of a canonical sheet token that may appear in drawing
    text: hyphenated, no-separator and space-separated, for both the exact and
    the zero-stripped number.  The exact token is searched first."""
    m = _TOKEN_PARTS.match(token)
    if not m:
        return [token]
    letters, num = m.group(1), m.group(2)
    forms = [token]
    for n in dict.fromkeys((num, _loose_num(num))):
        forms += [f"{letters}-{n}", f"{letters}{n}", f"{letters} {n}"]
    seen: set = set()
    out: list = []
    for f in forms:
        if f and f not in seen:
            seen.add(f)
            out.append(f)
    return out


def _detected(index: SheetIndex) -> dict:
    """``{canonical_sheet_token: page_no}`` for pages whose sheet was actually
    detected (placeholder ``PAGE-n`` pages are excluded)."""
    out: dict = {}
    for p in index.pages:
        if _PLACEHOLDER.match(p.sheet):
            continue
        out.setdefault(p.sheet, p.page_no)
    return out


def _round_rect(r, nd: int = 1) -> tuple:
    return (round(r[0], nd), round(r[1], nd), round(r[2], nd), round(r[3], nd))


# a lone sheet token (used to validate that a search hit is a whole token, not
# a substring of a longer number or an embedded run of digits/letters)
_LONE_TOKEN = re.compile(r"^([A-Z]{1,3})-?(\d{1,3}(?:\.\d{1,2})?)$")
# split a word into token-ish chunks on any run of non-token characters
# (keeps hyphens and dots, which are part of tokens like P-1.01)
_CHUNK_SPLIT = re.compile(r"[^0-9A-Za-z.\-]+")


def _canon_of(chunk: str):
    """Canonical ``LETTERS-NUMBER`` for a lone sheet token, else ``None``.
    ``P-100`` -> ``P-100``; ``P-1015``/``SP-101X``/``105550100`` -> ``None``."""
    m = _LONE_TOKEN.match(chunk.upper())
    return f"{m.group(1)}-{m.group(2)}" if m else None


def _covering_words(words, r, rm) -> list:
    """``[(x0, text)]`` for the extracted words that the hit rectangle ``r``
    actually covers, left-to-right.  ``search_for`` hits are in viewer space;
    on some PyMuPDF builds ``get_text('words')`` returns unrotated media coords
    on rotated pages, so if the raw rects overlap nothing we retry through the
    rotation matrix ``rm`` (viewer space) before giving up."""
    def collect(mat):
        out = []
        for w in words:
            wr = fitz.Rect(w[:4])
            if mat is not None:
                wr = wr * mat
            inter = wr & r
            if inter.is_empty:
                continue
            denom = min(wr.get_area(), r.get_area())
            if denom <= 0 or inter.get_area() <= 0.15 * denom:
                continue
            out.append((wr.x0, w[4]))
        return out
    got = collect(None)
    if not got and rm is not None:
        got = collect(rm)
    got.sort()
    return got


def _standalone(words, r, want: str, rm) -> bool:
    """True iff the search hit ``r`` sits over a *whole* sheet token equal to
    ``want`` (canonical).  Rejects substrings of a longer number (``P-1`` /
    ``P-10`` matched inside ``P-100``), embedded tokens (``P-101`` inside
    ``SP-101X`` or ``P-1015``) and digit runs such as a phone number
    (``P 105550100``) -- none of which are real cross-references."""
    want_loose = canon_loose(want)
    texts = [t for _x, t in _covering_words(words, r, rm)]
    if not texts:
        return False
    # per-word chunks catch a single word holding several tokens ("P-101/P-201");
    # the full concatenation catches a token an extractor split across words.
    for txt in list(texts) + ["".join(texts)]:
        for chunk in _CHUNK_SPLIT.split(txt):
            c = _canon_of(chunk.strip("-."))
            if c is not None and canon_loose(c) == want_loose:
                return True
    return False


# ------------------------------------------------------------- public API ---

def sheet_targets(index: SheetIndex) -> dict:
    """``{canonical_token: page_no}`` for every detected sheet, including its
    zero-stripped (``core.canon_loose``) key, so leading-zero variants resolve
    by lookup.  Hyphen/space display variants are resolved at search time by
    :func:`link_report` / :func:`auto_link` rather than as dict keys."""
    out: dict = {}
    for tok, pg in _detected(index).items():
        out.setdefault(tok, pg)
        out.setdefault(canon_loose(tok), pg)
    return out


def _find_hits(doc, targets: dict, link_self: bool, dedupe: bool) -> list:
    """Read-only pass: return ``[(src_page, token, target_page, rect)]`` for
    every resolved reference.  ``rect`` is in the source page's viewer space."""
    hits: list = []
    for i in range(doc.page_count):
        page = doc[i]
        src = i + 1
        text = page.get_text("text")
        if not text.strip():                       # image-only / blank page
            continue
        tp = page.get_textpage()                   # extract once, search many
        words = page.get_text("words", textpage=tp)
        rm = page.rotation_matrix if page.rotation % 360 else None
        page_seen: set = set()
        for token, target in targets.items():
            if src == target and not link_self:    # skip a sheet's own number
                continue
            for form in _variants(token):
                for r in page.search_for(form, textpage=tp):
                    # search_for matches substrings; keep only hits that sit
                    # over a *whole* token (rejects P-1 inside P-100, embedded
                    # tokens and phone-number digit runs -> wrong-target links)
                    if not _standalone(words, r, token, rm):
                        continue
                    key = (_round_rect(r), target)
                    if dedupe and key in page_seen:
                        continue
                    page_seen.add(key)
                    hits.append((src, token, target, (r.x0, r.y0, r.x1, r.y1)))
    return hits


def link_report(path: str, index=None, log=print) -> list:
    """Preview the links that :func:`auto_link` would add, WITHOUT writing.

    Returns ``[(src_page:int, token:str, target_page:int, rect:tuple)]`` where
    ``rect`` is ``(x0, y0, x1, y1)`` in the source page's viewer space.  Builds
    a :class:`SheetIndex` from ``path`` when ``index`` is not supplied.  Self
    references (a sheet number on its own page) are skipped."""
    if index is None:
        index = SheetIndex(path, log=log)
    targets = _detected(index)
    doc = fitz.open(path)
    try:
        hits = _find_hits(doc, targets, link_self=False, dedupe=True)
    finally:
        doc.close()
    log(f"  {len(hits)} reference(s) over {len({h[0] for h in hits})} page(s)")
    return hits


def _unresolved(doc, index: SheetIndex) -> list:
    """Sheet-shaped tokens present in the text that match no indexed sheet."""
    seen: set = set()
    out: list = []
    for i in range(doc.page_count):
        text = doc[i].get_text("text")
        if not text:
            continue
        for line in text.splitlines():
            if GHS_LINE.search(line):              # MSDS codes, not sheets
                continue
            for m in SHEET_TOKEN.finditer(line):
                tok = canon(m.group(1), m.group(2))
                if tok in seen:
                    continue
                seen.add(tok)
                if index.match(tok) is None:
                    out.append(tok)
    return out


def _atomic_write_doc(doc, out_path: str) -> None:
    """Serialize ``doc`` beside ``out_path``, fsync, then atomically replace --
    a crash can never leave a truncated PDF at the final path."""
    tmp = out_path + ".part"
    with open(tmp, "wb") as f:
        f.write(doc.tobytes(deflate=True))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out_path)


def auto_link(path: str, out_path: str, index=None, add_outline: bool = True,
              link_self: bool = False, dedupe: bool = True, log=print) -> LinkStats:
    """Add a native GoTo /Link annotation for every resolved sheet reference.

    Never mutates the input; writes ``out_path`` atomically.  Existing links are
    left in place (annotations are only added).  With ``add_outline`` the
    document outline is rebuilt as a sheet index -- one bookmark per detected
    sheet pointing at its page -- so the bookmark panel becomes a navigator.
    ``link_self`` includes a sheet's own number; ``dedupe`` avoids stacking
    identical links on the same rectangle."""
    if os.path.abspath(path) == os.path.abspath(out_path):
        raise ValueError("out_path must differ from input (input is never mutated)")
    if index is None:
        index = SheetIndex(path, log=log)
    stats = LinkStats()
    targets = _detected(index)
    stats.sheets_indexed = len(targets)

    doc = fitz.open(path)
    try:
        hits = _find_hits(doc, targets, link_self=link_self, dedupe=dedupe)
        by_page: dict = defaultdict(list)
        for h in hits:
            by_page[h[0]].append(h)
        for src, group in by_page.items():
            page = doc[src - 1]
            # existing GoTo links, in viewer space, so re-running on an already
            # linked file cannot stack a duplicate on top of one (get_links'
            # "from" is unrotated media space; derotate back to viewer space)
            dm = page.derotation_matrix
            existing = [(lk["page"], fitz.Rect(lk["from"]) * dm)
                        for lk in page.get_links()
                        if lk.get("kind") == fitz.LINK_GOTO]
            touched = False
            for _src, _tok, target, rect in group:
                vr = fitz.Rect(rect)
                dup = any(
                    tp == target - 1 and not (er & vr).is_empty
                    and (er & vr).get_area() > 0.6 * min(er.get_area(), vr.get_area())
                    for tp, er in existing)
                if dup:
                    continue
                page.insert_link({
                    "kind": fitz.LINK_GOTO,
                    "from": vr,
                    "page": target - 1,            # insert_link is 0-based
                    "to": fitz.Point(0, 0),
                })
                existing.append((target - 1, vr))
                stats.links_added += 1
                touched = True
            if touched:
                stats.pages_touched += 1
        stats.unresolved = _unresolved(doc, index)
        if add_outline and targets:
            toc = [[1, tok, pg] for tok, pg in
                   sorted(targets.items(), key=lambda kv: (kv[1], kv[0]))]
            doc.set_toc(toc)                       # replaces any existing outline
        _atomic_write_doc(doc, out_path)
    finally:
        doc.close()
    log(f"  {stats.links_added} link(s) on {stats.pages_touched} page(s); "
        f"{stats.sheets_indexed} sheet(s) indexed; "
        f"{len(stats.unresolved)} unresolved")
    return stats
