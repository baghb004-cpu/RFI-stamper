"""Shared local project store for the workspace modules.

One JSON file per project (suffix ``.ploom.json``) holding tasks, schedule,
punch list, inspections, change orders, budget lines, document register and
spec sections.  Everything is plain dataclasses + JSON on disk: no database,
no server, fully offline.  Writes are atomic (tmp + fsync + os.replace) so a
killed process can never leave a truncated project file behind.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime

KINDS = ("tasks", "schedule", "punch", "inspections", "change_orders",
         "budget", "documents", "specs")


# ------------------------------------------------------------ time hooks ---
# Kept as tiny module-level functions so tests (and callers) can freeze time
# by monkeypatching rather than by patching datetime itself.

def _today() -> str:
    """Current local date as ISO 'YYYY-MM-DD' (summary comparisons)."""
    return date.today().isoformat()


def _now() -> str:
    """Current local timestamp as ISO seconds (created/updated/added stamps)."""
    return datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------- dataclasses ---

class _Record:
    """Mixin shared by every store dataclass: new() / to_dict / from_dict."""

    @classmethod
    def new(cls, **kw):
        """Construct with a fresh uuid4-hex id and timestamps auto-filled
        (created/updated/added, where the class has such a field)."""
        names = {f.name for f in fields(cls)}
        kw.setdefault("id", uuid.uuid4().hex)
        stamp = _now()
        for auto in ("created", "updated", "added"):
            if auto in names and not kw.get(auto):
                kw[auto] = stamp
        return cls(**kw)

    def to_dict(self) -> dict:
        return asdict(self)          # deep copy: caller edits never leak back

    @classmethod
    def from_dict(cls, d: dict):
        """Build from a dict, ignoring unknown keys (forward compatible) and
        deep-copying values so the source dict is never shared or mutated."""
        names = {f.name for f in fields(cls)}
        return cls(**{k: deepcopy(v) for k, v in d.items() if k in names})


@dataclass
class Task(_Record):
    id: str
    title: str
    desc: str = ""
    assignee: str = ""
    status: str = "todo"        # todo / doing / blocked / done
    due: str = ""               # ISO date "YYYY-MM-DD"
    priority: str = "med"       # low / med / high
    linked_sheet: str = ""
    created: str = ""
    updated: str = ""


@dataclass
class ScheduleItem(_Record):
    id: str
    title: str
    start: str = ""             # ISO date
    end: str = ""               # ISO date
    crew: str = ""
    pct: float = 0.0            # percent complete, 0-100
    color: str = ""
    depends: list = field(default_factory=list)   # ids of prerequisite items


@dataclass
class PunchItem(_Record):
    id: str
    title: str
    location: str = ""
    sheet: str = ""
    status: str = "open"        # open / ready / closed
    assignee: str = ""
    photo_path: str = ""
    created: str = ""


@dataclass
class Inspection(_Record):
    id: str
    title: str
    date: str = ""              # ISO date
    inspector: str = ""
    status: str = "scheduled"   # scheduled / passed / failed
    checklist: list = field(default_factory=list)  # [{"item","ok","note"}]
    notes: str = ""


@dataclass
class ChangeOrder(_Record):
    id: str
    number: str = ""
    title: str = ""
    amount: float = 0.0
    status: str = "draft"       # draft / submitted / approved / rejected
    days_impact: int = 0
    created: str = ""


@dataclass
class BudgetLine(_Record):
    id: str
    code: str = ""
    desc: str = ""
    budget: float = 0.0
    committed: float = 0.0
    spent: float = 0.0


@dataclass
class DocEntry(_Record):
    id: str
    path: str = ""
    title: str = ""
    category: str = ""
    rev: str = ""
    added: str = ""


@dataclass
class SpecSection(_Record):
    id: str
    section: str = ""           # CSI number, e.g. "09 91 23"
    title: str = ""
    division: str = ""          # first two digits of section
    source: str = ""            # file the section was parsed from
    text: str = ""


_CLS_FOR = {
    "tasks": Task,
    "schedule": ScheduleItem,
    "punch": PunchItem,
    "inspections": Inspection,
    "change_orders": ChangeOrder,
    "budget": BudgetLine,
    "documents": DocEntry,
    "specs": SpecSection,
}


# ----------------------------------------------------------------- store ---

def _atomic_write_json(payload: dict, out_path: str) -> None:
    """Write beside out_path, fsync, then atomically replace: a killed process
    or crash can never leave a truncated project file at the final path."""
    tmp = out_path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out_path)


def _stem(path: str) -> str:
    base = os.path.basename(path)
    if base.endswith(Project.SUFFIX):
        return base[: -len(Project.SUFFIX)]
    return os.path.splitext(base)[0]


class Project:
    """The local project file. All list attributes hold dataclass instances;
    use add()/remove() so changes autosave when a path is set."""

    SUFFIX = ".ploom.json"

    def __init__(self, path: str | None = None):
        self.path = path
        self.name = ""
        for kind in KINDS:
            setattr(self, kind, [])
        if path:
            if os.path.exists(path):
                self.load(path)
            else:
                self.name = _stem(path)

    # internal: kind -> live list, with the one validation everything shares
    def _list(self, kind: str) -> list:
        if kind not in KINDS:
            raise ValueError(
                f"unknown kind {kind!r} (expected one of: {', '.join(KINDS)})")
        return getattr(self, kind)

    def add(self, kind: str, obj):
        lst = self._list(kind)
        cls = _CLS_FOR[kind]
        if not isinstance(obj, cls):
            raise TypeError(f"{kind} holds {cls.__name__}, "
                            f"got {type(obj).__name__}")
        if hasattr(obj, "updated"):
            obj.updated = _now()
        lst.append(obj)
        if self.path:
            self.save()
        return obj

    def remove(self, kind: str, id: str) -> bool:
        lst = self._list(kind)
        for i, obj in enumerate(lst):
            if obj.id == id:
                del lst[i]
                if self.path:
                    self.save()
                return True
        return False

    def get(self, kind: str, id: str):
        for obj in self._list(kind):
            if obj.id == id:
                return obj
        return None

    def items(self, kind: str) -> list:
        return list(self._list(kind))    # copy: mutate via add()/remove()

    def save(self, path: str | None = None) -> str:
        p = path or self.path
        if not p:
            raise ValueError("no path set: pass save(path=...) once")
        if not self.name:
            self.name = _stem(p)
        payload: dict = {"version": 1, "name": self.name}
        for kind in KINDS:
            payload[kind] = [obj.to_dict() for obj in getattr(self, kind)]
        _atomic_write_json(payload, p)
        self.path = p
        return p

    def load(self, path: str | None = None) -> "Project":
        p = path or self.path
        if not p:
            raise ValueError("no path set: pass load(path=...)")
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        version = data.get("version", 1)
        if version != 1:
            raise ValueError(f"unsupported project file version {version!r}")
        self.name = str(data.get("name") or _stem(p))
        for kind in KINDS:
            cls = _CLS_FOR[kind]
            setattr(self, kind, [cls.from_dict(d) for d in data.get(kind, [])])
        self.path = p
        return self

    def summary(self) -> dict:
        """Dashboard counters. Dates compare as ISO strings against _today();
        records with an empty date never count as overdue/behind."""
        today = _today()
        return {
            "tasks_open": sum(1 for t in self.tasks if t.status != "done"),
            "tasks_overdue": sum(1 for t in self.tasks
                                 if t.due and t.due[:10] < today
                                 and t.status != "done"),
            "punch_open": sum(1 for p in self.punch if p.status != "closed"),
            "inspections_failed": sum(1 for i in self.inspections
                                      if i.status == "failed"),
            "co_pending": sum(1 for c in self.change_orders
                              if c.status in ("draft", "submitted")),
            "co_approved_amount": float(sum(c.amount for c in self.change_orders
                                            if c.status == "approved")),
            "budget_total": float(sum(b.budget for b in self.budget)),
            "budget_spent": float(sum(b.spent for b in self.budget)),
            "docs": len(self.documents),
            "specs": len(self.specs),
            "schedule_behind": sum(1 for s in self.schedule
                                   if s.end and s.end[:10] < today
                                   and s.pct < 100),
        }


# ------------------------------------------------------------ spec books ---

_SECTION_TEXT_CAP = 20_000   # chars of body kept per section

# CSI MasterFormat heading: "NN NN NN" or "NN NN NN.NN", optionally led by
# the word SECTION, optionally followed by a dash/colon and the title on the
# same line.  (?![\d.]) stops "09 91 234" from half-matching.
_CSI_HEADING = re.compile(
    r"^[ \t]*(?:SECTION[ \t]+)?"
    r"(\d{2}[ \t]+\d{2}[ \t]+\d{2}(?:\.\d{2})?)(?![\d.])"
    r"[ \t]*(?:[-–—:][ \t]*)?(\S[^\n]*)?$",
    re.MULTILINE | re.IGNORECASE,
)
# a "title" that is really a page footer: bare number / "Page 3 of 12" / "3 of 12"
_PAGE_NO = re.compile(r"(?:page[ \t]+)?\d+(?:[ \t]+of[ \t]+\d+)?", re.IGNORECASE)


def _title_from_next_line(text: str, pos: int, end: int) -> tuple[str, int]:
    """Title on the line(s) after a bare heading: first non-empty line that is
    not itself a heading. Returns (title, new body start)."""
    while pos < end:
        nl = text.find("\n", pos)
        if nl == -1 or nl >= end:
            break
        line = text[pos:nl].strip()
        if line:
            if _CSI_HEADING.match(text[pos:nl]) or len(line) > 90:
                break                      # next heading / prose: no title
            return line, nl + 1
        pos = nl + 1
    return "", pos


def _split_csi(text: str, source: str) -> list[SpecSection]:
    """Split one document's text into SpecSections at CSI headings. Repeats of
    the current section number (page headers/footers) extend the open section
    instead of starting a new one."""
    out: list[SpecSection] = []
    matches = list(_CSI_HEADING.finditer(text))
    for i, m in enumerate(matches):
        number = " ".join(m.group(1).split())
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        if out and out[-1].section == number:      # page-break continuation
            more = text[body_start:body_end].strip()
            if more and len(out[-1].text) < _SECTION_TEXT_CAP:
                out[-1].text = (out[-1].text + "\n" + more)[:_SECTION_TEXT_CAP]
            continue
        title = (m.group(2) or "").strip()
        if not title or _PAGE_NO.fullmatch(title):
            title, body_start = _title_from_next_line(text, body_start, body_end)
        out.append(SpecSection.new(
            section=number,
            title=title,
            division=number[:2],
            source=source,
            text=text[body_start:body_end].strip()[:_SECTION_TEXT_CAP],
        ))
    return out


def parse_spec(paths, log=print) -> list[SpecSection]:
    """Parse spec book files (PDF / zip-package / raw text, sniffed by
    core.read_document) into SpecSections. A bad file never raises: it is
    logged and skipped so one corrupt volume cannot sink a whole import."""
    from .core import read_document   # lazy: keeps the store fitz-free
    if isinstance(paths, (str, os.PathLike)):
        paths = [paths]
    sections: list[SpecSection] = []
    for path in paths:
        p = os.fspath(path)
        try:
            text, kind = read_document(p)
            found = _split_csi(text, source=p)
        except Exception as e:                      # noqa: BLE001 - by design
            log(f"  !! {os.path.basename(p)}: {e} -- skipped")
            continue
        if found:
            log(f"  + {os.path.basename(p)} ({kind}): {len(found)} section(s)")
        else:
            log(f"  -- {os.path.basename(p)} ({kind}): no CSI headings found")
        sections.extend(found)
    return sections
