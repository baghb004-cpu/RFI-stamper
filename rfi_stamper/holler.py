"""Holler — Planloom's hands-free voice-control engine (ROADMAP Phase H).

Holler is the *deterministic hands and brain* that sit behind the Squawk Box
ear (:mod:`rfi_stamper.squawk`): the recognizer decides WHICH trained phrase
you said, and Holler turns that phrase into real keystrokes typed into
whatever OS window has focus, so you can drive an external drawing program by
speaking.  It ENHANCES the mouse and keyboard; it does not replace them.

The registry names (persona vocabulary — use these everywhere):

* **Holler** — the whole feature (the :class:`Holler` router).
* **The Caller** — the spoken-measurement/shape grammar
  (:func:`parse_number` / :func:`parse_dimension` / :func:`parse_shape` /
  :func:`speak_to_text`).  It is a hand-written grammar, NOT a model: the same
  words always produce the same text.  No ``eval``, no floating vocabulary.
* **Trips** — tool-shortcut triggers (a keystroke spec like ``ctrl+c``).
* **Placards** — exact text inserts (a title-block phrase, a note stamp).
* **Fetches** — open a file / folder / app through the OS shell.
* **Runs** — recorded keystroke macros (type / wait / key steps).
* **The Songbook** — the command dictionary (:class:`Songbook` of
  :class:`Entry` rows, JSON + CSV round-trip so it opens in a spreadsheet).
* **The Ticker** — history plus counters (:class:`Ticker`).

Honest boundaries (the product IS honesty — the flags say what really ran):

* The keystroke **Sender** is written straight against the Windows user32
  ``SendInput`` interface via :mod:`ctypes` — the sibling of squawk's winmm
  capture layer.  ``HAS_SEND`` is True only where user32 is actually
  reachable (Windows).  Everywhere else — this Linux box included — the whole
  Sender runs DRY: every intended keystroke is recorded to an *intent list*
  and returned (and logged), and NOTHING is injected.  Consumers check
  ``HAS_SEND`` before promising the user that keys will land.  The dry path
  makes the entire pipeline testable headless.
* **Fetches** hand a local path to the OS shell (``os.startfile`` on Windows,
  ``xdg-open`` / ``open`` elsewhere).  Planloom's OWN process opens ZERO
  network sockets — the toolkit-wide offline invariant is intact.  A Fetch
  row may be flagged ``is_url=True``; the opener then returns a plain note
  that a *separate* program (your browser) is what connected, never Planloom.
  The default Songbook ships with no URL rows.
* Recognition is speaker-trained (squawk) and therefore language-agnostic by
  construction — there is no bundled speech pack and no big-vocabulary
  claim.  Holler only shapes the text once a phrase has been recognized.

Determinism: nothing on a path a test compares calls :func:`datetime.now` —
the clock is passed in (``ts=`` arguments); the GUI supplies it.  Stdlib only
(plus an optional, lazy ``.squawk`` import on the GUI side for the ear); the
engine here is GUI-free and needs no audio.
"""
from __future__ import annotations

import ctypes
import csv
import io
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field

# =========================================================================
# The Caller — spoken measurement / shape grammar (PURE text, no side effects)
# =========================================================================

#: curly quotes / primes normalized to ASCII feet ' and inch " marks
_QUOTE_MAP = str.maketrans({
    "‘": "'", "’": "'", "′": "'",     # ' ' prime -> feet
    "“": '"', "”": '"', "″": '"',     # " " prime -> inches
})

#: cardinal words 0..19 plus the articles ("a"/"an" = 1) and "oh" = 0
_ONES = {
    "zero": 0, "oh": 0, "a": 1, "an": 1,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}

#: tens words 20..90
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}

#: scale words
_SCALES = {"hundred": 100, "thousand": 1000}

#: single decimal digits accepted after "point"
_DIGIT_WORDS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
}

#: fraction words -> (numerator, denominator); numerator here is always 1,
#: the spoken count in front supplies the real numerator ("three quarters").
FRACTION_WORDS = {
    "half": (1, 2), "halves": (1, 2),
    "third": (1, 3), "thirds": (1, 3),
    "quarter": (1, 4), "quarters": (1, 4),
    "fourth": (1, 4), "fourths": (1, 4),
    "eighth": (1, 8), "eighths": (1, 8),
    "sixteenth": (1, 16), "sixteenths": (1, 16),
}

#: full number-word registry (documented union; "thirty second(s)" = 1/32 is
#: a two-word fraction handled specially so the tens word "thirty" is not
#: swallowed as 30).

#: unit words that mark a value as feet / as inches
_FEET_UNITS = {"foot", "feet", "ft", "'"}
_INCH_UNITS = {"inch", "inches", "in", '"'}

#: connectors the grammar recognizes


def _as_tokens(text) -> list:
    """Normalize a spoken phrase (or a token list) to lowercase word tokens.

    Hyphens split ("thirty-second" -> ["thirty", "second"]); the feet ' and
    inch " marks tokenize on their own so a stray prime is still readable.
    """
    if isinstance(text, (list, tuple)):
        return [str(t).lower() for t in text]
    s = str(text).translate(_QUOTE_MAP).lower()
    s = s.replace("-", " ").replace("'", " ' ").replace('"', ' " ')
    return s.split()


def _fraction_at(tokens, i):
    """Fraction starting at token ``i`` -> ``((num, den), consumed)`` or None.

    Handles the two-word ``thirty second(s)`` = 1/32 before the plain map.
    """
    if i >= len(tokens):
        return None
    w = tokens[i]
    if (w == "thirty" and i + 1 < len(tokens)
            and tokens[i + 1] in ("second", "seconds")):
        return ((1, 32), 2)
    if w in FRACTION_WORDS:
        return (FRACTION_WORDS[w], 1)
    return None


def _parse_cardinal(tokens, i):
    """Greedy English cardinal parser -> ``(value:int, consumed:int)``.

    ``"one hundred five"`` -> 105, ``"twenty three"`` -> 23,
    ``"two thousand forty"`` -> 2040.  Stops at anything that is not a
    cardinal word, and refuses to swallow the ``thirty`` of a
    ``thirty second(s)`` fraction.
    """
    total = 0        # finalized by each "thousand"
    current = 0      # the group being built
    used = 0
    saw = False
    n = len(tokens)
    while i < n:
        w = tokens[i]
        if (w == "thirty" and i + 1 < n
                and tokens[i + 1] in ("second", "seconds")):
            break                                   # 1/32 fraction, not 30
        if w in _ONES:
            current += _ONES[w]
        elif w in _TENS:
            current += _TENS[w]
        elif w == "hundred":
            current = (current or 1) * 100
        elif w == "thousand":
            total += (current or 1) * 1000
            current = 0
        else:
            break
        saw = True
        i += 1
        used += 1
    return (total + current, used) if saw else (0, 0)


def parse_number(tokens, i: int = 0):
    """English cardinal + fraction + decimal parser.

    Returns ``(value: float, consumed: int)``; ``consumed == 0`` means no
    number was found at ``i`` (so callers can fall through).  Grammar:

    * cardinals to thousands: ``"one hundred five"`` -> 105.0
    * leading fraction: ``"a half"`` -> 0.5, ``"three quarters"`` -> 0.75
      (the cardinal in front is the numerator)
    * whole-and-fraction: ``"six and seven eighths"`` -> 6.875
    * decimal via "point": ``"twelve point five"`` -> 12.5
    """
    tokens = _as_tokens(tokens)
    n = len(tokens)
    start = i

    whole, used = _parse_cardinal(tokens, i)
    has_whole = used > 0
    j = i + used
    value = float(whole) if has_whole else None

    # decimal: "<whole> point <digit>..."
    if j < n and tokens[j] == "point":
        k = j + 1
        digits = []
        while k < n and tokens[k] in _DIGIT_WORDS:
            digits.append(_DIGIT_WORDS[tokens[k]])
            k += 1
        if digits:
            dec = 0.0
            for p, d in enumerate(digits):
                dec += d * (10.0 ** -(p + 1))
            return ((value or 0.0) + dec, k - start)

    # fraction, case B: an immediate fraction word — the whole (or an
    # implied 1) is the numerator: "three quarters", "a half", "seven eighths"
    fr = _fraction_at(tokens, j)
    if fr is not None:
        (fnum, fden), fused = fr
        num = whole if has_whole else 1
        return (num * fnum / fden, (j + fused) - start)

    # fraction, case A: "<whole> and <count> <fraction>"
    if has_whole and j < n and tokens[j] == "and":
        n2, u2 = _parse_cardinal(tokens, j + 1)
        fr2 = _fraction_at(tokens, j + 1 + u2)
        if fr2 is not None:
            (fnum, fden), fused = fr2
            num = n2 if u2 > 0 else 1
            value = float(whole) + num * fnum / fden
            return (value, (j + 1 + u2 + fused) - start)

    if not has_whole:
        return (0.0, 0)
    return (value, j - start)


def mixed(value: float, denom: int = 16) -> str:
    """A value as a mixed fraction string: ``6.875`` -> ``"6 7/8"``.

    Rounds to the nearest 1/``denom``, reduces the fraction, drops a zero
    whole (``0.75`` -> ``"3/4"``), and prints exact integers plainly
    (``2.0`` -> ``"2"``).  Negative-safe.  This mirrors the reduction math in
    :func:`rfi_stamper.draft.fmt_ftin` so a measure reads identically, but
    Holler stays standalone (no draft import — the engine is dependency-light).
    """
    denom = max(1, int(denom))
    units = round(abs(float(value)) * denom)
    whole, frac = divmod(units, denom)
    if frac:
        g = math.gcd(frac, denom)
        body = (f"{whole} {frac // g}/{denom // g}" if whole
                else f"{frac // g}/{denom // g}")
    else:
        body = str(whole)
    return ("-" + body) if (value < 0 and units) else body


# ------------------------------------------------------------- format profiles

#: Output profiles for a measurement.  ``kind`` selects the formatter;
#: ``sep`` is the feet/inch separator for the ft-in family.  A firm picks its
#: house style once; the same spoken measure then always prints the same way.
PROFILES = {
    "arch": {"kind": "ftin", "sep": "-",
             "desc": "feet-inches, hyphen  ->  105'-6 7/8\""},
    "arch_space": {"kind": "ftin", "sep": " ",
                   "desc": "feet-inches, space  ->  105' 6 7/8\""},
    "arch_nohyphen": {"kind": "ftin", "sep": "",
                      "desc": "feet-inches, no gap  ->  105'6 7/8\""},
    "decimal_ft": {"kind": "decimal_ft", "places": 3,
                   "desc": "decimal feet  ->  105.573'"},
    "decimal_in": {"kind": "decimal_in", "places": 3,
                   "desc": "decimal inches  ->  1266.875\""},
    "mm": {"kind": "mm",
           "desc": "millimetres, rounded  ->  3218 mm"},
    "custom": {"kind": "custom", "template": "{feet}'-{inches}{frac}\"",
               "desc": "user template over {feet}{inches}{frac}{num}{den}"
                       "{total_in}{total_ft}{mm}"},
}


def _profile(name):
    return PROFILES.get(name) or PROFILES["arch"]


def _trim(value: float, places: int) -> str:
    """Fixed-precision text with trailing zeros (and a lone dot) stripped."""
    s = f"{value:.{int(places)}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def format_ftin(feet, inches, frac_num, frac_den, profile: str = "arch") -> str:
    """Compose feet / inches / a reduced fraction into profile text.

    ``format_ftin(105, 6, 7, 8, "arch")`` -> ``"105'-6 7/8\""``.  The decimal
    and millimetre profiles reconstruct the total from the parts, so the same
    call renders ``"105.573'"`` / ``"3218 mm"`` under those profiles.
    """
    prof = _profile(profile)
    kind = prof["kind"]
    feet = int(feet)
    inches = int(inches)
    frac_num = int(frac_num)
    frac_den = int(frac_den) or 1
    total_in = feet * 12 + inches + (frac_num / frac_den if frac_num else 0.0)
    if kind == "decimal_ft":
        return _trim(total_in / 12.0, prof.get("places", 3)) + "'"
    if kind == "decimal_in":
        return _trim(total_in, prof.get("places", 3)) + '"'
    if kind == "mm":
        return f"{round(total_in * 25.4)} mm"
    if kind == "custom":
        frac = f" {frac_num}/{frac_den}" if frac_num else ""
        fields = {"feet": feet, "inches": inches, "num": frac_num,
                  "den": frac_den, "frac": frac,
                  "total_in": _trim(total_in, 3),
                  "total_ft": _trim(total_in / 12.0, 3),
                  "mm": round(total_in * 25.4)}
        try:
            return str(prof.get("template", "")).format(**fields)
        except (KeyError, IndexError, ValueError):
            return f"{feet}'-{inches}{frac}\""
    # ft-in family
    frac = f" {frac_num}/{frac_den}" if frac_num else ""
    return f"{feet}'{prof.get('sep', '-')}{inches}{frac}\""


def parse_dimension(text, profile: str = "arch"):
    """Spoken measurement -> formatted text, or ``None`` if it is not one.

    * feet + inches: the value before ``feet``/``foot`` is feet, the value
      after (an optional ``and`` bridges them) is inches, a trailing fraction
      adds to inches -> ``"two feet seven and seven eighths"`` -> ``2'-7 7/8"``
    * inches only (no feet word): ``"six and seven eighths"`` -> ``6 7/8"``
      (a bare inch mark under the ft-in profiles)
    * decimal: ``"twelve point five feet"`` -> per profile
    """
    tokens = _as_tokens(text)
    v1, c1 = parse_number(tokens, 0)
    if c1 == 0:
        return None
    i = c1
    n = len(tokens)
    feet_present = False

    if i < n and tokens[i] in _FEET_UNITS:
        feet_present = True
        total_in = v1 * 12.0
        i += 1
        if i < n and tokens[i] == "and":
            i += 1
        if i < n:
            v2, c2 = parse_number(tokens, i)
            if c2 > 0:
                total_in += v2
                i += c2
        if i < n and tokens[i] in _INCH_UNITS:
            i += 1
    else:
        total_in = float(v1)                      # a bare value is inches
        if i < n and tokens[i] in _INCH_UNITS:
            i += 1

    prof = _profile(profile)
    if prof["kind"] == "ftin" and not feet_present:
        return mixed(total_in) + '"'

    denom = 16
    units = round(total_in * denom)
    whole_in, fr = divmod(units, denom)
    ft, inch = divmod(whole_in, 12)
    if fr:
        g = math.gcd(fr, denom)
        num, den = fr // g, denom // g
    else:
        num, den = 0, 1
    return format_ftin(ft, inch, num, den, profile)


#: spoken shape word -> steel/section symbol (single-word forms)
SHAPE_PREFIXES = {
    "angle": "L", "l": "L",
    "channel": "C", "c": "C",
    "plate": "PL", "pl": "PL",
    "tube": "HSS", "hss": "HSS",
    "beam": "W", "w": "W",
    "wt": "WT", "mc": "MC", "mt": "MT", "st": "ST",
}

#: multi-word shape forms, matched greedily before the single-word map
_SHAPE_MULTI = [
    (("wide", "flange"), "W"),
    (("double", "u"), "W"),
    (("h", "s", "s"), "HSS"),
]


def _shape_prefix(tokens):
    """Leading shape symbol -> ``(symbol, consumed)`` or ``(None, 0)``."""
    if not tokens:
        return (None, 0)
    for words, sym in _SHAPE_MULTI:
        if tuple(tokens[:len(words)]) == words:
            return (sym, len(words))
    w = tokens[0]
    if w in SHAPE_PREFIXES:
        return (SHAPE_PREFIXES[w], 1)
    return (None, 0)


def parse_shape(text):
    """Spoken shape call -> section string, or ``None`` if no leading shape.

    ``"L two and one half by two and one half by one quarter"`` ->
    ``"L2 1/2x2 1/2x1/4"``.  Dimension groups are mixed fractions joined by
    ``x`` (spoken ``by``); no unit marks appear inside a shape call.
    """
    tokens = _as_tokens(text)
    sym, used = _shape_prefix(tokens)
    if sym is None:
        return None
    i = used
    n = len(tokens)
    v, c = parse_number(tokens, i)
    if c == 0:
        return None                                # a shape needs a size
    groups = [mixed(v)]
    i += c
    while i < n:
        if tokens[i] in ("by", "x"):
            v, c = parse_number(tokens, i + 1)
            if c == 0:
                break
            groups.append(mixed(v))
            i += 1 + c
        else:
            break
    return sym + "x".join(groups)


def speak_to_text(text, profile: str = "arch") -> dict:
    """Route a spoken phrase through the Caller.

    Returns ``{"kind": "shape"|"dimension"|None, "text": str|None}``.  A
    leading shape word is tried first, then a measurement; a phrase that is
    neither yields ``kind=None`` so the Router can fall through to a miss.
    """
    shape = parse_shape(text)
    if shape is not None:
        return {"kind": "shape", "text": shape}
    dim = parse_dimension(text, profile)
    if dim is not None:
        return {"kind": "dimension", "text": dim}
    return {"kind": None, "text": None}


# =========================================================================
# The Songbook — the command dictionary
# =========================================================================

def _norm(s) -> str:
    """Normalized trigger key: lowercase, whitespace collapsed."""
    return " ".join(str(s).lower().split())


def _steps_to_str(steps) -> str:
    """Serialize a Run's steps to the spreadsheet form
    ``"type:e | wait:1.0 | key:Tab | type:90 | key:Enter"``."""
    parts = []
    for step in steps or []:
        step = list(step)
        verb = str(step[0]) if step else ""
        arg = str(step[1]) if len(step) > 1 else ""
        parts.append(f"{verb}:{arg}" if arg != "" else verb)
    return " | ".join(parts)


def _str_to_steps(text) -> list:
    """Parse the serialized-step form back into ``[[verb, arg], ...]``."""
    text = str(text or "").strip()
    if not text:
        return []
    steps = []
    for part in text.split("|"):
        part = part.strip()
        if not part:
            continue
        verb, sep, arg = part.partition(":")
        steps.append([verb.strip(), arg.strip()] if sep else [verb.strip()])
    return steps


@dataclass
class Entry:
    """One Songbook command row.

    ``kind`` is one of ``trip`` | ``placard`` | ``fetch`` | ``run``:

    * **trip**   — ``payload`` is a keystroke spec (``"ctrl+c"``, ``"l+Enter"``)
    * **placard** — ``payload`` is the literal text to type
    * **fetch**  — ``payload`` is a path / target; ``is_url`` flags web targets
    * **run**    — ``steps`` carries the macro; ``payload`` is unused

    ``trigger`` is the spoken phrase (compared normalized: lowercase,
    whitespace collapsed).
    """

    trigger: str
    kind: str
    payload: str = ""
    steps: list = field(default_factory=list)
    is_url: bool = False
    note: str = ""
    enabled: bool = True

    def to_dict(self) -> dict:
        return {"trigger": self.trigger, "kind": self.kind,
                "payload": self.payload,
                "steps": [list(s) for s in self.steps],
                "is_url": bool(self.is_url), "note": self.note,
                "enabled": bool(self.enabled)}

    @classmethod
    def from_dict(cls, data: dict) -> "Entry":
        return cls(
            trigger=str(data.get("trigger", "")),
            kind=str(data.get("kind", "")),
            payload=str(data.get("payload", "")),
            steps=[list(s) for s in (data.get("steps") or [])],
            is_url=bool(data.get("is_url", False)),
            note=str(data.get("note", "")),
            enabled=bool(data.get("enabled", True)),
        )


#: CSV column order for :meth:`Songbook.to_csv` / :meth:`Songbook.from_csv`
CSV_COLUMNS = ["trigger", "kind", "payload", "steps", "is_url", "note",
               "enabled"]

_VERSION = 1


def default_path() -> str:
    """Default Songbook location: ``~/.planloom/holler/songbook.json``."""
    return os.path.join("~", ".planloom", "holler", "songbook.json")


def _atomic_write(path: str, blob: bytes) -> None:
    """Temp file + fsync + ``os.replace`` — a crash never truncates the file."""
    path = os.path.expanduser(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(blob)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _seed_entries() -> list:
    """The starter Songbook — GENERIC illustrations of each kind.  No real
    program shortcuts are baked in (map your own to your tools); no URL rows.
    """
    return [
        Entry("line", "trip", "l+Enter",
              note="type the tool letter, then Enter (map to your program)"),
        Entry("copy", "trip", "ctrl+c", note="copy"),
        Entry("paste", "trip", "ctrl+v", note="paste"),
        Entry("issued for construction", "placard", "ISSUED FOR CONSTRUCTION",
              note="title-block stamp"),
        Entry("issued for approval", "placard", "ISSUED FOR APPROVAL",
              note="title-block stamp"),
        Entry("project folder", "fetch", "~",
              note="open your project folder (edit this path)"),
        Entry("column rotate", "run", "",
              steps=[["type", "e"], ["wait", "1.0"], ["key", "Tab"],
                     ["type", "90"], ["key", "Enter"]],
              note="example macro: edit, wait, Tab, 90, Enter"),
    ]


#: the shipped starter deck (rebuilt fresh per Songbook so rows never alias)
SEED_SONGBOOK = _seed_entries()


class Songbook:
    """The command dictionary: a list of :class:`Entry` rows with JSON and CSV
    persistence.  Lookups are normalized (lowercase, whitespace collapsed);
    :meth:`find` matches an exact trigger first, then the longest trigger that
    is a word-boundary prefix of the utterance (so trailing filler is fine).
    """

    def __init__(self, entries=None, path=None):
        self.entries = list(entries) if entries is not None else []
        self.path = path

    @classmethod
    def seed(cls, path=None) -> "Songbook":
        """A Songbook loaded with the starter deck (:data:`SEED_SONGBOOK`)."""
        return cls(_seed_entries(), path=path)

    # -- editing ----------------------------------------------------------
    def add(self, entry, **kw) -> Entry:
        """Add an :class:`Entry` (or build one from keyword fields).  A new
        trigger with an existing normalized key replaces the old row."""
        if not isinstance(entry, Entry):
            entry = Entry(entry, **kw)
        self.remove(entry.trigger)
        self.entries.append(entry)
        return entry

    def remove(self, trigger) -> bool:
        key = _norm(trigger)
        before = len(self.entries)
        self.entries = [e for e in self.entries if _norm(e.trigger) != key]
        return len(self.entries) != before

    def find(self, trigger):
        """The best :class:`Entry` for ``trigger`` (enabled rows only), or
        ``None``: exact normalized match first, else the longest trigger that
        is a word-boundary prefix of the utterance."""
        q = _norm(trigger)
        if not q:
            return None
        for e in self.entries:
            if e.enabled and _norm(e.trigger) == q:
                return e
        best, best_len = None, -1
        for e in self.entries:
            if not e.enabled:
                continue
            t = _norm(e.trigger)
            if t and (q == t or q.startswith(t + " ")) and len(t) > best_len:
                best, best_len = e, len(t)
        return best

    # -- JSON persistence -------------------------------------------------
    def save(self, path=None) -> None:
        """Atomically write the Songbook as versioned JSON."""
        path = os.path.expanduser(path or self.path or default_path())
        self.path = path
        blob = json.dumps(
            {"planloom_holler": _VERSION,
             "entries": [e.to_dict() for e in self.entries]},
            indent=2, ensure_ascii=False).encode("utf-8")
        _atomic_write(path, blob)

    def load(self, path=None) -> "Songbook":
        """Load rows from JSON (missing / corrupt file -> empty, never raises)."""
        path = os.path.expanduser(path or self.path or default_path())
        self.path = path
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            self.entries = []
            return self
        raw = data.get("entries") if isinstance(data, dict) else None
        self.entries = [Entry.from_dict(d) for d in (raw or [])
                        if isinstance(d, dict) and d.get("trigger")]
        return self

    # -- CSV round trip ---------------------------------------------------
    def to_csv(self, path) -> None:
        """Write the Songbook as a spreadsheet-friendly CSV (atomic).  A Run's
        steps serialize to ``"type:e | wait:1.0 | key:Tab | ..."``."""
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(CSV_COLUMNS)
        for e in self.entries:
            w.writerow([e.trigger, e.kind, e.payload,
                        _steps_to_str(e.steps),
                        "true" if e.is_url else "false",
                        e.note,
                        "true" if e.enabled else "false"])
        _atomic_write(path, buf.getvalue().encode("utf-8"))

    def from_csv(self, path) -> "Songbook":
        """Load rows from a CSV written by :meth:`to_csv` (round-trip safe)."""
        with open(path, encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
        self.entries = []
        if not rows:
            return self
        header = [h.strip() for h in rows[0]]
        idx = {name: header.index(name) for name in CSV_COLUMNS
               if name in header}

        def cell(row, name, dflt=""):
            i = idx.get(name)
            return row[i] if i is not None and i < len(row) else dflt

        for row in rows[1:]:
            if not row or not cell(row, "trigger").strip():
                continue
            self.entries.append(Entry(
                trigger=cell(row, "trigger"),
                kind=cell(row, "kind"),
                payload=cell(row, "payload"),
                steps=_str_to_steps(cell(row, "steps")),
                is_url=cell(row, "is_url").strip().lower() in ("true", "1",
                                                               "yes"),
                note=cell(row, "note"),
                enabled=cell(row, "enabled", "true").strip().lower()
                not in ("false", "0", "no", ""),
            ))
        return self


# =========================================================================
# The Sender — OS keystroke synthesis (Windows user32 SendInput via ctypes)
# =========================================================================

def _load_user32():
    if sys.platform != "win32":
        return None
    try:
        return ctypes.WinDLL("user32")
    except OSError:
        return None


_USER32 = _load_user32()

#: True only where the user32 keystroke path is actually reachable (Windows).
#: Everywhere else the whole Sender runs DRY — intents are recorded, nothing
#: is injected.  Check this before promising the user that keys will land.
HAS_SEND = _USER32 is not None

_NO_SEND_MSG = ("keystroke injection runs on the Windows user32 SendInput "
                "interface and is available on Windows only — check "
                "holler.HAS_SEND; elsewhere every keystroke is recorded to "
                "the returned intent list (DRY-RUN) and nothing is sent")

#: named virtual keys -> Windows VK codes (Sender-only; harmless data on Linux)
KEY_VK = {
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "escape": 0x1B, "esc": 0x1B,
    "space": 0x20, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "insert": 0x2D,
}
KEY_VK.update({f"f{i}": 0x70 + (i - 1) for i in range(1, 13)})

#: modifier names -> VK codes
MODIFIERS = {"ctrl": 0x11, "control": 0x11, "alt": 0x12, "menu": 0x12,
             "shift": 0x10, "win": 0x5B, "super": 0x5B, "meta": 0x5B}

# -- Windows structures (defined unconditionally; only *called* under HAS_SEND)
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_INPUT_KEYBOARD = 1
_ULONG_PTR = (ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8
              else ctypes.c_uint32)


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_uint16),
                ("wScan", ctypes.c_uint16),
                ("dwFlags", ctypes.c_uint32),
                ("time", ctypes.c_uint32),
                ("dwExtraInfo", _ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    # sized to the largest INPUT member so cbSize (via sizeof) stays correct
    _fields_ = [("ki", _KEYBDINPUT), ("_pad", ctypes.c_uint8 * 32)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("u", _INPUTUNION)]


def _win_send(inputs) -> None:                     # pragma: no cover (Windows)
    """Push a list of ``_INPUT`` records through user32 SendInput."""
    n = len(inputs)
    arr = (_INPUT * n)(*inputs)
    _USER32.SendInput(n, ctypes.byref(arr), ctypes.sizeof(_INPUT))


def _win_char(ch, up=False):                       # pragma: no cover (Windows)
    ki = _KEYBDINPUT(0, ord(ch), _KEYEVENTF_UNICODE
                     | (_KEYEVENTF_KEYUP if up else 0), 0, 0)
    return _INPUT(_INPUT_KEYBOARD, _INPUTUNION(ki=ki))


def _win_vk(vk, up=False):                          # pragma: no cover (Windows)
    ki = _KEYBDINPUT(vk, 0, (_KEYEVENTF_KEYUP if up else 0), 0, 0)
    return _INPUT(_INPUT_KEYBOARD, _INPUTUNION(ki=ki))


def _dry_default(dry):
    return (not HAS_SEND) if dry is None else bool(dry)


def _norm_key(name) -> str:
    return str(name).strip().lower()


def type_text(s, dry=None, log=None) -> list:
    """Type a literal unicode string, char by char (UNICODE down+up each).

    Returns the intent list ``[("char", ch), ...]``.  ``dry`` defaults to
    ``not HAS_SEND``; on a dry run nothing is injected — the intents are the
    honest record of what *would* have been typed.
    """
    dry = _dry_default(dry)
    intents = [("char", ch) for ch in str(s)]
    if not dry and HAS_SEND and intents:            # pragma: no cover (Windows)
        seq = []
        for _, ch in intents:
            seq.append(_win_char(ch, up=False))
            seq.append(_win_char(ch, up=True))
        _win_send(seq)
    if log:
        log(f"type_text {s!r} ({'dry' if dry else 'sent'})")
    return intents


def tap_key(name, dry=None, log=None) -> list:
    """Tap one named virtual key (down+up).  Intent: ``[("key", name)]``.

    An unknown key name is recorded but not injected (honest no-op)."""
    dry = _dry_default(dry)
    key = _norm_key(name)
    intents = [("key", key)]
    if not dry and HAS_SEND and key in KEY_VK:      # pragma: no cover (Windows)
        vk = KEY_VK[key]
        _win_send([_win_vk(vk, up=False), _win_vk(vk, up=True)])
    if log:
        log(f"tap_key {key!r} ({'dry' if dry else 'sent'})")
    return intents


def chord(mods, key, dry=None, log=None) -> list:
    """Hold modifiers, tap a key, release the modifiers (reverse order).

    ``chord(["ctrl"], "c")`` -> ``[("down","ctrl"), ("key","c"),
    ("up","ctrl")]``.  Modifier and key names are normalized.
    """
    dry = _dry_default(dry)
    mods = [_norm_key(m) for m in mods]
    key = _norm_key(key)
    intents = ([("down", m) for m in mods]
               + [("key", key)]
               + [("up", m) for m in reversed(mods)])
    if not dry and HAS_SEND:                        # pragma: no cover (Windows)
        seq = []
        for m in mods:
            if m in MODIFIERS:
                seq.append(_win_vk(MODIFIERS[m], up=False))
        if key in KEY_VK:
            seq += [_win_vk(KEY_VK[key], up=False), _win_vk(KEY_VK[key], True)]
        elif len(key) == 1:
            seq += [_win_char(key, up=False), _win_char(key, up=True)]
        for m in reversed(mods):
            if m in MODIFIERS:
                seq.append(_win_vk(MODIFIERS[m], up=True))
        _win_send(seq)
    if log:
        log(f"chord {mods}+{key} ({'dry' if dry else 'sent'})")
    return intents


def parse_key_spec(spec):
    """Split a keystroke spec into ``(mods, key)`` (all normalized).

    ``"ctrl+c"`` -> ``(["ctrl"], "c")``; ``"l+Enter"`` -> ``(["l"], "enter")``;
    a lone token -> ``([], token)``.  Whether the leading parts are real
    modifiers (a chord) or literal characters (type-then-key) is decided by
    :func:`apply_trip`, which is what actually executes a Trip.
    """
    parts = [p for p in str(spec).split("+") if p != ""]
    if not parts:
        return ([], "")
    return ([_norm_key(p) for p in parts[:-1]], _norm_key(parts[-1]))


def apply_trip(payload, dry=None, log=None) -> list:
    """Execute a Trip payload and return its flat intent list.

    * all leading parts are modifiers -> a chord: ``"ctrl+c"`` ->
      ``[("down","ctrl"),("key","c"),("up","ctrl")]``
    * a leading NON-modifier -> type-then-key: ``"l+Enter"`` ->
      ``[("char","l"),("key","enter")]``
    * a lone named key -> a tap; a lone word -> typed literally
    """
    parts = [p for p in str(payload).split("+") if p != ""]
    if not parts:
        return []
    lead, last = parts[:-1], parts[-1]
    if lead and all(_norm_key(p) in MODIFIERS for p in lead):
        return chord([_norm_key(p) for p in lead], last, dry=dry, log=log)
    if lead:
        intents = []
        for p in lead:
            intents += type_text(p, dry=dry, log=log)
        intents += tap_key(last, dry=dry, log=log)
        return intents
    if _norm_key(last) in KEY_VK:
        return tap_key(last, dry=dry, log=log)
    return type_text(last, dry=dry, log=log)


def run_steps(steps, dry=None, sleep=time.sleep, log=None) -> list:
    """Execute a Run's step list and return the flat intent list.

    Steps are ``[verb, arg]`` pairs: ``type`` (literal text), ``key`` (named
    key), ``chord``/``trip`` (a spec via :func:`apply_trip`), and ``wait``
    (seconds).  A ``wait`` records ``("wait", seconds)``; on a real run it
    sleeps on the caller's thread, on a dry run it is recorded but NOT slept,
    so tests stay instant.
    """
    dry = _dry_default(dry)
    intents = []
    for step in steps or []:
        step = list(step)
        verb = _norm_key(step[0]) if step else ""
        arg = str(step[1]) if len(step) > 1 else ""
        if verb == "type" or verb == "text" or verb == "placard":
            intents += type_text(arg, dry=dry, log=log)
        elif verb == "key":
            intents += tap_key(arg, dry=dry, log=log)
        elif verb in ("chord", "trip", "hotkey"):
            intents += apply_trip(arg, dry=dry, log=log)
        elif verb == "wait" or verb == "sleep":
            try:
                sec = float(arg)
            except ValueError:
                sec = 0.0
            intents.append(("wait", sec))
            if not dry and sec > 0:
                sleep(sec)
        elif verb:
            if log:
                log(f"run_steps: unknown step verb {verb!r} (skipped)")
    return intents


def _is_url(target) -> bool:
    t = str(target).strip().lower()
    return t.startswith(("http://", "https://", "www.", "mailto:", "ftp://"))


def _os_open(target) -> bool:                       # pragma: no cover (desktop)
    """Hand a target to the OS shell.  Never raises; returns launched?."""
    try:
        if sys.platform == "win32" and hasattr(os, "startfile"):
            os.startfile(os.path.expanduser(target))    # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", os.path.expanduser(target)],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", os.path.expanduser(target)],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        return True
    except Exception:      # noqa: BLE001 -- a missing launcher is not fatal
        return False


def open_target(target, is_url=False, dry=None, log=None) -> dict:
    """Open a Fetch target through the OS shell.

    Returns ``{"opened": bool, "is_url": bool, "note": str}`` and NEVER raises.
    Planloom's own process opens no sockets: a URL merely hands off to your
    browser (a separate program) and the note says so.  A missing local path
    reports ``opened=False`` with an honest note.  ``dry`` defaults to
    ``not HAS_SEND`` so headless tests record the decision without spawning
    anything; a real desktop can force ``dry=False`` to actually launch.
    """
    dry = _dry_default(dry)
    url = bool(is_url) or _is_url(target)
    tgt = str(target)
    if url:
        note = (f"'{tgt}' launches your browser (a separate program); "
                "Planloom itself made no network connection")
        if not dry:
            _os_open(tgt)
        if log:
            log(note)
        return {"opened": True, "is_url": True, "note": note}
    path = os.path.expanduser(tgt)
    if not os.path.exists(path):
        note = f"no such file or folder: {path}"
        if log:
            log(note)
        return {"opened": False, "is_url": False, "note": note}
    note = f"opened '{path}' with the system file handler"
    if not dry:
        _os_open(path)
    if log:
        log(note)
    return {"opened": True, "is_url": False, "note": note}


def _keystroke_count(intents) -> int:
    """Keystrokes in an intent list (chars + named keys; mods/waits excluded)."""
    return sum(1 for t in intents if t and t[0] in ("char", "key"))


# =========================================================================
# The Ticker — history + counters
# =========================================================================

@dataclass
class Tick:
    """One dispatched utterance, recorded for the Ticker's log."""

    heard: str
    matched: str
    detail: str
    saved: int
    ts: str = ""

    def to_dict(self) -> dict:
        return {"heard": self.heard, "matched": self.matched,
                "detail": self.detail, "saved": int(self.saved),
                "ts": self.ts}

    @classmethod
    def from_dict(cls, data: dict) -> "Tick":
        return cls(heard=str(data.get("heard", "")),
                   matched=str(data.get("matched", "")),
                   detail=str(data.get("detail", "")),
                   saved=int(data.get("saved", 0) or 0),
                   ts=str(data.get("ts", "")))


def ticker_default_path() -> str:
    """Default lifetime-totals location: ``~/.planloom/holler/ticker.json``."""
    return os.path.join("~", ".planloom", "holler", "ticker.json")


class Ticker:
    """History plus running counters for dispatched commands.

    ``commands`` counts fired commands (a ``miss`` is logged but not counted);
    ``keystrokes_saved`` sums the per-command savings.  Timestamps are passed
    in (``ts=``) — the Ticker never reads the clock itself, so the GUI owns it.
    Lifetime totals may optionally be persisted to JSON.
    """

    RECENT = 20

    def __init__(self, path=None):
        self.history: list = []
        self.commands = 0
        self.keystrokes_saved = 0
        self.path = path

    def record(self, result, ts: str = "") -> Tick:
        """Log a :meth:`Holler.dispatch` result and update the counters."""
        tick = Tick(heard=str(result.get("heard", "")),
                    matched=str(result.get("matched", "miss")),
                    detail=str(result.get("detail", "")),
                    saved=int(result.get("keystrokes_saved", 0) or 0),
                    ts=str(ts or result.get("ts", "")))
        self.history.append(tick)
        if tick.matched != "miss":
            self.commands += 1
            self.keystrokes_saved += tick.saved
        return tick

    def summary(self) -> dict:
        """``{"commands", "keystrokes_saved", "recent": [tick dicts]}``."""
        return {"commands": self.commands,
                "keystrokes_saved": self.keystrokes_saved,
                "recent": [t.to_dict() for t in self.history[-self.RECENT:]]}

    def reset(self) -> None:
        """Clear history and zero the counters."""
        self.history = []
        self.commands = 0
        self.keystrokes_saved = 0

    # -- optional lifetime persistence ------------------------------------
    def save(self, path=None) -> None:
        """Atomically persist lifetime totals + recent history to JSON."""
        path = os.path.expanduser(path or self.path or ticker_default_path())
        self.path = path
        blob = json.dumps(
            {"planloom_holler_ticker": _VERSION,
             "commands": self.commands,
             "keystrokes_saved": self.keystrokes_saved,
             "history": [t.to_dict() for t in self.history[-self.RECENT:]]},
            indent=2, ensure_ascii=False).encode("utf-8")
        _atomic_write(path, blob)

    def load(self, path=None) -> "Ticker":
        """Load lifetime totals (missing / corrupt file -> zeros, no raise)."""
        path = os.path.expanduser(path or self.path or ticker_default_path())
        self.path = path
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return self
        if not isinstance(data, dict):
            return self
        self.commands = int(data.get("commands", 0) or 0)
        self.keystrokes_saved = int(data.get("keystrokes_saved", 0) or 0)
        self.history = [Tick.from_dict(d) for d in (data.get("history") or [])
                        if isinstance(d, dict)]
        return self


# =========================================================================
# The Router — utterance -> action
# =========================================================================

class Holler:
    """The Router: a Songbook, a format profile, and a Ticker.

    :meth:`dispatch` turns a recognized utterance into an action.  Precedence
    is Songbook FIRST (an exact trigger, then the longest word-boundary prefix
    match), ELSE the Caller (a spoken dimension or shape typed as text), ELSE a
    ``miss`` (nothing is sent).  Every dispatch is appended to the Ticker.
    """

    def __init__(self, songbook=None, profile: str = "arch", ticker=None):
        self.songbook = songbook if songbook is not None else Songbook.seed()
        self.profile = profile
        self.ticker = ticker if ticker is not None else Ticker()

    def set_profile(self, name: str) -> None:
        """Switch the Caller's output profile (see :data:`PROFILES`)."""
        self.profile = name

    def reload_songbook(self) -> Songbook:
        """Reload the Songbook from its file if one is set (else no-op)."""
        if self.songbook.path and os.path.exists(
                os.path.expanduser(self.songbook.path)):
            self.songbook.load()
        return self.songbook

    def dispatch(self, utterance, dry=None, ts: str = "", log=None) -> dict:
        """Route ``utterance`` and return the result dict::

            {"heard": str, "matched": "trip|placard|fetch|run|dimension|
                                        shape|miss",
             "detail": str, "intents": [...], "keystrokes_saved": int,
             "note": str}

        ``keystrokes_saved`` is the number of keystrokes the action typed
        minus 1 (the utterance's own "cost"), floored at 0.
        """
        heard = str(utterance)
        note = ""
        entry = self.songbook.find(utterance)
        if entry is not None:
            matched = entry.kind
            if entry.kind == "trip":
                intents = apply_trip(entry.payload, dry=dry, log=log)
                detail = entry.payload
            elif entry.kind == "placard":
                intents = type_text(entry.payload, dry=dry, log=log)
                detail = entry.payload
            elif entry.kind == "fetch":
                res = open_target(entry.payload, is_url=entry.is_url,
                                  dry=dry, log=log)
                intents = []
                detail = entry.payload
                note = res["note"]
            elif entry.kind == "run":
                intents = run_steps(entry.steps, dry=dry, log=log)
                detail = f"run ({len(entry.steps)} steps)"
            else:
                intents, detail, matched = [], entry.payload, "miss"
        else:
            spoken = speak_to_text(utterance, self.profile)
            if spoken["kind"] in ("dimension", "shape"):
                intents = type_text(spoken["text"], dry=dry, log=log)
                detail = spoken["text"]
                matched = spoken["kind"]
            else:
                intents, detail, matched = [], "", "miss"

        saved = max(0, _keystroke_count(intents) - 1)
        result = {"heard": heard, "matched": matched, "detail": detail,
                  "intents": intents, "keystrokes_saved": saved, "note": note}
        self.ticker.record(result, ts=ts)
        return result
