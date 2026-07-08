"""Self-contained tests for rfi_stamper.weaver — the Weaver, Planloom's
typed-command drafting agent.  Plain python, no pytest, no project data.
Exercises:

* the command() return contract (done/ask/refused shapes, pending frames)
* the owner's day-one commands VERBATIM: run/slope/cap/replace
* a corpus of 90+ phrasings: system + fixture synonyms, number words,
  slope phrasings, ft-in coordinates, grid addresses
* target resolution: selection, "the wc" (nearest / only / ask), "the
  main" (largest diameter), open ends, grid intersections, entity ids
* ask -> answer -> done round trips (ambiguity and missing-slot flows)
* Manhattan routing (horizontal-first L) vs "straight"
* one-undo-per-command batching (a compound run+tie+slope reverts whole)
* refusals: out-of-trade commands never touch the model
* say-string formatting (real feet-inches numbers from fmt_ftin)
* optional heartwood learning: lane-1 phrase memory, thesaurus expansion,
  clarification-taught synonym PROPOSALS (unverified — never auto-added)

Run:  python3 tests/test_weaver.py
"""
import json
import math
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper.draft import DraftModel, fmt_ftin          # noqa: E402
from rfi_stamper.weaver import (                            # noqa: E402
    FRAME_EXAMPLES, Weaver, _extract_slope, _size_value, _slope_value)

TMP = tempfile.mkdtemp(prefix="weaver_test_")

#: every distinct phrasing the suite pushed through command().
PHRASES: set = set()


def C(w, text, **ctx):
    PHRASES.add(text)
    return w.command(text, context=ctx or None)


def keys_ok(r, status):
    base = {"status", "say", "question", "options", "changed", "ents",
            "warnings"}
    assert base <= set(r), r
    assert r["status"] == status, (status, r)
    assert isinstance(r["say"], str) and r["say"], r
    assert isinstance(r["changed"], int)
    assert isinstance(r["ents"], list)
    assert isinstance(r["warnings"], list)
    if status == "ask":
        assert r["question"], r
        assert isinstance(r.get("pending"), dict), "ask must carry pending"
    else:
        assert r["question"] is None
    return r


# ----------------------------------------------------------- the contract --

def test_contract():
    m = DraftModel()
    w = Weaver(m)
    keys_ok(C(w, "tally"), "done")
    keys_ok(C(w, "sing me a song"), "refused")
    r = keys_ok(C(w, "draw"), "ask")
    # the pending frame is opaque but must round-trip through the caller
    r2 = keys_ok(C(w, "a wall from 0,0 to 10,0", pending=r), "done")
    assert r2["changed"] == 1 and len(r2["ents"]) == 1
    assert m.entity(r2["ents"][0]).kind == "wall"


# --------------------------------------------- the owner's day-one commands --

def day_one_model():
    m = DraftModel()
    m.add("fixture", [(5, 10)], stencil="wc")
    main = m.add("pipe", [(0, 0), (40, 0)], dia_in=4.0)      # the 4" san main
    return m, main


def test_owner_run():
    m, main = day_one_model()
    w = Weaver(m)
    r = keys_ok(C(w, 'run 4" sanitary from the wc to the main '
                     "at 1/8 per foot"), "done")
    runs = [e for e in m.ents if e.kind == "pipe" and e.id != main.id]
    assert len(runs) == 1
    run = runs[0]
    # a new san run exists, from the wc, tied into the main
    assert run.props["system"] == "san"
    assert abs(run.props["dia_in"] - 4.0) < 1e-9
    assert run.pts[0] == (5.0, 10.0)                # starts at the wc
    assert run.pts[-1] == (5.0, 0.0)                # lands on the main
    # connected: the tie point became a real vertex of the main
    assert (5.0, 0.0) in m.entity(main.id).pts
    from rfi_stamper.pipewright import derive_fittings, network
    net = network(m)
    j = net.nodes[net.node_near(5, 0, 0.1)]
    assert j.degree == 3, "not connected at the main"
    # Manhattan: every segment axis-aligned
    for a, b in zip(run.pts, run.pts[1:]):
        assert abs(a[0] - b[0]) < 1e-9 or abs(a[1] - b[1]) < 1e-9
    # sloped
    assert abs(run.props["slope_in_ft"] - 0.125) < 1e-12
    # the say mentions size, system, slope AND the fall (10 ft @ 1/8"/ft)
    assert "Ran 10'-0\"" in r["say"], r["say"]
    assert '4"' in r["say"] and "sanitary" in r["say"]
    assert '1/8"/ft' in r["say"]
    assert "0'-1 1/4\"" in r["say"], r["say"]       # the IE drop
    # derived fittings named out loud (san 90° branch with slope -> combo)
    assert "combo" in r["say"]
    fits = [f.kind for f in derive_fittings(m)
            if f.node_xy == (5.0, 0.0)]
    assert fits == ["combo"], fits
    # created + modified ids reported
    assert run.id in r["ents"] and main.id in r["ents"]
    assert r["changed"] == len(r["ents"]) == 2


def test_owner_slope_this():
    m, main = day_one_model()
    w = Weaver(m)
    r = keys_ok(C(w, "slope this run at 1/4", selection=[main.id]), "done")
    assert abs(m.entity(main.id).props["slope_in_ft"] - 0.25) < 1e-12
    assert '1/4"/ft' in r["say"]
    # 40 ft at 1/4"/ft falls 10"
    assert "0'-10\"" in r["say"], r["say"]
    assert main.id in r["ents"]


def test_owner_cap():
    m, main = day_one_model()
    w = Weaver(m)
    r = keys_ok(C(w, "cap the open ends"), "done")
    assert r["changed"] == 2, r["say"]              # both ends of the main
    assert "Capped 2 open end(s)" in r["say"]
    from rfi_stamper.pipewright import derive_fittings
    kinds = [f.kind for f in derive_fittings(m)]
    assert kinds.count("cap") == 2 and "open" not in kinds
    # idempotent through the Weaver too
    r2 = keys_ok(C(w, "cap open ends"), "done")
    assert r2["changed"] == 0


def test_owner_replace():
    m = DraftModel()
    m.add("pipe", [(0, 0), (10, 0), (20, 0)])
    m.add("pipe", [(18, 8), (10, 0)])               # 45° branch -> wye
    w = Weaver(m)
    from rfi_stamper.pipewright import derive_fittings
    assert [f.kind for f in derive_fittings(m)
            if f.node_xy == (10.0, 0.0)] == ["wye"]
    r = keys_ok(C(w, "replace that wye with a combo",
                  last_point=(10, 1)), "done")
    assert "combo" in r["say"]
    f = [f for f in derive_fittings(m) if f.node_xy == (10.0, 0.0)][0]
    assert f.kind == "combo" and f.note == "user override"
    assert r["changed"] == 1 and r["ents"]


# -------------------------------------------------------------- pipe flows --

def test_pipe_missing_to_flow():
    m, main = day_one_model()
    w = Weaver(m)
    r = keys_ok(C(w, "run pipe from the wc"), "ask")
    assert "where to" in r["question"].lower()
    r2 = keys_ok(C(w, "to the main", pending=r), "done")
    runs = [e for e in m.ents if e.kind == "pipe" and e.id != main.id]
    assert len(runs) == 1 and runs[0].pts[-1] == (5.0, 0.0)
    assert "Ran" in r2["say"]


def test_pipe_systems_and_sizes():
    m = DraftModel()
    w = Weaver(m)
    corpus = [
        ('run 4" sanitary from 0,1 to 20,1', "san", 4.0),
        ("run four inch waste from 0,2 to 20,2", "san", 4.0),
        ("run sewer from 0,3 to 20,3", "san", 4.0),
        ("run soil from 0,4 to 20,4", "san", 4.0),
        ('run 2" vent from 0,5 to 20,5', "vent", 2.0),
        ("run storm from 0,6 to 20,6", "storm", 4.0),
        ("run roof drain from 0,7 to 20,7", "storm", 4.0),
        ('run 1" cold water from 0,8 to 20,8', "dcw", 1.0),
        ("run cw from 0,9 to 20,9", "dcw", 1.0),
        ('run 3/4" hot water from 0,10 to 20,10', "dhw", 0.75),
        ("run hw from 0,11 to 20,11", "dhw", 0.75),
        ("run gas from 0,12 to 20,12", "gas", 1.0),
        ("run fuel gas from 0,13 to 20,13", "gas", 1.0),
        ('run 1 1/2" waste from 0,14 to 20,14', "san", 1.5),
        ("run domestic cold water from 0,15 to 20,15", "dcw", 1.0),
    ]
    for phrase, system, dia in corpus:
        r = keys_ok(C(w, phrase), "done")
        e = m.ents[-1]
        assert e.kind == "pipe" and e.props["system"] == system, phrase
        assert abs(e.props["dia_in"] - dia) < 1e-9, (phrase, e.props)
        assert "Ran 20'-0\"" in r["say"], (phrase, r["say"])


def test_pipe_routing():
    m = DraftModel()
    w = Weaver(m)
    # Manhattan: two-segment L, horizontal leg first
    keys_ok(C(w, "run pipe from 0,0 to 10,8"), "done")
    assert m.ents[-1].pts == [(0.0, 0.0), (10.0, 0.0), (10.0, 8.0)]
    # straight when said
    keys_ok(C(w, "run pipe straight from 0,20 to 10,28"), "done")
    assert m.ents[-1].pts == [(0.0, 20.0), (10.0, 28.0)]
    # aligned points go straight anyway
    keys_ok(C(w, "run pipe from 0,40 to 20,40"), "done")
    assert m.ents[-1].pts == [(0.0, 40.0), (20.0, 40.0)]
    # feet-inches coordinates parse
    keys_ok(C(w, "run pipe from 0,50 to 22'-6\",50"), "done")
    assert m.ents[-1].pts[-1] == (22.5, 50.0)
    # zero-length refusal: never a degenerate run
    n = len(m.ents)
    keys_ok(C(w, "run pipe from 5,5 to 5,5"), "refused")
    assert len(m.ents) == n
    # under-minimum slope WARNS, never blocks (engine passthrough)
    r = keys_ok(C(w, 'run 3" sanitary from 0,60 to 20,60 at 1/16 per foot'),
                "done")
    assert any("minimum" in x for x in r["warnings"]), r["warnings"]


def test_pipe_grid_refs():
    m = DraftModel()
    m.add("grid", [(10, -2), (10, 30)], label="1", bubble="both")
    m.add("grid", [(25, -2), (25, 30)], label="2", bubble="both")
    m.add("grid", [(-2, 5), (30, 5)], label="A", bubble="both")
    m.add("grid", [(-2, 20), (30, 20)], label="B", bubble="both")
    w = Weaver(m)
    keys_ok(C(w, "run pipe from a-1 to b-2"), "done")
    e = m.ents[-1]
    assert e.pts[0] == (10.0, 5.0) and e.pts[-1] == (25.0, 20.0)
    assert len(e.pts) == 3                           # Manhattan L
    # a grid address that does not exist is refused, never guessed
    n = len(m.ents)
    r = keys_ok(C(w, "run pipe from a-1 to c-9"), "refused")
    assert "C-9" in r["say"] and len(m.ents) == n


def test_connect():
    m = DraftModel()
    lav = m.add("fixture", [(3, 6)], stencil="lav")
    wc = m.add("fixture", [(12, 6)], stencil="wc")
    w = Weaver(m)
    r = keys_ok(C(w, "connect the lav to the wc"), "done")
    e = m.ents[-1]
    assert e.kind == "pipe"
    assert e.pts[0] == (3.0, 6.0) and e.pts[-1] == (12.0, 6.0)
    assert "Ran 9'-0\"" in r["say"]
    # connect can size and tie into a run too (6" = the main by diameter)
    main = m.add("pipe", [(0, 0), (30, 0)], dia_in=6.0)
    r = keys_ok(C(w, 'connect the lav to the main with 2" waste'), "done")
    e = m.ents[-1]
    assert abs(e.props["dia_in"] - 2.0) < 1e-9 and e.props["system"] == "san"
    assert (3.0, 0.0) in m.entity(main.id).pts       # vertex inserted
    assert lav is not None and wc is not None


# ------------------------------------------------------------------- walls --

def test_walls():
    m = DraftModel()
    w = Weaver(m)
    r = keys_ok(C(w, "draw a wall from 0,0 to 22'-6\",0"), "done")
    e = m.ents[-1]
    assert e.kind == "wall" and e.pts == [(0.0, 0.0), (22.5, 0.0)]
    assert e.props["wtype"] == "stud4"
    assert "22'-6\"" in r["say"]
    # chained walls, typed assembly, ONE undo for the whole chain
    r = keys_ok(C(w, "draw a cmu wall from 0,12 to 20,12 then to 20,24"),
                "done")
    assert r["changed"] == 2 and len(r["ents"]) == 2
    walls = [e for e in m.ents if e.kind == "wall"]
    assert len(walls) == 3
    assert all(m.entity(i).props["wtype"] == "cmu8" for i in r["ents"])
    assert m.undo()
    assert len([e for e in m.ents if e.kind == "wall"]) == 1
    # wall types by label fragment + size
    keys_ok(C(w, "draw a 6 inch stud wall from 0,30 to 10,30"), "done")
    assert m.ents[-1].props["wtype"] == "stud6"
    keys_ok(C(w, "draw a 12 block wall from 0,40 to 10,40"), "done")
    assert m.ents[-1].props["wtype"] == "cmu12"
    keys_ok(C(w, "draw a concrete wall from 0,50 to 10,50"), "done")
    assert m.ents[-1].props["wtype"] == "conc8"
    # missing points -> one question -> answered
    r = keys_ok(C(w, "draw a wall"), "ask")
    assert "where" in r["question"].lower()
    r2 = keys_ok(C(w, "from 5,60 to 15,60", pending=r), "done")
    assert m.ents[-1].pts == [(5.0, 60.0), (15.0, 60.0)]
    assert r2["changed"] == 1


# ---------------------------------------------------------------- fixtures --

def test_fixtures():
    m = DraftModel()
    m.add("grid", [(10, -2), (10, 30)], label="2", bubble="both")
    m.add("grid", [(-2, 20), (30, 20)], label="B", bubble="both")
    w = Weaver(m)
    # coordinates, grid address, synonyms
    keys_ok(C(w, "add a wc at 4, 4"), "done")
    assert m.ents[-1].props["stencil"] == "wc"
    keys_ok(C(w, "add a toilet at 6, 4"), "done")   # slang -> wc
    assert m.ents[-1].props["stencil"] == "wc"
    r = keys_ok(C(w, "add a lav at b-2"), "done")
    assert m.ents[-1].props["stencil"] == "lav"
    assert m.ents[-1].pts == [(10.0, 20.0)]
    assert "10'-0\"" in r["say"] and "20'-0\"" in r["say"]
    corpus = [
        ("place a water closet at 0, 8", "wc"),
        ("add a commode at 2, 8", "wc"),
        ("add a lavatory at 4, 8", "lav"),
        ("add a sink at 6, 8", "sink_s"),
        ("add a double sink at 8, 8", "sink_d"),
        ("add a urinal at 10, 8", "ur"),
        ("add a floor drain at 12, 8", "fd"),
        ("add a water heater at 14, 8", "wh"),
        ("add a drinking fountain at 16, 8", "df"),
        ("add a hose bibb at 18, 8", "hb"),
        ("add a shower at 20, 8", "shower"),
        ("add a bathtub at 24, 8", "tub"),
        ("add a mop sink at 28, 8", "mop"),
        ("put a cleanout at 30, 8", "co"),
    ]
    for phrase, key in corpus:
        keys_ok(C(w, phrase), "done")
        assert m.ents[-1].props["stencil"] == key, (phrase, m.ents[-1].props)
    # missing location -> one question -> answered with coordinates
    r = keys_ok(C(w, "add a urinal"), "ask")
    assert "where" in r["question"].lower()
    r2 = keys_ok(C(w, "3, 15", pending=r), "done")
    assert m.ents[-1].pts == [(3.0, 15.0)]
    assert r2 is not None


def test_ambiguity_roundtrip():
    m = DraftModel()
    a = m.add("fixture", [(0, 0)], stencil="wc")
    b = m.add("fixture", [(30, 0)], stencil="wc")
    w = Weaver(m)
    # two equally-good candidates, no last point -> ask with options
    r = keys_ok(C(w, "delete the wc"), "ask")
    assert r["options"] and len(r["options"]) == 2
    assert any(a.id in o for o in r["options"])
    # answer with the entity id -> done
    r2 = keys_ok(C(w, b.id, pending=r), "done")
    assert m.entity(b.id) is None and m.entity(a.id) is not None
    assert r2["changed"] == 1
    # ordinal answers work too
    c = m.add("fixture", [(50, 0)], stencil="wc")
    r = keys_ok(C(w, "delete the wc"), "ask")
    r2 = keys_ok(C(w, "the first", pending=r), "done")
    assert m.entity(a.id) is None and m.entity(c.id) is not None
    # a last point breaks the tie silently (nearest wins, no ask)
    m.add("fixture", [(0, 0)], stencil="wc")
    r = keys_ok(C(w, "delete the wc", last_point=(49, 1)), "done")
    assert m.entity(c.id) is None


# ------------------------------------------------------------------ slopes --

def test_slope_phrasings():
    m = DraftModel()
    run = m.add("pipe", [(0, 0), (22.5, 0)])
    w = Weaver(m)
    corpus = [
        ("slope this run at 1/8 per foot", 0.125),
        ('slope this run at 1/8"/ft', 0.125),
        ("slope this run at an eighth per foot", 0.125),
        ("slope this run at quarter inch per foot", 0.25),
        ("slope this run at a quarter per foot", 0.25),
        ("slope this run at 1/4", 0.25),
        ("slope this run at 0.125", 0.125),
        ("pitch this run at half inch per foot", 0.5),
        ("slope this run at three eighths per foot", 0.375),
    ]
    for phrase, want in corpus:
        r = keys_ok(C(w, phrase, selection=[run.id]), "done")
        assert abs(m.entity(run.id).props["slope_in_ft"] - want) < 1e-12, \
            (phrase, m.entity(run.id).props)
        assert "total fall" in r["say"]
    # the brief's arithmetic rides through: 22'-6" at 1/8"/ft
    r = keys_ok(C(w, "slope it at 1/8 per foot", selection=[run.id]),
                "done")
    assert "0'-2 13/16\"" in r["say"], r["say"]
    # a flat slope is refused by the engine and passed through honestly
    r = keys_ok(C(w, "slope this run at 0 per foot", selection=[run.id]),
                "refused")
    assert "refused" in r["say"].lower()
    # missing value -> one question -> answered
    r = keys_ok(C(w, "slope this run", selection=[run.id]), "ask")
    assert "pitch" in r["question"].lower()
    r2 = keys_ok(C(w, "1/8", pending=r, selection=[run.id]), "done")
    assert abs(m.entity(run.id).props["slope_in_ft"] - 0.125) < 1e-12
    assert r2["changed"] == 1


def test_the_main():
    m = DraftModel()
    small = m.add("pipe", [(0, 0), (20, 0)], dia_in=4.0)
    big = m.add("pipe", [(0, 5), (20, 5)], dia_in=6.0)
    w = Weaver(m)
    r = keys_ok(C(w, "slope the main at 1/4"), "done")
    assert abs(m.entity(big.id).props["slope_in_ft"] - 0.25) < 1e-12
    assert m.entity(small.id).props["slope_in_ft"] is None
    r = keys_ok(C(w, 'resize the main to 8"'), "done")
    assert abs(m.entity(big.id).props["dia_in"] - 8.0) < 1e-9
    assert abs(m.entity(small.id).props["dia_in"] - 4.0) < 1e-9
    assert '8"' in r["say"]
    # two equal largest runs is a genuine tie -> ask
    m.add("pipe", [(0, 10), (20, 10)], dia_in=8.0)
    r = keys_ok(C(w, "slope the main at 1/8"), "ask")
    assert r["options"] and len(r["options"]) == 2


# ----------------------------------------------------------------- replace --

def test_replace_flows():
    # unique open end resolves without a question
    m = DraftModel()
    m.add("fixture", [(0, 0.3)], stencil="wc")
    m.add("pipe", [(0, 0), (10, 0)])
    w = Weaver(m)
    r = keys_ok(C(w, "replace the open end with a cleanout"), "done")
    from rfi_stamper.pipewright import derive_fittings
    f = [f for f in derive_fittings(m) if f.node_xy == (10.0, 0.0)][0]
    assert f.kind == "cleanout"
    assert "cleanout" in r["say"]
    # two wyes -> ask -> ordinal answer -> done
    m2 = DraftModel()
    m2.add("pipe", [(0, 0), (10, 0), (20, 0), (30, 0)])
    m2.add("pipe", [(18, 8), (10, 0)])
    m2.add("pipe", [(28, 8), (20, 0)])
    w2 = Weaver(m2)
    r = keys_ok(C(w2, "swap that wye with a santee"), "ask")
    assert r["options"] and len(r["options"]) == 2
    r2 = keys_ok(C(w2, "first", pending=r), "done")
    f = [f for f in derive_fittings(m2) if f.node_xy == (10.0, 0.0)][0]
    assert f.kind == "santee"
    assert r2["changed"] == 1
    # missing new kind -> one question -> answered
    r = keys_ok(C(w2, "replace that wye", last_point=(20, 1)), "ask")
    assert "with what" in r["question"].lower()
    r2 = keys_ok(C(w2, "a combo", pending=r), "done")
    f = [f for f in derive_fittings(m2) if f.node_xy == (20.0, 0.0)][0]
    assert f.kind == "combo"
    # unknown fittings are refused, never guessed
    n = json.dumps([e.to_dict() for e in m2.ents])
    keys_ok(C(w2, "replace the sprocket with a wye"), "refused")
    assert json.dumps([e.to_dict() for e in m2.ents]) == n
    # nothing matching is an honest refusal
    keys_ok(C(w2, "replace the cross with a tee"), "refused")


# ------------------------------------------------------------------ resize --

def test_resize_flows():
    m = DraftModel()
    a = m.add("pipe", [(0, 0), (10, 0), (20, 0)])
    b = m.add("pipe", [(20, 0), (30, 0)])
    w = Weaver(m)
    r = keys_ok(C(w, 'resize this run to 6"', selection=[a.id]), "done")
    assert abs(m.entity(a.id).props["dia_in"] - 6.0) < 1e-9
    assert abs(m.entity(b.id).props["dia_in"] - 6.0) < 1e-9   # downstream
    assert r["changed"] == 2
    # "only" restricts to the one run
    r = keys_ok(C(w, "resize this run only to 4 inch", selection=[a.id]),
                "done")
    assert abs(m.entity(a.id).props["dia_in"] - 4.0) < 1e-9
    assert abs(m.entity(b.id).props["dia_in"] - 6.0) < 1e-9
    assert r["changed"] == 1
    # missing size -> one question -> answered ("the main" is now b at 6")
    r = keys_ok(C(w, "upsize the main"), "ask")
    assert "size" in r["question"].lower()
    r2 = keys_ok(C(w, "8 inch", pending=r), "done")
    assert abs(m.entity(b.id).props["dia_in"] - 8.0) < 1e-9
    assert abs(m.entity(a.id).props["dia_in"] - 4.0) < 1e-9
    assert r2["warnings"] == []


# ------------------------------------------------------------ move / delete --

def test_move_delete():
    m = DraftModel()
    m.add("grid", [(10, -2), (10, 30)], label="2", bubble="both")
    m.add("grid", [(-2, 20), (30, 20)], label="B", bubble="both")
    wc = m.add("fixture", [(4, 4)], stencil="wc")
    lav = m.add("fixture", [(0, 0)], stencil="lav")
    w = Weaver(m)
    r = keys_ok(C(w, "move the wc 2' north"), "done")
    assert m.entity(wc.id).pts == [(4.0, 6.0)]
    assert "2'-0\"" in r["say"] and "north" in r["say"]
    keys_ok(C(w, "move the wc two feet east"), "done")
    assert m.entity(wc.id).pts == [(6.0, 6.0)]
    keys_ok(C(w, "shift the lav to b-2"), "done")
    assert m.entity(lav.id).pts == [(10.0, 20.0)]
    # selection target + missing distance flow
    r = keys_ok(C(w, "move it south", selection=[wc.id]), "ask")
    assert "how far" in r["question"].lower()
    r2 = keys_ok(C(w, "6\"", pending=r, selection=[wc.id]), "done")
    assert m.entity(wc.id).pts == [(6.0, 5.5)]
    assert r2["changed"] == 1
    # delete by reference, by id, by selection, by plural kind
    keys_ok(C(w, "delete the lav"), "done")
    assert m.entity(lav.id) is None
    keys_ok(C(w, f"delete {wc.id}"), "done")
    assert m.entity(wc.id) is None
    tub = m.add("fixture", [(9, 9)], stencil="tub")
    keys_ok(C(w, "erase that", selection=[tub.id]), "done")
    assert m.entity(tub.id) is None
    keys_ok(C(w, "remove the grids"), "done")
    assert not [e for e in m.ents if e.kind == "grid"]
    # deleting a wall cascades its openings and says so honestly
    wall = m.add("wall", [(0, 0), (20, 0)], wtype="stud4")
    m.add("door", [], host=wall.id, t=0.5)
    r = keys_ok(C(w, "delete the wall"), "done")
    assert r["changed"] == 2, r["say"]
    assert not [e for e in m.ents if e.kind in ("wall", "door")]


# --------------------------------------------- grid / room / text / dim ----

def test_annotation_objects():
    m = DraftModel()
    w = Weaver(m)
    r = keys_ok(C(w, "add grid from 30,-2 to 30,30"), "done")
    g = m.ents[-1]
    assert g.kind == "grid" and g.props["label"] == "1"     # vertical -> num
    r = keys_ok(C(w, "add grid from -2,40 to 30,40"), "done")
    assert m.ents[-1].props["label"] == "A"                 # horizontal
    r = keys_ok(C(w, "add text VERIFY IN FIELD at 5, 5"), "done")
    t = m.ents[-1]
    assert t.kind == "text" and t.props["text"] == "VERIFY IN FIELD"
    assert "VERIFY IN FIELD" in r["say"]
    # missing content -> one question (raw case preserved on answer)
    r = keys_ok(C(w, "add a note at 6, 6"), "ask")
    r2 = keys_ok(C(w, "SEE STRUCTURAL", pending=r), "done")
    assert m.ents[-1].props["text"] == "SEE STRUCTURAL"
    r = keys_ok(C(w, "dimension from 0,0 to 20,0"), "done")
    d = m.ents[-1]
    assert d.kind == "dim" and len(d.pts) == 3
    assert "20'-0\"" in r["say"]
    r = keys_ok(C(w, "add a room named lobby 101 at 8, 8"), "done")
    rm = m.ents[-1]
    assert rm.kind == "room" and rm.props["name"] == "LOBBY"
    assert rm.props["number"] == "101"
    # missing room name -> one question
    r = keys_ok(C(w, "add a room at 12, 12"), "ask")
    r2 = keys_ok(C(w, "electrical", pending=r), "done")
    assert m.ents[-1].props["name"] == "ELECTRICAL"
    assert r2["changed"] == 1


# ---------------------------------------------------------------- refusals --

def test_refusals():
    m = DraftModel()
    m.add("pipe", [(0, 0), (10, 0)])
    w = Weaver(m)
    before = json.dumps([e.to_dict() for e in m.ents])
    for phrase in ("order me a pizza", "rm -rf", ""):
        r = keys_ok(C(w, phrase), "refused")
        assert r["changed"] == 0 and r["ents"] == []
    # the model was never touched
    assert json.dumps([e.to_dict() for e in m.ents]) == before
    # a refusal offers the 3 closest verbs and one worked example
    r = C(w, "order me a pizza")
    m_verbs = r["say"].split("Closest verbs:")[1].split(".")[0]
    assert len([v for v in m_verbs.split(",") if v.strip()]) == 3, r["say"]
    assert "Try:" in r["say"]
    assert any(ex in r["say"] for ex in FRAME_EXAMPLES.values()), r["say"]


# ------------------------------------------------------------- undo batching --

def test_one_undo_per_command():
    m, main = day_one_model()
    w = Weaver(m)
    before = json.dumps([e.to_dict() for e in m.ents])
    # the compound command: tie-in vertex + new run + slope = 3 mutations
    keys_ok(C(w, 'run 4" sanitary from the wc to the main at 1/8 per foot'),
            "done")
    assert json.dumps([e.to_dict() for e in m.ents]) != before
    assert m.undo()                       # ONE undo reverts everything
    assert json.dumps([e.to_dict() for e in m.ents]) == before
    # chained walls: one undo too
    before = json.dumps([e.to_dict() for e in m.ents])
    keys_ok(C(w, "draw a wall from 0,30 to 10,30 then to 10,40 then to "
               "0,40"), "done")
    assert len([e for e in m.ents if e.kind == "wall"]) == 3
    assert m.undo()
    assert json.dumps([e.to_dict() for e in m.ents]) == before
    # the undo/redo VERBS drive the same stack
    keys_ok(C(w, "add a wc at 20, 20"), "done")
    n = len(m.ents)
    keys_ok(C(w, "undo"), "done")
    assert len(m.ents) == n - 1
    keys_ok(C(w, "redo"), "done")
    assert len(m.ents) == n
    r = keys_ok(C(Weaver(DraftModel()), "undo"), "done")
    assert "nothing" in r["say"].lower()


# --------------------------------------------------------------- reporters --

def test_reporters():
    m = DraftModel()
    m.add("wall", [(0, 0), (20, 0)], wtype="stud4")
    m.add("pipe", [(0, 5), (22.5, 5)], invert_ft=100.0, slope_in_ft=0.0625)
    w = Weaver(m)
    r = keys_ok(C(w, "check"), "done")
    assert r["changed"] == 0
    assert r["warnings"], "check must surface the engine warnings"
    assert any("minimum" in x for x in r["warnings"])
    assert any("open end" in x for x in r["warnings"])
    r = keys_ok(C(w, "tally"), "done")
    assert "20'-0\" of wall" in r["say"], r["say"]
    assert "22'-6\" of 4\" sanitary" in r["say"], r["say"]
    r = keys_ok(C(Weaver(DraftModel()), "count"), "done")
    assert "nothing drawn" in r["say"]


# ------------------------------------------------------- say formatting ----

def test_say_formatting():
    m = DraftModel()
    w = Weaver(m)
    r = keys_ok(C(w, 'run 6" storm from 0,0 to 12'
                    "'-3\",0 at 1/8 per foot"), "done")
    # real numbers, real units, straight from the formatters
    assert fmt_ftin(12.25) == "12'-3\"" and "12'-3\"" in r["say"]
    assert '6"' in r["say"] and "storm" in r["say"]
    assert '1/8"/ft' in r["say"]
    assert "IE drops" in r["say"]
    fall = 0.125 * 12.25 / 12.0
    assert fmt_ftin(fall) in r["say"], (fmt_ftin(fall), r["say"])
    # helper-level spot checks (the parser's number sense)
    assert abs(_slope_value("an eighth") - 0.125) < 1e-12
    assert abs(_slope_value("quarter inch per foot") - 0.25) < 1e-12
    assert abs(_size_value("four inch") - 4.0) < 1e-9
    assert abs(_size_value('1 1/2"') - 1.5) < 1e-9
    v, rest = _extract_slope('4" sanitary from a to b at 1/8 per foot')
    assert abs(v - 0.125) < 1e-12 and "per foot" not in rest


# ------------------------------------------------- heartwood learning lane --

def test_learning_optional():
    hw = os.path.join(TMP, "weaver_hw.sqlite")
    m = DraftModel()
    m.add("fixture", [(5, 10)], stencil="wc")
    m.add("fixture", [(20, 10)], stencil="lav")
    m.add("pipe", [(0, 0), (40, 0)], dia_in=4.0)
    w = Weaver(m, heartwood=hw)
    # lane 1: a successful phrase lands in the feedback log as phrase->frame
    keys_ok(C(w, "cap the open ends on the storm"), "done")
    keys_ok(C(w, "plug the open ends"), "done")
    # thesaurus expansion: the shipped seed knows sillcock = hose bibb
    r = keys_ok(C(w, "add a sillcock at 2, 2"), "done")
    assert m.ents[-1].props["stencil"] == "hb"
    assert "hose bibb" in r["say"]
    # an unknown noun -> ONE clarification -> done, and the answered noun
    # becomes a thesaurus PROPOSAL (unverified: the human gate decides)
    r = keys_ok(C(w, "run pipe from the crapper to the main"), "ask")
    assert "crapper" in r["question"]
    r2 = keys_ok(C(w, "the wc", pending=r), "done")
    assert "Ran" in r2["say"]
    from rfi_stamper.heartwood.store import HeartwoodStore
    st = HeartwoodStore(hw)
    fb = [row["query"] for row in
          st.db.execute("SELECT query FROM hw_feedback")]
    assert any(q.startswith("weave:") and "-> cap" in q for q in fb), fb
    assert any("-> run.pipe" in q for q in fb), fb
    proposals = [(row["term"], row["canonical"]) for row in
                 st.thesaurus_rows(("unverified",))]
    assert ("crapper", "water closet") in proposals, proposals
    # NOTHING was auto-approved
    live = [(row["term"], row["canonical"]) for row in
            st.thesaurus_rows(("approved",))]
    assert ("crapper", "water closet") not in live
    st.close()
    # with learning OFF (no store) the same commands still work
    m2 = DraftModel()
    m2.add("fixture", [(5, 10)], stencil="wc")
    m2.add("pipe", [(0, 0), (40, 0)])
    w2 = Weaver(m2)
    r = keys_ok(C(w2, "run waste from the crapper to the main"), "ask")
    r2 = keys_ok(C(w2, "the wc", pending=r), "done")
    assert "Ran" in r2["say"]
    # and a broken store path never breaks commanding
    w3 = Weaver(m2, heartwood=os.path.join(TMP, "nodir", "x", "hw.sqlite"))
    w3._hw_dead = True                    # simulate an unavailable core
    keys_ok(C(w3, "cap the open ends"), "done")


# ---------------------------------------------------------------- the Corral --

def test_corral_by_construction():
    """The Weaver obeys the Corral: no networking, no gui, no eval/exec —
    knowledge is data, the verb table is code (CLAUDE.md invariant 1)."""
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "rfi_stamper", "weaver.py")
    src = open(path, encoding="utf-8").read()
    banned = re.compile(
        r"^\s*(?:import|from)\s+(?:socket|ssl|urllib|http|requests"
        r"|xmlrpc|ftplib|smtplib)\b", re.MULTILINE)
    assert not banned.search(src), "networking import in weaver.py"
    assert "tkinter" not in src and "rfi_stamper.gui" not in src
    assert not re.search(r"\beval\s*\(|\bexec\s*\(", src), \
        "eval/exec in weaver.py"


# ------------------------------------------------------------------ corpus --

def test_corpus_size():
    assert len(PHRASES) >= 90, f"only {len(PHRASES)} phrasings exercised"


def main():
    test_contract()
    print("PASS command() contract (done/ask/refused shapes, pending)")
    test_owner_run()
    print("PASS owner day-one: run 4\" sanitary wc -> main at 1/8")
    test_owner_slope_this()
    print("PASS owner day-one: slope this run at 1/4")
    test_owner_cap()
    print("PASS owner day-one: cap the open ends")
    test_owner_replace()
    print("PASS owner day-one: replace that wye with a combo")
    test_pipe_missing_to_flow()
    print("PASS missing-slot flow (run pipe from the wc -> where to?)")
    test_pipe_systems_and_sizes()
    print("PASS system synonyms + sizes corpus (waste->san, cw->dcw...)")
    test_pipe_routing()
    print("PASS Manhattan L routing, straight, ft-in coords, warnings")
    test_pipe_grid_refs()
    print("PASS grid-address routing + honest missing-grid refusal")
    test_connect()
    print("PASS connect two fixtures / fixture to the main")
    test_walls()
    print("PASS walls: ft-in sizes, chains, types, ask flow")
    test_fixtures()
    print("PASS fixtures at coords/grids + synonym corpus")
    test_ambiguity_roundtrip()
    print("PASS ambiguity ask -> answer -> done (id, ordinal, nearest)")
    test_slope_phrasings()
    print("PASS slope phrasings x9 + refusal + missing-value flow")
    test_the_main()
    print("PASS 'the main' largest-diameter resolution (+ tie ask)")
    test_replace_flows()
    print("PASS replace flows (open end, ask, missing kind, refusals)")
    test_resize_flows()
    print("PASS resize flows (downstream/only, missing size)")
    test_move_delete()
    print("PASS move/delete by reference, direction words, cascades")
    test_annotation_objects()
    print("PASS grid/text/dim/room objects + content flows")
    test_refusals()
    print("PASS refusals never touch the model, 3 verbs + example")
    test_one_undo_per_command()
    print("PASS one undo per command (compound run + chained walls)")
    test_reporters()
    print("PASS check/tally reporters")
    test_say_formatting()
    print("PASS say strings carry real feet-inches numbers")
    test_learning_optional()
    print("PASS heartwood lane-1 memory + proposals (and off-switch)")
    test_corral_by_construction()
    print("PASS Corral by construction (no network, no gui, no eval)")
    test_corpus_size()
    print(f"PASS corpus size ({len(PHRASES)} distinct phrasings)")
    print("WEAVER ENGINE TEST PASSED")
    print("outputs in", TMP)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("WEAVER TEST FAILED:", e)
        sys.exit(1)
