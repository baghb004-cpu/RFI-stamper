"""Daybook — the foreman's daily progress journal.

One entry per report: what happened on site that day, the crew and weather,
measurements taken, free-form comments, and references to photos.  Photos are
stored as *file paths only* — the daybook never copies, reads, or uploads an
image; the references stay valid exactly as long as the user keeps the files
where they were.

Entries live in a versioned JSON sidecar next to the project store
(``<base>.daybook.json``, same convention as the markups and resolution
sidecars).  Fully offline; depends only on the standard library and
:mod:`rfi_stamper.transmittal` (minipdf) for the printable log.  All writes
are atomic (temp file + fsync + ``os.replace``).
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import datetime, timezone

from . import transmittal

_VERSION = 1

#: Longest rendered "Measurements" cell in the PDF log, in characters.
MEASUREMENTS_CLIP = 160


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clip(text: str, limit: int) -> str:
    """Hard-clip ``text`` to ``limit`` characters with a trailing ellipsis."""
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


# ------------------------------------------------------------------ entry ---

@dataclass
class DaybookEntry:
    """One day's report.  Build new entries with :meth:`new` (it assigns the
    id, today's date, and the creation timestamp); the plain constructor is
    for deserialization."""

    id: str
    date: str = ""                     # ISO date (YYYY-MM-DD)
    crew: str = ""
    weather: str = ""
    summary: str = ""
    comments: str = ""
    measurements: list = field(default_factory=list)   # free-form strings
    photos: list = field(default_factory=list)         # file paths, refs only
    author: str = ""
    created: str = ""                  # ISO timestamp, set by new()

    @classmethod
    def new(cls, **kw) -> DaybookEntry:
        """A fresh entry: uuid4-hex id, today's date, and a UTC creation
        timestamp — any of which an explicit keyword overrides."""
        kw.setdefault("id", uuid.uuid4().hex)
        kw.setdefault("date", _date.today().isoformat())
        kw.setdefault("created", _now_iso())
        return cls(**kw)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "date": self.date,
            "crew": self.crew,
            "weather": self.weather,
            "summary": self.summary,
            "comments": self.comments,
            "measurements": [str(m) for m in self.measurements],
            "photos": [str(p) for p in self.photos],
            "author": self.author,
            "created": self.created,
        }

    @classmethod
    def from_dict(cls, d: dict) -> DaybookEntry:
        return cls(
            id=str(d.get("id", "")),
            date=str(d.get("date", "")),
            crew=str(d.get("crew", "")),
            weather=str(d.get("weather", "")),
            summary=str(d.get("summary", "")),
            comments=str(d.get("comments", "")),
            measurements=[str(m) for m in (d.get("measurements") or [])],
            photos=[str(p) for p in (d.get("photos") or [])],
            author=str(d.get("author", "")),
            created=str(d.get("created", "")),
        )


# ------------------------------------------------------------------ store ---

class DaybookStore:
    """Daybook entries persisted to a JSON sidecar.

    Construct with the project store's path (typically the ``.ploom.json``
    file) and the sidecar ``<base>.daybook.json`` is loaded automatically if
    present; every :meth:`add` / :meth:`remove` autosaves.  Construct with no
    path for an in-memory store (call :meth:`save` with an explicit path to
    persist)."""

    SUFFIX = ".daybook.json"

    def __init__(self, base_path: str | None = None):
        self.base_path = base_path
        self.path = (base_path + self.SUFFIX) if base_path else None
        self.entries: list[DaybookEntry] = []
        if self.path and os.path.exists(self.path):
            self.load()

    # ------------------------------------------------------------ updates --

    def add(self, **kw) -> DaybookEntry:
        """Create a :meth:`DaybookEntry.new` from the keywords, append it,
        autosave, and return it."""
        entry = DaybookEntry.new(**kw)
        self.entries.append(entry)
        if self.path:
            self.save()
        return entry

    def remove(self, id: str) -> bool:
        """Delete the entry with this id; autosaves.  ``False`` if absent."""
        for i, e in enumerate(self.entries):
            if e.id == id:
                del self.entries[i]
                if self.path:
                    self.save()
                return True
        return False

    # ------------------------------------------------------------ queries --

    def get(self, id: str):
        """The entry with this id, or ``None``."""
        for e in self.entries:
            if e.id == id:
                return e
        return None

    def by_date(self) -> list:
        """Entries newest date first; same-day entries newest created first.
        A new sorted list — the store's own order is untouched."""
        return sorted(self.entries, key=lambda e: (e.date, e.created),
                      reverse=True)

    def counts(self) -> dict:
        """``{"entries": n, "photos": total photo refs, "days": distinct
        dates}`` (blank dates do not count as a day)."""
        return {
            "entries": len(self.entries),
            "photos": sum(len(e.photos) for e in self.entries),
            "days": len({e.date for e in self.entries if e.date}),
        }

    # -------------------------------------------------------- persistence --

    def save(self, path: str | None = None) -> None:
        """Atomically write the versioned JSON sidecar (temp file + fsync +
        ``os.replace`` — a crash can never leave a truncated file)."""
        path = path or self.path
        if not path:
            raise ValueError("no sidecar path; construct with base_path or "
                             "pass an explicit path")
        blob = json.dumps(
            {"version": _VERSION,
             "entries": [e.to_dict() for e in self.entries]},
            indent=2, sort_keys=True).encode("utf-8")
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def load(self, path: str | None = None) -> None:
        """Load and validate the sidecar; malformed entries are dropped rather
        than crashing (the sidecar is user-visible and hand-editable)."""
        path = path or self.path
        if not path:
            raise ValueError("no sidecar path; construct with base_path or "
                             "pass an explicit path")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("entries") if isinstance(data, dict) else None
        cleaned: list[DaybookEntry] = []
        for d in raw if isinstance(raw, list) else []:
            if not (isinstance(d, dict) and str(d.get("id", "")).strip()):
                continue                    # an entry without an id is junk
            cleaned.append(DaybookEntry.from_dict(d))
        self.entries = cleaned


# -------------------------------------------------------------- printed log --

def daybook_pdf(store, out_path: str,
                title: str = "DAYBOOK — DAILY PROGRESS LOG",
                log=print) -> dict:
    """Render the daybook as a table PDF: one row per entry, newest first —
    ``Date | Crew | Weather | Summary | Measurements | Photos``.

    Measurements are joined with ``" | "`` and clipped to about
    :data:`MEASUREMENTS_CLIP` characters; the Photos column is a reference
    *count* — file paths never appear in the printed log.  Delegates to
    :func:`rfi_stamper.transmittal.table_pdf` (atomic write) and returns its
    result dict."""
    headers = ["Date", "Crew", "Weather", "Summary", "Measurements", "Photos"]
    rows: list[list] = []
    for e in store.by_date():
        rows.append([
            e.date,
            e.crew,
            e.weather,
            e.summary,
            _clip(" | ".join(str(m) for m in e.measurements),
                  MEASUREMENTS_CLIP),
            str(len(e.photos)),
        ])
    c = store.counts()
    subtitle = f"{c['entries']} entry(ies) · {c['photos']} photo ref(s)"
    # sums to the usable letter-portrait width (~504 pt); headers never wrap
    col_widths = [62.0, 70.0, 60.0, 148.0, 118.0, 46.0]
    return transmittal.table_pdf(out_path, headers, rows, title=title,
                                 subtitle=subtitle, col_widths=col_widths,
                                 log=log)
