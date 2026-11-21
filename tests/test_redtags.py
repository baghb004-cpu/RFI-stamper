"""Self-contained tests for Red Tags (rfi_stamper.redtags), the open items
register of the Batterboard.  Plain python, no pytest, no project data.

Exercises:

* regression case 10 (renumbering): numbers are stable per level across
  runs, saves and reloads; a new item appends; an item that stops being
  raised keeps its number on file and that number is never handed out
  again, even when its entry is lost from the file
* identity: level + key + a location anchor within MATCH_AT_FT; the
  anchor is pinned by the first raise; level-wide items (no anchor)
* evidence fields refresh every run, user fields (status, RFI number,
  quote) survive runs, saves and reloads; an answered item stays
  answered; reopen
* status_text: the only wording that may be printed, for every case
* hold policy: hold / check / release, default and custom, validated
* rfi_draft: neutral, built from the item, never claims anything was
  issued, no long dashes, "" for an answered item
* persistence: versioned, atomic, tolerant of one malformed entry (its
  number stays retired), refuses an unreadable or foreign file instead of
  starting empty
* plural / counts / refs / sort order

Run:  python3 tests/test_redtags.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper import redtags as rt             # noqa: E402
from rfi_stamper.redtags import RedTag, Register  # noqa: E402

LONG_DASHES = [chr(c) for c in range(0x2012, 0x2016)]


def expect(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc as e:
        return e
    raise AssertionError(f"expected {exc.__name__} from {fn!r}{args!r}")


def _read(path) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write(path, text) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _raise(reg, level, key, at=None, category="answer", tag=""):
    """One finding with wording derived from its key (stands in for the
    proof-bound wording a real run supplies)."""
    return reg.raise_item(
        level, key, category,
        label=f"{key} {tag}".strip(),
        wrong=f"What is wrong at {key}.",
        needed=f"What is needed at {key}.",
        at=at,
        boxes=[{"sheet": "S-201", "page": [10, 20, 30, 40],
                "world": [0.0, 0.0, 2.0, 2.0]}],
        proofs=["P-0001"], elements=[f"{level}-W-001"])


# -------------------------------------------------- case 10: renumbering --

def test_case_10_renumbering(tmp):
    path = os.path.join(tmp, "job.redtags.json")
    assert not os.path.exists(path)
    reg = Register(path)                       # a missing file is a first run
    assert len(reg) == 0 and reg.items() == []

    reg.begin_run("run-1")
    a = _raise(reg, "L2", "A")
    b = _raise(reg, "L2", "B")
    c = _raise(reg, "L2", "C")
    d = _raise(reg, "L1", "D")
    reg.end_run()
    assert (a.number, b.number, c.number) == (1, 2, 3), (a, b, c)
    assert d.number == 1                       # numbers are per level
    assert (a.ref, b.ref, c.ref, d.ref) == ("L2-1", "L2-2", "L2-3", "L1-1")
    assert all(t.first_run == "run-1" == t.last_run for t in (a, b, c, d))
    assert not os.path.exists(path), "nothing is written before save()"
    reg.save()
    assert os.path.exists(path) and not os.path.exists(path + ".part")
    blob = json.loads(_read(path))
    assert blob["version"] == 1 and blob["counters"] == {"L1": 1, "L2": 3}

    # ---- run 2 on a fresh load: C, a NEW item, then A; B is not raised
    reg = Register(path)
    assert [t.ref for t in reg.items()] == ["L1-1", "L2-1", "L2-2", "L2-3"]
    reg.begin_run("run-2")
    assert reg.items() == [], "every item is unseen until raised again"
    c2 = _raise(reg, "L2", "C", tag="rev")
    n2 = _raise(reg, "L2", "N")
    a2 = _raise(reg, "L2", "A")
    reg.end_run()
    assert a2.number == 1 and c2.number == 3, "earlier numbers must not change"
    assert n2.number == 4, "a new item appends"
    assert c2.label == "C rev" and c2.first_run == "run-1" \
        and c2.last_run == "run-2", "evidence refreshes, identity stays"
    b2 = reg.get("L2-2")
    assert b2 is not None and b2.key == "B" and b2.seen is False
    assert b2.last_run == "run-1"
    assert [t.ref for t in reg.items()] == ["L2-1", "L2-3", "L2-4"]
    assert [t.ref for t in reg.items(seen_only=False)] == [
        "L1-1", "L2-1", "L2-2", "L2-3", "L2-4"]
    assert [t.ref for t in reg.items("L2", seen_only=False)] == [
        "L2-1", "L2-2", "L2-3", "L2-4"]
    assert reg.refs() == {"L1-1", "L2-1", "L2-2", "L2-3", "L2-4"}
    assert reg.refs(seen_only=True) == {"L2-1", "L2-3", "L2-4"}
    assert reg.counts() == {"answer": 3, "confirm": 0, "answered": 0,
                            "total": 3}
    reg.save()

    # ---- run 3 on a fresh load: a brand-new item gets 5, never B's 2
    reg = Register(path)
    assert reg.get("L2-2").seen is False, "unseen survives the file"
    reg.begin_run("run-3")
    z = _raise(reg, "L2", "Z")
    y = _raise(reg, "L1", "Y")
    reg.end_run()
    assert z.number == 5, f"numbers are never reused, got {z.number}"
    assert y.number == 2, "levels count independently"
    assert reg.get("L2-2").key == "B", "B still owns number 2"
    reg.save()

    # ---- the counter lives in the file: lose N's entry by hand and its
    # number 4 is still never handed out again
    blob = json.loads(_read(path))
    blob["items"] = [e for e in blob["items"]
                     if not (e["level"] == "L2" and e["number"] == 4)]
    _write(path, json.dumps(blob))
    reg = Register(path)
    assert reg.get("L2-4") is None
    reg.begin_run("run-4")
    assert _raise(reg, "L2", "N").number == 6      # even N itself is new now
    assert _raise(reg, "L2", "Q").number == 7
    reg.end_run()

    # B comes back in a later run: same number as on day one
    reg.begin_run("run-5")
    assert _raise(reg, "L2", "B").number == 2
    assert reg.get("L2-2").seen is True and reg.get("L2-2").last_run == "run-5"
    print("PASS regression case 10: renumbering (earlier numbers never "
          "change, new items append, numbers are never reused, per level)")


# ---------------------------------------------------- identity by location --

def test_identity():
    reg = Register()
    reg.begin_run("r1")
    key = "XC-CORNER-CAD|wall corner"
    p = _raise(reg, "L2", key, at=(10.0, 10.0))
    q = _raise(reg, "L2", key, at=(40.0, 10.0))      # 30 ft away
    assert (p.number, q.number) == (1, 2), "two places, two items"
    again = _raise(reg, "L2", key, at=(10.2, 10.0))  # 0.2 ft from the first
    assert again is p and len(reg) == 2
    assert p.at == (10.0, 10.0), "the anchor stays where the first raise put it"
    edge = _raise(reg, "L2", key, at=(10.0, 10.5))   # exactly MATCH_AT_FT
    assert edge is p
    far = _raise(reg, "L2", key, at=(10.0, 10.6))    # just past it
    assert far is not p and far.number == 3
    # nearest wins when two anchors are both within reach
    near = _raise(reg, "L2", key, at=(10.0, 10.35))
    assert near is far, "0.25 ft from item 3, 0.35 ft from item 1"
    # the same key on another level is another item
    other = _raise(reg, "L3", key, at=(10.0, 10.0))
    assert other.ref == "L3-1"
    # another key at the same place is another item
    assert _raise(reg, "L2", "XC-FACE-CAD|wall", at=(10.0, 10.0)).number == 4
    # level-wide items: no anchor on both sides is one item
    w1 = _raise(reg, "L2", "GRID-SPACING")
    w2 = _raise(reg, "L2", "GRID-SPACING")
    assert w1 is w2 and w1.at is None and w1.number == 5
    # an anchored raise never matches a level-wide item, and vice versa
    assert _raise(reg, "L2", "GRID-SPACING", at=(0.0, 0.0)).number == 6
    assert _raise(reg, "L2", key).number == 7
    # bad input surfaces, it is never guessed around
    expect(ValueError, reg.raise_item, "L2", "K", "open", "l", "w", "n")
    expect(ValueError, reg.raise_item, "", "K", "answer", "l", "w", "n")
    expect(ValueError, reg.raise_item, "L2", "", "answer", "l", "w", "n")
    expect(ValueError, reg.raise_item, "L2", "K", "answer", "l", "w", "n",
           at=(1.0, float("nan")))
    expect(ValueError, reg.raise_item, "L2", "K", "answer", "l", "w", "n",
           boxes=["S-201"])
    expect(ValueError, reg.raise_item, "L2", "K", "answer", "l", "w", "n",
           boxes=[{"sheet": "S-201", "page": [1, 2, 3]}])
    assert len(reg) == 8, "a refused raise adds nothing"
    print("PASS identity: level + key + anchor within "
          f"{rt.MATCH_AT_FT} ft, anchor pinned, level-wide items")


# -------------------------------------------------------------- user fields --

def test_user_fields(tmp):
    path = os.path.join(tmp, "user.redtags.json")
    reg = Register(path)
    reg.begin_run("r1")
    ans = _raise(reg, "L2", "SWE-NO-DIM|shear wall end", at=(24.0, 20.0))
    conf = _raise(reg, "L2", "LVL-NO-WALL-BELOW|wall", at=(48.0, 0.0),
                  category="confirm")
    plain = _raise(reg, "L2", "XC-CAD-ONLY|wall", at=(60.0, 5.0))
    reg.end_run()

    # ---- status_text: the only wording that may be printed
    assert ans.status_text() == "RFI needed" == rt.DEFAULT_STATUS
    assert conf.status_text() == "Confirm before forming"
    assert ans.category_label == "Answer needed before layout"
    reg.set_status(conf.ref, "  Confirmed on site  ")
    assert conf.status_text() == "Confirmed on site"
    reg.answer(plain.ref, "12", "Add the wall on line C per the attached "
                                "background.")
    assert plain.category == "answered" and plain.rfi == "012"
    assert plain.origin == "answer", "the evidence category is remembered"
    assert plain.status_text() == "Answered per RFI 012"
    # a user status wins over every default wording
    reg.set_status(plain.ref, "Closed")
    assert plain.status_text() == "Closed"
    reg.set_status(plain.ref, "")
    assert plain.status_text() == "Answered per RFI 012"
    # an answered item with no RFI number on file prints the category label
    bare = RedTag("L9", 1, "K", None, "answered", "l", "w", "n")
    assert bare.status_text() == "Answered" and bare.ref == "L9-1"
    # answering needs both the number and the quote; unknown refs surface
    expect(ValueError, reg.answer, ans.ref, "", "a quote")
    expect(ValueError, reg.answer, ans.ref, "14", "   ")
    assert ans.category == "answer" and ans.rfi == "" and ans.quote == ""
    expect(KeyError, reg.answer, "L2-99", "14", "a quote")
    expect(KeyError, reg.set_status, "L7-1", "x")
    expect(KeyError, reg.reopen, "nope")
    assert reg.get("L2-99") is None

    # ---- the next run brings the same evidence back: user fields survive
    reg.begin_run("r2")
    conf2 = _raise(reg, "L2", "LVL-NO-WALL-BELOW|wall", at=(48.0, 0.0),
                   category="confirm", tag="again")
    plain2 = _raise(reg, "L2", "XC-CAD-ONLY|wall", at=(60.1, 5.0),
                    tag="again")
    reg.end_run()
    assert conf2 is conf and conf.status == "Confirmed on site"
    assert conf.label.endswith("again"), "evidence wording refreshed"
    assert plain2 is plain and plain.category == "answered", \
        "an answered item stays answered when the same evidence returns"
    assert plain.rfi == "012" and plain.quote.startswith("Add the wall")
    assert plain.origin == "answer" and plain.label.endswith("again")
    assert reg.counts("L2") == {"answer": 0, "confirm": 1, "answered": 1,
                                "total": 2}
    assert [t.ref for t in reg.items(categories="answered")] == [plain.ref]
    assert [t.ref for t in reg.items(categories=("answer", "confirm"))] == [
        conf.ref]

    # ---- and they survive the file
    reg.save()
    reg = Register(path)
    conf, plain, ans = (reg.get(conf.ref), reg.get(plain.ref),
                        reg.get(ans.ref))
    assert conf.status_text() == "Confirmed on site"
    assert plain.category == "answered" and plain.rfi == "012"
    assert plain.quote == "Add the wall on line C per the attached background."
    assert plain.status_text() == "Answered per RFI 012"
    assert ans.seen is False and ans.status_text() == "RFI needed"
    reg.begin_run("r3")
    again = _raise(reg, "L2", "XC-CAD-ONLY|wall", at=(60.0, 5.0),
                   category="confirm")
    assert again.category == "answered" and again.origin == "confirm", \
        "still answered; the latest evidence category is what reopen returns to"

    # ---- reopen undoes the answer
    reg.reopen(again.ref)
    assert again.category == "confirm" and again.rfi == "" and again.quote == ""
    assert again.status_text() == "Confirm before forming"
    reg.reopen(again.ref)                      # nothing to undo: unchanged
    assert again.category == "confirm"
    reg.begin_run("r4")
    back = _raise(reg, "L2", "XC-CAD-ONLY|wall", at=(60.0, 5.0))
    assert back is again and back.category == "answer" \
        and back.status_text() == "RFI needed"

    # ---- RedTag round trip keeps every field
    reg.answer(back.ref, "RFI 7", "Per the response.")
    d = back.to_dict()
    twin = RedTag.from_dict(json.loads(json.dumps(d)))
    assert twin == back and twin.rfi == "007" and twin.at == (60.0, 5.0)
    print("PASS user fields: status and answer survive runs + save/load, "
          "status_text wording, answered stays answered, reopen")


# -------------------------------------------------------------- hold policy --

def test_hold_policy():
    assert rt.DEFAULT_HOLD_POLICY == {"answer": "hold", "confirm": "check",
                                      "answered": "release"}
    reg = Register()
    reg.begin_run("r1")
    a = _raise(reg, "L2", "A", category="answer")
    c = _raise(reg, "L2", "C", category="confirm")
    d = _raise(reg, "L2", "D", category="answer")
    reg.answer(d.ref, "3", "Proceed as drawn.")
    assert (reg.action(a), reg.action(c), reg.action(d)) == (
        "hold", "check", "release")
    assert reg.action(a.ref) == "hold", "a ref works as well as the item"
    # a custom policy overlays the default, category by category
    strict = {"confirm": "hold"}
    assert (reg.action(a, strict), reg.action(c, strict),
            reg.action(d, strict)) == ("hold", "hold", "release")
    loose = {"answer": "check", "confirm": "release", "answered": "release"}
    assert (reg.action(a, loose), reg.action(c, loose)) == ("check", "release")
    # the register's own policy is the default for action()
    reg.hold_policy = {"answer": "hold", "confirm": "hold",
                       "answered": "check"}
    assert (reg.action(c), reg.action(d)) == ("hold", "check")
    assert reg.action(d, {"answered": "release"}) == "release"
    # a typo is refused, never silently defaulted
    expect(ValueError, reg.action, a, {"answer": "stop"})
    expect(ValueError, reg.action, a, {"answr": "hold"})
    expect(ValueError, reg.action, a, ["hold"])
    expect(KeyError, reg.action, "L2-99")
    print("PASS hold policy: hold / check / release, default + custom, "
          "validated")


# ---------------------------------------------------------------- RFI draft --

def test_rfi_draft():
    reg = Register()
    reg.begin_run("r1")
    label = "Shear wall end not dimensioned to the concrete"
    wrong = ("The architectural plan does not dimension the end of the "
             "shear wall at grid C/2 to the face of concrete.")
    needed = ("A dimension from a gridline to the end of the wall at "
              "grid C/2.")
    it = reg.raise_item(
        "L2", "SWE-NO-DIM|shear wall end", "answer", label, wrong, needed,
        at=(48.0, 20.0),
        boxes=[{"sheet": "S-201", "page": [100, 100, 140, 140]},
               {"sheet": "A-201", "page": [90, 100, 150, 150]},
               {"sheet": "S-201", "world": [47.0, 19.0, 49.0, 21.0]}],
        proofs=["P-0007"], elements=["L2-SWE-003"])
    draft = reg.rfi_draft(it, {"project": "Sample Project",
                               "date": "2026-11-21",
                               "drawn_by": "Layout crew"})
    lines = draft.splitlines()
    assert lines[0] == "RFI draft"
    assert "Project: Sample Project" in lines and "Date: 2026-11-21" in lines
    assert f"Item: {it.ref} (level L2)" in lines
    assert f"Subject: {label}" in lines
    assert "Reference: S-201, A-201" in lines, "box sheets, each once"
    assert f"Question: {wrong}" in lines
    assert f"Requested: {needed}" in lines
    assert label in draft and needed in draft
    order = [next(i for i, ln in enumerate(lines) if ln.startswith(k))
             for k in ("Subject:", "Reference:", "Question:", "Requested:")]
    assert order == sorted(order)
    assert lines[-1] == "This is a draft for review."
    low = draft.lower()
    for banned in ("sent", "sending", "being written"):
        assert banned not in low, banned
    assert not any(ch in draft for ch in LONG_DASHES)
    assert "Layout crew" not in draft, "only the project and date fields print"
    assert reg.rfi_draft(it.ref) == reg.rfi_draft(it), "ref or item"
    # no project settings: no project line, nothing invented
    bare = reg.rfi_draft(it)
    assert "Project:" not in bare and "Date:" not in bare
    # a long dash or a line break inside the evidence text is normalized
    odd = reg.raise_item("L2", "ODD", "confirm", "Edge run \u2014 light line",
                         "Heavy line is gone;\nonly a light line remains.",
                         "Confirm the edge \u2013 before forming.")
    d2 = reg.rfi_draft(odd)
    assert not any(ch in d2 for ch in LONG_DASHES)
    assert "Subject: Edge run - light line" in d2.splitlines()
    assert "Question: Heavy line is gone; only a light line remains." \
        in d2.splitlines()
    assert "Reference: not recorded" in d2.splitlines()
    # an answered item gets no draft
    reg.answer(it.ref, "21", "Dimension added, see the response.")
    assert reg.rfi_draft(it) == ""
    reg.reopen(it.ref)
    assert reg.rfi_draft(it).startswith("RFI draft\n")
    print("PASS rfi_draft: neutral, from the item's own fields, never says "
          "anything was issued, no long dashes, empty for answered items")


# -------------------------------------------------------------- persistence --

def test_tolerant_load(tmp):
    path = os.path.join(tmp, "tolerant.redtags.json")
    reg = Register(path)
    reg.begin_run("r1")
    for k in ("A", "B", "C"):
        _raise(reg, "L2", k)
    _raise(reg, "L1", "D")
    reg.set_status("L2-1", "Asked on the weekly call")
    reg.hold_policy = {"answer": "hold", "confirm": "hold",
                       "answered": "release"}
    reg.save()
    good = _read(path)

    # ---- one malformed entry: dropped, the good ones load, number retired
    blob = json.loads(good)
    for e in blob["items"]:
        if e["level"] == "L2" and e["number"] == 3:
            e["category"] = "bogus"
    blob["items"].append("not an entry")
    blob["items"].append({"level": "L2", "number": 1, "key": "DUP",
                          "category": "answer"})          # number used twice
    blob["counters"] = {"L2": 1}                            # stale counter too
    _write(path, json.dumps(blob))
    reg = Register(path)
    assert [t.ref for t in reg.items(seen_only=False)] == [
        "L1-1", "L2-1", "L2-2"]
    assert len(reg.dropped) == 3 and "bogus" in reg.dropped[0]["why"]
    assert reg.get("L2-1").key == "A" and \
        reg.get("L2-1").status == "Asked on the weekly call"
    assert reg.hold_policy["confirm"] == "hold", "the policy rides the file"
    reg.begin_run("r2")
    assert _raise(reg, "L2", "NEW").number == 4, \
        "a dropped entry's number is not handed out again"

    # ---- a truncated file is refused, never read as empty
    _write(path, good[: len(good) // 2])
    e = expect(ValueError, Register, path)
    assert "renumber" in str(e), e
    assert _read(path) == good[: len(good) // 2], \
        "a refused load leaves the file alone"
    _write(path, "")
    expect(ValueError, Register, path)
    # a foreign or newer file is refused the same way
    _write(path, json.dumps({"version": 1, "tasks": []}))
    expect(ValueError, Register, path)
    _write(path, json.dumps([1, 2, 3]))
    expect(ValueError, Register, path)
    blob = json.loads(good)
    blob["version"] = 2
    _write(path, json.dumps(blob))
    e = expect(ValueError, Register, path)
    assert "version" in str(e), e

    # ---- an invalid stored policy falls back to the default
    blob = json.loads(good)
    blob["hold_policy"] = {"answer": "stop"}
    _write(path, json.dumps(blob))
    assert Register(path).hold_policy == rt.DEFAULT_HOLD_POLICY

    # ---- in-memory registers: save needs a path, save(path) round trips
    mem = Register()
    expect(ValueError, mem.save)
    expect(ValueError, mem.load)
    mem.begin_run("m1")
    _raise(mem, "ROOF", "A", at=(1.5, 2.5))
    other = os.path.join(tmp, "copy.redtags.json")
    mem.save(other)
    twin = Register(other)
    assert twin.to_dict() == mem.to_dict()
    assert twin.get("ROOF-1").at == (1.5, 2.5) and twin.run_id == "m1"
    assert [f for f in os.listdir(tmp) if f.endswith(".part")] == []
    print("PASS persistence: versioned + atomic, one malformed entry "
          "dropped (number stays retired), unreadable file refused")


def test_small_things():
    assert rt.plural(1, "wall", "walls") == "1 wall"
    assert rt.plural(3, "wall", "walls") == "3 walls"
    assert rt.plural(0, "shear wall end", "shear wall ends") == \
        "0 shear wall ends"
    assert rt.CATEGORIES == ("answer", "confirm", "answered")
    assert set(rt.CATEGORY_LABEL) == set(rt.CATEGORIES)
    assert rt.MATCH_AT_FT == 0.5
    # natural level order: L2 lists before L10
    reg = Register()
    reg.begin_run("r1")
    for lv in ("L10", "L2", "L1"):
        _raise(reg, lv, "A")
        _raise(reg, lv, "B")
    assert [t.ref for t in reg.items()] == [
        "L1-1", "L1-2", "L2-1", "L2-2", "L10-1", "L10-2"]
    # a level code with a hyphen still resolves by ref
    hy = _raise(reg, "P-1", "A")
    assert hy.ref == "P-1-1" and reg.get("P-1-1") is hy
    # evidence lists are deduplicated, order kept
    t = reg.raise_item("L1", "E", "confirm", "l", "w", "n",
                       proofs=["P-2", "P-1", "P-2"],
                       elements=("L1-W-1", "L1-W-1"))
    assert t.proofs == ["P-2", "P-1"] and t.elements == ["L1-W-1"]
    assert t.sheets() == []
    # no text in this module's fixed wording carries a long dash
    fixed = " ".join(list(rt.CATEGORY_LABEL.values()) + [rt.DEFAULT_STATUS])
    assert not any(ch in fixed for ch in LONG_DASHES)
    print("PASS plural, natural level order, refs with hyphens, deduped "
          "evidence lists")


def main():
    tmp = tempfile.mkdtemp(prefix="redtags_")
    test_case_10_renumbering(tmp)
    test_identity()
    test_user_fields(tmp)
    test_hold_policy()
    test_rfi_draft()
    test_tolerant_load(tmp)
    test_small_things()
    print("REDTAGS TEST PASSED")
    print("outputs in", tmp)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("REDTAGS TEST FAILED:", e)
        sys.exit(1)
