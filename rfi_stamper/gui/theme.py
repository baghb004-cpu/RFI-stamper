"""Light/dark theming for ttk + plain tk widgets.

Dark Mode: reduces eyestrain on large plan sets.  ttk styles are restyled
globally; plain tk widgets (Text, Listbox, Canvas) register a recolor callback
via ThemeManager.register.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

LIGHT = {
    "name": "light",
    "bg": "#eef0f3",
    "panel": "#ffffff",
    "fg": "#1b1d21",
    "muted": "#6b7280",
    "accent": "#c22323",
    "accent_fg": "#ffffff",
    "entry_bg": "#ffffff",
    "border": "#c9ced6",
    "sel_bg": "#dbe7f8",
    "sel_fg": "#111318",
    "canvas_bg": "#dfe2e8",
    "log_bg": "#f7f8fa",
    "ok": "#177245",
    "warn": "#b45309",
    "err": "#b91c1c",
    "drop_bg": "#f4f6fa",
    "drop_hi": "#e3edfb",
}

DARK = {
    "name": "dark",
    "bg": "#1d1f24",
    "panel": "#26282f",
    "fg": "#e6e8ec",
    "muted": "#9aa1ac",
    "accent": "#e05a5a",
    "accent_fg": "#ffffff",
    "entry_bg": "#2e3038",
    "border": "#3d4049",
    "sel_bg": "#3a4a63",
    "sel_fg": "#f2f4f8",
    "canvas_bg": "#131418",
    "log_bg": "#212329",
    "ok": "#4ade80",
    "warn": "#fbbf24",
    "err": "#f87171",
    "drop_bg": "#24262d",
    "drop_hi": "#2d3644",
}


class ThemeManager:
    def __init__(self, root: tk.Misc, name: str = "light"):
        self.root = root
        self.style = ttk.Style(root)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass
        self.colors = dict(LIGHT)
        self._callbacks = []
        self.apply(name)

    @property
    def name(self) -> str:
        return self.colors["name"]

    def register(self, cb) -> None:
        """cb(colors) is called on every theme change (and once on register)."""
        self._callbacks.append(cb)
        cb(self.colors)

    def toggle(self) -> str:
        self.apply("dark" if self.name == "light" else "light")
        return self.name

    def apply(self, name: str) -> None:
        c = dict(DARK if name == "dark" else LIGHT)
        self.colors = c
        s = self.style
        s.configure(".", background=c["bg"], foreground=c["fg"],
                    fieldbackground=c["entry_bg"], bordercolor=c["border"],
                    lightcolor=c["panel"], darkcolor=c["bg"],
                    troughcolor=c["bg"], focuscolor=c["accent"])
        s.configure("TFrame", background=c["bg"])
        s.configure("Panel.TFrame", background=c["panel"])
        s.configure("TLabel", background=c["bg"], foreground=c["fg"])
        s.configure("Panel.TLabel", background=c["panel"], foreground=c["fg"])
        s.configure("Muted.TLabel", background=c["bg"], foreground=c["muted"])
        s.configure("Status.TLabel", background=c["panel"], foreground=c["muted"])
        s.configure("Ok.TLabel", background=c["panel"], foreground=c["ok"])
        s.configure("Err.TLabel", background=c["panel"], foreground=c["err"])
        s.configure("Title.TLabel", background=c["bg"], foreground=c["fg"],
                    font=("Segoe UI", 11, "bold"))
        s.configure("TLabelframe", background=c["bg"], bordercolor=c["border"])
        s.configure("TLabelframe.Label", background=c["bg"], foreground=c["muted"])
        s.configure("TButton", background=c["panel"], foreground=c["fg"], padding=4)
        s.map("TButton",
              background=[("active", c["sel_bg"]), ("disabled", c["bg"])],
              foreground=[("disabled", c["muted"])])
        s.configure("Accent.TButton", background=c["accent"], foreground=c["accent_fg"])
        s.map("Accent.TButton", background=[("active", c["accent"]), ("disabled", c["bg"])],
              foreground=[("disabled", c["muted"])])
        s.configure("Tool.TButton", padding=(6, 3))
        s.configure("ToolOn.TButton", padding=(6, 3), background=c["sel_bg"])
        s.configure("TEntry", fieldbackground=c["entry_bg"], foreground=c["fg"],
                    insertcolor=c["fg"])
        s.configure("TCombobox", fieldbackground=c["entry_bg"], foreground=c["fg"],
                    background=c["panel"], arrowcolor=c["fg"])
        s.configure("TSpinbox", fieldbackground=c["entry_bg"], foreground=c["fg"],
                    background=c["panel"], arrowcolor=c["fg"])
        s.configure("TCheckbutton", background=c["bg"], foreground=c["fg"])
        s.map("TCheckbutton", background=[("active", c["bg"])])
        s.configure("TNotebook", background=c["bg"], bordercolor=c["border"])
        s.configure("TNotebook.Tab", background=c["panel"], foreground=c["muted"],
                    padding=(14, 6))
        s.map("TNotebook.Tab",
              background=[("selected", c["bg"])],
              foreground=[("selected", c["fg"])])
        s.configure("Treeview", background=c["panel"], foreground=c["fg"],
                    fieldbackground=c["panel"], bordercolor=c["border"], rowheight=22)
        s.map("Treeview", background=[("selected", c["sel_bg"])],
              foreground=[("selected", c["sel_fg"])])
        s.configure("Treeview.Heading", background=c["bg"], foreground=c["muted"])
        s.configure("TScrollbar", background=c["panel"], troughcolor=c["bg"],
                    arrowcolor=c["muted"])
        s.configure("TPanedwindow", background=c["bg"])
        s.configure("TSeparator", background=c["border"])
        try:
            self.root.configure(background=c["bg"])
        except tk.TclError:
            pass
        self.root.option_add("*TCombobox*Listbox.background", c["entry_bg"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", c["sel_bg"])
        for cb in self._callbacks:
            try:
                cb(c)
            except Exception:   # noqa: BLE001 -- a dead widget must not kill theming
                pass

    # helpers for plain-tk widget styling ---------------------------------
    def style_text(self, w: tk.Text) -> None:
        c = self.colors
        w.configure(bg=c["log_bg"], fg=c["fg"], insertbackground=c["fg"],
                    selectbackground=c["sel_bg"], selectforeground=c["sel_fg"],
                    highlightthickness=1, highlightbackground=c["border"],
                    relief="flat")

    def style_listbox(self, w: tk.Listbox) -> None:
        c = self.colors
        w.configure(bg=c["panel"], fg=c["fg"], selectbackground=c["sel_bg"],
                    selectforeground=c["sel_fg"], highlightthickness=1,
                    highlightbackground=c["border"], relief="flat")

    def style_canvas(self, w: tk.Canvas) -> None:
        c = self.colors
        w.configure(bg=c["canvas_bg"], highlightthickness=0)
