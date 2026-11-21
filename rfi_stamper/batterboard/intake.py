"""Intake: classify every dropped file and page.  Never guess.

Brief section 5.1.  Each page becomes one of: structural plan (per level),
architectural plan (per level), general notes, wall schedule, shop
drawing, RFI response, CAD background (ASCII DXF only), or UNKNOWN.  A
page that no rule classifies is listed as unknown with the reason; the
user can assign it in the detected-files table (``Options.class_of`` /
``Options.level_of``), which is the only way an unknown ever becomes
something.

Rules are deterministic and read only the title block: the sheet number
(``sheets.SheetIndex``), the title lines beside it, the revision text.
For any CAD file that is not ASCII DXF the plain message asking for a DXF
is carried to the tab as is.
"""
from __future__ import annotations

import os
import re

import fitz

from .. import core, dxfread, sheets
from .. import sightline as sl
from . import model as M

_ORD = {"FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4, "FIFTH": 5, "SIXTH": 6,
        "SEVENTH": 7, "EIGHTH": 8, "NINTH": 9, "TENTH": 10}
_CAPTIONS = ("SCALE", "SHEET", "REVISION", "DATE", "DRAWN", "CHECKED", "PROJECT", "JOB")
_REV_RE = re.compile(r"^REV(?:ISION)?\.?\s*(?:NO\.?)?\s*[:#]?\s*([0-9]{1,3}|[A-Z])$")


def level_from_text(text: str):
    """-> ``(name, code, order)`` for the level a title names, or None.

    ``LEVEL 2`` / ``2ND FLOOR`` / ``SECOND FLOOR`` / ``L2`` -> ("LEVEL 2",
    "L2", 2); ``ROOF``; ``FOUNDATION``; ``BASEMENT``; ``P1`` parking
    levels.  A title that names two different levels names none (never
    guessed)."""
    t = " " + re.sub(r"[^A-Z0-9 ]", " ", text.upper()) + " "
    hits = set()
    for m in re.finditer(r" LEVEL\s+(\d{1,2}) ", t):
        hits.add(int(m.group(1)))
    for m in re.finditer(r" (\d{1,2})(?:ST|ND|RD|TH)\s+(?:FLOOR|LEVEL) ", t):
        hits.add(int(m.group(1)))
    for m in re.finditer(r" (" + "|".join(_ORD) + r")\s+(?:FLOOR|LEVEL) ", t):
        hits.add(_ORD[m.group(1)])
    for m in re.finditer(r" L(\d{1,2}) ", t):
        hits.add(int(m.group(1)))
    named = []
    if hits:
        named = [(f"LEVEL {n}", f"L{n}", float(n)) for n in sorted(hits)]
    if re.search(r" ROOF ", t):
        named.append(("ROOF", "RF", 900.0))
    if re.search(r" FOUNDATION ", t):
        named.append(("FOUNDATION", "FND", -10.0))
    if re.search(r" BASEMENT ", t):
        named.append(("BASEMENT", "B1", -5.0))
    for m in re.finditer(r" P(\d) ", t):
        named.append((f"PARKING {m.group(1)}", f"P{m.group(1)}", -float(m.group(1))))
    return named[0] if len(named) == 1 else None


def title_block(page, sheet: str) -> dict:
    """Read the title block beside the sheet number: ``{"title",
    "revision", "text"}`` (``text`` = every line of the zone, joined)."""
    lines = sl.text_lines(page)
    w, h = page.rect.width, page.rect.height
    anchor = None
    want = sheet.replace("-", "").upper()
    for ln in lines:
        if ln["text"].replace("-", "").replace(" ", "").upper() == want and want:
            if anchor is None or ln["size"] > anchor["size"]:
                anchor = ln
    if anchor is not None:
        ax = (anchor["bbox"][0] + anchor["bbox"][2]) / 2.0
        ay = (anchor["bbox"][1] + anchor["bbox"][3]) / 2.0
        zone = (ax - 300.0, ay - 200.0, w, h)
    else:
        zone = (0.62 * w, 0.68 * h, w, h)
    zl = [ln for ln in lines
          if zone[0] <= (ln["bbox"][0] + ln["bbox"][2]) / 2.0 <= zone[2]
          and zone[1] <= (ln["bbox"][1] + ln["bbox"][3]) / 2.0 <= zone[3]]
    zl.sort(key=lambda ln: (round(ln["bbox"][1]), ln["bbox"][0]))
    revision = ""
    title = None
    for ln in zl:
        t = ln["text"].strip().upper()
        m = _REV_RE.match(t)
        if m:
            revision = m.group(1)
            continue
        if t.rstrip(":") in _CAPTIONS or t.replace("-", "").replace(" ", "") == want:
            continue
        if not re.search(r"[A-Z]{3}", t) or '"' in t:
            continue
        key = (any(k in t for k in ("PLAN", "NOTES", "SCHEDULE", "SHOP DRAWING", "DETAIL")),
               ln["bold"], ln["size"], len(t))
        if title is None or key > title[0]:
            title = (key, ln["text"].strip())
    return {"title": title[1] if title else "", "revision": revision,
            "text": " | ".join(ln["text"].strip() for ln in zl)}


def classify_title(sheet: str, title: str) -> tuple:
    """-> ``(page class, why)`` from the sheet number and its title."""
    t = title.upper()
    pre = re.match(r"[A-Z]+", sheet.upper())
    disc = pre.group(0) if pre else ""
    if "GENERAL NOTES" in t:
        return M.PG_NOTES, "title says GENERAL NOTES"
    if "SCHEDULE" in t and ("WALL" in t or "SHEAR" in t):
        return M.PG_SCHEDULE, "title says wall schedule"
    if "SHOP DRAWING" in t:
        return M.PG_SHOP, "title says SHOP DRAWING"
    if "PLAN" in t:
        if disc.startswith("S"):
            return M.PG_STRUCT, "an S sheet whose title says PLAN"
        if disc.startswith("A"):
            return M.PG_ARCH, "an A sheet whose title says PLAN"
        return M.PG_UNKNOWN, (f"a plan on sheet {sheet or '(no number)'}: neither an S "
                              "nor an A sheet")
    if not t:
        return M.PG_UNKNOWN, "no title could be read from the title block"
    return M.PG_UNKNOWN, f"no rule matches the title {title!r}"


def _is_pdf(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(5).startswith(b"%PDF")
    except OSError:
        return False


def _rfi_record(path: str):
    """The RFI response a small PDF carries, or None."""
    try:
        doc = fitz.open(path)
        n = doc.page_count
        head = (doc[0].get_text() if n else "")[:4000]
        doc.close()
    except Exception:   # noqa: BLE001
        return None
    if n > 12 or not re.search(r"REQUEST\s+FOR\s+INFORMATION|\bRFI\b", head, re.I):
        return None
    try:
        recs = core.parse_paths([path], log=lambda *_: None)
    except Exception:   # noqa: BLE001
        return None
    for r in recs:
        if r.numbered and (r.question or r.answer):
            return r
    return None


def classify_drop(paths, opts: M.Options | None = None, log=lambda *_: None) -> list:
    """Classify every file (and every page of every PDF) -> ``[SheetRef]``
    in drop order.  Folders are walked one level."""
    opts = opts or M.Options()
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += sorted(os.path.join(p, f) for f in os.listdir(p)
                            if os.path.isfile(os.path.join(p, f)))
        else:
            files.append(p)
    out: list = []
    for path in files:
        base = os.path.basename(path)
        if not os.path.isfile(path):
            out.append(M.SheetRef(file=path, why="file not found"))
            continue
        if _is_pdf(path):
            rec = _rfi_record(path)
            if rec is not None:
                out.append(M.SheetRef(file=path, page=1, cls=M.PG_RFI, rfi=rec.number,
                                      title=rec.title, answer=rec.answer.strip(),
                                      why="reads as an RFI with a number"))
                continue
            try:
                index = sheets.SheetIndex(path, log=lambda *_: None)
                doc = fitz.open(path)
            except Exception as e:   # noqa: BLE001
                out.append(M.SheetRef(file=path, why=f"could not be opened as a PDF: {e}"))
                continue
            try:
                for i in range(doc.page_count):
                    info = index.info(i + 1)
                    sheet = (info.sheet if info else "") or ""
                    tb = title_block(doc[i], sheet)
                    cls, why = classify_title(sheet, tb["title"])
                    lv = level_from_text(tb["title"])
                    ref = M.SheetRef(file=path, page=i + 1, sheet=sheet, title=tb["title"],
                                     cls=cls, level=lv[0] if lv else "",
                                     revision=tb["revision"], why=why)
                    _apply_overrides(ref, opts)
                    if ref.cls in (M.PG_STRUCT, M.PG_ARCH, M.PG_SHOP) and not ref.level:
                        ref.why += "; the title names no level"
                    out.append(ref)
            finally:
                doc.close()
            continue
        kind = dxfread.sniff(path)
        if kind == "ascii_dxf":
            from .cadread import level_token
            code = level_token(base)
            ref = M.SheetRef(file=path, page=0, cls=M.PG_CAD, title=base,
                             level=_name_for_code(code), why="an ASCII DXF")
            _apply_overrides(ref, opts)
            if not ref.level:
                ref.why += "; the file name names no level"
            out.append(ref)
        elif kind in ("binary_dxf", "native_cad"):
            out.append(M.SheetRef(file=path, page=0, cls=M.PG_UNKNOWN, title=base,
                                  why=dxfread.NOT_ASCII_DXF))
        else:
            out.append(M.SheetRef(file=path, page=0, cls=M.PG_UNKNOWN, title=base,
                                  why="not a PDF and not an ASCII DXF"))
    _mark_prior(out)
    for r in out:
        log(f"  {os.path.basename(r.file)} p{r.page} {r.sheet or '-'}: {r.cls}"
            f"{' ' + r.level if r.level else ''} ({r.why})")
    return out


def _name_for_code(code: str) -> str:
    if not code:
        return ""
    m = re.fullmatch(r"L(\d+)", code)
    if m:
        return f"LEVEL {int(m.group(1))}"
    return {"RF": "ROOF", "FND": "FOUNDATION", "B1": "BASEMENT"}.get(code, code)


def _apply_overrides(ref: M.SheetRef, opts: M.Options) -> None:
    for key in (ref.sheet, os.path.basename(ref.file)):
        if key and key in opts.class_of:
            ref.cls = opts.class_of[key]
            ref.why = "set by the user"
        if key and key in opts.level_of:
            ref.level = str(opts.level_of[key]).upper()
            if "set by the user" not in ref.why:
                ref.why += "; level set by the user"


def _rev_key(rev: str):
    if rev.isdigit():
        return (1, int(rev), "")
    return (0, 0, rev) if rev else None


def _mark_prior(refs) -> None:
    """Two issues of one sheet: the lower revision is the PRIOR issue.
    When the revision text cannot order them both stay current and the
    reason says so (the reader then refuses that level)."""
    groups: dict = {}
    for r in refs:
        if r.cls in (M.PG_STRUCT, M.PG_ARCH) and r.sheet:
            groups.setdefault((r.cls, r.sheet), []).append(r)
    for (_cls, sheet), rs in groups.items():
        if len(rs) < 2:
            continue
        keys = [_rev_key(r.revision) for r in rs]
        if any(k is None for k in keys) or len(set(keys)) != len(keys) \
                or len({k[0] for k in keys}) != 1:
            for r in rs:
                r.why += (f"; {len(rs)} files carry sheet {sheet} and their revision "
                          "text does not order them")
            continue
        top = max(keys)
        for r, k in zip(rs, keys):
            if k != top:
                r.prior = True
                r.why += f"; an earlier issue of {sheet}"


def level_code(name: str) -> str:
    lv = level_from_text(name)
    return lv[1] if lv else re.sub(r"[^A-Z0-9]", "", name.upper())[:6]


def level_order(name: str) -> float:
    lv = level_from_text(name)
    return lv[2] if lv else 0.0
