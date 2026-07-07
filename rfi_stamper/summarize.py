"""Deterministic offline cliff-note summarizer for RFI records.

Replaces a removed cloud-API summarizer with pure extractive text
processing: no network, no models, same input -> same output.  Splits the
question and answer into sentences, scores each with a small heuristic
(lexicon hits, question mark, directive verbs, position, length sweet
spot), and composes "Q: <best q> A: <best a>" clipped to a length budget.
"""
from __future__ import annotations

import re

_UNREADABLE = "(question text not readable — see RFI document)"

# ~40-term construction lexicon: sentences that talk about the actual work
# outrank transmittal boilerplate ("thank you for your prompt attention").
_LEXICON = frozenset("""
    pipe duct beam conduit footing detail sheet spec dimension conflict
    clearance route install verify confirm provide relocate elevation slab
    joist girder column rebar anchor flange valve damper louver hanger
    sleeve penetration wall ceiling grade invert riser soffit curb embed
    drawing plan section coordinate
""".split())

# verbs that mark a directive/answer sentence
_DIRECTIVE = frozenset(
    "provide use install route see refer confirm approved rejected revise "
    "relocate submit coordinate verify".split())

# drawing-note abbreviations whose trailing period is not a sentence end
_ABBREV = frozenset(
    "no nos dwg dwgs fig figs sht shts det ref rev sect sec typ approx min "
    "max dia ea mr ms dr vs elev bldg mech elec struct arch e.g i.e".split())

_END = re.compile(r"([.!?])\s+(?=[A-Z0-9\"'(])")
_LAST_WORD = re.compile(r"([A-Za-z][A-Za-z.]*)$")
_WORDS = re.compile(r"[a-z]+")


def split_sentences(text: str) -> list[str]:
    """Split on .!? followed by space + capital/digit, tolerant of Dwg./No./Fig."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    out, start = [], 0
    for m in _END.finditer(text):
        if m.group(1) == ".":
            w = _LAST_WORD.search(text, 0, m.start())
            word = w.group(1).lower() if w else ""
            if word.rstrip(".") in _ABBREV or (len(word) == 1 and word.isalpha()):
                continue                       # "Dwg. A-101" / "J. Q." style
        out.append(text[start:m.end(1)].strip())
        start = m.end()
    out.append(text[start:].strip())
    return [s for s in out if s]


def _score(sent: str, idx: int, total: int, *, question: bool) -> float:
    words = _WORDS.findall(sent.lower())
    s = 0.0
    if question and "?" in sent:
        s += 3.0
    s += min(sum(1 for w in words if w in _LEXICON), 4)
    if not question:
        s += 1.5 * min(sum(1 for w in words if w in _DIRECTIVE), 2)
    s += (total - idx) / max(total, 1)          # earlier is better
    n = len(sent)
    if 40 <= n <= 160:
        s += 1.0                                # length sweet spot
    elif n < 15 or n > 260:
        s -= 1.0
    return s


def _best(text: str, *, question: bool) -> str:
    sents = split_sentences(text)
    if not sents:
        return ""
    n = len(sents)
    return max((_score(s, i, n, question=question), -i, s)
               for i, s in enumerate(sents))[2]


def _clip(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    if max_len <= 1:
        return s[:max(max_len, 0)]
    cut = s[:max_len - 1]
    sp = cut.rfind(" ")
    if sp > max_len * 0.5:                      # clip at a word boundary
        cut = cut[:sp]
    return cut.rstrip(" ,;:.") + "…"


def make_note(question: str, answer: str, max_len: int = 240) -> str:
    """Compose a one-line extractive Q/A note. Never raises."""
    try:                                        # tolerate a garbage max_len too
        max_len = int(max_len)
    except Exception:                           # noqa: BLE001
        max_len = 240
    try:
        q = _best(str(question or ""), question=True) or _UNREADABLE
        a = _best(str(answer or ""), question=False)
        note = f"Q: {q} A: {a}" if a else f"Q: {q} Resp: not in file."
        return _clip(note, max_len)
    except Exception:                           # noqa: BLE001 -- never raise
        return f"Q: {_UNREADABLE} Resp: not in file."[:max(max_len, 0) or 240]


class OfflineSummarizer:
    """Drop-in for the removed cloud summarizer; caller falls back on None."""

    def summarize(self, rec) -> str | None:
        try:
            return make_note(rec.question, rec.answer)
        except Exception:                       # noqa: BLE001
            return None
