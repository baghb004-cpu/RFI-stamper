"""Regression tests for GUI-startup robustness (rebuild group "gui").

All headless -- no Tk window is constructed.  Covers:

  #17  a corrupt 'recent' pref (a bare string, or a list of strings) must be
       coerced to a clean list-of-dicts by prefs.load() so HomeTab.show_recent
       never calls .get on a str; tab_home.show_recent / app.add_recent also
       skip non-dict items defensively.
  #30  a corrupt/unknown 'effects' value must be clamped so nothing raises
       ValueError out of fx.set_quality at startup.
  #31  switching animation quality to "off" mid-tween must finalize every
       in-flight animation (snap to final) and drop ambient loops, leaving the
       shared scheduler idle -- quality-off must never keep touching tk.
  #39  a full<->reduced quality change mid-tween re-rates live tweens to the
       new frame interval.

Run:  python tests/test_reb_gui.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_with(raw_json):
    """Run prefs.load() against a temp prefs.json holding raw_json."""
    from rfi_stamper.gui import prefs

    d = tempfile.mkdtemp()
    path = os.path.join(d, "prefs.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(raw_json)
    old_path, old_migrate = prefs.PREFS_PATH, prefs._migrate
    prefs.PREFS_PATH = path
    prefs._migrate = lambda: None
    try:
        return prefs.load()
    finally:
        prefs.PREFS_PATH = old_path
        prefs._migrate = old_migrate


def test_prefs_sanitize():
    # #17: 'recent' as a bare string -> []
    p = _load_with(json.dumps({"recent": "junk"}))
    assert p["recent"] == [], p["recent"]

    # #17: 'recent' as a list of strings -> filtered to [] (no dicts survive)
    p = _load_with(json.dumps({"recent": ["a.pdf", "b.pdf"]}))
    assert p["recent"] == [], p["recent"]

    # #17: mixed list keeps only dicts, in order
    p = _load_with(json.dumps({"recent": [
        {"path": "one.pdf", "kind": "plan"}, "bogus",
        {"path": "two.pdf", "kind": "markup"}, 7]}))
    assert p["recent"] == [{"path": "one.pdf", "kind": "plan"},
                           {"path": "two.pdf", "kind": "markup"}], p["recent"]

    # #30: unknown 'effects' clamped to a safe default
    for bad in ('"turbo"', "null", "5", "[1,2]"):
        p = _load_with('{"effects": %s}' % bad)
        assert p["effects"] in ("auto", "full", "reduced", "off"), (bad, p)

    # a known 'effects' value is preserved
    p = _load_with(json.dumps({"effects": "reduced"}))
    assert p["effects"] == "reduced", p["effects"]

    # missing/corrupt file -> pure defaults (recent is a list, not crashing)
    p = _load_with("this is not json {{{")
    assert isinstance(p["recent"], list) and p["recent"] == []
    assert p["effects"] == "auto"
    print("prefs sanitize (recent/effects): ok")


def test_effects_clamp_never_raises():
    """#30: the app.py startup path clamps unknown effects to auto_quality
    instead of feeding it to set_quality (which would raise ValueError).  We
    exercise the same guard shape here without constructing the GUI."""
    from rfi_stamper.gui import fx

    for eff in ("auto", "full", "reduced", "off", "turbo", "", None, 5):
        # mirror app.py: only known levels go to set_quality, else auto path.
        if eff in ("full", "reduced", "off"):
            fx.set_quality(eff)                 # must not raise
        else:
            # auto_quality(None) is the headless no-op passthrough
            fx.auto_quality(None)
    print("effects clamp never raises ValueError: ok")


class _Owner:
    """Bare stub owner -- no tk attributes, so any tk call would raise."""
    pass


def test_quality_off_finalizes_inflight():
    """#31: registering a tween then flipping quality to "off" must snap it to
    its final value, fire on_done, drop it, and leave the scheduler idle --
    all with ZERO tk calls (stub owner has no winfo_exists)."""
    from rfi_stamper.gui import fx

    fx.set_quality("full")
    owner = _Owner()
    seen, done = [], []
    # Insert a real in-flight _Anim straight into the task table.  (We bypass
    # _SCHED.add here on purpose: add() arms the pump, and with a tk-free stub
    # owner it would find no live host and flush the table before we can test
    # finalize.  The task table is what finalize_all operates on.)
    anim = fx._Anim(owner, "k", 0.0, 100.0, 5000, seen.append,
                    fx._ease_linear, lambda: done.append(True))
    fx._SCHED._tasks[anim.tkey] = anim
    assert not fx._SCHED.idle()

    fx.set_quality("off")                       # must finalize + disarm
    assert seen == [100.0], seen                # snapped to final value
    assert done == [True], done                 # on_done fired once
    assert fx._SCHED.idle()                     # dropped + timer disarmed
    print("quality-off finalizes in-flight tween: ok")


def test_quality_change_rerates_tween():
    """#39: a full<->reduced change re-rates a live _Anim's interval to the
    current _frame_ms(); ambient _LoopTask intervals are left alone."""
    from rfi_stamper.gui import fx

    fx.set_quality("full")
    owner = _Owner()
    anim = fx._Anim(owner, "k", 0.0, 1.0, 5000, lambda v: None,
                    fx._ease_linear, None)
    assert anim.interval == 33                  # full cadence at creation
    loop = fx._LoopTask(owner, "loop", 90, lambda: True)
    fx._SCHED._tasks[anim.tkey] = anim          # insert without arming (stub
    fx._SCHED._tasks[loop.tkey] = loop          # owner has no live host)

    fx.set_quality("reduced")
    assert anim.interval == 66, anim.interval   # re-rated to reduced cadence
    assert loop.interval == 90, loop.interval   # loop cadence untouched

    fx.set_quality("full")
    assert anim.interval == 33, anim.interval   # and back

    fx.cancel(owner)                            # clean up (tk-free)
    fx.set_quality("off")                       # finalize anything left
    fx.set_quality("full")
    assert fx._SCHED.idle()
    print("quality change re-rates live tween: ok")


def main():
    test_prefs_sanitize()
    test_effects_clamp_never_raises()
    test_quality_off_finalizes_inflight()
    test_quality_change_rerates_tween()
    # leave the module in the default state for any later test in a shared run
    from rfi_stamper.gui import fx
    fx.set_quality("full")
    print("REB GUI TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
