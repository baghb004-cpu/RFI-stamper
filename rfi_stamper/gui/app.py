"""Main window: tabs, menu, dark mode, command palette, offline guard."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from .. import __version__, offline_guard
from . import dnd, prefs
from .palette import CommandPalette
from .tab_compare import CompareTab
from .tab_markup import MarkupTab
from .tab_merge import MergeTab
from .tab_stamp import StampTab
from .theme import ThemeManager
from .widgets import StatusBar

SHORTCUTS = """\
Ctrl+K        command palette (search every feature)
Ctrl+D        toggle dark mode
Ctrl+Z / Y    undo / redo markup change
Ctrl+M        multiply selected markups
Alt+1..5      set markup status (none/accepted/rejected/completed/cancelled)
Del           delete selected markups
Esc           cancel the in-progress tool
V P G L A R E C Q T N M   markup tools (select, pen, highlight, line, arrow,
                          rect, ellipse, cloud, callout, text, count, length)
Ctrl+Wheel    zoom at cursor        Middle-drag   pan
PgUp / PgDn   previous / next page
"""


class App:
    def __init__(self, root):
        self.root = root
        self.prefs = prefs.load()
        root.title(f"RFI Stamper {__version__} — offline plan toolkit")
        root.geometry("1280x840")
        self.theme = ThemeManager(root, self.prefs.get("theme", "light"))

        if self.prefs.get("offline_guard", True):
            offline_guard.install()

        self.nb = ttk.Notebook(root)
        self.status = StatusBar(root, self.theme,
                                show_tips=self.prefs.get("tips", True))
        self.status.pack(side="bottom", fill="x")
        self.status.set_offline(offline_guard.is_active())
        self.nb.pack(fill="both", expand=True, padx=4, pady=(4, 0))

        self.stamp = StampTab(self.nb, self.theme, self.status)
        self.merge = MergeTab(self.nb, self.theme, self.status)
        self.markup = MarkupTab(self.nb, self.theme, self.status,
                                author=self.prefs.get("author", ""))
        self.compare = CompareTab(self.nb, self.theme, self.status)
        self.nb.add(self.stamp, text=" Stamp RFIs ")
        self.nb.add(self.merge, text=" Combine PDFs ")
        self.nb.add(self.markup, text=" Markup & Measure ")
        self.nb.add(self.compare, text=" Compare / Overlay ")

        self.palette = CommandPalette(root, self.theme)
        self._register_commands()
        self.markup.bind_shortcuts(root)
        root.bind("<Control-k>", self.palette.open)
        root.bind("<Control-d>", lambda e: self.toggle_dark())
        root.bind("<F1>", lambda e: self.show_shortcuts())
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_menu()
        if not dnd.HAS_DND:
            self.status.set("Tip: pip install tkinterdnd2 enables OS drag-and-"
                            "drop (everything also works via Browse)", "info")

    # ---------------------------------------------------------------- menu
    def _build_menu(self):
        m = tk.Menu(self.root)
        filem = tk.Menu(m, tearoff=0)
        filem.add_command(label="Open PDF in Markup tab…",
                          command=lambda: (self.nb.select(self.markup),
                                           self.markup.open_pdf()))
        filem.add_separator()
        filem.add_command(label="Exit", command=self.on_close)
        m.add_cascade(label="File", menu=filem)
        viewm = tk.Menu(m, tearoff=0)
        viewm.add_command(label="Toggle dark mode\tCtrl+D",
                          command=self.toggle_dark)
        viewm.add_command(label="Invert PDF colors (markup view)",
                          command=self.toggle_invert)
        m.add_cascade(label="View", menu=viewm)
        toolsm = tk.Menu(m, tearoff=0)
        toolsm.add_command(label="Command palette\tCtrl+K",
                           command=self.palette.open)
        toolsm.add_command(label="Set author name…", command=self.set_author)
        toolsm.add_command(label="Toggle offline guard",
                           command=self.toggle_guard)
        m.add_cascade(label="Tools", menu=toolsm)
        helpm = tk.Menu(m, tearoff=0)
        helpm.add_command(label="Keyboard shortcuts\tF1",
                          command=self.show_shortcuts)
        helpm.add_command(label="About", command=self.about)
        m.add_cascade(label="Help", menu=helpm)
        self.root.config(menu=m)

    # ------------------------------------------------------------ commands
    def _register_commands(self):
        p = self.palette
        p.register("Toggle dark mode", "View", self.toggle_dark, "Ctrl+D")
        p.register("Invert PDF colors in markup view", "View", self.toggle_invert)
        p.register("Keyboard shortcuts", "Help", self.show_shortcuts, "F1")
        p.register("About RFI Stamper", "Help", self.about)
        p.register("Set author name (markups)", "Preferences", self.set_author)
        p.register("Toggle offline guard", "Preferences", self.toggle_guard)
        p.register("Toggle status-bar tips", "Preferences", self.toggle_tips)
        for tab, name in ((self.stamp, "Stamp RFIs"),
                          (self.merge, "Combine PDFs"),
                          (self.markup, "Markup & Measure"),
                          (self.compare, "Compare / Overlay")):
            p.register(f"Go to {name}", "Tabs",
                       lambda t=tab: self.nb.select(t))
            p.register_many(tab.commands())

    # ------------------------------------------------------------- actions
    def toggle_dark(self):
        name = self.theme.toggle()
        self.prefs["theme"] = name
        if self.prefs.get("invert_pdf_in_dark"):
            self.markup.viewer.set_invert(name == "dark")

    def toggle_invert(self):
        v = self.markup.viewer
        v.set_invert(not v.invert)
        self.prefs["invert_pdf_in_dark"] = v.invert

    def toggle_guard(self):
        if offline_guard.is_active():
            if not messagebox.askyesno(
                    "Offline guard",
                    "The offline guard blocks every outbound network "
                    "connection from this app — that is the point of it.\n\n"
                    "Really turn it OFF for this session?"):
                return
            offline_guard.uninstall()
            self.prefs["offline_guard"] = False
        else:
            offline_guard.install()
            self.prefs["offline_guard"] = True
        self.status.set_offline(offline_guard.is_active())

    def toggle_tips(self):
        self.prefs["tips"] = not self.prefs.get("tips", True)
        self.status.set("Tips " + ("on (restart to apply)"
                                   if self.prefs["tips"] else "off"), "ok")

    def set_author(self):
        name = simpledialog.askstring(
            "Author", "Name to record on new markups:",
            initialvalue=self.prefs.get("author", ""), parent=self.root)
        if name is not None:
            self.prefs["author"] = name.strip()
            self.markup.author = self.prefs["author"]

    def show_shortcuts(self):
        messagebox.showinfo("Keyboard shortcuts", SHORTCUTS, parent=self.root)

    def about(self):
        messagebox.showinfo(
            "About",
            f"RFI Stamper {__version__} — offline plan toolkit\n\n"
            "Stamp RFI cliff notes onto plan sets, combine and split PDFs,\n"
            "mark up and measure drawings, compare revisions — all locally.\n\n"
            "This application makes no network connections. Documents,\n"
            "markups, and preferences never leave this machine.",
            parent=self.root)

    def on_close(self):
        prefs.save(self.prefs)
        self.root.destroy()


def run():
    root = dnd.make_root()
    App(root)
    root.mainloop()
