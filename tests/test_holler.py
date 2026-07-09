"""Self-contained tests for rfi_stamper.holler — Holler, Planloom's hands-free
voice-control engine.  Plain python, no pytest, fully headless (this box is
Linux, so the Sender runs DRY and every keystroke is a recorded intent).

Exercises:

* The Caller grammar — the owner's spoken examples VERBATIM plus the general
  grammar: cardinals (units/teens/tens/hundred/thousand), mixed fractions,
  feet+inches, inches-only, decimals via "point", shape calls joined by "by";
  fraction reduction; each PROFILE formatting the same measure differently;
  non-measure text -> kind None
* The Songbook — Entry round trip, add/find (exact + startswith), disabled
  skip, JSON save/load, CSV round trip incl. a Run's step serialization, seed
* The Sender — HAS_SEND honesty, type_text/tap_key/chord intents, key-spec
  parsing, apply_trip forms, run_steps (the owner's macro), open_target on a
  real dir / a missing path / a URL
* The Router — Songbook-then-Caller precedence, each kind, miss, saved math
* The Ticker — record/summary/reset, miss not counted, save/load
* the offline Corral by construction: no networking / gui / eval in the engine

Run:  python3.12 tests/test_holler.py
"""
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper import holler                                # noqa: E402
from rfi_stamper.holler import (                              # noqa: E402
    CSV_COLUMNS, HAS_SEND, PROFILES, SEED_SONGBOOK, Entry, Holler, Songbook,
    Tick, Ticker, apply_trip, chord, default_path, format_ftin, mixed,
    open_target, parse_dimension, parse_key_spec, parse_number, parse_shape,
    run_steps, speak_to_text, tap_key, type_text)

TMP = tempfile.mkdtemp(prefix="holler_test_")

_N = [0]


def check(cond, msg=""):
    _N[0] += 1
    if not cond:
        raise AssertionError(msg or f"check #{_N[0]} failed")


# ---------------------------------------------------- the Caller: numbers ----

def test_parse_number():
    def val(s):
        return parse_number(s)

    check(val("zero") == (0.0, 1))
    check(val("one") == (1.0, 1))
    check(val("nine") == (9.0, 1))
    check(val("ten") == (10.0, 1))
    check(val("eleven") == (11.0, 1))
    check(val("nineteen") == (19.0, 1))
    check(val("twenty") == (20.0, 1))
    check(val("twenty three") == (23.0, 2), val("twenty three"))
    check(val("forty") == (40.0, 1))
    check(val("ninety nine") == (99.0, 2))
    check(val("one hundred") == (100.0, 2))
    check(val("one hundred five") == (105.0, 3), val("one hundred five"))
    check(val("two hundred fifty six") == (256.0, 4))
    check(val("one thousand") == (1000.0, 2))
    check(val("two thousand forty") == (2040.0, 3), val("two thousand forty"))
    check(val("a") == (1.0, 1))                      # article = 1
    check(val("an") == (1.0, 1))
    # leading fractions: the cardinal in front is the numerator
    check(val("a half") == (0.5, 2), val("a half"))
    check(val("half") == (0.5, 1))
    check(val("three quarters") == (0.75, 2), val("three quarters"))
    check(val("seven eighths") == (0.875, 2))
    check(val("one quarter") == (0.25, 2))
    check(val("one sixteenth") == (1.0 / 16.0, 2))
    # thirty-second is 1/32, NOT the tens word "thirty"
    check(val("thirty seconds") == (1.0 / 32.0, 2), val("thirty seconds"))
    check(val("seven thirty seconds") == (7.0 / 32.0, 3),
          val("seven thirty seconds"))
    # whole-and-fraction
    check(val("six and seven eighths") == (6.875, 4),
          val("six and seven eighths"))
    check(val("one and one half") == (1.5, 4))
    # decimal via "point"
    check(val("twelve point five") == (12.5, 3), val("twelve point five"))
    check(val("point five") == (0.5, 2))
    check(val("point two five") == (0.25, 3))
    check(val("one hundred five point two five") == (105.25, 6))
    # no number -> the (0, 0) fall-through sentinel
    check(val("garbage") == (0.0, 0))
    check(val("") == (0.0, 0))
    check(parse_number("two feet") == (2.0, 1))      # stops at the unit word
    # token-list input works too
    check(parse_number(["twenty", "three"]) == (23.0, 2))


def test_mixed():
    check(mixed(6.875) == "6 7/8", mixed(6.875))
    check(mixed(2.5) == "2 1/2")
    check(mixed(0.25) == "1/4")                      # zero whole dropped
    check(mixed(0.75) == "3/4")
    check(mixed(2.0) == "2")                         # exact int
    check(mixed(0.0) == "0")
    check(mixed(4.5) == "4 1/2")                     # 4 8/16 reduces to 4 1/2
    check(mixed(-3.5) == "-3 1/2", mixed(-3.5))
    check(mixed(1.0 / 16.0) == "1/16")
    check(mixed(0.5, denom=32) == "1/2")
    check(mixed(7.0 / 32.0, denom=32) == "7/32")


# --------------------------------------------------- the Caller: dimensions --

def test_parse_dimension_owner_examples():
    # the owner's examples, VERBATIM
    check(parse_dimension("one hundred five feet six and seven eighths")
          == "105'-6 7/8\"",
          parse_dimension("one hundred five feet six and seven eighths"))
    check(parse_dimension("two feet seven and seven eighths") == "2'-7 7/8\"",
          parse_dimension("two feet seven and seven eighths"))
    # inches only -> bare inch mark (arch)
    check(parse_dimension("six and seven eighths") == "6 7/8\"",
          parse_dimension("six and seven eighths"))


def test_parse_dimension_general():
    check(parse_dimension("twenty feet") == "20'-0\"")
    check(parse_dimension("twenty three feet six") == "23'-6\"")
    check(parse_dimension("twelve point five feet") == "12'-6\"")
    check(parse_dimension("five feet and one half") == "5'-0 1/2\"",
          parse_dimension("five feet and one half"))
    check(parse_dimension("eight inches") == "8\"")
    check(parse_dimension("eight and one quarter inches") == "8 1/4\"")
    check(parse_dimension("three quarters") == "3/4\"")
    check(parse_dimension("one hundred feet") == "100'-0\"")
    # a bare integer is inches
    check(parse_dimension("five") == "5\"")
    # non-measures fall through
    check(parse_dimension("hello world") is None)
    check(parse_dimension("") is None)


def test_profiles():
    # every profile formats the SAME measure differently
    m = "twelve point five feet"                     # 12'-6" = 150"
    check(parse_dimension(m, "arch") == "12'-6\"")
    check(parse_dimension(m, "arch_space") == "12' 6\"")
    check(parse_dimension(m, "arch_nohyphen") == "12'6\"")
    check(parse_dimension(m, "decimal_ft") == "12.5'",
          parse_dimension(m, "decimal_ft"))
    check(parse_dimension(m, "decimal_in") == "150\"",
          parse_dimension(m, "decimal_in"))
    check(parse_dimension(m, "mm") == "3810 mm", parse_dimension(m, "mm"))
    outs = {parse_dimension(m, p) for p in
            ("arch", "arch_space", "arch_nohyphen", "decimal_ft",
             "decimal_in", "mm")}
    check(len(outs) == 6, outs)                       # all distinct
    # the documented decimal-feet example
    check(parse_dimension("one hundred five feet six and seven eighths",
                          "decimal_ft") == "105.573'")
    # profile registry shape
    for key in ("arch", "arch_space", "arch_nohyphen", "decimal_ft",
                "decimal_in", "mm", "custom"):
        check(key in PROFILES, key)
        check("desc" in PROFILES[key], key)
    # unknown profile falls back to arch, never raises
    check(parse_dimension(m, "does_not_exist") == "12'-6\"")


def test_format_ftin_and_custom():
    check(format_ftin(105, 6, 7, 8, "arch") == "105'-6 7/8\"")
    check(format_ftin(2, 7, 7, 8, "arch") == "2'-7 7/8\"")
    check(format_ftin(12, 0, 0, 1, "arch") == "12'-0\"")
    check(format_ftin(0, 6, 0, 1, "decimal_ft") == "0.5'")
    # the custom template hook (over {feet}{inches}{frac}{num}{den}{total_*})
    saved = PROFILES["custom"]["template"]
    try:
        PROFILES["custom"]["template"] = "{total_ft} ft"
        check(format_ftin(105, 6, 7, 8, "custom") == "105.573 ft",
              format_ftin(105, 6, 7, 8, "custom"))
        PROFILES["custom"]["template"] = "{feet}'-{inches}{frac}\""
        check(format_ftin(2, 7, 7, 8, "custom") == "2'-7 7/8\"")
    finally:
        PROFILES["custom"]["template"] = saved


# ------------------------------------------------------- the Caller: shapes --

def test_parse_shape():
    check(parse_shape("L two and one half by two and one half by one quarter")
          == "L2 1/2x2 1/2x1/4",
          parse_shape("L two and one half by two and one half by one quarter"))
    check(parse_shape("angle two by two by one quarter") == "L2x2x1/4")
    check(parse_shape("channel twelve by thirty") == "C12x30")
    check(parse_shape("plate one half") == "PL1/2")
    check(parse_shape("wide flange twelve by fourteen") == "W12x14")
    check(parse_shape("double u twelve by fourteen") == "W12x14")
    check(parse_shape("h s s four by four by one quarter") == "HSS4x4x1/4",
          parse_shape("h s s four by four by one quarter"))
    check(parse_shape("tube six by six") == "HSS6x6")
    check(parse_shape("w twelve by twenty six") == "W12x26")
    # no leading shape word -> None (falls through to a dimension)
    check(parse_shape("six and seven eighths") is None)
    check(parse_shape("two feet") is None)
    check(parse_shape("l") is None)                   # a shape needs a size


def test_speak_to_text():
    r = speak_to_text("L two by two by one quarter")
    check(r == {"kind": "shape", "text": "L2x2x1/4"}, r)
    r = speak_to_text("two feet seven and seven eighths")
    check(r == {"kind": "dimension", "text": "2'-7 7/8\""}, r)
    r = speak_to_text("six and seven eighths")
    check(r == {"kind": "dimension", "text": "6 7/8\""}, r)
    r = speak_to_text("hello world")
    check(r == {"kind": None, "text": None}, r)
    # profile flows through to a dimension
    r = speak_to_text("twelve point five feet", profile="decimal_ft")
    check(r == {"kind": "dimension", "text": "12.5'"}, r)


# ----------------------------------------------------------- the Songbook ----

def test_entry_roundtrip():
    e = Entry("column rotate", "run", "",
              steps=[["type", "e"], ["wait", "1.0"], ["key", "Tab"]],
              note="macro", enabled=True)
    d = e.to_dict()
    check(set(d) == {"trigger", "kind", "payload", "steps", "is_url", "note",
                     "enabled"}, d)
    e2 = Entry.from_dict(d)
    check(e2 == e, (e, e2))
    # a URL fetch row survives the trip
    f = Entry("docs", "fetch", "http://x", is_url=True)
    check(Entry.from_dict(f.to_dict()).is_url is True)


def test_songbook_add_find():
    sb = Songbook()
    sb.add(Entry("line", "trip", "l+Enter"))
    sb.add(Entry("issued for construction", "placard", "ISSUED FOR "
                                                       "CONSTRUCTION"))
    # exact (normalized) match
    check(sb.find("line").payload == "l+Enter")
    check(sb.find("LINE").payload == "l+Enter")       # case-folded
    check(sb.find("  line  ").payload == "l+Enter")   # whitespace collapsed
    check(sb.find("issued for construction").kind == "placard")
    # word-boundary startswith fallback (trailing filler is fine)
    check(sb.find("issued for construction now please").kind == "placard",
          "startswith fallback")
    # a shorter fragment does NOT partial-fire
    check(sb.find("issued") is None)
    check(sb.find("nope") is None)
    # longest prefix wins
    sb.add(Entry("zoom", "trip", "z"))
    sb.add(Entry("zoom fit", "trip", "z+f+Enter"))
    check(sb.find("zoom fit window").payload == "z+f+Enter",
          sb.find("zoom fit window"))
    # add replaces a same-trigger row; remove drops it
    sb.add(Entry("line", "trip", "pl+Enter"))
    check(sb.find("line").payload == "pl+Enter")
    check(sb.remove("line") is True)
    check(sb.find("line") is None)
    check(sb.remove("line") is False)
    # disabled rows never match
    sb.add(Entry("hidden", "placard", "X", enabled=False))
    check(sb.find("hidden") is None)


def test_songbook_json_roundtrip():
    sb = Songbook.seed()
    p = os.path.join(TMP, "songbook.json")
    sb.save(p)
    check(os.path.exists(p) and not os.path.exists(p + ".part"))
    raw = json.load(open(p, encoding="utf-8"))
    check(raw.get("planloom_holler") == 1)
    check(len(raw["entries"]) == len(SEED_SONGBOOK))
    sb2 = Songbook().load(p)
    check([e.trigger for e in sb2.entries]
          == [e.trigger for e in sb.entries])
    run = sb2.find("column rotate")
    check(run.kind == "run" and run.steps[0] == ["type", "e"], run.steps)
    check(run.steps[3] == ["type", "90"])
    # a corrupt / missing file loads empty, never raises
    bad = os.path.join(TMP, "bad.json")
    open(bad, "w").write("{not json")
    check(Songbook().load(bad).entries == [])
    check(Songbook().load(os.path.join(TMP, "nope.json")).entries == [])


def test_songbook_csv_roundtrip():
    sb = Songbook.seed()
    p = os.path.join(TMP, "songbook.csv")
    sb.to_csv(p)
    text = open(p, encoding="utf-8").read()
    # header columns and the exact step serialization
    check(text.splitlines()[0].split(",")[:2] == ["trigger", "kind"])
    check("type:e | wait:1.0 | key:Tab | type:90 | key:Enter" in text, text)
    sb2 = Songbook().from_csv(p)
    check(len(sb2.entries) == len(sb.entries))
    a = sb2.find("column rotate")
    check(a.kind == "run", a.kind)
    check(a.steps == [["type", "e"], ["wait", "1.0"], ["key", "Tab"],
                      ["type", "90"], ["key", "Enter"]], a.steps)
    check(sb2.find("issued for construction").payload
          == "ISSUED FOR CONSTRUCTION")
    check(sb2.find("copy").payload == "ctrl+c")
    check(CSV_COLUMNS == ["trigger", "kind", "payload", "steps", "is_url",
                          "note", "enabled"])


def test_seed_songbook():
    sb = Songbook.seed()
    kinds = {e.kind for e in sb.entries}
    check(kinds == {"trip", "placard", "fetch", "run"}, kinds)
    check(sb.find("line").payload == "l+Enter")
    check(sb.find("copy").payload == "ctrl+c")
    check(sb.find("paste").payload == "ctrl+v")
    check(sb.find("issued for approval").payload == "ISSUED FOR APPROVAL")
    # NO url rows ship by default (offline-honest)
    check(all(e.is_url is False for e in sb.entries))
    # seeds are independent objects per Songbook (no aliasing)
    a = Songbook.seed()
    b = Songbook.seed()
    a.find("line").payload = "changed"
    check(b.find("line").payload == "l+Enter")
    check(default_path().endswith(os.path.join(".planloom", "holler",
                                               "songbook.json")))


# --------------------------------------------------------------- the Sender --

def test_has_send_honesty():
    # this box is Linux: keystroke injection must be honestly OFF
    check(sys.platform != "win32", "suite calibrates the non-win path")
    check(HAS_SEND is False)
    check(holler._USER32 is None)


def test_type_and_keys():
    check(type_text("abc") == [("char", "a"), ("char", "b"), ("char", "c")])
    check(type_text("90") == [("char", "9"), ("char", "0")])
    check(type_text("") == [])
    check(tap_key("Enter") == [("key", "enter")])     # name normalized
    check(tap_key("Tab") == [("key", "tab")])
    check(tap_key("F5") == [("key", "f5")])
    # chord: mods down, key, mods up (release reversed)
    check(chord(["ctrl"], "c") == [("down", "ctrl"), ("key", "c"),
                                   ("up", "ctrl")])
    check(chord(["ctrl", "shift"], "s")
          == [("down", "ctrl"), ("down", "shift"), ("key", "s"),
              ("up", "shift"), ("up", "ctrl")], chord(["ctrl", "shift"], "s"))


def test_key_specs_and_trips():
    check(parse_key_spec("ctrl+c") == (["ctrl"], "c"))
    check(parse_key_spec("l+Enter") == (["l"], "enter"), parse_key_spec(
        "l+Enter"))
    check(parse_key_spec("ctrl+shift+s") == (["ctrl", "shift"], "s"))
    check(parse_key_spec("escape") == ([], "escape"))
    # apply_trip: a real modifier chord
    check(apply_trip("ctrl+c") == [("down", "ctrl"), ("key", "c"),
                                   ("up", "ctrl")])
    # apply_trip: the "type then Enter" shortcut form (l is not a modifier)
    check(apply_trip("l+Enter") == [("char", "l"), ("key", "enter")],
          apply_trip("l+Enter"))
    # apply_trip: a lone named key taps; a lone word types
    check(apply_trip("Enter") == [("key", "enter")])
    check(apply_trip("hi") == [("char", "h"), ("char", "i")])
    check(apply_trip("ctrl+shift+s")
          == [("down", "ctrl"), ("down", "shift"), ("key", "s"),
              ("up", "shift"), ("up", "ctrl")])


def test_run_steps():
    steps = [["type", "e"], ["wait", "1.0"], ["key", "Tab"],
             ["type", "90"], ["key", "Enter"]]
    got = run_steps(steps, dry=True)
    check(got == [("char", "e"), ("wait", 1.0), ("key", "tab"),
                  ("char", "9"), ("char", "0"), ("key", "enter")], got)
    # dry run does NOT sleep
    naps = []
    run_steps(steps, dry=True, sleep=naps.append)
    check(naps == [], naps)
    # a real (non-dry) run honors the wait regardless of HAS_SEND
    naps = []
    run_steps(steps, dry=False, sleep=naps.append)
    check(naps == [1.0], naps)
    # a chord step routes through apply_trip
    got = run_steps([["chord", "ctrl+c"]], dry=True)
    check(got == [("down", "ctrl"), ("key", "c"), ("up", "ctrl")], got)
    # unknown verbs are skipped, empty list is fine
    check(run_steps([["bogus", "x"]], dry=True) == [])
    check(run_steps([], dry=True) == [])


def test_open_target():
    # a real local directory opens (dry on this box: recorded, not spawned)
    res = open_target(TMP)
    check(res["opened"] is True and res["is_url"] is False, res)
    check("opened" in res["note"])
    # a missing local path -> opened False + honest note, never raises
    res = open_target(os.path.join(TMP, "no", "such", "place"))
    check(res["opened"] is False, res)
    check("no such" in res["note"])
    # a URL target is flagged and the note disclaims a Planloom connection
    res = open_target("http://example.com", is_url=True)
    check(res["is_url"] is True and res["opened"] is True, res)
    check("browser" in res["note"] and "no network connection" in res["note"],
          res["note"])
    # url auto-detected from the scheme even without the flag
    check(open_target("www.example.com")["is_url"] is True)


# ---------------------------------------------------------------- the Router --

def test_dispatch_precedence():
    h = Holler()
    # a Songbook trip
    r = h.dispatch("line")
    check(r["matched"] == "trip", r)
    check(r["intents"] == [("char", "l"), ("key", "enter")], r["intents"])
    check(r["detail"] == "l+Enter")
    check(r["keystrokes_saved"] == 1, r)              # 2 keys - 1
    # a Songbook placard (exact literal text)
    r = h.dispatch("issued for construction")
    check(r["matched"] == "placard")
    check(r["detail"] == "ISSUED FOR CONSTRUCTION")
    check(r["keystrokes_saved"] == len("ISSUED FOR CONSTRUCTION") - 1, r)
    # a Songbook chord trip -> saved floors at 0 (one keystroke)
    r = h.dispatch("copy")
    check(r["intents"] == [("down", "ctrl"), ("key", "c"), ("up", "ctrl")])
    check(r["keystrokes_saved"] == 0, r)
    # a fetch
    r = h.dispatch("project folder")
    check(r["matched"] == "fetch" and r["intents"] == [])
    check("opened" in r["note"])
    # a run macro
    r = h.dispatch("column rotate")
    check(r["matched"] == "run", r)
    check(("char", "e") in r["intents"] and ("key", "tab") in r["intents"])
    # the Caller: a spoken dimension typed as text
    r = h.dispatch("two feet seven and seven eighths")
    check(r["matched"] == "dimension", r)
    check(r["detail"] == "2'-7 7/8\"")
    check(r["intents"] == [("char", c) for c in "2'-7 7/8\""], r["intents"])
    check(r["keystrokes_saved"] == len("2'-7 7/8\"") - 1)
    # the Caller: a spoken shape
    r = h.dispatch("angle two by two by one quarter")
    check(r["matched"] == "shape" and r["detail"] == "L2x2x1/4")
    # gibberish -> a miss, nothing sent
    r = h.dispatch("asdf qwer zxcv")
    check(r["matched"] == "miss", r)
    check(r["intents"] == [] and r["keystrokes_saved"] == 0)
    check(r["note"] == "")


def test_dispatch_songbook_beats_caller():
    # a trigger that also parses as a number resolves to the Songbook
    h = Holler()
    h.songbook.add(Entry("five", "placard", "FIVE"))
    r = h.dispatch("five")
    check(r["matched"] == "placard" and r["detail"] == "FIVE", r)


def test_profile_switch():
    h = Holler(profile="arch")
    check(h.dispatch("twelve point five feet")["detail"] == "12'-6\"")
    h.set_profile("decimal_ft")
    check(h.dispatch("twelve point five feet")["detail"] == "12.5'")


def test_reload_songbook():
    p = os.path.join(TMP, "reload.json")
    sb = Songbook.seed(path=p)
    sb.save(p)
    h = Holler(songbook=sb)
    sb.add(Entry("extra", "placard", "X"))
    check(h.songbook.find("extra") is not None)
    h.reload_songbook()                               # reverts to disk
    check(h.songbook.find("extra") is None)
    check(h.songbook.find("line") is not None)


# ---------------------------------------------------------------- the Ticker --

def test_ticker():
    tk = Ticker()
    for u, m, saved in [("line", "trip", 1), ("copy", "trip", 0),
                        ("two feet", "dimension", 3)]:
        tk.record({"heard": u, "matched": m, "detail": u,
                   "keystrokes_saved": saved}, ts="2026-07-09T10:00:00")
    s = tk.summary()
    check(s["commands"] == 3, s)
    check(s["keystrokes_saved"] == 4, s)
    check(len(s["recent"]) == 3)
    check(s["recent"][0]["heard"] == "line")
    check(s["recent"][0]["ts"] == "2026-07-09T10:00:00")
    # a miss is logged but NOT counted as a command
    tk.record({"heard": "junk", "matched": "miss", "keystrokes_saved": 0})
    s = tk.summary()
    check(s["commands"] == 3 and len(s["recent"]) == 4, s)
    # reset zeroes everything
    tk.reset()
    check(tk.summary() == {"commands": 0, "keystrokes_saved": 0,
                           "recent": []})


def test_ticker_persistence():
    p = os.path.join(TMP, "ticker.json")
    tk = Ticker()
    tk.record({"heard": "line", "matched": "trip", "detail": "l",
               "keystrokes_saved": 5}, ts="2026-07-09T09:00:00")
    tk.save(p)
    check(os.path.exists(p) and not os.path.exists(p + ".part"))
    tk2 = Ticker().load(p)
    check(tk2.commands == 1 and tk2.keystrokes_saved == 5, tk2.summary())
    check(tk2.history[0].heard == "line")
    # missing / corrupt file -> zeros, no raise
    check(Ticker().load(os.path.join(TMP, "nope.json")).commands == 0)


def test_dispatch_records_to_ticker():
    h = Holler()
    h.dispatch("line", ts="t1")
    h.dispatch("copy", ts="t2")
    h.dispatch("junk junk", ts="t3")
    s = h.ticker.summary()
    check(s["commands"] == 2, s)                      # the miss is not counted
    check(len(s["recent"]) == 3)
    check(s["recent"][-1]["matched"] == "miss")
    check(s["recent"][0]["ts"] == "t1")


def test_tick_dataclass():
    t = Tick("heard", "trip", "detail", 4, ts="ts")
    d = t.to_dict()
    check(d == {"heard": "heard", "matched": "trip", "detail": "detail",
                "saved": 4, "ts": "ts"}, d)
    check(Tick.from_dict(d) == t)


# ------------------------------------------------------------- the Corral -----

def test_corral_by_construction():
    """Holler obeys the standing rules: no networking, engine free of gui,
    no eval/exec (CLAUDE.md invariant 1 — offline always)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    banned = re.compile(
        r"^\s*(?:import|from)\s+(?:socket|ssl|urllib|http|requests"
        r"|xmlrpc|ftplib|smtplib)\b", re.MULTILINE)
    eng = open(os.path.join(root, "rfi_stamper", "holler.py"),
               encoding="utf-8").read()
    check(not banned.search(eng), "networking import in holler.py")
    check("tkinter" not in eng and "rfi_stamper.gui" not in eng)
    check(not re.search(r"\beval\s*\(|\bexec\s*\(", eng), "no eval/exec")
    # the honesty flag and its explanation are present
    check("HAS_SEND" in eng and "DRY" in eng)


def main():
    tests = [
        (test_parse_number, "Caller number parser (units/tens/hundred/"
                             "thousand/fraction/decimal)"),
        (test_mixed, "mixed-fraction formatter (reduce/drop-zero/int/neg)"),
        (test_parse_dimension_owner_examples,
         "Caller dimensions — owner examples VERBATIM"),
        (test_parse_dimension_general, "Caller dimensions — general grammar"),
        (test_profiles, "PROFILES each format the same measure differently"),
        (test_format_ftin_and_custom, "format_ftin + the custom template hook"),
        (test_parse_shape, "Caller shape calls (prefix + by-joined groups)"),
        (test_speak_to_text, "speak_to_text routing (shape/dimension/None)"),
        (test_entry_roundtrip, "Entry to_dict/from_dict round trip"),
        (test_songbook_add_find, "Songbook add/find (exact + startswith)"),
        (test_songbook_json_roundtrip, "Songbook JSON save/load round trip"),
        (test_songbook_csv_roundtrip, "Songbook CSV round trip incl. steps"),
        (test_seed_songbook, "seed Songbook (every kind, no URL rows)"),
        (test_has_send_honesty, "Sender HAS_SEND honest on this platform"),
        (test_type_and_keys, "type_text / tap_key / chord intents"),
        (test_key_specs_and_trips, "parse_key_spec + apply_trip forms"),
        (test_run_steps, "run_steps executes the owner macro (dry, no sleep)"),
        (test_open_target, "open_target dir / missing / URL (offline-honest)"),
        (test_dispatch_precedence, "Router dispatch precedence + saved math"),
        (test_dispatch_songbook_beats_caller,
         "Router: Songbook beats a numeric-looking Caller match"),
        (test_profile_switch, "Router set_profile changes Caller output"),
        (test_reload_songbook, "Router reload_songbook reverts to disk"),
        (test_ticker, "Ticker record/summary/reset (+ miss not counted)"),
        (test_ticker_persistence, "Ticker save/load lifetime totals"),
        (test_dispatch_records_to_ticker, "dispatch appends to the Ticker"),
        (test_tick_dataclass, "Tick dataclass round trip"),
        (test_corral_by_construction,
         "Corral by construction (no network/gui/eval; honest flags)"),
    ]
    for fn, label in tests:
        fn()
        print("PASS", label)
    print(f"HOLLER ENGINE TEST PASSED ({_N[0]} checks)")
    print("outputs in", TMP)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("HOLLER TEST FAILED:", e)
        sys.exit(1)
