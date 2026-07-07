"""Full-window drag-and-drop overlay.

The moment a file drag enters the window, the whole app becomes one giant
drop target: a dimmed canvas with a big dashed frame and a huge label saying
exactly what dropping will do on the active tab.  Appears only during a drag
(two canvas draws), so it costs nothing at idle.
"""
from __future__ import annotations

import tkinter as tk

from . import dnd


class DropOverlay:
    def __init__(self, root, theme, get_hint, on_paths):
        """get_hint() -> big label text for the active tab;
        on_paths(paths) routes the dropped files to that tab."""
        self.root = root
        self.theme = theme
        self.get_hint = get_hint
        self.on_paths = on_paths
        self.canvas = None
        if not dnd.HAS_DND:
            return
        try:
            root.drop_target_register(dnd.DND_FILES)
            root.dnd_bind("<<DropEnter>>", self._show)
            root.dnd_bind("<<DropLeave>>", self._hide)
            # a drop on the bare root (outside any child target) still routes
            root.dnd_bind("<<Drop>>", self._drop)
        except Exception:   # noqa: BLE001 -- overlay is sugar, never fatal
            pass

    # ------------------------------------------------------------------ #
    def _show(self, _e=None):
        if self.canvas is not None and self.canvas.winfo_exists():
            return
        c = self.theme.colors
        cv = tk.Canvas(self.root, highlightthickness=0, bg=c["bg"])
        cv.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        self.canvas = cv
        try:
            cv.drop_target_register(dnd.DND_FILES)
            cv.dnd_bind("<<Drop>>", self._drop)
            cv.dnd_bind("<<DropLeave>>", self._hide)
        except Exception:   # noqa: BLE001
            pass
        self.root.after(10, self._draw)

    def _draw(self):
        cv = self.canvas
        if cv is None or not cv.winfo_exists():
            return
        c = self.theme.colors
        w = max(cv.winfo_width(), 40)
        h = max(cv.winfo_height(), 40)
        cv.delete("all")
        # dim wash (stipple = cheap fake translucency over the whole window)
        cv.create_rectangle(0, 0, w, h, fill=c["canvas_bg"], outline="",
                            stipple="gray50")
        m = 26
        cv.create_rectangle(m, m, w - m, h - m, dash=(14, 9), width=3,
                            outline=c["accent"])
        cv.create_text(w / 2, h / 2 - 26, text="⤓", fill=c["accent"],
                       font=("Segoe UI", 46, "bold"))
        cv.create_text(w / 2, h / 2 + 34, text=self.get_hint(),
                       fill=c["fg"], font=("Segoe UI", 20, "bold"),
                       width=w - 120, justify="center")
        cv.create_text(w / 2, h - m - 26,
                       text="release to drop  •  files stay on this machine",
                       fill=c["muted"], font=("Segoe UI", 11))

    def _hide(self, _e=None):
        if self.canvas is not None:
            self.canvas.destroy()
            self.canvas = None

    def _drop(self, event):
        paths = dnd.parse_drop_paths(self.root, event.data)
        self._hide()
        if paths:
            # let tkdnd finish its drop protocol before mutating widgets
            self.root.after(20, lambda: self.on_paths(paths))
        return "copy"
