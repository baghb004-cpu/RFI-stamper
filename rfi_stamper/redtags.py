"""Red Tags: the open items register (BATTERBOARD_PLAN.md, brief section 6).

A red tag on site means "do not proceed until cleared".  This module keeps
the list of things a layout run could not settle from the documents: a
shear wall end with no dimension to the concrete, a corner that disagrees
with the second source, a slab edge run with no plotted line.  One register
per project.

The rules (each one is a promise to the person holding the sheets):

* **Sidecar file.**  The register lives in its own versioned JSON file
  whose path the caller supplies.  It is never stored inside the project
  file: an older build that opens and saves a project keeps only the
  record kinds it knows and would silently erase the register.
* **Numbers are stable per level.**  They are never reused and never
  renumbered.  New items append.  The per-level counter is stored in the
  file and only ever grows, so a number stays retired even after its item
  stops being raised (or its entry is lost from the file).
* **Identity** is ``level`` + ``key`` + the location anchor ``at``: an
  incoming raise is the SAME item when level and key are equal and either
  both anchors are ``None`` (a level-wide item) or both are set and within
  :data:`MATCH_AT_FT`.  The anchor is pinned by the first raise and is not
  moved by later ones, so an item cannot drift into its neighbor run after
  run; the location boxes carry where the evidence sits now.
* **Evidence fields refresh, user fields survive.**  Every run rewrites
  category, label, wording, boxes, proof ids and element ids from that
  run's evidence.  A run never touches the fields a person set: the
  status text, and for an answered item its RFI number and quote.  An item
  the user answered stays answered when the same evidence comes back.
* **A status prints only from a field the user set.**  The one wording
  callers may print is :meth:`RedTag.status_text`; with nothing set it is
  "RFI needed" for an item that needs an answer.  The program never states
  that an RFI is being written or sent, and :meth:`Register.rfi_draft`
  only ever produces a draft for review.
* **Hold policy.**  Categories map to what the points do: hold, check or
  release (:meth:`Register.action`).
* **Wording comes from the evidence.**  This module never invents item
  text; the caller builds label / wrong / needed from its proof records.
  :func:`plural` is the singular / plural helper callers share.
* **Nothing is written until** :meth:`Register.save`.  A run that ends
  blocked can simply not save.  Writes are atomic; a load drops a
  malformed entry instead of crashing (its number stays retired) and
  REFUSES an unreadable file with a clear error, because starting empty
  would renumber every open item.

Fully offline, stdlib only.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field

from .fsutil import atomic_write_bytes

_VERSION = 1

#: item categories, in the order they are listed on a plate
CATEGORIES = ("answer", "confirm", "answered")
CATEGORY_LABEL = {
    "answer": "Answer needed before layout",
    "confirm": "Confirm before forming",
    "answered": "Answered",
}
#: printed for an item that needs an answer when the user set no status
DEFAULT_STATUS = "RFI needed"
#: what the points of an item do under the hold policy
ACTIONS = ("hold", "check", "release")
DEFAULT_HOLD_POLICY = {"answer": "hold", "confirm": "check",
                       "answered": "release"}
#: two raises with the same level and key are one item within this distance
MATCH_AT_FT = 0.5

_DRAFT_TITLE = "RFI draft"
_DRAFT_FOOTER = "This is a draft for review."
_NO_SHEETS = "not recorded"
_LONG_DASHES = dict.fromkeys(range(0x2012, 0x2016), "-")
_RFI_PREFIX = re.compile(r"^(?:RFI(?![A-Z])[\s\-:#.]*|#\s*)", re.IGNORECASE)


# ----------------------------------------------------------------- helpers --

def plural(n, singular: str, plural_form: str) -> str:
    """``1 wall`` / ``3 walls``: the count with the matching noun form."""
    return f"{n} {singular if n == 1 else plural_form}"


def _norm_rfi(number) -> str:
    """Canonical RFI number: an optional ``RFI`` / ``#`` lead-in dropped and
    bare digits zero-filled to 3, the same way RFI parsing keys them."""
    s = _RFI_PREFIX.sub("", str(number or "").strip()).strip()
    return s.zfill(3) if s.isdigit() else s


def _level_key(level: str) -> list:
    """Natural sort key so ``L2`` lists before ``L10``."""
    return [(0, int(t), "") if t.isdigit() else (1, 0, t)
            for t in re.findall(r"\d+|\D+", str(level))]


def _one_line(text) -> str:
    return " ".join(str(text or "").split())


def _plain(text: str) -> str:
    """Long dashes (U+2012 to U+2015) become a plain hyphen."""
    return text.translate(_LONG_DASHES)


def _strs(values) -> list:
    out = []
    for v in values or ():
        s = str(v)
        if s and s not in out:
            out.append(s)
    return out


def _at(at):
    if at is None:
        return None
    try:
        e, n = (float(at[0]), float(at[1]))
        ok = len(at) == 2
    except (TypeError, ValueError, IndexError):
        ok = False
    if not ok or not (math.isfinite(e) and math.isfinite(n)):
        raise ValueError(f"at must be None or a finite (e, n) pair, got {at!r}")
    return (e, n)


def _box(b) -> dict:
    """One location box: ``{"sheet", "page": [x0, y0, x1, y1] | None,
    "world": [e0, n0, e1, n1] | None}`` (extra keys ride along)."""
    if not isinstance(b, dict):
        raise ValueError("a location box must be a dict, got "
                         f"{type(b).__name__}")
    out = dict(b)
    out["sheet"] = str(b.get("sheet", "") or "")
    for k in ("page", "world"):
        v = b.get(k)
        if v is None:
            out[k] = None
            continue
        try:
            v = [float(x) for x in v]
        except (TypeError, ValueError):
            raise ValueError(f"box {k!r} must be four numbers, got "
                             f"{b.get(k)!r}") from None
        if len(v) != 4 or not all(math.isfinite(x) for x in v):
            raise ValueError(f"box {k!r} must be four finite numbers, got "
                             f"{b.get(k)!r}")
        out[k] = v
    return out


def _policy(policy) -> dict:
    """The default hold policy overlaid with ``policy``; validated."""
    out = dict(DEFAULT_HOLD_POLICY)
    if policy is None:
        return out
    if not isinstance(policy, dict):
        raise ValueError("a hold policy is a dict of category -> action")
    for cat, act in policy.items():
        if cat not in CATEGORIES:
            raise ValueError(f"unknown category {cat!r} in the hold policy; "
                             f"expected one of {CATEGORIES}")
        if act not in ACTIONS:
            raise ValueError(f"unknown action {act!r} for {cat!r} in the "
                             f"hold policy; expected one of {ACTIONS}")
        out[cat] = act
    return out


# -------------------------------------------------------------------- item --

@dataclass
class RedTag:
    """One open item.  ``ref`` (``L2-3``) is what plates, point notes and
    the report print; ``number`` is stable per level for the life of the
    project."""
    level: str
    number: int
    key: str                    # identity key WITHOUT coordinates
    at: tuple | None            # world (e, n) feet anchor; None = level-wide
    category: str               # one of CATEGORIES
    label: str                  # short label
    wrong: str                  # what is wrong
    needed: str                 # what is needed
    boxes: list = field(default_factory=list)      # location boxes
    proofs: list = field(default_factory=list)     # proof record ids
    elements: list = field(default_factory=list)   # element ids
    status: str = ""            # USER-set text only
    rfi: str = ""               # answered items: the RFI number (user-set)
    quote: str = ""             # answered items: the exact quote (user-set)
    first_run: str = ""
    last_run: str = ""
    seen: bool = True           # raised in the latest run
    origin: str = ""            # the category the evidence gave (reopen target)

    def __post_init__(self):
        if self.origin not in CATEGORIES:
            self.origin = self.category

    @property
    def ref(self) -> str:
        return f"{self.level}-{self.number}"

    @property
    def category_label(self) -> str:
        return CATEGORY_LABEL.get(self.category, self.category)

    @property
    def user_answered(self) -> bool:
        """True when a person recorded the answer (RFI number on file)."""
        return self.category == "answered" and bool(self.rfi)

    def status_text(self) -> str:
        """The ONLY status wording that may be printed for this item."""
        if self.status.strip():
            return self.status.strip()
        if self.category == "answered" and self.rfi:
            return f"Answered per RFI {self.rfi}"
        if self.category == "answer":
            return DEFAULT_STATUS
        return self.category_label

    def sheets(self) -> list:
        """Sheet numbers of the location boxes, first seen first."""
        out = []
        for b in self.boxes:
            s = str(b.get("sheet", "") or "") if isinstance(b, dict) else ""
            if s and s not in out:
                out.append(s)
        return out

    def to_dict(self) -> dict:
        return {
            "level": self.level, "number": self.number, "key": self.key,
            "at": list(self.at) if self.at is not None else None,
            "category": self.category, "origin": self.origin,
            "label": self.label, "wrong": self.wrong, "needed": self.needed,
            "boxes": [dict(b) for b in self.boxes],
            "proofs": list(self.proofs), "elements": list(self.elements),
            "status": self.status, "rfi": self.rfi, "quote": self.quote,
            "first_run": self.first_run, "last_run": self.last_run,
            "seen": bool(self.seen),
        }

    @classmethod
    def from_dict(cls, d) -> "RedTag":
        """Strict: raises ``ValueError`` on a malformed entry (the register
        loader drops such an entry and keeps its number retired)."""
        if not isinstance(d, dict):
            raise ValueError("entry is not an object")
        level = str(d.get("level", "") or "").strip()
        if not level:
            raise ValueError("entry has no level")
        number = d.get("number")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise ValueError(f"entry number {number!r} is not a positive "
                             "whole number")
        key = str(d.get("key", "") or "").strip()
        if not key:
            raise ValueError("entry has no key")
        category = d.get("category")
        if category not in CATEGORIES:
            raise ValueError(f"unknown category {category!r}")
        boxes = []
        for b in d.get("boxes") or []:
            try:
                boxes.append(_box(b))
            except ValueError:
                continue            # one bad box never costs the item
        return cls(
            level=level, number=number, key=key, at=_at(d.get("at")),
            category=category, label=str(d.get("label", "") or ""),
            wrong=str(d.get("wrong", "") or ""),
            needed=str(d.get("needed", "") or ""),
            boxes=boxes,
            proofs=_strs(d.get("proofs") if isinstance(d.get("proofs"), list)
                         else []),
            elements=_strs(d.get("elements")
                           if isinstance(d.get("elements"), list) else []),
            status=str(d.get("status", "") or ""),
            rfi=str(d.get("rfi", "") or ""),
            quote=str(d.get("quote", "") or ""),
            first_run=str(d.get("first_run", "") or ""),
            last_run=str(d.get("last_run", "") or ""),
            seen=bool(d.get("seen", True)),
            origin=str(d.get("origin", "") or ""))


# ---------------------------------------------------------------- register --

class Register:
    """The project's open items, persisted to a JSON sidecar.

    Construct with the sidecar path and the file is loaded when it exists
    (a missing file is a first run; an unreadable one raises
    ``ValueError``).  Construct with no path for an in-memory register and
    call :meth:`save` with an explicit path to persist.

    A run goes: :meth:`begin_run`, one :meth:`raise_item` per finding,
    :meth:`end_run`, then :meth:`save`.  Nothing touches the disk before
    :meth:`save`."""

    def __init__(self, path: str | None = None):
        self.path = path
        self.run_id = ""
        self.hold_policy = dict(DEFAULT_HOLD_POLICY)
        #: entries the last load dropped: ``[{"entry", "why"}]``
        self.dropped: list = []
        self._items: list[RedTag] = []
        self._counters: dict[str, int] = {}     # level -> highest number issued
        if path and os.path.exists(path):
            self.load()

    def __len__(self) -> int:
        return len(self._items)

    # ---------------------------------------------------------------- runs --

    def begin_run(self, run_id: str) -> None:
        """Start a run: every item on file becomes unseen until this run
        raises it again."""
        self.run_id = str(run_id or "")
        for it in self._items:
            it.seen = False

    def end_run(self) -> None:
        """Close the run.  Nothing is deleted and nothing is renumbered: an
        item this run did not raise stays on file with ``seen`` False and
        keeps its number for good."""
        return None

    def _match(self, level: str, key: str, at):
        best, best_d = None, 0.0
        for it in self._items:
            if it.level != level or it.key != key:
                continue
            if at is None and it.at is None:
                return it
            if at is None or it.at is None:
                continue
            d = math.hypot(at[0] - it.at[0], at[1] - it.at[1])
            if d <= MATCH_AT_FT + 1e-9 and (best is None or d < best_d):
                best, best_d = it, d
        return best

    def raise_item(self, level, key, category, label, wrong, needed, *,
                   at=None, boxes=(), proofs=(), elements=()) -> RedTag:
        """Record one finding of this run.

        The same finding as an item on file (see the identity rule in the
        module note) keeps that item's number and refreshes its evidence
        fields; anything else appends with the level's next number.  User
        fields are never touched, and an item the user answered stays
        answered."""
        level = str(level or "").strip()
        if not level:
            raise ValueError("a Red Tag needs a level: numbers are stable "
                             "per level")
        key = str(key or "").strip()
        if not key:
            raise ValueError("a Red Tag needs an identity key")
        if category not in CATEGORIES:
            raise ValueError(f"unknown category {category!r}; expected one "
                             f"of {CATEGORIES}")
        at = _at(at)
        boxes = [_box(b) for b in boxes or ()]
        proofs, elements = _strs(proofs), _strs(elements)
        label, wrong, needed = str(label or ""), str(wrong or ""), str(needed or "")

        it = self._match(level, key, at)
        if it is None:
            number = self._counters.get(level, 0) + 1
            self._counters[level] = number
            it = RedTag(level=level, number=number, key=key, at=at,
                        category=category, label=label, wrong=wrong,
                        needed=needed, boxes=boxes, proofs=proofs,
                        elements=elements, first_run=self.run_id,
                        last_run=self.run_id, seen=True, origin=category)
            self._items.append(it)
            return it
        keep_answer = it.user_answered
        it.origin = category
        if not keep_answer:
            it.category = category
        it.label, it.wrong, it.needed = label, wrong, needed
        it.boxes, it.proofs, it.elements = boxes, proofs, elements
        it.seen = True
        it.last_run = self.run_id
        return it

    # ------------------------------------------------------------- queries --

    def items(self, level=None, *, seen_only: bool = True,
              categories=None) -> list:
        """Items sorted by (level, number).  By default only the items the
        latest run raised; ``seen_only=False`` lists everything on file."""
        if categories is None:
            cats = None
        elif isinstance(categories, str):
            cats = {categories}
        else:
            cats = set(categories)
        out = [it for it in self._items
               if (level is None or it.level == level)
               and (it.seen or not seen_only)
               and (cats is None or it.category in cats)]
        out.sort(key=lambda it: (_level_key(it.level), it.number))
        return out

    def get(self, ref: str):
        """The item with this ref (``L2-3``), or ``None``."""
        ref = str(ref or "").strip()
        for it in self._items:
            if it.ref == ref:
                return it
        return None

    def _need(self, item) -> RedTag:
        if isinstance(item, RedTag):
            return item
        it = self.get(item)
        if it is None:
            raise KeyError(f"no Red Tag {str(item)!r} on file")
        return it

    def refs(self, *, seen_only: bool = False) -> set:
        """Every item ref on file, for reference resolution
        (``seen_only=True`` narrows it to the latest run's items)."""
        return {it.ref for it in self._items if it.seen or not seen_only}

    def counts(self, level=None) -> dict:
        """``{"answer", "confirm", "answered", "total"}`` over the items
        the latest run raised."""
        out = {c: 0 for c in CATEGORIES}
        for it in self.items(level):
            out[it.category] += 1
        out["total"] = sum(out[c] for c in CATEGORIES)
        return out

    def action(self, item, policy=None) -> str:
        """``"hold"`` | ``"check"`` | ``"release"`` for an item under the
        hold policy (``policy`` overlays the register's own; both are
        validated)."""
        it = self._need(item)
        effective = _policy(self.hold_policy)
        if policy is not None:
            if not isinstance(policy, dict):
                raise ValueError("a hold policy is a dict of category -> "
                                 "action")
            effective = _policy({**effective, **policy})
        return effective[it.category]

    # ---------------------------------------------------------- user fields --

    def set_status(self, ref, text) -> None:
        """Set the user's status text (``""`` clears it).  A run never
        overwrites it.  Raises ``KeyError`` for an unknown ref."""
        self._need(ref).status = str(text or "").strip()

    def answer(self, ref, rfi, quote) -> None:
        """Record the answer a person entered: the item becomes
        ``answered`` and carries the RFI number (bare digits zero-filled to
        3) and the exact quote.  Both are required: an answered item
        without its evidence is a claim, not a record."""
        it = self._need(ref)
        number = _norm_rfi(rfi)
        text = str(quote or "").strip()
        if not number:
            raise ValueError(f"answering {it.ref} needs the RFI number")
        if not text:
            raise ValueError(f"answering {it.ref} needs the quote from "
                             f"RFI {number}")
        it.category = "answered"
        it.rfi = number
        it.quote = text

    def reopen(self, ref) -> None:
        """Undo :meth:`answer`: back to the category the evidence gave, the
        RFI number and quote cleared.  An item nobody answered is left as
        it is."""
        it = self._need(ref)
        if not it.user_answered:
            return
        it.category = it.origin
        it.rfi = ""
        it.quote = ""

    # ----------------------------------------------------------- RFI drafts --

    def rfi_draft(self, item, project=None) -> str:
        """A short neutral RFI draft for an open item, built only from the
        item's own fields and the project settings; ``""`` for an answered
        item.  It is a draft for review: nothing here issues anything."""
        it = self._need(item)
        if it.category == "answered":
            return ""
        proj = project if isinstance(project, dict) else {}
        lines = [_DRAFT_TITLE]
        for caption, k in (("Project", "project"), ("Date", "date")):
            v = _one_line(proj.get(k, ""))
            if v:
                lines.append(f"{caption}: {v}")
        sheets = it.sheets()
        lines += [
            f"Item: {it.ref} (level {it.level})",
            f"Subject: {_one_line(it.label)}",
            "Reference: " + (", ".join(sheets) if sheets else _NO_SHEETS),
            "",
            f"Question: {_one_line(it.wrong)}",
            "",
            f"Requested: {_one_line(it.needed)}",
            "",
            _DRAFT_FOOTER,
        ]
        return _plain("\n".join(lines)) + "\n"

    # ---------------------------------------------------------- persistence --

    def to_dict(self) -> dict:
        return {
            "version": _VERSION, "run": self.run_id,
            "counters": {lv: self._counters[lv]
                         for lv in sorted(self._counters, key=_level_key)},
            "hold_policy": dict(self.hold_policy),
            "items": [it.to_dict() for it in
                      sorted(self._items,
                             key=lambda it: (_level_key(it.level), it.number))],
        }

    def save(self, path: str | None = None) -> None:
        """Atomically write the versioned JSON sidecar (temp file + fsync +
        ``os.replace``: a crash can never leave a truncated register)."""
        path = path or self.path
        if not path:
            raise ValueError("no register path; construct with a path or "
                             "pass one to save()")
        blob = json.dumps(self.to_dict(), indent=2, sort_keys=True)
        atomic_write_bytes(blob.encode("utf-8"), path)

    def load(self, path: str | None = None) -> None:
        """Load the sidecar.  A malformed entry is dropped (listed in
        ``dropped``; its number stays retired) rather than crashing; an
        unreadable or foreign file raises ``ValueError`` instead of
        starting empty, because an empty register would renumber every
        open item."""
        path = path or self.path
        if not path:
            raise ValueError("no register path; construct with a path or "
                             "pass one to load()")
        name = os.path.basename(path)

        def refuse(why) -> str:
            return (f"the Red Tags register {name!r} cannot be read ({why}). "
                    "Not starting an empty register: that would renumber "
                    "the open items. Restore the file, or move it aside on "
                    "purpose to start over.")

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise ValueError(refuse(e)) from e
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise ValueError(refuse("it is not a register file"))
        if data.get("version") != _VERSION:
            raise ValueError(refuse(
                f"file version {data.get('version')!r}, this build reads "
                f"version {_VERSION}"))

        items, dropped, counters, taken = [], [], {}, set()
        for raw in data["items"]:
            try:
                it = RedTag.from_dict(raw)
                if (it.level, it.number) in taken:
                    raise ValueError(f"number {it.ref} is used twice")
            except (ValueError, TypeError, KeyError) as e:
                dropped.append({"entry": raw, "why": str(e)})
                if isinstance(raw, dict):       # keep its number retired
                    lv, num = raw.get("level"), raw.get("number")
                    if (isinstance(lv, str) and lv.strip()
                            and isinstance(num, int)
                            and not isinstance(num, bool) and num > 0):
                        lv = lv.strip()
                        counters[lv] = max(counters.get(lv, 0), num)
                continue
            taken.add((it.level, it.number))
            items.append(it)
            counters[it.level] = max(counters.get(it.level, 0), it.number)
        stored = data.get("counters")
        if isinstance(stored, dict):
            for lv, num in stored.items():
                if (isinstance(num, int) and not isinstance(num, bool)
                        and num > 0 and str(lv).strip()):
                    lv = str(lv).strip()
                    counters[lv] = max(counters.get(lv, 0), num)
        try:
            policy = _policy(data.get("hold_policy"))
        except ValueError:
            policy = dict(DEFAULT_HOLD_POLICY)

        self._items = items
        self._counters = counters
        self.dropped = dropped
        self.hold_policy = policy
        self.run_id = str(data.get("run", "") or "")
