"""Heartwood lexer — trade-aware tokenizer + from-scratch Porter stemmer.

Heartwood has read enough field paperwork to know that '1-1/2"' is one word,
not three, and that a code reference like "NEC 210.8" is a citation you never
take apart.  This module is the mouth of the whole meaning layer: every
downstream skill (vectors, thesaurus, search, digest, restate) sees text only
through it.

Contract:

* PROTECTED tokens survive as single units and are flagged ``is_num``:
  dimensioned numbers (3/4", 2x4, #12, 1-1/2"), code refs (NEC 210.8,
  IBC 1011.5), gauges/sizes (12 AWG), plain numbers with units.  These are
  IMMUTABLE downstream — ``restate`` refuses any output that changes them.
* A trade-aware stopword list: general English filler is dropped, but words
  that carry code meaning ("not", "shall", "min", "max") are KEPT.
* ``stem()`` is a from-scratch implementation of the public-domain Porter
  stemming ALGORITHM (original code, no library).  Protected tokens are
  never stemmed.

Faithful port of a field-proven reference implementation; kept byte-for-byte
compatible where determinism matters.  Pure, deterministic, stdlib only.
"""
from __future__ import annotations

import re
from typing import NamedTuple

# --------------------------------------------------------------- stopwords --
# General English filler that carries no trade meaning.  Deliberately EXCLUDES
# "not", "shall", "min", "max", "no" — in code language those flip requirements.
STOPWORDS = frozenset("""
    a an and are as at be been being but by can could did do does doing done
    for from had has have having he her here hers him his how i if in into is
    it its may me might my of on or our ours out over own she so some such
    than that the their theirs them then there these they this those to too
    up us was we were what when where which while who whom why will with
    would you your yours about after all also any because before between both
    each few more most other same very just only against during under above
    below again further once ourselves themselves himself herself itself
    myself yourself am until through down off via etc ie eg upon onto within
    without per
""".split())

# ----------------------------------------------------- protected trade tokens --
# Ordered: longest / most specific first so the combined regex matches whole
# units before their fragments.  Each alternative is a single protected token.
_PROTECTED_PATTERNS = [
    # Code references: NEC 210.8, IBC 1011.5, UPC 604.1, NFPA 13 5.2.1
    r"\b[A-Z]{2,5}\s?\d{1,4}(?:\.\d{1,4}){0,4}(?:\([a-zA-Z0-9]\))?",
    # Gauge / conductor sizes: 12 AWG, #12, 4/0 AWG
    r"\b\d{1,2}/0\s?AWG\b|\b\d{1,3}\s?AWG\b|#\d{1,3}\b",
    # Dimensioned mixed numbers: 1-1/2", 2 1/2 in, 3/4", 1/2 inch
    r"\b\d+[- ]\d+/\d+\s?(?:\"|''|in\.?|inch(?:es)?)?"
    r"|\b\d+/\d+\s?(?:\"|''|in\.?|inch(?:es)?)?",
    # Lumber / grid sizes: 2x4, 4x8, 24x24
    r"\b\d+\s?[xX]\s?\d+\b",
    # Decimal or whole numbers with an optional unit suffix: 100 psi, 60"
    r"\b\d+(?:\.\d+)?\s?(?:\"|''|%|psi|gpm|gph|cfm|cfh|fpm|fps|kw|kva|hp|btu"
    r"|btuh|mbh|amps?|volts?|va|ft|feet|foot|in\.?|inch(?:es)?|mm|cm|lbs?|kg"
    r"|gal(?:lons?)?|deg(?:rees)?|°[fFcC]?)?\b",
]

# re.ASCII keeps \b, \d and \w on the exact byte-level semantics the reference
# implementation had — a unicode letter must not extend a protected token.
_PROTECTED_RE = re.compile(
    "|".join("(?:" + p + ")" for p in _PROTECTED_PATTERNS), re.ASCII)
_WORD_RE = re.compile(r"[a-z][a-z'-]*")


class Tok(NamedTuple):
    """One token: ``t`` normalized term, ``raw`` surface form, ``is_num``
    protected/numeric (and therefore immutable downstream)."""
    t: str
    raw: str
    is_num: bool


def is_numericish(raw: str) -> bool:
    """Does a token look numeric / measured / cited (=> immutable)?"""
    return any(ch.isdigit() for ch in raw)


def tokenize(text: str) -> list[Tok]:
    """Tokenize preserving protected trade tokens.

    * ``raw``    the surface form as found (trimmed);
    * ``t``      the normalized term: lowercased; protected tokens keep their
      inner structure (spaces collapsed) so "NEC 210.8" == "nec 210.8";
    * ``is_num`` True for protected/numeric tokens.

    Stopwords are dropped.  Order is preserved (needed for sliding windows).
    """
    s = "" if text is None else str(text)
    out: list[Tok] = []

    def emit_words(segment: str) -> None:
        for w in _WORD_RE.findall(segment.lower()):
            clean = w.strip("'-")
            if not clean or clean in STOPWORDS:
                continue
            out.append(Tok(clean, clean, False))

    last = 0
    for m in _PROTECTED_RE.finditer(s):
        if not m.group(0).strip():
            continue
        if m.start() > last:
            emit_words(s[last:m.start()])
        raw = m.group(0).strip()
        t = re.sub(r"\s+", " ", raw.lower())
        out.append(Tok(t, raw, is_numericish(raw)))
        last = m.end()
    if last < len(s):
        emit_words(s[last:])
    return out


# -- Porter stemmer (from-scratch implementation of the public-domain algorithm)

_C = "[^aeiou]"            # consonant
_V = "[aeiouy]"            # vowel
_CS = _C + "[^aeiouy]*"    # consonant sequence
_VS = _V + "[aeiou]*"      # vowel sequence

_MGR0 = re.compile("^(" + _CS + ")?" + _VS + _CS)                       # m > 0
_MEQ1 = re.compile("^(" + _CS + ")?" + _VS + _CS + "(" + _VS + ")?$")   # m = 1
_MGR1 = re.compile("^(" + _CS + ")?" + _VS + _CS + _VS + _CS)           # m > 1
_HAS_V = re.compile("^(" + _CS + ")?" + _V)                             # vowel
_CVC = re.compile("^" + _CS + _V + "[^aeiouwxy]$")

_STEP2_MAP = {
    "ational": "ate", "tional": "tion", "enci": "ence", "anci": "ance",
    "izer": "ize", "bli": "ble", "alli": "al", "entli": "ent", "eli": "e",
    "ousli": "ous", "ization": "ize", "ation": "ate", "ator": "ate",
    "alism": "al", "iveness": "ive", "fulness": "ful", "ousness": "ous",
    "aliti": "al", "iviti": "ive", "biliti": "ble", "logi": "log",
}
_STEP3_MAP = {
    "icate": "ic", "ative": "", "alize": "al", "iciti": "ic", "ical": "ic",
    "ful": "", "ness": "",
}
_STEP4_RE = re.compile(
    r"^(.+?)(al|ance|ence|er|ic|able|ible|ant|ement|ment|ent|ou|ism|ate|iti"
    r"|ous|ive|ize)$")
_STEP4_ION = re.compile(r"^(.+?)(s|t)(ion)$")
_STEM_1B = re.compile(r"^(.+?)(ed|ing)$")
_DOUBLE_C = re.compile(r"([^aeiouylsz])\1$")
_NON_ALPHA = re.compile(r"[^a-z]")


def stem(term: str) -> str:
    """The Porter stemming algorithm, implemented from scratch.

    Protected/numeric tokens are returned untouched (never stem "210.8").
    """
    w = ("" if term is None else str(term)).lower()
    if len(w) < 3 or is_numericish(w) or _NON_ALPHA.search(w):
        return w

    first_y = w[0] == "y"
    if first_y:
        w = "Y" + w[1:]

    # Step 1a — plurals
    if w.endswith("sses"):
        w = w[:-4] + "ss"
    elif w.endswith("ies"):
        w = w[:-3] + "i"
    elif w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]

    # Step 1b — -eed / -ed / -ing
    if w.endswith("eed"):
        part = w[:-3]
        if _MGR0.search(part):
            w = part + "ee"
    else:
        m1b = _STEM_1B.match(w)
        if m1b and _HAS_V.search(m1b.group(1)):
            w = m1b.group(1)
            if w.endswith(("at", "bl", "iz")):
                w += "e"
            elif _DOUBLE_C.search(w):
                w = w[:-1]                       # double consonant
            elif _CVC.search(w):
                w += "e"                         # cvc

    # Step 1c — y -> i, per the algorithm author's later refinement: only when
    # the y follows a consonant that is not the word's first letter ("happy"
    # -> "happi" but "relay" stays "relay" — avoids the enjoy->enjoi wart).
    if w.endswith("y"):
        part = w[:-1]
        if len(part) > 1 and not part[-1].lower() in "aeiouy":
            w = part + "i"

    # Step 2
    for suf, rep in _STEP2_MAP.items():
        if w.endswith(suf):
            part = w[: -len(suf)]
            if _MGR0.search(part):
                w = part + rep
            break

    # Step 3
    for suf, rep in _STEP3_MAP.items():
        if w.endswith(suf):
            part = w[: -len(suf)]
            if _MGR0.search(part):
                w = part + rep
            break

    # Step 4
    m4 = _STEP4_RE.match(w)
    if m4:
        if _MGR1.search(m4.group(1)):
            w = m4.group(1)
    else:
        m_ion = _STEP4_ION.match(w)
        if m_ion and _MGR1.search(m_ion.group(1) + m_ion.group(2)):
            w = m_ion.group(1) + m_ion.group(2)

    # Step 5a — trailing e
    if w.endswith("e"):
        part = w[:-1]
        if _MGR1.search(part) or (_MEQ1.search(part) and not _CVC.search(part)):
            w = part
    # Step 5b — -ll -> -l when m > 1
    if w.endswith("ll") and _MGR1.search(w):
        w = w[:-1]

    if first_y:
        w = "y" + w[1:]
    return w


def terms(text: str) -> list[Tok]:
    """Tokenize then stem the non-protected terms — the exact vocabulary
    stream the vector trainer and searcher share.  Protected tokens pass
    through untouched."""
    return [t if t.is_num else Tok(stem(t.t), t.raw, False)
            for t in tokenize(text)]
