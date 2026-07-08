"""Self-contained tests for the Fieldstitch Pro upgrade — the field-grade
point model (rfi_stamper.fieldstitch extensions) and the QA layer
(rfi_stamper.fieldpro).  Plain python, no pytest, no project data.

Exercises:

* backward compat: an old-shape sidecar loads; new fields default; lean
  to_dict (extension keys only when non-default)
* label validation: 16-char hard cap, collector charset, at creation and
  on strict (options=) export
* spools: default ranges, minting, spool-full ValueError, quarantine on
  import collision, mint counter never rewinds, tombstoned numbers
* statuses: ISO-dated transitions, never-downgrade bulk seeding, REJECTED
  re-arm, number locking (CONTROL / STAKED / VERIFIED) in renumber()
* witness points: derived world coords, parent-move recompute, cascade
  delete, auto-description, mixed-offset export lint
* CSV options matrix: PENZD swap, 4 decimals, code column, comment header
  with frame hash, headerless, None-Z empty field, desc comma policy,
  duplicate-id ValueError, .tag.txt sidecar with content checksum
* import: delimiter sniffing, null-Z sentinels, collision policies
  (quarantine default / keep / replace / refuse; CONTROL never overwritten),
  zero-fill-aware matching, advisory validators (swap / range / unit /
  outliers) that modify nothing
* delta math against hand-computed values incl. the brief's example
  (HD = 0.00721... at design (5000,2000,100) staked
  (5000.006,1999.996,100.02)), verdict bands, cut/fill derivation
* as-staked pairing ladder ('1' must NOT match '1001'), frame-hash gate,
  commit statuses (latest governs, VERIFIED never bulk-downgraded)
* check shots + brackets (failed closing check flags the bracket)
* As-Staked Ledger PDF (opens in pypdf, page count matches) + _qa.csv
* walking-route sort: beats identity ordering, order-only (numbers and the
  job's point list never mutate), elevation-band grouping

Run:  python3.12 tests/test_fieldstitch_pro.py
"""
import hashlib
import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper.fieldstitch import (             # noqa: E402
    DEFAULT_SPOOLS, LayoutJob, LayoutPoint, QUARANTINE_LAYER, Spool,
    export_csv_pnezd, frame_hash, import_csv, validate_import_csv,
    validate_label)
from rfi_stamper import fieldpro                  # noqa: E402
from rfi_stamper.fieldpro import (                # noqa: E402
    CheckShot, DEFAULT_TOLERANCES, DeltaRecord, PAINT_COLORS, QAStore,
    QA_CSV_HEADERS, SEED_CODES, ToleranceClass, brackets, check_shot,
    commit_asstaked, compose, cut_fill, deltas, export_ledger_csv,
    job_tolerances, ledger_pdf, pair_asstaked, route_length, route_order,
    set_job_tolerance, tolerance_for, two_state, walk_route)
from rfi_stamper.markups.measure import ScaleCal  # noqa: E402


def expect(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc as e:
        return e
    raise AssertionError(f"expected {exc.__name__} from {fn!r}{args!r}")


def calibrated_job(pdf_path=None):
    """Basepoint at page (100, 700) = world N 5000 / E 2000, 1 pt = 0.1 ft."""
    job = LayoutJob(pdf_path)
    job.base_page_xy = (100.0, 700.0)
    job.base_world = (5000.0, 2000.0)
    job.scale = ScaleCal(real_per_pt=0.1, unit="ft").to_dict()
    return job


# ------------------------------------------------------------ back-compat --

def test_backcompat(tmp):
    # a sidecar exactly as version 1 wrote it — no extension keys anywhere
    old = {
        "version": 1, "units": "ft", "next_num": 2, "pad": 3,
        "prefix": "CP-", "suffix": "", "base_page_xy": [100.0, 700.0],
        "base_world": [5000.0, 2000.0], "rotation_deg": 0.0,
        "scale": {"real_per_pt": 0.1, "unit": "ft"},
        "layers": [{"name": "Layout", "color": "#d84c3f", "visible": True,
                    "locked": False, "category": ""}],
        "points": [{"id": "deadbeef", "num": 1, "prefix": "CP-",
                    "suffix": "", "page": 1, "x": 110.0, "y": 680.0,
                    "elev": 2.5, "desc": "old point", "category": "",
                    "layer": "Layout",
                    "created": "2024-01-01T00:00:00+00:00"}],
    }
    side = os.path.join(tmp, "old.stitch.json")
    with open(side, "w", encoding="utf-8") as f:
        json.dump(old, f)
    job = LayoutJob()
    job.load(side)
    assert len(job.points) == 1 and job.spools == []
    assert job.retired == set() and job.tolerances == {}
    p = job.points[0]
    # every new field defaults
    assert p.kind == "DESIGN" and p.status == "PENDING"
    assert p.z_ref == "FF" and p.tol_class == "" and p.code == ""
    assert p.provenance is None and p.status_log is None
    assert p.ref_num is None and p.parent_uid == "" and not p.locked
    assert p.elev == 2.5 and p.uid == p.id == "deadbeef"
    # lean to_dict: extension keys omitted while at defaults
    d = p.to_dict()
    for k in ("kind", "status", "z_ref", "provenance", "locked",
              "offset_ft", "monument"):
        assert k not in d, (k, d)
    assert set(d) == {"id", "num", "prefix", "suffix", "page", "x", "y",
                      "elev", "desc", "category", "layer", "created"}, d
    # ...and round-trips to an equal point
    assert LayoutPoint.from_dict(d) == p
    # non-default extension values DO persist
    p.kind, p.monument = "CONTROL", "rebar+cap"
    d2 = p.to_dict()
    assert d2["kind"] == "CONTROL" and d2["monument"] == "rebar+cap"
    q = LayoutPoint.from_dict(d2)
    assert q.kind == "CONTROL" and q.monument == "rebar+cap"
    # None elevation survives a dict round-trip (never conflated with 0.0)
    p.elev = None
    assert LayoutPoint.from_dict(p.to_dict()).elev is None


# ----------------------------------------------------------------- labels --

def test_labels():
    validate_label("CP-001-S")                    # fine
    validate_label("A1.B_2-3")                    # full charset
    e = expect(ValueError, validate_label, "X" * 17)
    assert "16" in str(e), e
    e = expect(ValueError, validate_label, "cp-001")
    assert "cp-001" in str(e), e
    expect(ValueError, validate_label, "CP 001")  # space
    expect(ValueError, validate_label, "=CMD001")
    # enforced at creation through add_point (prefix + zfill + suffix)
    job = LayoutJob()
    job.prefix = "LONGPREFIX-"                    # 11 chars + 3 pad + 3 suf
    job.suffix = "-SS"
    e = expect(ValueError, job.add_point, 1, 0.0, 0.0)
    assert "hard-capped" in str(e) or "16" in str(e), e
    job.prefix, job.suffix = "ab-", ""
    expect(ValueError, job.add_point, 1, 0.0, 0.0)
    job.prefix = "AB-"
    p = job.add_point(1, 0.0, 0.0)
    assert job.composed(p) == "AB-001"


# ----------------------------------------------------------------- spools --

def test_spools(tmp):
    job = calibrated_job()
    assert job.add_default_spools() == len(DEFAULT_SPOOLS) == 13
    assert job.spool("Steel").start == 2000 and job.spool("Steel").end == 2999
    s1 = job.add_point(1, 110.0, 680.0, layer="Steel")
    s2 = job.add_point(1, 120.0, 680.0, layer="Steel")
    assert (s1.num, s2.num) == (2000, 2001)
    c1 = job.add_point(1, 130.0, 680.0, layer="Control", kind="CONTROL")
    assert c1.num == 1 and c1.locked            # CONTROL defaults locked
    # spool ranges may not overlap; duplicate layer refused
    expect(ValueError, job.add_spool, "Steel", 50000, 50001)
    expect(ValueError, job.add_spool, "Odd", 2500, 2600)

    # spool full -> ValueError naming the layer
    tiny = LayoutJob()
    tiny.add_spool("Tiny", 10, 11)
    t1 = tiny.add_point(1, 0.0, 0.0, layer="Tiny")
    t2 = tiny.add_point(1, 1.0, 1.0, layer="Tiny")
    assert (t1.num, t2.num) == (10, 11)
    e = expect(ValueError, tiny.add_point, 1, 2.0, 2.0, layer="Tiny")
    assert "spool full" in str(e) and "Tiny" in str(e), e

    # tombstones: deleted numbers retire and are never re-minted
    wide = LayoutJob()
    wide.add_spool("W", 10, 13)
    w1 = wide.add_point(1, 0.0, 0.0, layer="W")     # 10
    assert wide.remove(w1.id) == 1 and 10 in wide.retired
    w2 = wide.add_point(1, 1.0, 1.0, layer="W")
    assert w2.num == 11, w2.num                     # not 10
    wide.spool("W").next = 10                       # simulate a rewind attempt
    w3 = wide.add_point(1, 2.0, 2.0, layer="W")
    assert w3.num == 12, w3.num                     # skips retired 10, live 11
    # explicit reuse of a retired or live number refuses
    e = expect(ValueError, wide.add_point, 1, 3.0, 3.0, num=10)
    assert "retired" in str(e), e
    e = expect(ValueError, wide.add_point, 1, 3.0, 3.0, num=11)
    assert "in use" in str(e), e
    # spools + retired survive the sidecar round-trip
    side = os.path.join(tmp, "spool.stitch.json")
    wide.save(side)
    back = LayoutJob()
    back.load(side)
    assert back.retired == {10}
    assert back.spool("W") == Spool("W", 10, 13, next=13)


# --------------------------------------------------------------- statuses --

def test_statuses():
    job = calibrated_job()
    a = job.add_point(1, 110.0, 680.0)
    b = job.add_point(1, 120.0, 680.0)
    c = job.add_point(1, 130.0, 680.0, kind="CONTROL", num=90)
    assert a.status == "PENDING" and a.status_log is None
    job.set_status(a.id, "STAKED", note="crew 1", by="JD")
    assert a.status == "STAKED" and len(a.status_log) == 1
    entry = a.status_log[0]
    assert entry["status"] == "STAKED" and entry["by"] == "JD"
    assert "T" in entry["ts"] and entry["ts"].endswith("+00:00"), entry
    assert a.staked_by == "JD" and a.staked_at == entry["ts"]
    expect(ValueError, job.set_status, a.id, "painted")
    expect(ValueError, job.set_status, "no-such-uid", "STAKED")

    # bulk seeding never downgrades; direct set re-arms REJECTED
    job.set_status(b.id, "VERIFIED")
    assert job.seed_statuses({b.id: "STAKED"}) == 0     # would downgrade
    assert b.status == "VERIFIED"
    assert job.seed_statuses({a.id: "VERIFIED", b.id: "VERIFIED"}) == 1
    assert a.status == "VERIFIED" and len(a.status_log) == 2
    job.set_status(a.id, "REJECTED")
    assert job.seed_statuses({a.id: "STAKED"}) == 1     # re-arm is an upgrade
    job.set_status(a.id, "PENDING")                     # direct re-arm allowed
    assert a.status == "PENDING"
    # seeding accepts numbers (zero-fill-aware) as keys
    assert job.seed_statuses({str(a.num).zfill(3): "STAKED"}) == 1
    assert a.status == "STAKED"

    # renumber skips CONTROL and number-locked points, reports both counts
    d = job.add_point(1, 140.0, 680.0)                  # movable
    before_c, before_a, before_b = c.num, a.num, b.num
    rep = job.renumber(start=500)
    assert rep == {"locked": 3, "reflowed": 1}, rep     # c CONTROL, a+b status
    assert (c.num, a.num, b.num) == (before_c, before_a, before_b)
    assert d.num == 500, d.num


def test_renumber_spooled():
    job = calibrated_job()
    job.add_spool("Steel", 2000, 2010)
    s1 = job.add_point(1, 10.0, 10.0, layer="Steel")    # 2000
    s2 = job.add_point(1, 20.0, 20.0, layer="Steel")    # 2001
    p = job.add_point(1, 30.0, 30.0)                    # Layout
    assert job.remove(s1.id) == 1 and 2000 in job.retired
    rep = job.renumber(start=50)
    assert rep == {"locked": 0, "reflowed": 2}, rep
    assert s2.num == 2001, s2.num       # re-flowed within its spool, 2000 dead
    assert p.num == 50
    assert job.spool("Steel").next >= 2002     # mint counter never rewinds


# ---------------------------------------------------------------- witness --

def test_witness():
    job = calibrated_job()
    job.prefix = "CP-"
    parent = job.add_point(1, 110.0, 680.0, elev=12.5)   # world (5002, 2001)
    w = job.add_witness(parent, offset_ft=2.0, offset_azimuth=0.0)
    assert w.parent_uid == parent.id and w.num == parent.num
    assert job.composed(w) == "CP-001W"
    assert w.desc == "W 2FT N OF CP-001", w.desc
    n, e, z = job.to_world(w)
    assert abs(n - 5004.0) < 1e-9 and abs(e - 2001.0) < 1e-9, (n, e)
    assert z == 12.5
    # east witness
    w2 = job.add_witness(parent.id, offset_ft=5.0, offset_azimuth=90.0)
    n2, e2, _ = job.to_world(w2)
    assert abs(n2 - 5002.0) < 1e-9 and abs(e2 - 2006.0) < 1e-9, (n2, e2)
    assert "5FT E OF" in w2.desc, w2.desc
    # host-parametric: moving the parent carries the witness
    parent.x += 10.0                                    # +1 ft East
    n3, e3, _ = job.to_world(w)
    assert abs(n3 - 5004.0) < 1e-9 and abs(e3 - 2002.0) < 1e-9, (n3, e3)
    # renumber: witness follows the parent's number
    job.renumber(start=7)
    assert parent.num == 7 and w.num == 7 and w2.num == 7
    # witnesses cannot host witnesses
    expect(ValueError, job.add_witness, w.id)
    expect(ValueError, job.add_witness, parent, offset_ft=0.0)
    # cascade delete: parent takes both witnesses with it
    assert job.remove(parent.id) == 3
    assert job.points == []


def test_witness_lint(tmp):
    job = calibrated_job()
    p1 = job.add_point(1, 110.0, 680.0)
    p2 = job.add_point(1, 150.0, 640.0)
    job.add_witness(p1, offset_ft=2.0, offset_azimuth=0.0)
    out = os.path.join(tmp, "wit.csv")
    assert export_csv_pnezd(job, out) == 3               # consistent: exports
    job.add_witness(p2, offset_ft=5.0, offset_azimuth=0.0)  # mixed distance!
    e = expect(ValueError, export_csv_pnezd, job, out)
    assert "witness" in str(e).lower(), e
    # same distance, other side is just as wrong
    job2 = calibrated_job()
    q1 = job2.add_point(1, 110.0, 680.0)
    q2 = job2.add_point(1, 150.0, 640.0)
    job2.add_witness(q1, offset_ft=2.0, offset_azimuth=0.0)
    job2.add_witness(q2, offset_ft=2.0, offset_azimuth=180.0)
    expect(ValueError, export_csv_pnezd, job2,
           os.path.join(tmp, "wit2.csv"))


# ------------------------------------------------------------ CSV options --

def _read_lines(path):
    with open(path, "rb") as f:
        raw = f.read()
    assert not raw.startswith(b"\xef\xbb\xbf"), "BOM in wire format"
    assert b"\r\n" in raw, "wire format must be CRLF"
    return raw, raw.decode("ascii").split("\r\n")


def test_csv_options(tmp):
    job = calibrated_job()
    job.prefix = "CP-"
    p1 = job.add_point(1, 110.0, 680.0, elev=12.5, desc="anchor, row A",
                       code="AB")
    p2 = job.add_point(1, 130.0, 720.0, elev=None)       # no elevation
    out = os.path.join(tmp, "opts.csv")

    # defaults reproduce the classic export (header, PNEZD, 3 decimals)
    assert export_csv_pnezd(job, out) == 2
    _, lines = _read_lines(out)
    assert lines[0].startswith("Point,Northing,Easting"), lines[0]
    assert lines[1].split(",")[1] == "5002.000", lines[1]

    # PENZD swap + 4 decimals + code column + headerless + comment header
    opts = {"order": "PENZD", "decimals": 4, "include_code": True,
            "header": False, "comment_header": True,
            "desc_commas": "semicolon"}
    assert export_csv_pnezd(job, out, options=opts) == 2
    _, lines = _read_lines(out)
    comments = [ln for ln in lines if ln.startswith("#")]
    assert comments and any("frame: " + frame_hash(job) in ln
                            for ln in comments), comments
    assert any("units: ft" in ln for ln in comments)
    assert any("order: PENZD" in ln for ln in comments)
    data = [ln for ln in lines if ln and not ln.startswith("#")]
    r1 = data[0].split(",")
    assert r1[0] == "CP-001"
    assert r1[1] == "2001.0000" and r1[2] == "5002.0000", r1   # E before N
    assert r1[3] == "12.500" and r1[4] == "AB", r1             # Z then Code
    assert r1[5] == "anchor; row A", r1                        # comma policy
    r2 = data[1].split(",")
    assert r2[3] == "", r2                       # None elevation = EMPTY field
    assert export_csv_pnezd(job, out, options={"z_decimals": 2})
    _, lines = _read_lines(out)
    assert lines[1].split(",")[3] == "12.50", lines[1]
    expect(ValueError, export_csv_pnezd, job, out, options={"decimals": 5})
    expect(ValueError, export_csv_pnezd, job, out,
           options={"order": "NEZDP"})
    expect(ValueError, export_csv_pnezd, job, out,
           options={"desc_commas": "shrug"})

    # duplicate exported ids refuse loudly, listing the dupes
    dup = calibrated_job()
    dup.points.append(LayoutPoint.new(num=1, prefix="D-", page=1,
                                      x=110.0, y=680.0))
    dup.points.append(LayoutPoint.new(num=1, prefix="D-", page=1,
                                      x=120.0, y=690.0))
    e = expect(ValueError, export_csv_pnezd, dup, out)
    assert "duplicate" in str(e) and "D-001" in str(e), e

    # strict label validation on the options path only (legacy guard stays)
    host = calibrated_job()
    host.points.append(LayoutPoint.new(num=1, prefix="=cmd", page=1,
                                       x=110.0, y=680.0))
    assert export_csv_pnezd(host, out) == 1              # legacy: guarded, ok
    expect(ValueError, export_csv_pnezd, host, out, options={})

    # .tag.txt sidecar: metadata + 6-hex content checksum
    tag_csv = os.path.join(tmp, "crew.csv")
    assert export_csv_pnezd(job, tag_csv,
                            options={"header": False,
                                     "tag_sidecar": True}) == 2
    tag_path = os.path.join(tmp, "crew.tag.txt")
    assert os.path.isfile(tag_path), os.listdir(tmp)
    with open(tag_csv, "rb") as f:
        csv_bytes = f.read()
    raw, tag_lines = _read_lines(tag_path)
    tag = dict(ln.split(": ", 1) for ln in tag_lines if ": " in ln)
    assert tag["checksum"] == hashlib.sha256(csv_bytes).hexdigest()[:6]
    assert tag["count"] == "2" and tag["units"] == "ft"
    assert tag["frame"] == frame_hash(job)
    assert tag["basepoint"].startswith("N 5000.0000 E 2000.0000")
    assert "min" in tag and "max" in tag
    assert not os.path.exists(tag_path + ".part")


# ----------------------------------------------------------------- import --

def test_import(tmp):
    # delimiter sniffing (semicolon) + null-Z sentinels
    f1 = os.path.join(tmp, "shots.txt")
    with open(f1, "w", newline="") as f:
        f.write("7;5002.000;2001.000;3.500;hanger\r\n")
        f.write("8;5002.000;2001.500;?;no z\r\n")
        f.write("9;5002.000;2002.000;-99999;sentinel\r\n")
        f.write("10;5002.000;2002.500;NULL;sentinel\r\n")
        f.write("11;5002.000;2003.000;9999.999;sentinel\r\n")
        f.write("12;5002.000;2003.500;;empty\r\n")
    job = calibrated_job()
    assert import_csv(job, f1, log=lambda m: None) == 6
    by = {p.num: p for p in job.points}
    assert by[7].elev == 3.5
    for num in (8, 9, 10, 11, 12):
        assert by[num].elev is None, (num, by[num].elev)

    # explicit zero-as-null toggle
    f2 = os.path.join(tmp, "zero.csv")
    with open(f2, "w", newline="") as f:
        f.write("20,5002.000,2004.000,0.000,slab\r\n")
    job2 = calibrated_job()
    import_csv(job2, f2, log=lambda m: None)
    assert job2.points[0].elev == 0.0                    # default: 0 is real
    job3 = calibrated_job()
    import_csv(job3, f2, log=lambda m: None, zero_elev_is_null=True)
    assert job3.points[0].elev is None

    # collisions: quarantine (default) — zero-fill-aware ('007' == 7)
    coll = os.path.join(tmp, "coll.csv")
    with open(coll, "w", newline="") as f:
        f.write("007,4900.000,1900.000,1.000,dupe\r\n")
    logged = []
    n_before = by[7].num, by[7].x, by[7].y
    assert import_csv(job, coll, log=logged.append) == 1
    qpts = [p for p in job.points if p.layer == QUARANTINE_LAYER]
    assert len(qpts) == 1 and qpts[0].num >= 90000, qpts
    assert (by[7].num, by[7].x, by[7].y) == n_before     # ours untouched
    assert any("quarantined" in m for m in logged), logged
    # keep: row skipped entirely
    count = len(job.points)
    assert import_csv(job, coll, log=lambda m: None, on_collision="keep") == 0
    assert len(job.points) == count
    # replace: coordinates taken for a plain point...
    assert import_csv(job, coll, log=lambda m: None,
                      on_collision="replace") == 1
    n, e, z = job.to_world(by[7])
    assert abs(n - 4900.0) < 0.01 and abs(e - 1900.0) < 0.01, (n, e)
    # ...but NEVER for CONTROL — those quarantine instead
    ctl = calibrated_job()
    ctl.add_point(1, 110.0, 680.0, kind="CONTROL", num=7)
    logged2 = []
    assert import_csv(ctl, coll, log=logged2.append,
                      on_collision="replace") == 1
    cp = ctl.find_by_num(7)
    assert (cp.x, cp.y) == (110.0, 680.0), "control coords overwritten!"
    assert any("never overwritten" in m for m in logged2), logged2
    assert any(p.layer == QUARANTINE_LAYER for p in ctl.points)
    # refuse: loud ValueError naming the id
    e = expect(ValueError, import_csv, ctl, coll, on_collision="refuse")
    assert "007" in str(e), e
    expect(ValueError, import_csv, ctl, coll, on_collision="maybe")

    # PENZD positional order honored
    penzd = os.path.join(tmp, "penzd.csv")
    with open(penzd, "w", newline="") as f:
        f.write("40,2001.000,5002.000,1.000,swapped file\r\n")
    job4 = calibrated_job()
    import_csv(job4, penzd, log=lambda m: None, order="PENZD")
    n, e, _ = job4.to_world(job4.points[0])
    assert abs(n - 5002.0) < 0.01 and abs(e - 2001.0) < 0.01, (n, e)


def test_import_validators(tmp):
    job = calibrated_job()
    job.add_point(1, 110.0, 680.0, elev=3.5)             # world (5002, 2001)
    job.add_point(1, 120.0, 690.0, elev=3.6)

    straight = os.path.join(tmp, "straight.csv")
    with open(straight, "w", newline="") as f:
        f.write("101,5002.100,2001.100,3.500,a\r\n")
        f.write("102,5001.900,2000.900,3.600,b\r\n")
        f.write("102,5001.800,2000.800,9000.000,dupe+outlier\r\n")
    rep = validate_import_csv(job, straight)
    assert rep["rows"] == 3 and not rep["swap_suggested"]
    assert rep["duplicate_ids"] == ["102"], rep
    assert rep["range_ok"] and rep["foreign"] == []
    assert rep["elev_outliers"] == ["102"], rep["elev_outliers"]
    assert rep["unit_hint"] == "" and rep["frame_hash_ok"] is None
    assert len(job.points) == 2, "validator modified the job!"

    swapped = os.path.join(tmp, "swapped.csv")
    with open(swapped, "w", newline="") as f:
        f.write("201,2001.100,5002.100,3.500,a\r\n")
        f.write("202,2000.900,5001.900,3.600,b\r\n")
    rep = validate_import_csv(job, swapped)
    assert rep["swap_suggested"], rep                     # suggest, never apply
    assert len(job.points) == 2

    foreign = os.path.join(tmp, "foreign.csv")
    with open(foreign, "w", newline="") as f:
        f.write("301,900000.000,900000.000,3.500,far\r\n")
    rep = validate_import_csv(job, foreign)
    assert not rep["range_ok"] and rep["foreign"] == ["301"], rep

    metric = os.path.join(tmp, "metric.csv")
    with open(metric, "w", newline="") as f:
        f.write(f"401,{5002 * 0.3048:.3f},{2001 * 0.3048:.3f},1.0,m\r\n")
    rep = validate_import_csv(job, metric)
    assert "meters" in rep["unit_hint"], rep["unit_hint"]

    # frame hash round-trip through the comment header
    exported = os.path.join(tmp, "roundtrip.csv")
    export_csv_pnezd(job, exported, options={"comment_header": True,
                                             "header": False})
    rep = validate_import_csv(job, exported)
    assert rep["frame_hash_ok"] is True, rep
    job.rotation_deg = 5.0                               # frame edit!
    rep = validate_import_csv(job, exported)
    assert rep["frame_hash_ok"] is False, rep
    job.rotation_deg = 0.0

    # collisions listed (zero-fill-aware)
    collide = os.path.join(tmp, "collide.csv")
    with open(collide, "w", newline="") as f:
        f.write("001,5002.000,2001.000,3.500,x\r\n")
    rep = validate_import_csv(job, collide)
    assert rep["collisions"] == ["001"], rep


# ----------------------------------------------------- tolerances & codes --

def test_tolerances_codes(tmp):
    # the shipped table, spot-checked against the brief (decimal feet)
    t = DEFAULT_TOLERANCES
    assert t["CONTROL"].h_ft == 0.005 and t["CONTROL"].v_ft == 0.02
    assert t["GRIDLINE"].h_ft == 0.0104 and t["GRIDLINE"].v_ft is None
    assert t["ANCHOR-S"].h_ft == 0.0208 and t["ANCHOR-S"].v_ft == 0.0417
    assert t["ANCHOR-M"].h_ft == 0.0313
    assert t["ANCHOR-L"].h_ft == 0.0417
    assert t["BOLT-IN-GROUP"].h_ft == 0.0104
    assert t["EMBED"].h_ft == 0.0833
    assert t["SLEEVE"].h_ft == 0.0417
    assert t["SAWCUT"].h_ft == 0.0625
    assert t["SLAB-ELEVATION"].h_ft is None
    assert t["SLAB-ELEVATION"].v_ft == 0.0625
    assert t["CURTAIN-WALL-EMBED"].h_ft == 0.0208
    assert t["CURTAIN-WALL-EMBED"].v_ft == 0.0104
    assert t["FINISH-GRADE"].h_ft is None and t["FINISH-GRADE"].v_ft == 0.01
    assert t["CURB-GUTTER"].h_ft == 0.02
    for tc in t.values():                        # every row states its basis
        assert tc.basis, tc.name
    assert "verify against project spec" in fieldpro.TOLERANCE_DISCLAIMER

    # job-level overrides persist in the sidecar and win on lookup
    pdf = os.path.join(tmp, "tolplan.pdf")
    with open(pdf, "wb") as f:
        f.write(b"%PDF-fake")
    job = calibrated_job(pdf)
    set_job_tolerance(job, ToleranceClass("SLEEVE", 0.0208, None,
                                          "project spec 22 05 29"))
    assert job_tolerances(job)["SLEEVE"].h_ft == 0.0208
    job2 = LayoutJob(pdf)                        # reload from sidecar
    assert job_tolerances(job2)["SLEEVE"].h_ft == 0.0208
    assert job_tolerances(job2)["EMBED"].h_ft == 0.0833   # defaults intact

    # tolerance_for: tol_class field, then code default, then GRIDLINE
    p = job.add_point(1, 110.0, 680.0, tol_class="EMBED")
    assert tolerance_for(job, p).name == "EMBED"
    q = job.add_point(1, 120.0, 680.0, code="SLV")
    assert tolerance_for(job, q).name == "SLEEVE"
    r = job.add_point(1, 130.0, 680.0)
    assert tolerance_for(job, r).name == "GRIDLINE"

    # paint colors: exactly the 8 utility-marking colors
    assert set(PAINT_COLORS) == {"WHITE", "PINK", "RED", "YELLOW", "ORANGE",
                                 "BLUE", "GREEN", "PURPLE"}
    # seed codes + compose grammar
    for code in ("CP", "CTRL", "BM", "WP", "GL", "COL", "AB", "ABOLT",
                 "EMB", "SLV", "HGR", "TRK", "PEN", "CJ", "FD", "BOX",
                 "UG", "TBC", "OS"):
        assert code in SEED_CODES, code
        assert SEED_CODES[code].meaning, code
        assert SEED_CODES[code].paint_color in PAINT_COLORS, code
    assert compose("SLV", ["4"]) == "SLV-4"
    assert compose("COL", ["A1"]) == "COL-A1"
    assert compose("AB", ["1.25", "C4"]) == "AB 1.25 C4"
    assert compose("XYZ", ["9"]) == "XYZ 9"              # unknown: spaces


# ------------------------------------------------------------- delta math --

def test_delta_math():
    # the brief's worked example
    rec = deltas((5000.0, 2000.0, 100.0),
                 (5000.006, 1999.996, 100.02), tol_h=0.0104)
    assert abs(rec.dn - 0.006) < 1e-12 and abs(rec.de + 0.004) < 1e-12
    assert abs(rec.dz - 0.02) < 1e-12
    assert abs(rec.hd - 0.0072111025509) < 1e-9, rec.hd
    assert abs(rec.azimuth - 326.309932474) < 1e-6, rec.azimuth
    assert rec.verdict == "SNUG" and rec.passed          # 69% of 1/8 in class
    assert rec.cut_fill == "C 0.02", rec.cut_fill
    assert two_state(rec) == "PASS"

    # same miss judged with a vertical tolerance too: |dZ| busts it
    rec2 = deltas((5000.0, 2000.0, 100.0),
                  (5000.006, 1999.996, 100.02),
                  tol_h=0.0104, tol_v=0.0104)
    assert rec2.verdict == "LOOSE" and not rec2.passed
    assert two_state(rec2) == "NEAR"                     # 1.92x < 2x

    # TIGHT band and the <=-passes boundary (unrounded compare)
    rec3 = deltas((0.0, 0.0, None), (0.003, 0.0, None), tol_h=0.0104)
    assert rec3.verdict == "TIGHT" and rec3.passed
    assert rec3.dz is None and rec3.cut_fill == ""
    rec4 = deltas((0.0, 0.0), (0.0104, 0.0), tol_h=0.0104)
    assert rec4.verdict == "SNUG" and rec4.passed        # exactly 1.0 passes
    rec5 = deltas((0.0, 0.0), (0.02081, 0.0), tol_h=0.0104)
    assert rec5.verdict == "LOOSE" and two_state(rec5) == "FAIL"

    # miss azimuth quadrants (from north, clockwise)
    assert abs(deltas((0, 0), (1.0, 0.0)).azimuth - 0.0) < 1e-9
    assert abs(deltas((0, 0), (0.0, 1.0)).azimuth - 90.0) < 1e-9
    assert abs(deltas((0, 0), (-1.0, 0.0)).azimuth - 180.0) < 1e-9
    assert abs(deltas((0, 0), (0.0, -1.0)).azimuth - 270.0) < 1e-9

    # cut/fill derivation
    assert cut_fill(1.25) == "C 1.25" and cut_fill(-1.25) == "F 1.25"
    assert cut_fill(0.004) == "GRADE" and cut_fill(-0.0049) == "GRADE"
    assert cut_fill(None) == ""
    assert cut_fill(0.005) == "C 0.01"                   # 0.005 is real

    # record round-trip
    assert DeltaRecord.from_dict(rec.to_dict()) == rec


# ------------------------------------------------------- as-staked pairing --

def _pairing_job():
    job = calibrated_job()
    job.add_point(1, 110.0, 680.0, num=1001)             # world (5002, 2001)
    job.add_point(1, 120.0, 690.0, num=101)              # world (5001, 2002)
    job.add_point(1, 130.0, 700.0, num=200)              # world (5000, 2003)
    return job


def test_pairing(tmp):
    job = _pairing_job()
    shots = os.path.join(tmp, "asstaked.csv")
    with open(shots, "w", newline="") as f:
        f.write("1,4900.000,1900.000,,orphan\r\n")        # '1' != '1001'!
        f.write("1001,5002.010,2001.000,100.00,\r\n")     # id exact
        f.write("0101,5001.000,2002.005,,\r\n")           # zero-fill id
        f.write("1101,5001.002,2002.000,,\r\n")           # block +1000
        f.write("101STK,5001.001,2002.001,,\r\n")         # STK suffix
        f.write("X9,5000.001,2003.001,,STK 200\r\n")      # STK desc token
        f.write("QQ,5002.005,2001.003,,\r\n")             # proximity only
    rep = pair_asstaked(job, shots)
    assert rep["count"] == 7
    by_id = {r["shot_id"]: r for r in rep["rows"]}
    assert by_id["1"]["via"] == "unmatched", \
        "'1' must NOT substring-match '1001'"
    assert rep["unmatched"] == [by_id["1"]]
    assert by_id["1001"]["via"] == "id" and by_id["1001"]["label"] == "1001"
    assert by_id["0101"]["via"] == "id" and by_id["0101"]["label"] == "101"
    assert by_id["1101"]["via"] == "block" and "-1000" in by_id["1101"]["note"]
    assert by_id["101STK"]["via"] == "desc"
    assert by_id["X9"]["via"] == "desc" and by_id["X9"]["label"] == "200"
    assert by_id["QQ"]["via"] == "proximity" and not by_id["QQ"]["confirmed"]
    for sid in ("1001", "0101", "1101", "101STK", "X9"):
        assert by_id[sid]["confirmed"], sid
    assert rep["frame_hash_ok"] is None and rep["frame_warning"] == ""

    # frame-hash gate: matching passes, a frame edit warns LOUDLY
    gated = os.path.join(tmp, "gated.csv")
    with open(gated, "w", newline="") as f:
        f.write(f"# frame: {frame_hash(job)}\r\n")
        f.write("1001,5002.010,2001.000,100.00,\r\n")
    assert pair_asstaked(job, gated)["frame_hash_ok"] is True
    job.rotation_deg = 3.0
    rep2 = pair_asstaked(job, gated)
    assert rep2["frame_hash_ok"] is False
    assert "FRAME MISMATCH" in rep2["frame_warning"]
    job.rotation_deg = 0.0


def test_commit(tmp):
    job = _pairing_job()
    qa = QAStore()
    shots = os.path.join(tmp, "commit1.csv")
    with open(shots, "w", newline="") as f:
        f.write("1001,5002.008,2001.000,,\r\n")           # 0.008 < 0.0104: ok
        f.write("101,5001.000,2002.030,,\r\n")            # 0.030: LOOSE
        f.write("QQ,5000.001,2003.001,,\r\n")             # proximity: skipped
    rep = pair_asstaked(job, shots)
    res = commit_asstaked(job, qa, rep["rows"], session_id="S1",
                          staked_by="JD")
    assert res["committed"] == 2 and res["passed"] == 1 and res["failed"] == 1
    assert len(res["skipped"]) == 1                       # unconfirmed prox
    p1001, p101 = job.find_by_num(1001), job.find_by_num(101)
    assert p1001.status == "STAKED" and p1001.staked_by == "JD"
    assert p101.status == "REJECTED"
    assert qa.latest(p1001.uid).session_id == "S1"
    assert qa.latest(p101.uid).verdict == "LOOSE"

    # a human confirms the proximity row -> it commits
    prox = [r for r in rep["rows"] if r["via"] == "proximity"][0]
    prox["confirmed"] = True
    res2 = commit_asstaked(job, qa, [prox], session_id="S1", staked_by="JD")
    assert res2["committed"] == 1
    assert job.find_by_num(200).status == "STAKED"

    # re-stake: REJECTED re-arms to STAKED; every attempt is kept
    shots2 = os.path.join(tmp, "commit2.csv")
    with open(shots2, "w", newline="") as f:
        f.write("101,5001.000,2002.004,,\r\n")            # now good
    rep2 = pair_asstaked(job, shots2)
    commit_asstaked(job, qa, rep2["rows"], session_id="S2",
                    verify_on_pass=True)
    assert p101.status == "VERIFIED"
    assert len(qa.attempts(p101.uid)) == 2                # both attempts kept
    assert qa.latest(p101.uid).passed                     # latest governs

    # VERIFIED is never bulk-downgraded by a later failed attempt
    shots3 = os.path.join(tmp, "commit3.csv")
    with open(shots3, "w", newline="") as f:
        f.write("101,5001.000,2002.500,,\r\n")            # way off
    rep3 = pair_asstaked(job, shots3)
    res3 = commit_asstaked(job, qa, rep3["rows"], session_id="S3")
    assert p101.status == "VERIFIED", "bulk commit downgraded VERIFIED!"
    assert res3["kept_verified"] == ["101"]
    assert len(qa.attempts(p101.uid)) == 3                # ...but recorded

    # QA sidecar round-trips
    pdf = os.path.join(tmp, "qaplan.pdf")
    with open(pdf, "wb") as f:
        f.write(b"%PDF-fake")
    store = QAStore(pdf)
    for recs in qa.records.values():
        for r in recs:
            store.add_delta(r)
    assert os.path.isfile(pdf + QAStore.SUFFIX)
    back = QAStore(pdf)
    assert len(back.attempts(p101.uid)) == 3
    assert back.latest(p1001.uid) == qa.latest(p1001.uid)


# -------------------------------------------------- check shots & brackets --

def test_checkshots_brackets():
    job = calibrated_job()
    ctl = job.add_point(1, 110.0, 680.0, kind="CONTROL", num=1, elev=100.0)
    n, e, _ = job.to_world(ctl)                           # (5002, 2001)
    ok = check_shot(job, ctl.id, (n + 0.005, e, 100.015),
                    ts="2026-07-08T08:00:00+00:00")
    assert ok.passed and abs(ok.hd - 0.005) < 1e-9
    assert abs(ok.dz - 0.015) < 1e-9 and ok.control == "001"
    bad_h = check_shot(job, "1", (n + 0.02, e, 100.0),
                       ts="2026-07-08T12:00:00+00:00")
    assert not bad_h.passed                               # 0.02 > 0.01 H
    bad_v = check_shot(job, ctl, (n, e, 100.025))
    assert not bad_v.passed                               # 0.025 > 0.02 V
    assert check_shot(job, ctl, (n, e, None)).passed      # H-only when no Z
    # the control itself was never touched
    assert (ctl.x, ctl.y, ctl.elev) == (110.0, 680.0, 100.0)
    expect(ValueError, check_shot, job, "nope", (0, 0, 0))

    # brackets: records group chronologically between checks; a failed
    # CLOSING check flags its bracket and names the points to re-shoot
    recs = [
        DeltaRecord(point_uid="a", label="P1",
                    ts="2026-07-08T09:00:00+00:00", hd=0.004, tol_h=0.01),
        DeltaRecord(point_uid="b", label="P2",
                    ts="2026-07-08T10:00:00+00:00", hd=0.004, tol_h=0.01),
        DeltaRecord(point_uid="c", label="P3",
                    ts="2026-07-08T13:00:00+00:00", hd=0.004, tol_h=0.01),
    ]
    brs = brackets([ok, bad_h], recs)
    assert len(brs) == 3, [len(b["records"]) for b in brs]
    assert brs[0]["close"] is ok and not brs[0]["flagged"]
    assert brs[0]["records"] == []                        # before first check
    assert brs[1]["close"] is bad_h and brs[1]["flagged"]
    assert brs[1]["points"] == ["P1", "P2"], brs[1]["points"]
    assert brs[2]["close"] is None and brs[2]["unclosed"]
    assert brs[2]["points"] == ["P3"]
    assert CheckShot.from_dict(ok.to_dict()) == ok


# ------------------------------------------------------------------ ledger --

def test_ledger(tmp):
    job = _pairing_job()
    qa = QAStore()
    shots = os.path.join(tmp, "ledger_shots.csv")
    with open(shots, "w", newline="") as f:
        f.write("1001,5002.008,2001.000,,\r\n")
        f.write("101,5001.000,2002.030,,\r\n")
        f.write("200,5000.002,2003.001,,\r\n")
    rep = pair_asstaked(job, shots)
    commit_asstaked(job, qa, rep["rows"], session_id="S1", staked_by="JD")
    ctl = job.add_point(1, 100.0, 700.0, kind="CONTROL", num=1, elev=100.0)
    n, e, _ = job.to_world(ctl)
    qa.add_check(check_shot(job, ctl, (n + 0.002, e, 100.005),
                            ts="2026-07-08T07:00:00+00:00"))
    qa.add_check(check_shot(job, ctl, (n + 0.03, e, 100.0),
                            ts="2026-07-09T23:59:00+00:00"))   # failed close

    out = os.path.join(tmp, "ledger.pdf")
    res = ledger_pdf(job, qa, out, project="JOB-7", area="L2 plumbing",
                     crew="JD + MK", instrument="5 arcsec, 2mm+2ppm",
                     control_held="CP 001", log=lambda m: None)
    assert os.path.isfile(out) and not os.path.exists(out + ".part")
    assert res["rows"] == 3
    s = res["summary"]
    assert s["staked"] == 3 and s["passed"] == 2
    assert s["near"] + s["failed"] == 1
    assert abs(s["max_hd"] - 0.030) < 1e-6, s
    assert s["rms_hd"] > 0
    from pypdf import PdfReader
    reader = PdfReader(out)
    assert len(reader.pages) == res["pages"] >= 1, (len(reader.pages), res)

    # the _qa.csv companion: governing rows, ASCII CRLF, exact columns
    qcsv = os.path.join(tmp, "ledger_qa.csv")
    assert export_ledger_csv(job, qa, qcsv) == 3
    raw, lines = _read_lines(qcsv)
    assert lines[0] == ",".join(QA_CSV_HEADERS), lines[0]
    body = [ln for ln in lines[1:] if ln]
    assert len(body) == 3
    failed_rows = [ln for ln in body if ",0," in ln]
    assert len(failed_rows) == 1 and "LOOSE" in failed_rows[0], failed_rows


# ------------------------------------------------------------- route sort --

def test_route(tmp):
    job = calibrated_job()
    # a 10-station line placed in a scrambled order: identity walk is awful
    order_in = [0, 5, 1, 6, 2, 7, 3, 8, 4, 9]
    for k in order_in:
        job.add_point(1, 110.0 + 10.0 * k, 680.0, desc=f"sta {k}")
    pts = list(job.points)
    world = [job.to_world(p)[:2] for p in pts]
    identity = list(range(len(pts)))
    nums_before = [p.num for p in job.points]

    route = walk_route(job, start=pts[0])
    ordered = [job.to_world(p)[:2] for p in route]
    coords = [(w[0], w[1]) for w in world]
    ident_len = route_length(coords, identity)
    route_len = route_length([(w[0], w[1]) for w in ordered],
                             list(range(len(ordered))))
    assert route_len < ident_len, (route_len, ident_len)
    assert route_len == 9.0, route_len                   # the perfect walk
    assert route[0] is pts[0]                            # start honored
    # ORDER ONLY: numbers and the job's own list never mutate
    assert [p.num for p in job.points] == nums_before
    assert list(job.points) == pts
    assert sorted(p.id for p in route) == sorted(p.id for p in pts)

    # 2-opt uncrosses a bad greedy tour on a square + outlier
    sq = [(0.0, 0.0), (0.0, 10.0), (10.0, 10.0), (10.0, 0.0), (0.1, 5.0)]
    order = route_order(sq, start=0)
    assert sorted(order) == [0, 1, 2, 3, 4]
    assert route_length(sq, order) <= route_length(sq, [0, 4, 1, 2, 3])

    # elevation bands: everything on the lower band walks first
    job2 = calibrated_job()
    lo1 = job2.add_point(1, 110.0, 680.0, elev=0.0)
    hi1 = job2.add_point(1, 111.0, 680.0, elev=12.0)
    lo2 = job2.add_point(1, 112.0, 680.0, elev=1.0)
    hi2 = job2.add_point(1, 113.0, 680.0, elev=13.0)
    r = walk_route(job2, start=lo1, band_ft=10.0)
    assert [p.id for p in r[:2]] == [lo1.id, lo2.id], \
        [(p.desc, p.elev) for p in r]
    assert {p.id for p in r[2:]} == {hi1.id, hi2.id}
    assert walk_route(job2, points=[]) == []
    expect(ValueError, walk_route, job2, start="nope")


def main():
    tmp = tempfile.mkdtemp(prefix="fieldstitch_pro_")
    test_backcompat(tmp)
    test_labels()
    test_spools(tmp)
    test_statuses()
    test_renumber_spooled()
    test_witness()
    test_witness_lint(tmp)
    test_csv_options(tmp)
    test_import(tmp)
    test_import_validators(tmp)
    test_tolerances_codes(tmp)
    test_delta_math()
    test_pairing(tmp)
    test_commit(tmp)
    test_checkshots_brackets()
    test_ledger(tmp)
    test_route(tmp)
    # nothing leaves temp files behind
    leftovers = [f for _, _, fs in os.walk(tmp) for f in fs
                 if f.endswith(".part")]
    assert not leftovers, leftovers
    print("FIELDSTITCH PRO TESTS PASSED  (old-sidecar compat + lean dicts, "
          "label caps, spools/quarantine/tombstones, status lifecycle + "
          "never-downgrade + number locks, witness derive/cascade/lint, "
          "CSV options matrix + tag sidecar + frame hash, import policies + "
          "advisory validators, delta math vs hand-computed, pairing ladder "
          "('1' != '1001') + frame gate + commit semantics, check-shot "
          "brackets, As-Staked Ledger PDF + _qa.csv, walking-route sort)")
    print("outputs in", tmp)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("FIELDSTITCH PRO TEST FAILED:", e)
        sys.exit(1)
