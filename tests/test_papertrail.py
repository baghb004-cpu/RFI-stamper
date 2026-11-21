"""Self-contained tests for the Paper Trail (rfi_stamper.papertrail): proof
records, statements bound to check ids, reference resolution and the text
and metadata lint.  Plain python, no pytest, synthetic data only.

Regression cases from the owner brief (section 9) that live here:

* case 4   overstated statement: 12 of 34 prints as counts; a "no dimension
           given" sentence cannot be made
* case 5   coverage wider than the check: a "matches" line names only the
           classes the check covered and says what was not compared
* case 8   dead reference: a sheet / item / detail / RFI that does not
           exist in the run comes back unresolved
* case 13  text and metadata lint: tool name in metadata, long dash,
           British spelling, how-to wording all fail; evidence is exempt

Also: the quantifier table, the template rules, conservative reference
reading (marks, level codes, bar sizes and standard designations are never
references), the trail round trip, and the file lint by type.

Run:  python3 tests/test_papertrail.py
"""
import json
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz                                            # noqa: E402

from rfi_stamper import papertrail as pt               # noqa: E402
from rfi_stamper import sheets                         # noqa: E402
from rfi_stamper.minipdf import canvas                 # noqa: E402
from rfi_stamper.papertrail import (                   # noqa: E402
    Inventory, OverstatedError, Quote, Ref, TemplateError, Trail, Verbatim)

EM = "—"
EN = "–"


def expect(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc as e:
        return e
    raise AssertionError(f"expected {exc.__name__} from {fn!r}{args!r}")


def refs_of(text, known=()):
    return [(r.kind, r.value) for r in pt.find_refs(text, known_sheets=known)]


def kinds(hits):
    return sorted({h.kind for h in hits})


# ------------------------------------------------------------ quantifiers --

def test_quant():
    wc = ("wall corner", "wall corners")
    assert pt.quant(34, 34, 34, wc) == "all 34 wall corners"
    assert pt.quant(0, 34, 34, wc) == "none of the 34 wall corners"
    assert pt.quant(12, 34, 34, wc) == "12 of 34 wall corners"
    assert pt.quant(1, 34, 34, wc) == "1 of 34 wall corners"
    assert pt.quant(1, 1, 1, wc) == "the 1 wall corner"
    assert pt.quant(0, 1, 1, wc) == "0 of 1 wall corner"
    assert pt.quant(0, 0, 0, wc) == "0 of 0 wall corners"
    # part of the class compared: counts only, both totals named
    assert pt.quant(12, 34, 20, wc) == \
        "12 of the 20 compared wall corners (34 in total)"
    for n in (0, 20):
        s = pt.quant(n, 34, 20, wc)
        assert "all" not in s.split() and "none" not in s.split(), s
        assert "20 compared" in s and "34 in total" in s, s
    # the count must be supported
    expect(OverstatedError, pt.quant, 21, 34, 20, wc)     # more than compared
    expect(OverstatedError, pt.quant, 5, 34, 35, wc)      # compared > total
    expect(OverstatedError, pt.quant, -1, 34, 34, wc)
    expect(OverstatedError, pt.quant, 1.5, 34, 34, wc)
    expect(OverstatedError, pt.quant, True, 34, 34, wc)
    expect(ValueError, pt.quant, 1, 34, 34, "wall corner")
    # "no" has one producer and it needs a zero over the whole class
    dim = ("dimension to the concrete", "dimensions to the concrete")
    assert pt.absent(0, 34, 34, dim) == "no dimension to the concrete"
    assert pt.absent(0, 0, 0, dim) == "no dimension to the concrete"
    expect(OverstatedError, pt.absent, 1, 34, 34, dim)
    expect(OverstatedError, pt.absent, 0, 34, 20, dim)
    print("PASS quant / absent: all, none, no only with full coverage and "
          "a supporting count")


# ------------------------------------------------- case 4: overstatement --

def _ends(trail, with_string, compared=34, total=34):
    return trail.add(
        "T-ARCH-STRINGS", level="LEVEL 2",
        compared="shear wall ends on S-201",
        against="architect dimension strings on A-201",
        classes={"shear wall end": {
            "noun": ["shear wall end", "shear wall ends"],
            "total": total, "compared": compared,
            "with_string": with_string}},
        values={"sheet": "A-201"}, result="info")


def test_case4_overstated():
    pt.register("T-ARCH-STRINGS", "summary",
                "{n:shear wall end:with_string} "
                "{s:shear wall end:with_string:has|have} an architect "
                "dimension string on {v:sheet}.")
    t = Trail()
    st = t.say(_ends(t, 12).id)
    assert st.text == ("12 of 34 shear wall ends have an architect "
                       "dimension string on A-201."), st.text
    assert "12 of 34 shear wall ends" in st.text
    assert Ref("sheet", "A-201") in st.refs, st.refs
    # the count decides the words: zero over the whole class
    st0 = t.say(_ends(t, 0).id)
    assert st0.text.startswith("none of the 34 shear wall ends have"), st0.text
    st34 = t.say(_ends(t, 34).id)
    assert st34.text.startswith("all 34 shear wall ends have"), st34.text
    st1 = t.say(_ends(t, 1).id)
    assert st1.text.startswith("1 of 34 shear wall ends has"), st1.text
    # only 20 of the 34 compared: the words all / none never appear
    for n in (20, 0, 7):
        s = t.say(_ends(t, n, compared=20).id).text
        words = s.replace(".", " ").lower().split()
        assert "all" not in words and "none" not in words, s
        assert f"{n} of the 20 compared shear wall ends (34 in total)" in s, s
    # a "no dimension given" sentence cannot be registered ..
    for bad in ("no dimension given",
                "No dimension is given at {c:shear wall end:total}.",
                "all ends are dimensioned", "every end checks",
                "none are dimensioned",
                "12 of 34 ends have a string",          # digits typed by hand
                "{n:shear wall end:with_string} of 34 ends"):
        expect(TemplateError, pt.register, "T-ARCH-STRINGS", "bad", bad)
    assert ("T-ARCH-STRINGS", "bad") not in pt.TEMPLATES
    # .. nor rendered if one is planted behind the registry's back
    pt.TEMPLATES[("T-ARCH-STRINGS", "planted")] = "no dimension given"
    try:
        expect(TemplateError, t.say, _ends(t, 12).id, "planted")
    finally:
        del pt.TEMPLATES[("T-ARCH-STRINGS", "planted")]
    # .. nor smuggled in through the proof record
    expect(OverstatedError, t.add, "T-ARCH-STRINGS",
           values={"status": "no dimension given"})
    expect(OverstatedError, t.add, "T-ARCH-STRINGS",
           compared="all shear wall ends")
    expect(OverstatedError, t.add, "T-ARCH-STRINGS",
           not_compared=["every slab edge"])
    p = _ends(t, 12)
    p.values["status"] = "no dimension given"          # mutated after add
    pt.register("T-ARCH-STRINGS", "status", "Status: {v:status}.")
    expect(OverstatedError, t.say, p.id, "status")
    # the count-backed way to say "no": only when the count is zero
    pt.register("T-ARCH-STRINGS", "absent",
                "This end has {no:dimension:here}.")
    cls = {"dimension": {"noun": ["dimension to the concrete",
                                  "dimensions to the concrete"],
                         "total": 41, "compared": 41, "here": 0}}
    ok = t.add("T-ARCH-STRINGS", classes=cls)
    assert t.say(ok.id, "absent").text == \
        "This end has no dimension to the concrete."
    cls2 = {"dimension": dict(cls["dimension"], here=1)}
    expect(OverstatedError, t.say, t.add("T-ARCH-STRINGS", classes=cls2).id,
           "absent")
    cls3 = {"dimension": dict(cls["dimension"], compared=30)}
    expect(OverstatedError, t.say, t.add("T-ARCH-STRINGS", classes=cls3).id,
           "absent")
    # say() takes no free values: nothing typed by hand enters a statement
    expect(TypeError, t.say, p.id, "summary", note="no dimension given")
    print("PASS case 4: 12 of 34 prints as counts; a 'no dimension given' "
          "sentence is impossible (template, planted, proof value)")


# ------------------------------------------- case 5: coverage vs the check --

def test_case5_coverage():
    pt.register("T-CORNER-CAD", "summary",
                "Corner test of {compared} against {against}: "
                "{classes:match} match within {v:tol}. {not_compared}")
    pt.register("T-CORNER-CAD", "round",
                "{n:round column center:match} match within {v:tol}.")
    t = Trail()
    p = t.add(
        "T-CORNER-CAD", level="LEVEL 2",
        compared="structural plan S-201", against="the CAD background",
        classes={
            "wall corner": {"noun": ["wall corner", "wall corners"],
                            "total": 34, "compared": 34, "match": 34},
            "rectangular column corner": {
                "noun": ["rectangular column corner",
                         "rectangular column corners"],
                "total": 8, "compared": 8, "match": 8},
            "round column center": {
                "noun": ["round column center", "round column centers"],
                "total": 4, "compared": 0, "match": 0},
        },
        values={"tol": "1/2 in."},
        not_compared=["round column centers", "slab edges"],
        result="match")
    st = t.say(p.id)
    assert st.text == (
        "Corner test of structural plan S-201 against the CAD background: "
        "all 34 wall corners and all 8 rectangular column corners match "
        "within 1/2 in. Not compared: round column centers; slab edges."), \
        st.text
    head, _, coverage = st.text.partition(" Not compared: ")
    assert "round column" not in head, head         # never in the matches list
    assert "wall corners" in head and "rectangular column corners" in head
    assert "round column centers" in coverage and "slab edges" in coverage
    assert Ref("sheet", "S-201") in st.refs
    # asked about the uncompared class directly: counts, never all / none
    r = t.say(p.id, "round").text
    assert r == ("0 of the 0 compared round column centers (4 in total) "
                 "match within 1/2 in."), r
    # a partly compared class keeps its own coverage in the list
    p.classes["wall corner"].update(compared=30, match=30)
    s2 = t.say(p.id).text
    assert "30 of the 30 compared wall corners (34 in total)" in s2, s2
    assert "all 34 wall corners" not in s2
    # three compared classes join with commas and a final "and"
    p.classes["wall corner"].update(compared=34, match=33)
    p.classes["round column center"].update(compared=4, match=3)
    p.not_compared[:] = ["slab edges"]
    s3 = t.say(p.id).text
    assert ("33 of 34 wall corners, all 8 rectangular column corners and "
            "3 of 4 round column centers match within 1/2 in.") in s3, s3
    assert s3.endswith("Not compared: slab edges."), s3
    # nothing compared at all: there is no "matches" sentence to print
    empty = t.add("T-CORNER-CAD", compared="structural plan S-201",
                  against="the CAD background",
                  classes={"wall corner": {
                      "noun": ["wall corner", "wall corners"],
                      "total": 34, "compared": 0, "match": 0}},
                  values={"tol": "1/2 in."})
    expect(OverstatedError, t.say, empty.id)
    # an empty coverage list still prints a coverage sentence
    pt.register("T-CORNER-CAD", "cov", "{not_compared}")
    full = t.add("T-CORNER-CAD", compared="structural plan S-201",
                 against="the CAD background")
    assert t.say(full.id, "cov").text == \
        "Not compared: nothing in this scope."
    # no second source: a statement must not imply one
    pt.register("T-CORNER-CAD", "against", "Compared against {against}.")
    lone = t.add("T-CORNER-CAD", compared="structural plan S-201")
    expect(OverstatedError, t.say, lone.id, "against")
    print("PASS case 5: 'matches' names only the compared classes and the "
          "statement says what was not compared")


# ------------------------------------------------- case 8: dead reference --

def test_case8_dead_reference():
    inv = Inventory(sheets={"S-201", "A-201", "S-001", "S-501"},
                    items={"L2-1", "L2-2", "L2-3"},
                    details={"5/S-501"}, rfis={"012"},
                    detail_unindexed={"A-201"})
    pt.register("T-REF", "cite", "See {v:sheet} and {v:item} per {v:rfi}; "
                                 "detail {v:detail}.")
    t = Trail()
    good = t.say(t.add("T-REF", values={
        "sheet": "S-201", "item": "ITEM L2-3", "rfi": "RFI 012",
        "detail": "5/S-501"}).id, "cite")
    assert good.text == "See S-201 and ITEM L2-3 per RFI 012; detail 5/S-501."
    for r in (Ref("sheet", "S-201"), Ref("rfi", "012"),
              Ref("detail", "5/S-501"), Ref("sheet", "S-501"),
              Ref("item", "L2-3")):
        assert r in good.refs, (r, good.refs)
    assert len(good.refs) == 5, good.refs
    assert pt.unresolved([good], inv) == [], pt.unresolved([good], inv)
    # a declared reference value needs no lead word and no telling shape
    pt.register("T-REF", "bare", "Sheet {v:sheet_a}, item {v:item_a}, "
                                 "answered by {v:rfi_a}.")
    bare = t.say(t.add("T-REF", values={
        "sheet_a": "S201", "item_a": "L2-2", "rfi_a": "12"}).id, "bare")
    assert bare.refs == [Ref("sheet", "S201"), Ref("item", "L2-2"),
                         Ref("rfi", "012")], bare.refs
    assert pt.unresolved(bare, inv) == []
    lost = t.say(t.add("T-REF", values={
        "sheet_a": "X9", "item_a": "L2-8", "rfi_a": "99"}).id, "bare")
    assert [r for _t, r in pt.unresolved(lost, inv)] == \
        [Ref("sheet", "X9"), Ref("item", "L2-8"), Ref("rfi", "099")]

    dead = t.say(t.add("T-REF", values={
        "sheet": "S-900", "item": "ITEM L2-9", "rfi": "RFI 044",
        "detail": "7/S-777"}).id, "cite")
    bad = {r for _txt, r in pt.unresolved([good, dead], inv)}
    assert bad >= {Ref("sheet", "S-900"), Ref("item", "L2-9"),
                   Ref("rfi", "044"), Ref("detail", "7/S-777"),
                   Ref("sheet", "S-777")}, bad
    assert all(txt == dead.text for txt, _r in pt.unresolved([good, dead], inv))
    # plain strings are scanned the same way (plates, notes, point files)
    assert pt.unresolved("SEE S-201 AND 5/S-501. HOLD ITEM L2-2. RFI #12.",
                         inv) == []
    one = pt.unresolved("WALL PLAN: SEE S-202 FOR OPENINGS", inv)
    assert [r for _t, r in one] == [Ref("sheet", "S-202")], one
    assert [r for _t, r in pt.unresolved("HOLD ITEM L2-7", inv)] == \
        [Ref("item", "L2-7")]
    assert [r for _t, r in pt.unresolved("PER RFI 13", inv)] == \
        [Ref("rfi", "013")]
    # a detail resolves by index, or by its sheet when that sheet's view
    # titles could not be indexed; an indexed sheet must hold the detail
    assert inv.has(Ref("detail", "5/S-501"))
    assert inv.has(Ref("detail", "05/S501"))               # loose forms
    assert not inv.has(Ref("detail", "6/S-501"))
    assert inv.has(Ref("detail", "3/A-201"))
    assert not inv.has(Ref("detail", "3/S-001"))
    # tolerant the way the plan-set index is: hyphen, leading zeros
    assert inv.has(Ref("sheet", "S201")) and inv.has(Ref("sheet", "A201"))
    assert inv.has(Ref("sheet", "s-1")) and inv.has(Ref("sheet", "S01"))  # S-001
    assert not inv.has(Ref("sheet", "S-21"))
    assert inv.has(Ref("rfi", "12")) and inv.has(Ref("rfi", "0012"))
    assert not inv.has(Ref("rfi", "120"))
    assert not inv.has(Ref("sheet", "")) and not inv.has(Ref("plate", "S-201"))
    # marks, level codes and bar sizes never produce references
    for text in ("SW1", "C12", "L2", "#4", "W12 AT C12 ON L2 PER SW1",
                 "walls on L2 against walls on L1", "#4 @ 12 O.C. EACH WAY",
                 "GL-A1", "A/1", "C/2", "L2-W-014", "L2-1001", "P-0001",
                 "BB-HOLD", "12'-4 1/2\"", "1/4 in.", "3/4\" CHAMFER",
                 "RFI needed", "1 item is open", "RFI drafts: 3"):
        assert refs_of(text) == [], (text, refs_of(text))
    assert pt.unresolved("W12 AT C12 ON L2 PER SW1, #4 BARS", inv) == []
    # after a cue word, a mark-shaped token is a sheet only when it carries
    # a discipline prefix this set uses: S10 is a dead sheet, SW1 / L10 /
    # C12 are marks (the set has S and A sheets only)
    cue = "SEE SW1 SCHEDULE. SEE L10. SEE C12. SEE S10."
    assert [r for _t, r in pt.unresolved(cue, inv)] == [Ref("sheet", "S10")]
    pt.register("T-REF", "note", "Governing note: {q:0}")
    quoted = t.say(t.add("T-REF", quotes=[Quote(text=cue, sheet="S-001")]).id,
                   "note")
    assert Ref("sheet", "SW1") in quoted.refs          # read with no set known
    assert [r for _t, r in pt.unresolved(quoted, inv)] == [Ref("sheet", "S10")]
    assert good.explicit == good.refs and quoted.explicit == []
    # a reference added to a statement by hand is treated as declared
    hand = pt.Statement(text="Wall plan.", refs=[Ref("sheet", "X9")])
    assert [r for _t, r in pt.unresolved(hand, inv)] == [Ref("sheet", "X9")]
    print("PASS case 8: a dead sheet / item / detail / RFI reference comes "
          "back unresolved; marks and level codes never do")


def test_find_refs():
    assert sheets.TOKEN_FULL.match("S-201")            # the shared token shape
    # strict shapes need no cue
    assert refs_of("WALLS ON S-201 AND A2.01") == \
        [("sheet", "S-201"), ("sheet", "A2.01")]
    assert refs_of("S-201.") == [("sheet", "S-201")]
    assert refs_of("(S-201)") == [("sheet", "S-201")]
    # strong cues accept any sheet-shaped token of 3+ characters, and lists
    assert refs_of("SEE S10 AND S11 FOR C12") == \
        [("sheet", "S10"), ("sheet", "S11")]
    assert refs_of("SHEETS T-1.01, T-1.02 & S5.1") == \
        [("sheet", "T-1.01"), ("sheet", "T-1.02"), ("sheet", "S5.1")]
    assert refs_of("REFER TO P-10.10") == [("sheet", "P-10.10")]
    assert refs_of("DWG. E-002") == [("sheet", "E-002")]
    assert refs_of("see S10.") == [("sheet", "S10")]
    assert refs_of("SEE S1") == []                     # two characters
    # weak cues need a hyphen or a point
    assert refs_of("ON T-1.01") == [("sheet", "T-1.01")]
    assert refs_of("PER S-5") == [("sheet", "S-5")]
    assert refs_of("ON L2") == [] and refs_of("PER SW1") == []
    assert refs_of("PER W12 SCHEDULE") == []
    # a sentence end closes a cue
    assert refs_of("SEE PLAN. S10 IS VOID") == []
    # details carry their sheet
    assert refs_of("PROVIDE TRIM BARS PER DETAIL 5/S-501") == \
        [("detail", "5/S-501"), ("sheet", "S-501")]
    assert refs_of("12A/A-301") == [("detail", "12A/A-301"),
                                    ("sheet", "A-301")]
    assert refs_of("1/2 AND 3/4 AND A/1 AND 2/C") == []
    # items and RFIs
    assert refs_of("HOLD ITEM 3") == [("item", "3")]
    assert refs_of("ITEMS L2-3, L2-4 AND L2-5") == \
        [("item", "L2-3"), ("item", "L2-4"), ("item", "L2-5")]
    assert refs_of("ITEM L2-3 AND 4 WALLS") == [("item", "L2-3")]
    assert refs_of("RFI 12, RFI #7, RFI-012, RFI NO. 3") == \
        [("rfi", "012"), ("rfi", "007"), ("rfi", "003")]
    assert refs_of("RFIs 12 and 14") == [("rfi", "012"), ("rfi", "014")]
    assert refs_of("RFI 12 and 3 walls") == [("rfi", "012")]
    assert refs_of("RFI-012") == [("rfi", "012")]      # never a sheet
    # a designation after a standards issuer, or before a material word,
    # is a standard: never a sheet
    for text in ("WELD PER AWS D1.1.", "ASTM A-615 OR A-706 GRADE 60",
                 "A-325 BOLTS", "PER ASTM A-992", "TYPE S-100 MORTAR",
                 "PER A-706 GRADE 60"):
        assert refs_of(text) == [], (text, refs_of(text))
    assert refs_of("WELD PER AWS D1.1. SEE S-501") == [("sheet", "S-501")]
    # known sheets match hyphen- and zero-tolerant, without any cue
    assert refs_of("wall at T-1.01 and S201 near C12",
                   known={"T-1.01", "S-201"}) == \
        [("sheet", "T-1.01"), ("sheet", "S201")]
    assert refs_of("C12", known={"C-12"}) == [("sheet", "C12")]
    assert refs_of("") == [] and refs_of(None) == []
    print("PASS find_refs: shapes, cues, lists, details, items, RFIs; "
          "standard designations and marks stay out")


# --------------------------------------------------------- template rules --

def test_template_rules():
    # shape errors
    for bad in ("{unknown}", "{n:wall}", "{n:wall:a:b}", "{s:wall:n:has}",
                "{q:x}", "{v:}", "{classes}", "unbalanced {v:tol",
                "unbalanced v:tol}", "{{v:tol}}", "", "   "):
        expect(TemplateError, pt.register, "T-RULES", "bad", bad)
    # the two words of an agreement slot obey the literal rules too
    expect(TemplateError, pt.register, "T-RULES", "bad",
           "{s:wall:n:no|all}")
    expect(TemplateError, pt.register, "T-RULES", "", "text")
    expect(TemplateError, pt.register, "", "k", "text")
    # idempotent for identical text, refuses a quiet rewording
    pt.register("T-RULES", "a", "Walls: {c:wall:n}.")
    pt.register("T-RULES", "a", "Walls: {c:wall:n}.")
    expect(TemplateError, pt.register, "T-RULES", "a", "Walls {c:wall:n}.")
    t = Trail()
    walls = {"wall": {"noun": ["wall", "walls"], "total": 3, "compared": 3,
                      "n": 1}}
    p = t.add("T-RULES", classes=walls, values={"tol": "1/4 in."})
    assert t.say(p.id, "a").text == "Walls: 1 wall."
    p.classes["wall"]["n"] = 3
    assert t.say(p.id, "a").text == "Walls: 3 walls."
    # unknown key / class / counter / value / quote index
    expect(TemplateError, t.say, p.id, "nope")
    for key, tpl in (("k1", "{c:column:n}"), ("k2", "{c:wall:flag}"),
                     ("k3", "{v:missing}"), ("k4", "{q:0}"),
                     ("k5", "{n:wall:noun}")):
        pt.register("T-RULES", key, tpl)
        expect(TemplateError, t.say, p.id, key)
    expect(KeyError, t.say, "P-9999")
    expect(KeyError, t.get, "P-9999")
    # rendered program wording must pass the lint
    pt.register("T-RULES", "lint", "Tolerance {v:tol}.")
    for bad in (f"1/4 in. {EM} checked", "the colour band", "click Run"):
        q = t.add("T-RULES", values={"tol": bad})
        expect(TemplateError, t.say, q.id, "lint")
    assert t.say(t.add("T-RULES", values={"tol": pt.plain(
        f"1/4 in. {EM} checked")}).id, "lint").text == \
        "Tolerance 1/4 in., checked."
    # a malformed or self-overstating record is refused where it is made
    for kw in ({"classes": {"wall": {"noun": ["wall", "walls"], "total": 3}}},
               {"classes": {"wall": {"total": 3, "compared": 3}}},
               {"classes": {"wall": {"noun": "wall", "total": 3,
                                     "compared": 3}}},
               {"classes": {"wall": 3}},
               {"result": "verified"},
               {"values": {"x": [1, 2]}},
               {"not_compared": [""]},
               {"quotes": ["not a quote"]}):
        expect(ValueError, t.add, "T-RULES", **kw)
    for kw in ({"classes": {"wall": {"noun": ["wall", "walls"], "total": 3,
                                     "compared": 4}}},
               {"classes": {"wall": {"noun": ["wall", "walls"], "total": 3,
                                     "compared": 3, "n": -1}}},
               {"classes": {"wall": {"noun": ["wall", "walls"], "total": 3,
                                     "compared": 3, "n": 1.5}}},
               {"classes": {"wall": {"noun": ["no wall", "walls"],
                                     "total": 3, "compared": 3}}}):
        expect(OverstatedError, t.add, "T-RULES", **kw)
    expect(TypeError, t.add, "T-RULES", colour="red")
    expect(ValueError, t.add, "")
    print("PASS template rules: slot shapes, literal rules, registration, "
          "proof validation, lint of rendered wording")


def test_quotes_and_verbatim():
    pt.register("T-NOTES", "quote", "Governing note: {q:0}")
    pt.register("T-NOTES", "title", "Read from {v:sheet}, titled {v:title}.")
    t = Trail()
    note = Quote(text=f"ALL SLAB EDGES SHALL BE SET TO THE CENTRE OF THE "
                      f"KERB {EN} SEE 7/S-501 AND S-900.",
                 sheet="S-001", heading="COLOUR CODED TOLERANCES",
                 note_no="4", file="notes.pdf", page=1)
    assert note.cite() == "S-001, COLOUR CODED TOLERANCES, note 4"
    assert Quote(text="x", sheet="S-001").cite() == "S-001"
    assert Quote(text="x", note_no="2").cite() == "note 2"
    assert Quote(text="x", file="notes.pdf").cite() == ""   # never the file
    p = t.add("T-NOTES", quotes=[note.to_dict()],           # dicts accepted
              values={"sheet": "S-001",
                      "title": Verbatim("TYPICAL DETAILS - ALL LEVELS")})
    st = t.say(p.id, "quote")
    # the quote prints exactly, British words, long dash and "ALL" included:
    # it is evidence, not program wording
    assert st.text == ('Governing note: "' + note.text
                       + '" (S-001, COLOUR CODED TOLERANCES, note 4)'), st.text
    assert st.quotes == [note.text]
    assert st.verbatim == [note.text, "COLOUR CODED TOLERANCES"]
    # references inside the quote are still read (and must resolve)
    for r in (Ref("sheet", "S-001"), Ref("detail", "7/S-501"),
              Ref("sheet", "S-501"), Ref("sheet", "S-900")):
        assert r in st.refs, (r, st.refs)
    inv = Inventory(sheets={"S-001", "S-501"}, details={"7/S-501"})
    assert [r for _t, r in pt.unresolved([st], inv)] == [Ref("sheet", "S-900")]
    # a Verbatim value is evidence too; a plain value saying the same is not
    st2 = t.say(p.id, "title")
    assert st2.text == "Read from S-001, titled TYPICAL DETAILS - ALL LEVELS."
    assert st2.verbatim == ["TYPICAL DETAILS - ALL LEVELS"]
    assert st2.quotes == []
    expect(OverstatedError, t.add, "T-NOTES",
           values={"title": "TYPICAL DETAILS - ALL LEVELS"})
    # the statement's verbatim list makes a later file lint pass
    assert kinds(pt.lint_text(st.text)) == ["british", "long_dash"]
    assert pt.lint_text(st.text, quotes=st.verbatim) == []
    print("PASS quotes and Verbatim: evidence prints exactly, is exempt "
          "from wording rules, and its references still resolve or block")


# -------------------------------------------------------- case 13: the lint --

def _pdf(path, lines, size=10):
    c = canvas.Canvas(path, pagesize=(612, 792))
    c.setFont("Helvetica", size)
    y = 720
    for ln in lines:
        c.drawString(72, y, ln)
        y -= size + 4
    c.showPage()
    c.save()
    return path


def test_case13_lint(tmp):
    project = {"project": "Harbour Centre Tower", "drawn_by": "Field Crew",
               "date": "2026-11-21"}
    # a clean PDF from the repo's own writer passes: no /Info at all
    clean = _pdf(os.path.join(tmp, "clean.pdf"), [
        "LEVEL 2 WALL PLAN", "12 of 34 shear wall ends have an architect "
        "dimension string.", "Not compared: slab edges.", "FOR REVIEW"])
    doc = fitz.open(clean)
    assert not any(v for k, v in doc.metadata.items()
                   if k not in ("format", "encryption")), doc.metadata
    doc.close()
    assert pt.lint_pdf(clean) == [], pt.lint_pdf(clean)
    assert pt.lint_file(clean, project=project) == []

    # 1. a tool name in the metadata fails
    doc = fitz.open(clean)
    doc.set_metadata({"producer": "PyMuPDF 1.28", "creator": "python"})
    tooled = os.path.join(tmp, "tooled.pdf")
    doc.save(tooled)
    doc.close()
    hits = pt.lint_pdf(tooled)
    assert kinds(hits) == ["tool_name"], hits
    assert {h.token for h in hits} == {"producer=PyMuPDF 1.28",
                                       "creator=python"}, hits
    assert all(h.where == "tooled.pdf" for h in hits)
    # metadata comes from project settings only: a foreign title or a
    # timestamp fails, a project settings value passes
    for meta, want in (({"title": "Some Other Title"}, ["metadata"]),
                       ({"creationDate": "D:20261121120000"}, ["metadata"]),
                       ({"author": "Planloom"}, ["tool_name"]),
                       ({"title": "Harbour Centre Tower",
                         "author": "Field Crew"}, [])):
        doc = fitz.open(clean)
        doc.set_metadata(meta)
        path = os.path.join(tmp, "meta.pdf")
        doc.save(path)
        doc.close()
        got = pt.lint_pdf(path, project=project)
        assert kinds(got) == want, (meta, got)
    assert kinds(pt.lint_pdf(path)) == ["metadata"]       # no settings given
    # an XML metadata packet fails too
    doc = fitz.open(clean)
    doc.set_xml_metadata("<x:xmpmeta xmlns:x='adobe:ns:meta/'></x:xmpmeta>")
    xml = os.path.join(tmp, "xml.pdf")
    doc.save(xml)
    doc.close()
    assert [h.token for h in pt.lint_pdf(xml)] == ["xml metadata packet"]
    assert pt.lint_metadata({"producer": "", "title": None, "x": "  "}) == []
    assert kinds(pt.lint_metadata({"producer": "rfi_stamper 5"})) == \
        ["tool_name"]

    # 2. a long dash fails (the writer and the reader both keep the dash)
    dashed = _pdf(os.path.join(tmp, "dash.pdf"),
                  [f"WALL PLAN {EM} LEVEL 2", f"Grids A{EN}D"])
    doc = fitz.open(dashed)
    assert EM in doc[0].get_text() and EN in doc[0].get_text()
    doc.close()
    hits = pt.lint_pdf(dashed)
    assert kinds(hits) == ["long_dash"], hits
    assert {h.token for h in hits} == {"U+2014", "U+2013"}, hits
    assert hits[0].where == "dash.pdf p1", hits[0]
    # 3. British spelling fails
    hits = pt.lint_pdf(_pdf(os.path.join(tmp, "brit.pdf"), [
        "Opening centreline to grey kerb, 3 metres", "Two storeys"]))
    assert kinds(hits) == ["british"], hits
    assert {h.token for h in hits} == {"centreline", "grey", "kerb", "metres",
                                       "storeys"}, hits
    assert pt.BRITISH["centreline"] == "centerline"
    # 4. how-to wording fails
    hits = pt.lint_pdf(_pdf(os.path.join(tmp, "howto.pdf"), [
        "Step 1: click Run.", "How to use this sheet", "Follow these notes"]))
    assert kinds(hits) == ["howto"], hits
    assert {h.token for h in hits} == {"step 1", "click", "how to",
                                       "follow these"}, hits
    # tool names on the page fail as well
    hits = pt.lint_pdf(_pdf(os.path.join(tmp, "tool.pdf"),
                            ["Rendered with PyMuPDF and numpy"]))
    assert kinds(hits) == ["tool_name"], hits
    # American spelling and plain wording pass
    assert pt.lint_text("Opening centerline to gray curb, 3 meters. "
                        "The stepped footing is 2 stories below. "
                        "Show to the crew; a 2-step check.") == []

    # evidence is exempt: an exact source quote, even wrapped over lines
    quote = (f"SET THE KERB TO THE CENTRE OF THE GREY BAND {EM} "
             "TOLERANCE PER THE PROGRAMME.")
    quoted = _pdf(os.path.join(tmp, "quoted.pdf"), [
        'Governing note: "SET THE KERB TO THE CENTRE',
        f'OF THE GREY BAND {EM} TOLERANCE', 'PER THE PROGRAMME." (S-001, '
        'note 4)'])
    assert kinds(pt.lint_pdf(quoted)) == ["british", "long_dash"]
    assert pt.lint_pdf(quoted, quotes=[quote]) == []
    assert pt.lint_pdf(quoted, quotes=[Quote(text=quote)]) == []
    # .. but only the quote itself: the same word outside it still fails
    hits = pt.lint_text(f'"{quote}" The kerb is grey.', quotes=[quote])
    assert [h.token for h in hits] == ["kerb", "grey"], hits
    # project settings values are the only names allowed on the page
    named = _pdf(os.path.join(tmp, "named.pdf"),
                 ["PROJECT: Harbour Centre Tower", "DRAWN BY: Field Crew"])
    assert kinds(pt.lint_pdf(named)) == ["british"]
    assert pt.lint_pdf(named, project=project) == []
    # a short exempt value never hides part of a longer word
    assert [h.token for h in pt.lint_text("grey area", quotes=["g", "a"])] \
        == ["grey"]
    print("PASS case 13: tool name in metadata, long dash, British "
          "spelling and how-to wording all fail; quotes and project "
          "settings values are exempt")


def test_lint_file(tmp):
    def write(name, body, enc="utf-8"):
        path = os.path.join(tmp, name)
        with open(path, "w", encoding=enc, newline="") as fh:
            fh.write(body)
        return path

    ok = write("pts.csv", "L2-1001,1000.000,1000.000,,POST WP L2-W-014 "
                          "HOLD ITEM 3\r\n")
    assert pt.lint_file(ok) == []
    bad = write("bad.csv", f"L2-1001,1000.0,1000.0,,WP {EM} CENTRE OF WALL\r\n")
    assert kinds(pt.lint_file(bad)) == ["british", "long_dash"]
    assert pt.lint_file(bad)[0].where == "bad.csv"
    # JSON is linted by its decoded strings: an escaped dash is still found
    j = write("job.json", json.dumps({"desc": f"WP {EM} HOLD", "n": 1,
                                      "list": ["colour", {"k": "ok"}]}))
    assert "\\u2014" in open(j, encoding="utf-8").read()
    assert kinds(pt.lint_file(j)) == ["british", "long_dash"]
    assert kinds(pt.lint_file(write("keys.json", '{"click": 1}'))) == ["howto"]
    assert kinds(pt.lint_file(write("broken.json", "{not json: colour"))) == \
        ["british"]
    assert kinds(pt.lint_file(write(
        "bg.dxf", "999\r\nwritten by numpy\r\n0\r\nEOF\r\n"))) == ["tool_name"]
    assert pt.lint_file(write("bg2.dxf", "0\r\nSECTION\r\n2\r\nENTITIES\r\n"
                                         "0\r\nENDSEC\r\n0\r\nEOF\r\n")) == []
    assert kinds(pt.lint_file(write("report.txt", "How to use: step 2"))) == \
        ["howto"]
    for ext in (".md", ".xml", ".rw5", ".gsi"):
        assert kinds(pt.lint_file(write("f" + ext, "grey"))) == ["british"]
    assert pt.lint_file(write("f.txt", "Harbour Centre Tower"),
                        project={"project": "Harbour Centre Tower"}) == []
    # a spreadsheet: sheet text and document properties
    xlsx = os.path.join(tmp, "pts.xlsx")
    with zipfile.ZipFile(xlsx, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml",
                   "<worksheet><c><is><t>CENTRE OF WALL</t></is></c>"
                   "</worksheet>")
        z.writestr("docProps/app.xml",
                   "<Properties><Application>python</Application>"
                   "</Properties>")
    hits = pt.lint_file(xlsx)
    assert kinds(hits) == ["british", "tool_name"], hits
    # what it cannot read is reported as not lintable, never as clean
    assert pt.can_lint("a.pdf") and pt.can_lint("a.CSV") and pt.can_lint(xlsx)
    assert not pt.can_lint("plan.png")
    png = os.path.join(tmp, "plan.png")
    open(png, "wb").write(b"\x89PNG colour")
    assert pt.lint_file(png) == []
    print("PASS lint_file: csv / json (decoded) / dxf / txt / md / xml / "
          "fieldbooks / spreadsheet; can_lint names what is not read")


# -------------------------------------------------------------- the trail --

def test_trail_roundtrip():
    pt.register("T-TRAIL", "summary",
                "{level}: {n:wall:match} match {against}. {q:0}")
    t = Trail()
    ids = []
    for i in range(3):
        p = t.add("T-TRAIL", level=f"LEVEL {i + 1}",
                  compared=f"structural plan S-{i + 1}01",
                  against="the CAD background",
                  classes={"wall": {"noun": ["wall", "walls"], "total": 16,
                                    "compared": 16, "match": 15 + (i == 0)}},
                  values={"tol": "1/4 in.", "title": Verbatim("NO SCALE")},
                  elements=[f"L{i + 1}-W-{k:03d}" for k in range(1, 4)],
                  quotes=[Quote("VERIFY IN FIELD.", sheet="S-001",
                                heading="GENERAL", note_no="1",
                                file="notes.pdf", page=2)],
                  not_compared=["slab edges"], result="match")
        ids.append(p.id)
    assert ids == ["P-0001", "P-0002", "P-0003"], ids
    assert [p.id for p in t.proofs()] == ids
    assert [p.id for p in t.proofs(check="T-TRAIL", level="LEVEL 2")] == \
        ["P-0002"]
    assert t.proofs(check="OTHER") == []
    assert t.has("P-0002") and not t.has("P-0004")
    s1 = t.say("P-0001")
    assert s1.text == ('LEVEL 1: all 16 walls match the CAD background. '
                       '"VERIFY IN FIELD." (S-001, GENERAL, note 1)'), s1.text
    s2 = t.say("P-0002")
    assert s2.text.startswith("LEVEL 2: 15 of 16 walls match"), s2.text
    assert t.statements == [s1, s2]
    assert (s1.proof_id, s1.check, s1.key) == ("P-0001", "T-TRAIL", "summary")

    d = json.loads(json.dumps(t.to_dict()))            # through real JSON
    t2 = Trail.from_dict(d)
    assert t2.to_dict() == t.to_dict()
    assert [p.id for p in t2.proofs()] == ids
    assert t2.get("P-0003").quotes[0] == t.get("P-0003").quotes[0]
    assert isinstance(t2.get("P-0003").values["title"], Verbatim)
    assert t2.statements == t.statements
    assert t2.statements[0].refs == s1.refs and isinstance(
        t2.statements[0].refs[0], Ref)
    # ids stay sequential after a reload and are never reused
    assert t2.add("T-TRAIL").id == "P-0004"
    t3 = Trail.from_dict({"proofs": [d["proofs"][2]]})    # no "next" stored
    assert t3.add("T-TRAIL").id == "P-0004"
    assert t3.say("P-0003").text == t.say("P-0003").text
    # a refused record does not burn an id
    expect(ValueError, t2.add, "T-TRAIL", result="verified")
    assert t2.add("T-TRAIL").id == "P-0005"
    print("PASS trail: sequential stable ids, filters, print manifest, "
          "JSON round trip")


def test_plain():
    assert pt.plain(f"Editable defaults {EM} verify against project spec.") \
        == "Editable defaults, verify against project spec."
    assert pt.plain(f"45{EN}135 and A{EM}B and 10 {EN} 20") == \
        "45-135 and A-B and 10 - 20"
    assert pt.plain("‒―") == "--"
    assert pt.plain("LEVEL 2 SLAB PLAN") == "LEVEL 2 SLAB PLAN"
    assert pt.plain(None) == "" and pt.plain(12) == "12"
    assert pt.lint_text(pt.plain(f"a {EM} b {EN} c")) == []
    # nothing this module prints carries a long dash
    src = open(pt.__file__, encoding="utf-8").read()
    assert not any(ch in src for ch in "‒–—―")
    print("PASS plain: long dashes and odd spaces from other modules are "
          "made printable")


def main():
    tmp = tempfile.mkdtemp(prefix="papertrail_")
    test_quant()
    test_case4_overstated()
    test_case5_coverage()
    test_case8_dead_reference()
    test_find_refs()
    test_template_rules()
    test_quotes_and_verbatim()
    test_case13_lint(tmp)
    test_lint_file(tmp)
    test_trail_roundtrip()
    test_plain()
    print("PAPERTRAIL TEST PASSED")
    print("outputs in", tmp)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("PAPERTRAIL TEST FAILED:", e)
        sys.exit(1)
