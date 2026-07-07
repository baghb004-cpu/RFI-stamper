"""FX framework tests (rfi_stamper/gui/fx.py).

Headless section runs with no display: easing math, the pure PPM gradient
byte-builder (_gradient_ppm), quality get/set, and animate()/cancel() under
quality "off" -- which by contract must never touch tk at all (verified with
a bare stub owner that has no tk attributes whatsoever).

If DISPLAY is unset but xvfb-run exists, the script re-execs itself under
xvfb (like tests/run_all.py does for the GUI test) to run the real-Tk
section: a tween 0->100 with monotone updates and an exact landing,
slide_switch frame swaps, CountUp reaching its target, GradientHeader
construct/redraw/sheen sweep, ambient widgets, and the zero-idle-CPU
guarantee (no pending scheduler `after` once everything finishes).

Run:  python3 tests/test_fx.py                      (re-execs under xvfb)
      xvfb-run -a python3 tests/test_fx.py          (Tk section directly)
"""
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _parse_ppm(raw):
    m = re.match(rb"P6\s+(\d+)\s+(\d+)\s+(\d+)\s", raw)
    assert m, raw[:24]
    return int(m.group(1)), int(m.group(2)), raw[m.end():]


def headless_tests():
    from rfi_stamper.gui import fx

    # easing: exact key set, endpoints 0/1, clamping ------------------------
    assert set(fx.EASINGS) == {"linear", "ease_out_quad", "ease_in_out_cubic",
                               "ease_out_back", "ease_out_elastic"}
    for name, fn in fx.EASINGS.items():
        assert abs(fn(0.0)) < 1e-9, name
        assert abs(fn(1.0) - 1.0) < 1e-9, name
        assert fx.ease(name, -3.0) == fn(0.0), name         # clamped low
        assert abs(fx.ease(name, 42.0) - 1.0) < 1e-9, name  # clamped high
    # smooth easings are monotone
    for name in ("linear", "ease_out_quad", "ease_in_out_cubic"):
        vals = [fx.ease(name, i / 64.0) for i in range(65)]
        assert all(b >= a - 1e-9 for a, b in zip(vals, vals[1:])), name
    # overshoot easings stay bounded and settle at exactly 1
    for name in ("ease_out_back", "ease_out_elastic"):
        vals = [fx.ease(name, i / 128.0) for i in range(129)]
        assert -0.6 < min(vals) and max(vals) < 1.6, name
        assert abs(vals[-1] - 1.0) < 1e-9, name
    assert fx.ease("no_such_easing", 0.25) == 0.25          # linear fallback

    # pure PPM gradient builder (tk-free) -----------------------------------
    raw = fx._gradient_ppm(4, 2, [(0.0, "#000000"), (1.0, "#ff0000")], True)
    w, h, px = _parse_ppm(raw)
    assert (w, h) == (4, 2) and len(px) == 4 * 2 * 3
    assert px[0:3] == b"\x00\x00\x00"           # left edge = first stop
    assert px[9:12] == b"\xff\x00\x00"          # right edge = last stop
    assert px[:12] == px[12:24]                 # horizontal: rows identical
    mid = px[3:6]                               # 1/3 across ~ 85 red
    assert mid[1:3] == b"\x00\x00" and 60 <= mid[0] <= 110, mid

    raw = fx._gradient_ppm(3, 4, [(0.0, "#112233"), (1.0, "#445566")], False)
    w, h, px = _parse_ppm(raw)
    assert (w, h) == (3, 4)
    assert px[0:3] == b"\x11\x22\x33" and px[-3:] == b"\x44\x55\x66"
    assert px[0:3] == px[3:6] == px[6:9]        # vertical: row is one color

    # a 3-stop ramp hits the middle stop dead-on
    raw = fx._gradient_ppm(5, 1, [(0.0, "#000000"), (0.5, "#ffffff"),
                                  (1.0, "#000000")], True)
    _, _, px = _parse_ppm(raw)
    assert px[6:9] == b"\xff\xff\xff"

    # quality get/set --------------------------------------------------------
    assert fx.quality() == "full"
    fx.set_quality("reduced")
    assert fx.quality() == "reduced"
    try:
        fx.set_quality("bogus")
        raise AssertionError("bad quality level accepted")
    except ValueError:
        pass
    fx.set_quality("off")
    assert fx.quality() == "off"

    # animate under quality "off": instant, and MUST NOT touch tk at all.
    # The stub deliberately has no tk methods -- any tk call would raise.
    class Stub:
        pass

    owner = Stub()
    seen, done = [], []
    fx.animate(owner, "k", 0, 42, 500, seen.append,
               on_done=lambda: done.append(True))
    assert seen == [42.0] and done == [True]
    fx.animate(owner, "k", 5, 7, 0, seen.append)        # dur<=0 also instant
    assert seen[-1] == 7.0
    fx.cancel(owner, "k")                               # both forms tk-free
    fx.cancel(owner)
    assert fx._SCHED.idle()                             # never even armed
    assert fx.auto_quality(None) == "off"               # no-probe passthrough
    fx.set_quality("full")
    assert fx.auto_quality(None) == "full"              # headless-safe
    print("headless fx tests: ok")


def _pump(root, ms):
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        root.update()
        time.sleep(0.004)


def tk_tests():
    import tkinter as tk

    from rfi_stamper.gui import fx
    from rfi_stamper.gui import theme as theme_mod

    th = dict(theme_mod.DARK)           # the fx API takes plain color dicts
    fx.set_quality("full")
    root = tk.Tk()
    root.geometry("760x560")
    root.update_idletasks()

    def drain(ms=3000):
        """Pump the event loop until the shared scheduler goes idle."""
        end = time.monotonic() + ms / 1000.0
        while time.monotonic() < end:
            root.update()
            if fx._SCHED.idle():
                return
            time.sleep(0.004)
        raise AssertionError("scheduler did not go idle")

    def _px(im, x, y):
        v = im.get(x, y)
        if isinstance(v, str):
            v = tuple(int(s) for s in v.split())
        return tuple(v)

    # auto_quality probe completes fast and lands on a real level ------------
    q = fx.auto_quality(root)
    assert q in ("full", "reduced"), q
    fx.set_quality("full")

    # animate: 0 -> 100, monotone updates, exact landing ---------------------
    holder = tk.Frame(root)
    holder.pack()
    vals, fin = [], []
    fx.animate(holder, "v", 0, 100, 300, vals.append,
               on_done=lambda: fin.append(True))
    drain()
    assert fin == [True]
    assert vals and vals[-1] == 100.0, vals[-5:]
    assert all(b >= a - 1e-9 for a, b in zip(vals, vals[1:]))
    assert len(vals) >= 4, len(vals)            # ~30 fps over 300 ms
    assert fx._SCHED.idle()                     # timer disarmed: zero idle CPU

    # re-animating the same (owner, key) replaces the running tween ----------
    a_vals, b_vals = [], []
    fx.animate(holder, "v", 0, 10, 400, a_vals.append)
    fx.animate(holder, "v", 0, -10, 200, b_vals.append)
    drain()
    assert b_vals and b_vals[-1] == -10.0
    assert not a_vals                           # first never got a frame

    # cancel() stops frames and leaves the scheduler idle --------------------
    c_vals = []
    fx.animate(holder, "c", 0, 1, 5000, c_vals.append)
    fx.cancel(holder, "c")
    assert fx._SCHED.idle()

    # slide_switch: new slides in over old, old is forgotten -----------------
    cont = tk.Frame(root, width=320, height=90)
    cont.pack_propagate(False)
    cont.pack(fill="x")
    old = tk.Frame(cont, bg="#333333")
    old.place(relx=0, rely=0, relwidth=1, relheight=1)
    new = tk.Frame(cont, bg="#555555")
    root.update()
    fin2 = []
    fx.slide_switch(cont, old, new, dur=180, on_done=lambda: fin2.append(True))
    drain()
    root.update()
    assert fin2 == [True]
    assert new.place_info().get("relwidth") == "1", new.place_info()
    assert new.winfo_ismapped() and not old.winfo_ismapped()
    # unmapped container (width 1) -> instant switch, no crash
    ghost = tk.Frame(root)                      # never packed: width == 1
    g1, g2 = tk.Frame(ghost), tk.Frame(ghost)
    fx.slide_switch(ghost, g1, g2)
    assert g2.place_info().get("relwidth") == "1"
    assert fx._SCHED.idle()

    # CountUp -----------------------------------------------------------------
    lbl = tk.Label(root, text="—")
    lbl.pack()
    cu = fx.CountUp(lbl)
    cu.to(1234, dur=250)
    drain()
    assert lbl.cget("text") == "1,234", lbl.cget("text")
    cu.to(0, dur=120)                           # animates back down
    drain()
    assert lbl.cget("text") == "0"

    # gradient_photo: real image, cache hit, corner colors --------------------
    img = fx.gradient_photo(64, 20, [(0.0, "#112233"), (1.0, "#445566")])
    assert img.width() == 64 and img.height() == 20
    assert fx.gradient_photo(64, 20,
                             [(0.0, "#112233"), (1.0, "#445566")]) is img
    assert _px(img, 0, 0) == (0x11, 0x22, 0x33)
    assert _px(img, 63, 19) == (0x44, 0x55, 0x66)

    # GradientHeader: constructs, redraws, text updates, one sheen sweep ------
    hd = fx.GradientHeader(root, th, height=64, title="Plan Set",
                           subtitle="16 sheets", sheen=True)
    hd.pack(fill="x")
    root.update()
    assert hd.find_withtag("_fx_grad") and hd.find_withtag("_fx_title")
    tid = hd.find_withtag("_fx_title")[0]
    assert hd.itemcget(tid, "text") == "Plan Set"
    hd.set_text("Revision B", "delta check")
    assert hd.itemcget(tid, "text") == "Revision B"
    hd.set_stops([(0.0, "#202020"), (1.0, "#808080")])
    root.geometry("820x560")                    # width change -> redraw
    _pump(root, 60)
    hd._sweep()                                 # drive one sheen sweep
    drain(4000)
    assert hd.itemcget(hd._band, "state") == "hidden"
    hd.destroy()                                # cancels its ambient timers
    root.update()

    # Sparkline / Meter --------------------------------------------------------
    sp = fx.Sparkline(root, th)
    sp.pack()
    mt = fx.Meter(root, th, label="mapped")
    mt.pack()
    root.update()
    sp.set_data([3, 9, 4, 12, 7, 15])
    mt.set(72)
    drain()
    assert sp.find_withtag("_fx_line")
    texts = [mt.itemcget(i, "text") for i in mt.find_withtag("_fx_meter")
             if mt.type(i) == "text"]
    assert "72%" in texts, texts

    # pulse: widens the stroke then restores it exactly -------------------------
    cv = tk.Canvas(root, width=140, height=80)
    cv.pack()
    root.update()
    item = cv.create_rectangle(20, 20, 100, 60, width=1)
    fx.pulse(cv, item)
    drain()
    assert abs(float(cv.itemcget(item, "width")) - 1.0) < 1e-6
    txt = cv.create_text(60, 40, text="halo")   # no width option -> halo path
    fx.pulse(cv, txt)
    drain()
    assert not cv.find_withtag("_fx_halo")      # halo cleaned up

    # shimmer: ambient sweep runs while mapped, dies with the widget -----------
    sh = fx.shimmer(root, th, rows=3)
    sh.pack(fill="x")
    root.update()
    _pump(root, 140)
    assert not fx._SCHED.idle()                 # sweep loop is live
    sh.destroy()
    root.update()
    _pump(root, 80)

    # BlueprintBackdrop: grid drawn beneath, drifts, stops clean ---------------
    bcv = tk.Canvas(root, width=220, height=120, bg=th["canvas_bg"])
    bcv.pack()
    root.update()
    marker = bcv.create_rectangle(40, 40, 80, 80)   # pre-existing content
    bp = fx.BlueprintBackdrop(bcv, th)
    bp.start()
    root.update()
    grid = bcv.find_withtag("_fx_bp")
    assert grid
    # backdrop sits beneath the canvas's real content: find_all() returns
    # bottom-to-top stacking order, so the marker must come after every
    # backdrop item
    stacking = list(bcv.find_all())
    assert max(stacking.index(g) for g in grid) < stacking.index(marker)
    x_before = bcv.coords(grid[0])
    _pump(root, 300)
    assert bcv.coords(grid[0]) != x_before      # grid actually drifts
    bp.stop()
    root.update()
    assert not bcv.find_withtag("_fx_bp")

    # zero idle CPU: scheduler fully disarmed, no stray after timers -----------
    drain()
    assert fx._SCHED.idle()
    pending = root.tk.call("after", "info")
    assert not pending, ("stray after timers", pending)

    root.destroy()
    print("tk fx tests: ok")


def main():
    headless_tests()
    if os.environ.get("DISPLAY"):
        tk_tests()
        print("FX TESTS PASSED")
        return 0
    xvfb = shutil.which("xvfb-run")
    if xvfb and not os.environ.get("_FX_NO_REEXEC"):
        env = dict(os.environ, _FX_NO_REEXEC="1")
        r = subprocess.run([xvfb, "-a", sys.executable,
                            os.path.abspath(__file__)], env=env)
        return r.returncode
    print("-- tk section skipped (no display, no xvfb-run)")
    print("FX TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
