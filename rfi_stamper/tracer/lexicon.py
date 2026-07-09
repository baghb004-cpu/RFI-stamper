"""The Tracer — Phase P3: lexicon / grammar / number-lock post-correction.

A raw 96–98 % character engine (P2) is lifted to ~99 %+ **field** accuracy by
routing every emitted token through a domain language model the app already
owns (OCR_PLAN §4/§2.13).  Nothing here reads pixels; it reasons over the
already-classified text using the repo's own grammars:

* **sheet numbers** — ``core.SHEET_TOKEN`` / ``core.canon`` / ``core.canon_loose``
  plus a cross-check against the document's OWN sheet index (free
  self-supervision): a confused ``O``→``0`` / ``I``→``1`` is snapped to the
  nearest real sheet by a **confusion-weighted** edit distance;
* **dimensions** — a typed feet-inches grammar (``holler.format_ftin`` rebuilds
  the canonical text; ``holler.parse_dimension`` validates spoken forms), repaired
  only within the grammar and **number-locked**;
* **words** — a generic room / CSI / plan lexicon (optionally widened by
  Heartwood trade vocabulary) with a SymSpell delete-2 index and a
  confusion-weighted edit-distance snap (δ≤1, δ≤2 max, cap δ<⌈len/3⌉), a char
  3-gram back-off for out-of-lexicon tokens, and **never a digit string**;
* **numbers / any digit string** — the NUMBER-LOCK, fail-closed: reuse
  ``heartwood.restate.number_multiset`` / ``same_multiset`` (plus a raw
  digit-run guard) so a scanned ``8'`` can never become ``6'``.

Garbage rejection follows OCR_PLAN §5: below ``TAU_LO`` a read is dropped, at or
above ``TAU_HI`` it auto-accepts, and in between it is kept but flagged
low-confidence.

Everything is pure numpy + stdlib, deterministic, offline — the domain hooks are
imported lazily and every one is guarded so the stage degrades to a no-op rather
than ever raising into the pipeline.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from typing import NamedTuple

import numpy as np

from .fonts import CHARSET

# --------------------------------------------------------------------------- #
#  Confidence anchors (OCR_PLAN §5)                                            #
# --------------------------------------------------------------------------- #
TAU_LO = 0.60             # below this a token is garbage → dropped
TAU_HI = 0.90             # at/above this a token auto-accepts
_LIFT_TO = 0.95           # confidence a grammar/index-verified token is lifted to

# --------------------------------------------------------------------------- #
#  Edit-distance thresholds (OCR_PLAN §5 "Edit distance" / "SymSpell")         #
# --------------------------------------------------------------------------- #
WORD_DELTA = 1            # high-confidence snap distance
WORD_DELTA_MAX = 2        # never exceed this raw edit distance
SYMSPELL_MAX_DEL = 2      # precompute dictionary deletes to distance 2
WORD_COST_MAX = 1.6       # weighted-cost ceiling for a word snap
SHEET_COST_MAX = 2.6      # weighted-cost ceiling for a sheet-index snap

# Substitution-cost model (OCR_PLAN §4 "Number-lock" confusion classes).  A
# prior pair is a cheap swap; an arbitrary substitution is capped so a single
# unmodeled error still fits inside δ≤2.
CAP_SUB = 1.3
PRIOR_SUB = 0.4

# The classic machine-print confusion classes named in OCR_PLAN §4: 0/O,
# 1/I/7, 5/6/8, 2/Z, plus a handful of monoline look-alikes and the two prime
# marks (feet ' vs inch ").  Symmetric.
_PRIOR_PAIRS = [
    "0O", "0Q", "0D", "0C",
    "1I", "1L", "1T", "17", "7I", "7T", "IL",
    "5S", "56", "58", "68", "6G", "6B",
    "2Z", "8B", "BR", "VY", "VU", "GC", "MN", "MW", "3E", "9G", "9Q",
    "'\"", "-/", ".,", "./", ".\"", ".'",
]


# precompute a prior-neighbour map for cheap candidate generation
def _build_prior_map():
    m: dict[str, set] = {}
    for p in _PRIOR_PAIRS:
        a, b = p[0], p[1]
        m.setdefault(a, set()).add(b)
        m.setdefault(b, set()).add(a)
    return m


_PRIOR_MAP = _build_prior_map()


def _prior_of(ch: str) -> set:
    return _PRIOR_MAP.get(ch, set())


# --------------------------------------------------------------------------- #
#  Number lock — reuse restate.number_multiset (guarded), plus a raw guard     #
# --------------------------------------------------------------------------- #
_DIGIT_RUN = re.compile(r"\d+")


def _restate_multiset(text: str):
    """``heartwood.restate.number_multiset`` if importable, else ``None``."""
    try:
        from ..heartwood.restate import number_multiset
        return tuple(number_multiset(text))
    except Exception:
        return None


def num_key(text: str) -> tuple:
    """Fail-closed numeric fingerprint of a token.

    Combines the trade-aware protected-token multiset (``restate``) with a raw
    maximal-digit-run multiset, so any change to a digit string — even one the
    trade tokenizer would miss — trips the lock.
    """
    runs = tuple(sorted(_DIGIT_RUN.findall(text)))
    ms = _restate_multiset(text)
    return (ms, runs)


def number_locked(before: str, after: str) -> bool:
    """True iff ``after`` carries the identical numeric fingerprint as ``before``.

    The strict lock (restate protected-token multiset AND raw digit runs) — used
    for word/num tokens, where a digit string must never change at all.
    """
    return num_key(before) == num_key(after)


def digit_locked(before: str, after: str) -> bool:
    """True iff the raw digit multiset is unchanged (marks/units may differ).

    The DIM lock: a feet-inches repair may add or fix a prime/separator mark
    (``8'-6'`` → ``8'-6"``), but it can never change a digit — so a scanned
    ``8'`` can never become ``6'`` (OCR_PLAN §4 "digit multiset").
    """
    return sorted(_DIGIT_RUN.findall(before)) == sorted(_DIGIT_RUN.findall(after))


# --------------------------------------------------------------------------- #
#  Confusion-weighted edit distance                                            #
# --------------------------------------------------------------------------- #
_IDX = {c: i for i, c in enumerate(CHARSET)}


def build_cost_matrix(confusion: np.ndarray | None) -> np.ndarray:
    """43×43 substitution-cost matrix from the model confusion counts + prior.

    ``cost[i, j]`` is the price of reading true class *i* as class *j*.  The
    diagonal is free; every off-diagonal starts at ``CAP_SUB`` and is lowered by
    (a) empirical evidence — where the held-out confusion matrix actually saw
    *i*→*j*, cost drops toward a floor — and (b) the domain prior pairs, which
    are pinned cheap.  Deterministic; ``confusion=None`` yields prior-only costs.
    """
    n = len(CHARSET)
    cost = np.full((n, n), CAP_SUB, np.float64)
    np.fill_diagonal(cost, 0.0)
    if confusion is not None:
        C = np.asarray(confusion, np.float64)
        row = C.sum(1)
        for i in range(n):
            rs = row[i]
            if rs <= 0:
                continue
            for j in range(n):
                if i == j or C[i, j] <= 0:
                    continue
                p = C[i, j] / rs
                cost[i, j] = min(cost[i, j], max(0.25, CAP_SUB - 0.9 * min(1.0, 4.0 * p)))
    for pair in _PRIOR_PAIRS:
        a, b = pair[0], pair[1]
        if a in _IDX and b in _IDX:
            ia, ib = _IDX[a], _IDX[b]
            cost[ia, ib] = min(cost[ia, ib], PRIOR_SUB)
            cost[ib, ia] = min(cost[ib, ia], PRIOR_SUB)
    return cost


def sub_cost(a: str, b: str, cost: np.ndarray | None) -> float:
    """Cost of substituting observed ``a`` for candidate ``b`` (uppercased)."""
    a, b = a.upper(), b.upper()
    if a == b:
        return 0.0
    if cost is not None and a in _IDX and b in _IDX:
        return float(cost[_IDX[a], _IDX[b]])
    if a + b in _PRIOR_PAIRS or b + a in _PRIOR_PAIRS:
        return PRIOR_SUB
    return 1.0


def weighted_edit(a: str, b: str, cost: np.ndarray | None,
                  ins: float = 1.0, dele: float = 1.0) -> float:
    """Levenshtein with confusion-weighted substitutions (a→b)."""
    la, lb = len(a), len(b)
    if la == 0:
        return lb * ins
    if lb == 0:
        return la * dele
    prev = [j * ins for j in range(lb + 1)]
    for i in range(1, la + 1):
        cur = [i * dele] + [0.0] * lb
        ai = a[i - 1]
        for j in range(1, lb + 1):
            s = prev[j - 1] + sub_cost(ai, b[j - 1], cost)
            cur[j] = min(prev[j] + dele, cur[j - 1] + ins, s)
        prev = cur
    return prev[lb]


def _raw_edit(a: str, b: str) -> int:
    """Unweighted Levenshtein — used only to enforce the δ cap."""
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        ai = a[i - 1]
        for j in range(1, lb + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1,
                         prev[j - 1] + (0 if ai == b[j - 1] else 1))
        prev = cur
    return prev[lb]


# --------------------------------------------------------------------------- #
#  Built-in generic lexicon (no client data, no vendor/product names)          #
# --------------------------------------------------------------------------- #
_ROOM_WORDS = """
OFFICE CORRIDOR LOBBY STORAGE MECHANICAL ELECTRICAL RESTROOM TOILET STAIR
STAIRWAY ELEVATOR KITCHEN CLOSET JANITOR CONFERENCE RECEPTION VESTIBULE UTILITY
LAUNDRY GARAGE LOUNGE PANTRY BREAK WAITING EXAM LABORATORY CLASSROOM GYMNASIUM
AUDITORIUM CAFETERIA LIBRARY WAREHOUSE LOADING DOCK ENTRY HALL BEDROOM BATHROOM
LIVING DINING PORCH PATIO ATTIC BASEMENT MEZZANINE OPEN WORKROOM SHOP DATA
SERVER TELECOM RISER SHAFT CHASE PLENUM MAIL BREAKROOM
"""
_CSI_WORDS = """
GENERAL CONCRETE MASONRY METALS WOOD PLASTICS THERMAL MOISTURE OPENINGS FINISHES
SPECIALTIES EQUIPMENT FURNISHINGS CONVEYING FIRE SUPPRESSION PLUMBING HEATING
VENTILATING COOLING INTEGRATED AUTOMATION COMMUNICATIONS SAFETY SECURITY
EARTHWORK UTILITIES TRANSPORTATION PROCESS STRUCTURAL ARCHITECTURAL CIVIL
LANDSCAPE MECHANICAL ELECTRICAL PROTECTION
"""
_PLAN_WORDS = """
SHEET PLAN DETAIL SECTION ELEVATION SCHEDULE DIAGRAM RISER FRAMING FOUNDATION
ROOF FLOOR NOTES TYPICAL SIMILAR DIMENSIONS VERIFY FIELD DRAWING DRAWINGS LEGEND
KEYNOTE SCALE NORTH REVISION DATE TITLE PROJECT NUMBER WINDOW DOOR WALL PARTITION
COLUMN BEAM SLAB GRID EXISTING NEW DEMOLITION PROVIDE INSTALL REMOVE REFER
CONTRACTOR ARCHITECT ENGINEER APPROVED MINIMUM MAXIMUM UNLESS NOTED OTHERWISE
ABOVE BELOW FINISHED GRADE CEILING HEIGHT CODE RATING HOUR REINFORCING SLOPE
INVERT CENTERLINE EQUAL CLEAR OPPOSITE HAND MOUNTING
"""
_BUILTIN_WORDS = frozenset(
    w for chunk in (_ROOM_WORDS, _CSI_WORDS, _PLAN_WORDS) for w in chunk.split())

# Common sheet-prefix letters (discipline codes) — a generic, non-proprietary
# hint set for the sheet grammar; SHEET_TOKEN does the real shape work.
SHEET_PREFIXES = frozenset(
    "A C D E F G H I L M P S T V".split()
    + ["FA", "FP", "AD", "ID", "SP", "PL", "EL", "ME", "GT", "LS"])


def _deletes(word: str, max_d: int) -> set:
    """All strings reachable from ``word`` by ≤ ``max_d`` single deletions."""
    result = {word}
    queue = {word}
    for _ in range(max_d):
        nxt = set()
        for w in queue:
            if len(w) <= 1:
                continue
            for i in range(len(w)):
                nxt.add(w[:i] + w[i + 1:])
        result |= nxt
        queue = nxt
    return result


class Lexicon:
    """Word list + SymSpell delete-index + char 3-gram back-off.

    Holds a generic room/CSI/plan vocabulary (no client data), optionally
    widened by Heartwood trade terms.  ``suggest`` returns the best
    confusion-weighted snap within the δ budget; ``plausible`` scores an
    out-of-lexicon token by its char 3-gram support so a genuinely word-shaped
    token is left verbatim rather than force-corrected.
    """

    def __init__(self, words=None, heartwood_terms=None):
        base = set(_BUILTIN_WORDS)
        for extra in (words, heartwood_terms):
            if extra:
                base |= {str(w).upper() for w in extra
                         if str(w).isalpha() and len(str(w)) >= 2}
        self.words = frozenset(base)
        self._index: dict[str, set] = {}
        for w in self.words:
            for d in _deletes(w, SYMSPELL_MAX_DEL):
                self._index.setdefault(d, set()).add(w)
        self._grams = self._build_grams(self.words)

    # -- construction helpers ------------------------------------------------ #
    @classmethod
    def default(cls) -> "Lexicon":
        return cls()

    @classmethod
    def from_heartwood(cls, path: str | None = None, seed_terms=None) -> "Lexicon":
        """Build the lexicon widened by Heartwood vocabulary (guarded).

        The Heartwood store is consulted only when a path is given AND the store
        loads; every ``similar`` neighbourhood of the supplied ``seed_terms``
        (bounded, no mass sweep) widens the word list.  Any failure degrades to
        the built-in lexicon (offline, honest).
        """
        terms = set()
        probes = list(seed_terms or ())[:64]           # bound the store queries
        if path and probes and os.path.exists(path):
            try:
                from ..heartwood import Heartwood
                hw = Heartwood(path)
                for probe in probes:
                    terms.add(str(probe).upper())
                    try:
                        sim = hw.similar(str(probe))
                    except Exception:
                        continue
                    for nb in sim.get("neighbors", []):
                        t = nb[0] if isinstance(nb, (list, tuple)) else nb
                        if isinstance(t, str) and t.isalpha():
                            terms.add(t.upper())
                    for t in sim.get("thesaurus", []):
                        if isinstance(t, str) and t.isalpha():
                            terms.add(t.upper())
            except Exception:
                pass
        elif seed_terms:
            terms = {str(t).upper() for t in seed_terms if str(t).isalpha()}
        return cls(heartwood_terms=terms)

    @staticmethod
    def _build_grams(words) -> dict:
        grams: dict[str, int] = {}
        for w in words:
            s = "^" + w + "$"
            for i in range(len(s) - 2):
                g = s[i:i + 3]
                grams[g] = grams.get(g, 0) + 1
        return grams

    # -- queries ------------------------------------------------------------- #
    def contains(self, token: str) -> bool:
        return token.upper() in self.words

    def candidates(self, token: str) -> set:
        """SymSpell candidate set for ``token`` (dictionary words near it)."""
        q = token.upper()
        out = set(self._index.get(q, set()))
        for d in _deletes(q, SYMSPELL_MAX_DEL):
            out |= self._index.get(d, set())
        return out

    def suggest(self, token: str, cost: np.ndarray | None):
        """Best in-lexicon snap → ``(word, weighted_cost)`` or ``None``.

        Enforces the OCR_PLAN §5 δ budget: raw edit distance ≤ ``WORD_DELTA_MAX``
        and strictly below ``⌈len/3⌉``, and the weighted cost below
        ``WORD_COST_MAX``.  A δ=1 snap always beats a δ=2 one.
        """
        q = token.upper()
        if q in self.words:
            return (q, 0.0)
        cap = min(WORD_DELTA_MAX, math.ceil(len(q) / 3) - 1)
        if cap < 1:
            return None
        best = None
        for cand in self.candidates(token):
            raw = _raw_edit(q, cand)
            if raw < 1 or raw > cap:
                continue
            wc = weighted_edit(q, cand, cost)
            if wc > WORD_COST_MAX:
                continue
            key = (raw, wc, cand)          # prefer fewer raw edits, then cost
            if best is None or key < best[0]:
                best = (key, cand, wc)
        return None if best is None else (best[1], best[2])

    def plausible(self, token: str) -> float:
        """Char-3-gram support of ``token`` in [0, 1] (back-off for OOV tokens).

        A token made of trigrams the lexicon has seen reads as a plausible word
        and is left verbatim; a low score marks it as unlike any real word.
        """
        q = token.upper()
        if not q:
            return 0.0
        s = "^" + q + "$"
        if len(s) < 3:
            return 1.0
        hit = sum(1 for i in range(len(s) - 2) if s[i:i + 3] in self._grams)
        return hit / float(len(s) - 2)


# --------------------------------------------------------------------------- #
#  Typed feet-inches grammar (OCR reads printed dimensions, not spoken ones)   #
# --------------------------------------------------------------------------- #
# holler.parse_dimension parses *spoken* measures; printed dimensions carry
# digit + prime forms, so the typed grammar lives here and reuses
# holler.format_ftin for canonical rebuild.  Accepts 8'-6", 8'-6 1/2", 6", 8',
# 8'-6, 6 1/2" and the space/no-hyphen variants.
_DIM_FTIN = re.compile(
    r"^\s*(\d+)\s*'\s*-?\s*(\d+)(?:\s+(\d+)\s*/\s*(\d+))?\s*(?:\"|'')?\s*$")
_DIM_FT = re.compile(r"^\s*(\d+)\s*'\s*$")
_DIM_IN = re.compile(r"^\s*(\d+)(?:\s+(\d+)\s*/\s*(\d+))?\s*(?:\"|'')\s*$")


def dim_parse(text: str):
    """Parse a typed dimension → ``(feet, inches, num, den)`` or ``None``.

    Also succeeds on a *spoken* measure via ``holler.parse_dimension`` (guarded)
    so the one validator covers both worlds.
    """
    t = text.strip()
    m = _DIM_FTIN.match(t)
    if m:
        ft = int(m.group(1))
        inch = int(m.group(2))
        num = int(m.group(3)) if m.group(3) else 0
        den = int(m.group(4)) if m.group(4) else 1
        if inch < 12 and (num == 0 or (0 < num < den)):
            return (ft, inch, num, den)
    m = _DIM_FT.match(t)
    if m:
        return (int(m.group(1)), 0, 0, 1)
    m = _DIM_IN.match(t)
    if m:
        inch = int(m.group(1))
        num = int(m.group(2)) if m.group(2) else 0
        den = int(m.group(3)) if m.group(3) else 1
        if num == 0 or (0 < num < den):
            return (0, inch, num, den)
    # spoken fall-through (never fires on printed digit forms, but honours the
    # OCR_PLAN §4 reuse of holler as the dimension grammar)
    try:
        from .. import holler
        if holler.parse_dimension(t) is not None:
            return ()          # parses, but not a typed ft-in tuple
    except Exception:
        pass
    return None


def dim_ok(text: str) -> bool:
    return dim_parse(text) is not None


def dim_canonical(parts) -> str | None:
    """Rebuild a parsed ``(feet, inches, num, den)`` via ``holler.format_ftin``.

    Reuses the repo's own feet-inches formatter (arch profile) so a repaired
    dimension reads in the house style; ``None`` if the parts are not a typed
    ft-in tuple or holler is unavailable.
    """
    if not parts or len(parts) != 4:
        return None
    try:
        from .. import holler
        return holler.format_ftin(parts[0], parts[1], parts[2], parts[3], "arch")
    except Exception:
        return None


# --------------------------------------------------------------------------- #
#  Context                                                                     #
# --------------------------------------------------------------------------- #
_MODEL_PATH = os.path.join(os.path.dirname(__file__), "model.npz")
_COST_CACHE: dict[int, np.ndarray] = {}


def default_cost_matrix() -> np.ndarray:
    """The shipped confusion-cost matrix (loaded once from ``model.npz``)."""
    if not _COST_CACHE:
        conf = None
        try:
            d = np.load(_MODEL_PATH, allow_pickle=False)
            conf = d["confusion"] if "confusion" in d else None
        except Exception:
            conf = None
        _COST_CACHE[0] = build_cost_matrix(conf)
    return _COST_CACHE[0]


@dataclass
class Context:
    """Everything the post-correction stage needs, carried per document.

    ``page_wh`` is ``(W, H)`` in the same units as each token box; when ``None``
    the pipeline fills it from the raster it is reading (so one Context serves a
    whole multi-page document).
    """
    sheet_hints: list = field(default_factory=list)
    lexicon: "Lexicon | None" = None
    confusion: np.ndarray | None = None
    page_wh: tuple | None = None

    @staticmethod
    def build(sheet_hints=None, lexicon=None, heartwood_path=None,
              page_wh=None) -> "Context":
        """Assemble a Context from the pipeline's optional kwargs.

        A non-empty result means the stage runs; ``Context.build()`` with all
        arguments ``None`` still returns a usable (prior-only) Context, so the
        gating on *whether* to correct lives in the caller.
        """
        lex = lexicon
        if lex is None:
            lex = (Lexicon.from_heartwood(heartwood_path,
                                          seed_terms=sorted(_BUILTIN_WORDS))
                   if heartwood_path else Lexicon.default())
        hints = _canon_hints(sheet_hints or [])
        return Context(sheet_hints=hints, lexicon=lex,
                       confusion=default_cost_matrix(), page_wh=page_wh)


def _canon_hints(hints) -> list:
    """Normalize supplied sheet hints to canonical ``LETTERS-NUM`` form, unique."""
    out, seen = [], set()
    for h in hints:
        from ..core import SHEET_TOKEN, canon
        m = SHEET_TOKEN.search(str(h).upper())
        tok = canon(m.group(1), m.group(2)) if m else str(h).upper().strip()
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


# --------------------------------------------------------------------------- #
#  Field classification                                                        #
# --------------------------------------------------------------------------- #
_MARKS = set("-.\"'/#&")


def _in_sheet_region(box, page_wh) -> bool:
    """Right ≤25 % width × bottom ≤25 % height title-block prior (OCR_PLAN §4)."""
    if not box or not page_wh:
        return False
    x0, y0, x1, y1 = box
    W, H = page_wh
    if not W or not H:
        return False
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    return cx >= 0.75 * W and cy >= 0.75 * H


def _looks_sheet(token: str) -> bool:
    from ..core import SHEET_TOKEN
    t = token.upper().strip()
    m = SHEET_TOKEN.fullmatch(t)
    return bool(m) and 1 <= len(m.group(1)) <= 3


# A looser sheet SHAPE: 1–3 letters, optional dash, then a body of digits and
# their digit-confusable look-alikes (O I L S B G Z) with at least one true
# digit — so a confused "S-1O0" / "A-1O1" still routes to the sheet path (and
# gets index-snapped) even when it no longer matches SHEET_TOKEN exactly.
_SHEET_SHAPE = re.compile(r"^[A-Z]{1,3}-?[0-9OILSBGZ][0-9OILSBGZ.'\"]*$")


def _sheet_shaped(token: str) -> bool:
    up = token.upper().strip()
    return bool(_SHEET_SHAPE.match(up)) and any(c.isdigit() for c in up)


def field_of(token_text: str, box=None, page_wh=None, char_scores=None) -> str:
    """Classify a token's field by shape + region prior.

    Returns one of ``"sheet" | "dim" | "word" | "num" | "mark"``.  Shape decides
    first; the bottom-right sheet-number region only *promotes* an ambiguous
    letters+digits token to ``sheet``.
    """
    t = (token_text or "").strip()
    if not t:
        return "mark"
    up = t.upper()
    letters = sum(c.isalpha() for c in up)
    digits = sum(c.isdigit() for c in up)
    if letters == 0 and digits == 0:
        return "mark"
    # a sheet number is checked FIRST: it always carries a 1–3 letter prefix, so
    # a prime-mark misread of its dot ("E-1\"10") must not be stolen by the
    # dimension branch (dimensions never start with letters).
    if _looks_sheet(up) or _sheet_shaped(up):
        return "sheet"
    # printed dimension: a digit next to a prime mark, no sheet prefix
    if digits and ("\"" in t or "'" in t or "''" in t):
        return "dim"
    if letters and digits and _in_sheet_region(box, page_wh):
        return "sheet"
    if letters == 0:
        return "num"
    if digits == 0:
        return "word"
    return "word"


# --------------------------------------------------------------------------- #
#  Per-field correction                                                        #
# --------------------------------------------------------------------------- #
class Tok(NamedTuple):
    """A classified token awaiting correction: text, box (in page_wh units),
    and the word confidence (mean per-char score)."""
    text: str
    box: tuple = ()
    conf: float = 1.0


def _result(text, conf, changed, fieldname, why, keep=True):
    return {"text": text, "conf": float(conf), "changed": bool(changed),
            "field": fieldname, "why": why, "keep": bool(keep)}


def _correct_sheet(text, ctx, char_scores):
    from ..core import SHEET_TOKEN, canon, canon_loose
    up = text.upper().strip()
    m = SHEET_TOKEN.search(up)
    canonical = canon(m.group(1), m.group(2)) if m else up
    if ctx.sheet_hints:
        best = None
        for hint in ctx.sheet_hints:
            raw = _raw_edit(up, hint)
            wc = weighted_edit(up, hint, ctx.confusion)
            key = (wc, raw, hint)
            if best is None or key < best[0]:
                best = (key, hint, wc, raw)
        if best is not None and best[2] <= SHEET_COST_MAX and best[3] <= WORD_DELTA_MAX + 1:
            hint = best[1]
            changed = hint != text
            why = "sheet:index_snap" if changed else "sheet:index_match"
            return _result(hint, max(ctx_conf(char_scores), _LIFT_TO),
                           changed, "sheet", why)
    changed = canonical != text
    # a bare canonicalization is only cosmetic; keep the original confidence
    return _result(canonical, ctx_conf(char_scores), changed, "sheet",
                   "sheet:canon" if changed else "sheet:verbatim")


def ctx_conf(char_scores) -> float:
    """Mean top-1 confidence over the token's glyphs (0 if unavailable)."""
    if not char_scores:
        return 0.0
    vals = []
    for r in char_scores:
        if isinstance(r, (list, tuple)) and r and isinstance(r[0], (list, tuple)):
            vals.append(float(r[0][1]))
        elif isinstance(r, (int, float)):
            vals.append(float(r))
    return float(np.mean(vals)) if vals else 0.0


def _dim_candidates(text, cost, max_delta=WORD_DELTA_MAX):
    """Yield single/double confusion-substitution repairs of a dimension token.

    Only substitutions (no ins/del) are tried, biased to the prior confusion
    neighbours, so the digit multiset is disturbed as little as possible; the
    number-lock is the final arbiter regardless.
    """
    chars = list(text)
    n = len(chars)
    # δ=1
    seen = set()
    for i in range(n):
        for c in _prior_of(chars[i].upper()) | _prior_of(chars[i]):
            cand = "".join(chars[:i]) + c + "".join(chars[i + 1:])
            if cand not in seen:
                seen.add(cand)
                yield cand
    if max_delta >= 2:
        for i in range(n):
            for ci in _prior_of(chars[i].upper()) | _prior_of(chars[i]):
                for j in range(i + 1, n):
                    for cj in _prior_of(chars[j].upper()) | _prior_of(chars[j]):
                        cand = ("".join(chars[:i]) + ci + "".join(chars[i + 1:j])
                                + cj + "".join(chars[j + 1:]))
                        if cand not in seen:
                            seen.add(cand)
                            yield cand


def _correct_dim(text, ctx, char_scores):
    if dim_ok(text):
        return _result(text, max(ctx_conf(char_scores), _LIFT_TO), False,
                       "dim", "dim:parses")
    for cand in _dim_candidates(text, ctx.confusion):
        parts = dim_parse(cand)
        if parts is None:
            continue
        # rebuild in the house feet-inches style via holler (guarded); fall back
        # to the repaired candidate itself if the formatter is unavailable
        out_text = dim_canonical(parts) or cand
        if digit_locked(text, out_text):
            return _result(out_text, max(ctx_conf(char_scores), _LIFT_TO), True,
                           "dim", "dim:grammar_repair")
    # unrepairable within the grammar + lock → leave verbatim (never guess)
    return _result(text, ctx_conf(char_scores), False, "dim", "dim:verbatim")


def _correct_word(text, ctx, char_scores):
    # NUMBER-LOCK: a digit string is never dictionary-snapped
    if any(c.isdigit() for c in text):
        return _result(text, ctx_conf(char_scores), False, "word",
                       "word:has_digit_locked")
    lex = ctx.lexicon
    if lex is None:
        return _result(text, ctx_conf(char_scores), False, "word", "word:no_lexicon")
    if lex.contains(text):
        up = text.upper()
        changed = up != text
        return _result(up, max(ctx_conf(char_scores), _LIFT_TO), changed,
                       "word", "word:in_lexicon")
    sug = lex.suggest(text, ctx.confusion)
    if sug is not None:
        word, _wc = sug
        if number_locked(text, word):          # belt-and-suspenders (no digits)
            return _result(word, max(ctx_conf(char_scores), _LIFT_TO), True,
                           "word", "word:lexicon_snap")
    # char 3-gram back-off: plausible-word-shaped tokens are left verbatim
    return _result(text, ctx_conf(char_scores), False, "word", "word:verbatim")


def correct(tok, char_scores=None, ctx: "Context | None" = None) -> dict:
    """Post-correct one classified token.

    ``tok`` is a :class:`Tok` (``text``/``box``/``conf``) or a bare string.
    Returns ``{"text","conf","changed","field","why","keep"}``.  Routing:

    * garbage below ``TAU_LO`` is dropped (``keep=False``);
    * ``sheet`` → ``core.SHEET_TOKEN`` + confusion-weighted index snap;
    * ``dim``   → typed feet-inches grammar repair, number-locked;
    * ``word``  → SymSpell lexicon snap (never a digit string);
    * ``num``/``mark`` → verbatim, number-locked.

    With ``ctx=None`` the token passes through unchanged (the pipeline's
    zero-context path) — this function is a pure no-op then.
    """
    text = tok.text if isinstance(tok, Tok) else str(tok)
    box = tok.box if isinstance(tok, Tok) else ()
    conf = tok.conf if isinstance(tok, Tok) else ctx_conf(char_scores)
    if ctx is None:
        return _result(text, conf, False, "word", "no_context")

    fieldname = field_of(text, box, ctx.page_wh, char_scores)
    # garbage rejection (OCR_PLAN §5): drop below τ_lo, flag the mid-band
    if conf < TAU_LO:
        return _result(text, conf, False, fieldname, "reject:below_tau_lo",
                       keep=False)

    if fieldname == "sheet":
        out = _correct_sheet(text, ctx, char_scores)
    elif fieldname == "dim":
        out = _correct_dim(text, ctx, char_scores)
    elif fieldname == "word":
        out = _correct_word(text, ctx, char_scores)
    else:                        # num / mark: verbatim, number-locked
        out = _result(text, conf, False, fieldname, "%s:verbatim" % fieldname)

    # honour the incoming confidence when the correction did not verify anything
    if not out["changed"] and out["why"].endswith("verbatim"):
        out["conf"] = conf
    if out["conf"] <= 0.0:          # no per-char scores supplied → fall back
        out["conf"] = conf
    if TAU_LO <= out["conf"] < TAU_HI:
        out["why"] = out["why"] + "|low_conf"
    return out
