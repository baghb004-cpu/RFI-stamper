"""Home tab: hero header, big action cards, recent files, giant drop zone.
Pure widgets — draws twice per theme change, zero idle cost."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

from .widgets import DropZone

CARDS = [
    ("stamp", "▣", "Stamp RFIs", "Map RFI files to sheets and stamp\n"
     "verified cliff-note boxes."),
    ("merge", "⧉", "Combine PDFs", "Merge, reorder, extract pages,\n"
     "split and rotate."),
    ("markup", "✎", "Markup & Measure", "Clouds, callouts, counts and\n"
     "calibrated measurements."),
    ("compare", "⇄", "Compare Revisions", "Auto-align two sets and see\n"
     "every change in color."),
]


def round_rect(cv, x0, y0, x1, y1, r, **kw):
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return cv.create_polygon(pts, smooth=True, **kw)


class ActionCard(tk.Canvas):
    W, H = 316, 122

    def __init__(self, parent, theme, glyph, title, desc, command):
        super().__init__(parent, width=self.W, height=self.H,
                         highlightthickness=0, cursor="hand2")
        self.theme, self.glyph, self.title, self.desc = theme, glyph, title, desc
        self._hover = False
        self.bind("<Button-1>", lambda e: command())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        theme.register(lambda c: self._draw())

    def _set_hover(self, on):
        self._hover = on
        self._draw()

    def _draw(self):
        c = self.theme.colors
        self.configure(bg=c["bg"])
        self.delete("all")
        fill = c["card_hi"] if self._hover else c["card"]
        border = c["accent"] if self._hover else c["border"]
        round_rect(self, 3, 3, self.W - 3, self.H - 3, 14, fill=fill,
                   outline=border, width=1.6)
        self.create_text(34, self.H / 2, text=self.glyph, fill=c["accent"],
                         font=("Segoe UI", 26, "bold"))
        self.create_text(66, 36, text=self.title, anchor="w", fill=c["fg"],
                         font=("Segoe UI", 13, "bold"))
        self.create_text(66, 74, text=self.desc, anchor="w", fill=c["muted"],
                         font=("Segoe UI", 9), justify="left",
                         width=self.W - 80)


class HomeTab(ttk.Frame):
    def __init__(self, parent, theme, status, actions, recent, on_recent):
        """actions: {key: callback} for the four cards;
        recent: list of {'path','kind'}; on_recent(path, kind) opens one."""
        super().__init__(parent, padding=(36, 26))
        self.theme = theme
        self.on_recent = on_recent

        ttk.Label(self, text="RFI Stamper", style="Hero.TLabel").pack(anchor="w")
        ttk.Label(self, style="Sub.TLabel",
                  text="Offline plan toolkit — stamp, combine, mark up, "
                       "compare. Nothing ever leaves this machine."
                  ).pack(anchor="w", pady=(2, 22))

        grid = ttk.Frame(self)
        grid.pack(anchor="w")
        for i, (key, glyph, title, desc) in enumerate(CARDS):
            card = ActionCard(grid, theme, glyph, title, desc, actions[key])
            card.grid(row=i // 2, column=i % 2, padx=(0, 18), pady=(0, 16))

        self.recent_box = ttk.Frame(self)
        self.recent_box.pack(fill="x", anchor="w", pady=(14, 0))
        self.show_recent(recent)

        DropZone(self, theme,
                 "Drop anything here — a plan set, RFI files, or PDFs to "
                 "combine", self._route, browse=actions["markup"],
                 height=110, big=True).pack(fill="both", expand=True,
                                            pady=(18, 0))
        self._route_cb = None

    def set_router(self, cb):
        self._route_cb = cb

    def _route(self, paths):
        if self._route_cb:
            self._route_cb(paths)

    def show_recent(self, recent):
        for w in self.recent_box.winfo_children():
            w.destroy()
        if not recent:
            return
        ttk.Label(self.recent_box, text="Recent",
                  style="Title.TLabel").pack(anchor="w", pady=(0, 4))
        glyphs = {"markup": "✎", "plan": "▣", "combine": "⧉", "compare": "⇄"}
        for item in recent[:6]:
            path, kind = item.get("path", ""), item.get("kind", "markup")
            row = ttk.Frame(self.recent_box)
            row.pack(anchor="w", fill="x")
            lbl = ttk.Label(
                row, style="Sub.TLabel", cursor="hand2",
                text=f"{glyphs.get(kind, '·')}  {os.path.basename(path)}"
                     f"    —  {os.path.dirname(path)}")
            lbl.pack(anchor="w", pady=1)
            lbl.bind("<Button-1>",
                     lambda e, p=path, k=kind: self.on_recent(p, k))

    def commands(self):
        return [("Go to Home", "Tabs", lambda: None)]  # replaced by app wiring
