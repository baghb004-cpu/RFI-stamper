"""Animation & visual-effects toolkit for the tkinter workspace.

Everything here is timer-driven off ONE shared `after` scheduler: while any
tween or ambient loop is live the scheduler ticks (~30 fps at "full" quality,
~15 fps at "reduced"); the moment the last task finishes it disarms
completely -- zero idle CPU, matching the house rule in theme.py ("no render
loops, near-zero idle CPU").  Ambient effects (header sheen, blueprint
backdrop, shimmer sweep) park themselves while their widget is unmapped and
resume on <Map>.  Fully offline: pure tk + math, no I/O, no network.

Quality levels:
    "full"     everything on
    "reduced"  half frame rate, ambient extras (sheen, pen strokes) off
    "off"      state jumps instantly; `animate` never touches tk at all, so
               it is safe with stub owners / headless code paths
"""
from __future__ import annotations

import math
import random
import time
import tkinter as tk
from collections import OrderedDict

from .theme import FAMILY

# ------------------------------------------------------------------ easing --


def _ease_linear(t: float) -> float:
    return t


def _ease_out_quad(t: float) -> float:
    return 1.0 - (1.0 - t) * (1.0 - t)


def _ease_in_out_cubic(t: float) -> float:
    if t < 0.5:
        return 4.0 * t * t * t
    return 1.0 - ((-2.0 * t + 2.0) ** 3) / 2.0


def _ease_out_back(t: float) -> float:
    c1 = 1.70158
    c3 = c1 + 1.0
    u = t - 1.0
    return 1.0 + c3 * u * u * u + c1 * u * u


def _ease_out_elastic(t: float) -> float:
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    c4 = (2.0 * math.pi) / 3.0
    return math.pow(2.0, -10.0 * t) * math.sin((t * 10.0 - 0.75) * c4) + 1.0


EASINGS: dict = {
    "linear": _ease_linear,
    "ease_out_quad": _ease_out_quad,
    "ease_in_out_cubic": _ease_in_out_cubic,
    "ease_out_back": _ease_out_back,
    "ease_out_elastic": _ease_out_elastic,
}


def ease(name: str, t: float) -> float:
    """Evaluate easing `name` at t (clamped to 0..1).  Unknown names fall
    back to linear so a typo degrades gracefully instead of crashing."""
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else float(t))
    return EASINGS.get(name, _ease_linear)(t)


# ----------------------------------------------------------------- quality --

_quality = "full"


def quality() -> str:
    return _quality


def set_quality(q: str) -> None:
    global _quality
    if q not in ("full", "reduced", "off"):
        raise ValueError(f"unknown quality {q!r} (full/reduced/off)")
    _quality = q


def _frame_ms() -> int:
    """Tween frame interval: ~30 fps full, halved when reduced."""
    return 66 if _quality == "reduced" else 33


def auto_quality(root) -> str:
    """~150 ms probe: run a burst of after(16) ticks and measure jitter;
    heavy jitter means the event loop can't hold a smooth rate, so drop to
    "reduced".  Never blocks longer than the probe (hard 600 ms guard) and
    is safe headless: on any failure the current quality is returned."""
    global _quality
    if _quality == "off" or root is None:
        return _quality
    try:
        flag = tk.BooleanVar(master=root, value=False)
        gaps: list = []
        last = [time.monotonic()]

        def _tick():
            now = time.monotonic()
            gaps.append((now - last[0]) * 1000.0)
            last[0] = now
            if len(gaps) < 9:
                root.after(16, _tick)
            else:
                flag.set(True)

        guard = root.after(600, lambda: flag.set(True))
        root.after(16, _tick)
        root.wait_variable(flag)
        root.after_cancel(guard)
        if len(gaps) >= 5:
            trimmed = sorted(gaps[1:])          # first gap absorbs warm-up
            mean = sum(trimmed) / len(trimmed)
            _quality = ("reduced" if mean > 42.0 or trimmed[-1] > 140.0
                        else "full")
    except Exception:   # noqa: BLE001 -- headless / dead root: keep as-is
        pass
    return _quality


# -------------------------------------------------------- shared scheduler --


class _Anim:
    """One float tween; stepped by the shared scheduler."""

    __slots__ = ("owner", "tkey", "frm", "to", "t0", "dur", "ease_fn",
                 "on_update", "on_done", "interval", "next_due")

    def __init__(self, owner, key, frm, to, dur, on_update, ease_fn, on_done):
        self.owner = owner
        self.tkey = (id(owner), key)
        self.frm = float(frm)
        self.to = float(to)
        self.t0 = time.monotonic()
        self.dur = max(1, int(dur)) / 1000.0
        self.on_update = on_update
        self.on_done = on_done
        self.ease_fn = ease_fn
        self.interval = _frame_ms()
        self.next_due = self.t0                 # first frame on next tick

    def step(self, now: float) -> bool:
        try:
            if not int(self.owner.winfo_exists()):
                return False
        except Exception:   # noqa: BLE001 -- dead widget: silent cancel
            return False
        t = (now - self.t0) / self.dur
        if t >= 1.0:
            try:
                self.on_update(self.to)
            except tk.TclError:
                return False
            if self.on_done is not None:
                try:
                    self.on_done()
                except tk.TclError:
                    pass
            return False
        try:
            self.on_update(self.frm + (self.to - self.frm) * self.ease_fn(t))
        except tk.TclError:
            return False
        return True


class _LoopTask:
    """Ambient repeating task; fn() returning False (or a dead owner, or a
    TclError) unregisters it."""

    __slots__ = ("owner", "tkey", "fn", "interval", "next_due")

    def __init__(self, owner, key, interval, fn):
        self.owner = owner
        self.tkey = (id(owner), key)
        self.fn = fn
        self.interval = max(20, int(interval))
        self.next_due = time.monotonic()

    def step(self, now: float) -> bool:
        try:
            if not int(self.owner.winfo_exists()):
                return False
        except Exception:   # noqa: BLE001
            return False
        try:
            return self.fn() is not False
        except tk.TclError:
            return False


def _resolve_host(owner):
    """The widget whose .after drives ticks: prefer the interp root (it
    outlives every child), fall back to the owner itself."""
    try:
        w = owner.nametowidget(".")
        if int(w.winfo_exists()):
            return w
    except Exception:   # noqa: BLE001
        pass
    try:
        if int(owner.winfo_exists()):
            return owner
    except Exception:   # noqa: BLE001
        pass
    return None


class _Scheduler:
    """The single shared `after` pump.  Holds every live tween and ambient
    loop; each arm targets the earliest due task, and the instant the task
    table empties the pending timer is cancelled -- zero idle CPU."""

    def __init__(self):
        self._tasks: dict = {}          # (id(owner), key) -> task
        self._after_id = None
        self._host = None

    # public-ish ----------------------------------------------------------
    def add(self, task) -> None:
        self._tasks[task.tkey] = task   # replaces a same-key task silently
        self._arm()

    def cancel(self, owner, key=None) -> None:
        oid = id(owner)
        for tkey in [k for k in self._tasks if k[0] == oid]:
            if key is None or tkey[1] == key:
                self._tasks.pop(tkey, None)
        self._disarm_if_idle()

    def drop(self, owner, key) -> None:
        """Pure-dict removal (never touches tk) -- quality "off" path."""
        self._tasks.pop((id(owner), key), None)

    def idle(self) -> bool:
        return not self._tasks and self._after_id is None

    # internals -------------------------------------------------------------
    def _live_host(self):
        try:
            if self._host is not None and int(self._host.winfo_exists()):
                return self._host
        except Exception:   # noqa: BLE001
            pass
        for task in self._tasks.values():
            host = _resolve_host(task.owner)
            if host is not None:
                return host
        return None

    def _arm(self):
        if self._after_id is not None or not self._tasks:
            return
        host = self._live_host()
        if host is None:                # every owner is dead: flush the table
            self._tasks.clear()
            return
        now = time.monotonic()
        due = min(t.next_due for t in self._tasks.values())
        delay = max(1, int((due - now) * 1000.0))
        try:
            self._host = host
            self._after_id = host.after(delay, self._tick)
        except tk.TclError:
            self._tasks.clear()
            self._after_id = None

    def _disarm_if_idle(self):
        if self._tasks or self._after_id is None:
            return
        try:
            if self._host is not None:
                self._host.after_cancel(self._after_id)
        except Exception:   # noqa: BLE001 -- interp already torn down
            pass
        self._after_id = None

    def _tick(self):
        self._after_id = None
        now = time.monotonic()
        for tkey in list(self._tasks):
            task = self._tasks.get(tkey)
            if task is None or task.next_due > now + 0.001:
                continue
            try:
                alive = task.step(now)
            except Exception:   # noqa: BLE001 -- one broken callback must
                alive = False   # not kill the shared pump
            if self._tasks.get(tkey) is task:   # step() may have re-keyed
                if alive:
                    task.next_due = now + task.interval / 1000.0
                else:
                    self._tasks.pop(tkey, None)
        self._arm()


_SCHED = _Scheduler()


# ------------------------------------------------------------- public core --


def animate(owner, key: str, frm: float, to: float, dur: int, on_update,
            easing: str = "ease_in_out_cubic", on_done=None) -> None:
    """Tween a float frm -> to over dur ms, calling on_update(value) each
    frame on the shared scheduler.  Re-animating the same (owner, key)
    replaces the running tween.  quality "off" (or dur <= 0): on_update(to)
    then on_done() immediately, with ZERO tk calls -- safe even when owner
    is a bare stub object.  Dead widgets / TclError from callbacks cancel
    the tween silently."""
    if _quality == "off" or dur <= 0:
        _SCHED.drop(owner, key)
        on_update(float(to))
        if on_done is not None:
            on_done()
        return
    _SCHED.add(_Anim(owner, key, frm, to, dur, on_update,
                     EASINGS.get(easing, _ease_linear), on_done))


def cancel(owner, key=None) -> None:
    """Drop any running tween/loop for owner (all keys when key is None)."""
    _SCHED.cancel(owner, key)


def slide_switch(container, old, new, direction: int = 1, dur: int = 240,
                 on_done=None) -> None:
    """Animated section change: `new` slides in over `old` (+1 from the
    right, -1 from the left) using place().  When finished `new` fills the
    container and `old` is place_forgotten.  Unmapped container (width 1)
    or quality "off" -> instant switch, no motion."""
    def _finish():
        try:
            new.place(in_=container, relx=0.0, rely=0.0,
                      relwidth=1.0, relheight=1.0)
            new.tkraise()
            if old is not None and old is not new and old.winfo_exists():
                old.place_forget()
        except tk.TclError:
            pass
        if on_done is not None:
            on_done()

    try:
        w = container.winfo_width()
    except tk.TclError:
        w = 1
    if _quality == "off" or w <= 1 or dur <= 0:
        _finish()
        return
    d = 1 if direction >= 0 else -1
    try:
        new.place(in_=container, x=d * w, y=0, relwidth=1.0, relheight=1.0)
        new.tkraise()
    except tk.TclError:
        _finish()
        return

    def _upd(v):
        new.place_configure(x=int(round(v)))

    animate(container, "_fx_slide", float(d * w), 0.0, dur, _upd,
            easing="ease_in_out_cubic", on_done=_finish)


# ---------------------------------------------------------------- gradient --


def _colors(theme) -> dict:
    """Accept a plain color dict or a manager object with a .colors dict."""
    return getattr(theme, "colors", theme)


def _hex_rgb(color) -> tuple:
    s = str(color).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    n = int(s[:6], 16)
    return ((n >> 16) & 255, (n >> 8) & 255, n & 255)


def _rgb_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v))))
                                   for v in rgb)


def _mix(c1, c2, t: float) -> str:
    a, b = _hex_rgb(c1), _hex_rgb(c2)
    return _rgb_hex(tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))


def _stop_ramp(stops, n: int) -> list:
    """n interpolated (r, g, b) tuples across the sorted stop list."""
    ss = sorted((max(0.0, min(1.0, float(p))), _hex_rgb(c)) for p, c in stops)
    if not ss:
        ss = [(0.0, (0, 0, 0))]
    out = []
    for i in range(n):
        t = i / (n - 1) if n > 1 else 0.0
        if t <= ss[0][0]:
            out.append(ss[0][1])
            continue
        if t >= ss[-1][0]:
            out.append(ss[-1][1])
            continue
        for (p0, c0), (p1, c1) in zip(ss, ss[1:]):
            if p0 <= t <= p1:
                f = 0.0 if p1 <= p0 else (t - p0) / (p1 - p0)
                out.append(tuple(int(round(c0[k] + (c1[k] - c0[k]) * f))
                                 for k in range(3)))
                break
    return out


def _gradient_ppm(w: int, h: int, stops, horizontal: bool = True) -> bytes:
    """Pure-python P6 PPM byte builder for a multi-stop linear gradient.
    One raw buffer, no per-pixel .put -- and tk-free, so it is testable
    headless; gradient_photo() wraps it in a PhotoImage."""
    w, h = max(1, int(w)), max(1, int(h))
    header = ("P6 %d %d 255\n" % (w, h)).encode("ascii")
    if horizontal:
        row = b"".join(bytes(c) for c in _stop_ramp(stops, w))
        return header + row * h
    return header + b"".join(bytes(c) * w for c in _stop_ramp(stops, h))


_GRAD_CACHE: OrderedDict = OrderedDict()
_GRAD_CACHE_MAX = 48


def gradient_photo(width: int, height: int, stops,
                   horizontal: bool = True) -> "tk.PhotoImage":
    """Multi-stop linear gradient as a tk.PhotoImage, LRU-cached by
    (w, h, stops, horizontal).  The cache also keeps the Python reference
    tk needs so the image can't be garbage-collected mid-display."""
    key = (int(width), int(height),
           tuple((round(float(p), 4), str(c)) for p, c in stops),
           bool(horizontal))
    img = _GRAD_CACHE.get(key)
    if img is not None:
        try:
            img.width()                 # cached copy may belong to a dead interp
            _GRAD_CACHE.move_to_end(key)
            return img
        except tk.TclError:
            _GRAD_CACHE.pop(key, None)
    img = tk.PhotoImage(data=_gradient_ppm(int(width), int(height),
                                           stops, horizontal))
    _GRAD_CACHE[key] = img
    while len(_GRAD_CACHE) > _GRAD_CACHE_MAX:
        _GRAD_CACHE.popitem(last=False)
    return img


def _default_header_stops(c: dict) -> list:
    accent = c.get("accent", "#c22323")
    return [(0.0, _mix(accent, "#101318", 0.62)),
            (0.55, _mix(accent, "#101318", 0.22)),
            (1.0, _mix(accent, "#ffffff", 0.12))]


# ----------------------------------------------------------------- widgets --


class GradientHeader(tk.Canvas):
    """Full-width gradient banner: bold ~17pt title + subtitle in white with
    a 1 px dark shadow for readability.  At "full" quality a soft diagonal
    sheen drifts across every ~6 s -- an ambient loop that parks while the
    header is unmapped (only one pending timer between sweeps; the shared
    scheduler stays idle)."""

    SHEEN_PERIOD_MS = 6000
    SHEEN_SWEEP_MS = 1300

    def __init__(self, parent, theme, height=64, stops=None, title="",
                 subtitle="", sheen=True):
        c = _colors(theme)
        super().__init__(parent, height=height, highlightthickness=0, bd=0,
                         bg=c.get("panel", "#20222a"))
        self._h = int(height)
        self._auto = stops is None
        self._stops = list(stops) if stops is not None else \
            _default_header_stops(c)
        self._title = title
        self._sub = subtitle
        self._sheen_wanted = bool(sheen)
        self._sheen_after = None
        self._img = None
        self._grad_item = None
        self._band = None
        self._text_items: dict = {}
        self.bind("<Configure>", lambda e: self._redraw(), add="+")
        self.bind("<Map>", self._on_map, add="+")
        self.bind("<Unmap>", self._on_unmap, add="+")
        self.bind("<Destroy>", self._on_destroy, add="+")
        if hasattr(theme, "register"):
            theme.register(self._on_theme)
        self._redraw()

    def set_text(self, title, subtitle=""):
        self._title, self._sub = title, subtitle
        if self._text_items:
            self.itemconfigure(self._text_items["shadow"], text=title)
            self.itemconfigure(self._text_items["title"], text=title)
            self.itemconfigure(self._text_items["sub"], text=subtitle)
            self._layout_text()

    def set_stops(self, stops):
        self._auto = False
        self._stops = list(stops)
        self._redraw()

    # internals -------------------------------------------------------------
    def _redraw(self):
        try:
            w = max(int(self.winfo_width()), 2)
        except tk.TclError:
            return
        w16 = ((w + 15) // 16) * 16     # coarse width buckets: small cache
        self._img = gradient_photo(w16, self._h, self._stops)
        if self._grad_item is None:
            self._grad_item = self.create_image(0, 0, anchor="nw",
                                                image=self._img,
                                                tags=("_fx_grad",))
        else:
            self.itemconfigure(self._grad_item, image=self._img)
        self._layout_text()

    def _layout_text(self):
        ti = self._text_items
        y_t = self._h * (0.36 if self._sub else 0.5)
        y_s = self._h * 0.72
        if not ti:
            ti["shadow"] = self.create_text(
                19, y_t + 1, anchor="w", fill="#14161a",
                font=(FAMILY, 17, "bold"), text=self._title,
                tags=("_fx_text",))
            ti["title"] = self.create_text(
                18, y_t, anchor="w", fill="#ffffff",
                font=(FAMILY, 17, "bold"), text=self._title,
                tags=("_fx_text", "_fx_title"))
            ti["sub"] = self.create_text(
                18, y_s, anchor="w", fill="#f0f1f5", font=(FAMILY, 10),
                text=self._sub, tags=("_fx_text", "_fx_sub"))
        else:
            self.coords(ti["shadow"], 19, y_t + 1)
            self.coords(ti["title"], 18, y_t)
            self.coords(ti["sub"], 18, y_s)
        self.tag_raise("_fx_text")

    def _on_map(self, _e=None):
        self._redraw()
        self._queue_sheen(1200)

    def _on_unmap(self, _e=None):
        self._stop_sheen()

    def _on_destroy(self, _e=None):
        self._stop_sheen()
        cancel(self)

    def _on_theme(self, colors):
        try:
            if not int(self.winfo_exists()):
                return
        except tk.TclError:
            return
        if self._auto:
            self._stops = _default_header_stops(colors)
            self._redraw()

    def _stop_sheen(self):
        if self._sheen_after is not None:
            try:
                self.after_cancel(self._sheen_after)
            except tk.TclError:
                pass
            self._sheen_after = None
        cancel(self, "_fx_sheen")
        if self._band is not None:
            try:
                self.itemconfigure(self._band, state="hidden")
            except tk.TclError:
                pass

    def _queue_sheen(self, delay=None):
        """One pending timer between sweeps; nothing else runs meanwhile."""
        if not (self._sheen_wanted and quality() == "full"):
            return
        if self._sheen_after is not None:
            return
        try:
            if not self.winfo_ismapped():
                return
            self._sheen_after = self.after(delay or self.SHEEN_PERIOD_MS,
                                           self._sweep)
        except tk.TclError:
            pass

    def _sweep(self):
        self._sheen_after = None
        if not (self._sheen_wanted and quality() == "full"):
            return
        try:
            if not self.winfo_ismapped():
                return
            w = max(self.winfo_width(), 2)
        except tk.TclError:
            return
        h = self._h
        band_w = max(60, int(w * 0.16))
        slant = int(h * 0.7)
        if self._band is None:
            self._band = self.create_polygon(
                0, 0, 0, 0, 0, 0, 0, 0, fill="#ffffff", outline="",
                stipple="gray12", state="hidden", tags=("_fx_sheen",))
        self.tag_raise("_fx_text")

        def _at(x):
            self.coords(self._band, x, h, x + slant, 0,
                        x + slant + band_w, 0, x + band_w, h)
            self.itemconfigure(self._band, state="normal")

        def _done():
            try:
                self.itemconfigure(self._band, state="hidden")
            except tk.TclError:
                return
            self._queue_sheen()

        animate(self, "_fx_sheen", -(band_w + slant), float(w + band_w),
                self.SHEEN_SWEEP_MS, _at, easing="ease_in_out_cubic",
                on_done=_done)


class CountUp:
    """Animates the number shown in a tk/ttk Label toward a target."""

    def __init__(self, label, fmt="{:,.0f}"):
        self.label = label
        self.fmt = fmt
        self._value = 0.0

    def to(self, value: float, dur: int = 700):
        def _upd(v):
            self._value = v
            self.label.configure(text=self.fmt.format(v))

        animate(self.label, "_fx_countup", self._value, float(value), dur,
                _upd, easing="ease_out_quad")


class Sparkline(tk.Canvas):
    """Tiny trend line with an animated left-to-right draw-in."""

    def __init__(self, parent, theme, width=140, height=36, color=None):
        c = _colors(theme)
        super().__init__(parent, width=width, height=height,
                         bg=c.get("panel", "#ffffff"),
                         highlightthickness=0, bd=0)
        self._color = color or c.get("accent", "#c22323")
        self._values: list = []
        self._progress = 0.0
        self._wh = (int(width), int(height))
        self.bind("<Configure>", self._on_conf, add="+")
        self.bind("<Destroy>", lambda e: cancel(self), add="+")

    def set_data(self, values):
        self._values = [float(v) for v in values]
        self._progress = 0.0
        if len(self._values) < 2:
            self.delete("_fx_line")
            self._progress = 1.0
            if self._values:
                self._draw(1.0)
            return
        animate(self, "_fx_spark", 0.0, 1.0, 550, self._draw,
                easing="ease_in_out_cubic")

    # internals -------------------------------------------------------------
    def _on_conf(self, e):
        self._wh = (max(e.width, 10), max(e.height, 10))
        if self._values:
            self._draw(self._progress)

    def _pts(self):
        w, h = self._wh
        vals = self._values
        pad = 3.0
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1.0
        n = len(vals)
        return [(pad + (w - 2 * pad) * (i / (n - 1) if n > 1 else 0.0),
                 h - pad - (h - 2 * pad) * ((v - lo) / span))
                for i, v in enumerate(vals)]

    def _draw(self, p):
        self._progress = p
        self.delete("_fx_line")
        pts = self._pts()
        if not pts:
            return
        if len(pts) == 1:
            head = pts
        else:
            cut = max(0.0, min(1.0, p)) * (len(pts) - 1)
            i = int(cut)
            head = pts[: i + 1]
            if i < len(pts) - 1:
                f = cut - i
                x0, y0 = pts[i]
                x1, y1 = pts[i + 1]
                head = head + [(x0 + (x1 - x0) * f, y0 + (y1 - y0) * f)]
            flat = [c for xy in head for c in xy]
            if len(flat) >= 4:
                self.create_line(*flat, fill=self._color, width=2.0,
                                 capstyle="round", joinstyle="round",
                                 tags=("_fx_line",))
        hx, hy = head[-1]
        self.create_oval(hx - 2.2, hy - 2.2, hx + 2.2, hy + 2.2,
                         fill=self._color, outline="", tags=("_fx_line",))


class Meter(tk.Canvas):
    """Animated donut gauge 0..100 with the percentage in the middle and an
    optional caption underneath."""

    def __init__(self, parent, theme, width=120, height=120, color=None,
                 label=""):
        c = _colors(theme)
        super().__init__(parent, width=width, height=height,
                         bg=c.get("panel", "#ffffff"),
                         highlightthickness=0, bd=0)
        self._c = c
        self._color = color or c.get("accent", "#c22323")
        self._label = label
        self._shown = 0.0
        self._wh = (int(width), int(height))
        self.bind("<Configure>", self._on_conf, add="+")
        self.bind("<Destroy>", lambda e: cancel(self), add="+")
        self._build()

    def set(self, pct: float):
        target = max(0.0, min(100.0, float(pct)))
        animate(self, "_fx_meter", self._shown, target, 650, self._paint,
                easing="ease_in_out_cubic")

    # internals -------------------------------------------------------------
    def _on_conf(self, e):
        self._wh = (max(e.width, 20), max(e.height, 20))
        self._build()
        self._paint(self._shown)

    def _build(self):
        self.delete("_fx_meter")
        w, h = self._wh
        size = min(w, h)
        th = max(7.0, size * 0.10)
        pad = th / 2 + 3
        cx, cy = w / 2.0, h / 2.0
        r = size / 2.0 - pad
        box = (cx - r, cy - r, cx + r, cy + r)
        self._track = self.create_arc(*box, start=90.0, extent=-359.9,
                                      style="arc", width=th,
                                      outline=self._c.get("border", "#888"),
                                      tags=("_fx_meter",))
        self._arc = self.create_arc(*box, start=90.0, extent=-0.1,
                                    style="arc", width=th,
                                    outline=self._color, state="hidden",
                                    tags=("_fx_meter",))
        fpx = max(11, int(size * 0.16))
        self._pct = self.create_text(cx, cy - (7 if self._label else 0),
                                     text="0%", font=(FAMILY, fpx, "bold"),
                                     fill=self._c.get("fg", "#111"),
                                     tags=("_fx_meter",))
        self._cap = self.create_text(cx, cy + fpx * 0.9, text=self._label,
                                     font=(FAMILY, max(8, int(size * 0.075))),
                                     fill=self._c.get("muted", "#777"),
                                     tags=("_fx_meter",))

    def _paint(self, v):
        self._shown = v
        ext = -3.599 * v
        if abs(ext) < 0.05:
            self.itemconfigure(self._arc, state="hidden")
        else:
            self.itemconfigure(self._arc, extent=ext, state="normal")
        self.itemconfigure(self._pct, text=f"{int(round(v))}%")


# ----------------------------------------------------- blueprint / shimmer --


def _polyline_prefix(pts, lens, dist):
    """Points of a polyline truncated at path-length `dist`."""
    out = [pts[0]]
    left = dist
    for (a, b), seg in zip(zip(pts, pts[1:]), lens):
        if left <= 0:
            break
        if seg <= left:
            out.append(b)
            left -= seg
        else:
            f = left / seg if seg else 1.0
            out.append((a[0] + (b[0] - a[0]) * f,
                        a[1] + (b[1] - a[1]) * f))
            break
    return out


class BlueprintBackdrop:
    """Ambient blueprint texture on an existing canvas: a faint drafting grid
    drifting diagonally plus (full quality) an occasional "pen" sketching a
    random rectangle or line.  <= 12 fps, parks while the canvas is unmapped
    (resumes on <Map>), and always sits beneath the canvas's real content
    (tag_lower).  All items carry the "_fx_bp" tag."""

    GRID = 26                   # px between minor grid lines
    MAJOR_EVERY = 4             # every 4th line is stronger

    def __init__(self, canvas, theme):
        self.canvas = canvas
        c = _colors(theme)
        base = c.get("canvas_bg", "#20232a")
        self._minor = _mix(base, c.get("fg", "#ffffff"), 0.055)
        self._major = _mix(base, c.get("fg", "#ffffff"), 0.10)
        self._pen = _mix(base, c.get("accent", "#c22323"), 0.5)
        self._running = False
        self._off = [0.0, 0.0]
        self._afters: list = []
        canvas.bind("<Map>", self._on_map, add="+")
        canvas.bind("<Unmap>", lambda e: self._pause(), add="+")
        canvas.bind("<Configure>", self._on_conf, add="+")
        canvas.bind("<Destroy>", lambda e: self.stop(), add="+")

    def start(self):
        self._running = True
        self._draw_grid()
        self._resume()

    def stop(self):
        self._running = False
        self._pause()
        try:
            self.canvas.delete("_fx_bp")
        except tk.TclError:
            pass

    # internals -------------------------------------------------------------
    def _on_map(self, _e=None):
        if self._running:
            self._draw_grid()
            self._resume()

    def _on_conf(self, _e=None):
        if self._running:
            self._draw_grid()

    def _resume(self):
        if not self._running or quality() == "off":
            return
        try:
            if not self.canvas.winfo_ismapped():
                return
        except tk.TclError:
            return
        interval = 90 if quality() == "full" else 170       # <= ~11 fps
        _SCHED.add(_LoopTask(self.canvas, "_fx_bp_drift", interval,
                             self._drift))
        self._queue_stroke()

    def _pause(self):
        _SCHED.cancel(self.canvas, "_fx_bp_drift")
        _SCHED.cancel(self.canvas, "_fx_bp_pen")
        for aid in self._afters:
            try:
                self.canvas.after_cancel(aid)
            except tk.TclError:
                pass
        self._afters = []
        try:
            self.canvas.delete("_fx_bp_pen")
        except tk.TclError:
            pass

    def _draw_grid(self):
        cv = self.canvas
        try:
            w = max(cv.winfo_width(), 40)
            h = max(cv.winfo_height(), 40)
        except tk.TclError:
            return
        cv.delete("_fx_bp_grid")
        sp = self.GRID
        margin = sp * self.MAJOR_EVERY      # wrap period incl. major lines
        self._off = [0.0, 0.0]
        for i, x in enumerate(range(-margin, w + margin + 1, sp)):
            color = self._major if i % self.MAJOR_EVERY == 0 else self._minor
            cv.create_line(x, -margin, x, h + margin, fill=color,
                           tags=("_fx_bp", "_fx_bp_grid"))
        for i, y in enumerate(range(-margin, h + margin + 1, sp)):
            color = self._major if i % self.MAJOR_EVERY == 0 else self._minor
            cv.create_line(-margin, y, w + margin, y, fill=color,
                           tags=("_fx_bp", "_fx_bp_grid"))
        cv.tag_lower("_fx_bp")

    def _drift(self):
        cv = self.canvas
        try:
            if not cv.winfo_ismapped():
                return False            # <Map> binding resumes the loop
        except tk.TclError:
            return False
        if not self._running:
            return False
        dx, dy = 0.45, 0.28
        wrap = self.GRID * self.MAJOR_EVERY
        cv.move("_fx_bp_grid", dx, dy)
        self._off[0] += dx
        self._off[1] += dy
        if self._off[0] >= wrap:
            cv.move("_fx_bp_grid", -wrap, 0)
            self._off[0] -= wrap
        if self._off[1] >= wrap:
            cv.move("_fx_bp_grid", 0, -wrap)
            self._off[1] -= wrap
        return True

    def _queue_stroke(self):
        if not (self._running and quality() == "full"):
            return
        for aid in self._afters:
            try:
                self.canvas.after_cancel(aid)
            except tk.TclError:
                pass
        try:
            self._afters = [self.canvas.after(random.randint(2600, 6800),
                                              self._stroke)]
        except tk.TclError:
            self._afters = []

    def _stroke(self):
        cv = self.canvas
        self._afters = []
        try:
            if not (self._running and cv.winfo_ismapped()
                    and quality() == "full"):
                return
            w = max(cv.winfo_width(), 60)
            h = max(cv.winfo_height(), 60)
        except tk.TclError:
            return
        if random.random() < 0.6:       # sketch a rectangle
            x0 = random.uniform(0.08, 0.6) * w
            y0 = random.uniform(0.10, 0.55) * h
            x1 = x0 + random.uniform(0.12, 0.30) * w
            y1 = y0 + random.uniform(0.10, 0.30) * h
            pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
        else:                           # or a straight line
            x0 = random.uniform(0.05, 0.5) * w
            y0 = random.uniform(0.1, 0.9) * h
            pts = [(x0, y0), (x0 + random.uniform(0.2, 0.45) * w,
                              y0 + random.uniform(-0.2, 0.2) * h)]
        lens = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
        total = sum(lens) or 1.0

        def _upd(p):
            cv.delete("_fx_bp_pen")
            head = _polyline_prefix(pts, lens, p * total)
            if len(head) >= 2:
                cv.create_line(*[c for xy in head for c in xy],
                               fill=self._pen, width=1.4, capstyle="round",
                               joinstyle="round",
                               tags=("_fx_bp", "_fx_bp_pen"))
                cv.tag_lower("_fx_bp")

        def _done():
            def _fade():
                self._afters = []
                try:
                    cv.delete("_fx_bp_pen")
                except tk.TclError:
                    return
                self._queue_stroke()
            try:
                self._afters = [cv.after(2000, _fade)]
            except tk.TclError:
                self._afters = []

        animate(cv, "_fx_bp_pen", 0.0, 1.0, 1100, _upd,
                easing="ease_in_out_cubic", on_done=_done)


def pulse(canvas, item, scale=1.15, dur=280):
    """Quick emphasis pulse of a canvas item: widens its stroke and settles
    back; items without a width option (text/images) get a short-lived
    expanding halo rectangle instead."""
    if quality() == "off":
        return
    try:
        if not int(canvas.winfo_exists()) or not canvas.bbox(item):
            return
        try:
            base = float(canvas.itemcget(item, "width"))
        except (tk.TclError, ValueError):
            base = None
    except tk.TclError:
        return
    key = "_fx_pulse_%s" % item
    if base is not None and base > 0:
        peak = max(base * float(scale), base + 2.2)

        def _upd(p):
            canvas.itemconfigure(
                item, width=base + (peak - base) * math.sin(math.pi * p))

        def _done():
            try:
                canvas.itemconfigure(item, width=base)
            except tk.TclError:
                pass

        animate(canvas, key, 0.0, 1.0, dur, _upd, easing="linear",
                on_done=_done)
        return
    x0, y0, x1, y1 = canvas.bbox(item)
    grow = max(4.0, (float(scale) - 1.0) * max(x1 - x0, y1 - y0))
    color = None
    for opt in ("outline", "fill"):
        try:
            v = str(canvas.itemcget(item, opt))
            if v:
                color = v
                break
        except tk.TclError:
            continue
    halo = canvas.create_rectangle(x0, y0, x1, y1,
                                   outline=color or "#999999", width=2.4,
                                   tags=("_fx_halo",))

    def _upd(p):
        o = grow * _ease_out_quad(p)
        canvas.coords(halo, x0 - o, y0 - o, x1 + o, y1 + o)
        canvas.itemconfigure(halo, width=max(0.6, 2.4 * (1.0 - p)))

    def _done():
        try:
            canvas.delete(halo)
        except tk.TclError:
            pass

    animate(canvas, key, 0.0, 1.0, dur, _upd, easing="linear", on_done=_done)


def shimmer(parent, theme, rows=3) -> "tk.Frame":
    """Skeleton-loading placeholder: gray content bars with a light sweep
    gliding across.  Returns a Frame the caller packs and later destroys;
    the sweep is an ambient loop that stops with the widget and parks while
    unmapped.  quality "off" -> static bars, no loop."""
    c = _colors(theme)
    panel = c.get("panel", "#ffffff")
    bar_fill = _mix(panel, c.get("fg", "#000000"), 0.07)
    hi_fill = _mix(panel, c.get("fg", "#000000"), 0.15)
    bar_h, gap, pad = 13, 11, 10
    rows = max(1, int(rows))
    height = pad * 2 + rows * bar_h + (rows - 1) * gap
    frame = tk.Frame(parent, bg=panel)
    cv = tk.Canvas(frame, height=height, bg=panel, highlightthickness=0,
                   bd=0)
    cv.pack(fill="x", expand=True)
    fracs = [0.94, 0.70, 0.52, 0.84, 0.61]
    state = {"bars": [], "t0": time.monotonic()}

    def _layout(_e=None):
        try:
            w = max(cv.winfo_width(), 60)
        except tk.TclError:
            return
        cv.delete("all")
        state["bars"] = []
        for r in range(rows):
            y0 = pad + r * (bar_h + gap)
            x1 = pad + (w - 2 * pad) * fracs[r % len(fracs)]
            rect = (pad, y0, x1, y0 + bar_h)
            cv.create_rectangle(*rect, fill=bar_fill, outline="")
            hi = cv.create_rectangle(0, 0, 0, 0, fill=hi_fill, outline="",
                                     state="hidden")
            state["bars"].append((rect, hi))

    def _tick():
        try:
            if not cv.winfo_ismapped():
                return False            # <Map> binding resumes
        except tk.TclError:
            return False
        band = 64.0
        period = 1.4
        now = time.monotonic() - state["t0"]
        for r, ((x0, y0, x1, y1), hi) in enumerate(state["bars"]):
            p = (now / period + r * 0.13) % 1.0
            x = x0 - band + p * (x1 - x0 + 2 * band)
            a, b = max(x0, x), min(x1, x + band)
            if b - a < 1:
                cv.itemconfigure(hi, state="hidden")
            else:
                cv.coords(hi, a, y0, b, y1)
                cv.itemconfigure(hi, state="normal")
        return True

    def _resume(_e=None):
        if quality() == "off":
            return
        _SCHED.add(_LoopTask(cv, "_fx_shimmer", _frame_ms(), _tick))

    cv.bind("<Configure>", _layout, add="+")
    cv.bind("<Map>", _resume, add="+")
    cv.bind("<Unmap>", lambda e: _SCHED.cancel(cv, "_fx_shimmer"), add="+")
    cv.bind("<Destroy>", lambda e: _SCHED.cancel(cv, "_fx_shimmer"), add="+")
    _layout()
    return frame
