"""Shear wall schedule reader: the table and the zone length detail.

Brief section 5.5: read the schedule (marks, thickness, minimum length,
end bars, ties) and the zone length detail.  On site the end zones are
called boundary zones or jamb bars.  Where the schedule and the detail
DISAGREE the caller raises an open item; this module only reports both
values (:meth:`Schedule.disagreements`), it never picks one.

How it reads a page
-------------------
* Text comes only through ``sightline.words`` / ``sightline.text_lines``
  and is worked in the upright reading frame (:class:`notes.TextFrame`),
  so a /Rotate sheet reads the same as an upright one.
* The header is found by its WORDS: a line carrying MARK and at least two
  of THICK*, LENGTH, BARS, TIES, ZONE.
* Table lines group by v-BAND, never by the extractor's block/line (CAD
  tables emit every cell as its own block).  Columns come from the
  vertical ruling lines when the page has them (read through the
  Sightline, so a superseded rule hidden on the plotted sheet is not a
  rule), else from the midpoints between the header cells.
* Lengths must carry a unit (a foot or inch mark, IN / FT / MM, or a unit
  in the column header).  A cell that will not parse is left unread and
  named in ``problems``: nothing is guessed and nothing is dropped
  silently.
* The zone length detail is every line ``<MARK> = <length>`` (or
  ``<MARK>: <length>``) outside the table whose mark is in the table.

GUI-free.  fitz + numpy + stdlib only.
"""
from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, field

import fitz

from .. import geom2d as g2
from .. import sightline
from ..draft import parse_ftin
from .notes import Frag, TextFrame, caps_short, fragments

SIXTEENTH_FT = 1.0 / 192.0
_BLANK = {"", "-", "--", "---", "N/A", "NA", "NONE"}
_QUOTES = str.maketrans({"’": "'", "′": "'", "‘": "'",
                         "”": '"', "″": '"', "“": '"'})
_MARK_RE = re.compile(r"^[A-Z]{1,5}[-.]?\d{1,3}[A-Z]?$")
_HEAD_KEYS = ("THICK", "LENGTH", "BARS", "TIES", "ZONE")


@dataclass
class WallRow:
    """One schedule row.  Lengths are decimal feet, ``None`` when the cell
    is blank or would not parse (then ``Schedule.problems`` says why)."""
    mark: str
    thickness_ft: float | None = None
    min_length_ft: float | None = None
    zone_length_ft: float | None = None
    end_bars: str = ""
    ties: str = ""
    cells: dict = field(default_factory=dict)     # header text -> cell text as printed
    sheet: str = ""
    page: int = 0
    bbox: tuple = (0.0, 0.0, 0.0, 0.0)


@dataclass
class ZoneDetail:
    """One ``<MARK> = <length>`` line of the zone length detail."""
    mark: str
    zone_length_ft: float | None
    text: str                      # the line exactly as printed
    sheet: str = ""
    page: int = 0
    bbox: tuple = (0.0, 0.0, 0.0, 0.0)
    detail: str = ""               # view number of the detail ("5"), "" when none found


@dataclass
class Schedule:
    sheet: str = ""
    page: int = 0
    rows: list = field(default_factory=list)
    details: list = field(default_factory=list)
    problems: list = field(default_factory=list)   # plain-words reader remarks

    def row(self, mark: str):
        m = _norm_mark(mark)
        return next((r for r in self.rows if r.mark == m), None)

    def detail(self, mark: str):
        m = _norm_mark(mark)
        return next((d for d in self.details if d.mark == m), None)

    def marks(self) -> list:
        return [r.mark for r in self.rows]

    def disagreements(self) -> list:
        """``[(mark, schedule_ft, detail_ft)]`` where the schedule and the
        detail both give a zone length and they differ by more than
        1/16 in.  Both values are reported; neither is preferred."""
        out = []
        for r in self.rows:
            if r.zone_length_ft is None:
                continue
            for d in self.details:
                if (d.mark == r.mark and d.zone_length_ft is not None
                        and abs(d.zone_length_ft - r.zone_length_ft) > SIXTEENTH_FT):
                    out.append((r.mark, r.zone_length_ft, d.zone_length_ft))
        return out


def _norm_mark(s: str) -> str:
    return "".join(str(s).upper().split())


# ---------------------------------------------------------------- lengths --

def _frac(s: str):
    """``12`` / ``7 1/2`` / ``1/2`` / ``0.75`` -> float, else None."""
    s = s.strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)", s)
    if m:
        return float(m.group(1))
    m = re.fullmatch(r"(?:(\d+)[\s-]+)?(\d+)\s*/\s*(\d+)", s)
    if m and int(m.group(3)) != 0:
        return float(m.group(1) or 0) + int(m.group(2)) / int(m.group(3))
    return None


def parse_length(cell: str, header: str = ""):
    """A schedule length cell -> ``(feet | None, ok)``.

    ``ok`` is False only for a NON-BLANK cell that is not a length with a
    unit.  The unit must be on the cell (a foot or inch mark, IN / FT /
    MM / CM / M) or in the column header (``THICKNESS (IN)``); a bare
    number under a header with no unit is not guessed at."""
    s = " ".join(str(cell).translate(_QUOTES).upper().split())
    if s in _BLANK:
        return None, True
    m = re.fullmatch(r"(.+?)\s*(MM|CM|M)\.?", s)
    if m and _frac(m.group(1)) is not None:
        k = {"MM": 0.001, "CM": 0.01, "M": 1.0}[m.group(2)]
        return _frac(m.group(1)) * k / 0.3048, True
    m = re.fullmatch(r"(.+?)\s*(IN\.?|INCH|INCHES)", s)
    if m and _frac(m.group(1)) is not None:
        return _frac(m.group(1)) / 12.0, True
    m = re.fullmatch(r"(.+?)\s*(FT\.?|FEET|FOOT)", s)
    if m and _frac(m.group(1)) is not None:
        return _frac(m.group(1)), True
    if "'" in s or '"' in s:
        v = parse_ftin(s)
        return (v, True) if v is not None and v >= 0 else (None, False)
    v = _frac(s)
    if v is not None:
        h = " ".join(str(header).translate(_QUOTES).upper().split())
        if re.search(r"\(\s*IN|\bIN\.|\bINCH|\"", h):
            return v / 12.0, True
        if re.search(r"\(\s*FT|\bFT\.|\bFEET|'", h):
            return v, True
        if re.search(r"\(\s*MM", h):
            return v * 0.001 / 0.3048, True
    return None, False


def _role(header: str) -> str:
    """Column role from its header words."""
    toks = set(re.sub(r"[^A-Z ]", " ", header.upper()).split())
    if "MARK" in toks:
        return "mark"
    if any(t.startswith("THICK") or t in ("THK", "THKNS") for t in toks):
        return "thickness"
    if toks & {"LENGTH", "LEN", "LGTH"}:
        if toks & {"ZONE", "BOUNDARY"}:
            return "zone_length"
        if toks & {"MIN", "MINIMUM"}:
            return "min_length"
        return ""
    if toks & {"BARS", "BAR"}:
        return "end_bars"
    if toks & {"TIES", "TIE"}:
        return "ties"
    return ""


def _is_header(words) -> bool:
    toks = [re.sub(r"[^A-Z]", "", w.text.upper()) for w in words]
    if "MARK" not in toks:
        return False
    hits = sum(1 for k in _HEAD_KEYS
               if any(t.startswith(k) or (k == "THICK" and t in ("THK", "THKNS"))
                      for t in toks))
    return hits >= 2


# ------------------------------------------------------------------ rules --

def _rules(page, frame: TextFrame) -> tuple:
    """Ruling lines and circles of the page, in the upright frame.

    Returns ``(h_rules, v_rules, circles)``: ``h_rules`` are ``(v, u0,
    u1)``, ``v_rules`` ``(u, v0, v1)``, ``circles`` ``(cu, cv, r)``.  The
    drawings come through the Sightline, so linework that is in the file
    but not on the plotted sheet is not a rule."""
    m = page.rotation_matrix
    h_rules, v_rules, circles = [], [], []

    def up(p):
        q = fitz.Point(p) * m
        return frame.up(q.x, q.y)

    def seg(a, b):
        (u0, v0), (u1, v1) = up(a), up(b)
        if abs(v1 - v0) <= 0.6 and abs(u1 - u0) >= 6.0:
            h_rules.append(((v0 + v1) / 2.0, min(u0, u1), max(u0, u1)))
        elif abs(u1 - u0) <= 0.6 and abs(v1 - v0) >= 6.0:
            v_rules.append(((u0 + u1) / 2.0, min(v0, v1), max(v0, v1)))

    for d in sightline.get_drawings(page, min_len_pt=4.0):
        curve_pts = []
        n_curves = 0
        for it in d.get("items") or ():
            op = it[0]
            if op == "l":
                seg(it[1], it[2])
            elif op == "re":
                r = it[1]
                c = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
                for i in range(4):
                    seg(c[i], c[(i + 1) % 4])
            elif op == "qu":
                q = it[1]
                c = [q.ul, q.ur, q.lr, q.ll]
                for i in range(4):
                    seg(c[i], c[(i + 1) % 4])
            elif op == "c":
                n_curves += 1
                curve_pts.extend(g2.bezier(*[up(p) for p in it[1:5]], n=8))
        if n_curves >= 4:
            cu, cv, r, rms = g2.fit_circle(curve_pts)
            if (3.0 <= r <= 45.0 and rms <= 0.03 * r
                    and g2.arc_span_deg(curve_pts, cu, cv) >= 300.0):
                circles.append((cu, cv, r))
    return h_rules, v_rules, circles


def _distinct(vals, tol: float = 1.5) -> list:
    out: list = []
    for v in sorted(vals):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


# ------------------------------------------------------------- the reader --

def read_schedule(page, sheet: str = "", page_no: int = 0) -> Schedule:
    """Read the shear wall schedule and the zone length detail off one
    page."""
    page_no = int(page_no) or (page.number + 1)
    sch = Schedule(sheet=sheet, page=page_no)
    frame = TextFrame(page)
    where = sheet or f"page {page_no}"
    if not frame.words:
        sch.problems.append(f"{where}: no text on the page, so no schedule was read")
        return sch
    bands = frame.bands()
    h_rules, v_rules, circles = _rules(page, frame)
    regions: list = []                 # upright boxes of the tables read
    for band in bands:
        marks = [w for w in band if re.sub(r"[^A-Z]", "", w.text.upper()) == "MARK"]
        if not marks:
            continue
        mw = marks[0]
        if any(u0 <= mw.cu <= u1 and v0 <= mw.cv <= v1 for u0, v0, u1, v1 in regions):
            continue
        table = (_ruled_table(frame, mw, h_rules, v_rules)
                 or _open_table(frame, bands, band, mw))
        if table is None:
            continue
        regions.append(table["box"])
        _rows(sch, frame, table, sheet, page_no, where)
    if not regions:
        sch.problems.append(
            f"{where}: no schedule header found (a header line needs MARK and at "
            "least two of THICKNESS, LENGTH, BARS, TIES, ZONE)")
        return sch
    seen: dict = {}
    for r in sch.rows:
        seen[r.mark] = seen.get(r.mark, 0) + 1
    for mark, n in seen.items():
        if n > 1:
            sch.problems.append(f"{where}: mark {mark} appears in {n} schedule rows")
    _details(sch, frame, bands, regions, circles, sheet, page_no, where)
    return sch


def _ruled_table(frame, mw, h_rules, v_rules):
    """The table around the MARK word when ruling lines box it.

    The table's own rules are told from the sheet border and the title
    strip (which also run past the header) by the header's TOP rule: the
    nearest horizontal rule above MARK that at least two vertical rules
    reach up to.  Its extent is the table's width."""
    vr_all = [r for r in v_rules if r[1] - 2.0 <= mw.cv <= r[2] + 2.0]
    vr = None
    tops = sorted((r for r in h_rules
                   if r[0] < mw.cv and r[1] - 2.0 <= mw.cu <= r[2] + 2.0),
                  key=lambda r: -r[0])
    for top in tops:
        if mw.cv - top[0] > 6.0 * mw.h:
            break
        got = [r for r in vr_all
               if top[1] - 2.0 <= r[0] <= top[2] + 2.0 and r[1] <= top[0] + 3.0]
        us = _distinct(r[0] for r in got)
        if len(us) >= 2 and us[0] < mw.cu < us[-1]:
            vr, tv0 = got, top[0]
            break
    if vr is None:
        # no top rule: the two rules that box the MARK cell, and every
        # rule of their height
        left = [r for r in vr_all if r[0] <= mw.u0 + 1.0]
        right = [r for r in vr_all if r[0] >= mw.u1 - 1.0]
        if not left or not right:
            return None
        ra, rb = max(left, key=lambda r: r[0]), min(right, key=lambda r: r[0])
        v0, v1 = max(ra[1], rb[1]), min(ra[2], rb[2])
        if mw.u0 - ra[0] > 10.0 * mw.size or rb[0] - mw.u1 > 30.0 * mw.size:
            return None                 # those are not this cell's rules
        vr = [r for r in vr_all if abs(r[1] - v0) <= 3.0 and abs(r[2] - v1) <= 3.0]
        us = _distinct(r[0] for r in vr)
        if len(us) < 2 or not (us[0] < mw.cu < us[-1]):
            return None
        tv0 = v0
    us = _distinct(r[0] for r in vr)
    tu0, tu1 = us[0], us[-1]
    tv1 = statistics.median(r[2] for r in vr)
    width = tu1 - tu0
    hs = _distinct(v for v, a, b in h_rules
                   if tv0 - 2.0 <= v <= tv1 + 2.0
                   and min(b, tu1) - max(a, tu0) >= 0.5 * width)
    above = [v for v in hs if v < mw.cv]
    below = [v for v in hs if v > mw.cv]
    head_top = above[-1] if above else tv0
    head_bot = below[0] if below else mw.v1 + 0.3 * mw.h
    head_words = [w for w in frame.words
                  if head_top < w.cv < head_bot and tu0 <= w.cu <= tu1]
    if not _is_header(head_words):
        return None
    cols = []
    for lo, hi in zip(us, us[1:]):
        ws = sorted((w for w in head_words if lo <= w.cu < hi),
                    key=lambda w: (round(w.cv, 1), w.u0))
        cols.append({"lo": lo, "hi": hi, "head": " ".join(w.text for w in ws)})
    body = [w for w in frame.words
            if head_bot < w.cv <= tv1 + 2.0 and tu0 <= w.cu <= tu1]
    seps = [v for v in hs if v >= head_bot - 0.5]
    if len(seps) >= 2:
        groups = [[w for w in body if a < w.cv < b] for a, b in zip(seps, seps[1:])]
    else:
        groups = frame.bands(body)
    return {"cols": cols, "groups": [g for g in groups if g],
            "box": (tu0, tv0, tu1, tv1), "ruled": True}


def _open_table(frame, bands, band, mw):
    """A table with no ruling lines: columns from the header cells."""
    size = mw.size
    cells = [f for f in fragments([band], 1.0 * size) if caps_short(f.words, 8)]
    k = next((i for i, c in enumerate(cells) if mw in c.words), None)
    if k is None:
        return None
    run = [cells[k]]
    for c in cells[k + 1:]:                       # cells to the right, one table
        if c.u0 - run[-1].u1 > 15.0 * size:
            break
        run.append(c)
    for c in reversed(cells[:k]):
        if run[0].u0 - c.u1 > 15.0 * size:
            break
        run.insert(0, c)
    if not _is_header([w for c in run for w in c.words]):
        return None
    pitch = (statistics.median(b.u0 - a.u0 for a, b in zip(run, run[1:]))
             if len(run) > 1 else 8.0 * size)
    edges = [run[0].u0 - 3.0 * size]
    edges += [(a.u1 + b.u0) / 2.0 for a, b in zip(run, run[1:])]
    edges.append(run[-1].u1 + max(pitch, 4.0 * size))
    cols = [{"lo": lo, "hi": hi, "head": c.text, "cell": c}
            for c, lo, hi in zip(run, edges, edges[1:])]
    tu0, tu1 = edges[0], edges[-1]
    head_v = run[0].cv
    groups = []
    last_v, row_h = head_v, None
    for b in bands:
        cv = statistics.median(w.cv for w in b)
        if cv <= head_v + 0.5 * mw.h:
            continue
        ws = [w for w in b if tu0 <= w.cu <= tu1]
        if not ws:
            continue
        gap = cv - last_v
        if row_h is not None and gap > 2.5 * row_h:
            break
        if row_h is None and gap > 6.0 * mw.h:
            break
        row_h = gap if row_h is None else statistics.median([row_h, gap])
        last_v = cv
        groups.append(ws)
    return {"cols": cols, "groups": groups,
            "box": (tu0, head_v - mw.h, tu1, last_v + mw.h), "ruled": False}


def _cell_texts(table, group) -> list:
    """The words of one row -> one text per column."""
    cols = table["cols"]
    out = [[] for _ in cols]
    if table["ruled"]:
        for w in group:
            for i, c in enumerate(cols):
                if c["lo"] <= w.cu < c["hi"]:
                    out[i].append(w)
                    break
    else:
        # no rules: a cell goes to the column whose header it overlaps
        # most (left-aligned cells run on past the midpoint), else to the
        # column its center falls in
        size = max(w.size for w in group)
        for f in fragments([sorted(group, key=lambda w: w.u0)], 1.6 * size):
            best, best_ov = None, 0.0
            for i, c in enumerate(cols):
                ov = min(f.u1, c["cell"].u1) - max(f.u0, c["cell"].u0)
                if ov > best_ov:
                    best, best_ov = i, ov
            if best is None:
                cu = (f.u0 + f.u1) / 2.0
                best = next((i for i, c in enumerate(cols)
                             if c["lo"] <= cu < c["hi"]), None)
            if best is not None:
                out[best].extend(f.words)
    return [" ".join(w.text for w in sorted(ws, key=lambda w: (round(w.cv, 1), w.u0)))
            for ws in out]


def _rows(sch: Schedule, frame, table, sheet, page_no, where) -> None:
    cols = table["cols"]
    roles: dict = {}
    for i, c in enumerate(cols):
        r = _role(c["head"])
        if not r:
            continue
        if r == "end_bars" and r in roles:
            # several BARS columns: the end / boundary / jamb one is meant
            if not re.search(r"\b(END|BOUNDARY|JAMB|ZONE)\b", c["head"].upper()):
                continue
            if re.search(r"\b(END|BOUNDARY|JAMB|ZONE)\b", cols[roles[r]]["head"].upper()):
                continue
        elif r in roles:
            continue
        roles[r] = i
    if "mark" not in roles:
        return
    last = None
    for group in table["groups"]:
        texts = _cell_texts(table, group)
        mark = _norm_mark(texts[roles["mark"]])
        if not mark:
            if last is not None and any(texts):
                # a wrapped line of the row above
                for c, t in zip(cols, texts):
                    if t and c["head"]:
                        last.cells[c["head"]] = (last.cells.get(c["head"], "") + " " + t).strip()
                _fill(sch, last, cols, roles, where, redo=True)
            elif any(texts):
                sch.problems.append(
                    f"{where}: a schedule row with no mark was left unread "
                    f"({' | '.join(t for t in texts if t)})")
            continue
        if not _MARK_RE.match(mark):
            sch.problems.append(
                f"{where}: \"{texts[roles['mark']]}\" in the MARK column is not a "
                "wall mark; the row was left unread")
            continue
        row = WallRow(mark=mark, sheet=sheet, page=page_no,
                      bbox=frame.box_of(group))
        for c, t in zip(cols, texts):
            if c["head"]:
                row.cells[c["head"]] = t
        _fill(sch, row, cols, roles, where)
        sch.rows.append(row)
        last = row


def _fill(sch: Schedule, row: WallRow, cols, roles, where, redo: bool = False) -> None:
    """Typed fields from the row's printed cells."""
    for role, attr in (("thickness", "thickness_ft"), ("min_length", "min_length_ft"),
                       ("zone_length", "zone_length_ft")):
        if role not in roles:
            continue
        head = cols[roles[role]]["head"]
        cell = row.cells.get(head, "")
        val, ok = parse_length(cell, head)
        setattr(row, attr, val)
        if redo:
            sch.problems[:] = [p for p in sch.problems
                               if not p.startswith(f"{where}: row {row.mark}: the {head} cell")]
        if not ok:
            sch.problems.append(
                f"{where}: row {row.mark}: the {head} cell \"{cell}\" is not a "
                "length with a unit; it was left unread")
    if "end_bars" in roles:
        row.end_bars = row.cells.get(cols[roles["end_bars"]]["head"], "")
    if "ties" in roles:
        row.ties = row.cells.get(cols[roles["ties"]]["head"], "")


# ---------------------------------------------------------------- details --

_DETAIL_RE = re.compile(r"^([A-Z0-9][A-Z0-9.\-]*)\s*[=:]\s*(\S.*)$")


def _details(sch: Schedule, frame, bands, regions, circles, sheet, page_no, where) -> None:
    marks = set(sch.marks())
    if not marks:
        return
    titles = _view_titles(frame, bands, circles)
    for band in bands:
        ws = [w for w in band
              if not any(u0 <= w.cu <= u1 and v0 <= w.cv <= v1
                         for u0, v0, u1, v1 in regions)]
        if not ws:
            continue
        size = max(w.size for w in ws)
        for f in fragments([ws], 2.5 * size):
            text = f.text
            m = _DETAIL_RE.match(text.translate(_QUOTES).upper())
            if not m or _norm_mark(m.group(1)) not in marks:
                continue
            mark = _norm_mark(m.group(1))
            val, ok = parse_length(m.group(2))
            if not ok or val is None:
                sch.problems.append(
                    f"{where}: the detail line \"{text}\" gives no length with a "
                    "unit; it was left unread")
                val = None
            sch.details.append(ZoneDetail(
                mark=mark, zone_length_ft=val, text=text, sheet=sheet, page=page_no,
                bbox=frame.box_of(f.words), detail=_nearest_title(f, titles)))


def _view_titles(frame, bands, circles) -> list:
    """View titles: a bubble holding a 1-3 character number, with an
    upper-case title beside it.  ``[{"number", "title", "u0", "u1",
    "cv"}]``."""
    out = []
    for cu, cv, r in circles:
        inside = [w for w in frame.words
                  if len(w.text) <= 3 and w.text.replace(".", "").isalnum()
                  and math.hypot(w.cu - cu, w.cv - cv) <= 0.8 * r]
        if len(inside) != 1:
            continue
        best = None
        for band in bands:
            ws = [w for w in band if w not in inside]
            if not ws:
                continue
            for f in fragments([ws], 2.5 * max(w.size for w in ws)):
                if not (cu + r - 2.0 <= f.u0 <= cu + 7.0 * r):
                    continue
                if abs(f.cv - cv) > 1.3 * r:
                    continue
                letters = [c for c in f.text if c.isalpha()]
                if len(letters) < 3 or sum(c.isupper() for c in letters) < 0.6 * len(letters):
                    continue
                dist = (f.u0 - cu) + 2.0 * abs(f.cv - cv)
                if best is None or dist < best[0]:
                    best = (dist, f)
        if best is None:
            continue
        f = best[1]
        out.append({"number": inside[0].text, "title": f.text,
                    "u0": cu - r, "u1": f.u1, "cv": cv})
    return out


def _nearest_title(f: Frag, titles, reach: float = 500.0) -> str:
    """The view number of the title that names this detail: view titles
    sit UNDER their view, so one below is preferred to one above."""
    best = None
    for t in titles:
        gap_u = max(0.0, f.u0 - t["u1"], t["u0"] - f.u1)
        dv = t["cv"] - f.v1 if t["cv"] >= f.v0 else 3.0 * (f.v0 - t["cv"])
        score = math.hypot(gap_u, max(dv, 0.0))
        if score <= reach and (best is None or score < best[0]):
            best = (score, t["number"])
    return best[1] if best else ""
