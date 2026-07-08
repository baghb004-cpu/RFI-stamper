"""Submittal register parsing and log-PDF rendering (offline).

Submittals are the spec-section-based sibling of RFIs: shop drawings,
product data, samples and mix designs organized by CSI MasterFormat
division (03 concrete, 22 plumbing, 23 HVAC, 26 electrical...).  A
submittal register/log lists many items, each with a number, a title,
a spec section, and a review status (Approved / Approved as Noted /
Revise & Resubmit / Rejected / For Record / Pending).

Ingestion reuses :func:`rfi_stamper.core.read_document` (which sniffs
PDF / zip-package / raw-text and folds document-controls non-breaking
spaces to plain spaces).  Rendering is delegated to
``rfi_stamper.transmittal.table_pdf``, imported lazily so this module
loads even before the transmittal engine is present.

Fully offline: no network, no telemetry, no third-party services.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .core import _normalize_text, read_document

# --------------------------------------------------------------- statuses ---

#: The canonical review-status vocabulary.  ``normalize_status`` maps any
#: recognized phrasing onto exactly one of these (or "" when unknown).
CANONICAL_STATUSES: tuple[str, ...] = (
    "Approved",
    "Approved as Noted",
    "Revise & Resubmit",
    "Rejected",
    "For Record",
    "Pending",
)


def normalize_status(text: str) -> str:
    """Fold a free-text review status onto the canonical vocabulary.

    Case-insensitive; tolerant of punctuation and phrasing ("no exceptions
    taken" -> Approved, "make corrections noted" -> Approved as Noted,
    "revise and resubmit" -> Revise & Resubmit).  Returns "" when nothing
    is recognized.  Never raises.
    """
    if not text:
        return ""
    # collapse whitespace, slashes, underscores and dashes to single spaces
    t = re.sub(r"[\s–—/_.-]+", " ", str(text).lower()).strip()
    if not t:
        return ""

    # order matters: the "as noted" family must beat plain Approved / Rejected
    if any(k in t for k in (
            "as noted", "as corrected", "with comments", "w comments",
            "make corrections", "corrections noted", "note the corrections",
            "furnish as corrected", "exceptions taken as noted",
            "approved with", "approved as")):
        return "Approved as Noted"
    if any(k in t for k in (
            "revise", "resubmit", "re submit", "amend and", "correct and")):
        return "Revise & Resubmit"
    if any(k in t for k in (
            "reject", "not approved", "disapprov", "returned no",
            "returned for")):
        return "Rejected"
    if any(k in t for k in (
            "for record", "record only", "record copy", "received for record",
            "no action", "information only", "for information")) or t == "fyi":
        return "For Record"
    if any(k in t for k in (
            "approved", "approve", "no exception", "accepted", "furnish as",
            "reviewed no")) or t == "reviewed":
        return "Approved"
    if any(k in t for k in (
            "pending", "review", "open", "awaiting", "in progress", "not yet",
            "submitted", "under", "outstanding", "hold")):
        return "Pending"
    return ""


# ----------------------------------------------------------------- record ---

@dataclass
class SubmittalRecord:
    """One row of a submittal register."""

    number: str = ""
    title: str = ""
    spec_section: str = ""      # e.g. "22 11 16" or "23 05 00" (CSI MasterFormat)
    status: str = ""            # canonical (see CANONICAL_STATUSES) or ""
    ball_in_court: str = ""
    source: str = ""


# ----------------------------------------------------------------- parsing --

# any recognized field label, used as a right boundary when reading a value
_ANY_LABEL = (
    r"Submittal\s*(?:No\.?|Number|#)|Sub\s*(?:No\.?|#)"
    r"|Item(?:\s*(?:No\.?|#|Description))?"
    r"|Title|Description|Subject"
    r"|Spec(?:ification)?\.?\s*Sec(?:tion|\.)?|Spec\.?|Section"
    r"|CSI(?:\s*(?:No\.?|Section))?"
    r"|Review\s*Status|Status|Disposition|Action|Result|Response"
    r"|Ball\s*in\s*Court|Responsible(?:\s*Party)?|BIC|Assigned\s*To|Held\s*By"
    r"|Received|Returned|Rev(?:ision)?\.?|Contractor|Date"
)

# per-field label alternatives
_NUMBER_LABEL = r"Submittal\s*(?:No\.?|Number|#)|Sub\s*(?:No\.?|#)|Item\s*(?:No\.?|#)"
_TITLE_LABEL = r"Item\s*Description|Description|Title|Subject"
_SPEC_LABEL = (
    r"Spec(?:ification)?\.?\s*Sec(?:tion|\.)?|CSI(?:\s*(?:No\.?|Section))?"
    r"|Spec\.?|Section"
)
_STATUS_LABEL = r"Review\s*Status|Status|Disposition|Action|Result|Response"
_BALL_LABEL = r"Ball\s*in\s*Court|Responsible(?:\s*Party)?|BIC|Assigned\s*To|Held\s*By"

# record boundary inside a register: a line begins a new item at a number label
_NUM_START = re.compile(
    r"(?:Submittal|Sub|Item)\s*(?:No\.?|Number|#)\s*[:#]", re.IGNORECASE)

# CSI MasterFormat: "NN NN NN" / "NN NN NN.NN", or legacy 5-6 digit "NNNNN"
_CSI_SPACED = re.compile(r"\b(\d{2})[ \t]+(\d{2})[ \t]+(\d{2})(\.\d{1,2})?\b")
_CSI_LEGACY = re.compile(r"\b(\d{5,6})\b")


def _value(chunk: str, label: str, *, require_colon: bool = False) -> str:
    """Read the value that follows ``label`` up to the next label or line end.

    Uses ``[ \\t]`` (never ``\\s``) around the separator so a value never
    swallows the newline that anchors the next field -- the same discipline
    ``core.py`` relies on for RFI ingestion.
    """
    sep = r"[:#]" if require_colon else r"[:#]?"
    pat = (rf"(?:{label})[ \t]*{sep}[ \t]*"
           rf"([^\n]*?)"
           rf"(?=(?:[ \t]+(?:{_ANY_LABEL})[ \t]*[:#])|[ \t]*(?:\n|$))")
    m = re.search(pat, chunk, re.IGNORECASE)
    return m.group(1).strip(" \t.,;:-") if m else ""


def _extract_csi(text: str, legacy: bool = True) -> str:
    """Return a normalized CSI spec section found in ``text``, or "".

    The spaced ``NN NN NN`` form is unambiguous.  The loose 5-6 digit
    ``_CSI_LEGACY`` form matches dates, PO/phone numbers and the like, so it
    is only tried when ``legacy`` is set — never against a whole record chunk,
    where a fabricated section is worse than a blank one.
    """
    if not text:
        return ""
    m = _CSI_SPACED.search(text)
    if m:
        return f"{m.group(1)} {m.group(2)} {m.group(3)}{m.group(4) or ''}"
    if legacy:
        m = _CSI_LEGACY.search(text)
        if m:
            return m.group(1)
    return ""


def _parse_fields(chunk: str, source: str) -> SubmittalRecord:
    number = _value(chunk, _NUMBER_LABEL)
    if not number:
        number = _value(chunk, r"No\.?", require_colon=True)

    title = _value(chunk, _TITLE_LABEL)

    raw_spec = _value(chunk, _SPEC_LABEL)
    if raw_spec:
        spec = _extract_csi(raw_spec) or raw_spec
    else:
        spec = _extract_csi(number) or _extract_csi(chunk, legacy=False)

    status = normalize_status(_value(chunk, _STATUS_LABEL))
    ball = _value(chunk, _BALL_LABEL)

    return SubmittalRecord(
        number=number, title=title, spec_section=spec,
        status=status, ball_in_court=ball, source=source)


def _split_records(text: str, source: str) -> list[SubmittalRecord]:
    text = _normalize_text(text)
    starts = [m.start() for m in _NUM_START.finditer(text)]
    if starts:
        bounds = starts + [len(text)]
        chunks = [text[bounds[i]:bounds[i + 1]] for i in range(len(starts))]
    else:
        # no explicit item labels: treat each line as a candidate row
        chunks = text.split("\n")

    recs: list[SubmittalRecord] = []
    for c in chunks:
        if len(c.strip()) < 3:
            continue
        r = _parse_fields(c, source)
        if r.number or r.status or r.spec_section or r.title:
            recs.append(r)
    return recs


def _merge_into(keep: SubmittalRecord, other: SubmittalRecord) -> None:
    """Backfill blank fields of ``keep`` from ``other`` (never overwrite)."""
    for fld in ("title", "spec_section", "status", "ball_in_court", "source"):
        if not getattr(keep, fld) and getattr(other, fld):
            setattr(keep, fld, getattr(other, fld))


def _sort_key(r: SubmittalRecord):
    # blank spec sorts last; then by number
    return (r.spec_section or "￿", r.number)


def parse_submittals(paths, log=print) -> list[SubmittalRecord]:
    """Parse one or more register files/dirs into de-duplicated records.

    Every path is read through :func:`core.read_document` (PDF, zip package
    or raw text).  A single file may enumerate many items.  Records are keyed
    by number and merged (blank fields backfilled).  A bad file is logged and
    skipped, never raised.
    """
    if isinstance(paths, (str, bytes, os.PathLike)):
        paths = [paths]

    files: list[str] = []
    for p in paths:
        p = os.fspath(p)
        try:
            if os.path.isdir(p):
                files += sorted(
                    os.path.join(p, f) for f in os.listdir(p)
                    if f.lower().endswith((".pdf", ".txt", ".zip", ".csv")))
            else:
                files.append(p)
        except OSError as e:                                    # noqa: BLE001
            log(f"  !! could not list {p}: {e}")

    out: dict[str, SubmittalRecord] = {}
    order: list[str] = []
    for f in files:
        base = os.path.basename(str(f))
        try:
            text, kind = read_document(f)
        except Exception as e:                                 # noqa: BLE001
            log(f"  !! could not read {base}: {e}")
            continue
        try:
            recs = _split_records(text, base)
        except Exception as e:                                 # noqa: BLE001
            log(f"  !! could not parse {base}: {e}")
            continue
        for r in recs:
            key = r.number or f"__anon_{len(order)}"
            if key in out:
                _merge_into(out[key], r)
            else:
                out[key] = r
                order.append(key)
        log(f"  read {base} [{kind}]: {len(recs)} item(s)")

    records = [out[k] for k in order]
    records.sort(key=_sort_key)
    return records


# --------------------------------------------------------------- rendering --

def submittal_log_pdf(records: list[SubmittalRecord], out_path: str,
                      title: str = "SUBMITTAL LOG", log=print) -> dict:
    """Render a submittal log table to ``out_path`` and return the writer dict.

    Columns: No. | Spec Section | Title | Status | Ball in Court, sorted by
    spec section then number.  Delegates to ``transmittal.table_pdf`` (imported
    lazily so this module is usable before the transmittal engine exists).
    """
    try:
        from . import transmittal
    except ImportError as e:                                   # noqa: BLE001
        raise RuntimeError(
            "submittal_log_pdf requires rfi_stamper.transmittal (transmittal.py), "
            "which is not importable in this build. Add the transmittal module to "
            "render the log PDF; parsing does not need it."
        ) from e

    headers = ["No.", "Spec Section", "Title", "Status", "Ball in Court"]
    ordered = sorted(records, key=_sort_key)
    rows = [[r.number, r.spec_section, r.title, r.status, r.ball_in_court]
            for r in ordered]
    subtitle = f"{len(rows)} item(s)"
    return transmittal.table_pdf(
        out_path, headers, rows, title=title, subtitle=subtitle, log=log)
