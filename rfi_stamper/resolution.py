"""RFI resolution lifecycle: the status layer that rides on the stamped notes.

A stamped plan set already shows each RFI's question and answer; this module
adds *whether the fix is done*.  Every RFI moves through a five-step
lifecycle::

    open -> answered -> in_work -> fixed -> verified

Statuses live in a versioned JSON sidecar next to the plan PDF
(``<plan.pdf>.rfistatus.json``, same convention as the markups sidecar), each
with a full timestamped history.  The current status is surfaced two ways:

* a compact suffix appended to the note-box header line by
  :func:`rfi_stamper.layout.make_entries` (e.g. ``RFI 001 — TITLE · ANSWERED``)
  — same bold red Helvetica header, no change to the user-approved box style;
* :func:`pickup_pdf`, the one-page(ish) table a designer carries: every mapped
  RFI that is not yet verified, with a concrete "what to do next" instruction.

Fully offline; depends only on the standard library and
:mod:`rfi_stamper.transmittal` (minipdf).  All writes are atomic.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from . import transmittal

#: Lifecycle stages, in order.
STATUSES = ("open", "answered", "in_work", "fixed", "verified")

#: Display labels (used in the header suffix and the pickup sheet).
LABELS = {"open": "OPEN", "answered": "ANSWERED", "in_work": "IN WORK",
          "fixed": "FIXED", "verified": "VERIFIED"}

#: What the designer should do next, per current status.  ``verified`` has no
#: entry on purpose — verified items never appear on the pickup sheet.
NEXT_STEP = {
    "open": "Answer pending — do not build from this detail yet",
    "answered": "Incorporate answer; mark In Work",
    "in_work": "Finish and mark Fixed",
    "fixed": "Field-verify, then mark Verified",
}

_VERSION = 1


def status_suffix(status: str) -> str:
    """Compact header suffix for a status: ``" · ANSWERED"`` (middle dot, one
    space each side).  Unknown or empty statuses return ``""`` so callers can
    append unconditionally."""
    label = LABELS.get(str(status or "").strip().lower())
    return f" · {label}" if label else ""


def _norm(number) -> str:
    """Canonical RFI number: stripped; bare digits zero-filled to 3 the same
    way core parsing does, so '1' and '001' address the same record."""
    s = str(number or "").strip()
    return s.zfill(3) if s.isdigit() else s


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ResolutionStore:
    """Per-plan RFI status store with history, persisted to a JSON sidecar.

    Construct with the plan PDF's path and the sidecar
    ``<plan>.rfistatus.json`` is loaded automatically if present; every
    :meth:`set` / :meth:`seed_from_records` autosaves.  Construct with no path
    for an in-memory store (call :meth:`save` with an explicit path to
    persist)."""

    SUFFIX = ".rfistatus.json"

    def __init__(self, plan_path: str | None = None):
        self.plan_path = plan_path
        self.path = (plan_path + self.SUFFIX) if plan_path else None
        self._rfis: dict[str, list[dict]] = {}     # number -> history entries
        if self.path and os.path.exists(self.path):
            self.load()

    # ------------------------------------------------------------ queries --

    def get(self, number: str) -> str:
        """Current status for an RFI number; ``""`` if untracked."""
        hist = self._rfis.get(_norm(number))
        return hist[-1]["status"] if hist else ""

    def history(self, number: str) -> list:
        """Full status history (oldest first) as a list of dicts with keys
        ``status``, ``ts``, ``note``, ``author``.  Copy — safe to mutate."""
        return [dict(e) for e in self._rfis.get(_norm(number), [])]

    def statuses(self) -> dict:
        """``{number: current_status}`` for every tracked RFI."""
        return {n: h[-1]["status"] for n, h in self._rfis.items() if h}

    def counts(self) -> dict:
        """``{status: n}`` over current statuses, zeros included."""
        out = {s: 0 for s in STATUSES}
        for st in self.statuses().values():
            if st in out:
                out[st] += 1
        return out

    # ------------------------------------------------------------ updates --

    def set(self, number: str, status: str, note: str = "",
            author: str = "") -> None:
        """Record a status change (validated) with a UTC timestamp, an
        optional note, and an optional author; autosaves the sidecar."""
        status = str(status or "").strip().lower()
        if status not in STATUSES:
            raise ValueError(
                f"unknown status {status!r}; expected one of {STATUSES}")
        num = _norm(number)
        if not num:
            raise ValueError("RFI number must be non-empty")
        self._rfis.setdefault(num, []).append({
            "status": status, "ts": _now_iso(),
            "note": str(note or ""), "author": str(author or "")})
        if self.path:
            self.save()

    def seed_from_records(self, records) -> int:
        """Start tracking any untracked record: ``answered`` if the record
        carries an answer, else ``open``.  Existing entries are NEVER touched
        (so a hand-set ``fixed`` survives a re-seed).  Returns how many
        numbers were added."""
        added = 0
        for rec in records:
            num = _norm(getattr(rec, "number", ""))
            if not num or self._rfis.get(num):
                continue                     # tracked already — never downgrade
            status = "answered" if getattr(rec, "has_answer", False) else "open"
            self._rfis[num] = [{
                "status": status, "ts": _now_iso(),
                "note": "seeded from RFI record", "author": ""}]
            added += 1
        if added and self.path:
            self.save()
        return added

    # -------------------------------------------------------- persistence --

    def save(self, path: str | None = None) -> None:
        """Atomically write the versioned JSON sidecar (temp file + fsync +
        ``os.replace`` — a crash can never leave a truncated file)."""
        path = path or self.path
        if not path:
            raise ValueError("no sidecar path; construct with plan_path or "
                             "pass an explicit path")
        blob = json.dumps({"version": _VERSION, "rfis": self._rfis},
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
            raise ValueError("no sidecar path; construct with plan_path or "
                             "pass an explicit path")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        rfis = data.get("rfis") if isinstance(data, dict) else None
        cleaned: dict[str, list[dict]] = {}
        for num, hist in (rfis or {}).items():
            entries = []
            for e in hist if isinstance(hist, list) else []:
                if not (isinstance(e, dict) and e.get("status") in STATUSES):
                    continue
                entries.append({"status": e["status"],
                                "ts": str(e.get("ts", "")),
                                "note": str(e.get("note", "")),
                                "author": str(e.get("author", ""))})
            if entries:
                # #12: zfill-equivalent keys ("1" and "001") normalize to the
                # same RFI — MERGE their histories instead of letting the last
                # one overwrite (which silently dropped history and could
                # downgrade the current status).
                key = _norm(num)
                cleaned.setdefault(key, []).extend(entries)
        # order each merged history by timestamp so get()/statuses() (which
        # read the last entry) see the latest lifecycle step last.
        for hist in cleaned.values():
            hist.sort(key=lambda e: e["ts"])
        self._rfis = cleaned


# ------------------------------------------------------------ pickup sheet --

def _rfi_num_key(num: str):
    m = re.search(r"\d+", str(num))
    return (0, int(m.group()), str(num)) if m else (1, 0, str(num))


def pickup_pdf(rows, index, store, out_path: str,
               title: str = "DESIGNER PICKUP SHEET", log=print) -> dict:
    """Render the sheet a designer carries: one row per mapped RFI that is not
    yet ``verified`` — ``Sheet(s) | RFI | Status | Title | What to do next``.

    ``rows``/``index`` are pipeline duck-types (``row.record.number`` /
    ``.title``, ``row.pages``, ``index.info(p).sheet``); ``store`` is a
    :class:`ResolutionStore`.  Untracked RFIs are shown at their seed status
    (``answered`` if the record has an answer, else ``open``).  Rows are
    sorted by first sheet then RFI number.  Delegates to
    :func:`rfi_stamper.transmittal.table_pdf` (atomic write) and returns its
    result dict plus ``{"items": n}``."""
    items = []
    for row in rows:
        pages = list(getattr(row, "pages", None) or [])
        if not pages:
            continue                             # pickup covers mapped RFIs only
        rec = row.record
        status = store.get(rec.number) or (
            "answered" if getattr(rec, "has_answer", False) else "open")
        if status == "verified":
            continue                             # done — nothing to pick up
        sheets = ", ".join(index.info(p).sheet for p in pages)
        items.append((sheets, rec.number, status, rec.title))

    items.sort(key=lambda it: (it[0], _rfi_num_key(it[1])))
    table_rows = [[sheets, num, LABELS[st], rfi_title, NEXT_STEP[st]]
                  for sheets, num, st, rfi_title in items]

    headers = ["Sheet(s)", "RFI", "Status", "Title", "What to do next"]
    # sums to the usable letter-portrait width (~504 pt); headers never wrap
    col_widths = [74.0, 36.0, 64.0, 150.0, 180.0]
    result = dict(transmittal.table_pdf(
        out_path, headers, table_rows, title=title,
        subtitle=f"{len(table_rows)} outstanding RFI item(s) — "
                 "not yet field-verified",
        col_widths=col_widths, log=log))
    result["items"] = len(table_rows)
    return result
