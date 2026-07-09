"""Planloom main window: animated section nav, the seven workspaces, project
lifecycle, command palette, full-window drag-drop, offline guard."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from .. import __version__, offline_guard
from ..project import Project
from . import dnd, fx, prefs
from .nav import NavBar
from .oldhand import OldHandDrawer
from .overlay import DropOverlay
from .palette import CommandPalette
from .tab_field import FieldSection
from .tab_home import HomeTab
from .tab_integrations import IntegrationsSection
from .tab_plansbim import PlansSection
from .tab_project import ProjectSection
from .tab_reporting import ReportingSection
from .tab_truth import TruthSection
from .theme import ThemeManager
from .widgets import StatusBar, toast

SECTION_ORDER = ("home", "field", "project", "plans", "reporting",
                 "integrations", "truth")

SHORTCUTS = """\
Ctrl+K        command palette (search every feature)
Ctrl+1..7     jump between workspaces
Ctrl+D        toggle dark mode          F11    fullscreen
Ctrl+Z / Y    undo / redo markup change
Ctrl+M        multiply selected markups
Alt+1..5      set markup status
Del           delete selected markups     Esc   cancel the in-progress tool
V P G L A R E C Q T N M   markup tools
Ctrl+Wheel    zoom at cursor        Middle-drag   pan
The Loft      V W D N F G M R T C L tools · Esc chain · Space rotate/flip
Ctrl+/        the Old Hand — ask the trades (offline, cited)
"""


def resource_path(rel: str) -> str:
    """Locate a bundled asset both from source and from a frozen exe."""
    import sys
    base = getattr(sys, "_MEIPASS",
                   os.path.dirname(os.path.dirname(os.path.dirname(
                       os.path.abspath(__file__)))))
    return os.path.join(base, rel)


class App:
    def __init__(self, root):
        self.root = root
        self.prefs = prefs.load()
        root.title(f"Planloom {__version__} — offline construction workspace")
        root.geometry("1400x900")
        try:
            self._icon = tk.PhotoImage(
                file=resource_path(os.path.join("assets", "planloom.png")))
            root.iconphoto(True, self._icon)
        except Exception:   # noqa: BLE001 -- icon is cosmetic
            pass
        self.theme = ThemeManager(root, self.prefs.get("theme", "dark"))

        eff = self.prefs.get("effects", "auto")
        if eff in ("full", "reduced", "off"):
            fx.set_quality(eff)
        else:                               # "auto" or a corrupt/unknown value
            fx.auto_quality(root)

        if self.prefs.get("offline_guard", True):
            offline_guard.install()

        self.project: Project | None = None

        self.nav = NavBar(root, self.theme, SECTION_ORDER, self._on_switch)
        self.nav.pack(fill="x")
        self.status = StatusBar(root, self.theme,
                                show_tips=self.prefs.get("tips", True))
        self.status.pack(side="bottom", fill="x")
        self.status.set_offline(offline_guard.is_active())
        self.container = ttk.Frame(root)
        self.container.pack(fill="both", expand=True)

        get_project = lambda: self.project           # noqa: E731
        self.home = HomeTab(
            self.container, self.theme, self.status,
            goto_section=self.goto,
            project_ops={"name": lambda: self.project.name
                         if self.project else "", "new": self.new_project,
                         "open": self.open_project},
            recent=self.prefs.get("recent", []), on_recent=self.open_recent)
        self.field = FieldSection(self.container, self.theme, self.status,
                                  get_project, self.data_changed, root=root,
                                  author=self.prefs.get("author", ""))
        self.projsec = ProjectSection(self.container, self.theme, self.status,
                                      root, get_project, self.data_changed)
        self.plans = PlansSection(self.container, self.theme, self.status,
                                  root, author=self.prefs.get("author", ""))
        self.reporting = ReportingSection(self.container, self.theme,
                                          self.status, root, get_project,
                                          self.projsec)
        self.integrations = IntegrationsSection(
            self.container, self.theme, self.status, root, get_project,
            self.data_changed, self.route_paths)
        self.truth = TruthSection(self.container, self.theme, self.status,
                                  get_project, self.projsec)
        self.sections = {"home": self.home, "field": self.field,
                         "project": self.projsec, "plans": self.plans,
                         "reporting": self.reporting,
                         "integrations": self.integrations,
                         "truth": self.truth}
        self._current = "home"
        self.home.place(x=0, y=0, relwidth=1.0, relheight=1.0)

        # drop hints per section for the full-window overlay
        self.home.drop_hint = "Drop it — Planloom routes it to the right tool"
        self.field.drop_hint = "Field Management — drop task CSVs on " \
                               "App Integrations to import"
        self.projsec.drop_hint = "Project Management — plan set first, " \
                                 "then RFI files"
        self.plans.drop_hint = "Open in Plan Viewing"
        self.reporting.drop_hint = "Reporting — generate PDFs from the " \
                                   "buttons inside"
        self.integrations.drop_hint = "App Integrations — drop a CSV to " \
                                      "import tasks"
        self.truth.drop_hint = "Ground Truth reads the project stores — " \
                               "drop files on Home instead"
        self.overlay = DropOverlay(root, self.theme, self._drop_hint,
                                   self._drop_route)

        # the Old Hand: the Heartwood Q&A drawer, reachable from any section
        self.oldhand = OldHandDrawer(
            root, self.theme, self.status,
            get_records=lambda: [r.record for r in
                                 (self.projsec.stamp.rows or [])])
        ttk.Button(self.status, text="⚘ Old Hand", style="Tool.TButton",
                   command=lambda: self.oldhand.toggle()).pack(
            side="right", padx=4)

        # recents + toasts riding on the embedded tools
        self.plans.markup.on_opened = lambda p: self.add_recent(p, "markup")
        self.plans.loft.on_opened = lambda p: self.add_recent(p, "loft")
        # the Weaver learns phrasing through the same Heartwood store
        self.plans.loft.hw_path_provider = lambda: self.oldhand._path()
        # Ground Truth's Heartwood card reads the same store as the drawer
        self.truth.hw_path_provider = lambda: self.oldhand._path()
        # the Backcheck logs recurring findings as Heartwood lessons
        self.plans.backcheck.hw_path_provider = lambda: self.oldhand._path()
        st = self.projsec.stamp
        prev = st.on_scanned
        def scanned(plan, _prev=prev):
            if _prev:
                _prev(plan)
            self.add_recent(plan, "plan")
            self.truth.refresh()
            # lane-2 self-learning: answered RFIs -> unverified shop notes
            try:
                self.oldhand.capture_rfis_async()
            except Exception:   # noqa: BLE001 -- learning must never block
                pass            # the stamping workflow
        st.on_scanned = scanned
        def stamped(ok, _out):
            toast(root, self.theme,
                  "Stamped & verified — statuses woven into the sheets" if ok
                  else "Verification FAILED — do not issue",
                  "ok" if ok else "err")
            self.truth.refresh()
            if ok:
                self.celebrate_verified()
        st.on_stamped = stamped
        self.projsec.merge.on_combined = lambda out, files, pages: (
            self.add_recent(out, "combine"),
            toast(root, self.theme, f"Combined {files} file(s) → {pages} "
                                    f"pages"))
        self.plans.asbuilt.compare.on_compared = lambda out: (
            self.add_recent(out, "compare"),
            toast(root, self.theme, "Overlay PDF written"))

        self.palette = CommandPalette(root, self.theme)
        self._register_commands()
        self.plans.markup.bind_shortcuts(root)
        root.bind("<Control-k>", self.palette.open)
        root.bind("<Control-slash>", lambda e: self.oldhand.toggle())
        root.bind("<Control-d>", lambda e: self.toggle_dark())
        root.bind("<F1>", lambda e: self.show_shortcuts())
        root.bind("<F11>", lambda e: self.toggle_fullscreen())
        for i, key in enumerate(SECTION_ORDER, start=1):
            root.bind(f"<Control-Key-{i}>",
                      lambda e, k=key: self.goto(k))
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build_menu()

        last = self.prefs.get("last_project", "")
        if last and os.path.exists(last):
            self._load_project(last)

        if fx.quality() != "off":
            self.root.after(50, self._warp_up)

    # -------------------------------------------------------- celebration
    def celebrate_verified(self):
        """A rubber-stamp slam: 'VERIFIED' drops onto the screen at an angle,
        thuds to size, then fades — the payoff for a pixel-clean run.
        Quality 'off' skips it entirely."""
        if fx.quality() == "off":
            return
        from .theme import mix
        c = self.theme.colors
        w = max(self.root.winfo_width(), 300)
        h = max(self.root.winfo_height(), 200)
        cv = tk.Canvas(self.root, highlightthickness=0, bg=c["bg"])
        # stipple keeps the workspace visible under the stamp moment
        cv.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        cv.create_rectangle(0, 0, w, h, fill=c["bg"], outline="",
                            stipple="gray25")
        cv.bind("<Button-1>", lambda e: cv.destroy())
        red = c["accent"]

        def draw(t):
            if not cv.winfo_exists():
                return
            cv.delete("stamp")
            # slam: oversized -> settle (ease_out_back overshoot reads as thud)
            size = int(110 - 68 * min(t, 1.0))
            angle = -14 + 8 * min(t, 1.0)
            fill = red if t <= 1.0 else mix(red, c["bg"], (t - 1.0) / 0.6)
            tid = cv.create_text(w / 2, h / 2, text="VERIFIED",
                                 font=("Segoe UI", max(size, 8), "bold"),
                                 fill=fill, angle=angle, tags="stamp")
            x0, y0, x1, y1 = cv.bbox(tid)
            pad = 18
            cv.create_rectangle(x0 - pad, y0 - pad, x1 + pad, y1 + pad,
                                outline=fill, width=5, tags="stamp")
            cv.create_text(w / 2, y1 + pad + 22, tags="stamp",
                           text="nothing covered · pixel-diff clean",
                           font=("Segoe UI", 11), fill=fill)
            cv.tag_raise(tid)

        def phase2():
            fx.animate(cv, "fade", 1.0, 1.6, 500, draw,
                       easing="linear",
                       on_done=lambda: cv.winfo_exists() and cv.destroy())

        fx.animate(cv, "slam", 0.0, 1.0, 380, draw,
                   easing="ease_out_back",
                   on_done=lambda: self.root.after(450, phase2))

    # ------------------------------------------------------------ warp-up
    def _warp_up(self):
        """Boot splash: loom warp threads rise, the wordmark resolves, a
        weft shuttle sweeps under it, then the curtain lifts.  Click skips.
        Runs entirely on fx's scheduler; never shown when quality is off."""
        from .theme import mix
        c = self.theme.colors
        cv = tk.Canvas(self.root, highlightthickness=0, bg=c["bg"])
        cv.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        cv.bind("<Button-1>", lambda e: _finish(True))
        self.root.update_idletasks()
        w = max(cv.winfo_width(), 400)
        h = max(cv.winfo_height(), 300)
        cx, cy = w / 2, h / 2 - 20
        n_threads = 26
        span = min(w * 0.55, 720)
        xs = [cx - span / 2 + span * i / (n_threads - 1)
              for i in range(n_threads)]
        thread_col = mix(c["accent"], c["bg"], 0.55)

        def draw_threads(t):
            if not cv.winfo_exists():
                return
            cv.delete("warp")
            for i, x in enumerate(xs):
                # threads rise outward-in with a slight per-thread delay
                d = abs(i - (n_threads - 1) / 2) / (n_threads / 2)
                tt = max(0.0, min(1.0, (t - d * 0.35) / 0.65))
                if tt <= 0:
                    continue
                y0 = cy + 90
                y1 = y0 - (170 * tt)
                cv.create_line(x, y0, x, y1, fill=thread_col, width=1,
                               tags="warp")

        def draw_mark(t):
            if not cv.winfo_exists():
                return
            cv.delete("mark")
            fg = mix(c["bg"], c["fg"], t)
            ac = mix(c["bg"], c["accent"], t)
            t1 = cv.create_text(cx, cy, text="PLAN", anchor="e", fill=fg,
                                font=("Segoe UI", 34, "bold"), tags="mark")
            cv.create_text(cv.bbox(t1)[2] + 2, cy, text="LOOM", anchor="w",
                           fill=ac, font=("Segoe UI", 34, "bold"),
                           tags="mark")
            cv.create_text(cx, cy + 44, tags="mark",
                           text="weaves the answers into the sheets",
                           fill=mix(c["bg"], c["muted"], t),
                           font=("Segoe UI", 11))
            # weft shuttle sweeping under the wordmark
            sx = cx - span / 2 + span * t
            cv.create_line(cx - span / 2, cy + 66, sx, cy + 66,
                           fill=ac, width=2, tags="mark")
            cv.create_oval(sx - 4, cy + 62, sx + 4, cy + 70, fill=ac,
                           outline="", tags="mark")

        def lift(t):
            if cv.winfo_exists():
                cv.place_configure(y=-int(h * t))

        def _finish(skip=False):
            fx.cancel(cv)
            if cv.winfo_exists():
                if skip:
                    cv.destroy()
                else:
                    fx.animate(cv, "lift", 0.0, 1.0, 340, lift,
                               easing="ease_in_out_cubic",
                               on_done=lambda: cv.winfo_exists()
                               and cv.destroy())

        fx.animate(cv, "warp", 0.0, 1.0, 620, draw_threads,
                   easing="ease_out_quad",
                   on_done=lambda: fx.animate(
                       cv, "mark", 0.0, 1.0, 520, draw_mark,
                       easing="ease_out_quad",
                       on_done=lambda: self.root.after(320, _finish)))

    # ------------------------------------------------------------- routing
    def goto(self, key):
        self.nav.select(key)

    def _on_switch(self, key, direction):
        old = self.sections[self._current]
        new = self.sections[key]
        self._current = key
        fx.slide_switch(self.container, old, new, direction=direction)
        refresh = getattr(new, "refresh", None)
        if refresh:
            self.root.after(80, refresh)

    def _drop_hint(self):
        return getattr(self.sections[self._current], "drop_hint",
                       "Drop files")

    def _drop_route(self, paths):
        sec = self.sections[self._current]
        if self._current == "project":
            self.projsec.stamp.handle_drop(paths)
        elif self._current == "plans":
            self.plans.markup.on_drop(paths)
        elif hasattr(sec, "handle_drop"):
            sec.handle_drop(paths)
        else:
            self.route_paths(paths)

    def route_paths(self, paths):
        lofts = [p for p in paths if p.lower().endswith(".loft.json")]
        if lofts:
            self.goto("plans")
            self.plans.nb.select(self.plans.loft)
            self.plans.loft.open_file(lofts[0])
            return
        pdfs = [p for p in paths if p.lower().endswith(".pdf")
                and not os.path.isdir(p)]
        other = [p for p in paths if p not in pdfs]
        if len(pdfs) == 1 and not other:
            self.goto("plans")
            self.plans.markup.open_pdf(pdfs[0])
        elif len(pdfs) > 1 and not other:
            self.goto("project")
            self.projsec.nb.select(self.projsec.docs_tab)
            self.projsec.merge.add_paths(pdfs)
        elif paths:
            self.goto("project")
            self.projsec.nb.select(0)      # RFIs
            self.projsec.stamp.handle_drop(paths)

    # ------------------------------------------------------------- project
    def data_changed(self):
        self.truth.refresh()

    def new_project(self):
        p = filedialog.asksaveasfilename(
            title="Create project file",
            defaultextension=Project.SUFFIX,
            initialfile="project" + Project.SUFFIX,
            filetypes=[("Planloom project", "*" + Project.SUFFIX)])
        if p:
            self._load_project(p, create=True)

    def open_project(self):
        p = filedialog.askopenfilename(
            filetypes=[("Planloom project", "*" + Project.SUFFIX),
                       ("All", "*.*")])
        if p:
            self._load_project(p)

    def _load_project(self, path, create=False):
        try:
            self.project = Project(path)
            if create:
                self.project.save()
        except Exception as e:      # noqa: BLE001
            messagebox.showerror("Planloom", f"Could not open project:\n{e}")
            return
        self.prefs["last_project"] = path
        prefs.save(self.prefs)
        self.home.set_project_name(self.project.name)
        for sec in (self.field, self.projsec):
            sec.refresh()
        self.truth.refresh()
        self.status.set(f"Project '{self.project.name}' open", "ok")

    # ------------------------------------------------------------- recents
    def add_recent(self, path, kind):
        rec = [r for r in self.prefs.get("recent", [])
               if isinstance(r, dict) and r.get("path") != path]
        rec.insert(0, {"path": path, "kind": kind})
        self.prefs["recent"] = rec[:10]
        prefs.save(self.prefs)
        self.home.show_recent(self.prefs["recent"])

    def open_recent(self, path, kind):
        if not os.path.exists(path):
            toast(self.root, self.theme, "File no longer exists", "err")
            return
        if kind == "plan":
            self.goto("project")
            self.projsec.stamp.plan_var.set(path)
        elif kind == "loft":
            self.goto("plans")
            self.plans.nb.select(self.plans.loft)
            self.plans.loft.open_file(path)
        else:
            self.goto("plans")
            self.plans.markup.open_pdf(path)

    # ---------------------------------------------------------------- menu
    def _build_menu(self):
        m = tk.Menu(self.root)
        filem = tk.Menu(m, tearoff=0)
        filem.add_command(label="New project…", command=self.new_project)
        filem.add_command(label="Open project…", command=self.open_project)
        filem.add_separator()
        filem.add_command(label="Open PDF in Plan Viewing…",
                          command=lambda: (self.goto("plans"),
                                           self.plans.markup.open_pdf()))
        filem.add_separator()
        filem.add_command(label="Exit", command=self.on_close)
        m.add_cascade(label="File", menu=filem)
        viewm = tk.Menu(m, tearoff=0)
        viewm.add_command(label="Toggle dark mode\tCtrl+D",
                          command=self.toggle_dark)
        viewm.add_command(label="Fullscreen\tF11",
                          command=self.toggle_fullscreen)
        effm = tk.Menu(viewm, tearoff=0)
        for q in ("auto", "full", "reduced", "off"):
            effm.add_command(label=q.capitalize(),
                             command=lambda qq=q: self.set_effects(qq))
        viewm.add_cascade(label="Animation quality", menu=effm)
        m.add_cascade(label="View", menu=viewm)
        toolsm = tk.Menu(m, tearoff=0)
        toolsm.add_command(label="Command palette\tCtrl+K",
                           command=self.palette.open)
        toolsm.add_command(label="Crewpass seat ledger…",
                           command=self.crewpass_dialog)
        toolsm.add_command(label="The Old Hand — ask the trades\tCtrl+/",
                           command=lambda: self.oldhand.toggle(True))
        toolsm.add_command(label="Manage the Heartwood…",
                           command=self.oldhand.manage_dialog)
        toolsm.add_separator()
        toolsm.add_command(label="Set author name…", command=self.set_author)
        toolsm.add_command(label="Toggle offline guard",
                           command=self.toggle_guard)
        m.add_cascade(label="Tools", menu=toolsm)
        helpm = tk.Menu(m, tearoff=0)
        helpm.add_command(label="Keyboard shortcuts\tF1",
                          command=self.show_shortcuts)
        helpm.add_command(label="About Planloom", command=self.about)
        m.add_cascade(label="Help", menu=helpm)
        self.root.config(menu=m)

    # ------------------------------------------------------------ commands
    def _register_commands(self):
        p = self.palette
        p.register("Toggle dark mode", "View", self.toggle_dark, "Ctrl+D")
        p.register("Fullscreen", "View", self.toggle_fullscreen, "F11")
        p.register("Keyboard shortcuts", "Help", self.show_shortcuts, "F1")
        p.register("About Planloom", "Help", self.about)
        p.register("Set author name (markups)", "Preferences",
                   self.set_author)
        p.register("Toggle offline guard", "Preferences", self.toggle_guard)
        p.register("Crewpass seat ledger", "Tools", self.crewpass_dialog)
        p.register_many(self.oldhand.commands())
        for q in ("auto", "full", "reduced", "off"):
            p.register(f"Animation quality: {q}", "Preferences",
                       lambda qq=q: self.set_effects(qq))
        for i, key in enumerate(SECTION_ORDER, start=1):
            from .theme import SECTIONS
            p.register(f"Go to {SECTIONS[key]['label']}", "Workspaces",
                       lambda k=key: self.goto(k), f"Ctrl+{i}")
        for sec in self.sections.values():
            cmds = getattr(sec, "commands", None)
            if cmds:
                p.register_many(cmds())

    # ------------------------------------------------------------- actions
    def set_effects(self, q):
        self.prefs["effects"] = q
        if q == "auto":
            q = fx.auto_quality(self.root)
        else:
            fx.set_quality(q)
        self.status.set(f"Animation quality: {q}", "ok")

    def toggle_dark(self):
        name = self.theme.toggle()
        self.prefs["theme"] = name
        if self.prefs.get("invert_pdf_in_dark"):
            self.plans.markup.viewer.set_invert(name == "dark")

    def toggle_fullscreen(self):
        self.root.attributes("-fullscreen",
                             not self.root.attributes("-fullscreen"))

    def toggle_guard(self):
        if offline_guard.is_active():
            if not messagebox.askyesno(
                    "Offline guard",
                    "The offline guard blocks every outbound network "
                    "connection — that is the point of Planloom.\n\n"
                    "Really turn it OFF for this session?"):
                return
            offline_guard.uninstall()
            self.prefs["offline_guard"] = False
        else:
            offline_guard.install()
            self.prefs["offline_guard"] = True
        self.status.set_offline(offline_guard.is_active())

    def crewpass_dialog(self):
        """Offline seat ledger: who runs Planloom on which device.  A local
        JSON registry — no license server, no activation calls, ever."""
        from .. import crewpass
        from .widgets import make_tree, open_path, toast
        ledger = crewpass.Ledger()
        dlg = tk.Toplevel(self.root)
        dlg.title("Crewpass — seat ledger (offline)")
        dlg.transient(self.root)
        dlg.geometry("640x460")
        frm = ttk.Frame(dlg, padding=12)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Crewpass", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frm, style="Muted.TLabel",
                  text="Assign users to devices, transfer seats, print the "
                       "report — all in one local file.").pack(anchor="w")
        frame, tree = make_tree(
            frm, self.theme,
            [("user", "USER"), ("role", "ROLE"), ("device", "DEVICE"),
             ("status", "STATUS")], (150, 80, 170, 90), height=9)
        frame.pack(fill="both", expand=True, pady=8)

        def refresh():
            sel = tree.selection()
            tree.delete(*tree.get_children())
            for s in ledger.seats:
                tree.insert("", "end", iid=s.id, values=(
                    s.user, s.role, s.device or "—",
                    "Active" if s.device else "Released"))
            keep = [iid for iid in sel if tree.exists(iid)]
            if keep:
                tree.selection_set(keep)

        row = ttk.Frame(frm)
        row.pack(fill="x")
        uv, dv = tk.StringVar(), tk.StringVar()
        rv = tk.StringVar(value="field")
        ttk.Entry(row, textvariable=uv, width=16).pack(side="left")
        ttk.Entry(row, textvariable=dv, width=16).pack(side="left", padx=4)
        ttk.Combobox(row, textvariable=rv, values=list(crewpass.ROLES),
                     state="readonly", width=8).pack(side="left")
        ttk.Label(row, text="  user · device · role",
                  style="Muted.TLabel").pack(side="left")

        def assign():
            try:
                ledger.assign(uv.get().strip(), dv.get().strip(), rv.get())
            except ValueError as e:      # bad role / empty / duplicate seat
                messagebox.showwarning("Crewpass", str(e), parent=dlg)
                return
            refresh()

        def transfer():
            sel = tree.selection()
            if not sel:
                return
            nd = simpledialog.askstring("Transfer seat", "New device:",
                                        parent=dlg)
            if nd:
                try:
                    ledger.transfer(sel[0], nd.strip())
                except (KeyError, ValueError) as e:
                    # unknown seat / blank device / user already active there
                    messagebox.showwarning("Crewpass", str(e), parent=dlg)
                refresh()

        def release():
            for iid in tree.selection():
                ledger.release(iid)
            refresh()

        def report():
            out = filedialog.asksaveasfilename(
                parent=dlg, defaultextension=".pdf",
                initialfile="crewpass_report.pdf",
                filetypes=[("PDF", "*.pdf")])
            if out:
                crewpass.report_pdf(ledger, out)
                toast(self.root, self.theme, "Crewpass report ready")
                open_path(out)

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="Assign", style="Accent.TButton",
                   command=assign).pack(side="left")
        ttk.Button(btns, text="Transfer…", command=transfer).pack(
            side="left", padx=4)
        ttk.Button(btns, text="Release", command=release).pack(side="left")
        ttk.Button(btns, text="Report PDF…", command=report).pack(
            side="right")
        refresh()

    def set_author(self):
        name = simpledialog.askstring(
            "Author", "Name to record on new markups:",
            initialvalue=self.prefs.get("author", ""), parent=self.root)
        if name is not None:
            self.prefs["author"] = name.strip()
            self.plans.markup.author = self.prefs["author"]

    def show_shortcuts(self):
        messagebox.showinfo("Keyboard shortcuts", SHORTCUTS,
                            parent=self.root)

    def about(self):
        messagebox.showinfo(
            "About",
            f"Planloom {__version__} — offline construction workspace\n\n"
            "Weaves RFI answers straight into the plan sheets, then wraps "
            "the whole job around them: field management, project "
            "management, plans & BIM, reporting, integrations, and ground "
            "truth — all local, all offline.\n\n"
            "This application makes no network connections. Documents,\n"
            "markups, and project data never leave this machine.",
            parent=self.root)

    def on_close(self):
        from .widgets import busy_count
        if busy_count() > 0 and not messagebox.askyesno(
                "Planloom",
                "Background work is still running (a stamp, combine, or "
                "export could be mid-write).\n\nQuit anyway?"):
            return
        prefs.save(self.prefs)
        self.root.destroy()


def run():
    root = dnd.make_root()
    App(root)
    root.mainloop()
