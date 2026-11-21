"""The Paper Trail: proof records (BATTERBOARD_PLAN.md; owner brief section 7).

Nothing prints without evidence.  A paper trail is the chain of documents
that lets every claim be walked back to where it came from; this module is
that chain for the Batterboard.  It is GUI-free and holds no geometry: it
only knows records, statements, references and words.

The rules it enforces (each one is a regression case in the brief):

1. **Proof records.**  A :class:`Proof` is one check: its id, what was
   compared with what, which elements, the per-class counts, the result,
   the exact source quotes and what was NOT compared.
2. **Statements are bound to check ids.**  A sentence is rendered only by
   :meth:`Trail.say` from a template registered for the proof's check.  The
   literal text of a template may hold no digit and none of the words
   all / none / every / no: every number and every quantifier comes from
   the proof record through a slot.  :func:`quant` is the only producer of
   "all" and "none" and :func:`absent` the only producer of "no"; both
   refuse unless the scope covers the whole class and the count supports
   the word.  Otherwise the counts print, for example "12 of 34".
3. **Coverage.**  ``{not_compared}`` prints what was not compared, and
   ``{classes:COUNTER}`` names exactly the classes a check covered.
4. **References resolve.**  :func:`find_refs` reads the sheet, item,
   detail and RFI references out of any text; :func:`unresolved` returns
   the ones an :class:`Inventory` of this run cannot resolve.  One
   unresolved reference blocks delivery.
5. **Lint.**  :func:`lint_text` / :func:`lint_pdf` / :func:`lint_file`
   reject long dashes, British spellings, how-to wording and tool names;
   :func:`lint_metadata` accepts file metadata only when it equals a
   project settings value.  Exact source quotes are evidence and exempt.

Program wording versus evidence: template literals, proof values and the
``compared`` / ``against`` / ``level`` fields are PROGRAM wording, so they
are linted and may not carry a quantifier word.  Text copied from a
document belongs in a :class:`Quote` (printed through ``{q:INDEX}``) or,
for a short inline string such as a sheet title, in a :class:`Verbatim`
value; both are exempt and are listed on the statement (``verbatim``) so a
later file lint can exempt them too.

Template slots::

    {compared} {against} {level}   proof fields ({against} refuses when the
                                   proof has no second source)
    {v:NAME}                       proof.values[NAME], printed as stored
    {n:CLASS:COUNTER}              quantified phrase, "12 of 34 wall corners"
    {c:CLASS:COUNTER}              bare count and noun, "3 round columns"
    {s:CLASS:COUNTER:has|have}     first word when the count is 1, else second
    {no:CLASS:COUNTER}             "no <noun>", only when the count is zero
                                   over the whole class
    {classes:COUNTER}              the {n:..} phrase of every compared class
    {not_compared}                 the coverage sentence
    {q:INDEX}                      the exact quote with its citation

Fully offline: stdlib + PyMuPDF (only inside :func:`lint_pdf`).
"""
from __future__ import annotations

import json
import os
import re
import zipfile
from dataclasses import dataclass, field

from .sheets import TOKEN_FULL

__all__ = [
    "OverstatedError", "TemplateError", "Verbatim", "Quote", "Proof", "Ref",
    "Statement", "Trail", "TEMPLATES", "register", "quant", "absent",
    "find_refs", "Inventory", "unresolved", "LintHit", "lint_text",
    "lint_metadata", "lint_pdf", "lint_file", "can_lint", "plain", "BRITISH",
]


class OverstatedError(ValueError):
    """A statement claims more than its proof record supports."""


class TemplateError(ValueError):
    """A template breaks the literal-text rules or names an unknown slot."""


class Verbatim(str):
    """A proof value copied character for character from a source document
    (a sheet title, a mark as drawn).  It prints as stored and, like a
    quote, is exempt from the wording rules because it is evidence."""


# ------------------------------------------------------------------ words --

_LONG_DASHES = "\u2012\u2013\u2014\u2015"
_ODD_SPACES = "\u00a0\u202f\u2007"
_QUANT_WORDS = re.compile(r"\b(all|none|every|no)\b", re.IGNORECASE)

RESULTS = ("match", "flag", "info", "skip")


def plain(text) -> str:
    """Make text that comes from ANOTHER module printable here: a spaced
    em dash becomes a comma, every other long dash a hyphen, and the
    non-breaking spaces become ordinary spaces."""
    s = "" if text is None else str(text)
    for ch in _ODD_SPACES:
        s = s.replace(ch, " ")
    s = s.replace(" \u2014 ", ", ")
    for ch in _LONG_DASHES:
        s = s.replace(ch, "-")
    return s


def _quant_word(text):
    m = _QUANT_WORDS.search(str(text))
    return m.group(0) if m else None


def _check_literal(text: str, what: str) -> None:
    for ch in text:
        if ch.isnumeric():
            raise TemplateError(
                f"{what}: literal text holds the digit {ch!r}; a number "
                "must come from the proof record through a slot")
    w = _quant_word(text)
    if w:
        raise TemplateError(
            f"{what}: literal text holds the word {w!r}; a quantifier "
            "must come from the proof counts through a slot")


def _check_program_text(text, what: str) -> None:
    """Proof-supplied program wording may carry numbers, never a
    quantifier word (that would be a count claim with no count)."""
    if isinstance(text, Verbatim):
        return
    w = _quant_word(text)
    if w:
        raise OverstatedError(
            f"{what} holds the word {w!r} with no count behind it; state "
            "it through a class counter, or pass document text as a "
            "Quote / Verbatim")


# ---------------------------------------------------------------- records --

@dataclass
class Quote:
    """One exact source quote with where it sits: sheet, the heading above
    it in the same column, and its note number."""
    text: str
    sheet: str = ""
    heading: str = ""
    note_no: str = ""
    file: str = ""
    page: int = 0

    def cite(self) -> str:
        """``S-001, CONCRETE, note 4`` (empty parts omitted).  The file
        name is deliberately never part of a citation."""
        parts = []
        if str(self.sheet).strip():
            parts.append(str(self.sheet).strip())
        if str(self.heading).strip():
            parts.append(str(self.heading).strip())
        if str(self.note_no).strip():
            parts.append(f"note {str(self.note_no).strip()}")
        return ", ".join(parts)

    def to_dict(self) -> dict:
        return {"text": self.text, "sheet": self.sheet,
                "heading": self.heading, "note_no": self.note_no,
                "file": self.file, "page": self.page}

    @classmethod
    def from_dict(cls, d: dict) -> "Quote":
        return cls(text=str(d.get("text", "")), sheet=str(d.get("sheet", "")),
                   heading=str(d.get("heading", "")),
                   note_no=str(d.get("note_no", "")),
                   file=str(d.get("file", "")), page=int(d.get("page", 0) or 0))


def _as_quote(q) -> Quote:
    if isinstance(q, Quote):
        return q
    if isinstance(q, dict):
        return Quote.from_dict(q)
    raise ValueError(f"not a Quote: {q!r}")


def _value_out(v):
    return {"verbatim": str(v)} if isinstance(v, Verbatim) else v


def _value_in(v):
    if isinstance(v, dict) and set(v) == {"verbatim"}:
        return Verbatim(v["verbatim"])
    return v


@dataclass
class Proof:
    """One check, recorded: the evidence a statement prints from."""
    id: str = ""
    check: str = ""
    level: str = ""
    compared: str = ""
    against: str = ""
    classes: dict = field(default_factory=dict)
    values: dict = field(default_factory=dict)
    elements: list = field(default_factory=list)
    result: str = "info"
    quotes: list = field(default_factory=list)
    not_compared: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "check": self.check, "level": self.level,
            "compared": self.compared, "against": self.against,
            "classes": {k: dict(v, noun=list(v.get("noun", ())))
                        for k, v in self.classes.items()},
            "values": {k: _value_out(v) for k, v in self.values.items()},
            "elements": list(self.elements), "result": self.result,
            "quotes": [_as_quote(q).to_dict() for q in self.quotes],
            "not_compared": list(self.not_compared),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Proof":
        return cls(
            id=str(d.get("id", "")), check=str(d.get("check", "")),
            level=str(d.get("level", "")),
            compared=str(d.get("compared", "")),
            against=str(d.get("against", "")),
            classes={str(k): dict(v) for k, v in
                     (d.get("classes") or {}).items()},
            values={str(k): _value_in(v) for k, v in
                    (d.get("values") or {}).items()},
            elements=list(d.get("elements") or []),
            result=str(d.get("result", "info")),
            quotes=[Quote.from_dict(q) for q in (d.get("quotes") or [])],
            not_compared=[str(s) for s in (d.get("not_compared") or [])])


def _count(v, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise OverstatedError(f"{what} is not a whole count: {v!r}")
    if v < 0:
        raise OverstatedError(f"{what} is negative: {v}")
    return v


def _noun(noun, what: str = "noun") -> tuple:
    if (not isinstance(noun, (list, tuple)) or len(noun) != 2
            or not all(isinstance(s, str) and s.strip() for s in noun)):
        raise ValueError(f"{what} must be (singular, plural), got {noun!r}")
    for s in noun:
        _check_program_text(s, f"{what} {s!r}")
    return str(noun[0]), str(noun[1])


def _validate(proof: Proof) -> None:
    """Refuse a malformed or self-overstating proof record, loudly and at
    the place it is made."""
    if not isinstance(proof.check, str) or not proof.check.strip():
        raise ValueError("a proof needs a check id")
    if proof.result not in RESULTS:
        raise ValueError(f"unknown proof result {proof.result!r}; expected "
                         f"one of {RESULTS}")
    for name in ("level", "compared", "against"):
        _check_program_text(getattr(proof, name), f"proof field {name!r}")
    if not isinstance(proof.classes, dict):
        raise ValueError("proof.classes must be a dict")
    for key, c in proof.classes.items():
        what = f"class {key!r}"
        if not isinstance(c, dict):
            raise ValueError(f"{what} must be a dict of counts")
        for need in ("noun", "total", "compared"):
            if need not in c:
                raise ValueError(f"{what} is missing {need!r}")
        _noun(c["noun"], f"{what} noun")
        total = _count(c["total"], f"{what} total")
        compared = _count(c["compared"], f"{what} compared")
        if compared > total:
            raise OverstatedError(
                f"{what}: compared {compared} exceeds total {total}")
        for k, v in c.items():
            if k not in ("noun", "total", "compared"):
                _count(v, f"{what} counter {k!r}")
    if not isinstance(proof.values, dict):
        raise ValueError("proof.values must be a dict")
    for k, v in proof.values.items():
        if isinstance(v, bool) or not isinstance(v, (str, int, float)):
            raise ValueError(f"proof value {k!r} must be text or a number")
        _check_program_text(v, f"proof value {k!r}")
    for s in proof.not_compared:
        if not isinstance(s, str) or not s.strip():
            raise ValueError("not_compared entries must be plain phrases")
        _check_program_text(s, f"not_compared entry {s!r}")
    proof.quotes = [_as_quote(q) for q in proof.quotes]
    proof.elements = list(proof.elements)
    proof.not_compared = list(proof.not_compared)


@dataclass(frozen=True)
class Ref:
    """A printed reference: kind is sheet | item | detail | rfi."""
    kind: str
    value: str


@dataclass
class Statement:
    """One rendered sentence with the proof it came from.  ``refs`` is
    every reference it prints; ``explicit`` is the subset the template
    DECLARED through a ``{v:sheet}``-style value (the rest were read out of
    the text).  ``quotes`` are the exact quote texts it embeds;
    ``verbatim`` is every source string it embeds (quote texts, their
    headings, Verbatim values), ready to be passed to a file lint as
    exempt text."""
    text: str = ""
    proof_id: str = ""
    check: str = ""
    key: str = ""
    refs: list = field(default_factory=list)
    quotes: list = field(default_factory=list)
    verbatim: list = field(default_factory=list)
    explicit: list = field(default_factory=list)

    def to_dict(self) -> dict:
        def out(refs):
            return [{"kind": r.kind, "value": r.value} for r in refs]
        return {"text": self.text, "proof_id": self.proof_id,
                "check": self.check, "key": self.key,
                "refs": out(self.refs), "explicit": out(self.explicit),
                "quotes": list(self.quotes), "verbatim": list(self.verbatim)}

    @classmethod
    def from_dict(cls, d: dict) -> "Statement":
        def back(name):
            return [Ref(str(r["kind"]), str(r["value"]))
                    for r in (d.get(name) or [])]
        return cls(text=str(d.get("text", "")),
                   proof_id=str(d.get("proof_id", "")),
                   check=str(d.get("check", "")), key=str(d.get("key", "")),
                   refs=back("refs"), explicit=back("explicit"),
                   quotes=[str(q) for q in (d.get("quotes") or [])],
                   verbatim=[str(q) for q in (d.get("verbatim") or [])])


# -------------------------------------------------------------- quantifiers --

def quant(n, total, compared, noun) -> str:
    """The quantified phrase for ``n`` of a class: the ONLY producer of
    "all" and "none".

    ``all 34 wall corners`` and ``none of the 34 wall corners`` print only
    when the whole class was compared (``compared == total``) and the
    count supports the word; otherwise the counts print (``12 of 34 wall
    corners``).  When only part of the class was compared the phrase says
    so (``12 of the 20 compared wall corners (34 in total)``) and never
    uses either word."""
    c = _count(n, "count")
    t = _count(total, "total")
    k = _count(compared, "compared")
    if k > t:
        raise OverstatedError(f"compared {k} exceeds total {t}")
    if c > k:
        raise OverstatedError(f"count {c} exceeds the {k} compared")
    singular, plural = _noun(noun)
    word = singular if t == 1 else plural
    if k < t:
        return f"{c} of the {k} compared {word} ({t} in total)"
    if t == 0:
        return f"0 of 0 {plural}"
    if c == t:
        return f"the 1 {singular}" if t == 1 else f"all {t} {plural}"
    if c == 0:
        return f"0 of 1 {singular}" if t == 1 else f"none of the {t} {plural}"
    return f"{c} of {t} {word}"


def absent(n, total, compared, noun) -> str:
    """``no <noun>``: the ONLY producer of "no".  It refuses unless the
    whole class was looked at and the count is zero."""
    c = _count(n, "count")
    t = _count(total, "total")
    k = _count(compared, "compared")
    singular, _plural = _noun(noun)
    if k != t:
        raise OverstatedError(
            f"cannot say 'no {singular}': only {k} of {t} were compared")
    if c != 0:
        raise OverstatedError(
            f"cannot say 'no {singular}': the count is {c}")
    return f"no {singular}"


# ---------------------------------------------------------------- templates --

#: (check id, key) -> template text.  Filled by :func:`register`.
TEMPLATES: dict = {}

_PLAIN_SLOTS = ("compared", "against", "level", "not_compared")
_REF_VALUE = re.compile(r"^(sheet|item|rfi|detail)(\d*|_.*)$")


def _parse(template: str) -> list:
    """Template -> [("lit", text) | ("slot", body)]."""
    parts, buf, i, n = [], [], 0, len(template)
    while i < n:
        ch = template[i]
        if ch == "{":
            j = template.find("}", i + 1)
            if j < 0 or "{" in template[i + 1:j]:
                raise TemplateError(f"unbalanced '{{' in template: "
                                    f"{template!r}")
            if buf:
                parts.append(("lit", "".join(buf)))
                buf = []
            parts.append(("slot", template[i + 1:j]))
            i = j + 1
        elif ch == "}":
            raise TemplateError(f"unbalanced '}}' in template: {template!r}")
        else:
            buf.append(ch)
            i += 1
    if buf:
        parts.append(("lit", "".join(buf)))
    return parts


def _slot_spec(body: str) -> tuple:
    """Validate a slot body's shape -> (kind, args...)."""
    if body in _PLAIN_SLOTS:
        return (body,)
    bits = body.split(":")
    kind = bits[0]
    if kind == "v" and len(bits) == 2 and bits[1]:
        return ("v", bits[1])
    if kind in ("n", "c", "no") and len(bits) == 3 and bits[1] and bits[2]:
        return (kind, bits[1], bits[2])
    if kind == "s" and len(bits) == 4 and bits[1] and bits[2]:
        words = bits[3].split("|")
        if len(words) != 2 or not all(w.strip() for w in words):
            raise TemplateError(
                f"slot {{{body}}}: expected two words as one|many")
        for w in words:
            _check_literal(w, f"slot {{{body}}}")
        return ("s", bits[1], bits[2], words[0], words[1])
    if kind == "q" and len(bits) == 2 and bits[1].isdigit():
        return ("q", int(bits[1]))
    if kind == "classes" and len(bits) == 2 and bits[1]:
        return ("classes", bits[1])
    raise TemplateError(f"unknown slot {{{body}}}")


def _compile(template: str, what: str) -> list:
    if not isinstance(template, str) or not template.strip():
        raise TemplateError(f"{what}: empty template")
    out = []
    for tag, text in _parse(template):
        if tag == "lit":
            _check_literal(text, what)
            out.append(("lit", text))
        else:
            out.append(("slot", _slot_spec(text)))
    return out


def register(check: str, key: str, template: str) -> None:
    """Bind a statement template to a check id.  Registering the identical
    text again is a no-op; different text for the same (check, key) is an
    error, so a statement can never be quietly reworded elsewhere."""
    if not isinstance(check, str) or not check.strip():
        raise TemplateError("a template needs a check id")
    if not isinstance(key, str) or not key.strip():
        raise TemplateError("a template needs a key")
    _compile(template, f"template ({check}, {key})")
    have = TEMPLATES.get((check, key))
    if have is not None and have != template:
        raise TemplateError(
            f"template ({check}, {key}) is already registered with "
            "different text")
    TEMPLATES[(check, key)] = template


def _class(proof: Proof, name: str, body: str) -> dict:
    c = proof.classes.get(name)
    if not isinstance(c, dict):
        raise TemplateError(f"slot {{{body}}}: proof {proof.id} has no "
                            f"class {name!r}")
    for need in ("noun", "total", "compared"):
        if need not in c:
            raise TemplateError(f"slot {{{body}}}: class {name!r} is "
                                f"missing {need!r}")
    return c


def _counter(c: dict, name: str, counter: str, body: str) -> int:
    if counter not in c or counter == "noun":
        raise TemplateError(f"slot {{{body}}}: class {name!r} has no "
                            f"counter {counter!r}")
    return _count(c[counter], f"class {name!r} counter {counter!r}")


def _render(proof: Proof, compiled: list):
    """-> (pieces, quote_texts, verbatim, explicit_refs); a piece is
    (text, is_evidence)."""
    pieces, quotes, verbatim, refs = [], [], [], []

    def prog(text, what):
        _check_program_text(text, what)
        pieces.append((str(text), False))

    for tag, spec in compiled:
        if tag == "lit":
            pieces.append((spec, False))
            continue
        kind = spec[0]
        body = ":".join(str(s) for s in spec)
        if kind in ("compared", "level"):
            prog(getattr(proof, kind), f"proof field {kind!r}")
        elif kind == "against":
            if not str(proof.against).strip():
                raise OverstatedError(
                    f"proof {proof.id} ({proof.check}) has no second "
                    "source; a statement must not imply one")
            prog(proof.against, "proof field 'against'")
        elif kind == "not_compared":
            for s in proof.not_compared:
                _check_program_text(s, f"not_compared entry {s!r}")
            if proof.not_compared:
                pieces.append(("Not compared: "
                               + "; ".join(str(s) for s in proof.not_compared)
                               + ".", False))
            else:
                pieces.append(("Not compared: nothing in this scope.", False))
        elif kind == "v":
            name = spec[1]
            if name not in proof.values:
                raise TemplateError(f"slot {{{body}}}: proof {proof.id} "
                                    f"has no value {name!r}")
            val = proof.values[name]
            if isinstance(val, Verbatim):
                pieces.append((str(val), True))
                verbatim.append(str(val))
            else:
                prog(val, f"proof value {name!r}")
            m = _REF_VALUE.match(name)
            if m:
                refs.extend(_explicit_refs(m.group(1), str(val)))
        elif kind in ("n", "c", "no", "s"):
            c = _class(proof, spec[1], body)
            count = _counter(c, spec[1], spec[2], body)
            total = _count(c["total"], f"class {spec[1]!r} total")
            compared = _count(c["compared"], f"class {spec[1]!r} compared")
            if kind == "n":
                pieces.append((quant(count, total, compared, c["noun"]), False))
            elif kind == "no":
                pieces.append((absent(count, total, compared, c["noun"]),
                               False))
            elif kind == "c":
                singular, plural = _noun(c["noun"])
                pieces.append((f"{count} {singular if count == 1 else plural}",
                               False))
            else:
                pieces.append((spec[3] if count == 1 else spec[4], False))
        elif kind == "classes":
            phrases = []
            for name, c in proof.classes.items():
                c = _class(proof, name, body)
                if _count(c["compared"], f"class {name!r} compared") <= 0:
                    continue
                phrases.append(quant(_counter(c, name, spec[1], body),
                                     c["total"], c["compared"], c["noun"]))
            if not phrases:
                raise OverstatedError(
                    f"proof {proof.id} ({proof.check}) compared nothing; "
                    "there is no class to state")
            joined = (phrases[0] if len(phrases) == 1 else
                      ", ".join(phrases[:-1]) + " and " + phrases[-1])
            pieces.append((joined, False))
        elif kind == "q":
            idx = spec[1]
            if idx >= len(proof.quotes):
                raise TemplateError(f"slot {{{body}}}: proof {proof.id} "
                                    f"has {len(proof.quotes)} quote(s)")
            q = _as_quote(proof.quotes[idx])
            cite = q.cite()
            pieces.append(('"' + q.text + '"'
                           + (f" ({cite})" if cite else ""), True))
            quotes.append(q.text)
            verbatim.append(q.text)
            if str(q.heading).strip():
                verbatim.append(str(q.heading).strip())
        else:                                       # pragma: no cover
            raise TemplateError(f"unknown slot {{{body}}}")
    return pieces, quotes, verbatim, refs


def _dedupe(seq) -> list:
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


class Trail:
    """The run's proof records and the print manifest (every statement
    said).  Proof ids are sequential (``P-0001``) and never reused, also
    across a ``to_dict`` / ``from_dict`` round trip."""

    def __init__(self):
        self._proofs: dict = {}
        self._next = 1
        self.statements: list = []

    # -- records ----------------------------------------------------------
    def add(self, check, **fields_) -> Proof:
        """Record one check.  ``fields_`` are :class:`Proof` fields other
        than ``id`` and ``check``; a malformed or self-overstating record
        is refused here, where it is made."""
        allowed = set(Proof.__dataclass_fields__) - {"id", "check"}
        unknown = sorted(set(fields_) - allowed)
        if unknown:
            raise TypeError(f"unknown proof field(s): {', '.join(unknown)}")
        proof = Proof(id=f"P-{self._next:04d}", check=check, **fields_)
        _validate(proof)
        self._next += 1
        self._proofs[proof.id] = proof
        return proof

    def get(self, proof_id) -> Proof:
        return self._proofs[proof_id]

    def has(self, proof_id) -> bool:
        return proof_id in self._proofs

    def proofs(self, check=None, level=None) -> list:
        return [p for p in self._proofs.values()
                if (check is None or p.check == check)
                and (level is None or p.level == level)]

    # -- statements -------------------------------------------------------
    def say(self, proof_id, key="summary") -> Statement:
        """Render the template registered for ``(proof.check, key)`` from
        the proof record alone, and add it to the print manifest.  Nothing
        typed by hand can enter a statement: there are no free values."""
        proof = self.get(proof_id)
        template = TEMPLATES.get((proof.check, key))
        if template is None:
            raise TemplateError(f"no template is registered for check "
                                f"{proof.check!r}, key {key!r}")
        what = f"template ({proof.check}, {key})"
        _validate(proof)
        raw, quotes, verbatim, refs = _render(
            proof, _compile(template, what))
        pieces = []
        for t_, e in raw:
            # a value such as "1/4 in." that ends a sentence: one period
            if (pieces and not e and not pieces[-1][1]
                    and pieces[-1][0].endswith(".") and t_.startswith(".")):
                t_ = t_[1:]
            pieces.append((t_, e))
        text = "".join(t for t, _e in pieces).strip()
        masked = "".join(" " * len(t) if e else t for t, e in pieces)
        hits = lint_text(masked, where=what)
        if hits:
            raise TemplateError(
                f"{what} renders text that fails the lint: "
                + ", ".join(f"{h.kind} {h.token!r}" for h in hits))
        st = Statement(text=text, proof_id=proof.id, check=proof.check,
                       key=key, refs=_dedupe(refs + find_refs(text)),
                       quotes=quotes, verbatim=_dedupe(verbatim),
                       explicit=_dedupe(refs))
        self.statements.append(st)
        return st

    # -- persistence ------------------------------------------------------
    def to_dict(self) -> dict:
        return {"version": 1, "next": self._next,
                "proofs": [p.to_dict() for p in self._proofs.values()],
                "statements": [s.to_dict() for s in self.statements]}

    @classmethod
    def from_dict(cls, d: dict) -> "Trail":
        t = cls()
        for pd in d.get("proofs") or []:
            p = Proof.from_dict(pd)
            t._proofs[p.id] = p
        top = 0
        for pid in t._proofs:
            m = re.match(r"^P-(\d+)$", pid)
            if m:
                top = max(top, int(m.group(1)))
        t._next = max(int(d.get("next", 1) or 1), top + 1)
        t.statements = [Statement.from_dict(s)
                        for s in d.get("statements") or []]
        return t


# --------------------------------------------------------------- references --

#: a cue that makes ANY sheet-shaped token after it a sheet reference
_STRONG_CUES = frozenset(("SEE", "SHEET", "SHEETS", "SHT", "SHTS", "DWG",
                          "DWGS", "DRAWING", "DRAWINGS"))
#: a cue too common to trust alone: the token must also carry a hyphen or
#: a point ("ON S-201"), so "ON L2" / "PER SW1" never become sheets
_WEAK_CUES = frozenset(("PER", "ON"))
_CONNECTORS = frozenset(("AND", "OR", "THRU", "THROUGH", "TO"))
#: words after which a designation is a standard or a product grade, not a
#: sheet: standards-issuer acronyms and grade words
_DESIGNATION_CUES = frozenset((
    "ASTM", "AWS", "ACI", "AISC", "AISI", "ANSI", "ASCE", "ASME", "AASHTO",
    "CRSI", "ICC", "IBC", "NFPA", "ISO", "CSA", "SSPC",
    "TYPE", "GRADE", "GR", "CLASS", "SERIES", "MODEL", "ALLOY"))
#: words that mark the token BEFORE them as a material designation
_MATERIAL_WORDS = frozenset((
    "BOLT", "BOLTS", "STEEL", "ROD", "RODS", "BAR", "BARS", "REBAR",
    "REINFORCING", "REINFORCEMENT", "ANCHOR", "ANCHORS", "NUT", "NUTS",
    "WASHER", "WASHERS", "STUD", "STUDS", "ELECTRODE", "ELECTRODES",
    "GRADE", "GR", "TYPE", "CLASS", "WIRE", "FABRIC", "STRAND", "CEMENT"))

_TOKEN = re.compile(r"[A-Za-z0-9#][A-Za-z0-9#.\-]*")
_SHAPE_A = re.compile(r"^[A-Z]{1,3}-\d{3}(\.\d{1,2})?$")
_SHAPE_B = re.compile(r"^[A-Z]{1,2}\d\.\d{1,2}$")
_DETAIL = re.compile(
    r"(?<![A-Za-z0-9.\-/#])(\d{1,3}[A-Z]?)/"
    r"([A-Z]{1,3}-?\d{1,3}(?:\.\d{1,2})?)(?![A-Za-z0-9])")
_RFI_HEAD = re.compile(
    r"\bRFI(S?)\b(?:\s*(?:#|NO\.?|NUMBER))?[\s#\-]*(\d{1,12})\b",
    re.IGNORECASE)
_RFI_NEXT = re.compile(
    r"\s*(?:,|&|\bAND\b|\bOR\b)\s*(?:#|NO\.?)?\s*(\d{1,12})\b",
    re.IGNORECASE)
_ITEM_REF = r"((?:[A-Z][A-Z0-9]{0,5}-)?\d{1,4})\b"
_ITEM_HEAD = re.compile(r"(?i:\bITEM(S?))\s+" + _ITEM_REF)
_ITEM_NEXT = re.compile(r"\s*(?:,|&|(?i:\bAND\b)|(?i:\bOR\b))\s*" + _ITEM_REF)
_LOOSE = re.compile(r"^([A-Z]{1,3})-?(\d{1,3})(\.\d{1,2})?$")


def _loose_sheet(tok) -> str:
    """Hyphen- and leading-zero-tolerant sheet key (the same tolerance the
    plan-set index applies): ``S201`` / ``S-0201`` -> ``S-201``."""
    s = str(tok).strip().upper()
    m = _LOOSE.match(s)
    if not m:
        return s
    return f"{m.group(1)}-{int(m.group(2))}{m.group(3) or ''}"


def _loose_detail(value) -> tuple:
    s = str(value).strip().upper()
    num, _, sheet = s.partition("/")
    return (num.lstrip("0") or "0", _loose_sheet(sheet))


def _rfi_value(digits) -> str:
    return str(digits).zfill(3)


_REF_LEAD = {"sheet": r"(?:SHEETS?|SHT|DWG|DRAWING)", "item": r"ITEMS?",
             "detail": r"DETAILS?", "rfi": r"RFIS?"}


def _explicit_refs(kind: str, value: str) -> list:
    """A ``{v:sheet}`` / ``{v:item}`` / ``{v:rfi}`` / ``{v:detail}`` value
    is a reference by declaration; the value may carry its own lead word
    (``ITEM L2-3``) or not (``L2-3``)."""
    v = re.sub(r"^" + _REF_LEAD[kind] + r"\b[\s.#:]*", "", value.strip(),
               flags=re.IGNORECASE).strip()
    if not v:
        return []
    if kind == "rfi":
        m = re.search(r"\d+", v)
        return [Ref("rfi", _rfi_value(m.group(0)) if m else v)]
    if kind == "detail":
        out = [Ref("detail", v.upper())]
        _num, _, sheet = v.upper().partition("/")
        if sheet:
            out.append(Ref("sheet", sheet))
        return out
    if kind == "item":
        return [Ref("item", v.upper())]
    return [Ref("sheet", v)]


def find_refs(text, known_sheets=()) -> list:
    """Every sheet / item / detail / RFI reference printed in ``text``.

    Deliberately conservative, because one unresolved reference blocks
    delivery: a wall mark (``SW1``), a column mark (``C12``), a level code
    (``L2``), a bar size (``#4``) or a standard designation that follows a
    standards-issuer acronym is never a reference.  A sheet is read from

    * a token equal to one of ``known_sheets`` (hyphen / zero tolerant);
    * a strictly sheet-shaped token (``S-201``, ``A2.01``);
    * a sheet-shaped token of three or more characters right after SEE,
      SHEET(S), SHT, DWG, DRAWING or REFER TO, and the tokens listed with
      it (``SEE S10 AND S11``); when ``known_sheets`` is given, a token
      with neither hyphen nor point must also carry a discipline prefix
      this set uses, so ``SEE SW1`` / ``SEE L10`` read as marks;
    * a sheet-shaped token with a hyphen or a point right after PER or ON.

    ``5/S-501`` yields the detail and its sheet; ``ITEM L2-3`` an item;
    ``RFI 12`` / ``RFI #12`` / ``RFI-012`` the RFI ``012``."""
    text = "" if text is None else str(text)
    refs: list = []
    taken: list = []                  # (start, end, keeps_list)

    def add(kind, value):
        r = Ref(kind, value)
        if r not in refs:
            refs.append(r)

    known = {_loose_sheet(s) for s in known_sheets if str(s).strip()}
    disciplines = {m.group(0) for m in
                   (re.match(r"[A-Z]+", k) for k in known) if m}

    for m in _DETAIL.finditer(text):
        sheet = m.group(2)
        if len(sheet) < 3:
            continue
        add("detail", f"{m.group(1)}/{sheet}")
        add("sheet", sheet)
        taken.append((m.start(), m.end(), True))

    for m in _RFI_HEAD.finditer(text):
        add("rfi", _rfi_value(m.group(2)))
        end = m.end()
        if m.group(1):                               # "RFIs 12 and 14"
            while True:
                nx = _RFI_NEXT.match(text, end)
                if not nx:
                    break
                add("rfi", _rfi_value(nx.group(1)))
                end = nx.end()
        taken.append((m.start(), end, False))

    for m in _ITEM_HEAD.finditer(text):
        add("item", m.group(2).upper())
        end = m.end()
        if m.group(1):                               # "ITEMS L2-3 AND L2-4"
            while True:
                nx = _ITEM_NEXT.match(text, end)
                if not nx:
                    break
                add("item", nx.group(1).upper())
                end = nx.end()
        taken.append((m.start(), end, False))

    def overlap(a, b):
        for s, e, keeps in taken:
            if a < e and b > s:
                return keeps
        return None

    toks = list(_TOKEN.finditer(text))
    state = None                       # None | "strong" | "weak"
    designation = False                # inside "<issuer> <designation> ..."
    prev_up = ""
    prev_end = 0
    for i, m in enumerate(toks):
        raw = m.group(0)
        tok = raw.rstrip(".-")
        up = tok.upper()
        gap = text[prev_end:m.start()]
        prev_end = m.end()
        if re.search(r"[.;!?]", gap):
            state, designation = None, False
        sentence_end = raw.endswith(".")
        keeps = overlap(m.start(), m.end())
        if keeps is not None:                        # a detail / RFI / item
            if not keeps:
                state = None
            designation = False
            prev_up = up
            continue
        if not tok:
            prev_up = up
            continue
        nxt = toks[i + 1].group(0).rstrip(".-").upper() if i + 1 < len(toks) else ""

        is_cue = True
        if up in _STRONG_CUES or (up == "TO" and prev_up in (
                "REFER", "REFERS", "REFERRING")):
            state = "strong"
        elif up in _WEAK_CUES:
            state = "weak"
        elif state and up in _CONNECTORS:
            pass
        else:
            is_cue = False
        if is_cue:
            designation = designation and up in _CONNECTORS
            prev_up = up
            continue

        if up in _DESIGNATION_CUES:
            designation, state, prev_up = True, None, up
            if sentence_end:
                designation = False
            continue

        guarded = designation or nxt in _MATERIAL_WORDS
        accepted = False
        if known and _loose_sheet(up) in known and any(
                ch.isdigit() for ch in up):
            add("sheet", up)
            accepted = True
        elif (state == "strong" and TOKEN_FULL.match(tok) and len(tok) >= 3
              and ("-" in tok or "." in tok or not known
                   or re.match(r"[A-Z]+", tok).group(0) in disciplines)):
            add("sheet", tok)
            accepted = True
        elif (state == "weak" and TOKEN_FULL.match(tok)
              and ("-" in tok or "." in tok) and not guarded):
            add("sheet", tok)
            accepted = True
        elif (_SHAPE_A.match(tok) or _SHAPE_B.match(tok)) and not guarded:
            add("sheet", tok)
            accepted = True

        if not accepted:
            state = None
        if designation and not (any(ch.isdigit() for ch in up)
                                or up in _CONNECTORS or up in _MATERIAL_WORDS):
            designation = False
        if sentence_end:
            state, designation = None, False
        prev_up = up
    return refs


@dataclass
class Inventory:
    """What exists in THIS run, for reference resolution: sheet numbers,
    Red Tag item refs, indexed details (``5/S-501``), RFI numbers, and the
    sheets whose view titles could not be indexed (a detail on one of
    those resolves by its sheet alone)."""
    sheets: set = field(default_factory=set)
    items: set = field(default_factory=set)
    details: set = field(default_factory=set)
    rfis: set = field(default_factory=set)
    detail_unindexed: set = field(default_factory=set)

    def __post_init__(self):
        for name in ("sheets", "items", "details", "rfis",
                     "detail_unindexed"):
            setattr(self, name, set(getattr(self, name) or ()))

    def has(self, ref: Ref) -> bool:
        kind, value = ref.kind, str(ref.value).strip()
        if not value:
            return False
        if kind == "sheet":
            return _loose_sheet(value) in {_loose_sheet(s)
                                           for s in self.sheets}
        if kind == "item":
            return value.upper() in {str(s).strip().upper()
                                     for s in self.items}
        if kind == "rfi":
            def key(v):
                s = str(v).strip().upper()
                return str(int(s)) if s.isdigit() else s
            return key(value) in {key(r) for r in self.rfis}
        if kind == "detail":
            want = _loose_detail(value)
            if want in {_loose_detail(d) for d in self.details}:
                return True
            sheet = want[1]
            return (sheet in {_loose_sheet(s) for s in self.sheets}
                    and sheet in {_loose_sheet(s)
                                  for s in self.detail_unindexed})
        return False


def unresolved(things, inventory: Inventory) -> list:
    """``[(text, Ref)]`` for every printed reference this run cannot
    resolve.  ``things`` are Statements and / or plain strings (a single
    one is accepted too).  An empty list is the only passing answer."""
    if isinstance(things, (str, Statement)):
        things = [things]
    out = []
    for th in things:
        if isinstance(th, Statement):
            # declared references always count; references read out of the
            # text are re-read against this run's sheet set, so a mark
            # after a cue word is not mistaken for a sheet
            text = th.text
            free = set(find_refs(text))
            declared = [r for r in th.refs
                        if r in th.explicit or r not in free]
            refs = _dedupe(list(th.explicit) + declared
                           + find_refs(text, known_sheets=inventory.sheets))
        else:
            text = "" if th is None else str(th)
            refs = find_refs(text, known_sheets=inventory.sheets)
        for r in refs:
            if not inventory.has(r):
                out.append((text, r))
    return out


# --------------------------------------------------------------------- lint --

@dataclass
class LintHit:
    """One lint failure: kind is long_dash | british | howto | tool_name |
    metadata; ``token`` is what was found; ``where`` says in which output."""
    kind: str
    token: str
    where: str = ""


#: British spelling -> American spelling (whole words, lower case)
BRITISH = {
    "colour": "color", "colours": "colors", "coloured": "colored",
    "colouring": "coloring",
    "centre": "center", "centres": "centers", "centred": "centered",
    "centring": "centering", "centreline": "centerline",
    "centrelines": "centerlines",
    "metre": "meter", "metres": "meters", "millimetre": "millimeter",
    "millimetres": "millimeters", "centimetre": "centimeter",
    "centimetres": "centimeters", "kilometre": "kilometer",
    "kilometres": "kilometers",
    "storey": "story", "storeys": "stories", "multistorey": "multistory",
    "kerb": "curb", "kerbs": "curbs", "kerbing": "curbing",
    "aluminium": "aluminum",
    "grey": "gray", "greys": "grays", "greyed": "grayed",
    "greyscale": "grayscale",
    "licence": "license", "licences": "licenses",
    "programme": "program", "programmes": "programs",
    "labour": "labor", "labours": "labors", "laboured": "labored",
    "labouring": "laboring", "labourer": "laborer", "labourers": "laborers",
    "neighbour": "neighbor", "neighbours": "neighbors",
    "neighbouring": "neighboring", "neighbourhood": "neighborhood",
    "neighbourhoods": "neighborhoods",
    "analyse": "analyze", "analysed": "analyzed", "analysing": "analyzing",
    "organise": "organize", "organised": "organized",
    "organises": "organizes", "organising": "organizing",
    "organisation": "organization", "organisations": "organizations",
    "recognise": "recognize", "recognised": "recognized",
    "recognises": "recognizes", "recognising": "recognizing",
    "defence": "defense", "defences": "defenses",
    "travelled": "traveled", "travelling": "traveling",
    "traveller": "traveler", "travellers": "travelers",
    "modelled": "modeled", "modelling": "modeling",
    "levelled": "leveled", "levelling": "leveling",
    "cancelled": "canceled", "cancelling": "canceling",
    "labelled": "labeled", "labelling": "labeling",
    "catalogue": "catalog", "catalogues": "catalogs",
    "catalogued": "cataloged",
    "mould": "mold", "moulds": "molds", "moulded": "molded",
    "moulding": "molding", "mouldings": "moldings",
    "fibre": "fiber", "fibres": "fibers", "fibreglass": "fiberglass",
    "calibre": "caliber", "calibres": "calibers",
    "litre": "liter", "litres": "liters", "millilitre": "milliliter",
    "millilitres": "milliliters",
    "behaviour": "behavior", "behaviours": "behaviors",
    "favour": "favor", "favours": "favors", "favoured": "favored",
    "favourable": "favorable", "favourite": "favorite",
    "favourites": "favorites",
    "honour": "honor", "honours": "honors", "honoured": "honored",
    "tonne": "metric ton", "tonnes": "metric tons",
    "draught": "draft", "draughts": "drafts", "draughtsman": "draftsman",
    "plough": "plow", "ploughs": "plows", "ploughed": "plowed",
    "tyre": "tire", "tyres": "tires",
}

_BRITISH_RE = re.compile(
    r"\b(" + "|".join(sorted(BRITISH, key=len, reverse=True)) + r")\b",
    re.IGNORECASE)
_LONG_DASH_RE = re.compile("[" + _LONG_DASHES + "]")
_HOWTO_RE = re.compile(
    r"\bstep\s*\d|\bclick(?:s|ed|ing)?\b|\bhow\s+to\b|\btutorials?\b"
    r"|\bfollow\s+these\b", re.IGNORECASE)
_TOOLS = ("pyinstaller", "reportlab", "pymupdf", "tkinter", "python",
          "mupdf", "numpy", "pypdf", "fitz")
_TOOL_RE = re.compile(
    r"(?<![A-Za-z])(" + "|".join(_TOOLS) + r")(?![A-Za-z])", re.IGNORECASE)
_TOOL_META_RE = re.compile(
    "|".join(_TOOLS) + r"|planloom|rfi[ _\-]?stamper", re.IGNORECASE)


def _squash(s) -> str:
    return re.sub(r"\s+", " ", str(s)).strip()


def _exempt_texts(quotes) -> list:
    out = []
    for q in quotes or ():
        t = _squash(q.text if isinstance(q, Quote) else q)
        if t:
            out.append(t)
    return sorted(set(out), key=len, reverse=True)


def _mask(s: str, exempt) -> str:
    """Blank every whole occurrence of an exempt string (never a fragment
    inside a longer word, so a short value cannot hide part of a word)."""
    for q in exempt:
        pat = re.escape(q)
        if q[0].isalnum() or q[0] == "_":
            pat = r"(?<![A-Za-z0-9_])" + pat
        if q[-1].isalnum() or q[-1] == "_":
            pat = pat + r"(?![A-Za-z0-9_])"
        s = re.sub(pat, " ", s)
    return s


def lint_text(text, quotes=(), where="") -> list:
    """Wording lint for one piece of output text.  ``quotes`` are exact
    source strings (Quote objects or plain text); they are evidence, so an
    occurrence of one is exempt from every rule.  White space is compared
    loosely, so a quote that was wrapped over several lines still counts."""
    s = _mask(_squash("" if text is None else text), _exempt_texts(quotes))
    hits = []
    for m in _LONG_DASH_RE.finditer(s):
        hits.append(LintHit("long_dash", f"U+{ord(m.group(0)):04X}", where))
    for m in _BRITISH_RE.finditer(s):
        hits.append(LintHit("british", m.group(0).lower(), where))
    for m in _HOWTO_RE.finditer(s):
        hits.append(LintHit("howto", _squash(m.group(0)).lower(), where))
    for m in _TOOL_RE.finditer(s):
        hits.append(LintHit("tool_name", m.group(0).lower(), where))
    return hits


def lint_metadata(meta: dict, allowed_values=(), where="") -> list:
    """File metadata comes from project settings only: every non-empty
    value must equal one of ``allowed_values``.  Anything else is a
    ``metadata`` hit, or a ``tool_name`` hit when it names a tool, a
    library or this product."""
    allowed = {str(v).strip() for v in (allowed_values or ())
               if v is not None and str(v).strip()}
    hits = []
    for k, v in (meta or {}).items():
        if v is None:
            continue
        s = str(v).strip()
        if not s or s in allowed:
            continue
        kind = "tool_name" if _TOOL_META_RE.search(s) else "metadata"
        hits.append(LintHit(kind, f"{k}={s}", where))
    return hits


def _project_values(project) -> list:
    out = []
    for v in (project or {}).values():
        if isinstance(v, (str, int, float)) and not isinstance(v, bool):
            if str(v).strip():
                out.append(str(v).strip())
    return out


def lint_pdf(path, project=None, quotes=()) -> list:
    """Metadata and wording lint of one produced PDF: the document info
    (every key but the format and encryption notes), the XML metadata
    packet (must be absent) and the text of every page.  Project settings
    values are the only names allowed, in metadata and on the page."""
    import fitz
    base = os.path.basename(str(path))
    allowed = _project_values(project)
    exempt = list(quotes or ()) + allowed
    hits = []
    doc = fitz.open(path)
    try:
        meta = {k: v for k, v in (doc.metadata or {}).items()
                if k not in ("format", "encryption")}
        try:                           # custom /Info keys the summary skips
            kind, val = doc.xref_get_key(-1, "Info")
            if kind == "xref":
                xref = int(val.split()[0])
                for key in doc.xref_get_keys(xref):
                    if key.lower() not in {m.lower() for m in meta}:
                        meta[key] = doc.xref_get_key(xref, key)[1]
        except Exception:               # noqa: BLE001 -- no /Info at all
            pass
        hits += lint_metadata(meta, allowed, where=base)
        xml = doc.get_xml_metadata() or ""
        if xml.strip():
            kind = "tool_name" if _TOOL_META_RE.search(xml) else "metadata"
            hits.append(LintHit(kind, "xml metadata packet", base))
        for i, page in enumerate(doc):
            hits += lint_text(page.get_text(), quotes=exempt,
                              where=f"{base} p{i + 1}")
    finally:
        doc.close()
    return hits


_TEXT_EXT = (".txt", ".csv", ".json", ".md", ".dxf", ".xml", ".rw5", ".gsi")


def can_lint(path) -> bool:
    """True when :func:`lint_file` reads this kind of file; an output it
    cannot read must be reported as not linted, never as clean."""
    ext = os.path.splitext(str(path))[1].lower()
    return ext == ".pdf" or ext == ".xlsx" or ext in _TEXT_EXT


def _json_strings(node, out: list) -> None:
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for k, v in node.items():
            out.append(str(k))
            _json_strings(v, out)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _json_strings(v, out)


def lint_file(path, project=None, quotes=()) -> list:
    """Lint one output file by type: a PDF through :func:`lint_pdf`; a
    text format (.txt .csv .json .md .dxf .xml .rw5 .gsi) by its content
    (JSON by its decoded strings, so an escaped long dash is still
    found); a spreadsheet by the text of its parts and its document
    properties.  Other types return no hits: see :func:`can_lint`."""
    path = str(path)
    base = os.path.basename(path)
    ext = os.path.splitext(path)[1].lower()
    allowed = _project_values(project)
    exempt = list(quotes or ()) + allowed
    if ext == ".pdf":
        return lint_pdf(path, project=project, quotes=quotes)
    if ext == ".xlsx":
        hits = []
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if not name.lower().endswith((".xml", ".rels")):
                    continue
                body = z.read(name).decode("utf-8", errors="replace")
                texts = [t for t in re.findall(r">([^<>]+)<", body)
                         if t.strip()]
                if name.lower().startswith("docprops/"):
                    hits += lint_metadata(
                        {f"{name}#{i}": t for i, t in enumerate(texts)},
                        allowed, where=base)
                else:
                    hits += lint_text("\n".join(texts), quotes=exempt,
                                      where=f"{base}:{name}")
        return hits
    if ext not in _TEXT_EXT:
        return []
    with open(path, encoding="utf-8", errors="replace") as fh:
        body = fh.read()
    if ext == ".json":
        try:
            strings: list = []
            _json_strings(json.loads(body), strings)
            body = "\n".join(strings)
        except ValueError:
            pass
    return lint_text(body, quotes=exempt, where=base)
