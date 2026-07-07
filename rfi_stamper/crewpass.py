"""Crewpass — offline seat management (who runs the toolkit on which device).

A *seat* is one person's use of the software on one device.  Crewpass records
seat assignments, transfers between devices, releases, and prints a usage
report — entirely from a LOCAL versioned JSON ledger (default
``~/.planloom/crewpass.json``).  There is **no license server, no activation
call, no phone-home, no network traffic of any kind, ever**: the ledger is a
bookkeeping convenience for crews sharing hardware, not an enforcement
mechanism, and it honors the toolkit-wide offline invariant (NDA-covered
work never prompts an outbound connection).

Released seats keep their record (device cleared, history appended) so the
usage report stays a complete audit trail.  Depends only on the standard
library plus :mod:`rfi_stamper.transmittal` (reportlab) for the printable
report.  All writes are atomic (temp file + fsync + ``os.replace``).
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import transmittal

#: Seat roles, most to least capable.
ROLES = ("office", "field", "viewer")

_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _short_ts(ts: str) -> str:
    """Compact an ISO timestamp for table cells: ``2026-07-07 18:04``."""
    return str(ts or "")[:16].replace("T", " ")


# -------------------------------------------------------------------- seat --

@dataclass
class Seat:
    """One person's seat on one device, with a full event history.

    ``device == ""`` means the seat has been released; the record is kept for
    the report.  ``history`` entries are ``{"event", "ts", "detail"}`` dicts,
    oldest first.
    """

    id: str
    user: str = ""
    device: str = ""
    role: str = "field"
    activated: str = ""                # ISO timestamp, set by :meth:`new`
    history: list = field(default_factory=list)   # [{"event","ts","detail"}]

    @classmethod
    def new(cls, **kw) -> "Seat":
        """Build a seat with a fresh ``uuid4().hex`` id and an ISO ``activated``
        timestamp (either may still be overridden through ``kw``)."""
        kw.setdefault("id", uuid.uuid4().hex)
        kw.setdefault("activated", _now_iso())
        return cls(**kw)

    def to_dict(self) -> dict:
        return {"id": self.id, "user": self.user, "device": self.device,
                "role": self.role, "activated": self.activated,
                "history": [dict(e) for e in self.history]}

    @classmethod
    def from_dict(cls, data: dict) -> "Seat":
        """Rebuild a seat from ledger JSON; malformed history entries are
        dropped rather than crashing (the ledger is hand-editable)."""
        history = [{"event": str(e.get("event", "")),
                    "ts": str(e.get("ts", "")),
                    "detail": str(e.get("detail", ""))}
                   for e in (data.get("history") or [])
                   if isinstance(e, dict) and e.get("event")]
        return cls(id=str(data.get("id", "")),
                   user=str(data.get("user", "")),
                   device=str(data.get("device", "")),
                   role=str(data.get("role", "")) or "field",
                   activated=str(data.get("activated", "")),
                   history=history)


# ------------------------------------------------------------------ ledger --

class Ledger:
    """The local seat ledger, persisted to a versioned JSON file.

    Construct with a path (or let it default to :data:`DEFAULT_PATH`, expanded
    at runtime) and the file is loaded automatically if it exists; every
    :meth:`assign` / :meth:`transfer` / :meth:`release` autosaves.  The file
    never leaves the machine — see the module docstring.
    """

    DEFAULT_PATH = os.path.join("~", ".planloom", "crewpass.json")

    def __init__(self, path: str | None = None):
        self.path = os.path.expanduser(path or self.DEFAULT_PATH)
        self.seats: list[Seat] = []
        if os.path.exists(self.path):
            self.load()

    # ------------------------------------------------------------ queries --

    def get(self, seat_id: str) -> Seat | None:
        """The seat with this id, or ``None``."""
        for seat in self.seats:
            if seat.id == seat_id:
                return seat
        return None

    def active(self) -> list[Seat]:
        """Assigned seats (device non-empty), in ledger order."""
        return [s for s in self.seats if s.device]

    def counts(self) -> dict:
        """``{"seats": total_records, "active": n_active, "by_role": {...}}``
        where ``by_role`` counts ACTIVE seats per role, zeros included."""
        by_role = {r: 0 for r in ROLES}
        act = self.active()
        for seat in act:
            if seat.role in by_role:
                by_role[seat.role] += 1
        return {"seats": len(self.seats), "active": len(act),
                "by_role": by_role}

    # ------------------------------------------------------------ updates --

    def assign(self, user: str, device: str, role: str = "field") -> Seat:
        """Assign ``user`` a seat on ``device``.  Validates the role, refuses
        a duplicate ACTIVE (user, device) pair, records an ``assigned``
        history event, autosaves, and returns the new :class:`Seat`."""
        role = str(role or "").strip().lower()
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}; expected one of {ROLES}")
        user = str(user or "").strip()
        device = str(device or "").strip()
        if not user or not device:
            raise ValueError("user and device must be non-empty")
        for seat in self.seats:
            if seat.device and seat.user == user and seat.device == device:
                raise ValueError(
                    f"{user!r} already holds an active seat on {device!r} "
                    f"(seat {seat.id}); release or transfer it first")
        seat = Seat.new(user=user, device=device, role=role)
        seat.history.append({"event": "assigned", "ts": seat.activated,
                             "detail": f"{user} on {device} as {role}"})
        self.seats.append(seat)
        self.save()
        return seat

    def transfer(self, seat_id: str, new_device: str) -> Seat:
        """Move a seat to ``new_device`` (``KeyError`` if the id is unknown),
        record a ``transferred`` history event with old -> new detail,
        autosave, and return the seat."""
        seat = self.get(seat_id)
        if seat is None:
            raise KeyError(seat_id)
        new_device = str(new_device or "").strip()
        if not new_device:
            raise ValueError("new_device must be non-empty (use release())")
        for other in self.seats:
            if (other is not seat and other.device
                    and other.user == seat.user
                    and other.device == new_device):
                raise ValueError(
                    f"{seat.user!r} already holds an active seat on "
                    f"{new_device!r} (seat {other.id})")
        old = seat.device
        seat.device = new_device
        seat.history.append({"event": "transferred", "ts": _now_iso(),
                             "detail": f"{old or '(released)'} -> {new_device}"})
        self.save()
        return seat

    def release(self, seat_id: str) -> bool:
        """Mark the seat released (device cleared, ``released`` history event)
        but KEEP the record for the report; autosaves.  Returns ``True`` when
        a seat was released, ``False`` for an unknown id or an already
        released seat."""
        seat = self.get(seat_id)
        if seat is None or not seat.device:
            return False
        old = seat.device
        seat.device = ""
        seat.history.append({"event": "released", "ts": _now_iso(),
                             "detail": f"released from {old}"})
        self.save()
        return True

    # -------------------------------------------------------- persistence --

    def save(self, path: str | None = None) -> None:
        """Atomically write the versioned JSON ledger (temp file + fsync +
        ``os.replace`` — a crash can never leave a truncated file).  Creates
        the parent directory if needed."""
        path = os.path.expanduser(path or self.path)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        blob = json.dumps(
            {"version": _VERSION,
             "seats": [seat.to_dict() for seat in self.seats]},
            indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def load(self, path: str | None = None) -> None:
        """Load and validate the ledger; entries without an id (and malformed
        history rows) are dropped rather than crashing."""
        path = os.path.expanduser(path or self.path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("seats") if isinstance(data, dict) else None
        seats: list[Seat] = []
        for entry in raw or []:
            if isinstance(entry, dict) and entry.get("id"):
                seats.append(Seat.from_dict(entry))
        self.seats = seats


# ------------------------------------------------------------------ report --

def report_pdf(ledger, out_path: str, title: str = "CREWPASS — SEAT REPORT",
               log=print) -> dict:
    """Render the printable seat-usage report via
    :func:`rfi_stamper.transmittal.table_pdf` (atomic write, offline).

    One row per seat record — released seats included — with columns
    ``User | Role | Device | Activated | Last event | Status`` where the last
    event comes from the history tail and Status is ``Active`` / ``Released``.
    The subtitle summarizes :meth:`Ledger.counts`.  Returns
    :func:`~rfi_stamper.transmittal.table_pdf`'s result dict.
    """
    headers = ["User", "Role", "Device", "Activated", "Last event", "Status"]
    rows: list[list] = []
    for seat in ledger.seats:
        last = seat.history[-1] if seat.history else None
        last_txt = (f"{last['event']} {_short_ts(last.get('ts', ''))}".strip()
                    if last else "")
        rows.append([
            seat.user,
            seat.role,
            seat.device or "—",
            _short_ts(seat.activated),
            last_txt,
            "Active" if seat.device else "Released",
        ])
    c = ledger.counts()
    role_bits = " / ".join(f"{r} {c['by_role'][r]}" for r in ROLES)
    subtitle = (f"{c['seats']} seat(s), {c['active']} active — "
                f"active by role: {role_bits}")
    # Tuned so headers never wrap; sums to the usable letter width (~504 pt).
    col_widths = [100.0, 54.0, 108.0, 86.0, 102.0, 54.0]
    return transmittal.table_pdf(out_path, headers, rows, title=title,
                                 subtitle=subtitle, col_widths=col_widths,
                                 log=log)
