"""The Batterboard tab: the one-click layout package (Field Management).

Top to bottom, in the order the brief sets: the drop zone, the table of
what was detected, the two hat buttons, the switches, one Run button, the
stage list with its pass / flag / block lights, and Open Output Folder.

The tab holds no engine logic.  It gathers the drop and the switches into
``batterboard.model.Options``, calls ``batterboard.run.run`` on a worker
thread and shows what came back.  The command line twin (``python -m
rfi_stamper batterboard --options run.json``) reads the same Options from
a JSON file; "Save options file" here writes exactly that file.

House rules that shaped it:

* Every switch on this tab is honored by the engine.  The four scopes the
  package does not lay out are shown disabled and labeled "not built yet":
  no placeholder looks finished.
* A hat that is off greys out the switches that belong to it, with the
  reason beside them.
* Switches are saved per project, in a sidecar beside the project file
  (``<project>.batterboard.json``): the project file format is untouched.
  With no project open only the switches are remembered, on this computer;
  the per-sheet decisions (what a sheet is, its level, its concrete fill
  color) are never carried from one drop to an unrelated one.
* A stage the run never reached is drawn hollow.  It is not a pass.
* The worker thread never touches tk: progress comes back through a queue
  that a run-scoped pump drains, and the pump stops with the run.
"""
from __future__ import annotations

import json
import os
import queue
import tkinter as tk
from tkinter import filedialog, ttk

from ..batterboard import model as M
from ..fsutil import atomic_write_bytes
from . import prefs as prefs_mod
from .theme import FAMILY, section_color
from .widgets import DropZone, LogConsole, Tooltip, make_tree, open_path, run_bg, toast

PRESET_VERSION = 1
PRESET_SUFFIX = ".batterboard.json"
PREFS_KEY = "batterboard"
OUT_FOLDER = "Batterboard output"
SAMPLE_FOLDER = "Planloom Batterboard sample"

HAT_TEXT = {
    "concrete": ("CONCRETE HAT",
                 "wall, column, slab edge, slab opening and shear wall end layout"),
    "surveyor": ("SURVEYOR HAT",
                 "control request sheets, setup plans and check forms"),
}
STAGE_CHOICES = (("pre", "Before the pour"), ("post", "After the pour"), ("both", "Both"))
OUTPUTS = (("out_plates", "Plates"), ("out_booklet", "Phone booklet"),
           ("out_kits", "Point kits"), ("out_legend", "Legend"),
           ("out_rfi_drafts", "RFI drafts"), ("out_report", "Run report"))
#: switches that belong to one hat: (variable name, hat)
HAT_OWNED = (("out_booklet", "concrete"), ("out_rfi_drafts", "concrete"),
             ("masonry", "concrete"))
HOLD_LABEL = {"hold": "Hold the points", "check": "Lay out, marked CHECK",
              "release": "Lay out, no mark"}
HOLD_ROWS = (("answer", "Items that need an answer before layout"),
             ("confirm", "Items to confirm before forming"))
#: what a user may set a PDF page to in the detected table
CLASS_CHOICES = (M.PG_STRUCT, M.PG_ARCH, M.PG_NOTES, M.PG_SCHEDULE, M.PG_SHOP)
AS_READ = "(as read)"
NOT_USED = "do not use"
WAITING, WORKING = "", "working"
LIGHT_WORD = {M.PASS: "pass", M.FLAG: "flag", M.BLOCK: "BLOCKED",
              M.NOT_REACHED: "not reached", WAITING: "waiting", WORKING: "working"}
RESULT_LINE = {
    "PASS": "Every self-check held and nothing is open.",
    "PASS WITH FLAGS": "Every self-check held. Open items or flags exist: they are in the "
                       "open items list and the run report.",
    "BLOCKED": "A gate blocked the run. Nothing was written to the deliverables folder. "
               "The run report says why.",
}
_NO_WHEEL = ("Treeview", "Text", "TCombobox", "Listbox", "TEntry")
ROW_H = 24
#: the on/off switches, by their ``Options`` field name
BOOL_KEYS = ("out_plates", "out_booklet", "out_kits", "out_legend", "out_rfi_drafts",
             "out_report", "masonry", "overlays", "issue", "proof_only")
PROJECT_KEYS = ("project", "drawn_by", "date")


def default_switches() -> dict:
    """The switches as a fresh install has them: the engine's own
    ``Options`` defaults, so the tab cannot drift from the command line."""
    o = M.Options()
    out = {k: getattr(o, k) for k in ("hats", "levels", "stage", "working_range_ft",
                                      *BOOL_KEYS)}
    out["hats"] = list(o.hats)
    out["hold_policy"] = dict(o.hold_policy)
    out["project"] = {k: "" for k in PROJECT_KEYS}
    return out


# ------------------------------------------------------------- presets --

def preset_path(project_path: str) -> str:
    """The switches sidecar beside a project file (the same base the Red
    Tags and names registers use)."""
    return os.path.splitext(project_path)[0] + PRESET_SUFFIX


def read_preset(project_path: str | None) -> dict:
    """The saved switches for a project, or the ones remembered on this
    computer when no project is open.  A missing or unreadable file is an
    empty preset: the defaults then stand."""
    if project_path:
        try:
            with open(preset_path(project_path), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}
    data = prefs_mod.load().get(PREFS_KEY)
    return data if isinstance(data, dict) else {}


def write_preset(project_path: str | None, preset: dict) -> str:
    """Write the preset; returns where it went (a path, or "" for the
    preferences of this computer)."""
    if project_path:
        path = preset_path(project_path)
        atomic_write_bytes(json.dumps(preset, indent=1).encode("utf-8"), path)
        return path
    prefs = prefs_mod.load()
    # with no project the per-sheet decisions are not carried along: they
    # belong to one drop and would silently reclassify an unrelated one
    prefs[PREFS_KEY] = {"version": preset.get("version", PRESET_VERSION),
                        "switches": preset.get("switches", {})}
    prefs_mod.save(prefs)
    return ""


# ------------------------------------------------------------ scroller --

class _Scroller(ttk.Frame):
    """One scrolling column: the tab reads top to bottom on any screen."""

    def __init__(self, parent, theme):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda _e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(
            self._win, width=e.width))
        theme.register(lambda c: self.canvas.configure(bg=c["bg"]))

    def wheel(self, event) -> None:
        if getattr(event, "num", 0) in (4, 5):               # X11
            step = -1 if event.num == 4 else 1
        else:
            step = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(step * 2, "units")

    def bind_wheel(self, widget) -> None:
        """Wheel-scroll the column from every plain widget in it.  Lists
        and text boxes keep their own wheel."""
        if widget.winfo_class() not in _NO_WHEEL:
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                widget.bind(seq, self.wheel, add="+")
        for child in widget.winfo_children():
            self.bind_wheel(child)

    def reveal(self, widget) -> None:
        """Scroll so ``widget`` sits near the top of the view."""
        self.update_idletasks()
        total = max(1, self.body.winfo_height())
        y = widget.winfo_rooty() - self.body.winfo_rooty()
        self.canvas.yview_moveto(max(0.0, min(1.0, (y - 8) / total)))


# ------------------------------------------------------------- the tab --

class BatterboardTab(ttk.Frame):
    """Drop a plan set, pick a hat, flip switches, press Run."""

    def __init__(self, parent, theme, status, root=None, get_project=None):
        super().__init__(parent)
        self.theme, self.status = theme, status
        self.root = root or self.winfo_toplevel()
        self.get_project = get_project
        self.paths: list = []            # dropped files, in drop order
        self.refs: list = []             # what intake made of them
        self.class_of: dict = {}         # sheet or file name -> PG_*
        self.level_of: dict = {}         # sheet or file name -> level label
        self.fill_class: dict = {}       # sheet -> [r, g, b]
        self.result = None               # the last RunResult
        self.error = None                # the last run's exception, if any
        self.light = {n: WAITING for n in M.STAGES}
        self.light_lines: dict = {}
        self.light_first: dict = {}      # stage -> the line shown beside its light
        self.level_vars: dict = {}       # level code -> BooleanVar
        self.level_names: dict = {}      # level code -> label
        self._levels_wanted = None       # None = every level; else a set of codes
        self._running = False
        self._classifying = False
        self._cls_seq = 0
        self._q: queue.Queue | None = None
        self._preset_for = object()      # the project path the switches were loaded for
        self._ask_images: list = []      # PhotoImage references (tk drops unreferenced ones)
        self.ask_win = None

        # every switch starts at the engine's default (set by apply_switches
        # once the widgets exist)
        self.hat_on = {h: False for h in M.HATS}
        self.var = {k: tk.BooleanVar(value=False) for k in BOOL_KEYS}
        self.stage_var = tk.StringVar(value="both")
        self.hold_var = {cat: tk.StringVar(value="") for cat, _ in HOLD_ROWS}
        self.range_var = tk.StringVar(value="")
        self.proj_var = {k: tk.StringVar(value="") for k in PROJECT_KEYS}
        self.out_var = tk.StringVar(value="")

        theme.register(self._style)
        self.scroll = _Scroller(self, theme)
        self.scroll.pack(fill="both", expand=True)
        body = self.scroll.body
        self._build_drop(body)
        self._build_table(body)
        self._build_hats(body)
        self._build_switches(body)
        self._build_run(body)
        self._build_stages(body)
        self._build_output(body)
        self.scroll.bind_wheel(body)
        theme.register(lambda _c: self._draw_lights())
        self.apply_switches(default_switches())
        self.refresh()

    # ------------------------------------------------------------ styling --
    def _style(self, c) -> None:
        s = self.theme.style
        s.configure("Hat.TButton", padding=(22, 12), font=(FAMILY, 12, "bold"),
                    background=c["panel"], foreground=c["muted"])
        s.configure("HatOn.TButton", padding=(22, 12), font=(FAMILY, 12, "bold"),
                    background=c["accent"], foreground=c["accent_fg"])
        s.map("HatOn.TButton", background=[("active", c["accent"])])
        s.configure("Run.TButton", padding=(30, 12), font=(FAMILY, 13, "bold"),
                    background=c["accent"], foreground=c["accent_fg"])
        s.map("Run.TButton",
              background=[("active", c["accent"]), ("disabled", c["bg"])],
              foreground=[("disabled", c["muted"])])
        if getattr(self, "tree", None) is not None:
            self.tree.tag_configure("unknown", foreground=c["err"])
            self.tree.tag_configure("prior", foreground=c["muted"])
            self.tree.tag_configure("user", foreground=c["accent"])

    @staticmethod
    def _step(parent, n: int, title: str, hint: str = "") -> ttk.Frame:
        row = ttk.Frame(parent, padding=(12, 10, 12, 2))
        row.pack(fill="x")
        ttk.Label(row, text=f"{n}  {title}", font=(FAMILY, 11, "bold"),
                  foreground=section_color("field")).pack(side="left")
        if hint:
            ttk.Label(row, style="Muted.TLabel", text="   " + hint).pack(side="left")
        return row

    # ---------------------------------------------------------- 1. the drop --
    def _build_drop(self, body) -> None:
        bar = ttk.Frame(body, padding=(12, 10, 12, 0))
        bar.pack(fill="x")
        ttk.Label(bar, text="▍The Batterboard", font=(FAMILY, 14, "bold"),
                  foreground=section_color("field")).pack(side="left")
        ttk.Label(bar, style="Muted.TLabel",
                  text="  drop a plan set, pick a hat, press Run: plates, point files, "
                       "open items and a plain report").pack(side="left")
        self.sample_btn = ttk.Button(bar, text="Try the sample set",
                                     command=self.load_sample)
        self.sample_btn.pack(side="right")
        Tooltip(self.sample_btn, "Writes a small made-up building (plans, a CAD background, "
                                 "a shop drawing and an RFI) to your Documents folder and "
                                 "loads it here, so a first run needs no files of your own.",
                self.theme)
        row = self._step(body, 1, "Drop the plan set")
        ttk.Button(row, text="Clear", command=self.clear).pack(side="right", padx=(4, 0))
        ttk.Button(row, text="Add a folder…", command=self.browse_folder).pack(
            side="right", padx=(4, 0))
        ttk.Button(row, text="Add files…", command=self.browse).pack(side="right")
        self.drop = DropZone(
            body, self.theme,
            "Drop the plan set here: structural and architectural plans, schedules and "
            "general notes (PDF), CAD backgrounds (DXF), shop drawings and RFI responses",
            self.add_paths, browse=self.browse, height=66)
        self.drop.pack(fill="x", padx=12, pady=(2, 0))

    # --------------------------------------------------- 2. what was detected --
    def _build_table(self, body) -> None:
        self._step(body, 2, "What was detected",
                   "check this before you run: a sheet read wrong is laid out wrong")
        frame, self.tree = make_tree(
            body, self.theme,
            [("file", "FILE"), ("page", "PAGE"), ("sheet", "SHEET"), ("what", "WHAT IT IS"),
             ("level", "LEVEL"), ("rev", "REVISION"), ("why", "HOW IT WAS READ")],
            (230, 50, 80, 170, 90, 80, 360), height=6)
        frame.pack(fill="x", padx=12, pady=(2, 0))
        self._style(self.theme.colors)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())
        fix = ttk.Frame(body, padding=(12, 4, 12, 0))
        fix.pack(fill="x")
        ttk.Label(fix, text="Selected row:", style="Muted.TLabel").pack(side="left")
        self.cls_var = tk.StringVar(value=AS_READ)
        self.cls_box = ttk.Combobox(fix, width=20, state="readonly", textvariable=self.cls_var,
                                    values=[AS_READ, *CLASS_CHOICES, NOT_USED])
        self.cls_box.pack(side="left", padx=(6, 2))
        ttk.Label(fix, text="level", style="Muted.TLabel").pack(side="left", padx=(8, 2))
        self.lvl_var = tk.StringVar(value="")
        self.lvl_box = ttk.Combobox(fix, width=12, textvariable=self.lvl_var, values=[])
        self.lvl_box.pack(side="left")
        self.fix_btn = ttk.Button(fix, text="Apply to the row", command=self.apply_fix)
        self.fix_btn.pack(side="left", padx=6)
        self.remove_btn = ttk.Button(fix, text="Remove the file", command=self.remove_selected)
        self.remove_btn.pack(side="left")
        self.summary = ttk.Label(body, style="Muted.TLabel", text="Nothing dropped yet.",
                                 padding=(12, 4, 12, 0), wraplength=1100, justify="left")
        self.summary.pack(fill="x")

    # ------------------------------------------------------------- 3. hats --
    def _build_hats(self, body) -> None:
        self._step(body, 3, "Hats", "both can be on")
        row = ttk.Frame(body, padding=(12, 2, 12, 0))
        row.pack(fill="x")
        self.hat_btn = {}
        for hat in M.HATS:
            cell = ttk.Frame(row)
            cell.pack(side="left", padx=(0, 18))
            b = ttk.Button(cell, command=lambda h=hat: self.toggle_hat(h))
            b.pack(anchor="w")
            ttk.Label(cell, style="Muted.TLabel", text=HAT_TEXT[hat][1]).pack(anchor="w")
            self.hat_btn[hat] = b
        self.hat_note = ttk.Label(row, style="Muted.TLabel", text="", wraplength=380,
                                  justify="left")
        self.hat_note.pack(side="left", padx=(6, 0))

    # --------------------------------------------------------- 4. switches --
    def _build_switches(self, body) -> None:
        self._step(body, 4, "Switches")
        box = ttk.Frame(body, padding=(12, 0, 12, 0))
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)
        self.sw_widget: dict = {}
        r = [0]

        def line(label) -> ttk.Frame:
            ttk.Label(box, text=label, style="Muted.TLabel").grid(
                row=r[0], column=0, sticky="nw", padx=(0, 14), pady=3)
            f = ttk.Frame(box)
            f.grid(row=r[0], column=1, sticky="w", pady=1)
            r[0] += 1
            return f

        self.levels_row = line("Levels")
        self.levels_hint = ttk.Label(self.levels_row, style="Muted.TLabel",
                                     text="the levels appear here once a plan set is read")
        self.levels_hint.pack(side="left")

        f = line("Stage")
        for val, text in STAGE_CHOICES:
            ttk.Radiobutton(f, text=text, value=val, variable=self.stage_var).pack(
                side="left", padx=(0, 12))

        f = line("Outputs")
        for key, text in OUTPUTS:
            cb = ttk.Checkbutton(f, text=text, variable=self.var[key])
            cb.pack(side="left", padx=(0, 12))
            self.sw_widget[key] = cb

        f = line("Include")
        for key, text in (("masonry", "Masonry from the CAD background"),
                          ("overlays", "Overlays from shop drawings")):
            cb = ttk.Checkbutton(f, text=text, variable=self.var[key])
            cb.pack(side="left", padx=(0, 12))
            self.sw_widget[key] = cb

        f = line("Hold policy")
        for cat, text in HOLD_ROWS:
            ttk.Label(f, text=text + ":").pack(side="left")
            ttk.Combobox(f, width=22, state="readonly", textvariable=self.hold_var[cat],
                         values=list(HOLD_LABEL.values())).pack(side="left", padx=(4, 16))

        f = line("Working range")
        self.range_entry = ttk.Entry(f, width=8, textvariable=self.range_var)
        self.range_entry.pack(side="left")
        ttk.Label(f, text=" ft").pack(side="left")
        self.range_note = ttk.Label(f, style="Muted.TLabel", text="")
        self.range_note.pack(side="left", padx=(10, 0))

        f = line("Issue")
        ttk.Checkbutton(f, text="Issue the plates (they are stamped FOR REVIEW until this "
                                "is on)", variable=self.var["issue"]).pack(side="left",
                                                                           padx=(0, 16))
        ttk.Checkbutton(f, text="One-sheet proof only", variable=self.var["proof_only"]).pack(
            side="left")

        f = line("Title block")
        for key, text, width in (("project", "Project", 26), ("drawn_by", "Drawn by", 12),
                                 ("date", "Date", 12)):
            ttk.Label(f, text=text).pack(side="left")
            ttk.Entry(f, width=width, textvariable=self.proj_var[key]).pack(
                side="left", padx=(4, 14))
        ttk.Label(f, style="Muted.TLabel",
                  text="printed as typed; an empty date prints the day of the run").pack(
            side="left")

        f = line("Not built yet")
        self.unbuilt, self._unbuilt_vars = [], []
        for name in M.NOT_LAID_OUT:
            # the variable is kept: tk forgets an unreferenced one and the box
            # then draws in its third, "mixed" state
            v = tk.BooleanVar(value=False)
            cb = ttk.Checkbutton(f, text=f"{name.capitalize()} (not built yet)",
                                 variable=v, state="disabled")
            cb.pack(side="left", padx=(0, 12))
            self.unbuilt.append(cb)
            self._unbuilt_vars.append(v)

        f = line("Saved")
        self.save_btn = ttk.Button(f, text="Save these switches", command=self.save_preset)
        self.save_btn.pack(side="left")
        self.preset_note = ttk.Label(f, style="Muted.TLabel", text="")
        self.preset_note.pack(side="left", padx=(10, 0))

    # -------------------------------------------------------------- 5. run --
    def _build_run(self, body) -> None:
        self.run_row = self._step(body, 5, "Run")
        out = ttk.Frame(body, padding=(12, 2, 12, 0))
        out.pack(fill="x")
        ttk.Label(out, text="Output folder").pack(side="left")
        ttk.Entry(out, textvariable=self.out_var).pack(side="left", fill="x", expand=True,
                                                       padx=(8, 6))
        ttk.Button(out, text="Browse…", command=self.browse_out).pack(side="left")
        row = ttk.Frame(body, padding=(12, 8, 12, 0))
        row.pack(fill="x")
        self.run_btn = ttk.Button(row, text="RUN THE BATTERBOARD", style="Run.TButton",
                                  command=self.run)
        self.run_btn.pack(side="left")
        res = ttk.Frame(row)
        res.pack(side="left", padx=(16, 0), fill="x", expand=True)
        self.result_head = ttk.Label(res, text="", font=(FAMILY, 15, "bold"))
        self.result_head.pack(anchor="w")
        self.result_line = ttk.Label(res, style="Muted.TLabel", text="Not run yet.",
                                     wraplength=760, justify="left")
        self.result_line.pack(anchor="w")

    # ----------------------------------------------------------- 6. stages --
    def _build_stages(self, body) -> None:
        self._step(body, 6, "Stages", "green pass, amber flag, red block, hollow not "
                                      "reached; click a stage for its lines")
        self.lights = tk.Canvas(body, height=len(M.STAGES) * ROW_H + 12,
                                highlightthickness=0, cursor="hand2")
        self.lights.pack(fill="x", padx=12, pady=(2, 0))
        self.lights.bind("<Configure>", lambda _e: self._draw_lights())
        self.lights.bind("<Button-1>", self._light_click)

    # ----------------------------------------------------------- 7. output --
    def _build_output(self, body) -> None:
        self._step(body, 7, "Output")
        row = ttk.Frame(body, padding=(12, 2, 12, 0))
        row.pack(fill="x")
        self.open_btn = ttk.Button(row, text="Open Output Folder", style="Accent.TButton",
                                   command=self.open_output, state="disabled")
        self.open_btn.pack(side="left")
        self.plates_btn = ttk.Button(row, text="Open the plates", command=self.open_plates,
                                     state="disabled")
        self.plates_btn.pack(side="left", padx=6)
        self.report_btn = ttk.Button(row, text="Open the run report",
                                     command=self.open_report, state="disabled")
        self.report_btn.pack(side="left")
        self.items_btn = ttk.Button(row, text="Open the open items list",
                                    command=self.open_items, state="disabled")
        self.items_btn.pack(side="left", padx=6)
        self.opts_btn = ttk.Button(row, text="Save options file…", command=self.save_options)
        self.opts_btn.pack(side="right")
        Tooltip(self.opts_btn, "Writes the dropped files and these switches as the JSON file "
                               "the command line twin reads:  planloom-cli batterboard "
                               "--options run.json", self.theme)
        self.log = LogConsole(body, self.theme, height=9)
        self.log.pack(fill="x", padx=12, pady=(8, 12))

    # =============================================================== the drop
    def browse(self) -> None:
        got = filedialog.askopenfilenames(
            title="Add plan set files",
            filetypes=[("Plans, CAD backgrounds and RFIs", "*.pdf *.dxf *.txt *.zip"),
                       ("All files", "*.*")])
        if got:
            self.add_paths(list(got))

    def browse_folder(self) -> None:
        got = filedialog.askdirectory(title="Add every file in a folder")
        if got:
            self.add_paths([got])

    def browse_out(self) -> None:
        got = filedialog.askdirectory(title="Output folder")
        if got:
            self.out_var.set(got)

    def add_paths(self, paths) -> None:
        """Take dropped files.  A folder brings the files directly in it
        (the same one-level rule intake uses)."""
        for p in paths or []:
            if not p:
                continue
            p = os.path.abspath(str(p))
            if os.path.isdir(p):
                found = sorted(os.path.join(p, f) for f in os.listdir(p)
                               if os.path.isfile(os.path.join(p, f)))
            else:
                found = [p]
            for f in found:
                if f not in self.paths:
                    self.paths.append(f)
        if self.paths and not self.out_var.get().strip():
            self.out_var.set(self._default_out())
        self.classify()

    def _default_out(self) -> str:
        base = os.path.dirname(self.paths[0]) if self.paths else os.path.expanduser("~")
        return os.path.join(base, OUT_FOLDER)

    def clear(self) -> None:
        self.paths, self.refs = [], []
        self.class_of.clear()
        self.level_of.clear()
        self.fill_class.clear()
        self.out_var.set("")
        self._cls_seq += 1
        self._classifying = False
        self._fill_table()
        self._build_levels()

    def remove_selected(self) -> None:
        files = {self.refs[int(i)].file for i in self.tree.selection() if int(i) < len(self.refs)}
        if not files:
            self.status.set("Select a row first.", "err")
            return
        self.paths = [p for p in self.paths if p not in files]
        self.classify()

    # ------------------------------------------------------------- intake --
    def _decision_opts(self) -> M.Options:
        return M.Options(class_of=dict(self.class_of), level_of=dict(self.level_of))

    def classify(self) -> None:
        """Read what each dropped file is, off the UI thread."""
        self._cls_seq += 1
        seq = self._cls_seq
        paths = list(self.paths)
        if not paths:
            self.refs = []
            self._classifying = False
            self._fill_table()
            self._build_levels()
            return
        opts = self._decision_opts()
        self._classifying = True
        self.summary.configure(text="Reading the drop…")

        def work():
            from ..batterboard import intake
            return intake.classify_drop(paths, opts)

        def done(refs, err):
            if seq != self._cls_seq:
                return                          # a newer drop superseded this read
            self._classifying = False
            if err is not None:
                self.refs = []
                self.summary.configure(text=f"The drop could not be read: {err}")
                self.status.set(f"Batterboard: the drop could not be read: {err}", "err")
            else:
                self.refs = list(refs or [])
            self._fill_table()
            self._build_levels()

        run_bg(self, work, done)

    def _fill_table(self) -> None:
        tree = self.tree
        tree.delete(*tree.get_children())
        counts: dict = {}
        for i, r in enumerate(self.refs):
            what = r.cls
            tag = ()
            if r.cls == M.PG_UNKNOWN:
                what, tag = "not classified, not used", ("unknown",)
            elif r.prior:
                what, tag = f"{r.cls} (earlier issue)", ("prior",)
            elif "set by the user" in (r.why or ""):
                tag = ("user",)
            if r.cls == M.PG_RFI and r.rfi:
                what = f"{r.cls} {r.rfi}"
            counts[r.cls] = counts.get(r.cls, 0) + 1
            tree.insert("", "end", iid=str(i), tags=tag, values=(
                r.name, r.page or "", r.sheet, what, r.level, r.revision, r.why))
        if not self.refs:
            self.summary.configure(text="Nothing dropped yet." if not self.paths
                                   else "Nothing could be read from the drop.")
        else:
            used = sum(n for k, n in counts.items() if k != M.PG_UNKNOWN)
            bits = [f"{n} {k}" for k, n in sorted(counts.items()) if k != M.PG_UNKNOWN]
            tail = (f"; {counts[M.PG_UNKNOWN]} not classified and not used"
                    if counts.get(M.PG_UNKNOWN) else "")
            self.summary.configure(text=f"{used} read: " + ", ".join(bits) + tail)
        self._on_select()

    def _key_of(self, ref) -> str:
        """The key a decision about this row is stored under: the sheet
        number when one was read, else the file name."""
        return ref.sheet or ref.name

    def _on_select(self) -> None:
        sel = [int(i) for i in self.tree.selection() if int(i) < len(self.refs)]
        if not sel:
            self.cls_var.set(AS_READ)
            self.lvl_var.set("")
            return
        key = self._key_of(self.refs[sel[0]])
        cls = self.class_of.get(key)
        self.cls_var.set(NOT_USED if cls == M.PG_UNKNOWN else (cls or AS_READ))
        self.lvl_var.set(self.level_of.get(key, ""))

    def apply_fix(self) -> None:
        """Set what the selected row is and/or its level.  The choice is
        stored by sheet number (or by file name when the page has none),
        shown in the table as "set by the user", and the drop is read again."""
        sel = [int(i) for i in self.tree.selection() if int(i) < len(self.refs)]
        if not sel:
            self.status.set("Select a row first.", "err")
            return
        cls, lvl = self.cls_var.get(), self.lvl_var.get().strip().upper()
        for i in sel:
            ref = self.refs[i]
            key = self._key_of(ref)
            is_page = bool(ref.page) and ref.cls != M.PG_RFI
            if cls == AS_READ or not is_page:
                # a CAD file and an RFI response are each read by their own
                # rule: what they are is not a choice
                self.class_of.pop(key, None)
                if cls != AS_READ:
                    self.status.set(f"{ref.name} is read as a {ref.cls} by its own rule; "
                                    "only its level can be set here.")
            else:
                self.class_of[key] = M.PG_UNKNOWN if cls == NOT_USED else cls
            if lvl:
                self.level_of[key] = lvl
            else:
                self.level_of.pop(key, None)
            if is_page and not ref.sheet:
                self.status.set(f"{ref.name} page {ref.page} has no sheet number, so the "
                                "choice applies to every page of that file.")
        self.classify()

    # -------------------------------------------------------------- levels --
    def _build_levels(self) -> None:
        from ..batterboard import intake
        for w in self.levels_row.winfo_children():
            w.destroy()
        found = {}
        for r in self.refs:
            if r.cls == M.PG_STRUCT and r.level and not r.prior:
                found[intake.level_code(r.level)] = (intake.level_order(r.level), r.level)
        keep = {c: v.get() for c, v in self.level_vars.items()}
        wanted = self._levels_wanted
        self.level_vars, self.level_names = {}, {}
        for code, (_o, name) in sorted(found.items(), key=lambda kv: kv[1]):
            # a box already on screen keeps what the user set; a level seen for
            # the first time takes the saved choice, else it is on
            if code in keep:
                on = keep[code]
            elif wanted is not None:
                on = code in wanted
            else:
                on = True
            v = tk.BooleanVar(value=on)
            self.level_vars[code], self.level_names[code] = v, name
            ttk.Checkbutton(self.levels_row, text=name, variable=v).pack(side="left",
                                                                         padx=(0, 12))
        if found:
            self._levels_wanted = None      # used: from here the boxes are the truth
        else:
            ttk.Label(self.levels_row, style="Muted.TLabel",
                      text="the levels appear here once a structural plan is read").pack(
                side="left")
        self.lvl_box.configure(values=sorted({name for _o, name in found.values()}))
        for child in self.levels_row.winfo_children():
            self.scroll.bind_wheel(child)   # the row itself is bound already

    # ---------------------------------------------------------------- hats --
    def toggle_hat(self, hat: str) -> None:
        self.hat_on[hat] = not self.hat_on[hat]
        self._sync_hats()

    def _sync_hats(self) -> None:
        for hat, b in self.hat_btn.items():
            on = self.hat_on[hat]
            b.configure(text=f"{HAT_TEXT[hat][0]}   {'ON' if on else 'off'}",
                        style="HatOn.TButton" if on else "Hat.TButton")
        for key, hat in HAT_OWNED:
            self.sw_widget[key].configure(state="normal" if self.hat_on[hat] else "disabled")
        surveyor = self.hat_on["surveyor"]
        self.range_entry.configure(state="normal" if surveyor else "disabled")
        self.range_note.configure(
            text="your own setting for the setup plan; empty means not set"
            if surveyor else "used by the Surveyor hat's setup plan (that hat is off)")
        notes = []
        if not any(self.hat_on.values()):
            notes.append("Pick a hat: the run needs at least one.")
        elif not self.hat_on["concrete"]:
            notes.append("Concrete hat off: the phone booklet, the RFI drafts and the "
                         "masonry plate are not built.")
        self.hat_note.configure(text=" ".join(notes))
        if not self._running:
            self.run_btn.configure(state="normal" if any(self.hat_on.values()) else "disabled")

    # ------------------------------------------------------------ switches --
    def switches(self) -> dict:
        """The switch state as plain ``Options`` fields."""
        levels = None
        if self.level_vars:
            on = [c for c, v in self.level_vars.items() if v.get()]
            if len(on) != len(self.level_vars):
                levels = on
        elif self._levels_wanted is not None:
            levels = sorted(self._levels_wanted)
        back = {v: k for k, v in HOLD_LABEL.items()}
        policy = dict(M.DEFAULT_HOLD_POLICY)
        for cat, _ in HOLD_ROWS:
            policy[cat] = back.get(self.hold_var[cat].get(), policy[cat])
        raw = self.range_var.get().strip()
        try:
            rng = float(raw) if raw else None
        except ValueError:
            rng = None
        out = {"hats": [h for h in M.HATS if self.hat_on[h]], "levels": levels,
               "stage": self.stage_var.get(), "hold_policy": policy,
               "working_range_ft": rng,
               "project": {k: v.get().strip() for k, v in self.proj_var.items()}}
        out.update({k: bool(v.get()) for k, v in self.var.items()})
        return out

    def apply_switches(self, sw: dict) -> list:
        """Set the switches from a saved dict.  Returns the names this
        build does not know (they are left out, and said)."""
        known = {"hats", "levels", "stage", "hold_policy", "working_range_ft", "project",
                 *self.var}
        unknown = sorted(set(sw) - known)
        if "hats" in sw:
            hats = [h for h in (sw["hats"] or []) if h in M.HATS]
            self.hat_on = {h: h in hats for h in M.HATS}
        if "levels" in sw:
            lv = sw["levels"]
            self._levels_wanted = None if lv is None else {str(c).upper() for c in lv}
            for code, v in self.level_vars.items():
                v.set(self._levels_wanted is None or code in self._levels_wanted)
        if sw.get("stage") in dict(STAGE_CHOICES):
            self.stage_var.set(sw["stage"])
        for cat, _ in HOLD_ROWS:
            act = (sw.get("hold_policy") or {}).get(cat)
            if act in HOLD_LABEL:
                self.hold_var[cat].set(HOLD_LABEL[act])
        if "working_range_ft" in sw:
            rng = sw["working_range_ft"]
            self.range_var.set("" if rng in (None, "") else f"{float(rng):g}")
        for k, v in (sw.get("project") or {}).items():
            if k in self.proj_var:
                self.proj_var[k].set(str(v or ""))
        for k, v in self.var.items():
            if k in sw:
                v.set(bool(sw[k]))
        self._sync_hats()
        return unknown

    def _project_path(self) -> str | None:
        proj = self.get_project() if self.get_project else None
        return getattr(proj, "path", None) or None

    def preset(self) -> dict:
        return {"version": PRESET_VERSION, "switches": self.switches(),
                "decisions": {"class_of": dict(self.class_of), "level_of": dict(self.level_of),
                              "fill_class": {k: list(v) for k, v in self.fill_class.items()}}}

    def save_preset(self, quiet: bool = False) -> None:
        try:
            where = write_preset(self._project_path(), self.preset())
        except OSError as e:
            self.status.set(f"Batterboard: the switches could not be saved: {e}", "err")
            return
        self._preset_for = self._project_path()
        self._note_preset()
        if not quiet:
            self.status.set("Batterboard: switches saved"
                            + (f" beside the project ({os.path.basename(where)})" if where
                               else " on this computer"), "ok")

    def _note_preset(self) -> None:
        path = self._project_path()
        if path:
            self.preset_note.configure(
                text=f"saved with this project, in {os.path.basename(preset_path(path))}")
        else:
            self.preset_note.configure(
                text="no project is open: saved on this computer. Open a project on Home "
                     "to keep a set of switches with each job.")

    def refresh(self) -> None:
        """Called when the section is shown: when the open project changed,
        load that project's switches."""
        path = self._project_path()
        if path == self._preset_for:
            return
        self._preset_for = path
        data = read_preset(path)
        # A project with no saved switches starts from the defaults: what was
        # on screen for another job never leaks into this one.
        self.apply_switches(default_switches())
        unknown = self.apply_switches(data.get("switches") or {}) if data else []
        if path:
            # the per-sheet decisions belong to the project: another project
            # brings its own, or none
            dec = (data.get("decisions") or {}) if data else {}
            new = (dict(dec.get("class_of") or {}), dict(dec.get("level_of") or {}),
                   {k: list(v) for k, v in (dec.get("fill_class") or {}).items()})
            if new != (self.class_of, self.level_of, self.fill_class):
                self.class_of, self.level_of, self.fill_class = new
                if self.paths:
                    self.classify()
        if unknown:
            self.log.say("The saved switches hold settings this build does not know; they "
                         "were left out: " + ", ".join(unknown))
        proj = self.get_project() if self.get_project else None
        if proj is not None and not self.proj_var["project"].get():
            self.proj_var["project"].set(str(getattr(proj, "name", "") or ""))
        self._note_preset()

    # ----------------------------------------------------------------- run --
    def problems(self) -> list:
        """Why Run cannot start, in plain words (empty = it can)."""
        out = []
        if not self.paths:
            out.append("Drop a plan set first.")
        if not any(self.hat_on.values()):
            out.append("Pick a hat.")
        if self.level_vars and not any(v.get() for v in self.level_vars.values()):
            out.append("Switch on at least one level.")
        raw = self.range_var.get().strip()
        if raw and self.hat_on["surveyor"]:
            try:
                if float(raw) <= 0:
                    raise ValueError
            except ValueError:
                out.append("The working range is a number of feet above zero, or empty.")
        return out

    def options(self) -> M.Options:
        """The switches and decisions as the engine's ``Options``."""
        sw = self.switches()
        project = {k: v for k, v in sw.pop("project").items() if v}
        path = self._project_path()
        if path:
            project["path"] = path          # the registers then live beside the project
        if not self.hat_on["surveyor"]:
            sw["working_range_ft"] = None
        return M.Options(project=project, class_of=dict(self.class_of),
                         level_of=dict(self.level_of),
                         fill_class={k: list(v) for k, v in self.fill_class.items()}, **sw)

    def run(self) -> None:
        if self._running:
            return
        probs = self.problems()
        if probs:
            self._show_result("", " ".join(probs), "err")
            self.status.set("Batterboard: " + " ".join(probs), "err")
            return
        opts = self.options()
        out_root = self.out_var.get().strip() or self._default_out()
        self.out_var.set(out_root)
        self.save_preset(quiet=True)
        paths = list(self.paths)
        self._running = True
        self.result = self.error = None
        self.light = {n: WAITING for n in M.STAGES}
        self.light[M.STAGES[0]] = WORKING
        self.light_lines, self.light_first = {}, {}
        self._draw_lights()
        self.run_btn.configure(state="disabled", text="RUNNING…")
        for b in (self.open_btn, self.plates_btn, self.report_btn, self.items_btn):
            b.configure(state="disabled")
        self._show_result("", "Running. Each stage lights up as it finishes.", "muted")
        self.status.set("Batterboard: running…")
        self.log.clear()
        self.scroll.reveal(self.run_row)
        q = self._q = queue.Queue()
        say = self.log.say

        def work():
            from ..batterboard import run as bb_run
            return bb_run.run(paths, out_root, opts, log=say,
                              progress=lambda name, status: q.put((name, status)))

        run_bg(self, work, self._run_done)
        self.after(120, self._pump)

    def _drain(self) -> None:
        q = self._q
        if q is None:
            return
        changed = False
        try:
            while True:
                name, status = q.get_nowait()
                self.light[name] = status
                changed = True
        except queue.Empty:
            pass
        if changed:
            if self._running:
                for n in M.STAGES:                 # the stage now at work
                    if self.light[n] == WORKING:
                        self.light[n] = WAITING
                nxt = next((n for n in M.STAGES if self.light[n] == WAITING), None)
                if nxt is not None and not any(v in (M.BLOCK, M.NOT_REACHED)
                                               for v in self.light.values()):
                    self.light[nxt] = WORKING
            self._draw_lights()

    def _pump(self) -> None:
        """Run-scoped: drains progress while a run is active, then stops."""
        if not self.winfo_exists():
            return
        self._drain()
        if self._running:
            self.after(120, self._pump)

    def _run_done(self, res, err) -> None:
        self._running = False
        self._drain()
        self.run_btn.configure(text="RUN THE BATTERBOARD")
        self._sync_hats()
        if err is not None or res is None:
            self.error = err
            for n in M.STAGES:
                if self.light[n] == WORKING:
                    self.light[n] = WAITING
            self._draw_lights()
            why = (f"The output folder could not be written, so nothing was delivered: "
                   f"{err}. Pick another output folder." if isinstance(err, OSError)
                   else f"The run stopped on an error and delivered nothing: {err}")
            self._show_result("STOPPED", why, "err")
            self.log.say(f"The run stopped on an error: {err!r}")
            self.status.set(f"Batterboard: the run stopped on an error: {err}", "err")
            return
        self.result = res
        for st in res.stages:
            self.light[st.name] = st.status
            self.light_lines[st.name] = list(st.lines)
            # beside the light: the line that blocked, else the line that
            # flagged, else the first line
            blocked = [ln for ln in st.lines if ln.startswith("BLOCKED")]
            pick = blocked or list(getattr(st, "flagged", None) or []) or list(st.lines)
            self.light_first[st.name] = pick[0] if pick else ""
        self._draw_lights()
        kind = {"PASS": "ok", "PASS WITH FLAGS": "warn"}.get(res.status, "err")
        line = RESULT_LINE.get(res.status, "")
        if res.deliverables:
            line += f"  {len(res.files)} file(s) delivered."
        deliver = res.stage("Deliver")
        if deliver is not None and deliver.status == M.BLOCK:
            line = ("Everything was checked, but the deliverables folder could not be "
                    "replaced. Close any file you have open from that folder, then run "
                    "again. Nothing was changed in it.")
        self._show_result(res.status, line, kind)
        self.open_btn.configure(state="normal")
        self.report_btn.configure(state="normal")
        self.plates_btn.configure(state="normal" if self._plates_path() else "disabled")
        self.items_btn.configure(state="normal" if self._items_path() else "disabled")
        self.status.set(f"Batterboard: {res.status}", "ok" if kind != "err" else "err")
        try:
            toast(self.winfo_toplevel(), self.theme, f"The Batterboard: {res.status}",
                  kind="ok" if kind != "err" else "err")
        except tk.TclError:
            pass
        asks = [a for a in (getattr(res.rr, "asks", None) or [])
                if a.get("kind") == "fill_class"]
        if asks:
            self.show_asks(asks)

    def _show_result(self, head: str, line: str, kind: str) -> None:
        c = self.theme.colors
        self.result_head.configure(text=head, foreground=c.get(kind, c["fg"]))
        self.result_line.configure(text=line)

    # -------------------------------------------------------------- lights --
    def _draw_lights(self) -> None:
        cv = getattr(self, "lights", None)
        if cv is None or not cv.winfo_exists():
            return
        c = self.theme.colors
        cv.configure(bg=c["panel"])
        cv.delete("all")
        w = max(cv.winfo_width(), 320)
        color = {M.PASS: c["ok"], M.FLAG: c["warn"], M.BLOCK: c["err"]}
        room = max(10, int((w - 280) / 6.2))
        for i, name in enumerate(M.STAGES):
            y = 6 + i * ROW_H + ROW_H / 2
            st = self.light.get(name, WAITING)
            col = color.get(st)
            if col:
                cv.create_oval(12, y - 7, 26, y + 7, fill=col, outline=col,
                               tags=("light", f"light-{st}"))
            elif st == WORKING:
                cv.create_oval(12, y - 7, 26, y + 7, outline=c["accent"], width=2.4,
                               tags=("light", "light-working"))
            else:
                cv.create_oval(12, y - 7, 26, y + 7, outline=c["muted"], width=1.2,
                               dash=(2, 2) if st == M.NOT_REACHED else None,
                               tags=("light", "light-hollow"))
            cv.create_text(38, y, anchor="w", text=name, fill=c["fg"],
                           font=(FAMILY, 10, "bold"))
            cv.create_text(150, y, anchor="w", text=LIGHT_WORD.get(st, st),
                           fill=col or c["muted"],
                           font=(FAMILY, 10, "bold" if st == M.BLOCK else "normal"))
            lines = self.light_lines.get(name) or []
            first = self.light_first.get(name, "")
            if len(first) > room:
                first = first[:max(0, room - 1)].rstrip() + "…"
            more = f"   (+{len(lines) - 1} more)" if len(lines) > 1 else ""
            cv.create_text(250, y, anchor="w", text=first + more, fill=c["muted"],
                           font=(FAMILY, 9))

    def _light_click(self, event) -> None:
        i = int((event.y - 6) // ROW_H)
        if 0 <= i < len(M.STAGES):
            self.show_stage(M.STAGES[i])

    def show_stage(self, name: str) -> None:
        """Print one stage's lines into the log."""
        lines = self.light_lines.get(name) or []
        self.log.say(f"--- {name}: {LIGHT_WORD.get(self.light.get(name, WAITING))} ---")
        for ln in lines or ["(nothing recorded)"]:
            self.log.say("  " + ln)

    # -------------------------------------------------------------- output --
    def _plates_path(self) -> str | None:
        res = self.result
        if res is None or not res.deliverables:
            return None
        for name in ("Batterboard_plates.pdf", "Batterboard_one_sheet_proof.pdf"):
            p = os.path.join(res.deliverables, "plates", name)
            if os.path.exists(p):
                return p
        return None

    def _items_path(self) -> str | None:
        res = self.result
        if res is None or not res.deliverables:
            return None
        p = os.path.join(res.deliverables, "open_items", "open_items.csv")
        return p if os.path.exists(p) else None

    def _report_path(self) -> str | None:
        res = self.result
        if res is None:
            return None
        # the paths THIS run wrote: never an earlier run's report that was
        # left in place because another program had it open
        for p in (res.report_pdf, res.report_txt):
            if p and os.path.exists(p):
                return p
        return None

    def _open(self, path) -> None:
        if not path:
            self.status.set("Batterboard: there is nothing to open yet.", "err")
            return
        try:
            open_path(path)
        except Exception as e:   # noqa: BLE001 -- no opener on this system
            self.status.set(f"Could not open {path}: {e}", "err")

    def output_folder(self) -> str | None:
        """The deliverables folder of a delivered run; the output root (it
        holds the run log) of a blocked one."""
        res = self.result
        if res is None:
            return None
        return res.deliverables or res.out_root

    def open_output(self) -> None:
        self._open(self.output_folder())

    def open_plates(self) -> None:
        self._open(self._plates_path())

    def open_report(self) -> None:
        self._open(self._report_path())

    def open_items(self) -> None:
        self._open(self._items_path())

    def options_file(self) -> dict:
        """Exactly what the command line twin reads."""
        data = self.options().to_dict()
        data["files"] = list(self.paths)
        data["out"] = self.out_var.get().strip() or self._default_out()
        return data

    def save_options(self, path: str | None = None) -> None:
        probs = self.problems()
        if probs:
            self.status.set("Batterboard: " + " ".join(probs), "err")
            return
        path = path or filedialog.asksaveasfilename(
            title="Save the options file", defaultextension=".json",
            initialfile="batterboard_run.json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        atomic_write_bytes(json.dumps(self.options_file(), indent=1).encode("utf-8"), path)
        self.status.set(f"Batterboard: options file saved to {path}", "ok")

    # -------------------------------------------------------------- sample --
    @staticmethod
    def sample_dir() -> str:
        home = os.path.expanduser("~")
        docs = os.path.join(home, "Documents")
        return os.path.join(docs if os.path.isdir(docs) else home, SAMPLE_FOLDER)

    def load_sample(self, folder: str | None = None) -> None:
        """Write the made-up sample building to disk and load it as the drop."""
        folder = folder or self.sample_dir()
        self.sample_btn.configure(state="disabled")
        self.status.set("Batterboard: writing the sample set…")

        def work():
            from ..batterboard import sampleset
            return sampleset.build_sample_set(os.path.join(folder, "plan set"))["files"]

        def done(files, err):
            self.sample_btn.configure(state="normal")
            if err is not None:
                self.status.set(f"Batterboard: the sample set could not be written: {err}",
                                "err")
                return
            self.clear()
            self.out_var.set(os.path.join(folder, OUT_FOLDER))
            self.add_paths(files)
            self.log.say(f"The sample set is in {folder}. It is a made-up building: "
                         "press RUN THE BATTERBOARD to see a whole run.")
            self.status.set("Batterboard: sample set loaded. Press RUN.", "ok")

        run_bg(self, work, done)

    # ---------------------------------------------------------------- asks --
    def show_asks(self, asks) -> None:
        """A gate that could not decide alone asks once, with pictures:
        which fill color is the concrete on this sheet."""
        if self.ask_win is not None and self.ask_win.winfo_exists():
            self.ask_win.destroy()
        win = self.ask_win = tk.Toplevel(self.winfo_toplevel())
        win.title("Which fill color is the concrete?")
        c = self.theme.colors
        win.configure(bg=c["bg"])
        self._ask_images = []
        self.ask_buttons = []
        pending = {a["sheet"] for a in asks}
        ttk.Label(win, padding=(14, 12, 14, 4), wraplength=760, justify="left",
                  text="More than one fill color on a sheet could be the concrete. Pick the "
                       "one that is. Your choice is saved with the switches and the run "
                       "starts again.").pack(anchor="w")

        def choose(sheet, rgb):
            self.fill_class[sheet] = list(rgb)
            pending.discard(sheet)
            if not pending:
                win.destroy()
                self.run()

        for a in asks:
            box = ttk.LabelFrame(win, text=f"  Sheet {a['sheet']}  ", padding=10)
            box.pack(fill="x", padx=14, pady=6)
            for cand in a["candidates"]:
                row = ttk.Frame(box)
                row.pack(fill="x", pady=4)
                rgb = tuple(int(v) for v in cand["rgb"])
                sw = tk.Canvas(row, width=44, height=44, highlightthickness=1,
                               highlightbackground=c["border"],
                               bg="#%02x%02x%02x" % rgb)
                sw.pack(side="left")
                ttk.Label(row, text=f"  {cand['shapes']} shape(s), {cand['area_ft2']} sq ft  "
                          ).pack(side="left")
                for png in cand.get("crops", [])[:3]:
                    img = self._crop_image(png)
                    if img is not None:
                        self._ask_images.append(img)
                        tk.Label(row, image=img, bd=1, relief="solid").pack(side="left",
                                                                            padx=3)
                b = ttk.Button(row, text="This is the concrete", style="Accent.TButton",
                               command=lambda s=a["sheet"], r=rgb: choose(s, r))
                b.pack(side="right")
                self.ask_buttons.append(b)
        ttk.Button(win, text="Not now", command=win.destroy).pack(anchor="e", padx=14,
                                                                  pady=(4, 12))

    @staticmethod
    def _crop_image(png: bytes, max_px: int = 240):
        """PNG bytes -> a tk image (through PPM, the house route), shrunk
        to fit a row of the dialog."""
        try:
            import fitz
            pix = fitz.Pixmap(png)
            n = 0
            while max(pix.width, pix.height) >> n > max_px and n < 4:
                n += 1
            if n:
                pix.shrink(n)
            return tk.PhotoImage(data=pix.tobytes("ppm"))
        except Exception:   # noqa: BLE001 -- a crop that cannot be shown is left out
            return None

    # ------------------------------------------------------------ commands --
    def commands(self, show) -> list:
        """Command palette entries; ``show()`` brings this tab forward."""
        def go(fn):
            def run():
                show()
                fn()
            return run
        return [
            ("Batterboard: open the layout package", "Field", show),
            ("Batterboard: try the sample set", "Field", go(self.load_sample)),
            ("Batterboard: add plan set files", "Field", go(self.browse)),
            ("Batterboard: run", "Field", go(self.run)),
            ("Batterboard: open output folder", "Field", go(self.open_output)),
        ]
