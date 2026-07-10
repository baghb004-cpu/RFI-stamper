"""The Cut Ticket — the model-driven pull list feeding the Swatchbook.

Pins the whole discipline: explicit-tags-only census (tag-shaped TEXT is
never scraped — it collides with sheet refs), honest category guessing
(never forced onto the 0-49 standard), harvest-style reconcile (machine
facts refresh, human work untouched, orphans flagged never deleted),
write-if-changed persistence, determinism, census purity (no undo/dirty
pollution), and the packets handed to the Swatchbook (proposals only —
nothing here ever builds a PDF).

Run:  python3.12 tests/test_cutticket.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper import cutticket, draft, swatchbook   # noqa: E402
from rfi_stamper.project import Project                # noqa: E402

_N = [0]


def A(cond, msg=""):
    _N[0] += 1
    assert cond, msg


TMP = tempfile.mkdtemp(prefix="cutticket_test_")


def _model(specs):
    """DraftModel from [(stencil, tag), ...] plus decoy entities that MUST
    never enter the census (tag-shaped text, callouts, rooms, grids)."""
    m = draft.DraftModel()
    for i, (stencil, tag) in enumerate(specs):
        m.add("fixture", [(5.0 + i, 5.0)], stencil=stencil, tag=tag)
    m.add("text", [(1.0, 1.0)], text="SEE DETAIL 2/P-1")
    m.add("text", [(1.0, 2.0)], text="WH-99 SCHEDULE NOTE")
    m.add("callout", [(2.0, 2.0), (3.0, 3.0)], detail="2", sheet="A-501")
    m.add("room", [(8.0, 8.0)], name="MECH", number="101")
    m.add("grid", [(0.0, 0.0), (0.0, 20.0)], label="A")
    return m


# --------------------------------------------------------------------------- #
#  census                                                                      #
# --------------------------------------------------------------------------- #

def test_census():
    m = _model([("wc", "WC-1"), ("wc", "WC-1"), ("lav", "L-1"),
                ("df", "DF-1"), ("wc", "")])
    c = cutticket.census(m)
    by = {r["tag"]: r for r in c["rows"]}
    A(set(by) == {"WC-1", "L-1", "DF-1"},
      f"explicit tags only — decoy text/callouts never counted: {set(by)}")
    A(by["WC-1"]["count"] == 2 and by["WC-1"]["prefix"] == 1, by["WC-1"])
    A(by["L-1"]["prefix"] == 3 and by["DF-1"]["prefix"] == 14, "prefixes")
    A([r["tag"] for r in c["rows"]] == ["WC-1", "L-1", "DF-1"],
      "ordered by (prefix, tag)")
    A(c["untagged"] == {"Water closet, tank type": 1},
      f"untagged surfaced with counts, no invented tags: {c['untagged']}")

    # canonicalization: wc1 and WC-1 are ONE tag
    m2 = _model([("wc", "wc1"), ("wc", "WC-1")])
    c2 = cutticket.census(m2)
    A(len(c2["rows"]) == 1 and c2["rows"][0]["count"] == 2,
      "WC1/WC-1 spellings merge through canonical_tag")

    # honest category gaps: no 0-49 entry -> prefix -1 + loud flag, and a
    # structure stencil is not silently forced into the plumbing standard
    m3 = _model([("co", "CO-1"), ("col_steel", "C-1")])
    c3 = cutticket.census(m3)
    by3 = {r["tag"]: r for r in c3["rows"]}
    A(by3["CO-1"]["prefix"] == -1
      and any("no 0-49 category" in f for f in by3["CO-1"]["flags"]),
      f"needs-category is loud, never guessed: {by3['CO-1']}")
    A(by3["C-1"]["prefix"] == -1, "structure stencil never gets a prefix")

    # one tag on two stencils is a conflict, never a silent merge
    m4 = _model([("wc", "WC-1"), ("lav", "WC-1")])
    c4 = cutticket.census(m4)
    A(len(c4["conflicts"]) == 1 and "two different stencils"
      in c4["conflicts"][0], c4["conflicts"])
    A(c4["rows"][0]["count"] == 2, "both placements still counted")

    # determinism: permuted insertion order -> identical census
    ma = _model([("df", "DF-1"), ("wc", "WC-1"), ("lav", "L-1")])
    A(cutticket.census(ma)["rows"] == cutticket.census(
        _model([("lav", "L-1"), ("df", "DF-1"), ("wc", "WC-1")]))["rows"],
      "census independent of entity order")


def test_census_purity():
    m = _model([("wc", "WC-1")])
    m.dirty = False
    undo_n = len(m._undo)
    ents_before = [(e.id, dict(e.props)) for e in m.ents]
    cutticket.census(m)
    A(m.dirty is False and len(m._undo) == undo_n,
      "census never dirties the model or pushes undo snapshots")
    A([(e.id, dict(e.props)) for e in m.ents] == ents_before,
      "census mutates nothing")


# --------------------------------------------------------------------------- #
#  reconcile into the project store                                            #
# --------------------------------------------------------------------------- #

def test_reconcile():
    ppath = os.path.join(TMP, "job.ploom.json")
    proj = Project(ppath)
    proj.save()
    m = _model([("wc", "WC-1"), ("wc", "WC-1"), ("lav", "L-1"),
                ("df", "")])
    r = cutticket.sync_project(proj, m, "a.loft.json")
    A(r["added"] == 2 and r["tags"] == 2 and r["untagged"] == 1, r)
    wc = next(it for it in proj.pull_list if it.tag == "WC-1")
    A(wc.count == 2 and wc.sources == {"a.loft.json": 2}
      and wc.origin == "model" and not wc.missing_from_model, wc)

    # human work lands on the row...
    wc.callouts = ["flush valve 111", "carrier Z1201"]
    wc.notes = "ADA height"
    wc.status = "confirmed"
    proj.save()

    # ...and survives every re-census while machine facts refresh
    m2 = _model([("wc", "WC-1"), ("ur", "UR-1")])   # one WC gone, L-1 gone
    r2 = cutticket.sync_project(proj, m2, "a.loft.json")
    wc = next(it for it in proj.pull_list if it.tag == "WC-1")
    A(wc.count == 1, "count refreshed (machine fact)")
    A(wc.callouts == ["flush valve 111", "carrier Z1201"]
      and wc.notes == "ADA height" and wc.status == "confirmed",
      "human-owned fields untouched by re-census")
    lv = next(it for it in proj.pull_list if it.tag == "L-1")
    A(lv.missing_from_model and lv.count == 0 and r2["orphaned"] == 1,
      "orphan is TOMBSTONED — flagged, never deleted (harvest law)")
    A(any(it.tag == "UR-1" for it in proj.pull_list), "new tag added")

    # revival clears the tombstone
    m3 = _model([("wc", "WC-1"), ("ur", "UR-1"), ("lav", "L-1")])
    cutticket.sync_project(proj, m3, "a.loft.json")
    lv = next(it for it in proj.pull_list if it.tag == "L-1")
    A(not lv.missing_from_model and lv.count == 1, "re-placing revives")

    # two drawings merge; removing from one keeps the other's count
    mb = _model([("wc", "WC-1")])
    cutticket.sync_project(proj, mb, "b.loft.json")
    wc = next(it for it in proj.pull_list if it.tag == "WC-1")
    A(wc.count == 2 and set(wc.sources) == {"a.loft.json", "b.loft.json"},
      f"drawings merge per-source: {wc.sources}")
    cutticket.sync_project(proj, _model([]), "b.loft.json")
    wc = next(it for it in proj.pull_list if it.tag == "WC-1")
    A(wc.count == 1 and not wc.missing_from_model,
      "leaving one drawing only drops that drawing's count")

    # write-if-changed: an unchanged re-sync leaves the file bytes alone
    before = open(ppath, "rb").read()
    r5 = cutticket.sync_project(proj, m3, "a.loft.json")
    A(not r5["changed"] and open(ppath, "rb").read() == before,
      "no changes -> byte-identical store, no write churn")

    # the store round-trips the rows (restart survival)
    proj2 = Project(ppath)
    wc2 = next(it for it in proj2.pull_list if it.tag == "WC-1")
    A(wc2.callouts == ["flush valve 111", "carrier Z1201"],
      "human callouts survive a reload")


# --------------------------------------------------------------------------- #
#  the Swatchbook handoff                                                      #
# --------------------------------------------------------------------------- #

def test_to_packets():
    ppath = os.path.join(TMP, "job2.ploom.json")
    proj = Project(ppath)
    m = _model([("wc", "WC-1"), ("co", "CO-1"), ("hb", "HB-1")])
    cutticket.sync_project(proj, m, "a.loft.json")
    hb = next(it for it in proj.pull_list if it.tag == "HB-1")
    hb.callouts = ["8121-CP"]
    packets, needs = cutticket.to_packets(proj.pull_list)
    A([p["tag"] for p in packets] == ["WC-1", "HB-1"], packets)
    A(needs == [("CO-1", next(f for f in [
        "; ".join(it.flags) for it in proj.pull_list if it.tag == "CO-1"]))],
      f"needs-category surfaced, never force-prefixed: {needs}")
    wc = next(p for p in packets if p["tag"] == "WC-1")
    A(wc["filename"] == "01-WC-1.pdf" and wc["origin"] == "model", wc)
    A(any("the Cut Ticket" in f for f in wc["flags"]), "provenance flagged")
    A(wc["missing"] and "callouts" in wc["missing"][0],
      "no callouts -> loud gap, never fabricated components")
    hbp = next(p for p in packets if p["tag"] == "HB-1")
    A(hbp["callouts"] == ["8121-CP"] and not hbp["missing"],
      "stored callouts ride into the packet")
    # a tombstoned tag still ships its packet with the loud flag
    cutticket.sync_project(proj, _model([("wc", "WC-1")]), "a.loft.json")
    packets2, _ = cutticket.to_packets(proj.pull_list)
    hbp2 = next(p for p in packets2 if p["tag"] == "HB-1")
    A(any("MISSING FROM MODEL" in f for f in hbp2["flags"]),
      "tombstone rides into the packet flags")
    # nothing here builds PDFs — proposals only
    A(not [f for f in os.listdir(TMP) if f.endswith(".pdf")],
      "the Cut Ticket never builds a packet itself")


def test_tag_render_and_roundtrip():
    m = _model([("wc", "WC-1")])
    ops = draft.render_ops(m)
    texts = [op[3] for op in ops if op[0] == "text"]
    A("WC-1" in texts, "the tag labels the symbol in every rendered output")
    p = os.path.join(TMP, "d.loft.json")
    m.save(p)
    m2 = draft.DraftModel.load(p)
    tags = [e.props.get("tag") for e in m2.ents if e.kind == "fixture"]
    A("WC-1" in tags, "the tag prop round-trips through save/load")
    A(cutticket.census(m2)["rows"] == cutticket.census(m)["rows"],
      "identical census after a round trip")
    # canonical form applies at census, not storage (drafter's text kept)
    m.add("fixture", [(9.0, 9.0)], stencil="hb", tag="hb2")
    A(any(r["tag"] == "HB-2" for r in cutticket.census(m)["rows"]),
      "census canonicalizes hb2 -> HB-2")
    A(swatchbook.canonical_tag("hb2") == "HB-2", "one canonical law")


# --------------------------------------------------------------------------- #

def main():
    tests = [
        (test_census, "census: explicit tags, decoys ignored, honest gaps"),
        (test_census_purity, "census is pure (no dirty/undo pollution)"),
        (test_reconcile, "reconcile: machine refreshes, human survives, "
                         "orphans tombstone, write-if-changed"),
        (test_to_packets, "Swatchbook handoff: proposals only"),
        (test_tag_render_and_roundtrip, "tag renders + round-trips"),
    ]
    for fn, label in tests:
        fn()
        print(f"PASS {label}")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"CUT TICKET TEST PASSED  ({_N[0]} checks)  — the Cut Ticket")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("CUT TICKET TEST FAILED:", e)
        sys.exit(1)
