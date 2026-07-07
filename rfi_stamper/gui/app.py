"""Main window: home + tabs, menu, dark mode, command palette, full-window
drag-drop overlay, toasts, offline guard."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from .. import __version__, offline_guard
from . import dnd, prefs
from .overlay import DropOverlay
from .palette import CommandPalette
from .tab_compare import CompareTab
from .tab_home import HomeTab
from .tab_markup import MarkupTab
from .tab_merge import MergeTab
from .tab_stamp import StampTab
from .theme import ThemeManager
from .widgets import StatusBar, toast

SHORTCUTS = """\
Ctrl+K        command palette (search every feature)
Ctrl+D        toggle dark mode          F11    fullscreen
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
        root.geometry("1360x880")
        self.theme = ThemeManager(root, self.prefs.get("theme", "light"))

        if self.prefs.get("offline_guard", True):
            offline_guard.install()

        self.nb = ttk.Notebook(root)
        self.status = StatusBar(root, self.theme,
                                show_tips=self.prefs.get("tips", True))
        self.status.pack(side="bottom", fill="x")
        self.status.set_offline(offline_guard.is_active())
        self.nb.pack(fill="both", expand=True, padx=6, pady=(6, 0))

        self.stamp = StampTab(self.nb, self.theme, self.status)
        self.merge = MergeTab(self.nb, self.theme, self.status)
        self.markup = MarkupTab(self.nb, self.theme, self.status,
                                author=self.prefs.get("author", ""))
        self.compare = CompareTab(self.nb, self.theme, self.status)
        self.home = HomeTab(
            self.nb, self.theme, self.status,
            actions={"stamp": lambda: self.nb.select(self.stamp),
                     "merge": lambda: self.nb.select(self.merge),
                     "markup": lambda: self.nb.select(self.markup),
                     "compare": lambda: self.nb.select(self.compare)},
            recent=self.prefs.get("recent", []),
            on_recent=self.open_recent)
        self.home.drop_hint = "Drop it — I'll route it to the right tool"
        self.home.set_router(self.route_paths)
        self.nb.add(self.home, text="  ⌂  Home  ")
        self.nb.add(self.stamp, text="  ▣  Stamp RFIs  ")
        self.nb.add(self.merge, text="  ⧉  Combine  ")
        self.nb.add(self.markup, text="  ✎  Markup  ")
        self.nb.add(self.compare, text="  ⇄  Compare  ")

        # completion hooks -> recents + toasts
        self.markup.on_opened = lambda p: self.add_recent(p, "markup")
        self.stamp.on_scanned = lambda p: self.add_recent(p, "plan")
        self.stamp.on_stamped = lambda ok, out: toast(
            self.root, self.theme,
            "Stamped & verified — nothing covered" if ok
            else "Verification FAILED — do not issue",
            "ok" if ok else "err")
        self.merge.on_combined = lambda out, files, pages: (
            self.add_recent(out, "combine"),
            toast(self.root, self.theme,
                  f"Combined {files} file(s) → {pages} pages"))
        self.compare.on_compared = lambda out: (
            self.add_recent(out, "compare"),
            toast(self.root, self.theme, "Overlay PDF written"))

        # full-window drag-and-drop overlay, routed to the active tab
        self.overlay = DropOverlay(root, self.theme, self._drop_hint,
                                   self._drop_route)

        self.palette = CommandPalette(root, self.theme)
        self._register_commands()
        self.markup.bind_shortcuts(root)
        root.bind("<Control-k>", self.palette.open)
        root.bind("<Control-d>", lambda e: self.toggle_dark())
        root.bind("<F1>", lambda e: self.show_shortcuts())
        root.bind("<F11>", lambda e: self.toggle_fullscreen())
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_menu()
        if not dnd.HAS_DND:
            self.status.set("Tip: pip install tkinterdnd2 enables OS drag-and-"
                            "drop (everything also works via Browse)", "info")
        else:
            self.status.set("Ready")

    # ------------------------------------------------------------- routing
    def _active_tab(self):
        try:
            return self.nb.nametowidget(self.nb.select())
        except (tk.TclError, KeyError):
            return self.home

    def _drop_hint(self):
        tab = self._active_tab()
        return getattr(tab, "drop_hint", "Drop files")

    def _drop_route(self, paths):
        tab = self._active_tab()
        if tab is self.home:
            self.route_paths(paths)
        elif hasattr(tab, "handle_drop"):
            tab.handle_drop(paths)

    def route_paths(self, paths):
        """Home-screen smart routing: one PDF -> markup viewer; several PDFs
        -> combine list; folders / RFI-ish files -> the stamp tab."""
        pdfs = [p for p in paths if p.lower().endswith(".pdf")
                and not os.path.isdir(p)]
        other = [p for p in paths if p not in pdfs]
        if len(pdfs) == 1 and not other:
            self.nb.select(self.markup)
            self.markup.open_pdf(pdfs[0])
        elif len(pdfs) > 1 and not other:
            self.nb.select(self.merge)
            self.merge.add_paths(pdfs)
        elif paths:
            self.nb.select(self.stamp)
            self.stamp.handle_drop(paths)

    # ------------------------------------------------------------- recents
    def add_recent(self, path, kind):
        rec = [r for r in self.prefs.get("recent", [])
               if r.get("path") != path]
        rec.insert(0, {"path": path, "kind": kind})
        self.prefs["recent"] = rec[:10]
        prefs.save(self.prefs)
        self.home.show_recent(self.prefs["recent"])

    def open_recent(self, path, kind):
        if not os.path.exists(path):
            toast(self.root, self.theme, "File no longer exists", "err")
            return
        if kind == "plan":
            self.nb.select(self.stamp)
            self.stamp.plan_var.set(path)
        else:
            self.nb.select(self.markup)
            self.markup.open_pdf(path)

    # ---------------------------------------------------------------- menu
    def _build_menu(self):
        m = tk.Menu(self.root)
        filem = tk.Menu(m, tearoff=0)
        filem.add_command(label="Open PDF in Markup tab…",
                          command=lambda: (self.nb.select(self.markup),
                                           self.markup.open_pdf()))
        recentm = tk.Menu(filem, tearoff=0)
        filem.add_cascade(label="Recent", menu=recentm)
        filem.add_separator()
        filem.add_command(label="Exit", command=self.on_close)
        m.add_cascade(label="File", menu=filem)

        def fill_recent():
            recentm.delete(0, "end")
            for r in self.prefs.get("recent", []):
                recentm.add_command(
                    label=os.path.basename(r.get("path", "")),
                    command=lambda rr=r: self.open_recent(rr["path"],
                                                          rr.get("kind", "")))
        filem.configure(postcommand=fill_recent)

        viewm = tk.Menu(m, tearoff=0)
        viewm.add_command(label="Toggle dark mode\tCtrl+D",
                          command=self.toggle_dark)
        viewm.add_command(label="Fullscreen\tF11", command=self.toggle_fullscreen)
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
        p.register("Fullscreen", "View", self.toggle_fullscreen, "F11")
        p.register("Invert PDF colors in markup view", "View", self.toggle_invert)
        p.register("Keyboard shortcuts", "Help", self.show_shortcuts, "F1")
        p.register("About RFI Stamper", "Help", self.about)
        p.register("Set author name (markups)", "Preferences", self.set_author)
        p.register("Toggle offline guard", "Preferences", self.toggle_guard)
        p.register("Toggle status-bar tips", "Preferences", self.toggle_tips)
        for tab, name in ((self.home, "Home"),
                          (self.stamp, "Stamp RFIs"),
                          (self.merge, "Combine PDFs"),
                          (self.markup, "Markup & Measure"),
                          (self.compare, "Compare / Overlay")):
            p.register(f"Go to {name}", "Tabs",
                       lambda t=tab: self.nb.select(t))
            if tab is not self.home:
                p.register_many(tab.commands())

    # ------------------------------------------------------------- actions
    def toggle_dark(self):
        name = self.theme.toggle()
        self.prefs["theme"] = name
        if self.prefs.get("invert_pdf_in_dark"):
            self.markup.viewer.set_invert(name == "dark")

    def toggle_fullscreen(self):
        full = not self.root.attributes("-fullscreen")
        self.root.attributes("-fullscreen", full)

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
        from .widgets import busy_count
        if busy_count() > 0 and not messagebox.askyesno(
                "RFI Stamper",
                "Background work is still running (a stamp, combine, or "
                "export could be mid-write).\n\nQuit anyway?"):
            return
        prefs.save(self.prefs)
        self.root.destroy()


def run():
    root = dnd.make_root()
    App(root)
    root.mainloop()
