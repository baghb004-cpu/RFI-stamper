"""The Weaver: Planloom's typed-command drafting agent (GUI-free).

The one who works the loom.  Typed commands drive the Loft drawing through
a FIXED verb table (the Corral standing rules): the parser maps field
words onto a closed set of frames, deterministic engines
(draft.DraftModel, pipewright) do the actual work, and anything the
Weaver cannot parse or resolve is asked about or refused in plain words —
it NEVER guesses a mutation.  Knowledge is data: every lexicon below is a
reviewable table, there is no eval, no exec, and nothing learned can ever
add a verb.

Contract — ``Weaver(model).command(text, context=None)`` returns::

    {"status": "done" | "ask" | "refused",
     "say": str,            # plain words, feet-inches numbers, ALWAYS set
     "question": str|None,  # the ONE clarifying question when status=="ask"
     "options": list[str]|None,   # candidate list on an ambiguity ask
     "changed": int,        # entities created / modified / removed
     "ents": [str, ...],    # ids the command created or modified
     "warnings": [str, ...]}      # engine warnings passed through

plus ``"pending"`` (an opaque frame dict) when status == "ask".  The
caller answers by re-invoking ``command(answer_text,
context={"pending": <that whole return>})`` — the Weaver stitches the
answer into the pending frame itself; the GUI never looks inside it.
``context`` may also carry ``"selection"`` (entity ids), ``"last_point"``
((x, y) model feet) and ``"answer"`` (overrides ``text`` as the answer).
A "done" result MAY additionally carry ``"view"``: ``{"action":
"fit"|"in"|"out"|"goto", "point": (x, y)|None}`` — a view request the
GUI may honor and every other caller can ignore (changed is always 0).

The verb table (fixed; word synonyms live in ``_VERB_LEX``):

    draw / add / place / run   wall | pipe run | fixture | grid | room |
                               room MACRO (a "W by D <name>" phrase) |
                               text | dim   (the object noun picks the frame)
    connect                    a pipe run between two references
    slope                      pitch a run (pipewright.slope_run)
    cap                        cap every open end (pipewright.cap_open_ends)
    replace                    force the fitting at a node (replace_fitting)
    resize                     run diameter, downstream or this-run-only
    reshape / make             re-dimension the last room macro (memory)
    delete / remove / erase    entities by reference
    move                       entities by distance + direction or to a point
    zoom                       view only: fit / in / out / to a reference
    undo / redo                the model's snapshot undo
    check / tally              reporters: piping rule sweep / quantity readout

The room MACRO ("draw a 12 by 10 restroom at B-2 with two lavs, a wc and
a floor drain") builds the whole room as ONE undo step with a fixed,
documented layout: the anchor is the LOWER-LEFT corner; four chained
walls (default stud partition) close the W x D rectangle; a 3'-0" door
lands centered in the anchor-side (south) wall; the room tag sits at the
room center, named from the phrase noun and auto-numbered 101, 102, ...
(the GUI's room-number convention); the listed fixtures line the wall
OPPOSITE the door (the north wall), backs to the wall (rot 180), spaced
3'-0" on center with 1'-6" end clearance from the west corner
(:data:`ROOM_FIX_OC_FT` / :data:`ROOM_FIX_END_FT` — ADA-ish, always
verify against the project code).

Multi-turn memory: the last DONE mutation is stashed on the DraftModel
itself as ``model._weaver_memory`` (a private dict: ``{"kind":
"room_macro"|"batch", "ents": [ids], "macro": {...}}``), because the GUI
builds a fresh Weaver per command.  It powers "make it 14 wide" (reshape
the last room), "move it 2 feet north" / "delete that" (the last batch,
when nothing is selected) and "add another lav" (repeat the last fixture
kind 3'-0" further along the row).  No memory -> an honest ask/refusal.
Undo/redo and deleting the remembered batch clear the stash.

Question lane: an input that starts with no known verb and looks like a
question (ends in "?" or opens with what/how/is/...) is answered, never
drawn.  Slope-minimum questions come straight from pipewright's
MIN_SLOPE table (deterministic, no store needed); anything else quotes
the Heartwood's cited blocks when a store is attached and confident,
and otherwise refers the user to the Old Hand (Ctrl+/) honestly.

Pattern macros (lane 2, gated): :meth:`Weaver.save_macro` snapshots the
last room macro into the Heartwood as an UNVERIFIED note (origin
``"macro"``); "draw a <saved name> at X" replays it ONLY once a human
has trusted that note in the Old Hand's Manage screen.  No store, no
macro — refused politely; an untrusted macro says exactly why it will
not fire.

Target references the resolver understands: ``this/that/it/selected``
(the selection), fixture words (``the wc``, ``the lav`` — nearest to the
last point when given), ``the main`` (largest-diameter run of the
relevant system), ``the open ends`` (pipewright network open ends), grid
addresses (``at B-2``), bare coordinates (``at 10, 20`` in feet,
feet-inches welcome), and raw entity ids (``e0007``).  A genuine tie
between candidates comes back as an "ask" with options — one question at
a time, most-blocking slot first.

Every mutating command lands as ONE undo step no matter how many entities
it touched (the multi-add batch is resealed onto the model's snapshot
undo, mirroring pipewright's one-snapshot commands).

Fully offline, stdlib only.  pipewright imports lazily; heartwood is
OPTIONAL — pass ``heartwood=<store path>`` to turn on thesaurus synonym
expansion, lane-1 phrase memory (successful phrase -> frame key via the
feedback log) and clarification-taught synonym PROPOSALS (never
auto-approved — the Apprentice rule).  Every learning hook is guarded:
learning can never break commanding.
"""
from __future__ import annotations

import difflib
import json
import math
import re

from .draft import (STENCILS, WALL_TYPES, fmt_ftin, grid_points, parse_ftin)

# ------------------------------------------------------------- the lexicons -
#
# The Corral: these tables are DATA.  The parser can only land on the verbs
# below; imported knowledge, thesaurus rows and learned phrases can steer a
# word toward a table entry but can never mint a new verb or a new action.

#: verb word -> canonical verb (the fixed table).
_VERB_LEX: dict[str, str] = {
    "draw": "draw", "add": "draw", "place": "draw", "put": "draw",
    "insert": "draw", "stamp": "draw",
    "run": "run", "route": "run",
    "connect": "connect", "tie": "connect", "join": "connect",
    "slope": "slope", "pitch": "slope", "grade": "slope", "fall": "slope",
    "cap": "cap", "plug": "cap",
    "replace": "replace", "swap": "replace", "change": "replace",
    "resize": "resize", "upsize": "resize", "downsize": "resize",
    "size": "resize",
    "delete": "delete", "remove": "delete", "erase": "delete",
    "move": "move", "shift": "move", "slide": "move",
    "dimension": "draw", "label": "draw", "note": "draw",
    "make": "reshape", "reshape": "reshape",
    "zoom": "zoom",
    "undo": "undo", "redo": "redo",
    "check": "check", "audit": "check",
    "tally": "tally", "count": "tally", "takeoff": "tally",
}

#: one worked example per canonical verb (refusal help + the command bar).
FRAME_EXAMPLES: dict[str, str] = {
    "run": 'run 4" sanitary from the wc to the main at 1/8 per foot',
    "draw": "draw a wall from 0,0 to 20,0 then to 20,12",
    "connect": "connect the lav to the main",
    "slope": "slope this run at 1/4",
    "cap": "cap the open ends",
    "replace": "replace that wye with a combo",
    "resize": 'resize the main to 6"',
    "reshape": "make it 14 wide",
    "delete": "delete the wc",
    "move": "move the wc 2' north",
    "zoom": "zoom to the wc",
    "undo": "undo",
    "redo": "redo",
    "check": "check",
    "tally": "tally",
}

#: leading words stripped before verb dispatch.
_FILLERS = {"please", "now", "then", "ok", "okay", "hey", "kindly",
            "planloom", "weaver", "go", "just"}

#: system word -> pipewright system key (searched longest phrase first).
_SYSTEM_LEX: dict[str, str] = {
    "sanitary": "san", "san": "san", "waste": "san", "sewer": "san",
    "soil": "san", "dwv": "san",
    "vent": "vent",
    "storm": "storm", "roof drain": "storm", "rd": "storm",
    "rainwater": "storm", "rain water": "storm",
    "domestic cold water": "dcw", "domestic cold": "dcw",
    "cold water": "dcw", "cw": "dcw", "dcw": "dcw", "cold": "dcw",
    "domestic hot water": "dhw", "domestic hot": "dhw",
    "hot water": "dhw", "hw": "dhw", "dhw": "dhw", "hot": "dhw",
    "fuel gas": "gas", "natural gas": "gas", "gas": "gas",
}

#: fixture word -> stencil key.  Exact stencil keys always win; label words
#: and trade slang follow ("sink" lands on the single-bowl sink stencil,
#: "lav" stays a lavatory — the drawings tell them apart, so do we).
_FIXTURE_LEX: dict[str, str] = {
    "wc": "wc", "water closet": "wc", "toilet": "wc", "commode": "wc",
    "closet": "wc",
    "lav": "lav", "lavatory": "lav",
    "sink": "sink_s", "single sink": "sink_s", "single bowl sink": "sink_s",
    "double sink": "sink_d", "double bowl sink": "sink_d",
    "urinal": "ur", "ur": "ur",
    "drinking fountain": "df", "fountain": "df", "df": "df",
    "water heater": "wh", "heater": "wh", "wh": "wh",
    "floor drain": "fd", "fd": "fd",
    "cleanout": "co", "clean out": "co", "co": "co",
    "hose bibb": "hb", "hose bib": "hb", "hb": "hb",
    "shower": "shower",
    "tub": "tub", "bathtub": "tub", "bath tub": "tub",
    "mop sink": "mop", "mop basin": "mop", "mop": "mop",
    "concrete column": "col_conc",
    "steel column": "col_steel", "column": "col_steel",
}

#: fitting word -> pipewright OVERRIDE_KINDS key (longest phrase first;
#: hyphens read as spaces here so "p-trap" and "p trap" both land).
_KIND_LEX: list[tuple[str, str]] = [
    ("sanitary tee", "santee"), ("san tee", "santee"), ("santee", "santee"),
    ("combination wye", "combo"), ("combination", "combo"),
    ("combo", "combo"),
    ("quarter bend", "elbow90"), ("eighth bend", "elbow45"),
    ("90 elbow", "elbow90"), ("elbow 90", "elbow90"),
    ("elbow90", "elbow90"), ("ninety", "elbow90"),
    ("45 elbow", "elbow45"), ("elbow 45", "elbow45"),
    ("elbow45", "elbow45"), ("forty five", "elbow45"),
    ("closet flange", "closet-flange"), ("flange", "closet-flange"),
    ("p trap", "ptrap"), ("ptrap", "ptrap"), ("trap", "ptrap"),
    ("clean out", "cleanout"), ("cleanout", "cleanout"), ("co", "cleanout"),
    ("coupling", "coupling"),
    ("cross", "cross"),
    ("wye", "wye"),
    ("tee", "tee"),
    ("elbow", "elbow90"), ("ell", "elbow90"),
    ("90", "elbow90"), ("45", "elbow45"),
    ("cap", "cap"), ("plug", "cap"),
    ("open ends", "open"), ("open end", "open"), ("open", "open"),
]

#: fitting kind -> the way it is said out loud.
_FIT_SAY = {"elbow90": "90 ell", "elbow45": "45 ell", "tee": "tee",
            "santee": "san tee", "wye": "wye", "combo": "combo",
            "cross": "cross", "cap": "cap", "cleanout": "cleanout",
            "ptrap": "p-trap", "closet-flange": "closet flange",
            "coupling": "coupling", "fixture": "fixture connection"}

#: kind noun -> entity kind, for target references like "the wall".
_KIND_NOUNS = {"wall": "wall", "walls": "wall", "pipe": "pipe",
               "pipes": "pipe", "run": "pipe", "runs": "pipe",
               "line": "pipe", "lines": "pipe", "fixture": "fixture",
               "fixtures": "fixture", "grid": "grid", "grids": "grid",
               "room": "room", "rooms": "room", "text": "text",
               "note": "text", "notes": "text", "dim": "dim",
               "dims": "dim", "dimension": "dim", "dimensions": "dim",
               "door": "door", "doors": "door", "window": "window",
               "windows": "window"}

_SELECT_WORDS = {"this", "that", "it", "these", "those", "selected",
                 "selection"}

_NUM_WORDS = {"zero": 0.0, "one": 1.0, "two": 2.0, "three": 3.0,
              "four": 4.0, "five": 5.0, "six": 6.0, "seven": 7.0,
              "eight": 8.0, "nine": 9.0, "ten": 10.0, "eleven": 11.0,
              "twelve": 12.0}

_FRAC_WORDS = {"half": 0.5, "halves": 0.5, "quarter": 0.25,
               "quarters": 0.25, "eighth": 0.125, "eighths": 0.125,
               "sixteenth": 0.0625, "sixteenths": 0.0625}

_COUNT_WORDS = {"a": 1.0, "an": 1.0, "one": 1.0, "two": 2.0,
                "three": 3.0, "four": 4.0, "five": 5.0}

_DIRS = {"north": (0.0, 1.0), "south": (0.0, -1.0), "east": (1.0, 0.0),
         "west": (-1.0, 0.0), "up": (0.0, 1.0), "down": (0.0, -1.0),
         "right": (1.0, 0.0), "left": (-1.0, 0.0)}

_ORDINALS = {"1": 0, "first": 0, "one": 0, "2": 1, "second": 1, "two": 1,
             "3": 2, "third": 2, "three": 2, "4": 3, "fourth": 3,
             "four": 3, "5": 4, "fifth": 4, "five": 4}

_QUOTE_MAP = str.maketrans({
    "‘": "'", "’": "'", "′": "'",
    "“": '"', "”": '"', "″": '"',
})

_NUMWORD_ALT = "|".join(_NUM_WORDS)

#: a trade size in inches: 4" / 4 inch / four inch / 1 1/2" / 3/4".  The
#: lookbehind keeps it out of feet-inches coordinates like 22'-6".
_SIZE_RE = re.compile(
    r"(?<![\d'\"/\-.])"
    r"(\d+\s+\d+\s*/\s*\d+|\d+\s*/\s*\d+|\d+(?:\.\d+)?|" + _NUMWORD_ALT +
    r")[\s-]*(?:\"|inch(?:es)?\b|in\b)")

#: the per-foot anchor of a slope phrase.
_SLOPE_ANCHOR = re.compile(r"(?:per\s+foot\b|per\s+ft\b|/\s*ft\b|"
                           r"(?:an?|per)\s+lineal\s+foot\b)")

#: the value tokens directly before the anchor.
_SLOPE_HEAD = re.compile(
    r"(?:\bat\s+)?(?:\b(an?|one|two|three|four|five)\s+)?"
    r"(\d+\s*/\s*\d+|\d*\.\d+|\d+|half|halves|quarter|quarters|eighth|"
    r"eighths|sixteenth|sixteenths)\s*"
    r"(?:\"|inch(?:es)?|in\.?)?\s*$")

_GRID_ADDR = re.compile(r"^([a-z]{1,2})\s*[-/ ]?\s*(\d{1,3})$")
_ENT_ID = re.compile(r"^e\d{4}$")

# --- the room macro's fixed layout numbers (documented in the module
# --- docstring; ADA-ish defaults — always verify against the project code).
ROOM_DOOR_WIDTH_IN = 36.0   # the 3'-0" default door
ROOM_FIX_OC_FT = 3.0        # fixture spacing, on center
ROOM_FIX_END_FT = 1.5       # end clearance to the first fixture center

#: one plan dimension: 12 / 12.5 / 12' / 12'-6" (unit words handled around it)
_DIM_TOK = r"\d+(?:\.\d+)?(?:\s*'(?:\s*-?\s*\d+(?:\s+\d+/\d+)?\s*\")?)?"

#: "12 by 10 restroom ..." — the room-macro trigger.
_ROOM_MACRO_RE = re.compile(
    rf"^(?:an?\s+|the\s+)?({_DIM_TOK})\s*(?:feet|foot|ft)?"
    rf"\s*(?:by|x|×)\s*({_DIM_TOK})\s*(?:feet|foot|ft)?\s+(.+)$")

#: words that describe the wall assembly, stripped from a room name.
_WALL_WORDS = {"stud", "cmu", "block", "masonry", "concrete", "conc",
               "furring", "furred"}

#: openers that mark a question when the first word is not a verb.
_QUESTION_HEADS = {"what", "whats", "what's", "which", "who", "whose",
                   "when", "where", "why", "how", "is", "are", "am", "do",
                   "does", "did", "can", "could", "should", "would", "will",
                   "must"}

_EPS = 1e-9


# ---------------------------------------------------------------- helpers ---

def _norm(text) -> str:
    """Lowercased, quote-normalized, whitespace-collapsed command text."""
    s = ("" if text is None else str(text)).translate(_QUOTE_MAP)
    return " ".join(s.lower().split())


def _fmt_pt(pt) -> str:
    return f"({fmt_ftin(pt[0])}, {fmt_ftin(pt[1])})"


def _num_value(tok) -> float | None:
    """A bare number token: digits, decimal, fraction, or a number word."""
    t = _norm(tok)
    if t in _NUM_WORDS:
        return _NUM_WORDS[t]
    m = re.fullmatch(r"(\d+)\s+(\d+)\s*/\s*(\d+)", t)
    if m and int(m.group(3)):
        return int(m.group(1)) + int(m.group(2)) / int(m.group(3))
    m = re.fullmatch(r"(\d+)\s*/\s*(\d+)", t)
    if m and int(m.group(2)):
        return int(m.group(1)) / int(m.group(2))
    try:
        return float(t)
    except ValueError:
        return None


def _size_value(text) -> float | None:
    """A diameter answer in inches: 6 / 6" / 6 inch / 1 1/2 / three inch."""
    s = _norm(text)
    s = re.sub(r"\s*(?:\"|inch(?:es)?|in\.?)\s*$", "", s).strip()
    s = re.sub(r"^(?:to\s+|a\s+|an\s+)", "", s).strip()
    return _num_value(s)


def _slope_value(text) -> float | None:
    """A slope answer in inches per foot: 1/8, an eighth, quarter inch,
    0.125, 1/4 per foot..."""
    s = _norm(text)
    s = re.sub(r"\s*(?:per\s+foot|per\s+ft|/\s*ft|a\s+foot)\s*$", "", s)
    s = re.sub(r"\s*(?:\"|inch(?:es)?|in\.?)\s*$", "", s).strip()
    s = re.sub(r"^(?:at\s+)", "", s).strip()
    m = re.fullmatch(r"(?:(an?|one|two|three|four|five)\s+)?"
                     r"(half|halves|quarter|quarters|eighth|eighths|"
                     r"sixteenth|sixteenths)", s)
    if m:
        count = _COUNT_WORDS.get(m.group(1) or "a", 1.0)
        return count * _FRAC_WORDS[m.group(2)]
    return _num_value(s)


def _extract_slope(text: str):
    """Find a ``<value> per foot`` phrase; returns (in/ft | None, text
    with the phrase removed)."""
    m = _SLOPE_ANCHOR.search(text)
    if not m:
        return None, text
    head = text[:m.start()]
    hm = _SLOPE_HEAD.search(head)
    if not hm:
        return None, text
    count = _COUNT_WORDS.get(hm.group(1) or "", 1.0) if hm.group(1) else None
    base = hm.group(2)
    if base in _FRAC_WORDS:
        val = (count or 1.0) * _FRAC_WORDS[base]
    else:
        val = _num_value(base)
        if val is None:
            return None, text
    rest = (text[:hm.start()] + " " + text[m.end():]).strip()
    rest = re.sub(r"\s+at\s*$", "", rest).strip()
    return val, " ".join(rest.split())


def _extract_size(text: str):
    """Find a trade size in inches; returns (inches | None, text with the
    size phrase removed)."""
    m = _SIZE_RE.search(text)
    if not m:
        return None, text
    val = _num_value(m.group(1))
    if val is None:
        return None, text
    rest = (text[:m.start()] + " " + text[m.end():]).strip()
    return val, " ".join(rest.split())


def _find_phrase(text: str, lex: dict):
    """Longest lexicon phrase present in text (word-bounded) ->
    (value, phrase) or (None, None)."""
    for phrase in sorted(lex, key=len, reverse=True):
        if re.search(rf"\b{re.escape(phrase)}\b", text):
            return lex[phrase], phrase
    return None, None


def _fitting_kind(text) -> str | None:
    s = " " + _norm(text).replace("-", " ") + " "
    for phrase, kind in _KIND_LEX:
        if f" {phrase} " in s:
            return kind
    return None


def _wall_type(text: str) -> str:
    """Wall assembly from label fragments; stud partition is the default."""
    m = re.search(r"\b(stud|cmu|block|masonry|concrete|conc|furr\w*)\b",
                  text)
    if not m:
        return "stud4"
    word = m.group(1)
    if word.startswith("furr"):
        return "furr"
    window = text[max(0, m.start() - 14):m.end() + 8]
    nums = [int(n) for n in re.findall(r"\b(\d{1,2})\b", window)]
    if word == "stud":
        return "stud6" if 6 in nums else "stud4"
    if word in ("cmu", "block", "masonry"):
        return "cmu12" if 12 in nums else "cmu8"
    return "conc12" if 12 in nums else "conc8"


def _closest_on_seg(a, b, p):
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 <= _EPS:
        return (a[0], a[1])
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2
    t = min(1.0, max(0.0, t))
    return (a[0] + dx * t, a[1] + dy * t)


def _poly_len(pts) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1])
               for a, b in zip(pts, pts[1:]))


def _route(a, b, straight: bool) -> list:
    """Manhattan two-segment L, horizontal leg first; straight when asked
    or already axis-aligned."""
    a = (float(a[0]), float(a[1]))
    b = (float(b[0]), float(b[1]))
    if straight or abs(a[0] - b[0]) < _EPS or abs(a[1] - b[1]) < _EPS:
        return [a, b]
    return [a, (b[0], a[1]), b]


def _fit_phrase(counts: dict) -> str:
    """{"combo": 1, "wye": 2} -> "2 wyes, 1 combo" (largest count first)."""
    parts = []
    for kind, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        name = _FIT_SAY.get(kind, kind)
        parts.append(f"{n} {name}{'s' if n > 1 else ''}")
    return ", ".join(parts)


def _plural(word: str, n: int) -> str:
    if n == 1:
        return word
    if word.endswith("y") and not word.endswith(("ay", "ey", "oy", "uy")):
        return word[:-1] + "ies"
    if word.endswith(("s", "x", "z", "sh", "ch")):
        return word + "es"
    return word + "s"


def _dim_value(tok) -> float | None:
    """A plan dimension in feet: 12 / 12' / 12'-6" / twelve / 12 feet."""
    s = _norm(tok)
    s = re.sub(r"\s*(?:feet|foot|ft)\s*$", "", s).strip()
    if s in _NUM_WORDS:
        return _NUM_WORDS[s]
    return parse_ftin(s)


def _room_geometry(anchor, w: float, d: float, wtype: str, flat_keys):
    """The room macro's deterministic layout (see the module docstring):
    returns (wall point pairs CCW from the anchor, tag center, fixture
    (x, y) centers along the interior face of the north wall)."""
    x0, y0 = float(anchor[0]), float(anchor[1])
    x1, y1 = x0 + float(w), y0 + float(d)
    walls = [((x0, y0), (x1, y0)),      # south — the anchor side (door)
             ((x1, y0), (x1, y1)),      # east
             ((x1, y1), (x0, y1)),      # north — the fixture wall
             ((x0, y1), (x0, y0))]      # west
    center = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    thick = float(WALL_TYPES.get(wtype, WALL_TYPES["stud4"])["thick_in"])
    fix = []
    for i, key in enumerate(flat_keys):
        depth = float(STENCILS.get(key, {}).get("d_in", 12.0))
        fix.append((x0 + ROOM_FIX_END_FT + ROOM_FIX_OC_FT * i,
                    y1 - (thick / 2.0 + depth / 2.0) / 12.0))
    return walls, center, fix


# --------------------------------------------------------------- the Weaver -

class Weaver:
    """The typed-command agent driving one Loft drawing.

    ``model`` is a :class:`rfi_stamper.draft.DraftModel`; ``heartwood`` is
    an optional store path enabling synonym expansion and lane-1 phrase
    memory (default None = learning off)."""

    def __init__(self, model, heartwood: str | None = None):
        self.model = model
        self._hw_path = heartwood
        self._hw_store = None
        self._hw_dead = False
        self._mem_written = False

    # ------------------------------------------------------- entry point --

    def command(self, text, context=None) -> dict:
        ctx = dict(context or {})
        raw = "" if text is None else str(text)
        frame = self._pending_frame(ctx.get("pending"))
        if frame is not None:
            answer = str(ctx.get("answer") or raw)
            return self._resume(frame, answer, ctx)
        return self._fresh(raw, ctx)

    # ----------------------------------------------------- fresh parsing --

    def _fresh(self, raw: str, ctx: dict) -> dict:
        text = _norm(raw)
        if not text:
            return self._refuse(
                "Say a command — e.g. "
                f"{FRAME_EXAMPLES['run']!r}. Verbs: "
                + ", ".join(sorted(set(_VERB_LEX.values()))) + ".")
        toks = text.split()
        while toks and toks[0] in _FILLERS:
            toks.pop(0)
        if toks and toks[0] not in _VERB_LEX \
                and (text.endswith("?") or toks[0] in _QUESTION_HEADS):
            return self._answer_question(text)
        if not toks or toks[0] not in _VERB_LEX:
            return self._refuse_unknown(text)
        verb = _VERB_LEX[toks[0]]
        rest = " ".join(toks[1:])
        frame = {"_weaver_frame": 1, "verb": verb, "slots": {},
                 "raw": raw, "norm": text}
        parser = getattr(self, "_parse_" + verb, None)
        if parser is not None:
            err = parser(frame, rest, raw)
            if err:
                return self._refuse(err)
        return self._dispatch(frame, ctx)

    def _dispatch(self, frame: dict, ctx: dict) -> dict:
        verb = frame["verb"]
        handler = getattr(self, "_handle_" + verb)
        self._mem_written = False
        out = handler(frame, ctx)
        if out.get("status") == "done":
            self._record(frame)
            # multi-turn memory: remember the last mutating batch (handlers
            # that manage richer memory themselves set _mem_written)
            if verb not in ("delete", "undo", "redo") \
                    and out.get("changed") and out.get("ents") \
                    and not self._mem_written:
                self._remember("batch", out["ents"])
        return out

    # ---------------------------------------------------- multi-turn memory --

    def _memory(self) -> dict | None:
        """The last DONE result's stash, kept on the model itself (the GUI
        builds a fresh Weaver per command): ``model._weaver_memory``."""
        mem = getattr(self.model, "_weaver_memory", None)
        return mem if isinstance(mem, dict) and mem.get("ents") else None

    def _remember(self, kind: str, ents, extra: dict | None = None) -> None:
        try:
            mem = {"kind": str(kind), "ents": [str(i) for i in ents]}
            if extra:
                mem.update(extra)
            self.model._weaver_memory = mem
            self._mem_written = True
        except Exception:
            pass

    def _forget(self) -> None:
        try:
            self.model._weaver_memory = None
        except Exception:
            pass

    def _memory_live(self, mem: dict) -> bool:
        """Every remembered entity still on the drawing?"""
        return all(self.model.entity(str(i)) is not None
                   for i in mem.get("ents", []))

    # ------------------------------------------------------- ask / resume --

    def _pending_frame(self, pend):
        """Accept either the whole prior return or the bare frame."""
        if not isinstance(pend, dict):
            return None
        if pend.get("_weaver_frame"):
            return pend
        inner = pend.get("pending")
        if isinstance(inner, dict) and inner.get("_weaver_frame"):
            return inner
        return None

    def _ask(self, frame, slot, kind, question, options=None, ids=None,
             nodes=None, noun=None) -> dict:
        frame["await"] = {"slot": slot, "kind": kind,
                          "ids": list(ids or []),
                          "nodes": [list(n) for n in (nodes or [])],
                          "noun": noun}
        return {"status": "ask", "say": question, "question": question,
                "options": list(options) if options else None,
                "changed": 0, "ents": [], "warnings": [],
                "pending": frame}

    def _resume(self, frame: dict, answer: str, ctx: dict) -> dict:
        aw = frame.pop("await", None)
        if aw is None:
            return self._refuse("Nothing pending to answer.")
        slot, kind = aw["slot"], aw["kind"]
        a_norm = _norm(answer)
        if not a_norm:
            frame["await"] = aw
            return {"status": "ask", "say": "Still need an answer: ",
                    "question": "Still waiting — " + slot.replace("_", " ")
                                + "?",
                    "options": None, "changed": 0, "ents": [],
                    "warnings": [], "pending": frame}

        if kind == "choice":
            picked = self._pick_ent(a_norm, aw["ids"], ctx)
            if picked is None:
                frame["await"] = aw
                q = ("Which one? Answer with a number, an entity id, or "
                     "a point.")
                return {"status": "ask", "say": q, "question": q,
                        "options": None, "changed": 0, "ents": [],
                        "warnings": [], "pending": frame}
            frame["slots"][slot] = picked
            if aw.get("noun"):
                self._propose_from_ent(aw["noun"], picked)
        elif kind == "node":
            picked = self._pick_node(a_norm, aw["nodes"], ctx)
            if picked is None:
                frame["await"] = aw
                q = "Which one? Answer with a number or a point."
                return {"status": "ask", "say": q, "question": q,
                        "options": None, "changed": 0, "ents": [],
                        "warnings": [], "pending": frame}
            frame["slots"][slot] = list(picked)
        elif kind == "stencil":
            key = self._stencil_of(a_norm)
            if key is None:
                frame["await"] = aw
                q = ("Which fixture — e.g. "
                     + ", ".join(sorted(set(_FIXTURE_LEX.values()))[:6])
                     + "?")
                return {"status": "ask", "say": q, "question": q,
                        "options": None, "changed": 0, "ents": [],
                        "warnings": [], "pending": frame}
            frame["slots"][slot] = key
            if aw.get("noun"):
                self._propose(aw["noun"], self._stencil_word(key))
        elif kind == "slope":
            val = _slope_value(a_norm)
            if val is None:
                frame["await"] = aw
                q = 'At what pitch — e.g. 1/8 or 1/4 per foot?'
                return {"status": "ask", "say": q, "question": q,
                        "options": None, "changed": 0, "ents": [],
                        "warnings": [], "pending": frame}
            frame["slots"][slot] = val
        elif kind == "size":
            val = _size_value(a_norm)
            if val is None or val <= 0:
                frame["await"] = aw
                q = 'To what size — e.g. 4" or 6 inch?'
                return {"status": "ask", "say": q, "question": q,
                        "options": None, "changed": 0, "ents": [],
                        "warnings": [], "pending": frame}
            frame["slots"][slot] = val
        elif kind == "dist":
            val = (_NUM_WORDS.get(a_norm)
                   if a_norm in _NUM_WORDS else parse_ftin(a_norm))
            if val is None or val <= 0:
                frame["await"] = aw
                q = "How far — e.g. 2' or 6\"?"
                return {"status": "ask", "say": q, "question": q,
                        "options": None, "changed": 0, "ents": [],
                        "warnings": [], "pending": frame}
            frame["slots"][slot] = val
        elif kind == "kind":
            k = _fitting_kind(a_norm)
            if k is None:
                frame["await"] = aw
                q = ("Replace it with what — wye, combo, san tee, tee, "
                     "cleanout, cap, or coupling?")
                return {"status": "ask", "say": q, "question": q,
                        "options": None, "changed": 0, "ents": [],
                        "warnings": [], "pending": frame}
            frame["slots"][slot] = k
        elif kind == "text":
            frame["slots"][slot] = answer.strip().strip('"').strip()
        elif kind == "object":
            # re-dispatch the whole answer as the body of the draw command
            return self._fresh("draw " + answer, ctx)
        elif kind == "points":
            frame["slots"]["points_c"] = self._split_chain(
                re.sub(r"^\s*from\s+", "", a_norm))
        else:  # "target": a point / run / fixture reference clause
            clause = re.sub(r"^(?:to|from|at)\s+", "", a_norm).strip()
            frame["slots"][slot + "_c"] = clause
            frame["slots"].pop(slot, None)
        return self._dispatch(frame, ctx)

    # ------------------------------------------------- answer disambiguation

    def _pick_ent(self, answer: str, ids: list, ctx: dict):
        toks = answer.split()
        for i in ids:
            if i in toks or i == answer:
                return i
        key = answer
        if key.startswith("the "):
            key = key[4:]
        if key in _ORDINALS and _ORDINALS[key] < len(ids):
            return ids[_ORDINALS[key]]
        # a fixture word narrows the field
        skey = self._stencil_of(answer)
        if skey:
            hits = [i for i in ids
                    if (e := self.model.entity(i)) is not None
                    and e.props.get("stencil") == skey]
            if len(hits) == 1:
                return hits[0]
        # a point (grid or coords) picks the nearest candidate
        pt = self._literal_point(answer)
        if pt is not None:
            best, best_d = None, None
            for i in ids:
                e = self.model.entity(i)
                if e is None or not e.pts:
                    continue
                d = math.hypot(e.pts[0][0] - pt[0], e.pts[0][1] - pt[1])
                if best_d is None or d < best_d:
                    best, best_d = i, d
            return best
        return None

    def _pick_node(self, answer: str, nodes: list, ctx: dict):
        key = answer
        if key.startswith("the "):
            key = key[4:]
        if key in _ORDINALS and _ORDINALS[key] < len(nodes):
            return nodes[_ORDINALS[key]]
        pt = self._literal_point(answer)
        if pt is None:
            ref = self._fixture_ents(answer)
            if len(ref) == 1:
                pt = ref[0].pts[0]
        if pt is not None:
            return min(nodes, key=lambda n: math.hypot(n[0] - pt[0],
                                                       n[1] - pt[1]))
        return None

    # ------------------------------------------------------ result shapes --

    def _done(self, say, changed=0, ents=(), warnings=()) -> dict:
        return {"status": "done", "say": str(say), "question": None,
                "options": None, "changed": int(changed),
                "ents": [str(i) for i in ents],
                "warnings": [str(w) for w in warnings]}

    def _refuse(self, say) -> dict:
        return {"status": "refused", "say": str(say), "question": None,
                "options": None, "changed": 0, "ents": [], "warnings": []}

    def _refuse_unknown(self, text: str) -> dict:
        verbs = sorted(set(_VERB_LEX.values()))
        scored = []
        toks = text.split() or [text]
        for v in verbs:
            r = max(difflib.SequenceMatcher(None, v, t).ratio()
                    for t in toks)
            scored.append((r, v))
        scored.sort(key=lambda p: (-p[0], p[1]))
        top = [v for _r, v in scored[:3]]
        return self._refuse(
            "That is not a drawing command I know. Closest verbs: "
            + ", ".join(top)
            + f". Try: {FRAME_EXAMPLES[top[0]]}")

    # ------------------------------------------------------ one-undo batch --

    def _seal(self, depth: int) -> None:
        """Collapse every snapshot the batch pushed into one undo step:
        the FIRST snapshot is the pre-command state, so one Ctrl+Z reverts
        the whole command (mirrors pipewright's one-snapshot commands)."""
        undo = self.model._undo
        if len(undo) > depth + 1:
            del undo[depth + 1:]

    # ------------------------------------------------------- reference land -

    def _anchor(self, ent):
        return tuple(ent.pts[0]) if ent.pts else None

    def _desc(self, ent) -> str:
        if ent.kind == "fixture":
            key = str(ent.props.get("stencil", ""))
            label = STENCILS.get(key, {}).get("label", key or "fixture")
            return label.split(",")[0].lower()
        if ent.kind == "pipe":
            from . import pipewright as pw
            dia = float(ent.props.get("dia_in", 4.0))
            system = str(ent.props.get("system", "san"))
            return f'{pw.fmt_dia_in(dia)}" {system} run'
        if ent.kind == "wall":
            wtype = str(ent.props.get("wtype", ""))
            return (WALL_TYPES.get(wtype, {}).get("label", "wall")).lower()
        return ent.kind

    def _option(self, ent) -> str:
        at = self._anchor(ent)
        loc = f" at {_fmt_pt(at)}" if at else ""
        return f"{self._desc(ent)} {ent.id}{loc}"

    def _grid_point(self, letter: str, number: str):
        want = f"{letter.upper()}/{number.lstrip('0') or '0'}"
        for x, y, label in grid_points(self.model):
            if label.upper() == want:
                return (x, y)
        return None

    def _literal_point(self, clause: str):
        """A grid address or coordinate pair, or None."""
        s = clause.strip().strip(".")
        s = re.sub(r"^(?:at|to|from)\s+", "", s).strip()
        m = _GRID_ADDR.fullmatch(s)
        if m:
            return self._grid_point(m.group(1), m.group(2))
        if "," in s:
            parts = [p.strip() for p in s.split(",")]
            if len(parts) == 2:
                x, y = parse_ftin(parts[0]), parse_ftin(parts[1])
                if x is not None and y is not None:
                    return (x, y)
        return None

    def _stencil_of(self, text: str):
        """Fixture word -> stencil key: exact key, lexicon, thesaurus
        expansion, stencil-label words — in that order."""
        s = _norm(text)
        s = re.sub(r"^(?:the|a|an|that|this)\s+", "", s).strip()
        if s in STENCILS:
            return s
        key, _ = _find_phrase(s, _FIXTURE_LEX)
        if key:
            return key
        for alt in self._expand(s):
            a = _norm(alt)
            if a in STENCILS:
                return a
            key, _ = _find_phrase(a, _FIXTURE_LEX)
            if key:
                return key
        for k, spec in sorted(STENCILS.items()):
            if s and s in _norm(spec.get("label", "")):
                return k
        return None

    def _stencil_word(self, key: str) -> str:
        label = STENCILS.get(key, {}).get("label", key)
        return label.split(",")[0].lower()

    def _fixture_ents(self, clause: str) -> list:
        key = self._stencil_of(clause)
        if key is None:
            return []
        return [e for e in self.model.ents
                if e.kind == "fixture" and e.pts
                and str(e.props.get("stencil", "")) == key]

    def _main_run(self, system: str):
        """The main: the largest-diameter run of the system.  Returns
        (ent | None, tie: bool)."""
        pipes = [e for e in self.model.ents
                 if e.kind == "pipe" and len(e.pts) >= 2
                 and str(e.props.get("system", "san")) == system]
        if not pipes:
            return None, False
        top = max(float(e.props.get("dia_in", 4.0)) for e in pipes)
        best = [e for e in pipes
                if abs(float(e.props.get("dia_in", 4.0)) - top) < 1e-9]
        return (best[0], False) if len(best) == 1 else (best, True)

    def _selection_ents(self, ctx: dict, kind: str | None = None) -> list:
        out = []
        for i in ctx.get("selection") or []:
            e = self.model.entity(str(i))
            if e is not None and (kind is None or e.kind == kind):
                out.append(e)
        return out

    def _resolve_end(self, clause: str, ctx: dict, frame: dict, slot: str):
        """A pipe endpoint reference -> ("pt", (x, y)) | ("run", id) |
        ("ask", result) | ("err", msg)."""
        s = _norm(clause).strip().strip(".")
        s = re.sub(r"^(?:at|to|from)\s+", "", s).strip()
        toks = set(s.split())
        if toks & _SELECT_WORDS:
            sel = self._selection_ents(ctx)
            if not sel:                    # fall back to the last batch
                mem = self._memory()
                if mem:
                    sel = [e for i in mem["ents"]
                           if (e := self.model.entity(str(i))) is not None
                           and e.pts]
            if not sel:
                return "err", "Nothing is selected."
            e = sel[0]
            if e.kind == "pipe":
                return "run", e.id
            at = self._anchor(e)
            if at is None:
                return "err", f"{e.id} has no point to connect to."
            return "pt", at
        if s in ("here", "there"):
            lp = ctx.get("last_point")
            if lp is None:
                return "err", "No last point to use for 'here'."
            return "pt", (float(lp[0]), float(lp[1]))
        if _ENT_ID.fullmatch(s):
            e = self.model.entity(s)
            if e is None:
                return "err", f"No entity {s} on the drawing."
            if e.kind == "pipe":
                return "run", e.id
            at = self._anchor(e)
            return ("pt", at) if at else ("err",
                                          f"{s} has no point to use.")
        bare = re.sub(r"^(?:the|a|an)\s+", "", s).strip()
        if bare in ("main", "san main", "sewer main", "storm main"):
            system = frame["slots"].get("system") or "san"
            if bare.startswith(("sewer", "san")):
                system = "san"
            elif bare.startswith("storm"):
                system = "storm"
            main, tie = self._main_run(system)
            if main is None:
                return "err", f"No {system} run on the drawing to call " \
                              "the main."
            if tie:
                opts = [self._option(e) for e in main]
                return "ask", self._ask(
                    frame, slot, "choice",
                    "Which main — " + " or ".join(opts) + "?",
                    options=opts, ids=[e.id for e in main])
            return "run", main.id
        pt = self._literal_point(s)
        if pt is not None:
            return "pt", pt
        if _GRID_ADDR.fullmatch(bare):
            return "err", (f"No grid intersection {bare.upper()} on the "
                           "drawing.")
        # a fixture word (known, or resolved through the thesaurus)
        key = self._stencil_of(bare)
        if key is not None:
            cands = self._fixture_ents(bare)
            if not cands:
                return "err", (f"No {self._stencil_word(key)} on the "
                               "drawing.")
            if len(cands) == 1:
                return "pt", self._anchor(cands[0])
            lp = ctx.get("last_point")
            if lp is not None:
                best = min(cands, key=lambda e: math.hypot(
                    e.pts[0][0] - float(lp[0]),
                    e.pts[0][1] - float(lp[1])))
                return "pt", self._anchor(best)
            opts = [self._option(e) for e in cands]
            return "ask", self._ask(
                frame, slot, "choice",
                f"Which {self._stencil_word(key)} — "
                + " or ".join(opts) + "?", options=opts,
                ids=[e.id for e in cands])
        # unknown noun: offer the fixtures on the drawing (a clarification
        # here can teach the thesaurus a new field word)
        fixtures = [e for e in self.model.ents
                    if e.kind == "fixture" and e.pts]
        if fixtures and re.fullmatch(r"[a-z][a-z ]*", bare or " "):
            opts = [self._option(e) for e in fixtures]
            return "ask", self._ask(
                frame, slot, "choice",
                f"I don't know {bare!r} — which fixture do you mean: "
                + " or ".join(opts) + "?", options=opts,
                ids=[e.id for e in fixtures], noun=bare)
        return "err", f"Can't place {clause!r} — give a fixture, " \
                      "a grid address like B-2, or coordinates like 10, 20."

    def _resolve_ents(self, clause: str, ctx: dict, frame: dict, slot: str,
                      kinds: tuple | None = None):
        """Entity references for slope/resize/move/delete ->
        ("ents", [ids]) | ("ask", result) | ("err", msg)."""
        s = _norm(clause).strip().strip(".")
        s = re.sub(r"^(?:the|a|an)\s+(?=\S)", "", s) \
            if not (set(s.split()) & _SELECT_WORDS) else s
        toks = set(s.split())
        if toks & _SELECT_WORDS:
            kind = None
            for t in toks:
                if t in _KIND_NOUNS:
                    kind = _KIND_NOUNS[t]
            sel = self._selection_ents(ctx, kind)
            if kinds:
                sel = [e for e in sel if e.kind in kinds]
            if not sel:                    # fall back to the last batch
                mem = self._memory()
                if mem:
                    sel = [e for i in mem["ents"]
                           if (e := self.model.entity(str(i))) is not None
                           and (kind is None or e.kind == kind)
                           and (not kinds or e.kind in kinds)]
            if not sel:
                return "err", "Nothing (matching) is selected."
            return "ents", [e.id for e in sel]
        if _ENT_ID.fullmatch(s):
            e = self.model.entity(s)
            if e is None:
                return "err", f"No entity {s} on the drawing."
            return "ents", [e.id]
        if s.startswith("main") or " main" in f" {s}":
            system, _ = _find_phrase(s, _SYSTEM_LEX)
            main, tie = self._main_run(system
                                       or frame["slots"].get("system")
                                       or "san")
            if main is None:
                return "err", "No run on the drawing to call the main."
            if tie:
                opts = [self._option(e) for e in main]
                return "ask", self._ask(
                    frame, slot, "choice",
                    "Which main — " + " or ".join(opts) + "?",
                    options=opts, ids=[e.id for e in main])
            return "ents", [main.id]
        cands = self._fixture_ents(s)
        if not cands:
            plural = False
            kind = None
            for t in s.split():
                if t in _KIND_NOUNS:
                    kind = _KIND_NOUNS[t]
                    plural = t.endswith("s")
            if kind:
                system, _ = _find_phrase(s, _SYSTEM_LEX)
                cands = [e for e in self.model.ents if e.kind == kind]
                if system and kind == "pipe":
                    cands = [e for e in cands
                             if str(e.props.get("system")) == system]
                if plural and cands:
                    return "ents", [e.id for e in cands]
        if kinds:
            cands = [e for e in cands if e.kind in kinds]
        if len(cands) == 1:
            return "ents", [cands[0].id]
        if len(cands) > 1:
            lp = ctx.get("last_point")
            with_pts = [e for e in cands if e.pts]
            if lp is not None and with_pts:
                best = min(with_pts, key=lambda e: math.hypot(
                    e.pts[0][0] - float(lp[0]),
                    e.pts[0][1] - float(lp[1])))
                return "ents", [best.id]
            opts = [self._option(e) for e in cands]
            return "ask", self._ask(
                frame, slot, "choice",
                "Which one — " + " or ".join(opts) + "?",
                options=opts, ids=[e.id for e in cands])
        return "err", f"Nothing on the drawing matches {clause!r}."

    # ------------------------------------------------------------- parsing --

    def _split_chain(self, clause: str) -> list:
        """"0,0 to 20,0 then to 20,12" -> point clause list."""
        first, _, rest = clause.partition(" to ")
        out = [first.strip()]
        if rest:
            out += [p.strip() for p in
                    re.split(r"\s+then(?:\s+to)?\s+", rest) if p.strip()]
        return [c for c in out if c]

    def _parse_draw(self, frame, rest, raw):
        s = frame["slots"]
        verb_word = frame["norm"].split()[0]
        text = rest
        # the room MACRO first: a "W by D <noun>" phrase claims the frame
        if self._parse_room_macro(s, text):
            return None
        # object nouns decide the frame (fixed dispatch — the Corral)
        if re.search(r"\bwalls?\b", text):
            s["object"] = "wall"
        elif re.search(r"\bgrid\b", text):
            s["object"] = "grid"
        elif re.search(r"\broom\b", text):
            s["object"] = "room"
        elif verb_word == "dimension" or re.search(
                r"\bdim(?:ension)?s?\b", text):
            s["object"] = "dim"
        elif verb_word in ("label", "note") or re.search(
                r"\b(?:text|note|label)\b", text):
            s["object"] = "text"
        else:
            head = text.split(" at ")[0].split(" from ")[0]
            skey = self._stencil_of(head) if head.strip() else None
            if skey is not None:
                s["object"] = "fixture"
                s["stencil"] = skey
                if re.search(r"\banother\b|\bone more\b", text):
                    s["another"] = True
            else:
                system, _ = _find_phrase(text, _SYSTEM_LEX)
                if system or re.search(r"\b(?:pipe|line|run)\b", text):
                    s["object"] = "pipe"
                elif re.search(r"\banother\b|\bone more\b", text):
                    s["object"] = "fixture"   # repeat the remembered kind
                    s["another"] = True
                else:
                    claim = self._parse_saved_macro(s, text)
                    if isinstance(claim, str):
                        return claim          # a macro on file, not trusted
                    if not claim:
                        s["object"] = None    # ask "draw what?"
                    return None
        if s["object"] == "pipe":
            self._parse_pipe_slots(s, text)
        elif s["object"] == "wall":
            s["wtype"] = _wall_type(text)
            m = re.search(r"\bfrom\s+(.+)$", text)
            if m:
                s["points_c"] = self._split_chain(m.group(1))
        elif s["object"] == "fixture":
            m = re.search(r"\bat\s+(.+)$", text)
            if m:
                s["at_c"] = m.group(1).strip()
        elif s["object"] == "grid":
            m = re.search(r"\bgrid(?:\s+line)?\s+([a-z]{1,2}|\d{1,3})"
                          r"\s+from\b", text)
            if m:
                s["label"] = m.group(1)
            m = re.search(r"\bfrom\s+(.+?)\s+to\s+(.+)$", text)
            if m:
                s["from_c"], s["to_c"] = m.group(1), m.group(2)
        elif s["object"] == "room":
            m = re.search(r"\broom\s+(?:named\s+|called\s+)?(.+?)"
                          r"\s+at\s+(.+)$", text)
            if m:
                name = m.group(1).strip()
                nm = re.search(r"\s(\d+)$", name)
                if nm:
                    s["number"] = nm.group(1)
                    name = name[:nm.start()].strip()
                s["name"] = name
                s["at_c"] = m.group(2).strip()
            else:
                m = re.search(r"\broom\s+at\s+(.+)$", text)
                if m:
                    s["at_c"] = m.group(1).strip()
        elif s["object"] == "text":
            m = re.search(r"\b(?:text|note|label)\s+(?:saying\s+|"
                          r"reading\s+|that\s+says\s+)?(.+?)\s+at\s+(.+)$",
                          _norm(raw))
            if m:
                # pull the content from the RAW string to keep its case
                rm = re.search(re.escape(m.group(1)), raw, re.IGNORECASE)
                s["content"] = (rm.group(0) if rm
                                else m.group(1)).strip().strip('"')
                s["at_c"] = m.group(2).strip()
            else:
                m = re.search(r"\b(?:text|note|label)\s+at\s+(.+)$", text)
                if m:
                    s["at_c"] = m.group(1).strip()
                else:
                    m = re.search(r"\b(?:text|note|label)\s+(.+)$",
                                  _norm(raw))
                    if m:
                        rm = re.search(re.escape(m.group(1)), raw,
                                       re.IGNORECASE)
                        s["content"] = (rm.group(0) if rm
                                        else m.group(1)).strip().strip('"')
        elif s["object"] == "dim":
            m = re.search(r"\bfrom\s+(.+?)\s+to\s+(.+)$", text)
            if m:
                s["from_c"], s["to_c"] = m.group(1), m.group(2)
        return None

    def _parse_pipe_slots(self, s, text):
        slope, text = _extract_slope(text)
        if slope is not None:
            s["slope"] = slope
        size, text = _extract_size(text)
        if size is not None:
            s["dia_in"] = size
        system, phrase = _find_phrase(text, _SYSTEM_LEX)
        if system:
            s["system"] = system
            text = re.sub(rf"\b{re.escape(phrase)}\b", " ", text)
        if re.search(r"\bstraight\b", text):
            s["straight"] = True
            text = re.sub(r"\bstraight\b", " ", text)
        text = " ".join(text.split())
        m = re.search(r"\bfrom\s+(.+?)\s+to\s+(.+)$", text)
        if m:
            s["from_c"], s["to_c"] = m.group(1).strip(), m.group(2).strip()
        else:
            m = re.search(r"\bfrom\s+(.+)$", text)
            if m:
                s["from_c"] = m.group(1).strip()
            m = re.search(r"\bto\s+(.+)$", text)
            if m:
                s["to_c"] = m.group(1).strip()

    _parse_run = _parse_draw

    def _parse_room_macro(self, s: dict, text: str) -> bool:
        """Claim a "12 by 10 restroom at B-2 with two lavs..." phrase.
        The W x D dimension pair is the trigger; the noun after it names
        the room (wall-assembly words fall out of the name)."""
        m = _ROOM_MACRO_RE.match(text)
        if not m:
            return False
        w, d = _dim_value(m.group(1)), _dim_value(m.group(2))
        if w is None or d is None or w <= 0 or d <= 0:
            return False
        tail = m.group(3).strip()
        mm = re.search(r"\bwith\s+(.+)$", tail)
        if mm:
            s["fixtures_c"] = mm.group(1).strip()
            tail = tail[:mm.start()].strip()
        mm = re.search(r"\bat\s+(.+)$", tail)
        if mm:
            s["at_c"] = mm.group(1).strip()
            tail = tail[:mm.start()].strip()
        elif s.get("fixtures_c"):
            # "... with a wc at B-2" — the anchor rode into the with-clause
            mm = re.search(r"\s+at\s+(.+)$", s["fixtures_c"])
            if mm:
                s["at_c"] = mm.group(1).strip()
                s["fixtures_c"] = s["fixtures_c"][:mm.start()].strip()
        if not s.get("at_c"):
            # a bare "here"/"there" anchor rides without "at"
            mm = re.search(r"\s+(here|there)$", tail)
            if mm:
                s["at_c"] = mm.group(1)
                tail = tail[:mm.start()].strip()
        noun = re.sub(r"^(?:an?|the)\s+", "", tail).strip().strip(".")
        if not noun or not re.fullmatch(r"[a-z][a-z '-]*", noun):
            return False
        base = _KIND_NOUNS.get(noun)
        if base and base != "room":
            return False                    # "12 by 10 wall" is no room
        name = " ".join(t for t in noun.split() if t not in _WALL_WORDS)
        s["object"] = "roommacro"
        s["w"], s["d"] = float(w), float(d)
        s["name"] = name or "room"
        s["wtype"] = _wall_type(tail)
        return True

    def _parse_fixture_list(self, clause: str):
        """"two lavs, a wc and a floor drain" -> ([("lav", 2), ("wc", 1),
        ("fd", 1)], None), or (None, word) on the first unknown word."""
        out: list = []
        for item in re.split(r"\s*,\s*|\s+and\s+|\s*&\s*|\s+plus\s+",
                             _norm(clause)):
            item = item.strip().strip(".")
            if not item:
                continue
            n = 1
            m = re.match(rf"^(\d+|an?|{_NUMWORD_ALT})\s+(.+)$", item)
            if m:
                n = int(m.group(1)) if m.group(1).isdigit() \
                    else int(_NUM_WORDS.get(m.group(1), 1))
                item = m.group(2).strip()
            key = self._stencil_of(item)
            if key is None and item.endswith("ies"):
                key = self._stencil_of(item[:-3] + "y")
            if key is None and item.endswith("es"):
                key = self._stencil_of(item[:-2])
            if key is None and item.endswith("s"):
                key = self._stencil_of(item[:-1])
            if key is None:
                return None, item
            out.append((key, max(1, n)))
        return out, None

    def _parse_saved_macro(self, s: dict, text: str):
        """A trusted pattern macro by its saved name ("draw a standard
        restroom at B-2").  True when claimed, an error string when the
        macro exists but is not trusted (the lane-2 gate), None when no
        macro note matches."""
        m = re.match(r"^(?:an?\s+|the\s+)?(.+?)(?:\s+at\s+(.+))?$", text)
        if not m or not m.group(1).strip():
            return None
        name = m.group(1).strip().strip(".")
        status, payload = self._macro_note(name)
        if status is None:
            return None
        if status != "trusted":
            return (f"The macro {name!r} is on file but not trusted yet — "
                    "trust its note in the Old Hand's Manage screen "
                    "(Ground Truth) and it will draw.")
        if not isinstance(payload, dict) or "w" not in payload \
                or "d" not in payload:
            return (f"The macro {name!r} is trusted but its frame does not "
                    "read back — save it again.")
        s["object"] = "roommacro"
        s["w"], s["d"] = float(payload["w"]), float(payload["d"])
        s["name"] = str(payload.get("name") or name)
        s["wtype"] = str(payload.get("wtype") or "stud4")
        s["fixtures"] = [(str(k), int(n))
                         for k, n in (payload.get("fixtures") or [])]
        if m.group(2):
            s["at_c"] = m.group(2).strip()
        return True

    def _macro_note(self, name):
        """The newest live macro note called ``name`` -> (status, payload
        dict | None); (None, None) when no store or no such macro."""
        store = self._store()
        want = _norm(name)
        if store is None or not want:
            return None, None
        status = payload = None
        try:
            for row in store.notes():
                if str(row["origin"]) != "macro" \
                        or str(row["status"]) == "rejected":
                    continue
                text = str(row["text"])
                first = _norm(text.splitlines()[0])
                if not first.startswith("macro ") \
                        or first[6:].strip() != want:
                    continue
                status = str(row["status"])
                payload = None
                fm = re.search(r"^frame:\s*(\{.*\})\s*$", text,
                               re.MULTILINE)
                if fm:
                    try:
                        payload = json.loads(fm.group(1))
                    except ValueError:
                        payload = None
        except Exception:
            return None, None
        return status, payload

    def _parse_reshape(self, frame, rest, raw):
        s = frame["slots"]
        m = re.search(r"\b(wide|wider|width|deep|deeper|depth|long|longer)"
                      r"\b", rest)
        if m:
            s["dim"] = "w" if m.group(1).startswith("wid") else "d"
            head = rest[:m.start()].strip()
            mm = re.search(rf"(\d[\d'\"./ -]*|{_NUMWORD_ALT})\s*"
                           r"(?:feet|foot|ft)?\s*$", head)
            if mm:
                val = _dim_value(mm.group(1))
                if val is not None and val > 0:
                    s["value"] = val
        return None

    def _parse_zoom(self, frame, rest, raw):
        s = frame["slots"]
        r = re.sub(r"^the\s+", "", " ".join(rest.split()))
        if not r or re.fullmatch(r"(?:to\s+)?(?:fit|extents?|all|"
                                 r"everything|drawing|the drawing)", r):
            s["action"] = "fit"
        elif re.fullmatch(r"in(?:\s+a\s+bit)?", r):
            s["action"] = "in"
        elif re.fullmatch(r"out(?:\s+a\s+bit)?", r):
            s["action"] = "out"
        else:
            s["action"] = "goto"
            s["at_c"] = re.sub(r"^(?:in\s+)?(?:to|on|at)\s+", "", r).strip()
        return None

    def _parse_connect(self, frame, rest, raw):
        s = frame["slots"]
        s["object"] = "pipe"
        slope, rest = _extract_slope(rest)
        if slope is not None:
            s["slope"] = slope
        size, rest = _extract_size(rest)
        if size is not None:
            s["dia_in"] = size
        system, phrase = _find_phrase(rest, _SYSTEM_LEX)
        if system:
            s["system"] = system
            rest = re.sub(rf"\b{re.escape(phrase)}\b", " ", rest)
        rest = re.sub(r"\bwith\b.*$", "", rest)
        rest = " ".join(rest.split())
        m = re.search(r"^(?:from\s+)?(.+?)\s+(?:to|and)\s+(.+)$", rest)
        if m:
            s["from_c"], s["to_c"] = m.group(1).strip(), m.group(2).strip()
        return None

    def _parse_slope(self, frame, rest, raw):
        s = frame["slots"]
        val, rest = _extract_slope(rest)
        if val is None:
            m = re.search(r"\bat\s+(.+)$", rest)
            if m:
                val = _slope_value(m.group(1))
                if val is not None:
                    rest = rest[:m.start()].strip()
        if val is not None:
            s["value"] = val
        target = " ".join(rest.split())
        if target:
            s["target_c"] = target
        return None

    def _parse_cap(self, frame, rest, raw):
        system, _ = _find_phrase(rest, _SYSTEM_LEX)
        if system:
            frame["slots"]["system"] = system
        return None

    def _parse_replace(self, frame, rest, raw):
        s = frame["slots"]
        m = re.search(r"^(?:the\s+|that\s+|this\s+)?(.+?)\s+with\s+"
                      r"(?:a\s+|an\s+)?(.+)$", rest)
        if m:
            s["old_c"] = m.group(1).strip()
            k = _fitting_kind(m.group(2))
            if k:
                s["kind"] = k
            else:
                s["kind_c"] = m.group(2).strip()
        else:
            s["old_c"] = re.sub(r"^(?:the|that|this)\s+", "", rest).strip()
        return None

    def _parse_resize(self, frame, rest, raw):
        s = frame["slots"]
        if re.search(r"\b(?:only|just)\b|\bthis run only\b", rest):
            s["direction"] = "this"
            rest = re.sub(r"\b(?:only|just)\b", " ", rest)
        m = re.search(r"\bto\s+(.+)$", rest)
        if m:
            val = _size_value(m.group(1))
            if val is not None:
                s["dia_in"] = val
                rest = rest[:m.start()]
        if "dia_in" not in s:
            val, rest = _extract_size(rest)
            if val is not None:
                s["dia_in"] = val
        target = " ".join(rest.split())
        if target:
            s["target_c"] = target
        return None

    def _parse_delete(self, frame, rest, raw):
        target = " ".join(rest.split())
        if target:
            frame["slots"]["target_c"] = target
        return None

    def _parse_move(self, frame, rest, raw):
        s = frame["slots"]
        dm = re.search(
            r"(?:\b(" + _NUMWORD_ALT + r"|[\d'\"./ ]+?)\s*"
            r"(?:feet|foot|ft)?\s+)?"
            r"\b(north|south|east|west|up|down|left|right)\b", rest)
        if dm:
            dist_tok = (dm.group(1) or "").strip()
            dist = None
            if dist_tok:
                dist = (_NUM_WORDS.get(dist_tok)
                        if dist_tok in _NUM_WORDS
                        else parse_ftin(dist_tok))
            s["dir"] = dm.group(2)
            if dist is not None:
                s["dist"] = dist
            rest = (rest[:dm.start()] + " " + rest[dm.end():])
        else:
            m = re.search(r"\bto\s+(.+)$", rest)
            if m:
                s["dest_c"] = m.group(1).strip()
                rest = rest[:m.start()]
        target = " ".join(rest.split())
        if target:
            s["target_c"] = target
        return None

    # ------------------------------------------------------------ handlers --

    def _handle_draw(self, frame, ctx):
        obj = frame["slots"].get("object")
        if obj is None:
            return self._ask(frame, "object", "object",
                             "Draw what — a wall, pipe run, fixture, "
                             "grid, room, text, or dim?")
        return getattr(self, "_handle_obj_" + obj)(frame, ctx)

    _handle_run = _handle_draw
    _handle_connect = _handle_draw

    # ---- pipe runs -------------------------------------------------------

    def _handle_obj_pipe(self, frame, ctx):
        s = frame["slots"]
        s["system"] = s.get("system") or "san"
        questions = {
            "from": "Where from — a fixture, a grid address, or "
                    "coordinates like 10, 20?",
            "to": "Where to — the main, a fixture, a grid address, or "
                  "coordinates like 10, 20?",
        }
        for slot in ("from", "to"):        # FROM first: most blocking
            if s.get(slot + "_pt") is not None \
                    or s.get(slot + "_run") is not None:
                continue
            if s.get(slot) is not None:    # a choice ask was answered
                kind, val = self._end_of_ent(s.pop(slot))
            else:
                clause = s.get(slot + "_c")
                if not clause:
                    return self._ask(frame, slot, "target",
                                     questions[slot])
                kind, val = self._resolve_end(clause, ctx, frame, slot)
                if kind == "ask":
                    return val
                if kind == "err":
                    return self._refuse(val)
                s.pop(slot + "_c", None)
            if kind == "run":
                s[slot + "_run"] = val
            else:
                s[slot + "_pt"] = list(val)
        return self._exec_pipe(frame, ctx)

    def _end_of_ent(self, ent_id):
        """A choice-answered endpoint: entity id -> ("run", id)|("pt", xy)."""
        e = self.model.entity(str(ent_id))
        if e is not None and e.kind == "pipe":
            return "run", e.id
        if e is not None and e.pts:
            return "pt", self._anchor(e)
        return "pt", (0.0, 0.0)

    def _tie_point(self, run_ent, near_pt):
        best, best_d = None, None
        for a, b in zip(run_ent.pts, run_ent.pts[1:]):
            c = _closest_on_seg(a, b, near_pt)
            d = math.hypot(c[0] - near_pt[0], c[1] - near_pt[1])
            if best_d is None or d < best_d:
                best, best_d = c, d
        return best

    def _ensure_vertex(self, run_ent, pt) -> bool:
        """Insert pt as a vertex of the run (so the junction derives);
        False when it already sits on a vertex."""
        from . import pipewright as pw
        for v in run_ent.pts:
            if math.hypot(v[0] - pt[0], v[1] - pt[1]) <= pw.MERGE_TOL_FT:
                return False
        pts = list(run_ent.pts)
        for i, (a, b) in enumerate(zip(pts, pts[1:])):
            c = _closest_on_seg(a, b, pt)
            if math.hypot(c[0] - pt[0], c[1] - pt[1]) <= 1e-6:
                pts.insert(i + 1, (float(pt[0]), float(pt[1])))
                self.model.update(run_ent.id, pts=pts)
                return True
        return False

    def _exec_pipe(self, frame, ctx):
        from . import pipewright as pw
        s = frame["slots"]
        model = self.model
        system = s.get("system") or "san"
        dia = float(s.get("dia_in") or pw.SYSTEMS[system]["dia_in"])
        straight = bool(s.get("straight"))
        from_pt = tuple(s["from_pt"]) if s.get("from_pt") else None
        to_pt = tuple(s["to_pt"]) if s.get("to_pt") else None
        from_run = model.entity(s["from_run"]) if s.get("from_run") else None
        to_run = model.entity(s["to_run"]) if s.get("to_run") else None
        if from_run is not None and to_run is not None:
            return self._refuse("Both ends are runs — give at least one "
                                "fixed point to route from.")
        if from_run is not None:
            from_pt = self._tie_point(from_run, to_pt)
        if to_run is not None:
            to_pt = self._tie_point(to_run, from_pt)
        if from_pt is None or to_pt is None:
            return self._refuse("Could not pin both ends down.")
        if math.hypot(to_pt[0] - from_pt[0], to_pt[1] - from_pt[1]) < 1e-6:
            return self._refuse("Those are the same point — nothing to "
                                "run.")
        depth = len(model._undo)
        touched = []
        warnings = []
        for run in (from_run, to_run):
            if run is not None:
                pt = from_pt if run is from_run else to_pt
                if self._ensure_vertex(run, pt):
                    touched.append(run.id)
        route = _route(from_pt, to_pt, straight)
        ent = model.add("pipe", route, system=system, dia_in=dia)
        touched.append(ent.id)
        slope = s.get("slope")
        sr = None
        if slope is not None:
            sr = pw.slope_run(model, ent.id, float(slope))
            warnings += sr.get("warnings", [])
            if sr.get("changed"):
                touched += [d["ent_id"] for d in sr["runs"]]
        self._seal(depth)
        fits: dict[str, int] = {}
        for f in pw.derive_fittings(model):
            if ent.id in f.ent_ids and f.kind not in ("open",):
                fits[f.kind] = fits.get(f.kind, 0) + 1
        label = pw.SYSTEMS[system]["label"].lower()
        say = (f"Ran {fmt_ftin(_poly_len(route))} of "
               f'{pw.fmt_dia_in(dia)}" {label}')
        if slope is not None and sr and sr.get("changed"):
            say += f" at {pw.fmt_slope(float(slope))}"
        if fits:
            say += f" — {_fit_phrase(fits)} derived"
        if slope is not None and sr and sr.get("changed"):
            say += f"; IE drops {sr['total_fall']}."
        else:
            say += "."
        if slope is not None and sr and not sr.get("changed"):
            warnings.append(sr["report"])
        ids = list(dict.fromkeys(touched))
        return self._done(say, changed=len(ids), ents=ids,
                          warnings=warnings)

    # ---- walls -----------------------------------------------------------

    def _handle_obj_wall(self, frame, ctx):
        s = frame["slots"]
        clauses = s.get("points_c") or []
        if len(clauses) < 2:
            return self._ask(frame, "points", "points",
                             "Where does the wall run — from and to "
                             "(coordinates like 0,0 or grid addresses "
                             "like B-2)?")
        pts = []
        for c in clauses:
            kind, val = self._resolve_end(c, ctx, frame, "points")
            if kind == "ask":
                return val
            if kind == "err":
                return self._refuse(val)
            if kind == "run":
                return self._refuse("A wall needs points, not a pipe run.")
            pts.append(tuple(val))
        wtype = s.get("wtype", "stud4")
        depth = len(self.model._undo)
        ids = []
        total = 0.0
        for a, b in zip(pts, pts[1:]):
            if math.hypot(b[0] - a[0], b[1] - a[1]) < 1e-9:
                continue
            w = self.model.add("wall", [a, b], wtype=wtype)
            ids.append(w.id)
            total += math.hypot(b[0] - a[0], b[1] - a[1])
        self._seal(depth)
        if not ids:
            return self._refuse("Those points are all the same spot.")
        label = WALL_TYPES.get(wtype, {}).get("label", wtype)
        say = (f"Drew {len(ids)} wall{'s' if len(ids) > 1 else ''} — "
               f"{fmt_ftin(total)} of {label}.")
        return self._done(say, changed=len(ids), ents=ids)

    # ---- fixtures --------------------------------------------------------

    def _need_point(self, frame, ctx, slot, question):
        """Fill a point slot: ("ok", (x, y)) or ("out", ask/refuse dict)."""
        s = frame["slots"]
        if s.get(slot + "_pt") is not None:
            return "ok", tuple(s[slot + "_pt"])
        if s.get(slot) is not None:        # a choice ask was answered
            kind, val = self._end_of_ent(s.pop(slot))
            if kind == "pt":
                s[slot + "_pt"] = list(val)
                return "ok", tuple(val)
            return "out", self._refuse("That reference is a run, "
                                       "not a point.")
        clause = s.get(slot + "_c")
        if not clause:
            return "out", self._ask(frame, slot, "target", question)
        kind, val = self._resolve_end(clause, ctx, frame, slot)
        if kind == "ask":
            return "out", val
        if kind == "err":
            return "out", self._refuse(val)
        if kind == "run":
            return "out", self._refuse("That reference is a run, "
                                       "not a point.")
        s.pop(slot + "_c", None)
        s[slot + "_pt"] = list(val)
        return "ok", tuple(val)

    def _handle_obj_fixture(self, frame, ctx):
        s = frame["slots"]
        key = s.get("stencil")
        if s.get("another") and not s.get("at_c") \
                and s.get("at_pt") is None and s.get("at") is None:
            return self._repeat_fixture(key)
        if not key:
            return self._ask(frame, "stencil", "stencil",
                             "Which fixture — wc, lav, sink, urinal, "
                             "floor drain, shower, tub...?")
        got, pt = self._need_point(
            frame, ctx, "at",
            f"Where does the {self._stencil_word(key)} go — coordinates "
            "like 10, 20 or a grid address like B-2?")
        if got != "ok":
            return pt
        ent = self.model.add("fixture", [pt], stencil=key)
        say = f"Placed a {self._stencil_word(key)} at {_fmt_pt(pt)}."
        return self._done(say, changed=1, ents=[ent.id])

    def _repeat_fixture(self, key):
        """"add another lav": repeat the remembered fixture kind one row
        spacing (3'-0" o.c., continuing east) past the previous one."""
        mem = self._memory()
        prev = None
        if mem:
            for i in mem["ents"]:
                e = self.model.entity(str(i))
                if e is not None and e.kind == "fixture" and e.pts \
                        and (key is None
                             or str(e.props.get("stencil")) == key):
                    prev = e            # the LAST match = the row's end
        if prev is None:
            word = self._stencil_word(key) if key else "fixture"
            return self._refuse(
                f"No {word} in the last thing I drew to repeat — say "
                f"where instead: add a {word if key else 'lav'} at B-2.")
        key = key or str(prev.props.get("stencil"))
        pt = (prev.pts[0][0] + ROOM_FIX_OC_FT, prev.pts[0][1])
        ent = self.model.add("fixture", [pt], stencil=key,
                             rot=float(prev.props.get("rot", 0.0)),
                             flip=bool(prev.props.get("flip", False)))
        say = (f"Placed another {self._stencil_word(key)} "
               f"{fmt_ftin(ROOM_FIX_OC_FT)} over at {_fmt_pt(pt)}.")
        return self._done(say, changed=1, ents=[ent.id])

    # ---- grids / rooms / text / dims ---------------------------------------

    def _handle_obj_grid(self, frame, ctx):
        s = frame["slots"]
        chain = s.get("points_c") or []
        if not s.get("from_c") and len(chain) >= 2:
            s["from_c"], s["to_c"] = chain[0], chain[1]
        if not s.get("from_c") or not s.get("to_c"):
            return self._ask(frame, "points", "points",
                             "Where does the grid line run — from and to "
                             "(coordinates)?")
        a = self._literal_point(s["from_c"])
        b = self._literal_point(s["to_c"])
        if a is None or b is None:
            return self._refuse("Grid lines take coordinates, e.g. "
                                "add grid from 30,-2 to 30,40.")
        label = s.get("label")
        if not label:
            axis = "num" if abs(b[0] - a[0]) < abs(b[1] - a[1]) else "alpha"
            label = self.model.next_grid_label(axis)
        ent = self.model.add("grid", [a, b], label=str(label).upper(),
                             bubble="both")
        say = (f"Added grid line {str(label).upper()} from {_fmt_pt(a)} "
               f"to {_fmt_pt(b)}.")
        return self._done(say, changed=1, ents=[ent.id])

    def _handle_obj_room(self, frame, ctx):
        s = frame["slots"]
        if not s.get("name"):
            return self._ask(frame, "name", "text",
                             "What is the room called?")
        got, pt = self._need_point(frame, ctx, "at",
                                   "Where does the room tag go — "
                                   "coordinates or a grid address?")
        if got != "ok":
            return pt
        ent = self.model.add("room", [pt], name=str(s["name"]).upper(),
                             number=str(s.get("number", "")))
        num = f" {s['number']}" if s.get("number") else ""
        say = f"Labeled room {str(s['name']).upper()}{num} at {_fmt_pt(pt)}."
        return self._done(say, changed=1, ents=[ent.id])

    # ---- the room macro ----------------------------------------------------

    def _next_room_number(self) -> str:
        """The GUI's room-number convention (101, 102, ...): one past the
        highest trailing integer already tagged; 101 on a fresh drawing."""
        nums = []
        for e in self.model.ents:
            if e.kind == "room":
                m = re.search(r"(\d+)$", str(e.props.get("number", "")))
                if m:
                    nums.append(int(m.group(1)))
        return str(max(nums) + 1) if nums else "101"

    def _row_warning(self, flat, w: float) -> list:
        """Warn (never block) when the fixture row outruns the wall."""
        if not flat:
            return []
        need = 2 * ROOM_FIX_END_FT + ROOM_FIX_OC_FT * (len(flat) - 1)
        if need <= float(w) + 1e-9:
            return []
        return [f"{len(flat)} fixtures at {fmt_ftin(ROOM_FIX_OC_FT)} o.c. "
                f"with {fmt_ftin(ROOM_FIX_END_FT)} end clearance want "
                f"{fmt_ftin(need)} of wall — the room is only "
                f"{fmt_ftin(float(w))} wide; spread them by hand."]

    def _handle_obj_roommacro(self, frame, ctx):
        """The whole room in one breath, ONE undo: four walls, a door, the
        numbered tag, and the listed fixtures — layout rules per the module
        docstring (anchor = lower-left; door centered in the south wall;
        fixtures on the north wall, 3'-0" o.c., 1'-6" end clearance)."""
        s = frame["slots"]
        w, d = float(s["w"]), float(s["d"])
        name_word = str(s.get("name") or "room")
        if w < 3.0 or d < 3.0:
            return self._refuse(
                f"A {fmt_ftin(w)} x {fmt_ftin(d)} {name_word} is too small "
                "to build — give real feet, e.g. draw a 12 by 10 restroom "
                "at B-2.")
        fixtures = s.get("fixtures")
        if fixtures is None:
            fixtures, unknown = self._parse_fixture_list(
                s.get("fixtures_c") or "")
            if unknown is not None:
                return self._refuse(
                    f"I don't know the fixture {unknown!r} — say wc, lav, "
                    "sink, urinal, floor drain, shower, tub, mop sink...")
            s["fixtures"] = fixtures
        got, pt = self._need_point(
            frame, ctx, "at",
            f"Where does the {name_word} go — its lower-left corner: a "
            "grid address like B-2, coordinates like 30, 20, or 'here'?")
        if got != "ok":
            return pt
        model = self.model
        wtype = str(s.get("wtype") or "stud4")
        flat = [k for k, n in fixtures for _ in range(int(n))]
        walls, center, fix_pts = _room_geometry(pt, w, d, wtype, flat)
        depth = len(model._undo)
        wall_ids = [model.add("wall", [a, b], wtype=wtype).id
                    for a, b in walls]
        door_id = model.add("door", [], host=wall_ids[0], t=0.5,
                            width_in=ROOM_DOOR_WIDTH_IN).id
        name = name_word.upper()
        number = str(s.get("number") or self._next_room_number())
        tag_id = model.add("room", [center], name=name, number=number).id
        fix_ids = [model.add("fixture", [p], stencil=k, rot=180.0).id
                   for k, p in zip(flat, fix_pts)]
        self._seal(depth)
        ids = wall_ids + [door_id, tag_id] + fix_ids
        self._remember("room_macro", ids, {"macro": {
            "anchor": [float(pt[0]), float(pt[1])], "w": w, "d": d,
            "name": name, "number": number, "wtype": wtype,
            "fixtures": [[k, int(n)] for k, n in fixtures],
            "walls": wall_ids, "door": door_id, "tag": tag_id,
            "fix": fix_ids}})
        label = WALL_TYPES.get(wtype, WALL_TYPES["stud4"])["label"]
        say = (f"Built {name} {number} — {fmt_ftin(w)} x {fmt_ftin(d)} at "
               f"{_fmt_pt(pt)}: 4 walls ({fmt_ftin(2 * (w + d))} of "
               f"{label}), a {fmt_ftin(ROOM_DOOR_WIDTH_IN / 12.0)} door "
               "centered in the south wall")
        if flat:
            fixphrase = ", ".join(f"{n} {_plural(self._stencil_word(k), n)}"
                                  for k, n in fixtures)
            say += (f", and {fixphrase} on the north wall at "
                    f"{fmt_ftin(ROOM_FIX_OC_FT)} o.c. "
                    f"({fmt_ftin(ROOM_FIX_END_FT)} end clearance).")
        else:
            say += "."
        return self._done(say, changed=len(ids), ents=ids,
                          warnings=self._row_warning(flat, w))

    def _handle_obj_text(self, frame, ctx):
        s = frame["slots"]
        if not s.get("content"):
            return self._ask(frame, "content", "text",
                             "What should the note say?")
        got, pt = self._need_point(frame, ctx, "at",
                                   "Where does the note go — coordinates "
                                   "or a grid address?")
        if got != "ok":
            return pt
        ent = self.model.add("text", [pt], text=str(s["content"]),
                             size="body")
        say = f"Noted {s['content']!r} at {_fmt_pt(pt)}."
        return self._done(say, changed=1, ents=[ent.id])

    def _handle_obj_dim(self, frame, ctx):
        s = frame["slots"]
        chain = s.get("points_c") or []
        if not s.get("from_c") and len(chain) >= 2:
            s["from_c"], s["to_c"] = chain[0], chain[1]
        if not s.get("from_c") or not s.get("to_c"):
            return self._ask(frame, "points", "points",
                             "Dimension from where to where "
                             "(two points)?")
        a = self._literal_point(s["from_c"])
        b = self._literal_point(s["to_c"])
        if a is None or b is None:
            return self._refuse("Dimensions take two points, e.g. "
                                "dimension from 0,0 to 20,0.")
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length < 1e-9:
            return self._refuse("Those are the same point — nothing to "
                                "dimension.")
        u = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
        n = (-u[1], u[0])
        off = 0.5 * self.model.scale_ratio / 12.0     # 1/2" paper offset
        w = ((a[0] + b[0]) / 2.0 + n[0] * off,
             (a[1] + b[1]) / 2.0 + n[1] * off)
        ent = self.model.add("dim", [a, b, w])
        say = (f"Dimensioned {fmt_ftin(length)} from {_fmt_pt(a)} to "
               f"{_fmt_pt(b)}.")
        return self._done(say, changed=1, ents=[ent.id])

    # ---- slope ------------------------------------------------------------

    def _handle_slope(self, frame, ctx):
        from . import pipewright as pw
        s = frame["slots"]
        if not s.get("target"):
            clause = s.get("target_c") or "this"
            kind, val = self._resolve_ents(clause, ctx, frame, "target",
                                           kinds=("pipe",))
            if kind == "ask":
                return val
            if kind == "err":
                return self._refuse(val)
            s["target"] = val
        target = s["target"]
        if isinstance(target, str):
            target = [target]
        if s.get("value") is None:
            return self._ask(frame, "value", "slope",
                             "At what pitch — e.g. 1/8 or 1/4 per foot?")
        ids, warnings, reports = [], [], []
        total_changed = 0
        depth = len(self.model._undo)
        for tid in target:
            r = pw.slope_run(self.model, tid, float(s["value"]))
            if not r.get("changed"):
                return self._refuse(r["report"])
            total_changed += r["changed"]
            ids += [d["ent_id"] for d in r["runs"]]
            warnings += r.get("warnings", [])
            reports.append(r["report"])
        self._seal(depth)
        ids = list(dict.fromkeys(ids))
        return self._done(" ".join(reports), changed=total_changed,
                          ents=ids, warnings=warnings)

    # ---- cap ---------------------------------------------------------------

    def _handle_cap(self, frame, ctx):
        from . import pipewright as pw
        r = pw.cap_open_ends(self.model, frame["slots"].get("system"))
        ids = list(dict.fromkeys(c["ent_id"] for c in r.get("capped", [])))
        return self._done(r["report"], changed=r["changed"], ents=ids)

    # ---- replace -----------------------------------------------------------

    def _handle_replace(self, frame, ctx):
        from . import pipewright as pw
        s = frame["slots"]
        if s.get("node") is None:
            old = s.get("old_c", "")
            old_words = set(_norm(old).split())
            fits = pw.derive_fittings(self.model)
            if old_words and old_words <= (_SELECT_WORDS
                                           | {"fitting", "one"}):
                sel = set(ctx.get("selection") or [])
                cands = [f for f in fits if set(f.ent_ids) & sel] \
                    if sel else list(fits)
            else:
                want = _fitting_kind(old)
                if want is None:
                    return self._refuse(
                        f"I don't know the fitting {old!r} — say wye, "
                        "combo, san tee, tee, elbow, cleanout, or "
                        "open end.")
                cands = [f for f in fits if f.kind == want]
                if not cands:
                    return self._refuse(
                        f"No {_FIT_SAY.get(want, want)} on the piping.")
            if not cands:
                return self._refuse("No fitting there to replace.")
            if len(cands) > 1:
                lp = ctx.get("last_point")
                if lp is not None:
                    best = min(cands, key=lambda f: math.hypot(
                        f.node_xy[0] - float(lp[0]),
                        f.node_xy[1] - float(lp[1])))
                    s["node"] = list(best.node_xy)
                else:
                    opts = [f"{_FIT_SAY.get(f.kind, f.kind)} at "
                            f"{_fmt_pt(f.node_xy)}" for f in cands]
                    return self._ask(
                        frame, "node", "node",
                        "Which one — " + " or ".join(opts) + "?",
                        options=opts, nodes=[f.node_xy for f in cands])
            else:
                s["node"] = list(cands[0].node_xy)
        if s.get("kind") is None:
            return self._ask(frame, "kind", "kind",
                             "Replace it with what — wye, combo, san tee, "
                             "tee, cleanout, cap, or coupling?")
        r = pw.replace_fitting(self.model, tuple(s["node"]), s["kind"])
        if not r.get("changed"):
            return self._refuse(r["report"])
        return self._done(r["report"], changed=r["changed"],
                          ents=[r["ent_id"]] if r.get("ent_id") else [])

    # ---- resize ------------------------------------------------------------

    def _handle_resize(self, frame, ctx):
        from . import pipewright as pw
        s = frame["slots"]
        if not s.get("target"):
            clause = s.get("target_c") or "this"
            kind, val = self._resolve_ents(clause, ctx, frame, "target",
                                           kinds=("pipe",))
            if kind == "ask":
                return val
            if kind == "err":
                return self._refuse(val)
            s["target"] = val
        if s.get("dia_in") is None:
            return self._ask(frame, "dia_in", "size",
                             'To what size — e.g. 4" or 6 inch?')
        target = s["target"]
        tid = target[0] if isinstance(target, list) else target
        r = pw.resize_run(self.model, tid, float(s["dia_in"]),
                          direction=s.get("direction", "downstream"))
        if not r.get("changed"):
            return self._refuse(r["report"])
        return self._done(r["report"], changed=r["changed"],
                          ents=r.get("runs", []),
                          warnings=r.get("warnings", []))

    # ---- reshape (the room-macro memory) -------------------------------------

    def _handle_reshape(self, frame, ctx):
        s = frame["slots"]
        mem = self._memory()
        if not (mem and mem.get("kind") == "room_macro"
                and isinstance(mem.get("macro"), dict)):
            return self._refuse(
                "Nothing to reshape — the last thing drawn was not a room "
                "macro. Draw one first: draw a 12 by 10 restroom at B-2.")
        if not self._memory_live(mem):
            self._forget()
            return self._refuse("The last room is gone (deleted or undone)"
                                " — draw it again first.")
        if s.get("dim") is None:
            return self._refuse("Say which way and how much — e.g. make "
                                "it 14 wide, or make it 12 deep.")
        if s.get("value") is None:
            return self._ask(frame, "value", "dist",
                             "To what size — e.g. 14' or 14'-6\"?")
        mac = mem["macro"]
        w = float(s["value"]) if s["dim"] == "w" else float(mac["w"])
        d = float(s["value"]) if s["dim"] == "d" else float(mac["d"])
        if w < 3.0 or d < 3.0:
            return self._refuse(f"{fmt_ftin(float(s['value']))} is too "
                                "small for a room — keep it 3'-0\" or "
                                "better.")
        flat = [k for k, n in mac.get("fixtures", [])
                for _ in range(int(n))]
        walls, center, fix_pts = _room_geometry(
            mac["anchor"], w, d, str(mac.get("wtype") or "stud4"), flat)
        model = self.model
        depth = len(model._undo)
        for wid, (a, b) in zip(mac["walls"], walls):
            model.update(wid, pts=[a, b])
        model.update(mac["tag"], pts=[center])
        for fid, p in zip(mac.get("fix", []), fix_pts):
            model.update(fid, pts=[p])
        self._seal(depth)
        mac["w"], mac["d"] = w, d
        self._remember("room_macro", mem["ents"], {"macro": mac})
        ids = list(mac["walls"]) + [mac["tag"]] + list(mac.get("fix", []))
        say = (f"Reshaped {mac.get('name', 'ROOM')} "
               f"{mac.get('number', '')} to {fmt_ftin(w)} x {fmt_ftin(d)}"
               f" — anchor unchanged at {_fmt_pt(mac['anchor'])}.")
        return self._done(say, changed=len(ids), ents=ids,
                          warnings=self._row_warning(flat, w))

    # ---- zoom (view only — the model is never touched) -----------------------

    def _handle_zoom(self, frame, ctx):
        s = frame["slots"]
        action = s.get("action") or "fit"
        point = None
        if action == "goto":
            got, pt = self._need_point(
                frame, ctx, "at",
                "Zoom to what — a fixture, a grid address like B-2, or "
                "coordinates?")
            if got != "ok":
                return pt
            point = (float(pt[0]), float(pt[1]))
        says = {"fit": "Zoomed to fit the drawing.",
                "in": "Zoomed in.", "out": "Zoomed out."}
        out = self._done(says.get(action)
                         or f"Zoomed to {_fmt_pt(point)}.")
        out["view"] = {"action": action, "point": point}
        return out

    # ---- delete / move -------------------------------------------------------

    def _handle_delete(self, frame, ctx):
        s = frame["slots"]
        if not s.get("target"):
            clause = s.get("target_c")
            if not clause:
                return self._ask(frame, "target", "target",
                                 "Delete what — a fixture, a run, or an "
                                 "entity id?")
            kind, val = self._resolve_ents(clause, ctx, frame, "target")
            if kind == "ask":
                return val
            if kind == "err":
                return self._refuse(val)
            s["target"] = val
        ids = s["target"] if isinstance(s["target"], list) else [s["target"]]
        descs = [self._desc(e) for i in ids
                 if (e := self.model.entity(i)) is not None]
        n = self.model.remove(ids)
        if not n:
            return self._refuse("Nothing there to delete.")
        mem = self._memory()
        if mem and {str(i) for i in ids} & set(mem["ents"]):
            self._forget()                 # the remembered batch is gone
        if len(descs) == 1 and n == 1:
            say = f"Deleted the {descs[0]} ({ids[0]})."
        else:
            say = f"Deleted {n} entities."
            if n > len(ids):
                say += " Hosted openings went with their walls."
        return self._done(say, changed=n, ents=ids)

    def _handle_move(self, frame, ctx):
        s = frame["slots"]
        if not s.get("target"):
            clause = s.get("target_c")
            if not clause:
                return self._ask(frame, "target", "target",
                                 "Move what — a fixture, a run, or an "
                                 "entity id?")
            kind, val = self._resolve_ents(clause, ctx, frame, "target")
            if kind == "ask":
                return val
            if kind == "err":
                return self._refuse(val)
            s["target"] = val
        ids = s["target"] if isinstance(s["target"], list) else [s["target"]]
        if s.get("dir"):
            if s.get("dist") is None:
                return self._ask(frame, "dist", "dist",
                                 "Move it how far — e.g. 2' or 6\"?")
            d = float(s["dist"])
            ux, uy = _DIRS[s["dir"]]
            dx, dy = ux * d, uy * d
            tail = f"{fmt_ftin(d)} {s['dir']}"
        elif (s.get("dest_c") or s.get("dest_pt") is not None
                or s.get("dest") is not None):
            got, dest = self._need_point(frame, ctx, "dest",
                                         "Move it to what point — "
                                         "coordinates or a grid address?")
            if got != "ok":
                return dest
            first = self.model.entity(ids[0])
            at = self._anchor(first) if first else None
            if at is None:
                return self._refuse("That entity has no anchor point to "
                                    "move from.")
            dx, dy = dest[0] - at[0], dest[1] - at[1]
            tail = f"to {_fmt_pt(dest)}"
        else:
            return self._ask(frame, "dest", "target",
                             "Move it where — a direction and distance "
                             "(2' north), or a point?")
        n = self.model.move(ids, dx, dy)
        if not n:
            return self._refuse("Nothing moved — check the reference.")
        mem = self._memory()
        if mem and mem.get("kind") == "room_macro" \
                and isinstance(mem.get("macro"), dict) \
                and set(mem["macro"].get("walls") or ()) \
                    <= {str(i) for i in ids}:
            # the whole room moved: the macro anchor rides along so a
            # later reshape rebuilds in the right place
            ax, ay = mem["macro"].get("anchor", (0.0, 0.0))
            mem["macro"]["anchor"] = [float(ax) + dx, float(ay) + dy]
            self._remember("room_macro", mem["ents"],
                           {"macro": mem["macro"]})
        if len(ids) == 1:
            e = self.model.entity(ids[0])
            desc = self._desc(e) if e else ids[0]
            say = f"Moved the {desc} {tail}."
        else:
            say = f"Moved {n} entities {tail}."
        return self._done(say, changed=n, ents=ids)

    # ---- undo / redo / reporters --------------------------------------------

    def _handle_undo(self, frame, ctx):
        if self.model.undo():
            self._forget()          # remembered ids are stale after undo
            return self._done("Undid the last command.", changed=1)
        return self._done("Nothing to undo.", changed=0)

    def _handle_redo(self, frame, ctx):
        if self.model.redo():
            self._forget()
            return self._done("Redid the last undone command.", changed=1)
        return self._done("Nothing to redo.", changed=0)

    def _handle_check(self, frame, ctx):
        from . import pipewright as pw
        warns = pw.check(self.model)
        msgs = [w["msg"] for w in warns]
        if not msgs:
            return self._done("Checked the piping — no warnings.")
        head = "; ".join(msgs[:3])
        more = f" (+{len(msgs) - 3} more)" if len(msgs) > 3 else ""
        return self._done(
            f"Checked the piping — {len(msgs)} finding(s): {head}{more}",
            warnings=msgs)

    def _handle_tally(self, frame, ctx):
        from . import pipewright as pw
        st = self.model.stats()
        parts = []
        if st["walls"]:
            parts.append(f"{fmt_ftin(st['wall_lf'])} of wall in "
                         f"{st['walls']} segment(s)")
        if st["doors"]:
            parts.append(f"{st['doors']} door(s)")
        if st["windows"]:
            parts.append(f"{st['windows']} window(s)")
        if st["fixtures"]:
            fx = ", ".join(f"{n} {self._stencil_word(k)}"
                           for k, n in sorted(st["fixtures"].items()))
            parts.append(fx)
        lf: dict[tuple, float] = {}
        for e in self.model.ents:
            if e.kind == "pipe" and len(e.pts) >= 2:
                key = (str(e.props.get("system", "san")),
                       float(e.props.get("dia_in", 4.0)))
                lf[key] = lf.get(key, 0.0) + _poly_len(e.pts)
        for (system, dia), qty in sorted(lf.items()):
            label = pw.SYSTEMS.get(system, {}).get("label", system)
            parts.append(f'{fmt_ftin(qty)} of {pw.fmt_dia_in(dia)}" '
                         f"{label.lower()}")
        fits: dict[str, int] = {}
        for f in pw.derive_fittings(self.model):
            if f.kind not in ("open", "fixture"):
                fits[f.kind] = fits.get(f.kind, 0) + 1
        if fits:
            parts.append("fittings: " + _fit_phrase(fits))
        if not parts:
            return self._done("Tally: nothing drawn yet.")
        return self._done("Tally: " + "; ".join(parts) + ".")

    # ------------------------------------------------------- question lane --

    def _answer_question(self, text: str) -> dict:
        """Answer, never draw.  Slope minimums come straight from
        pipewright's MIN_SLOPE table (deterministic, no store needed);
        everything else quotes the Heartwood's cited blocks when a store
        is attached and confident, and otherwise refers to the Old Hand
        honestly.  Always changed=0 — a question moves no ink."""
        q = text.rstrip("?").strip()
        if re.search(r"\b(?:slope|pitch|fall|grade)s?\b", q):
            out = self._slope_answer(q)
            if out is not None:
                return out
        return self._heartwood_answer(text)

    def _slope_answer(self, q: str):
        from . import pipewright as pw
        table = ('1/4"/ft under 3", 1/8"/ft for 3" and larger — verify '
                 "against the project code.")
        size, _rest = _extract_size(q)
        if size is not None and size > 0:
            mn, _basis = pw.min_slope(size)
            return self._done(
                f'Minimum slope for {pw.fmt_dia_in(size)}" gravity '
                f"drainage: {pw.fmt_slope(mn)} — the table: {table}")
        if re.search(r"\b(?:min(?:imum)?|limit|least|required|much|what|"
                     r"whats)\b", q):
            return self._done(f"Gravity-drainage minimum slopes: {table}")
        return None

    def _heartwood_answer(self, question: str) -> dict:
        referral = ("That is a question, not a drawing command — and "
                    "nothing loaded here backs an answer. Ask the Old "
                    "Hand (Ctrl+/) once the Heartwood is seeded with "
                    "your codes and specs.")
        store = self._store()
        if store is None:
            return self._refuse(referral)
        try:
            if int(store.counts().get("chunks", 0)) <= 0:
                return self._refuse(referral)
            from .heartwood import ask as hw_ask
            res = hw_ask.ask(store, question)
        except Exception:
            return self._refuse(referral)
        if res.get("refused") or not res.get("blocks"):
            msg = str(res.get("message") or "").strip()
            return self._refuse(
                (msg + " " if msg else "")
                + "Ask the Old Hand (Ctrl+/) — or seed the Heartwood "
                  "and ask again.")
        lines = []
        for b in res["blocks"][:3]:
            t = str(b.get("text", "")).strip()
            if b.get("unverified"):
                t += "  [shop note — unverified]"
            lines.append(t)
        return self._done("\n".join(lines))

    # ------------------------------------------ pattern macros (lane 2) ----

    def save_macro(self, name) -> dict:
        """Snapshot the LAST room macro as a reusable named template — an
        UNVERIFIED Heartwood note (origin ``"macro"``).  It cannot draw
        until a human trusts that note in the Old Hand's Manage screen;
        that human gate IS the lane-2 rule.  Returns a command()-shaped
        result dict."""
        name = _norm(name)
        if not name or not re.fullmatch(r"[a-z][a-z0-9 _-]{0,60}", name):
            return self._refuse("Give the macro a plain name — letters "
                                "and numbers, e.g. standard restroom.")
        mem = self._memory()
        if not (mem and mem.get("kind") == "room_macro"
                and isinstance(mem.get("macro"), dict)):
            return self._refuse(
                "Nothing to save — draw a room macro first (draw a 12 by "
                "10 restroom at B-2 with two lavs and a wc), then save "
                "it.")
        store = self._store()
        if store is None:
            return self._refuse("No Heartwood store is attached — macros "
                                "live there as gated notes, so open the "
                                "knowledge core first.")
        status, _payload = self._macro_note(name)
        if status is not None:
            return self._refuse(f"A macro named {name!r} is already on "
                                f"file ({status}) — pick another name or "
                                "reject the old note first.")
        mac = mem["macro"]
        fixtures = [[str(k), int(n)] for k, n in mac.get("fixtures", [])]
        payload = {"kind": "room_macro", "w": float(mac["w"]),
                   "d": float(mac["d"]), "name": str(mac.get("name", "")),
                   "wtype": str(mac.get("wtype", "stud4")),
                   "fixtures": fixtures}
        fixphrase = ", ".join(f"{k} x{n}" for k, n in fixtures) or "none"
        text = (f"MACRO {name}\n"
                f"room template: {fmt_ftin(payload['w'])} x "
                f"{fmt_ftin(payload['d'])} {payload['name'] or 'ROOM'}; "
                f"fixtures: {fixphrase}\n"
                f"frame: {json.dumps(payload, sort_keys=True)}")
        try:
            store.add_note(text, origin="macro")
        except Exception:
            return self._refuse("The Heartwood store would not take the "
                                "note — macro not saved.")
        return self._done(
            f"Saved macro {name!r} as an UNVERIFIED note in the "
            "Heartwood. Trust it in the Old Hand's Manage screen and "
            f"'draw a {name} at B-2' will build it.")

    # -------------------------------------------- optional heartwood lane 1 --

    def _store(self):
        """The optional heartwood store; None when learning is off or the
        knowledge core is unavailable — commanding never depends on it."""
        if self._hw_path is None or self._hw_dead:
            return None
        if self._hw_store is not None:
            return self._hw_store
        try:
            from .heartwood.store import HeartwoodStore
            from .heartwood import thesaurus
            store = HeartwoodStore(self._hw_path)
            thesaurus.ensure_seed(store)
            self._hw_store = store
        except Exception:
            self._hw_dead = True
            return None
        return self._hw_store

    def _expand(self, term: str) -> list:
        store = self._store()
        if store is None:
            return []
        try:
            from .heartwood.thesaurus import expand
            return [d["term"] for d in expand(term, store)]
        except Exception:
            return []

    def _record(self, frame: dict) -> None:
        """Lane 1: a successful phrase -> frame key, via the feedback log
        (ranking-memory lane; chunk 0 = no chunk, it is a phrase record)."""
        store = self._store()
        if store is None:
            return
        try:
            key = frame["verb"]
            obj = frame.get("slots", {}).get("object")
            if obj:
                key += "." + str(obj)
            store.log_feedback(f"weave:{frame.get('norm', '')} -> {key}",
                               0, "used")
        except Exception:
            pass

    def _propose_from_ent(self, noun: str, ent_id: str) -> None:
        try:
            e = self.model.entity(str(ent_id))
            if e is None or e.kind != "fixture":
                return
            key = str(e.props.get("stencil", ""))
            self._propose(noun, self._stencil_word(key))
        except Exception:
            pass

    def _propose(self, noun: str, canonical: str) -> None:
        """PROPOSE (never auto-approve) a field-word synonym the operator
        just taught by answering a clarification — the human gate reviews
        it in the Ground Truth section."""
        store = self._store()
        if store is None or not noun or not canonical:
            return
        try:
            from .heartwood.thesaurus import norm as th_norm
            n, c = th_norm(noun), th_norm(canonical)
            if not n or not c or n == c:
                return
            for row in store.thesaurus_rows():
                if th_norm(row["term"]) == n \
                        and th_norm(row["canonical"]) == c:
                    return
            store.add_thesaurus(noun, canonical, "plumbing", "unverified")
        except Exception:
            pass
