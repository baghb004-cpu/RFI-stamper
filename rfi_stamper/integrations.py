"""App integrations — FILE-BASED bridges to other software (offline by policy).

"Integration" here never means a network connection: this product is 100%
offline (see CLAUDE.md invariant 1).  Every connector below reads or writes
ordinary local files in formats other desktop tools already understand —
CSV for spreadsheet applications, ICS for calendar apps, a JSON bundle for
hand-off/backup, and a one-shot drop-folder sweep that sorts files exported
by other PDF/document tools.  No sockets, no watchers, no daemons, no cloud.

All writers use the same crash-safe atomic pattern as merge.py: write to a
sibling ``.part`` file, fsync, then ``os.replace`` — a killed process can
never leave a truncated file at the final path.

The project data layer (``rfi_stamper.project``) is imported lazily inside
the functions that need it, so this module stays importable on its own.
"""
from __future__ import annotations

import csv
import io
import json
import os
import zipfile
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime, timedelta, timezone

import fitz  # PyMuPDF — used only for local page counts in scan_drop_folder

# ---------------------------------------------------------------------------
# connector registry (metadata only; the functions below do the work)
# ---------------------------------------------------------------------------


@dataclass
class Connector:
    key: str
    name: str
    desc: str
    direction: str          # "import" / "export" / "both"
    formats: list = field(default_factory=list)


REGISTRY: list[Connector] = [
    Connector(
        key="csv-tasks", name="Task list (CSV)", direction="both",
        formats=["csv"],
        desc=("Tasks as a plain CSV file that spreadsheet applications and "
              "other project tools open directly; import accepts common "
              "header spellings (title/name, assignee/owner, due/date). "
              "Local files only — no cloud.")),
    Connector(
        key="csv-punch", name="Punch list (CSV)", direction="export",
        formats=["csv"],
        desc=("Punch items as CSV for spreadsheet applications and field "
              "report tools. Local file only — no cloud.")),
    Connector(
        key="csv-budget", name="Budget (CSV)", direction="export",
        formats=["csv"],
        desc=("Budget lines as CSV for spreadsheet applications and "
              "accounting tools. Local file only — no cloud.")),
    Connector(
        key="csv-change-orders", name="Change orders (CSV)",
        direction="export", formats=["csv"],
        desc=("Change orders as CSV for spreadsheet applications and cost "
              "tracking tools. Local file only — no cloud.")),
    Connector(
        key="ics-schedule", name="Schedule (iCalendar)", direction="export",
        formats=["ics"],
        desc=("Schedule as an RFC 5545 .ics file that desktop and mobile "
              "calendar apps import as all-day events. Local file only — "
              "no cloud.")),
    Connector(
        key="json-bundle", name="Project bundle (zip of JSON)",
        direction="both", formats=["zip", "json"],
        desc=("The whole project as a zip of plain JSON, for backups and "
              "hand-off to other project tools or scripts. Local file only "
              "— no cloud.")),
    Connector(
        key="drop-folder", name="Drop folder sweep", direction="import",
        formats=["pdf", "zip", "txt"],
        desc=("One-shot sweep of a local folder: sorts files exported by "
              "other PDF/document tools into RFIs, plan sets, and other. "
              "No watchers, no daemons, no cloud — runs once when asked.")),
]

# kinds this module handles directly; bundles cover every kind project.py
# declares (see _all_kinds), so extra kinds travel through untouched
_KINDS = ("tasks", "schedule", "punch", "budget", "change_orders")

# CSV column orders (match project.py field order)
_CSV_HEADERS = {
    "tasks": ["id", "title", "desc", "assignee", "status", "due",
              "priority", "linked_sheet", "created", "updated"],
    "punch": ["id", "title", "location", "sheet", "status", "assignee",
              "photo_path", "created"],
    "budget": ["id", "code", "desc", "budget", "committed", "spent"],
    "change_orders": ["id", "number", "title", "amount", "status",
                      "days_impact", "created"],
}


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

def _atomic_write_bytes(data: bytes, out_path: str) -> None:
    """Write beside out_path, fsync, then atomically replace (merge.py
    pattern): a crash can never leave a truncated file at the final path."""
    tmp = out_path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out_path)


def _as_dict(item) -> dict:
    if hasattr(item, "to_dict"):
        return item.to_dict()
    if is_dataclass(item):
        return asdict(item)
    return dict(item)


#: Leading characters a spreadsheet may interpret as a formula (CSV injection).
_CSV_INJECT = ("=", "+", "-", "@", "\t", "\r", "\n")


def _csv_safe(v):
    """Neutralize spreadsheet formula injection: prefix a text value that
    begins with a formula trigger (= + - @ TAB CR LF) with a single quote.
    Non-string values pass through untouched (numbers stay numeric)."""
    if isinstance(v, str) and v[:1] in _CSV_INJECT:
        return "'" + v
    return v


def _export_csv(project, kind: str, out_path: str, log) -> int:
    headers = _CSV_HEADERS[kind]
    buf = io.StringIO()
    w = csv.writer(buf)               # csv default \r\n row endings
    w.writerow(headers)
    n = 0
    for item in project.items(kind):
        d = _as_dict(item)
        w.writerow([_csv_safe("" if d.get(h) is None else d.get(h, ""))
                    for h in headers])
        n += 1
    _atomic_write_bytes(buf.getvalue().encode("utf-8"), out_path)
    log(f"  wrote {out_path} ({n} {kind.replace('_', ' ')} row(s))")
    return n


# ---------------------------------------------------------------------------
# CSV export / import
# ---------------------------------------------------------------------------

def export_tasks_csv(project, out_path: str, log=print) -> int:
    """Write the task list as CSV; returns data rows written."""
    return _export_csv(project, "tasks", out_path, log)


def export_punch_csv(project, out_path: str, log=print) -> int:
    return _export_csv(project, "punch", out_path, log)


def export_budget_csv(project, out_path: str, log=print) -> int:
    return _export_csv(project, "budget", out_path, log)


def export_change_orders_csv(project, out_path: str, log=print) -> int:
    return _export_csv(project, "change_orders", out_path, log)


# header-tolerant task import: alias (lowercased, stripped) -> Task field
_TASK_ALIASES = {
    "title": "title", "name": "title", "task": "title",
    "desc": "desc", "description": "desc", "notes": "desc",
    "assignee": "assignee", "owner": "assignee", "assigned to": "assignee",
    "status": "status", "state": "status",
    "due": "due", "date": "due", "due date": "due", "due_date": "due",
    "priority": "priority",
    "linked_sheet": "linked_sheet", "linked sheet": "linked_sheet",
    "sheet": "linked_sheet",
}


def import_tasks_csv(project, path: str, log=print) -> int:
    """Import tasks from a CSV written by this tool or any spreadsheet.

    Header-tolerant (title/name, assignee/owner, due/date, status, priority;
    case-insensitive, BOM-safe).  Rows without a title are skipped.  Returns
    the number of tasks added.
    """
    from rfi_stamper.project import Task   # lazy: data layer, avoid cycles

    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        log(f"  !! {os.path.basename(path)}: empty file, nothing imported")
        return 0
    cols: dict[str, int] = {}          # canonical field -> column index
    for i, h in enumerate(rows[0]):
        canon = _TASK_ALIASES.get(h.strip().lower())
        if canon and canon not in cols:
            cols[canon] = i
    if "title" not in cols:
        raise ValueError(
            f"{path}: no title/name column found in header {rows[0]!r}")

    def cell(row: list, key: str) -> str:
        i = cols.get(key)
        v = row[i].strip() if i is not None and i < len(row) else ""
        # undo the CSV-injection guard apostrophe for clean round-trips
        if len(v) > 1 and v[0] == "'" and v[1] in _CSV_INJECT:
            v = v[1:]
        return v

    added = skipped = 0
    for row in rows[1:]:
        title = cell(row, "title")
        if not title:
            skipped += 1
            continue
        kw = {"title": title}
        for key in ("desc", "assignee", "status", "due", "priority",
                    "linked_sheet"):
            v = cell(row, key)
            if v:
                kw[key] = v
        project.add("tasks", Task.new(**kw))
        added += 1
    log(f"  imported {added} task(s) from {path}"
        + (f" ({skipped} titleless row(s) skipped)" if skipped else ""))
    return added


# ---------------------------------------------------------------------------
# iCalendar (RFC 5545) schedule export — hand-rolled, no dependencies
# ---------------------------------------------------------------------------

def _ics_escape(text: str) -> str:
    """Escape TEXT per RFC 5545 §3.3.11: backslash first, then ; , and
    newlines (any style) as literal \\n."""
    text = text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


def _ics_date(value) -> date | None:
    """ISO date (or datetime) string -> date; None if absent/unparseable."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _fold(line: str) -> bytes:
    """Fold one content line at 75 octets (RFC 5545 §3.1): continuation
    lines start with a single space that counts toward their own 75-octet
    budget.  Splits between characters, never inside a UTF-8 sequence."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return raw
    out = bytearray()
    chunk = bytearray()
    limit = 75                       # first physical line: 75 octets
    for ch in line:
        b = ch.encode("utf-8")
        if len(chunk) + len(b) > limit:
            out += chunk + b"\r\n "
            chunk = bytearray()
            limit = 74               # continuation: 1 space + 74 octets
        chunk += b
    out += chunk
    return bytes(out)


def export_schedule_ics(project, out_path: str, log=print) -> int:
    """Write the schedule as an all-day-event VCALENDAR for calendar apps.

    One VEVENT per ScheduleItem with a start date; items without dates are
    skipped (logged).  DTEND is exclusive per RFC 5545, so the stored
    inclusive end date is advanced one day.  Returns events written.
    """
    lines = ["BEGIN:VCALENDAR",
             "VERSION:2.0",
             "PRODID:-//Offline Plan Toolkit//Schedule Export//EN",
             "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    n = skipped = 0
    for item in project.items("schedule"):
        d = _as_dict(item)
        start = _ics_date(d.get("start"))
        if start is None:
            skipped += 1
            continue
        end = _ics_date(d.get("end")) or start        # one-day if no end
        end_excl = max(end, start) + timedelta(days=1)
        uid = _ics_escape(str(d.get("id") or f"schedule-{n + 1}"))
        title = str(d.get("title") or "(untitled)")
        desc_bits = []
        if d.get("crew"):
            desc_bits.append(f"Crew: {d['crew']}")
        if d.get("pct") not in (None, ""):
            desc_bits.append(f"Complete: {d['pct']}%")
        lines += ["BEGIN:VEVENT",
                  f"UID:{uid}@offline-plan-toolkit.local",
                  f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}",
                  f"DTEND;VALUE=DATE:{end_excl.strftime('%Y%m%d')}",
                  f"SUMMARY:{_ics_escape(title)}"]
        if desc_bits:
            lines.append(f"DESCRIPTION:{_ics_escape('; '.join(desc_bits))}")
        lines.append("END:VEVENT")
        n += 1
    lines.append("END:VCALENDAR")
    payload = b"".join(_fold(ln) + b"\r\n" for ln in lines)
    _atomic_write_bytes(payload, out_path)
    log(f"  wrote {out_path} ({n} event(s)"
        + (f", {skipped} undated item(s) skipped" if skipped else "") + ")")
    return n


# ---------------------------------------------------------------------------
# JSON bundle (zip) — full-project backup / hand-off
# ---------------------------------------------------------------------------

_BUNDLE_VERSION = 1


def _all_kinds() -> tuple:
    """Every kind the project store declares (falls back to the core five
    if project.py is not importable yet — it is built in parallel)."""
    try:
        from rfi_stamper.project import KINDS
        return tuple(KINDS)
    except Exception:
        return _KINDS


def _project_to_dict(project, kinds) -> dict:
    if hasattr(project, "to_dict"):
        return project.to_dict()
    # same shape as the on-disk .ploom.json save() payload
    data: dict = {"version": 1, "name": getattr(project, "name", "")}
    for k in kinds:
        data[k] = [_as_dict(it) for it in project.items(k)]
    return data


def export_bundle(project, out_zip: str, log=print) -> str:
    """Zip the whole project as plain JSON (project.json + manifest.json).

    Returns the path written.  The manifest carries {version, kinds, counts}
    so other tools (and import_bundle) can sanity-check without parsing the
    full payload.
    """
    kinds = _all_kinds()
    data = _project_to_dict(project, kinds)
    counts = {k: len(list(project.items(k))) for k in kinds}
    manifest = {"version": _BUNDLE_VERSION, "kinds": list(kinds),
                "counts": counts}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("project.json",
                   json.dumps(data, indent=2, ensure_ascii=False))
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
    _atomic_write_bytes(buf.getvalue(), out_zip)
    log(f"  wrote {out_zip} ({sum(counts.values())} item(s) across "
        f"{len(kinds)} kind(s))")
    return out_zip


def import_bundle(path: str, log=print):
    """Load a bundle zip into a new in-memory Project (nothing on disk)."""
    from rfi_stamper import project as _pmod   # lazy: data layer

    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "project.json" not in names:
            raise ValueError(f"{path}: not a project bundle "
                             "(missing project.json)")
        data = json.loads(z.read("project.json").decode("utf-8"))
        manifest = (json.loads(z.read("manifest.json").decode("utf-8"))
                    if "manifest.json" in names else {})

    kinds = _all_kinds()
    if hasattr(_pmod.Project, "from_dict"):
        proj = _pmod.Project.from_dict(data)
    else:
        classes = getattr(_pmod, "_CLS_FOR", None) or {
            "tasks": _pmod.Task, "schedule": _pmod.ScheduleItem,
            "punch": _pmod.PunchItem, "budget": _pmod.BudgetLine,
            "change_orders": _pmod.ChangeOrder}
        proj = _new_project(_pmod)
        if data.get("name") and hasattr(proj, "name"):
            proj.name = str(data["name"])
        for kind in kinds:
            cls = classes.get(kind)
            for d in (data.get(kind) or []) if cls else []:
                proj.add(kind, cls.from_dict(d))

    counts = {k: len(list(proj.items(k))) for k in kinds}
    for kind, expected in (manifest.get("counts") or {}).items():
        if kind in counts and counts[kind] != expected:
            log(f"  !! {kind}: manifest says {expected}, "
                f"loaded {counts[kind]}")
    log(f"  loaded {sum(counts.values())} item(s) from {path}")
    return proj


def _new_project(pmod):
    """Construct an empty Project without assuming its __init__ signature."""
    try:
        return pmod.Project()
    except TypeError:
        if hasattr(pmod.Project, "new"):
            return pmod.Project.new()
        raise


# ---------------------------------------------------------------------------
# drop-folder sweep (one-shot; no watchers, no daemons)
# ---------------------------------------------------------------------------

_PLANS_MIN_PAGES = 8
_PLANS_MIN_BYTES = 2 * 1024 * 1024      # 2 MB


def scan_drop_folder(folder: str, log=print) -> dict:
    """Classify every file in a local folder, once, right now.

    Returns {"rfis": [...], "plans": [...], "other": [...]} of absolute
    paths.  Heuristics (magic-byte sniff first — never trust extensions):
      * %PDF with >= 8 pages or > 2 MB      -> plans (full sheet set)
      * %PDF smaller                        -> rfis  (single RFI document)
      * PK zip named .pdf                   -> rfis  (document-controls
                                               export: JPEGs + OCR + manifest)
      * .txt                                -> rfis  (raw-text RFI export)
      * anything else / unreadable          -> other
    """
    out: dict[str, list] = {"rfis": [], "plans": [], "other": []}
    for name in sorted(os.listdir(folder)):
        path = os.path.abspath(os.path.join(folder, name))
        if not os.path.isfile(path):
            continue
        bucket = "other"
        try:
            with open(path, "rb") as f:
                head = f.read(8)
            if head.startswith(b"%PDF"):
                size = os.path.getsize(path)
                try:
                    with fitz.open(path) as doc:
                        pages = doc.page_count
                except Exception:
                    pages = None                    # unreadable -> other
                if pages is not None:
                    bucket = ("plans" if pages >= _PLANS_MIN_PAGES
                              or size > _PLANS_MIN_BYTES else "rfis")
            elif (head.startswith((b"PK\x03\x04", b"PK\x05\x06"))
                  and name.lower().endswith(".pdf")):
                bucket = "rfis"     # zip-in-.pdf document-controls export
            elif name.lower().endswith(".txt"):
                bucket = "rfis"
        except OSError:
            bucket = "other"
        out[bucket].append(path)
        log(f"  {bucket:>5}: {name}")
    log(f"  scan of {folder}: {len(out['rfis'])} rfi(s), "
        f"{len(out['plans'])} plan set(s), {len(out['other'])} other")
    return out
