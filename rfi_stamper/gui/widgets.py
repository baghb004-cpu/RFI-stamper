"""Reusable GUI widgets: drop zones, tooltips, log console, status bar."""
from __future__ import annotations

import queue
import tkinter as tk
from tkinter import ttk

from . import dnd


class Tooltip:
    """Hover tooltip.  Cheap feature discovery without interrupting work."""

    def __init__(self, widget, text: str, theme=None):
        self.widget, self.text, self.theme = widget, text, theme
        self.tip = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _show(self, _e=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        c = self.theme.colors if self.theme else {"panel": "#ffffe0", "fg": "#000",
                                                  "border": "#888"}
        lbl = tk.Label(tw, text=self.text, justify="left", bg=c["panel"], fg=c["fg"],
                       relief="solid", borderwidth=1, font=("Segoe UI", 9),
                       padx=6, pady=3)
        lbl.configure(highlightbackground=c["border"])
        lbl.pack()

    def _hide(self, _e=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class DropZone(ttk.Frame):
    """Dashed 'drop files here' target.  Click anywhere on it to browse instead;
    drag-drop lights up when tkinterdnd2 is present."""

    def __init__(self, parent, theme, text: str, on_paths, exts=None,
                 browse=None, height=64, big=False):
        super().__init__(parent)
        self.theme = theme
        self.on_paths = on_paths
        self.canvas = tk.Canvas(self, height=height, cursor="hand2")
        self.canvas.pack(fill="both", expand=True)
        self._text = text
        self._hover = False
        self._big = big
        dnd_on = dnd.enable_drop(self.canvas, on_paths, exts=exts,
                                 on_enter=lambda: self._set_hover(True),
                                 on_leave=lambda: self._set_hover(False))
        dnd.enable_drop(self, on_paths, exts=exts)
        if not dnd_on:
            self._text += "   (click to browse)"
        if browse:
            self.canvas.bind("<Button-1>", lambda e: browse())
        self.canvas.bind("<Configure>", lambda e: self._redraw())
        theme.register(lambda c: self._redraw())

    def _set_hover(self, on: bool):
        self._hover = on
        self._redraw()

    def _redraw(self):
        c = self.theme.colors
        cv = self.canvas
        cv.configure(bg=c["drop_hi"] if self._hover else c["drop_bg"],
                     highlightthickness=0)
        cv.delete("all")
        w = max(cv.winfo_width(), 10)
        h = max(cv.winfo_height(), 10)
        cv.create_rectangle(4, 4, w - 4, h - 4, dash=(6, 4), width=1.6,
                            outline=c["accent"] if self._hover else c["muted"])
        cv.create_text(w / 2, h / 2, text=self._text, fill=c["muted"],
                       font=("Segoe UI", 14 if self._big else 10),
                       width=w - 30, justify="center")


class LogConsole(ttk.Frame):
    """Read-only log with a thread-safe .say(); background workers log freely."""

    def __init__(self, parent, theme, height=7):
        super().__init__(parent)
        self.q = queue.Queue()
        self.text = tk.Text(self, height=height, state="disabled",
                            font=("Consolas", 9), wrap="word")
        sb = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        theme.register(lambda c: theme.style_text(self.text))
        self._pump()

    def say(self, msg):
        self.q.put(str(msg))

    def clear(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def _pump(self):
        try:
            while True:
                msg = self.q.get_nowait()
                self.text.configure(state="normal")
                self.text.insert("end", msg + "\n")
                self.text.see("end")
                self.text.configure(state="disabled")
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(120, self._pump)


TIPS = [
    "Ctrl+K opens the command palette — search every feature and preference.",
    "Drag PDFs from Explorer straight onto any tab; drop zones light up.",
    "Markup tab: Alt+1..Alt+5 sets the status of selected markups.",
    "Multiply (Ctrl+M) makes offset copies of a markup — counts, grids, forms.",
    "Calibrate the scale first and every measurement gets a real-world caption.",
    "Compare tab: Auto Align registers two revisions before overlaying them.",
    "Dark mode: Ctrl+D. Your eyes will thank you on 40-sheet sets.",
    "Combine tab: double-click the Pages cell to pull only a page range.",
    "Everything runs locally — this app never opens a network connection.",
    "Tool Chest keeps your standard markups one click away; type to search it.",
    "Save a styled markup as a Tool Chest preset to reuse it on every job.",
    "Mapping table: double-click a Sheets cell to correct an RFI's sheet list.",
]


_SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class StatusBar(ttk.Frame):
    """Status text + busy spinner + rotating discovery tips + offline pill."""

    def __init__(self, parent, theme, show_tips=True):
        super().__init__(parent, style="Panel.TFrame")
        self.theme = theme
        self.status = ttk.Label(self, text="Ready", style="Status.TLabel")
        self.status.pack(side="left", padx=8, pady=3)
        self.spinner = ttk.Label(self, text="", style="Status.TLabel")
        self.spinner.pack(side="left", padx=4)
        self.offline = ttk.Label(self, text="", style="PillOk.TLabel")
        self.offline.pack(side="right", padx=8)
        self.tip = ttk.Label(self, text="", style="Status.TLabel")
        if show_tips:
            self.tip.pack(side="right", padx=8)
            self._tip_i = 0
            self._rotate()
        self._spin_i = 0
        self._spin()

    def set(self, msg: str, kind: str = "info"):
        style = {"ok": "Ok.TLabel", "err": "Err.TLabel"}.get(kind, "Status.TLabel")
        self.status.configure(text=msg, style=style)

    def _spin(self):
        """Animated pulse while any background job runs — 9 fps, no CPU cost."""
        if not self.winfo_exists():
            return
        if busy_count() > 0:
            self.spinner.configure(text=_SPIN[self._spin_i % len(_SPIN)]
                                   + " working…")
            self._spin_i += 1
            self.after(110, self._spin)
        else:
            if self.spinner.cget("text"):
                self.spinner.configure(text="")
            self.after(260, self._spin)

    def set_offline(self, active: bool):
        self.offline.configure(
            text="● OFFLINE — network blocked" if active else "○ offline guard off",
            style="PillOk.TLabel" if active else "PillErr.TLabel")

    def _rotate(self):
        if not self.winfo_exists():
            return
        self.tip.configure(text="Tip: " + TIPS[self._tip_i % len(TIPS)])
        self._tip_i += 1
        self.after(25000, self._rotate)


_bg_lock = __import__("threading").Lock()
_bg_active = 0


def busy_count() -> int:
    """Number of run_bg workers still running (used to warn before quit)."""
    with _bg_lock:
        return _bg_active


def run_bg(widget, work, on_done):
    """Run work() on a worker thread; deliver (result, error) on the UI thread.

    work() must not touch tk objects — snapshot every widget/StringVar value
    into plain Python data BEFORE calling run_bg."""
    import threading
    global _bg_active
    holder = {}

    def wrapped():
        global _bg_active
        try:
            holder["r"] = work()
        except Exception as e:      # noqa: BLE001 -- surfaced via on_done
            holder["e"] = e
        finally:
            with _bg_lock:
                _bg_active -= 1

    with _bg_lock:
        _bg_active += 1
    t = threading.Thread(target=wrapped, daemon=True)
    t.start()

    def poll():
        if not widget.winfo_exists():
            return
        if t.is_alive():
            widget.after(150, poll)
            return
        on_done(holder.get("r"), holder.get("e"))

    widget.after(150, poll)


def open_path(path: str):
    """Open a file with the OS default application."""
    import subprocess
    import sys
    if sys.platform.startswith("win"):
        import os
        os.startfile(path)                        # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


_toasts: list = []


def toast(root, theme, msg: str, kind: str = "ok", ms: int = 2800):
    """Slide/fade notification card, bottom-right.  Timer-driven fade via
    window alpha (a handful of `after` ticks) — no continuous animation."""
    import tkinter as tk
    c = theme.colors
    tw = tk.Toplevel(root)
    tw.wm_overrideredirect(True)
    try:
        tw.attributes("-alpha", 0.0)
        fade = True
    except tk.TclError:
        fade = False
    color = {"ok": c["ok"], "err": c["err"], "info": c["muted"]}.get(kind, c["ok"])
    frame = tk.Frame(tw, bg=c["panel"], highlightbackground=color,
                     highlightthickness=2)
    frame.pack()
    glyph = {"ok": "✓", "err": "✕", "info": "ℹ"}.get(kind, "✓")
    tk.Label(frame, text=f"{glyph}  {msg}", bg=c["panel"], fg=c["fg"],
             font=("Segoe UI", 11), padx=16, pady=11).pack()
    tw.update_idletasks()
    _toasts[:] = [t for t in _toasts if t.winfo_exists()]
    stack = sum(t.winfo_height() + 10 for t in _toasts)
    x = root.winfo_rootx() + root.winfo_width() - tw.winfo_width() - 24
    y = (root.winfo_rooty() + root.winfo_height() - tw.winfo_height()
         - 56 - stack)
    tw.wm_geometry(f"+{max(0, x)}+{max(0, y)}")
    _toasts.append(tw)

    def _fade(step, closing):
        if not tw.winfo_exists():
            return
        a = (step / 6) if not closing else (1 - step / 6)
        try:
            tw.attributes("-alpha", max(0.0, min(0.94, a)))
        except tk.TclError:
            pass
        if step < 6:
            tw.after(28, _fade, step + 1, closing)
        elif closing:
            tw.destroy()

    def _close():
        if tw.winfo_exists():
            if fade:
                _fade(0, True)
            else:
                tw.destroy()

    if fade:
        _fade(0, False)
    tw.after(ms, _close)
    frame.bind("<Button-1>", lambda e: _close())
    return tw


def make_tree(parent, theme, columns, widths, height=8):
    """Treeview + scrollbar in a frame; returns (frame, tree)."""
    frame = ttk.Frame(parent)
    tree = ttk.Treeview(frame, columns=[c for c, _ in columns], show="headings",
                        height=height)
    for (cid, label), w in zip(columns, widths):
        tree.heading(cid, text=label)
        tree.column(cid, width=w, anchor="w")
    sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    tree.pack(side="left", fill="both", expand=True)
    sb.pack(side="left", fill="y")
    return frame, tree
