"""Heartwood restate — bounded restatement (the only "generation", provably
safe).

Heartwood can say a code sentence in field words, or a field sentence in
code words — and NOTHING else.  Two mechanisms only:

1. Word swaps drawn EXCLUSIVELY from approved thesaurus entries
   (whole-token matches; mode 'plain' = canonical->field, mode 'code' =
   field->canonical).
2. A handful of fixed sentence templates ("X shall Y" -> "The code requires
   X to Y."), applied only when the sentence matches exactly.

The NUMBER LOCK is the hard guarantee: the multiset of numeric/protected
tokens (sizes, code refs, gauges, quantities) is collected before and after;
if the restated text does not carry the IDENTICAL multiset, ``restate()``
returns the verbatim original.  Fail-closed: a wrong number can never leave
this module, because a changed number never leaves this module.

Citations are the caller's duty: every emitted block gets its
" [source: {doc} §{chunk}]" suffix appended by ask.py.
"""
from __future__ import annotations

import re

from . import lex, thesaurus

# Verb-present heuristic for the clause splitter: both halves of a candidate
# split must look like clauses (contain a verb) or the sentence stays intact.
_VERB_RE = re.compile(
    r"\b(?:shall|must|may|is|are|was|were|be|been|being|has|have|had|will"
    r"|would|can|cannot|could|should|do|does|did|requires?|required|means?"
    r"|meant|installs?|installed|provides?|provided|slopes?|sloped"
    r"|supports?|supported|sizes?|sized|seals?|sealed|protects?|protected"
    r"|maintains?|maintained|uses?|used|exceeds?|exceeded|connects?"
    r"|connected|extends?|extended|terminates?|terminated|serves?|served"
    r"|drains?|drained|vents?|vented|keeps?|kept|needs?|needed|allows?"
    r"|allowed|prohibits?|prohibited)\b", re.IGNORECASE)


def number_multiset(text: str) -> list[str]:
    """Multiset (sorted list) of the numeric/protected tokens in a text."""
    return sorted(t.t for t in lex.tokenize(text) if t.is_num)


def same_multiset(a: list[str], b: list[str]) -> bool:
    return a == b


def term_regex(term: str) -> re.Pattern:
    """Whole-token regex for a (possibly multiword, dotted) term."""
    body = re.sub(r"\\\s+|\s+", r"\\s+", re.escape(term))
    return re.compile(r"(?<![A-Za-z0-9-])" + body + r"(?![A-Za-z0-9-])",
                      re.IGNORECASE)


def substitute(sentence: str, mode: str, entries: list[dict]) -> tuple[str, list[dict]]:
    """Substitute approved thesaurus terms, whole-token, longest-first.
    mode 'plain': canonical -> field words; mode 'code': field -> canonical.
    Returns (text, subs=[{from, to}])."""
    pairs = []
    for e in entries:
        if e.get("approved") is not True:
            continue              # approved entries ONLY — the hard rule
        frm = e["canonical"] if mode == "plain" else e["field"]
        to = e["field"] if mode == "plain" else e["canonical"]
        if not frm or not to or thesaurus.norm(frm) == thesaurus.norm(to):
            continue
        pairs.append((frm, to))
    pairs.sort(key=lambda p: (-len(p[0]), p[0]))
    text = sentence
    subs: list[dict] = []

    for frm, to in pairs:
        pat = term_regex(frm)

        def repl(m: re.Match, _to=to) -> str:
            subs.append({"from": m.group(0), "to": _to})
            # keep a leading capital if the surface form had one
            if m.group(0)[:1].isupper():
                return _to[:1].upper() + _to[1:]
            return _to

        text = pat.sub(repl, text)
    return text, subs


def _looks_like_clause(s: str) -> bool:
    """Does this text read as a clause (verb-present heuristic)?"""
    t = str(s).strip()
    return len(t.split()) >= 2 and bool(_VERB_RE.search(t))


def split_clauses(sentence: str) -> list[str]:
    """Split a sentence on "; " and " and " ONLY where both halves parse as
    clauses; otherwise return it intact.  ("pipe and fittings" never splits
    — "fittings" is not a clause.)"""
    s = str(sentence).strip()
    for sep in ("; ", " and "):       # semicolon first (stronger separator)
        at = s.find(sep)
        if at > 0:
            left = s[:at].strip()
            right = s[at + len(sep):].strip()
            if _looks_like_clause(left) and _looks_like_clause(right):
                return split_clauses(left) + split_clauses(right)
    return [s]


def _bare(s: str) -> str:
    """Trim a clause for template embedding: drop the trailing period."""
    return re.sub(r"[.]\s*$", "", str(s).strip())


def apply_template(clause: str) -> tuple[str, bool]:
    """Apply the fixed statement templates to ONE clause.  Returns the
    transformed clause (always ending in '.') and whether a template fired."""
    c = _bare(clause)
    m = re.match(r"^(.+?)\s+shall\s+not\s+(.+)$", c, re.IGNORECASE)
    if m:  # "X shall not Y" -> "The code prohibits X from Y."
        return f"The code prohibits {_bare(m.group(1))} from {_bare(m.group(2))}.", True
    m = re.match(r"^(.+?)\s+shall\s+(.+)$", c, re.IGNORECASE)
    if m:  # "X shall Y" -> "The code requires X to Y."
        return f"The code requires {_bare(m.group(1))} to {_bare(m.group(2))}.", True
    # Definitions: "X: Y" or "X means Y" -> "X — that is, Y."
    m = re.match(r"^([^:]{2,60}):\s+(.+)$", c)
    if m and not re.search(r"\d:\d", c):
        return f"{_bare(m.group(1))} — that is, {_bare(m.group(2))}.", True
    m = re.match(r"^(.{2,60}?)\s+means\s+(.+)$", c, re.IGNORECASE)
    if m:
        return f"{_bare(m.group(1))} — that is, {_bare(m.group(2))}.", True
    return c + ".", False


def restate(sentence: str, mode: str, entries: list[dict] | None = None,
            store=None) -> dict:
    """Restate one sentence.  Returns {text, changed, safe, subs, templated}.

    * mode 'plain': speak the code sentence in field words;
    * mode 'code':  speak the field sentence in code words.

    Fail-closed: any violation of the number lock returns the verbatim
    original with changed=False.  ``safe`` is always True on return — unsafe
    text is unreachable output by construction.  Entry source defaults to
    the store's seed+approved thesaurus (pass ``entries`` to override)."""
    original = "" if sentence is None else str(sentence)
    fallback = {"text": original, "changed": False, "safe": True,
                "subs": [], "templated": False}
    if not original.strip() or mode not in ("plain", "code"):
        return fallback

    lock = number_multiset(original)

    # 1. Clause split (conservative) + templates per clause.
    templated = False
    clauses = split_clauses(original)
    parts = []
    for cl in clauses:
        text, fired = apply_template(cl)
        templated = templated or fired
        parts.append(text)
    rebuilt = " ".join(parts)
    # No template fired and no split happened -> keep the exact original
    # wording (never add punctuation for punctuation's sake).
    if not templated and len(clauses) == 1:
        rebuilt = original

    # 2. Approved-thesaurus substitutions, whole-token.
    if entries is None:
        entries = (thesaurus.entries(store) if store is not None
                   else [dict(e, approved=True)
                         for e in thesaurus.seed_entries()])
    swapped, subs = substitute(rebuilt, mode, entries)

    # 3. NUMBER LOCK — the identical multiset or nothing.
    if not same_multiset(lock, number_multiset(swapped)):
        return fallback

    changed = swapped != original
    return {"text": swapped if changed else original, "changed": changed,
            "safe": True, "subs": subs, "templated": templated}
