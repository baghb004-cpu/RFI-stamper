"""General notes reader: every numbered note with the heading above it.

Brief section 5.7: capture the notes that govern layout (tolerance
standard, reinforcing at openings, required surveys) with the EXACT quote,
sheet, note number and heading.  The heading is found by position: the
nearest heading above the note in the SAME column.  A hand run of this
workflow once cited a note under the wrong heading (two headings in one
column); regression case 6 exists so that cannot ship again.

How it reads a page
-------------------
* Text comes only through ``sightline.words`` / ``sightline.text_lines``
  (viewer points, correct on /Rotate pages) and is worked in an UPRIGHT
  reading frame (:class:`TextFrame`): whatever way the sheet is turned,
  ``u`` runs along the writing direction and ``v`` runs down the lines.
* Lines are grouped by v-BAND, never by the extractor's block/line (CAD
  sheets emit a note number and its text as separate objects).
* Columns come from the page's own geometry: the vertical white rivers
  between text.  Two columns are never assumed.
* A numbered note starts with ``1.`` / ``1)`` / ``(1)`` / ``A.`` left of
  the open note's text; lines under it belong to it.
* A heading is a short upper-case line that is not a note start and is
  bold, or larger than the column's body text, or directly followed by a
  note numbered 1 (or A).  The big sheet title above the columns is not a
  column heading: a note with only the sheet title above it gets ``""``.
* Nothing is reworded and nothing is added: the note text is the printed
  words joined with single spaces.  No standard name, section number or
  value is ever supplied from memory.

GUI-free.  fitz + numpy + stdlib only.
"""
from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, field

import numpy as np

from .. import papertrail, sightline

TOPICS = ("tolerance", "openings", "survey", "layout")

GUTTER_SIZES = 2.4        # a column gutter is at least this many body sizes wide..
GUTTER_MIN_PT = 14.0      # ..and never under this
NARROW_SIZES = 4.0        # a "column" narrower than this is a number rail, not a column
HEAD_WORDS = 6            # a heading has at most this many words
LARGER = 1.08             # "larger than the body" factor
TITLE_OVER_HEADS = 1.25   # the sheet title is this much larger than the other headings
TITLE_OVER_BODY = 1.6     # ..or, when it is the only heading, than the body text


# ====================================================================== #
#  The upright text frame (shared with schedule.py)                       #
# ====================================================================== #

@dataclass
class Word:
    """One word in the upright reading frame."""
    u0: float
    v0: float
    u1: float
    v1: float
    text: str
    size: float
    bold: bool
    rect: tuple                    # the same word in VIEWER points

    @property
    def cu(self) -> float:
        return (self.u0 + self.u1) / 2.0

    @property
    def cv(self) -> float:
        return (self.v0 + self.v1) / 2.0

    @property
    def h(self) -> float:
        return self.v1 - self.v0


def _snap(d) -> tuple:
    """A writing direction snapped to the nearest axis when within about
    ten degrees of it."""
    dx, dy = float(d[0]), float(d[1])
    n = math.hypot(dx, dy)
    if n < 1e-9:
        return (1.0, 0.0)
    dx, dy = dx / n, dy / n
    for cx, cy in ((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0)):
        if dx * cx + dy * cy > 0.985:
            return (cx, cy)
    return (round(dx, 4), round(dy, 4))


class TextFrame:
    """The page's words in an UPRIGHT reading frame.

    The dominant writing direction ``d`` of the page (by characters) is
    measured from ``sightline.text_lines``; ``u`` runs along it and ``v``
    across it, down the lines.  Words written in any other direction
    (a turned dimension, a vertical label) are left out.  Every word
    keeps its viewer rectangle, and :meth:`rect_back` returns any upright
    box to viewer points."""

    def __init__(self, page):
        lines = sightline.text_lines(page)
        raw = sightline.words(page)
        weight: dict = {}
        dirs = []
        for ln in lines:
            d = _snap(ln["dir"])
            dirs.append(d)
            weight[d] = weight.get(d, 0) + len(ln["text"])
        self.d = max(weight, key=weight.get) if weight else (1.0, 0.0)
        self.words: list = []
        if lines:
            lb = np.array([ln["bbox"] for ln in lines], dtype=float)
            area = (lb[:, 2] - lb[:, 0]) * (lb[:, 3] - lb[:, 1])
        for w in raw:
            text = str(w[4]).replace("\xa0", " ").strip()
            if not text:
                continue
            x0, y0, x1, y1 = (float(v) for v in w[:4])
            size, bold, d = None, False, self.d
            if lines:
                cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
                hit = np.flatnonzero((lb[:, 0] - 1.0 <= cx) & (cx <= lb[:, 2] + 1.0)
                                     & (lb[:, 1] - 1.0 <= cy) & (cy <= lb[:, 3] + 1.0))
                if len(hit):
                    k = int(hit[np.argmin(area[hit])])
                    ln = lines[k]
                    d = dirs[k]
                    size, bold = float(ln["size"]), bool(ln["bold"])
                    for sp in ln["spans"]:
                        if text in sp["text"]:
                            size, bold = float(sp["size"]), bool(sp["bold"])
                            break
            if d != self.d:
                continue
            us, vs = zip(*(self.up(x, y) for x, y in
                           ((x0, y0), (x1, y0), (x1, y1), (x0, y1))))
            u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
            if not size:
                size = 0.75 * (v1 - v0)
            self.words.append(Word(u0, v0, u1, v1, text, size, bold,
                                   (x0, y0, x1, y1)))

    # -- frame maps -------------------------------------------------------- #
    def up(self, x: float, y: float) -> tuple:
        """Viewer point -> upright ``(u, v)``."""
        dx, dy = self.d
        return (x * dx + y * dy, -x * dy + y * dx)

    def back(self, u: float, v: float) -> tuple:
        """Upright ``(u, v)`` -> viewer point."""
        dx, dy = self.d
        return (u * dx - v * dy, u * dy + v * dx)

    def rect_back(self, u0, v0, u1, v1) -> tuple:
        """An upright box -> its viewer rectangle ``(x0, y0, x1, y1)``."""
        xs, ys = zip(*(self.back(u, v) for u, v in
                       ((u0, v0), (u1, v0), (u1, v1), (u0, v1))))
        return (min(xs), min(ys), max(xs), max(ys))

    def box_of(self, words) -> tuple:
        """Viewer rectangle around a set of words."""
        return self.rect_back(min(w.u0 for w in words), min(w.v0 for w in words),
                              max(w.u1 for w in words), max(w.v1 for w in words))

    @property
    def body_size(self) -> float:
        """The page's body text size (median over words)."""
        return statistics.median(w.size for w in self.words) if self.words else 8.0

    # -- lines ------------------------------------------------------------- #
    def bands(self, words=None) -> list:
        """Words grouped into visual lines by v-BAND: ``[[Word, ...]]``,
        top to bottom, each line left to right.  A word joins the band
        whose first word's center is within half its own height."""
        ws = sorted(self.words if words is None else words,
                    key=lambda w: (w.cv, w.u0))
        out: list = []
        ref = None
        for w in ws:
            if out and abs(w.cv - ref) <= 0.5 * max(w.h, 1.0):
                out[-1].append(w)
            else:
                out.append([w])
                ref = w.cv
        for b in out:
            b.sort(key=lambda w: w.u0)
        return out


@dataclass
class Frag:
    """A run of words on one band with no wide gap inside it."""
    words: list
    spanning: bool = False

    @property
    def u0(self) -> float:
        return min(w.u0 for w in self.words)

    @property
    def u1(self) -> float:
        return max(w.u1 for w in self.words)

    @property
    def v0(self) -> float:
        return min(w.v0 for w in self.words)

    @property
    def v1(self) -> float:
        return max(w.v1 for w in self.words)

    @property
    def cv(self) -> float:
        return statistics.median(w.cv for w in self.words)

    @property
    def h(self) -> float:
        return max(w.h for w in self.words)

    @property
    def size(self) -> float:
        return max(w.size for w in self.words)

    @property
    def bold(self) -> bool:
        return all(w.bold for w in self.words)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def fragments(bands, gap: float) -> list:
    """Split every band at horizontal gaps wider than ``gap``."""
    out = []
    for band in bands:
        cur = [band[0]]
        for w in band[1:]:
            if w.u0 - max(x.u1 for x in cur) > gap:
                out.append(Frag(cur))
                cur = [w]
            else:
                cur.append(w)
        out.append(Frag(cur))
    return out


def caps_short(words, max_words: int = HEAD_WORDS) -> bool:
    """Upper-case and short: the look of a heading."""
    letters = [c for w in words for c in w.text if c.isalpha()]
    return (len(letters) >= 2 and all(c.isupper() for c in letters)
            and len(words) <= max_words)


# ====================================================================== #
#  Notes                                                                   #
# ====================================================================== #

@dataclass
class Note:
    """One numbered note, quoted exactly, with where it sits."""
    sheet: str
    page: int
    column: int                    # note column, left to right, 0-based
    heading: str                   # nearest heading above, same column ("" = none)
    number: str                    # as printed, without the dot / parentheses
    text: str                      # the printed words, single spaces, nothing reworded
    bbox: tuple                    # viewer points
    topics: list = field(default_factory=list)

    @property
    def ref(self) -> str:
        """Stable key (used for skip lists): ``S-001/CONCRETE/2``."""
        return f"{self.sheet}/{self.heading}/{self.number}"

    def quote(self, file: str = "") -> "papertrail.Quote":
        return papertrail.Quote(text=self.text, sheet=self.sheet,
                                heading=self.heading, note_no=self.number,
                                file=file, page=self.page)


_NUM_A = re.compile(r"^(\d{1,3}|[A-Z])[.)]$")
_NUM_B = re.compile(r"^\((\d{1,3}|[A-Z])\)$")


def note_number(token: str):
    """``1.`` / ``1)`` / ``(1)`` / ``A.`` -> the number as printed, else
    None."""
    m = _NUM_A.match(token) or _NUM_B.match(token)
    return m.group(1) if m else None


def topics_of(text: str) -> list:
    """Layout-governing topics of a note, by its own words."""
    t = " ".join(text.upper().split())
    out = []
    if "TOLERANCE" in t:
        out.append("tolerance")
    if "OPENING" in t and ("REINFORC" in t or "TRIM BAR" in t
                           or "ADDITIONAL BAR" in t or "APPROVAL" in t):
        out.append("openings")
    if "SURVEY" in t:
        out.append("survey")
    if ("DO NOT SCALE" in t or re.search(r"\bLAYOUT\b", t)
            or "CONTROL POINT" in t or ("VERIFY" in t and "DIMENSION" in t)):
        out.append("layout")
    return out


def governing(notes) -> list:
    """The notes that govern layout (at least one topic)."""
    return [n for n in notes if n.topics]


def survey_required(notes) -> list:
    """Notes that call for a slab elevation survey."""
    out = []
    for n in notes:
        if "survey" not in n.topics:
            continue
        t = n.text.upper()
        if "ELEVATION" in t and ("SLAB" in t or "FLOOR" in t):
            out.append(n)
    return out


# ------------------------------------------------------------- columns --

def _columns_from_cov(cov: np.ndarray, gutter: int, narrow: int) -> list:
    """Covered runs of the horizontal profile, merged across gaps under
    ``gutter`` bins; a run narrower than ``narrow`` (a rail of note
    numbers) joins its right neighbour.  Returns ``[[lo, hi), ...]``."""
    solid = (cov > 0).astype(np.int8)
    edges = np.flatnonzero(np.diff(np.concatenate([[0], solid, [0]])))
    runs = [[int(a), int(b)] for a, b in zip(edges[0::2], edges[1::2])]
    merged: list = []
    for a, b in runs:
        if merged and a - merged[-1][1] < gutter:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    out: list = []
    i = 0
    while i < len(merged):
        a, b = merged[i]
        if (b - a < narrow and i + 1 < len(merged)
                and merged[i + 1][0] - b < 2 * gutter):
            merged[i + 1][0] = a
        else:
            out.append([a, b])
        i += 1
    return out


def _overlaps(frag_bins, cols, min_bins: int = 6) -> list:
    a, b = frag_bins
    return [k for k, (c0, c1) in enumerate(cols)
            if min(b, c1) - max(a, c0) > min_bins]


def find_columns(frags, body: float) -> tuple:
    """Text columns from the page's own geometry.

    Returns ``(columns, origin)``: ``columns`` are ``[u_lo, u_hi)`` pairs
    in upright points.  A fragment that bridges a gutter all by itself
    (a title spanning above the columns) is marked ``spanning`` and left
    out of the profile, so it cannot weld two columns together."""
    if not frags:
        return [], 0.0
    gutter = int(round(max(GUTTER_MIN_PT, GUTTER_SIZES * body)))
    narrow = int(round(NARROW_SIZES * body))
    origin = math.floor(min(f.u0 for f in frags))
    n = int(math.ceil(max(f.u1 for f in frags) - origin)) + 2
    cov = np.zeros(n, dtype=np.int32)

    def bins(f):
        return (int(math.floor(f.u0 - origin)), int(math.ceil(f.u1 - origin)))

    for f in frags:
        a, b = bins(f)
        cov[a:b] += 1
    # a spanner is a wide fragment that is the ONLY cover over a stretch
    # at least a gutter wide, with real columns on both sides without it
    for f in sorted(frags, key=lambda f: f.u0 - f.u1):
        a, b = bins(f)
        if b - a <= gutter + 12:
            break
        alone = (cov[a:b] == 1).astype(np.int8)
        e = np.flatnonzero(np.diff(np.concatenate([[0], alone, [0]])))
        if not any(q - p >= gutter for p, q in zip(e[0::2], e[1::2])):
            continue
        cov2 = cov.copy()
        cov2[a:b] -= 1
        if len(_overlaps((a, b), _columns_from_cov(cov2, gutter, narrow))) >= 2:
            f.spanning = True
            cov = cov2
    cols = _columns_from_cov(cov, gutter, narrow)
    return [(origin + a, origin + b) for a, b in cols], origin


def _column_of(f: Frag, cols) -> int | None:
    for k, (lo, hi) in enumerate(cols):
        if lo - 2.0 <= f.u0 <= hi:
            return k
    best, best_ov = None, 0.0
    for k, (lo, hi) in enumerate(cols):
        ov = min(f.u1, hi) - max(f.u0, lo)
        if ov > best_ov:
            best, best_ov = k, ov
    return best


# ------------------------------------------------------------ the reader --

def read_notes(page, sheet: str = "", page_no: int = 0) -> list:
    """Every numbered note on the page, in reading order (column, then
    top to bottom), each with the nearest heading above it in its own
    column."""
    page_no = int(page_no) or (page.number + 1)
    frame = TextFrame(page)
    if not frame.words:
        return []
    body_page = frame.body_size
    gutter = max(GUTTER_MIN_PT, GUTTER_SIZES * body_page)
    frags = fragments(frame.bands(), gutter)
    cols, _origin = find_columns(frags, body_page)
    if not cols:
        return []
    per_col: dict = {k: [] for k in range(len(cols))}
    spanners = []
    for f in frags:
        if f.spanning:
            spanners.append(f)
            continue
        k = _column_of(f, cols)
        if k is not None:
            per_col[k].append(f)

    note_cols = []                     # [{"events": [...], "body": size}]
    for k in range(len(cols)):
        lines = _merge_lines(per_col[k])
        got = _classify(lines)
        if got is not None:
            note_cols.append(got)
    if not note_cols:
        return []

    # ---- which heading lines are the SHEET TITLE --------------------------- #
    heads = [ev[1] for col in note_cols for ev in col["events"] if ev[0] == "heading"]
    span_heads = [f for f in spanners if caps_short(f.words)]
    first_note_v = min(ev[1]["start"].cv for col in note_cols
                       for ev in col["events"] if ev[0] == "note")
    body = statistics.median(col["body"] for col in note_cols)
    all_heads = heads + span_heads
    titles = set()
    for h in all_heads:
        if h.cv >= first_note_v:
            continue
        others = [o.size for o in all_heads if o is not h]
        if (h.spanning
                or (others and h.size >= TITLE_OVER_HEADS * statistics.median(others))
                or (not others and h.size >= TITLE_OVER_BODY * body)):
            titles.add(id(h))

    # ---- text that runs on from the foot of the previous column ----------- #
    for i in range(1, len(note_cols)):
        prev, cur = note_cols[i - 1], note_cols[i]
        lead = []
        for ev in cur["events"]:
            if ev[0] != "loose":
                break
            lead.append(ev[1])
        last = prev["events"][-1] if prev["events"] else None
        if lead and last is not None and last[0] == "note":
            n = last[1]
            if all(abs(f.size - prev["body"]) <= 0.08 * prev["body"]
                   and f.bold == prev["bold"] for f in lead):
                for f in lead:
                    n["words"].extend(f.words)

    # ---- build -------------------------------------------------------------- #
    out = []
    for ci, col in enumerate(note_cols):
        heading = ""                    # never carried over from another column
        for ev in col["events"]:
            if ev[0] == "heading":
                if id(ev[1]) not in titles:
                    heading = ev[1].text
            elif ev[0] == "note":
                n = ev[1]
                words = n["words"]
                text = " ".join(w.text for w in words)
                out.append(Note(sheet=sheet, page=page_no, column=ci,
                                heading=heading, number=n["number"], text=text,
                                bbox=frame.box_of([n["start"].words[0]] + words
                                                  if words else n["start"].words),
                                topics=topics_of(text)))
    return out


def _merge_lines(frags) -> list:
    """Fragments of one column -> its lines (fragments sharing a band are
    one line), top to bottom."""
    fs = sorted(frags, key=lambda f: (f.cv, f.u0))
    lines: list = []
    for f in fs:
        if lines and abs(f.cv - lines[-1].cv) <= 0.5 * max(f.h, 1.0):
            lines[-1] = Frag(sorted(lines[-1].words + f.words, key=lambda w: w.u0))
        else:
            lines.append(f)
    return lines


def _classify(lines):
    """One column's lines -> events ``("heading", Frag)``, ``("note",
    {...})``, ``("loose", Frag)``; None when the column holds no numbered
    note."""
    starts = [ln for ln in lines if note_number(ln.words[0].text) is not None]
    if not starts:
        return None
    body_words = [w for ln in starts for w in ln.words[1:]]
    if not body_words:
        body_words = [w for ln in lines for w in ln.words]
    body = statistics.median(w.size for w in body_words)
    body_bold = sum(1 for w in body_words if w.bold) > 0.5 * len(body_words)
    pitches = [b.cv - a.cv for a, b in zip(lines, lines[1:]) if b.cv - a.cv > 0.5]
    pitch = statistics.median(pitches) if pitches else 1.3 * body
    tol = 0.35 * body
    events: list = []
    cur = None
    for i, ln in enumerate(lines):
        first = ln.words[0]
        num = note_number(first.text)
        if num is not None and (cur is None or first.u0 < cur["body_u0"] - tol):
            rest = ln.words[1:]
            cur = {"number": num, "start": ln, "words": list(rest),
                   "body_u0": rest[0].u0 if rest else first.u1 + 0.5 * body}
            events.append(("note", cur))
            continue
        if num is None and caps_short(ln.words):
            gap = ln.cv - lines[i - 1].cv if i else float("inf")
            placed = (cur is None or ln.u0 < cur["body_u0"] - tol
                      or gap > 1.15 * pitch)
            styled = (ln.bold and not body_bold) or ln.size > LARGER * body
            nxt = lines[i + 1] if i + 1 < len(lines) else None
            follows = (nxt is not None
                       and note_number(nxt.words[0].text) in ("1", "A"))
            if placed and (styled or follows):
                events.append(("heading", ln))
                cur = None
                continue
        if cur is not None:
            cur["words"].extend(ln.words)          # a line of the open note
        else:
            events.append(("loose", ln))
    return {"events": events, "body": body, "bold": body_bold}
