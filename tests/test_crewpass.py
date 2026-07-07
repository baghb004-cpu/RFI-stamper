"""Self-contained tests for rfi_stamper.crewpass — offline seat management.

Exercises, against TEMP paths only (the real ~/.planloom is never touched):

* assign / transfer / release flow with full history assertions
* duplicate ACTIVE (user, device) pair raises; bad role raises
* a released seat allows re-assigning the same user on a new seat record
* active() / counts() math (zeros included per role)
* save / reopen round-trip with a unicode user; versioned JSON on disk
* atomic writes (no .part file ever left behind)
* DEFAULT_PATH expands the home dir at runtime (verified via a fake HOME)
* report_pdf opens in fitz: title, a user, and "Released" all visible

Run:  python3.12 tests/test_crewpass.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz                                            # noqa: E402

from rfi_stamper.crewpass import (                     # noqa: E402
    ROLES, Ledger, Seat, report_pdf)

_QUIET = lambda m: None                                # noqa: E731


def expect(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc:
        return
    raise AssertionError(f"expected {exc.__name__} from {fn!r}{args!r}")


def no_part_files(root):
    leftovers = [os.path.join(dp, f) for dp, _dn, fn in os.walk(root)
                 for f in fn if f.endswith(".part")]
    assert not leftovers, f"temp .part file(s) left behind: {leftovers}"


# -------------------------------------------------------------------- seat --

def test_seat_dataclass():
    a = Seat.new(user="office-01", device="WKSTN-1", role="office")
    b = Seat.new(user="office-01", device="WKSTN-1", role="office")
    assert a.id and len(a.id) == 32 and a.id != b.id, "uuid4 hex ids"
    assert a.activated and "T" in a.activated, "ISO activation timestamp"
    assert a.history == [] and a.history is not b.history, "shared history list"
    # explicit kwargs win over generated defaults
    c = Seat.new(id="fixed", activated="2026-01-01T00:00:00+00:00")
    assert c.id == "fixed" and c.activated.startswith("2026-01-01")
    assert c.role == "field", "default role"
    # to_dict / from_dict round-trip
    a.history.append({"event": "assigned", "ts": a.activated, "detail": "x"})
    back = Seat.from_dict(a.to_dict())
    assert back == a, (back, a)
    assert back.history is not a.history and back.history[0] is not a.history[0]


# ----------------------------------------------------------- assign / flow --

def test_flow(tmp):
    path = os.path.join(tmp, "ledger", "crewpass.json")
    led = Ledger(path)
    assert led.seats == [] and not os.path.exists(path), "nothing saved yet"

    # bad role / empty fields raise, nothing recorded
    expect(ValueError, led.assign, "field-crew-a", "TABLET-7", "admin")
    expect(ValueError, led.assign, "field-crew-a", "TABLET-7", "")
    expect(ValueError, led.assign, "", "TABLET-7")
    expect(ValueError, led.assign, "field-crew-a", "")
    assert led.seats == []

    # assign: history event, autosave, role default
    s1 = led.assign("field-crew-a", "TABLET-7")
    assert s1.role == "field" and s1.user == "field-crew-a"
    assert s1.device == "TABLET-7" and s1.activated
    assert [e["event"] for e in s1.history] == ["assigned"]
    assert s1.history[0]["ts"] == s1.activated
    assert "TABLET-7" in s1.history[0]["detail"]
    assert os.path.isfile(path), "assign() must autosave"
    assert led.get(s1.id) is s1 and led.get("nope") is None

    # duplicate ACTIVE (user, device) pair refused; other combos fine
    expect(ValueError, led.assign, "field-crew-a", "TABLET-7")
    expect(ValueError, led.assign, "field-crew-a", "TABLET-7", "viewer")
    led.assign("field-crew-b", "TABLET-7", "viewer")     # other user, same device
    led.assign("field-crew-a", "LAPTOP-3", "office")     # same user, other device
    assert len(led.seats) == 3

    # transfer: device changes, old -> new detail, autosave; KeyError unknown
    s1b = led.transfer(s1.id, "TABLET-9")
    assert s1b is s1 and s1.device == "TABLET-9"
    assert [e["event"] for e in s1.history] == ["assigned", "transferred"]
    assert "TABLET-7 -> TABLET-9" in s1.history[-1]["detail"], s1.history[-1]
    assert s1.history[-1]["ts"], "transfer must be timestamped"
    expect(KeyError, led.transfer, "no-such-seat", "TABLET-1")
    expect(ValueError, led.transfer, s1.id, "")          # stealth release refused
    # transfer that would collide with an active (user, device) pair refused
    expect(ValueError, led.transfer, s1.id, "LAPTOP-3")  # crew-a already there

    # release: record kept, device cleared, history appended, autosave
    assert led.release(s1.id) is True
    assert s1.device == "" and led.get(s1.id) is s1, "record must survive"
    assert [e["event"] for e in s1.history] == \
        ["assigned", "transferred", "released"]
    assert "TABLET-9" in s1.history[-1]["detail"]
    assert led.release(s1.id) is False, "double release is a no-op"
    assert led.release("no-such-seat") is False
    assert len(led.seats) == 3, "release must never delete records"

    # released seat allows re-assigning the same user on a NEW seat
    s4 = led.assign("field-crew-a", "TABLET-9")
    assert s4.id != s1.id and s4.device == "TABLET-9"
    assert [e["event"] for e in s4.history] == ["assigned"], "fresh history"
    assert len(led.seats) == 4

    no_part_files(tmp)
    return led, path, s1, s4


# --------------------------------------------------------- active / counts --

def test_counts(led, s1, s4):
    act = led.active()
    assert s1 not in act and s4 in act
    assert all(s.device for s in act) and len(act) == 3
    c = led.counts()
    assert c == {"seats": 4, "active": 3,
                 "by_role": {"office": 1, "field": 1, "viewer": 1}}, c
    # empty ledger: zeros for every role
    c0 = Ledger(os.path.join(tempfile.gettempdir(), "nonexistent-cp.json"))
    c0.seats = []
    assert c0.counts() == {"seats": 0, "active": 0,
                           "by_role": {r: 0 for r in ROLES}}


# ------------------------------------------------------------- persistence --

def test_roundtrip(tmp):
    path = os.path.join(tmp, "unicode.json")
    led = Ledger(path)
    s = led.assign("chargé-of-works-ü", "TABLETTE-Ø1", "office")
    led.assign("viewer-01", "KIOSK-1", "viewer")
    led.release(s.id)

    # versioned JSON on disk, unicode intact, no temp file
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["version"] == 1 and len(data["seats"]) == 2, data
    assert not os.path.exists(path + ".part")

    # reopen: autoload, everything identical
    led2 = Ledger(path)
    assert [x.to_dict() for x in led2.seats] == [x.to_dict() for x in led.seats]
    back = led2.get(s.id)
    assert back is not None and back.user == "chargé-of-works-ü"
    assert back.device == "" and back.history[-1]["event"] == "released"
    assert led2.counts() == led.counts()

    # explicit save/load to another path leaves self.path alone
    other = os.path.join(tmp, "copy.json")
    led2.save(other)
    led3 = Ledger(os.path.join(tmp, "empty-slot.json"))
    led3.load(other)
    assert [x.to_dict() for x in led3.seats] == [x.to_dict() for x in led.seats]

    # malformed entries are dropped, not fatal
    with open(other, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "seats": [
            {"user": "no-id-so-dropped"},
            "junk",
            {"id": "keep1", "user": "kept", "device": "D-1", "role": "field",
             "history": ["junk", {"ts": "x"}, {"event": "assigned", "ts": "t"}]},
        ]}, f)
    led3.load(other)
    assert len(led3.seats) == 1 and led3.seats[0].id == "keep1"
    assert [e["event"] for e in led3.seats[0].history] == ["assigned"]

    no_part_files(tmp)


def test_default_path_runtime(tmp):
    """DEFAULT_PATH keeps '~' in the class attr and expands per-instance at
    runtime — proven against a fake HOME so the real ~/.planloom is never
    touched."""
    assert Ledger.DEFAULT_PATH.startswith("~"), Ledger.DEFAULT_PATH
    fake_home = os.path.join(tmp, "fakehome")
    os.makedirs(fake_home)
    saved = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE")}
    os.environ["HOME"] = fake_home
    os.environ["USERPROFILE"] = fake_home
    try:
        led = Ledger()
        expected = os.path.join(fake_home, ".planloom", "crewpass.json")
        assert led.path == expected, led.path
        led.assign("field-crew-c", "PHONE-2")
        assert os.path.isfile(expected), "autosave should create ~/.planloom"
        assert Ledger().counts()["active"] == 1, "autoload from default path"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    no_part_files(fake_home)


# ------------------------------------------------------------------ report --

def test_report_pdf(tmp):
    led = Ledger(os.path.join(tmp, "report-ledger.json"))
    s = led.assign("chargé-of-works-ü", "TABLET-7", "office")
    led.assign("field-crew-d", "TABLET-8")
    led.transfer(s.id, "LAPTOP-5")
    led.release(s.id)

    out = os.path.join(tmp, "seat_report.pdf")
    res = report_pdf(led, out, log=_QUIET)
    assert res["out_path"] == out and res["rows"] == 2, res
    assert res["pages"] >= 1 and os.path.isfile(out)
    assert not os.path.exists(out + ".part")

    doc = fitz.open(out)
    text = "\n".join(p.get_text() for p in doc)
    doc.close()
    assert "CREWPASS" in text and "SEAT REPORT" in text, "title missing"
    assert "chargé-of-works-ü" in text, "unicode user missing"
    assert "field-crew-d" in text and "TABLET-8" in text
    assert "Released" in text and "Active" in text, "status column"
    assert "released" in text, "last event (history tail) missing"
    # subtitle carries the counts
    assert "2 seat(s), 1 active" in text, "counts subtitle missing"

    # custom title is honored
    out2 = os.path.join(tmp, "seat_report_2.pdf")
    report_pdf(led, out2, title="CREW SEATS Q3", log=_QUIET)
    doc = fitz.open(out2)
    text2 = doc[0].get_text()
    doc.close()
    assert "CREW SEATS Q3" in text2
    no_part_files(tmp)


def main():
    tmp = tempfile.mkdtemp(prefix="crewpass_")
    test_seat_dataclass()
    led, _path, s1, s4 = test_flow(tmp)
    test_counts(led, s1, s4)
    test_roundtrip(tmp)
    test_default_path_runtime(tmp)
    test_report_pdf(tmp)
    print("CREWPASS TESTS PASSED  (assign/transfer/release history, duplicate "
          "+ bad-role guards, re-assign after release, active/counts math, "
          "unicode round-trip, atomic writes, runtime DEFAULT_PATH, report "
          "PDF in fitz)")
    print("outputs in", tmp)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("CREWPASS TEST FAILED:", e)
        sys.exit(1)
