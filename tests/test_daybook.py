"""Self-contained tests for rfi_stamper.daybook — the daily progress journal.

Exercises, with no project data needed:

* DaybookEntry.new / to_dict / from_dict round-trip, incl. unicode text and
  photo file paths (references only)
* DaybookStore add / remove / get, by_date ordering with crafted dates,
  autosave + reopen round-trip, atomic writes (no .part left), counts math
* daybook_pdf: opens in fitz, page 1 carries the title, a crew name and a
  measurement fragment; an empty store still renders (0 rows) cleanly

Run:  python3.12 tests/test_daybook.py
"""
import json
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz                                            # noqa: E402

from rfi_stamper.daybook import (                      # noqa: E402
    DaybookEntry, DaybookStore, daybook_pdf)


def expect(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc:
        return
    raise AssertionError(f"expected {exc.__name__} from {fn!r}{args!r}")


# ------------------------------------------------------------------- entry --

def test_entry_roundtrip():
    e = DaybookEntry.new(
        crew="crew B — 4 pipefitters",
        weather="overcast, 20°C",
        summary="Pour café-corner slab; second-floor rough-in — día uno",
        comments="Inspector on site 07:30 · no holds",
        measurements=["ceiling ht 9'-2\"", "corridor width 8'-0\"",
                      "østvegg 3,2 m"],
        photos=["site_photos/día_01/IMG_0001.jpg",
                r"C:\jobs\walkdown\photo 002.png"],
        author="foreman",
    )
    # new() fills id / date / created
    assert len(e.id) == 32 and all(c in "0123456789abcdef" for c in e.id), e.id
    assert e.date == date.today().isoformat(), e.date
    assert e.created and "T" in e.created, e.created
    assert DaybookEntry.new().id != DaybookEntry.new().id, "ids must be unique"
    # explicit keywords win over the defaults
    assert DaybookEntry.new(id="abc", date="2026-01-02").date == "2026-01-02"
    assert DaybookEntry.new(id="abc").id == "abc"

    # dict round-trip is lossless (unicode, quotes, backslash paths intact)
    d = e.to_dict()
    assert d["photos"] == e.photos and d["measurements"] == e.measurements
    e2 = DaybookEntry.from_dict(d)
    assert e2 == e, (e2, e)
    # and JSON-safe end to end
    e3 = DaybookEntry.from_dict(json.loads(json.dumps(d)))
    assert e3 == e
    # to_dict hands out copies — mutating them never touches the entry
    d["photos"].append("junk.jpg")
    d["measurements"].append("junk")
    assert len(e.photos) == 2 and len(e.measurements) == 3

    # from_dict tolerates missing fields and coerces junk to strings
    sparse = DaybookEntry.from_dict({"id": "x1", "measurements": [42, None]})
    assert sparse.id == "x1" and sparse.date == "" and sparse.photos == []
    assert sparse.measurements == ["42", "None"]


# ------------------------------------------------------------------- store --

def test_store(tmp):
    base = os.path.join(tmp, "project.ploom.json")
    st = DaybookStore(base)
    assert st.path == base + DaybookStore.SUFFIX
    assert st.entries == [] and st.get("nope") is None

    e1 = st.add(date="2026-07-01", created="2026-07-01T16:00:00+00:00",
                crew="crew A", summary="mobilize",
                photos=["p/one.jpg", "p/two.jpg"])
    e2 = st.add(date="2026-07-03", created="2026-07-03T18:00:00+00:00",
                crew="crew B", summary="late entry",
                measurements=["slab elev 100.25"])
    e3 = st.add(date="2026-07-03", created="2026-07-03T07:00:00+00:00",
                crew="crew B", summary="early entry", photos=["p/three.jpg"])
    e4 = st.add(date="2026-07-02", created="2026-07-02T12:00:00+00:00",
                crew="crew A", summary="middle day")
    assert st.get(e2.id) is e2 and st.get(e1.id) is e1

    # by_date: newest date first, then created desc; store order untouched
    order = [e.id for e in st.by_date()]
    assert order == [e2.id, e3.id, e4.id, e1.id], order
    assert [e.id for e in st.entries] == [e1.id, e2.id, e3.id, e4.id]

    # counts math: 4 entries, 3 photo refs, 3 distinct days
    assert st.counts() == {"entries": 4, "photos": 3, "days": 3}, st.counts()

    # autosave wrote a versioned sidecar atomically (no temp file left)
    assert os.path.isfile(st.path), "autosave did not write the sidecar"
    assert not os.path.exists(st.path + ".part"), "temp file left behind"
    with open(st.path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["version"] == 1 and len(data["entries"]) == 4, data.keys()

    # reopen round-trip: same base path autoloads everything, equal fields
    st2 = DaybookStore(base)
    assert [e.to_dict() for e in st2.entries] == \
        [e.to_dict() for e in st.entries]

    # remove: True once, False after; the removal persists across reopen
    assert st2.remove(e4.id) is True
    assert st2.remove(e4.id) is False
    assert st2.remove("no-such-id") is False
    assert st2.get(e4.id) is None
    st3 = DaybookStore(base)
    assert st3.counts() == {"entries": 3, "photos": 3, "days": 2}, st3.counts()
    assert not os.path.exists(st3.path + ".part")

    # in-memory store: works, but save() needs an explicit path
    mem = DaybookStore()
    assert mem.path is None
    mem.add(date="2026-06-30", crew="crew C")
    expect(ValueError, mem.save)
    side = os.path.join(tmp, "explicit.daybook.json")
    mem.save(side)
    assert not os.path.exists(side + ".part")
    mem2 = DaybookStore()
    mem2.load(side)
    assert mem2.counts()["entries"] == 1 and mem2.entries[0].crew == "crew C"

    # malformed sidecar entries are dropped, not fatal
    with open(side, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "entries": [
            {"id": "ok1", "crew": "crew D"}, "garbage", {"crew": "no id"},
            None]}, f)
    mem3 = DaybookStore()
    mem3.load(side)
    assert [e.id for e in mem3.entries] == ["ok1"], mem3.entries


# --------------------------------------------------------------------- pdf --

def test_daybook_pdf(tmp):
    st = DaybookStore()
    st.add(date="2026-07-06", created="2026-07-06T16:00:00+00:00",
           crew="crew B — 4 pipefitters", weather="overcast",
           summary="Second-floor rough-in complete",
           measurements=["ceiling ht 9'-2\"", "corridor width 8'-0\""],
           photos=["p/a.jpg", "p/b.jpg", "p/c.jpg"])
    st.add(date="2026-07-07", created="2026-07-07T16:00:00+00:00",
           crew="crew A", weather="clear",
           summary="Slab pour, north bay",
           measurements=["slab elev 100.25 verified"])

    out = os.path.join(tmp, "daybook.pdf")
    res = daybook_pdf(st, out, log=lambda m: None)
    assert res["out_path"] == out and os.path.isfile(out), res
    assert res["rows"] == 2 and res["pages"] >= 1, res
    assert not os.path.exists(out + ".part"), "temp file left behind"

    doc = fitz.open(out)
    text = doc[0].get_text()
    doc.close()
    flat = " ".join(text.split())      # cells wrap; collapse line breaks
    assert "DAYBOOK" in flat and "DAILY PROGRESS LOG" in flat, text[:400]
    assert "2 entry(ies)" in flat and "3 photo ref(s)" in flat, text[:400]
    assert "pipefitters" in flat, "crew name missing from page 1"
    assert "corridor width" in flat, "measurement fragment missing"
    assert "slab elev 100.25" in flat
    # photo column is a count; file paths never appear in the printed log
    assert "3" in text and "p/a.jpg" not in text
    # newest first: the 07-07 entry is rendered above the 07-06 entry
    assert text.find("2026-07-07") < text.find("2026-07-06"), "row order"

    # a custom title flows through to the page
    out2 = os.path.join(tmp, "daybook_titled.pdf")
    daybook_pdf(st, out2, title="SITE JOURNAL", log=lambda m: None)
    doc = fitz.open(out2)
    t2 = doc[0].get_text()
    doc.close()
    assert "SITE JOURNAL" in t2

    # empty store still renders: 0 rows, headers + subtitle only, no crash
    out3 = os.path.join(tmp, "daybook_empty.pdf")
    res3 = daybook_pdf(DaybookStore(), out3, log=lambda m: None)
    assert res3["rows"] == 0 and os.path.isfile(out3), res3
    doc = fitz.open(out3)
    t3 = doc[0].get_text()
    doc.close()
    assert "DAYBOOK" in t3 and "0 entry(ies)" in t3 and "0 photo ref(s)" in t3


def main():
    tmp = tempfile.mkdtemp(prefix="rfi_daybook_")
    test_entry_roundtrip()
    test_store(tmp)
    test_daybook_pdf(tmp)
    print("DAYBOOK TESTS PASSED  (entry round-trip incl. unicode + photo "
          "paths, store add/remove/get, by_date ordering, autosave + reopen, "
          "atomic writes, counts, daybook PDF incl. empty store)")
    print("outputs in", tmp)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("DAYBOOK TEST FAILED:", e)
        sys.exit(1)
