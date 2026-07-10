"""The Swatchbook — plumbing cut-sheet submittal builder acceptance.

Covers the approved-standard acceptance list (T1-T7):

* T5 stamp geometry — exact rect, colors, width, centered bold red tag.
* T6 never-restamp — a previously stamped PDF is refused as a component.
* T2 stamp presence — the tag is extractable on EVERY page of every packet
  (run over the committed golden set AND fresh synthetic builds).
* T3 rotation — a /Rotate source page gets its stamp in the VISUAL
  top-right (synthetic build + the golden rotated-page packet).
* Gap handling — a missing component never blocks its packet; gaps carry
  insertion positions; 00-BUILD-LOG.md has the approved sections; gap
  fillers insert at the recorded position (T7 shape).
* Library — manifest load, alias-tolerant resolution, sha256 refusal,
  manual import with the clean-sheet check, first-run install copy.
* T1 / T4 / T7 against the real seed kit are GATED on the seed library
  being installed (it ships as a separate ~35 MB kit): with seeds present
  they rebuild the 19 reference packets and match golden filenames + page
  counts exactly; without, they print a SKIP note and the suite stays
  green — the reportlab-oracle pattern.

Run:  python3.12 tests/test_swatchbook.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                    # noqa: E402
import fitz                                           # noqa: E402

from rfi_stamper import swatchbook as sb              # noqa: E402

_N = [0]


def A(cond, msg=""):
    _N[0] += 1
    assert cond, msg


def _quiet(*a, **k):
    pass


TMP = tempfile.mkdtemp(prefix="swatchbook_test_")
GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "golden_cutsheets")
SEEDS = os.path.join(sb.bundled_kit_dir(), "seed_library")
HAVE_SEEDS = os.path.isdir(SEEDS) and any(
    f.endswith(".pdf") for f in os.listdir(SEEDS)) if os.path.isdir(SEEDS) \
    else False


def _make_sheet(path, texts, size=(612, 792), rotate=0):
    """A synthetic clean manufacturer sheet: one page per text marker."""
    doc = fitz.open()
    for t in texts:
        pg = doc.new_page(width=size[0], height=size[1])
        pg.insert_text((60, 90), t, fontsize=14)
        if rotate:
            pg.set_rotation(rotate)
    doc.save(path)
    doc.close()
    return path


def _synthetic_library(root):
    """A tiny library with real hashes: three components + one booklet."""
    os.makedirs(os.path.join(root, "seed_library"), exist_ok=True)
    comps = []
    for cid, maker, aliases, texts in (
            ("bowl_alpha", "MakerA", ["AX-100", "AX100 series bowl"],
             ["ALPHA PAGE 1", "ALPHA PAGE 2"]),
            ("valve_beta", "MakerB", ["BV-2.2", "Beta 2.2 valve"],
             ["BETA PAGE 1"]),
            ("seat_gamma", "MakerC / MakerCC",
             ["GS-9", "OLD-7 (DISCONTINUED - substitution)"],
             ["GAMMA PAGE 1"]),
            ("booklet_delta", "MakerD", ["D series"],
             [f"DELTA PAGE {k}" for k in range(1, 7)])):
        rel = os.path.join("seed_library", f"{cid}.pdf")
        p = os.path.join(root, rel)
        _make_sheet(p, texts)
        comps.append({"id": cid, "manufacturer": maker, "aliases": aliases,
                      "file": rel, "pages": len(texts),
                      "sha256": sb._sha256(p), "source_url": "",
                      "fetched": "", "notes": "", "source": "seed"})
    with open(os.path.join(root, "manifest.json"), "w") as fh:
        json.dump({"components": comps, "wanted": [
            {"product": "widget with no public sheet"}]}, fh)
    return root


# --------------------------------------------------------------------------- #
#  categories + naming                                                        #
# --------------------------------------------------------------------------- #

def test_categories():
    A(len(sb.CATEGORIES) == 50, f"0-49 table complete, {len(sb.CATEGORIES)}")
    A(sb.CATEGORIES[1] == "Water closets" and sb.CATEGORIES[38] ==
      "Water heaters" and sb.CATEGORIES[49] == "Hose reel"
      and sb.CATEGORIES[0] == "Medical gas", "spot values exact")
    A(sb.packet_filename(1, "WC-1") == "01-WC-1.pdf", "two-digit prefix")
    A(sb.packet_filename(38, "WH-1") == "38-WH-1.pdf", "naming standard")
    # the standard hyphenates tags even where a schedule renders them WC1 —
    # and only hyphenated tags are visible to the never-restamp guard
    A(sb.canonical_tag("wc1") == "WC-1", "WC1 -> WC-1")
    A(sb.canonical_tag(" rhb-2 ") == "RHB-2", "already-hyphenated kept")
    A(sb.canonical_tag("B32X") == "B32X", "non-tag shapes untouched")


# --------------------------------------------------------------------------- #
#  T5 — stamp geometry                                                        #
# --------------------------------------------------------------------------- #

def test_stamp_geometry():
    doc = fitz.open()
    pg = doc.new_page(width=612, height=792)            # blank Letter
    sb.stamp(pg, "WC-1")
    tw = fitz.get_text_length("WC-1", fontname="Helvetica-Bold",
                              fontsize=10.5)
    w = max(tw + 12, 40)
    want = fitz.Rect(612 - 10 - w, 10, 612 - 10, 26)
    dr = [d for d in pg.get_drawings() if d["rect"].width > 5]
    A(len(dr) == 1, "one stamp rectangle drawn")
    d = dr[0]
    A(abs(d["rect"].x0 - want.x0) < 0.01 and abs(d["rect"].y1 - want.y1)
      < 0.01 and abs(d["rect"].x1 - want.x1) < 0.01
      and abs(d["rect"].y0 - want.y0) < 0.01,
      f"exact stamp rect: {d['rect']} != {want}")
    A(all(abs(a - b) < 0.005 for a, b in zip(d["color"], (0.80, 0.05, 0.05))),
      f"outline RGB(0.80,0.05,0.05), got {d['color']}")
    A(d["fill"] == (1.0, 1.0, 1.0), "solid white fill (never solid red)")
    A(abs(d["width"] - 0.9) < 1e-6, "0.9 pt outline")
    sp = [s for b in pg.get_text("dict")["blocks"] for ln in b["lines"]
          for s in ln["spans"]]
    A(len(sp) == 1 and sp[0]["text"] == "WC-1", "tag text embedded")
    A(abs(sp[0]["size"] - 10.5) < 0.01 and "Bold" in sp[0]["font"],
      f"Helvetica-Bold 10.5, got {sp[0]['font']} {sp[0]['size']}")
    tc = sp[0]["color"]
    rgb = ((tc >> 16 & 255) / 255, (tc >> 8 & 255) / 255, (tc & 255) / 255)
    A(all(abs(a - b) < 0.01 for a, b in zip(rgb, (0.80, 0.05, 0.05))),
      f"tag TEXT is the same red, got {rgb}")
    cx = (sp[0]["bbox"][0] + sp[0]["bbox"][2]) / 2
    A(abs(cx - (want.x0 + want.x1) / 2) < 1.0, "tag horizontally centered")
    doc.close()


# --------------------------------------------------------------------------- #
#  T6 — the never-restamp guard                                                #
# --------------------------------------------------------------------------- #

def test_never_restamp():
    clean = _make_sheet(os.path.join(TMP, "clean.pdf"), ["CLEAN SHEET"])
    A(sb.looks_stamped(clean) is None, "clean sheet passes")
    stamped = os.path.join(TMP, "stamped.pdf")
    sb.build_packet(stamped, "RHB-1", [clean])
    A(sb.looks_stamped(stamped) == "RHB-1", "stamped sheet detected")
    try:
        sb.build_packet(os.path.join(TMP, "double.pdf"), "X-1", [stamped])
        A(False, "double-stamping must be refused")
    except ValueError as e:
        A("clean manufacturer sheet" in str(e), str(e))
    # a golden (approved, already-stamped) packet is refused the same way
    g = os.path.join(GOLDEN, "01-WC-1.pdf")
    try:
        sb.build_packet(os.path.join(TMP, "double2.pdf"), "X-1", [g])
        A(False, "golden packet as component must be refused")
    except ValueError:
        A(True)
    # a foreign stamp written in MEDIA coords on a /Rotate page renders in
    # the visual top-right and must still refuse (text extraction reports
    # unrotated coordinates — the guard checks the derotated region too)
    rdoc = fitz.open()
    rpg = rdoc.new_page(width=612, height=792)
    rpg.insert_text((12, 60), "ZZ-9", fontsize=10)   # media-space corner
    rpg.set_rotation(90)                             # -> visual top-right
    rot_stamped = os.path.join(TMP, "rot_stamped.pdf")
    rdoc.save(rot_stamped)
    rdoc.close()
    A(sb.looks_stamped(rot_stamped) == "ZZ-9",
      "rotated foreign stamp detected in the VISUAL corner")


# --------------------------------------------------------------------------- #
#  T2 (golden) — stamp presence on every page of the approved set             #
# --------------------------------------------------------------------------- #

def test_golden_stamps_and_counts():
    with open(os.path.join(GOLDEN, "golden_pagecounts.json")) as fh:
        counts = json.load(fh)
    A(len(counts) == 19, "19 approved packets")
    for fname, n in sorted(counts.items()):
        tag = fname[3:-4]
        doc = fitz.open(os.path.join(GOLDEN, fname))
        A(doc.page_count == n, f"{fname}: {doc.page_count} != {n}")
        for page in doc:
            A(tag in page.get_text(), f"{fname}: tag missing on a page")
        doc.close()


# --------------------------------------------------------------------------- #
#  T3 — rotated pages stamp in the VISUAL top-right                           #
# --------------------------------------------------------------------------- #

def _red_topright(path, pno):
    doc = fitz.open(path)
    pix = doc[pno].get_pixmap(dpi=90)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width,
                                                     pix.n).copy()
    doc.close()
    tr = a[:60, -140:]
    red = ((tr[:, :, 0] > 150) & (tr[:, :, 1] < 100) & (tr[:, :, 2] < 100))
    return int(red.sum())


def test_rotation():
    rot = _make_sheet(os.path.join(TMP, "rot.pdf"), ["ROTATED SHEET"],
                      size=(612, 792), rotate=90)
    out = os.path.join(TMP, "rot_packet.pdf")
    sb.build_packet(out, "DF-1", [rot])
    doc = fitz.open(out)
    A(doc[0].rotation == 0, "output page is unrotated (flattened visual)")
    A(abs(doc[0].rect.width - 792) < 0.1, "visual (rotated) size preserved")
    A("DF-1" in doc[0].get_text(), "tag extractable")
    doc.close()
    A(_red_topright(out, 0) > 30, "red stamp in the VISUAL top-right")
    # the approved rotated-page packet carries its stamp the same way
    A(_red_topright(os.path.join(GOLDEN, "14-DF-1.pdf"), 2) > 30,
      "golden rotated page 3: stamp in visual top-right")


# --------------------------------------------------------------------------- #
#  library: resolution, sha refusal, import, install                          #
# --------------------------------------------------------------------------- #

def test_library():
    root = _synthetic_library(os.path.join(TMP, "lib"))
    lib = sb.Library(root)
    A(not lib.issues, f"healthy library: {lib.issues}")
    A(lib.verify() == [], "sha sweep clean")
    # alias resolution: case/punct blind, series-prefix tolerant
    A(lib.resolve("ax-100").id == "bowl_alpha", "case-blind")
    A(lib.resolve("AX 100").id == "bowl_alpha", "space=hyphen")
    A(lib.resolve("bv2.2").id == "valve_beta", "period-blind")
    A(lib.resolve("MakerB BV-2.2").id == "valve_beta", "maker+model")
    A(lib.resolve("AX-100-XL-77").id == "bowl_alpha",
      "suffixed callout resolves to the series sheet")
    A(lib.resolve("no such thing") is None, "unknown callout is a GAP")
    A(lib.resolve("") is None, "empty callout is a GAP")
    A(len(lib.wanted) == 1, "wanted list surfaces")
    # a discontinued-model callout NEVER resolves silently: the match
    # carries a loud engineer-confirm note, and only on an EXACT hit
    c, note = lib.resolve_ex("OLD-7")
    A(c is not None and c.id == "seat_gamma" and note
      and "SUBSTITUTION" in note and "confirm" in note,
      f"substitution surfaces loudly: {note}")
    c2, note2 = lib.resolve_ex("GS-9")
    A(c2 is not None and note2 is None, "a plain match carries no note")
    A(lib.resolve("OLD-7-XL-3") is None,
      "substitution aliases never join prefix matching")
    # each word of a multi-brand manufacturer string counts
    A(lib.resolve("MakerCC GS-9").id == "seat_gamma",
      "second brand of 'MakerC / MakerCC' resolves")

    # sha mismatch -> refused, surfaced, unusable
    victim = os.path.join(root, "seed_library", "seat_gamma.pdf")
    _make_sheet(victim, ["TAMPERED"])
    lib2 = sb.Library(root)
    A(any("sha256 mismatch" in i for i in lib2.issues), lib2.issues)
    A(lib2.resolve("GS-9") is None, "corrupted sheet refused")
    A(any("sha256 mismatch" in i for i in lib2.verify()), "verify reports")

    # manual import: clean-sheet check + manifest append
    fresh = _make_sheet(os.path.join(TMP, "fresh.pdf"), ["REP SHEET"])
    c = lib.import_pdf(fresh, "widget_new", "MakerE", ["WX-5"])
    A(c.source == "manual_import" and c.pages == 1, "import metadata")
    lib3 = sb.Library(root)
    A(lib3.resolve("wx5").id == "widget_new", "import persisted + resolves")
    stamped = os.path.join(TMP, "stamped.pdf")     # from test_never_restamp
    try:
        lib.import_pdf(stamped, "bad_import")
        A(False, "stamped import must be refused")
    except ValueError:
        A(True)

    # manifest entries with explicit JSON nulls must not poison resolution
    nulroot = os.path.join(TMP, "nul")
    _synthetic_library(nulroot)
    with open(os.path.join(nulroot, "manifest.json")) as fh:
        nm = json.load(fh)
    nm["components"][0]["aliases"] = None
    nm["components"][0]["notes"] = None
    with open(os.path.join(nulroot, "manifest.json"), "w") as fh:
        json.dump(nm, fh)
    nlib = sb.Library(nulroot)
    A(nlib.resolve("BV-2.2").id == "valve_beta",
      "null-aliased entry does not poison other lookups")
    A(nlib.resolve("bowl_alpha").id == "bowl_alpha",
      "null-aliased entry still resolves by id")

    # import over an UNREADABLE manifest must refuse, never clobber it
    badroot = os.path.join(TMP, "badman")
    os.makedirs(badroot, exist_ok=True)
    with open(os.path.join(badroot, "manifest.json"), "w") as fh:
        fh.write("{ not json")
    blib = sb.Library(badroot)
    try:
        blib.import_pdf(fresh, "x_any")
        A(False, "import over a broken manifest must refuse")
    except ValueError as e:
        A("unreadable" in str(e), str(e))
    A(open(os.path.join(badroot, "manifest.json")).read() == "{ not json",
      "the broken manifest was not clobbered")

    # first-run install copies the bundled kit to a user dir
    user = os.path.join(TMP, "userlib")
    got = sb.ensure_user_library(user)
    A(got == user and os.path.exists(os.path.join(user, "manifest.json")),
      "kit copied on first run")
    A(os.path.exists(os.path.join(user, "recipes_reference.json")),
      "reference recipes copied")
    # ...and a LATER-ARRIVING seed kit still syncs into that user dir
    # (the seed zip legitimately lands after first run — a first-run-only
    # gate would strand the user on an empty library forever)
    kit2 = _synthetic_library(os.path.join(TMP, "kit2"))
    user2 = os.path.join(TMP, "userlib2")
    os.makedirs(user2, exist_ok=True)
    with open(os.path.join(user2, "manifest.json"), "w") as fh:
        json.dump({"components": [], "wanted": []}, fh)   # pre-seed state
    sb.ensure_user_library(user2, src=kit2)
    A(os.path.exists(os.path.join(user2, "seed_library",
                                  "bowl_alpha.pdf")),
      "late seed kit: sheets sync into the existing user dir")
    u2 = sb.Library(user2)
    A(u2.resolve("AX-100") is not None,
      "late seed kit: manifest entries merged, callouts resolve")
    # a manual import in the user dir survives a re-sync untouched
    imported = u2.import_pdf(_make_sheet(
        os.path.join(TMP, "fresh2.pdf"), ["REP SHEET 2"]), "user_own")
    sb.ensure_user_library(user2, src=kit2)
    u2b = sb.Library(user2)
    A(u2b.get("user_own") is not None and imported.source == "manual_import",
      "re-sync never touches manual imports")

    # the bundled manifest itself: pre-seed state resolves as gaps, loudly
    bl = sb.Library()
    A(len(bl.components) == 43, f"43 bundled entries, {len(bl.components)}")
    A(len(bl.wanted) == 4, "4 wanted products")
    if not HAVE_SEEDS:
        # any real alias from the shipped manifest (taken from DATA, never
        # hard-coded here) resolves as a gap until the sheets install
        alias = next(a for c in bl.components for a in c.aliases
                     if "(" not in a)
        A(bl.resolve(alias) is None, "pre-seed: resolves as gap")
        A(any("not installed" in i for i in bl.issues), "loud about it")


# --------------------------------------------------------------------------- #
#  gap handling + build log + gap fillers (T7 shape, synthetic)               #
# --------------------------------------------------------------------------- #

_SYN_RECIPES = {
    "project": "SYNTHETIC PROJECT",
    "plan_set": "synthetic set",
    "packets": [
        {"filename": "01-WC-1.pdf", "tag": "WC-1", "prefix": 1,
         "category": "Water closets",
         "components": ["bowl_alpha", "valve_beta"],
         "missing": ["the seat - insert AFTER valve"],
         "flags": ["identical to WC-2; ADA height only"]},
        {"filename": "06-TP-1.pdf", "tag": "TP-1", "prefix": 6,
         "category": "Trap primers",   # dict entry + a filler after it
         "components": [{"id": "bowl_alpha", "page_range": [1, 1]}],
         "missing": [], "flags": []},
        {"filename": "07-WHA-1.pdf", "tag": "WHA-1", "prefix": 7,
         "category": "Water hammer arrestor",
         "components": ["valve_beta"], "missing": [], "flags": []},
        {"filename": "08-FD-1.pdf", "tag": "FD-1", "prefix": 8,
         "category": "Floor drains",
         "components": ["bowl_alpha", "ghost_component"],
         "missing": [], "flags": []},
        {"filename": "41-CP-1.pdf", "tag": "CP-1", "prefix": 41,
         "category": "Circ pump",
         "components": [{"id": "booklet_delta", "page_range": [2, 3]}],
         "missing": [], "flags": []},
        {"filename": "41-CP-2.pdf", "tag": "CP-2", "prefix": 41,
         "category": "Circ pump",      # one booklet, TWO different ranges
         "components": [{"id": "booklet_delta", "page_range": [2, 3]},
                        {"id": "booklet_delta", "page_range": [5, 5]}],
         "missing": [], "flags": []},
        {"filename": "04-SS-1.pdf", "tag": "SS-1", "prefix": 4,
         "category": "Sinks", "components": ["ghost_only"],
         "missing": [], "flags": []},
    ],
    "gap_fillers": [
        {"packet": "01-WC-1.pdf", "component": "seat_gamma",
         "insert_after": "valve_beta", "fills": "the seat"},
        {"packet": "06-TP-1.pdf", "component": "valve_beta",
         "insert_after": "bowl_alpha", "fills": ""},
        {"packet": "07-WHA-1.pdf", "component": "seat_gamma",
         "insert_after": "nonexistent_id", "fills": ""}],
    "not_built": [{"tag": "XX-1", "prefix": 0,
                   "reason": "no model pinned"}],
}


def test_build_all_and_gaps():
    root = _synthetic_library(os.path.join(TMP, "lib2"))
    lib = sb.Library(root)
    out = os.path.join(TMP, "out_nofill")
    res = sb.build_all(_SYN_RECIPES, lib, out, gap_fillers=False, log=_quiet)
    A(dict(res["built"])["01-WC-1.pdf"] == 3, "alpha(2)+beta(1) pages")
    A(dict(res["built"])["41-CP-1.pdf"] == 2,
      "booklet page-range: 2 of 6 pages")
    d = fitz.open(os.path.join(out, "41-CP-1.pdf"))
    A("DELTA PAGE 2" in d[0].get_text() and "DELTA PAGE 3" in d[1].get_text(),
      "the RIGHT booklet pages")
    d.close()
    # one booklet scheduled twice with two DIFFERENT ranges keeps both —
    # a path-keyed range map would collapse them to the last
    A(dict(res["built"])["41-CP-2.pdf"] == 3, "two ranges of one booklet")
    d = fitz.open(os.path.join(out, "41-CP-2.pdf"))
    A("DELTA PAGE 2" in d[0].get_text() and "DELTA PAGE 5"
      in d[2].get_text(), "per-occurrence ranges honored in order")
    d.close()
    A("08-FD-1.pdf" in dict(res["built"]),
      "a missing component never blocks the packet")
    A(any("ghost_component" in g for g in res["gapped"]["08-FD-1.pdf"]),
      "the gap is recorded")
    A(res["skipped"] and res["skipped"][0][0] == "04-SS-1.pdf",
      "nothing-usable packet is skipped with a reason")
    log_md = open(res["log_path"], encoding="utf-8").read()
    for section in ("## Delivered - complete packets",
                    "## Delivered - packets with a gap", "## Not built",
                    "## Engineer flags"):
        A(section in log_md, f"log section {section}")
    A("insert AFTER valve" in log_md, "gap insertion position in the log")
    A("XX-1" in log_md and "no model pinned" in log_md, "not-built carried")
    A("ADA height only" in log_md, "flags carried")
    # every built page is stamped (T2 over the synthetic build)
    for fname, _ in res["built"]:
        doc = fitz.open(os.path.join(out, fname))
        tag = fname[3:-4]
        for page in doc:
            A(tag in page.get_text(), f"{fname}: unstamped page")
        doc.close()

    # T7 shape: gap filler inserts at the recorded position, count grows
    out2 = os.path.join(TMP, "out_fill")
    res2 = sb.build_all(_SYN_RECIPES, lib, out2, gap_fillers=True, log=_quiet)
    A(dict(res2["built"])["01-WC-1.pdf"] == 4, "filled: 3 + 1 seat page")
    d = fitz.open(os.path.join(out2, "01-WC-1.pdf"))
    A("GAMMA PAGE 1" in d[3].get_text(),
      "filler sits AFTER the valve (recorded position)")
    for page in d:
        A("WC-1" in page.get_text(), "filled packet fully stamped")
    d.close()
    # a FILLED gap stops being reported as a gap — the packet is complete
    # and the fill is announced loudly instead of double-counted
    A("01-WC-1.pdf" not in res2["gapped"], "filled gap cleared")
    log2 = open(res2["log_path"], encoding="utf-8").read()
    A("filled by seat_gamma" in log2, "the fill is announced in the log")
    A("01-WC-1" in log2.split("## Delivered - packets with a gap")[0],
      "filled packet listed as complete")
    # a filler after a DICT component entry lands at the right position
    A(dict(res2["built"])["06-TP-1.pdf"] == 2, "dict-entry filler applied")
    d = fitz.open(os.path.join(out2, "06-TP-1.pdf"))
    A("BETA PAGE 1" in d[1].get_text(),
      "filler positioned after the dict-shaped component")
    d.close()
    # a filler whose insert_after is missing appends at the END — loudly
    A(any("APPENDED AT END" in fl for fl in res2["flags"]),
      "bad insert_after is loud, never silent")

    # determinism: identical bytes on rebuild
    res3 = sb.build_all(_SYN_RECIPES, lib, os.path.join(TMP, "out_det"),
                        gap_fillers=False, log=_quiet)
    A(open(os.path.join(out, "01-WC-1.pdf"), "rb").read()
      == open(os.path.join(TMP, "out_det", "01-WC-1.pdf"), "rb").read(),
      "packet bytes are deterministic")
    A(res3["built"] == res["built"], "results deterministic")
    raw = open(os.path.join(out, "01-WC-1.pdf"), "rb").read()
    A(b"/Producer" not in raw and b"/CreationDate" not in raw
      and b"/Info" not in raw, "delivered bytes are metadata-clean")


# --------------------------------------------------------------------------- #
#  T1 / T4 / T7 — the real kit (gated on the seed library being installed)    #
# --------------------------------------------------------------------------- #

def test_reference_kit():
    lib = sb.Library()
    recipes = sb.load_recipes()
    A(len(recipes["packets"]) == 19, "19 reference packets in the recipes")
    A(len(recipes["gap_fillers"]) == 3, "3 gap fillers on file")
    if not HAVE_SEEDS:
        print("  (T1/T4/T7 SKIPPED — seed_library not installed yet; "
              "drop the kit into rfi_stamper/data/cutsheet_library/)")
        return
    # T4 — library integrity
    A(lib.verify() == [], f"T4 sha sweep: {lib.verify()[:3]}")
    # T1 — rebuild WITHOUT gap fillers; names + page counts == golden
    out = os.path.join(TMP, "reference_build")
    res = sb.build_all(recipes, lib, out, gap_fillers=False, log=_quiet)
    with open(os.path.join(GOLDEN, "golden_pagecounts.json")) as fh:
        golden = json.load(fh)
    got = dict(res["built"])
    A(sorted(got) == sorted(golden), f"T1 filenames: {sorted(got)[:4]}…")
    for fname, n in golden.items():
        A(got[fname] == n, f"T1 {fname}: {got[fname]} != {n}")
    # T2 over the fresh rebuild
    for fname in got:
        doc = fitz.open(os.path.join(out, fname))
        tag = fname[3:-4]
        for page in doc:
            A(tag in page.get_text(), f"{fname}: unstamped page")
        doc.close()
    # T3 — the rotated-page packet stamps visual top-right
    A(_red_topright(os.path.join(out, "14-DF-1.pdf"), 2) > 30,
      "T3 rebuilt rotated page: stamp visual top-right")
    # T7 — WITH gap fillers: WC-1 7+1=8, L-1 9+1=10, position + stamps
    out2 = os.path.join(TMP, "reference_filled")
    res2 = sb.build_all(recipes, lib, out2, gap_fillers=True, log=_quiet)
    got2 = dict(res2["built"])
    A(got2["01-WC-1.pdf"] == 8 and got2["03-L-1.pdf"] == 10,
      f"T7 counts: {got2['01-WC-1.pdf']}, {got2['03-L-1.pdf']}")
    A(not any("APPENDED AT END" in fl for fl in res2["flags"]),
      "every reference gap filler found its recorded position")
    # the filled build must equal the unfilled build with ONE page inserted
    # at the recorded position (seat after the flush valve = page index 5;
    # P-trap after the P.O. plug = page index 6), every page stamped
    for fname, at in (("01-WC-1.pdf", 5), ("03-L-1.pdf", 6)):
        du = fitz.open(os.path.join(out, fname))
        df = fitz.open(os.path.join(out2, fname))
        tag = fname[3:-4]
        for k in range(df.page_count):
            A(tag in df[k].get_text(), f"T7 {fname}: page {k+1} unstamped")
        for k in range(du.page_count):
            kf = k if k < at else k + 1
            A(du[k].get_text() == df[kf].get_text(),
              f"T7 {fname}: page {k+1} shifted wrong (filler position)")
        du.close()
        df.close()
    print("  (T1/T3/T4/T7 ran against the installed seed kit)")


# --------------------------------------------------------------------------- #

def main():
    tests = [
        (test_categories, "0-49 category table + naming standard"),
        (test_stamp_geometry, "T5: exact approved stamp geometry"),
        (test_never_restamp, "T6: never-restamp guard"),
        (test_golden_stamps_and_counts,
         "T2: every golden page stamped; counts frozen"),
        (test_rotation, "T3: visual top-right on rotated pages"),
        (test_library, "library: resolve/sha/import/install"),
        (test_build_all_and_gaps,
         "gaps never block; log format; fillers; determinism"),
        (test_reference_kit, "T1/T4/T7: the reference kit (seed-gated)"),
    ]
    for fn, label in tests:
        fn()
        print(f"PASS {label}")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"SWATCHBOOK TEST PASSED  ({_N[0]} checks)  — the Swatchbook"
          + ("" if HAVE_SEEDS else "  [seed kit pending]"))


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("SWATCHBOOK TEST FAILED:", e)
        sys.exit(1)
