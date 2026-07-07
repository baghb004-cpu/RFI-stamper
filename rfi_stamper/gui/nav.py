"""Top navigation: PLANLOOM wordmark + section tabs with per-section accent
colors and an eased sliding indicator.  Section switches animate via
fx.slide_switch; everything is timer-driven with zero idle cost."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import fx
from .theme import SECTIONS, mix


class NavBar(ttk.Frame):
    HEIGHT = 62

    def __init__(self, parent, theme, keys, on_switch):
        super().__init__(parent)
        self.theme = theme
        self.keys = list(keys)
        self.on_switch = on_switch
        self.active = self.keys[0]
        self._hover = None
        self._zones: list = []          # (key, x0, x1)
        self._ind = None                # animated indicator (x, w)
        self.canvas = tk.Canvas(self, height=self.HEIGHT, highlightthickness=0)
        self.canvas.pack(fill="x")
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Leave>", lambda e: self._set_hover(None))
        self.canvas.bind("<Button-1>", self._click)
        theme.register(lambda c: self.redraw())

    # ------------------------------------------------------------- drawing
    def redraw(self):
        cv = self.canvas
        c = self.theme.colors
        w = max(cv.winfo_width(), 10)
        h = self.HEIGHT
        cv.delete("all")
        # subtle vertical gradient panel -> bg gives the bar depth
        try:
            grad = fx.gradient_photo(w, h, [(0.0, c["panel"]),
                                            (1.0, c["bg"])], horizontal=False)
            cv.create_image(0, 0, image=grad, anchor="nw")
            self._grad = grad           # keep a reference for tk
        except Exception:   # noqa: BLE001 -- flat fallback
            cv.create_rectangle(0, 0, w, h, fill=c["panel"], outline="")
        cv.create_line(0, h - 1, w, h - 1, fill=c["border"])

        # wordmark: PLAN in fg, LOOM in brand red, loom-thread motif after —
        # positions measured from bboxes so fonts never overlap
        x = 18
        t1 = cv.create_text(x, h / 2 - 7, text="PLAN", anchor="w",
                            fill=c["fg"], font=("Segoe UI", 17, "bold"))
        x1 = cv.bbox(t1)[2]
        t2 = cv.create_text(x1 + 1, h / 2 - 7, text="LOOM", anchor="w",
                            fill=c["accent"], font=("Segoe UI", 17, "bold"))
        x2 = cv.bbox(t2)[2]
        cv.create_text(x, h / 2 + 14, anchor="w", fill=c["muted"],
                       font=("Segoe UI", 8),
                       text="weaves the answers into the sheets")
        for i in range(4):
            cv.create_line(x2 + 8 + i * 5, h / 2 - 16, x2 + 8 + i * 5,
                           h / 2 + 2, fill=mix(c["accent"], c["bg"], 0.35),
                           width=1)
        cv.create_line(x2 + 4, h / 2 - 7, x2 + 28, h / 2 - 7,
                       fill=c["accent"], width=2)

        # section tabs
        self._zones = []
        tx = x2 + 46
        for key in self.keys:
            meta = SECTIONS[key]
            hot = key == self.active
            hov = key == self._hover
            col = meta["color"] if (hot or hov) else c["muted"]
            label = f"{meta['glyph']}  {meta['label']}"
            tid = cv.create_text(tx, h / 2, text=label, anchor="w", fill=col,
                                 font=("Segoe UI", 11,
                                       "bold" if hot else "normal"),
                                 tags=(f"tab_{key}",))
            x0, _, x1, _ = cv.bbox(tid)
            if hov and not hot:
                cv.create_rectangle(x0 - 8, 10, x1 + 8, h - 12, outline="",
                                    fill=mix(meta["color"], c["panel"], 0.86))
                cv.tag_raise(tid)
            self._zones.append((key, x0 - 10, x1 + 10))
            tx = x1 + 30

        # animated active indicator
        zone = next(((k, a, b) for k, a, b in self._zones
                     if k == self.active), None)
        if zone:
            _k, a, b = zone
            if self._ind is None:
                self._ind = [a, b - a]
            color = SECTIONS[self.active]["color"]
            cv.create_rectangle(self._ind[0], h - 4,
                                self._ind[0] + self._ind[1], h - 1,
                                fill=color, outline="", tags="indicator")
            if abs(self._ind[0] - a) > 1 or abs(self._ind[1] - (b - a)) > 1:
                self._animate_indicator(a, b - a)

    def _animate_indicator(self, tx, tw):
        fx.cancel(self.canvas, "navind")
        x0, w0 = self._ind

        def upd(t):
            self._ind = [x0 + (tx - x0) * t, w0 + (tw - w0) * t]
            cv = self.canvas
            if cv.winfo_exists():
                cv.coords("indicator", self._ind[0], self.HEIGHT - 4,
                          self._ind[0] + self._ind[1], self.HEIGHT - 1)

        fx.animate(self.canvas, "navind", 0.0, 1.0, 260, upd,
                   easing="ease_out_back")

    # ---------------------------------------------------------- interaction
    def _zone_at(self, x):
        for key, a, b in self._zones:
            if a <= x <= b:
                return key
        return None

    def _motion(self, event):
        self._set_hover(self._zone_at(event.x))

    def _set_hover(self, key):
        if key != self._hover:
            self._hover = key
            self.canvas.configure(cursor="hand2" if key else "")
            self.redraw()

    def _click(self, event):
        key = self._zone_at(event.x)
        if key:
            self.select(key)

    def select(self, key, fire=True):
        if key == self.active or key not in self.keys:
            return
        old_i = self.keys.index(self.active)
        new_i = self.keys.index(key)
        self.active = key
        self.redraw()
        if fire:
            self.on_switch(key, 1 if new_i > old_i else -1)
