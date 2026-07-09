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
        # Register on the toplevel itself: the router treats that as the
        # window-level enter/leave hook (show/hide the overlay) AND the
        # fallback target for a drop that lands outside any child target.
        # The router hides the overlay on every drop (leave fires first) and
        # defers the callback past the OS drop handshake.
        dnd.enable_drop(root, self._paths, on_enter=self._show,
                        on_leave=self._hide)

    # ------------------------------------------------------------------ #
    def _show(self, _e=None):
        if self.canvas is not None and self.canvas.winfo_exists():
            return
        c = self.theme.colors
        cv = tk.Canvas(self.root, highlightthickness=0, bg=c["bg"])
        cv.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        self.canvas = cv
        # The canvas is purely visual: the router routes by registry+geometry,
        # not by what is stacked on top, so child targets keep working and a
        # drop anywhere else falls back to this overlay's root registration.
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

    def _paths(self, paths):
        # already deferred + hidden by the router; just route to the tab
        if paths:
            self.on_paths(paths)
