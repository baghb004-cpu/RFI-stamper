"""The Selvage: the wire formats — LandXML CgPoints, GSI-8/16 fieldbook,
SP-record fieldbook, and the DXF attribute-block tier.

Everything a crew's tablet or an office suite ingests beyond the PNEZD CSV
that :mod:`rfi_stamper.fieldstitch` already writes.  Every exchange format
is a LOSSY wire — the job JSON sidecar stays the only lossless format, and
no importer ever syncs state *from* a wire file over richer local state
(imports go through :func:`fieldstitch.apply_import_rows`, which quarantines
collisions and never overwrites CONTROL).

Wire discipline (every writer): **ASCII only, CRLF line endings, no BOM**
(a BOM makes the first point id read as garbage; LF-only renders as one
endless line on older handhelds), atomic writes.  Readers strip BOMs and
normalize endings.

Coordinate ordering is centralized in :data:`WRITER_ORDER` — three formats
use three different orders (CSV/XML are Northing-first, the GSI fieldbook is
Easting-first, DXF group 10 is X = Easting) and inlining that per exporter
is exactly how N/E swaps ship.  Every exporter calls :func:`ordered`.

Fully offline; stdlib only.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from . import __version__
from .fieldstitch import (
    LayoutJob,
    PointLayer,
    _export_points,
    _atomic_bytes,
    apply_import_rows,
    lint_witness_offsets,
    validate_label,
)

# ------------------------------------------------- the one coordinate table --

#: THE writer table (brief section 3 intro): coordinate order per wire
#: format, in one place.  "csv" is PNEZD (N first; the PENZD swap is an
#: explicit profile in fieldstitch, never a table entry), "landxml" is the
#: N E [Z] text inside a CgPoint element, "gsi" is the fieldbook word order
#: (WI 81 = Easting FIRST, 82 = Northing, 83 = Elevation — the reverse of
#: PNEZD), "sp" is the SP record (N then E), "dxf" is drawing axes
#: (group 10 = X = Easting, 20 = Y = Northing, 30 = Z).
WRITER_ORDER = {
    "csv": ("n", "e", "z"),
    "landxml": ("n", "e", "z"),
    "gsi": ("e", "n", "z"),
    "sp": ("n", "e", "z"),
    "dxf": ("e", "n", "z"),
    # the PNEZD-order spreadsheet the robotic-total-station office
    # suites import directly (the grid-tablet XLSX stays E-first)
    "xlsx_pnezd": ("n", "e", "z"),
}


def ordered(fmt: str, n, e, z=None) -> tuple:
    """(n, e, z) -> the tuple in ``fmt``'s wire order.  Every exporter in
    this module goes through here — never inline coordinate order."""
    try:
        order = WRITER_ORDER[fmt]
    except KeyError:
        raise ValueError(f"unknown wire format {fmt!r}; expected one of "
                         f"{sorted(WRITER_ORDER)}") from None
    vals = {"n": n, "e": e, "z": z}
    return tuple(vals[k] for k in order)


#: Job units that mean feet (the job may say WHICH foot; both write the
#: same wire digit where the format cannot tell them apart).
_FEET_UNITS = ("ft", "ift", "usft")


def _is_feet(units) -> bool:
    return str(units or "").lower() in _FEET_UNITS


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------ shared export guard --

def _wire_points(job: LayoutJob, points):
    """Common export gate: visible points (or the explicit list), duplicate
    composed ids refused loudly, every label collector-validated, witness
    offsets linted (mixed side/distance within a layer refuses the export).
    Returns ``(points, labels)``."""
    pts = _export_points(job, points)
    labels = [job.composed(p) for p in pts]
    seen: dict[str, int] = {}
    for lb in labels:
        seen[lb] = seen.get(lb, 0) + 1
    dupes = sorted(lb for lb, c in seen.items() if c > 1)
    if dupes:
        raise ValueError("duplicate point id(s) in export: "
                         + ", ".join(dupes))
    for lb in labels:
        validate_label(lb)
    problems = lint_witness_offsets(job, pts)
    if problems:
        raise ValueError("witness offsets disagree — refusing export: "
                         + " | ".join(problems))
    return pts, labels


def _desc(p) -> str:
    return p.desc or p.category or p.layer


def _write_wire(text: str, out_path: str) -> None:
    """ASCII, CRLF already embedded, no BOM, atomic."""
    _atomic_bytes(text.encode("ascii", errors="replace"), out_path)


# =================================================== LandXML 1.2 CgPoints ===

LANDXML_NS = "http://www.landxml.org/schema/LandXML-1.2"

#: ``linearUnit`` per job unit — 'foot' vs the survey-foot spelling some
#: writers use is exactly how this format tells the two feet apart.
_LANDXML_LINEAR = {"ft": "foot", "ift": "foot", "usft": "USSurveyFoot",
                   "m": "meter"}

#: kind <-> state mapping (state enum: existing/proposed/abandoned/
#: destroyed).  Design layout goes out "proposed"; control goes out
#: "existing"; both directions live here.
_STATE_FOR_KIND = {"CONTROL": "existing"}
_KIND_FOR_STATE = {"existing": "CONTROL"}


def export_landxml(job: LayoutJob, out_path: str, points=None) -> int:
    """Minimal valid LandXML 1.2 CgPoints document (brief section 3.3).

    Coordinate text is ``NORTHING EASTING [ELEVATION]``, space-separated,
    INSIDE the element — never attributes, never reordered; the third token
    is omitted for 2D points.  ``state="proposed"`` for design layout,
    ``"existing"`` for control.  Units element is Imperial for feet jobs
    (``linearUnit`` says which foot) and Metric for meter jobs.  ASCII,
    CRLF, no BOM, atomic.  Returns the point count."""
    pts, labels = _wire_points(job, points)
    now = _now_utc()
    linear = _LANDXML_LINEAR.get(str(job.units).lower(), "foot")
    if _is_feet(job.units):
        units = (f'    <Imperial areaUnit="squareFoot" linearUnit="{linear}"'
                 ' volumeUnit="cubicFeet" temperatureUnit="fahrenheit"'
                 ' pressureUnit="inHG" angularUnit="decimal degrees"'
                 ' directionUnit="decimal degrees"/>')
    else:
        units = ('    <Metric areaUnit="squareMeter" linearUnit="meter"'
                 ' volumeUnit="cubicMeter" temperatureUnit="celsius"'
                 ' pressureUnit="milliBars" angularUnit="decimal degrees"'
                 ' directionUnit="decimal degrees"/>')

    def attr(v):
        return escape(str(v), {'"': "&quot;"})

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<LandXML xmlns="{LANDXML_NS}" version="1.2"'
        f' date="{now:%Y-%m-%d}" time="{now:%H:%M:%S}"'
        ' readOnly="false" language="English">',
        "  <Units>",
        units,
        "  </Units>",
        f'  <Application name="Planloom Fieldstitch"'
        f' version="{attr(__version__)}"/>',
        "  <CgPoints>",
    ]
    for p, lb in zip(pts, labels):
        n, e, z = job.to_world(p)
        a, b, c = ordered("landxml", n, e, z)
        text = f"{a:.4f} {b:.4f}" + ("" if c is None else f" {c:.3f}")
        state = _STATE_FOR_KIND.get(p.kind, "proposed")
        code = f' code="{attr(p.code)}"' if p.code else ""
        lines.append(
            f'    <CgPoint name="{attr(lb)}"{code}'
            f' desc="{attr(_desc(p))}" state="{state}">{text}</CgPoint>')
    lines += ["  </CgPoints>", "</LandXML>"]
    _write_wire("\r\n".join(lines) + "\r\n", out_path)
    return len(pts)


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def read_landxml(path: str) -> dict:
    """Namespace-agnostic CgPoint reader — any LandXML version, any (or no)
    namespace.  Reads name/code/desc/state attributes and splits the element
    text on whitespace as N E [Z].  Returns::

        {"rows": [{"id","n","e","z","desc","code","kind"}...],
         "bad": [(name, text)...], "units": "ft"|"usft"|"m"|"",
         "version": "..."}
    """
    tree = ElementTree.parse(path)
    root = tree.getroot()
    version = str(root.get("version", ""))
    units = ""
    rows, bad = [], []
    for el in root.iter():
        name = _localname(el.tag)
        if name == "Imperial":
            units = ("usft" if "survey" in str(el.get("linearUnit", ""
                                                      )).lower() else "ft")
        elif name == "Metric":
            units = "m"
        elif name == "CgPoint":
            pid = str(el.get("name", ""))
            toks = (el.text or "").split()
            try:
                a, b = float(toks[0]), float(toks[1])
                z = float(toks[2]) if len(toks) > 2 else None
            except (IndexError, ValueError):
                bad.append((pid, el.text or ""))
                continue
            # text is N-first per the writer table (never reordered)
            state = str(el.get("state", "proposed")).lower()
            rows.append({
                "id": pid, "n": a, "e": b, "z": z,
                "desc": str(el.get("desc", "")),
                "code": str(el.get("code", "")),
                "kind": _KIND_FOR_STATE.get(state, "DESIGN"),
            })
    return {"rows": rows, "bad": bad, "units": units, "version": version}


def import_landxml(job: LayoutJob, path: str, log=print, *,
                   on_collision: str = "quarantine") -> int:
    """Import CgPoints into the job (page coords via the inverse frame
    transform, same as the CSV importer).  ``state="existing"`` points land
    as CONTROL (locked).  Returns rows applied."""
    data = read_landxml(path)
    for pid, text in data["bad"]:
        log(f"  !! CgPoint {pid!r}: bad coordinate text {text!r}, skipped")
    return apply_import_rows(job, data["rows"], log,
                             on_collision=on_collision)


# ================================================= GSI-8 / GSI-16 fieldbook ==

#: Position-6 units digit -> unit-of-last-digit factor (distance digits
#: only; 2-5 are angle codes this exporter never writes).
GSI_UNIT_FACTORS = {"0": 0.001, "1": 0.001, "6": 0.0001, "7": 0.0001,
                    "8": 0.00001}
#: Which distance digits mean feet / meters (for the reader's unit report).
_GSI_FEET_DIGITS = ("1", "7")
_GSI_METER_DIGITS = ("0", "6", "8")

#: WI codes: 11 point id; 81/82/83 target E/N/Z; 84/85/86 station E/N/Z
#: (imported as CONTROL).
_GSI_E, _GSI_N, _GSI_Z = "81", "82", "83"
_GSI_STN = {"84": "e", "85": "n", "86": "z"}


def _gsi_data(value: float, factor: float) -> tuple[str, int]:
    """(sign, unsigned data int) for a coordinate value."""
    i = int(round(abs(float(value)) / factor))
    return ("-" if value < 0 else "+", i)


def export_gsi(job: LayoutJob, out_path: str, points=None) -> int:
    """GSI fieldbook (brief section 3.4) — exact character map.

    GSI-8 word = 16 chars: pos 1-2 WI, 3-6 info, 7 sign, 8-15 data
    (zero-padded, right-justified), 16 blank.  One line (block) per point:
    WI 11 (id; info = 4-digit line sequence from 0001) then 81/82/83 =
    **E, N, Z — the reverse of PNEZD** (via the writer table); the 83 word
    is omitted for a null Z.  Coordinate info is ``..1u``: pos 5 input mode
    1 (keyboard), pos 6 units digit — 1 for feet/0.001 ft, 0 for meter/mm,
    from ``job.units``.

    Overflow rule: 8 data digits max out at 99,999.999 — if ANY coordinate
    integer exceeds 8 digits or any id exceeds 8 chars, the WHOLE file
    switches to GSI-16 (``*`` line prefix, 16-char data) — widths are never
    mixed.  Returns the point count."""
    pts, labels = _wire_points(job, points)
    feet = _is_feet(job.units)
    digit = "1" if feet else "0"
    factor = GSI_UNIT_FACTORS[digit]

    blocks = []          # (label, [(wi, sign, data_int_or_str)...])
    need16 = False
    for p, lb in zip(pts, labels):
        n, e, z = job.to_world(p)
        if len(lb) > 8:
            need16 = True
        words = []
        for wi, val in zip((_GSI_E, _GSI_N, _GSI_Z),
                           ordered("gsi", n, e, z)):
            if val is None:
                continue                      # omit the 83 word for null Z
            sign, data = _gsi_data(val, factor)
            if len(str(data)) > 8:
                need16 = True
            words.append((wi, sign, data))
        blocks.append((lb, words))

    width = 16 if need16 else 8
    lines = []
    for seq, (lb, words) in enumerate(blocks, start=1):
        if len(lb) > width:
            raise ValueError(f"point id {lb!r} is {len(lb)} chars; GSI-16 "
                             f"ids are capped at {width}")
        parts = [f"11{seq:04d}+{lb.rjust(width, '0')} "]
        for wi, sign, data in words:
            ds = str(data)
            if len(ds) > width:
                raise ValueError(
                    f"coordinate for point {lb!r} needs {len(ds)} data "
                    f"digits — beyond even GSI-16")
            parts.append(f"{wi}..1{digit}{sign}{ds.rjust(width, '0')} ")
        prefix = "*" if width == 16 else ""
        lines.append(prefix + "".join(parts))
    _write_wire("\r\n".join(lines) + "\r\n", out_path)
    return len(pts)


def read_gsi(path: str) -> dict:
    """Tolerant GSI reader, both widths (a ``*`` prefix selects 24-char
    words / 16-char data slices).  Ids strip leading zeros; coordinate data
    is multiplied by the position-6 unit factor; sign at position 7;
    84/85/86 station words import as CONTROL.  Returns::

        {"rows": [{"id","n","e","z","desc","kind"}...],
         "bad": [(lineno, line)...], "unit": "ft"|"m"|""}
    """
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        raw = f.read()
    rows, bad = [], []
    unit = ""
    for lineno, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        if line.startswith("*"):
            body, wlen = line[1:], 24
        else:
            body, wlen = line, 16
        rec = {"id": "", "n": None, "e": None, "z": None, "desc": "",
               "kind": "DESIGN"}
        ok = False
        for i in range(0, len(body), wlen):
            w = body[i:i + wlen]
            if len(w) < 8:
                continue
            wi = w[:2]
            sign = w[6]
            data = w[7:wlen - 1] if len(w) >= wlen else w[7:]
            data = data.strip()
            if wi == "11":
                rec["id"] = data.lstrip("0") or "0"
                ok = True
            elif wi in (_GSI_E, _GSI_N, _GSI_Z) or wi in _GSI_STN:
                digit = w[5]
                factor = GSI_UNIT_FACTORS.get(digit)
                if factor is None:
                    continue                       # angle word etc.
                if digit in _GSI_FEET_DIGITS:
                    unit = unit or "ft"
                elif digit in _GSI_METER_DIGITS:
                    unit = unit or "m"
                try:
                    val = int(data) * factor
                except ValueError:
                    bad.append((lineno, line))
                    ok = False
                    break
                if sign == "-":
                    val = -val
                if wi in _GSI_STN:
                    rec[_GSI_STN[wi]] = val
                    rec["kind"] = "CONTROL"
                elif wi == _GSI_E:
                    rec["e"] = val
                elif wi == _GSI_N:
                    rec["n"] = val
                else:
                    rec["z"] = val
                ok = True
        if not ok:
            continue
        if rec["n"] is None or rec["e"] is None:
            bad.append((lineno, line))
            continue
        rows.append(rec)
    return {"rows": rows, "bad": bad, "unit": unit}


def import_gsi(job: LayoutJob, path: str, log=print, *,
               on_collision: str = "quarantine") -> int:
    """Import a GSI fieldbook.  Station blocks (WI 84/85/86) land as
    CONTROL.  Returns rows applied."""
    data = read_gsi(path)
    for lineno, line in data["bad"]:
        log(f"  !! GSI line {lineno}: unparseable block {line!r}, skipped")
    return apply_import_rows(job, data["rows"], log,
                             on_collision=on_collision)


# ================================================== SP-record fieldbook ====

#: MO-record UN code per job unit — this format encodes WHICH foot:
#: UN0 = US survey feet, UN1 = meters, UN2 = international feet.
SP_UNIT_CODES = {"usft": "0", "m": "1", "ift": "2", "ft": "2"}
_SP_UNITS_BACK = {"0": "usft", "1": "m", "2": "ift"}

#: Observation record types the importer must IGNORE — this is a raw
#: observation log; re-reducing already-reduced data double-applies
#: corrections.  Only SP (Store Point) lines are read.
SP_OBSERVATION_RECORDS = ("OC", "BK", "SS", "TR", "GPS", "LS", "BD", "BR")

_SP_NUM = re.compile(r"^[A-Z]{1,2}\s*(-?[0-9.]+)$")


def export_sp(job: LayoutJob, out_path: str, points=None, *,
              name: str = "", log=print,
              include_null_z: bool = False) -> int:
    """Comma-separated SP-record fieldbook (brief section 3.5).

    ``JB``/``MO`` header records (UN0|UN1|UN2 from job units — US survey /
    meters / international feet; SF carries the job CSF), then one
    ``SP,PN<id>,N <n>,E <e>,EL<z>,--<desc>`` per point — the single space
    after ``N`` and ``E`` is part of the grammar; coordinates 4 decimals,
    EL 3; the description rides after ``--`` as the final field (commas
    legal there).  Points with no elevation are EXCLUDED with a count
    warning unless ``include_null_z=True`` writes them as EL0.000 (explicit
    opt-in — a null staked as 0.00 sets sleeves at datum zero).  Returns
    the count written."""
    pts, labels = _wire_points(job, points)
    un = SP_UNIT_CODES.get(str(job.units).lower(), "2")
    sf = float(getattr(job, "csf", 1.0) or 1.0)
    now = _now_utc()
    stem = name or os.path.splitext(os.path.basename(out_path))[0]
    lines = [
        f"JB,NM{stem},DT{now:%m-%d-%Y},TM{now:%H:%M:%S}",
        f"MO,AD0,UN{un},SF{sf:.8f},EC0,EO0.0",
    ]
    written = skipped = 0
    for p, lb in zip(pts, labels):
        n, e, z = job.to_world(p)
        if z is None:
            if not include_null_z:
                skipped += 1
                continue
            z = 0.0
        a, b, c = ordered("sp", n, e, z)
        lines.append(f"SP,PN{lb},N {a:.4f},E {b:.4f},EL{c:.3f},"
                     f"--{_desc(p)}")
        written += 1
    if skipped:
        log(f"  !! {skipped} point(s) with no elevation excluded from the "
            "SP fieldbook (pass include_null_z=True to write EL0.000 — "
            "explicit opt-in only)")
    _write_wire("\r\n".join(lines) + "\r\n", out_path)
    return written


def read_sp(path: str) -> dict:
    """Tolerant SP-record reader: scans ONLY ``SP,`` lines; every
    observation record type (:data:`SP_OBSERVATION_RECORDS`), comment
    (``--``) and header line is skipped.  PN/N/E/EL parse with or without
    the space after the tag; the description is everything after ``,--``.
    Returns::

        {"rows": [{"id","n","e","z","desc"}...], "bad": [(lineno, line)...],
         "units": "usft"|"m"|"ift"|"", "job": "...", "sf": float|None}
    """
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        raw = f.read()
    rows, bad = [], []
    units, jobname, sf = "", "", None
    for lineno, line in enumerate(raw.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("--"):
            continue
        rectype = s.split(",", 1)[0].strip().upper()
        if rectype == "JB":
            for tok in s.split(","):
                if tok.strip().upper().startswith("NM"):
                    jobname = tok.strip()[2:]
            continue
        if rectype == "MO":
            for tok in s.split(","):
                t = tok.strip().upper()
                if t.startswith("UN"):
                    units = _SP_UNITS_BACK.get(t[2:3], "")
                elif t.startswith("SF"):
                    try:
                        sf = float(tok.strip()[2:])
                    except ValueError:
                        pass
            continue
        if rectype != "SP":
            continue                    # OC/BK/SS/TR/GPS/LS/BD/BR/anything
        head, sep, desc = s.partition(",--")
        rec = {"id": "", "n": None, "e": None, "z": None,
               "desc": desc if sep else ""}
        for tok in head.split(",")[1:]:
            t = tok.strip()
            up = t.upper()
            if up.startswith("PN"):
                rec["id"] = t[2:].strip()
                continue
            # tolerate 'N 5000.1250' and 'N5000.1250' alike (\s* in the re)
            m = _SP_NUM.match(up)
            if not m:
                continue
            try:
                val = float(m.group(1))
            except ValueError:
                continue
            # EL before E — 'EL100.250' also startswith('E')
            if up.startswith("EL"):
                rec["z"] = val
            elif up.startswith("N"):
                rec["n"] = val
            elif up.startswith("E"):
                rec["e"] = val
        if rec["n"] is None or rec["e"] is None:
            bad.append((lineno, line))
            continue
        rows.append(rec)
    return {"rows": rows, "bad": bad, "units": units, "job": jobname,
            "sf": sf}


def import_sp(job: LayoutJob, path: str, log=print, *,
              on_collision: str = "quarantine") -> int:
    """Import Store Point records; all observation records are ignored
    (never re-reduce reduced data).  Returns rows applied."""
    data = read_sp(path)
    for lineno, line in data["bad"]:
        log(f"  !! SP line {lineno}: unparseable record {line!r}, skipped")
    return apply_import_rows(job, data["rows"], log,
                             on_collision=on_collision)


# ================================================ DXF attribute-block tier ==

#: CAD layer-name rules (R12 fact): <= 31 chars, uppercase A-Z 0-9 $ - _
#: only.  Enforced at layer CREATION (:func:`add_cad_layer`), not at
#: export — legacy layers are sanitized on the way out instead.
DXF_LAYER_MAX = 31
DXF_LAYER_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789$-_")


def validate_dxf_layer(name: str) -> None:
    """Raise ``ValueError`` unless ``name`` is already a conformant CAD
    layer name (uppercase A-Z 0-9 $ - _, <= 31 chars, non-empty)."""
    s = str(name)
    if not s:
        raise ValueError("CAD layer name is empty")
    if len(s) > DXF_LAYER_MAX:
        raise ValueError(f"CAD layer name {s!r} is {len(s)} chars; the R12 "
                         f"cap is {DXF_LAYER_MAX}")
    bad = sorted({ch for ch in s if ch not in DXF_LAYER_CHARS})
    if bad:
        raise ValueError(
            f"CAD layer name {s!r} carries unsupported character(s) "
            f"{''.join(bad)!r}; allowed: A-Z 0-9 $ - _ (uppercase only; "
            "spaces become '_')")


def dxf_layer_name(name: str) -> str:
    """Sanitize any layer name into a conformant one: uppercase, spaces ->
    ``_``, every other unsupported char -> ``_``, truncated to 31."""
    s = str(name).upper()
    s = "".join(ch if ch in DXF_LAYER_CHARS else "_" for ch in s)
    return (s or "_")[:DXF_LAYER_MAX]


def add_cad_layer(job: LayoutJob, name: str, color: str = "#d84c3f",
                  category: str = "") -> PointLayer:
    """Create a point layer whose name is enforced NOW (the brief's rule:
    at layer creation, not export).  Raises on a non-conformant name."""
    validate_dxf_layer(name)
    layer = PointLayer(str(name), color=color, category=category)
    job.add_layer(layer)
    return layer


_LAYPT = "LAYPT"
_ATT_H = 1.5          # ATTDEF/ATTRIB text height, drawing units


def export_dxf_blocks(job: LayoutJob, out_path: str, points=None) -> int:
    """DXF R12 attribute-block tier (brief section 3.2, office-CAD
    dialect): a BLOCKS section defining ``LAYPT`` (attributes-follow flag
    70=2, crossing-lines marker, three ATTDEFs — PT at (+1,+1), ELEV at
    (+1,-1), DESC at (+1,-3)) and, per point, the plain POINT entity (field
    ecosystems harvest POINT and INSERT origins) plus
    INSERT(66=1)/ATTRIB(PT)/ATTRIB(ELEV)/ATTRIB(DESC)/SEQEND.  Null
    elevation writes ATTRIB text ``-`` (group 30 has no null form and gets
    0).  Layer names are sanitized via :func:`dxf_layer_name` (creation-
    time enforcement is :func:`add_cad_layer`).  Returns the point count."""
    from .fieldstitch import aci_for, _dxf_clean
    pts, labels = _wire_points(job, points)
    layer_colors: dict[str, int] = {}
    for ly in job.layers:
        layer_colors[dxf_layer_name(ly.name)] = aci_for(ly.color)
    for p in pts:
        layer_colors.setdefault(dxf_layer_name(p.layer), 7)

    pairs: list[tuple[int, str]] = [
        (0, "SECTION"), (2, "HEADER"),
        (9, "$ACADVER"), (1, "AC1009"),
        (0, "ENDSEC"),
        (0, "SECTION"), (2, "TABLES"),
        (0, "TABLE"), (2, "LAYER"), (70, str(len(layer_colors))),
    ]
    for lname, color in layer_colors.items():
        pairs += [(0, "LAYER"), (2, lname), (70, "0"),
                  (62, str(color)), (6, "CONTINUOUS")]
    pairs += [(0, "ENDTAB"), (0, "ENDSEC")]

    # ---- BLOCKS: the LAYPT definition --------------------------------
    pairs += [
        (0, "SECTION"), (2, "BLOCKS"),
        (0, "BLOCK"), (8, "0"), (2, _LAYPT), (70, "2"),
        (10, "0.0"), (20, "0.0"), (30, "0.0"),
        # marker: two crossing lines +/-0.75 units
        (0, "LINE"), (8, "0"),
        (10, "-0.75"), (20, "-0.75"), (11, "0.75"), (21, "0.75"),
        (0, "LINE"), (8, "0"),
        (10, "-0.75"), (20, "0.75"), (11, "0.75"), (21, "-0.75"),
    ]
    for tag, prompt, ox, oy in (("PT", "Point number", 1.0, 1.0),
                                ("ELEV", "Elevation", 1.0, -1.0),
                                ("DESC", "Description", 1.0, -3.0)):
        pairs += [(0, "ATTDEF"), (8, "0"),
                  (10, f"{ox:.1f}"), (20, f"{oy:.1f}"), (30, "0.0"),
                  (40, f"{_ATT_H:.2f}"),
                  (1, ""), (3, prompt), (2, tag), (70, "0")]
    pairs += [(0, "ENDBLK"), (0, "ENDSEC")]

    # ---- ENTITIES: POINT + INSERT/ATTRIBx3/SEQEND per point ----------
    pairs += [(0, "SECTION"), (2, "ENTITIES")]
    for p, lb in zip(pts, labels):
        n, e, z = job.to_world(p)
        x, y, zz = ordered("dxf", n, e, z)
        z0 = 0.0 if zz is None else zz
        layer = dxf_layer_name(p.layer)
        pairs += [(0, "POINT"), (8, layer),
                  (10, f"{x:.4f}"), (20, f"{y:.4f}"), (30, f"{z0:.4f}")]
        pairs += [(0, "INSERT"), (66, "1"), (2, _LAYPT), (8, layer),
                  (10, f"{x:.4f}"), (20, f"{y:.4f}"), (30, f"{z0:.4f}")]
        att_vals = ((lb, "PT", 1.0, 1.0),
                    ("-" if zz is None else f"{zz:.3f}", "ELEV", 1.0, -1.0),
                    (_dxf_clean(_desc(p)), "DESC", 1.0, -3.0))
        for value, tag, ox, oy in att_vals:
            pairs += [(0, "ATTRIB"), (8, layer),
                      (10, f"{x + ox:.4f}"), (20, f"{y + oy:.4f}"),
                      (30, f"{z0:.4f}"), (40, f"{_ATT_H:.2f}"),
                      (1, _dxf_clean(value)), (2, tag), (70, "0")]
        pairs += [(0, "SEQEND"), (8, layer)]
    pairs += [(0, "ENDSEC"), (0, "EOF")]
    text = "".join(f"{code}\r\n{value}\r\n" for code, value in pairs)
    _write_wire(text, out_path)
    return len(pts)


def read_dxf_pairs(path: str) -> list:
    """(group code, value) pairs from an ASCII DXF — the reparse helper the
    tests and the import path share."""
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        lines = f.read().splitlines()
    pairs = []
    for i in range(0, len(lines) - 1, 2):
        try:
            code = int(lines[i].strip())
        except ValueError:
            continue
        pairs.append((code, lines[i + 1]))
    return pairs


def read_dxf_points(path: str) -> dict:
    """Harvest points back out of a DXF: INSERT origins with their ATTRIB
    values when present (the block tier), plain POINT entities otherwise.
    Never expects proprietary CAD point objects.  Returns
    ``{"rows": [{"id","n","e","z","desc"}...]}`` (z None when the ELEV
    attribute reads ``-``)."""
    pairs = read_dxf_pairs(path)
    rows = []
    inserts, points = [], []
    ent = None                     # the open INSERT/POINT entity
    att = None                     # the open ATTRIB {tag, val}
    for code, value in pairs:
        if code == 0:
            att = None
            if value == "INSERT":
                ent = {"type": value, "x": None, "y": None, "z": 0.0,
                       "atts": {}}
                inserts.append(ent)
            elif value == "POINT":
                ent = {"type": value, "x": None, "y": None, "z": 0.0,
                       "atts": {}}
                points.append(ent)
            elif value == "ATTRIB" and ent is not None \
                    and ent["type"] == "INSERT":
                att = {"tag": None, "val": None}
            else:
                ent = None            # SEQEND or any other entity closes it
            continue
        if att is not None:
            # ATTRIB 10/20/30 are the text insertion, NOT the point — only
            # the value (1) and tag (2) matter here
            if code == 1:
                att["val"] = value
            elif code == 2:
                att["tag"] = value
            if att["tag"] is not None and att["val"] is not None:
                ent["atts"][att["tag"]] = att["val"]
                att = None
        elif ent is not None:
            if code == 10:
                ent["x"] = float(value)
            elif code == 20:
                ent["y"] = float(value)
            elif code == 30:
                ent["z"] = float(value)
    source = inserts if inserts else points
    for ent in source:
        if ent["x"] is None or ent["y"] is None:
            continue
        atts = ent["atts"]
        elev = atts.get("ELEV", "")
        if elev in ("", "-"):
            z = ent["z"] if not atts else None
        else:
            try:
                z = float(elev)
            except ValueError:
                z = None
        rows.append({"id": atts.get("PT", ""),
                     "n": ent["y"], "e": ent["x"], "z": z,
                     "desc": atts.get("DESC", "")})
    return {"rows": rows}
