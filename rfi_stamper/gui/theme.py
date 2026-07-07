"""Light/dark theming for ttk + plain tk widgets, plus the app type scale.

Dark Mode: reduces eyestrain on large plan sets.  ttk styles are restyled
globally; plain tk widgets (Text, Listbox, Canvas) register a recolor callback
via ThemeManager.register.  All animation in the app is timer-based (tk
`after`) — no render loops, near-zero idle CPU.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# ------------------------------------------------------------- type scale ---
FAMILY = "Segoe UI"                 # tk substitutes a system font elsewhere
F_UI = (FAMILY, 10)
F_UI_B = (FAMILY, 10, "bold")
F_BIG = (FAMILY, 12)
F_TITLE = (FAMILY, 15, "bold")
F_HERO = (FAMILY, 26, "bold")
F_STAT = (FAMILY, 22, "bold")
F_GHOST = (FAMILY, 15)
F_MONO = ("Consolas", 9)

LIGHT = {
    "name": "light",
    "bg": "#f3f4f7",
    "panel": "#ffffff",
    "fg": "#191b1f",
    "muted": "#69707d",
    "accent": "#c22323",
    "accent_fg": "#ffffff",
    "accent_soft": "#faeceb",
    "entry_bg": "#ffffff",
    "border": "#d4d8df",
    "sel_bg": "#dbe7f8",
    "sel_fg": "#111318",
    "canvas_bg": "#e2e5ea",
    "log_bg": "#f8f9fb",
    "ok": "#177245",
    "warn": "#b45309",
    "err": "#b91c1c",
    "drop_bg": "#f6f8fb",
    "drop_hi": "#e8f0fc",
    "card": "#ffffff",
    "card_hi": "#fdf6f5",
}

DARK = {
    "name": "dark",
    "bg": "#16171b",
    "panel": "#1f2127",
    "fg": "#e8eaee",
    "muted": "#98a0ab",
    "accent": "#e2564e",
    "accent_fg": "#ffffff",
    "accent_soft": "#33221f",
    "entry_bg": "#2a2c34",
    "border": "#383b44",
    "sel_bg": "#3a4a63",
    "sel_fg": "#f2f4f8",
    "canvas_bg": "#101114",
    "log_bg": "#1c1e23",
    "ok": "#4ade80",
    "warn": "#fbbf24",
    "err": "#f87171",
    "drop_bg": "#1d1f25",
    "drop_hi": "#283142",
    "card": "#22242b",
    "card_hi": "#2b2420",
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
                    troughcolor=c["bg"], focuscolor=c["accent"], font=F_UI)
        s.configure("TFrame", background=c["bg"])
        s.configure("Panel.TFrame", background=c["panel"])
        s.configure("TLabel", background=c["bg"], foreground=c["fg"], font=F_UI)
        s.configure("Panel.TLabel", background=c["panel"], foreground=c["fg"])
        s.configure("Muted.TLabel", background=c["bg"], foreground=c["muted"])
        s.configure("Status.TLabel", background=c["panel"], foreground=c["muted"])
        s.configure("Ok.TLabel", background=c["panel"], foreground=c["ok"])
        s.configure("Err.TLabel", background=c["panel"], foreground=c["err"])
        s.configure("Title.TLabel", background=c["bg"], foreground=c["fg"],
                    font=F_TITLE)
        s.configure("Hero.TLabel", background=c["bg"], foreground=c["fg"],
                    font=F_HERO)
        s.configure("Sub.TLabel", background=c["bg"], foreground=c["muted"],
                    font=F_BIG)
        s.configure("Ghost.TLabel", background=c["canvas_bg"],
                    foreground=c["muted"], font=F_GHOST)
        s.configure("Stat.TLabel", background=c["panel"], foreground=c["accent"],
                    font=F_STAT)
        s.configure("StatCap.TLabel", background=c["panel"],
                    foreground=c["muted"], font=(FAMILY, 9))
        # status pills: colored text on a soft panel chip
        s.configure("PillOk.TLabel", background=c["panel"], foreground=c["ok"],
                    font=F_UI_B, padding=(10, 3))
        s.configure("PillErr.TLabel", background=c["panel"], foreground=c["err"],
                    font=F_UI_B, padding=(10, 3))
        s.configure("TLabelframe", background=c["bg"], bordercolor=c["border"])
        s.configure("TLabelframe.Label", background=c["bg"],
                    foreground=c["muted"], font=F_UI_B)
        s.configure("TButton", background=c["panel"], foreground=c["fg"],
                    padding=(10, 5), font=F_UI)
        s.map("TButton",
              background=[("active", c["sel_bg"]), ("disabled", c["bg"])],
              foreground=[("disabled", c["muted"])])
        s.configure("Accent.TButton", background=c["accent"],
                    foreground=c["accent_fg"], font=F_UI_B, padding=(14, 6))
        s.map("Accent.TButton",
              background=[("active", c["accent"]), ("disabled", c["bg"])],
              foreground=[("disabled", c["muted"])])
        s.configure("Tool.TButton", padding=(5, 2), font=(FAMILY, 9))
        s.configure("ToolOn.TButton", padding=(5, 2), font=(FAMILY, 9),
                    background=c["sel_bg"])
        s.configure("TEntry", fieldbackground=c["entry_bg"], foreground=c["fg"],
                    insertcolor=c["fg"], padding=3)
        s.configure("TCombobox", fieldbackground=c["entry_bg"], foreground=c["fg"],
                    background=c["panel"], arrowcolor=c["fg"])
        s.map("TCombobox",
              fieldbackground=[("readonly", c["entry_bg"])],
              foreground=[("readonly", c["fg"])],
              selectbackground=[("readonly", c["entry_bg"])],
              selectforeground=[("readonly", c["fg"])])
        s.configure("TSpinbox", fieldbackground=c["entry_bg"], foreground=c["fg"],
                    background=c["panel"], arrowcolor=c["fg"])
        s.configure("TCheckbutton", background=c["bg"], foreground=c["fg"])
        s.map("TCheckbutton", background=[("active", c["bg"])])
        s.configure("TRadiobutton", background=c["bg"], foreground=c["fg"])
        s.map("TRadiobutton", background=[("active", c["bg"])])
        s.configure("TNotebook", background=c["bg"], bordercolor=c["border"],
                    tabmargins=(8, 6, 8, 0))
        s.configure("TNotebook.Tab", background=c["panel"],
                    foreground=c["muted"], padding=(18, 8), font=F_BIG)
        s.map("TNotebook.Tab",
              background=[("selected", c["bg"])],
              foreground=[("selected", c["fg"])])
        s.configure("Treeview", background=c["panel"], foreground=c["fg"],
                    fieldbackground=c["panel"], bordercolor=c["border"],
                    rowheight=26, font=F_UI)
        s.map("Treeview", background=[("selected", c["sel_bg"])],
              foreground=[("selected", c["sel_fg"])])
        s.configure("Treeview.Heading", background=c["bg"],
                    foreground=c["muted"], font=F_UI_B)
        s.configure("Sheets.Treeview", rowheight=62)   # thumbnail navigator
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
                    highlightbackground=c["border"], relief="flat",
                    font=F_UI)

    def style_canvas(self, w: tk.Canvas) -> None:
        c = self.colors
        w.configure(bg=c["canvas_bg"], highlightthickness=0)
