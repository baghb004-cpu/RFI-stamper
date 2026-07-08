"""The Loft: draft a plan from a blank sheet — Planloom's original drawing
mode.

Ships lofting to construction: the mold loft was the floor where full-size
lines were drawn before anything was built, and this tab is that floor.  It
deliberately does NOT look like any CAD ribbon: tools live on a compact
"tool spool", the left side is the Binder (plies, stencils, plates), the
right side is the Traits panel, and precision comes from the Plumbline snap
system (endpoint / midpoint / intersection / perpendicular / grid + ortho).

Muscle memory that IS honored (because every drafter expects it): wheel
zoom-at-cursor, middle-drag pan, Esc chain, single-key tool shorthand,
window-vs-crossing box selection (left→right contains, right→left touches),
Shift for temporary ortho, Space to rotate a fixture ghost.

Coordinates: the engine model space is decimal feet, y UP (north up); this
canvas flips y for the screen.  All drawing state lives in
:class:`rfi_stamper.draft.DraftModel`; this file is presentation + input.
"""
from __future__ import annotations

import math
import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from .. import draft
from . import dnd, fx
from .theme import mix, section_color
from .widgets import open_path, run_bg, toast

# tool key -> (label, shorthand key)
TOOLS = (
    ("select", "Select", "v"),
    ("wall", "Wall", "w"),
    ("door", "Door", "d"),
    ("window", "Window", "n"),
    ("fixture", "Fixture", "f"),
    ("pipe", "Pipe", "p"),
    ("grid", "Grid", "g"),
    ("dim", "Dimension", "m"),
    ("room", "Room", "r"),
    ("text", "Text", "t"),
    ("callout", "Callout", "c"),
    ("line", "Line", "l"),
)
TOOL_GROUPS = (("Modify", ("select",)),
               ("Build", ("wall", "door", "window", "fixture")),
               ("Pipe", ("pipe",)),
               ("Datum", ("grid", "dim")),
               ("Note", ("room", "text", "callout", "line")))

HINTS = {
    "select": "Click to select · drag left→right = window, right→left = "
              "crossing · drag a selection to move · Del deletes",
    "wall": "Click the wall start — walls chain until Esc · Shift = ortho",
    "wall2": "Click the wall end — length follows the cursor · Esc ends "
             "the chain",
    "door": "Hover a wall, click to hang the door · Space flips the hand · "
            "Tab flips the swing",
    "window": "Hover a wall, click to set the window",
    "fixture": "Click to place · Space rotates 90° · pick stencils in the "
               "Binder",
    "grid": "Click both ends of the grid line — vertical runs number, "
            "horizontal runs letter",
    "grid2": "Click the far end of the grid line",
    "dim": "Click the two points to dimension",
    "dim2": "Click the second point",
    "dim3": "Click to set the dimension line offset",
    "room": "Type name + number in the options bar, then click the room",
    "text": "Type the note in the options bar, then click to place it",
    "callout": "Set detail + plate in the options bar, then click",
    "line": "Click points · Enter or double-click finishes · Esc cancels",
    "pipe": "Click the run flow-wise (upstream → downstream) · Enter "
            "finishes · Pipewright derives the fittings",
}

# screen widths per pen weight (constant px, like a drafting screen display)
_W_PX = {"fine": 1.0, "light": 1.2, "medium": 1.8, "heavy": 2.4, "cut": 3.0}
_SNAP_LABEL = {"end": "endpoint", "mid": "midpoint", "x": "intersection",
               "perp": "perpendicular", "grid": "grid", "near": "nearest",
               "ortho": "ortho"}


class LoftTab(ttk.Frame):
    """The drafting board: Binder | canvas | Traits, tool spool on top."""

    def __init__(self, parent, theme, status, root, on_bim=None,
                 get_fieldstitch=None):
        super().__init__(parent)
        self.theme, self.status, self.root = theme, status, root
        self.on_bim = on_bim
        self.get_fieldstitch = get_fieldstitch
        self.model = draft.DraftModel()
        self.path: str | None = None
        self.tool = "select"
        self.sel: set[str] = set()
        self._pts: list[tuple] = []       # in-progress clicks (model feet)
        self._hover = None                # snapped cursor (x, y)
        self._snap_hit = None
        self._drag = None                 # select-tool drag state
        self._last_draw = None            # Space in Select repeats this tool
        self._last_stencil = "wc"
        self._plates: list[str] = []      # exports this session
        self.snap_on = {k: tk.BooleanVar(value=True)
                        for k in ("end", "mid", "x", "perp", "grid")}
        self.ortho = tk.BooleanVar(value=False)

        self._build_bars()
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self._build_binder(body)
        self._build_canvas(body)
        self._build_traits(body)
        self._build_strip()

        theme.register(self._on_theme)
        self._select_tool("select")
        self._zoom_extents()
        self.refresh_all()

    # ------------------------------------------------------------- top bars
    def _build_bars(self):
        top = ttk.Frame(self, padding=(8, 6, 8, 0))
        top.pack(fill="x")
        ttk.Label(top, text="▍The Loft", font=("Segoe UI", 13, "bold"),
                  foreground=section_color("plans")).pack(side="left")
        self.file_lbl = ttk.Label(top, style="Muted.TLabel",
                                  text="  new draft — unsaved")
        self.file_lbl.pack(side="left")
        for label, cmd in (("Tally CSV", self.export_tally),
                           ("→ Fieldstitch", self.grids_to_fieldstitch),
                           ("→ 3D model", self.send_to_bim),
                           ("PNG", self.export_png),
                           ("DXF", self.export_dxf),
                           ("Plate PDF", self.export_plate)):
            ttk.Button(top, text=label, style="Tool.TButton",
                       command=cmd).pack(side="right", padx=1)
        ttk.Separator(top, orient="vertical").pack(side="right", fill="y",
                                                   padx=6)
        for label, cmd in (("Save as…", self.save_as), ("Save", self.save),
                           ("Open…", self.open_file), ("New", self.new_file)):
            ttk.Button(top, text=label, style="Tool.TButton",
                       command=cmd).pack(side="right", padx=1)

        spool = ttk.Frame(self, padding=(8, 4, 8, 0))
        spool.pack(fill="x")
        self._tool_btns = {}
        for gi, (gname, keys) in enumerate(TOOL_GROUPS):
            if gi:
                ttk.Separator(spool, orient="vertical").pack(
                    side="left", fill="y", padx=6, pady=2)
            ttk.Label(spool, text=gname, style="Muted.TLabel",
                      font=("Segoe UI", 8)).pack(side="left", padx=(0, 3))
            for key in keys:
                label = dict((k, l) for k, l, s in TOOLS)[key]
                short = dict((k, s) for k, l, s in TOOLS)[key]
                b = ttk.Button(spool, text=f"{label} ({short.upper()})",
                               style="Tool.TButton",
                               command=lambda k=key: self._select_tool(k))
                b.pack(side="left", padx=1)
                self._tool_btns[key] = b

        # per-tool options bar (the contextual strip drafters expect)
        self.opts = ttk.Frame(self, padding=(8, 3, 8, 2))
        self.opts.pack(fill="x")
        self._opt_vars = {}

    def _rebuild_opts(self):
        for w in self.opts.winfo_children():
            w.destroy()
        v = self._opt_vars
        t = self.tool

        def lab(text):
            ttk.Label(self.opts, text=text, style="Muted.TLabel").pack(
                side="left", padx=(8, 2))

        if t == "wall":
            lab("Wall type")
            v["wtype"] = tk.StringVar(value=v.get("wtype",
                                      tk.StringVar(value="stud4")).get())
            keys = list(draft.WALL_TYPES)
            cb = ttk.Combobox(self.opts, width=22, state="readonly",
                              values=[draft.WALL_TYPES[k]["label"]
                                      for k in keys])
            cb.current(keys.index(v["wtype"].get())
                       if v["wtype"].get() in keys else 0)
            cb.bind("<<ComboboxSelected>>", lambda e, ks=keys, c=cb:
                    v["wtype"].set(ks[c.current()]))
            cb.pack(side="left")
        elif t == "door":
            lab("Width")
            v["dwidth"] = tk.StringVar(value=v.get(
                "dwidth", tk.StringVar(value='3\'-0"')).get())
            ttk.Combobox(self.opts, width=7, textvariable=v["dwidth"],
                         values=['2\'-6"', '2\'-8"', '3\'-0"', '3\'-6"']
                         ).pack(side="left")
            lab("Space flips hand · Tab flips swing")
        elif t == "window":
            lab("Width")
            v["wwidth"] = tk.StringVar(value=v.get(
                "wwidth", tk.StringVar(value='4\'-0"')).get())
            ttk.Combobox(self.opts, width=7, textvariable=v["wwidth"],
                         values=['2\'-0"', '3\'-0"', '4\'-0"', '6\'-0"']
                         ).pack(side="left")
        elif t == "fixture":
            lab("Stencil")
            v["stencil"] = tk.StringVar(value=self._last_stencil)
            keys = list(draft.STENCILS)
            cb = ttk.Combobox(self.opts, width=26, state="readonly",
                              values=[draft.STENCILS[k]["label"]
                                      for k in keys])
            cb.current(keys.index(self._last_stencil)
                       if self._last_stencil in keys else 0)
            cb.bind("<<ComboboxSelected>>", lambda e, ks=keys, c=cb:
                    self._set_stencil(ks[c.current()]))
            cb.pack(side="left")
            lab("Rotation")
            v["rot"] = tk.StringVar(value=v.get(
                "rot", tk.StringVar(value="0")).get())
            ttk.Spinbox(self.opts, from_=0, to=315, increment=45, width=5,
                        textvariable=v["rot"]).pack(side="left")
        elif t == "pipe":
            from .. import pipewright as pw
            lab("System")
            v["psys"] = tk.StringVar(value=v.get(
                "psys", tk.StringVar(value="san")).get())
            keys = list(pw.SYSTEMS)
            cbp = ttk.Combobox(self.opts, width=16, state="readonly",
                               values=[pw.SYSTEMS[k]["label"] for k in keys])
            cbp.current(keys.index(v["psys"].get())
                        if v["psys"].get() in keys else 0)
            cbp.bind("<<ComboboxSelected>>", lambda e, ks=keys, c=cbp:
                     v["psys"].set(ks[c.current()]))
            cbp.pack(side="left")
            lab("Size")
            v["pdia"] = tk.StringVar(value=v.get(
                "pdia", tk.StringVar(value="4")).get())
            ttk.Combobox(self.opts, width=5, textvariable=v["pdia"],
                         values=[f"{s:g}" for s in pw.SIZES_IN]
                         ).pack(side="left")
            ttk.Button(self.opts, text="Slope run…", style="Tool.TButton",
                       command=self.pipe_slope).pack(side="left",
                                                     padx=(10, 1))
            ttk.Button(self.opts, text="Cap open ends", style="Tool.TButton",
                       command=self.pipe_cap).pack(side="left", padx=1)
            ttk.Button(self.opts, text="Check ✓", style="Tool.TButton",
                       command=self.pipe_check).pack(side="left", padx=1)
        elif t == "grid":
            lab("Label")
            v["glabel"] = tk.StringVar(value="")
            ttk.Entry(self.opts, width=5, textvariable=v["glabel"]
                      ).pack(side="left")
            lab("(blank = auto: numbers across, letters up — I and O "
                "are skipped)")
        elif t == "room":
            lab("Name")
            v["rname"] = tk.StringVar(value=v.get(
                "rname", tk.StringVar(value="ROOM")).get())
            ttk.Entry(self.opts, width=16, textvariable=v["rname"]
                      ).pack(side="left")
            lab("Number")
            v["rnum"] = tk.StringVar(value=v.get(
                "rnum", tk.StringVar(value="101")).get())
            ttk.Entry(self.opts, width=6, textvariable=v["rnum"]
                      ).pack(side="left")
            lab("number auto-increments per placement")
        elif t == "text":
            lab("Note")
            v["ttext"] = tk.StringVar(value=v.get(
                "ttext", tk.StringVar(value="")).get())
            ttk.Entry(self.opts, width=34, textvariable=v["ttext"]
                      ).pack(side="left")
            lab("Size")
            v["tsize"] = tk.StringVar(value=v.get(
                "tsize", tk.StringVar(value="body")).get())
            ttk.Combobox(self.opts, width=6, state="readonly",
                         textvariable=v["tsize"],
                         values=["body", "sub", "title"]).pack(side="left")
        elif t == "callout":
            lab("Detail")
            v["cdet"] = tk.StringVar(value=v.get(
                "cdet", tk.StringVar(value="1")).get())
            ttk.Entry(self.opts, width=4, textvariable=v["cdet"]
                      ).pack(side="left")
            lab("on plate")
            v["csheet"] = tk.StringVar(value=v.get(
                "csheet", tk.StringVar(value="A-501")).get())
            ttk.Entry(self.opts, width=8, textvariable=v["csheet"]
                      ).pack(side="left")
        else:
            ttk.Label(self.opts, text=HINTS.get(t, ""),
                      style="Muted.TLabel").pack(side="left")

    def _set_stencil(self, key):
        self._last_stencil = key
        if "stencil" in self._opt_vars:
            self._opt_vars["stencil"].set(key)

    # -------------------------------------------------------------- binder
    def _build_binder(self, body):
        left = ttk.Frame(body, width=210)
        left.pack(side="left", fill="y", padx=(8, 0), pady=4)
        left.pack_propagate(False)
        row = ttk.Frame(left)
        row.pack(fill="x")
        ttk.Label(row, text="Binder", font=("Segoe UI", 10, "bold")
                  ).pack(side="left")
        self._find = tk.StringVar()
        e = ttk.Entry(row, width=10, textvariable=self._find)
        e.pack(side="right")
        e.bind("<KeyRelease>", lambda _e: self._fill_binder())
        self.binder = ttk.Treeview(left, show="tree", selectmode="browse")
        self.binder.pack(fill="both", expand=True, pady=(4, 0))
        self.binder.bind("<Double-Button-1>", self._binder_double)
        self.binder.bind("<<TreeviewSelect>>", self._binder_select)
        self._fill_binder()

    def _fill_binder(self):
        tr = self.binder
        open_state = {iid: tr.item(iid, "open")
                      for iid in ("plies", "stencils", "plates")
                      if tr.exists(iid)}
        tr.delete(*tr.get_children())
        needle = self._find.get().strip().lower()

        tr.insert("", "end", iid="plies", text="Plies (layers)",
                  open=open_state.get("plies", True))
        for ply in self.model.plies:
            state = "✓" if ply.visible else "–"
            extra = " ·half" if ply.halftone else ""
            extra += " ·lock" if ply.locked else ""
            iid = f"ply:{ply.name}"
            tr.insert("plies", "end", iid=iid,
                      text=f" {state} {ply.name}  {ply.weight}{extra}",
                      tags=(iid,))
            tr.tag_configure(iid, foreground=ply.color)

        tr.insert("", "end", iid="stencils", text="Stencils",
                  open=open_state.get("stencils", bool(needle)))
        cats: dict[str, list] = {}
        for key, st in draft.STENCILS.items():
            if needle and needle not in st["label"].lower():
                continue
            cats.setdefault(st["cat"], []).append((key, st["label"]))
        for cat in sorted(cats):
            cid = f"cat:{cat}"
            tr.insert("stencils", "end", iid=cid, text=cat.capitalize(),
                      open=bool(needle))
            for key, label in cats[cat]:
                tr.insert(cid, "end", iid=f"st:{key}", text=f"  {label}")

        tr.insert("", "end", iid="plates", text="Plates (exports)",
                  open=open_state.get("plates", True))
        for p in self._plates:
            tr.insert("plates", "end", iid=f"plate:{p}",
                      text=f"  {os.path.basename(p)}")

    def _binder_select(self, _e):
        sel = self.binder.selection()
        if sel and sel[0].startswith("st:"):
            self._set_stencil(sel[0][3:])
            self._select_tool("fixture")

    def _binder_double(self, _e):
        sel = self.binder.selection()
        if not sel:
            return
        iid = sel[0]
        if iid.startswith("ply:"):
            ply = self.model.ply(iid[4:])
            if ply:
                ply.visible = not ply.visible
                self.refresh_all()
        elif iid.startswith("plate:"):
            open_path(iid[6:])

    # -------------------------------------------------------------- canvas
    def _build_canvas(self, body):
        self.cv = tk.Canvas(body, highlightthickness=0, cursor="crosshair")
        self.cv.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        self.theme.style_canvas(self.cv)
        # view transform: ppf px/foot; (vx, vy) = model coords at canvas
        # top-left (y up, so vy is the HIGHEST visible model y)
        self.ppf = 8.0
        self.vx, self.vy = -10.0, 60.0
        cv = self.cv
        cv.bind("<ButtonPress-1>", self._on_press)
        cv.bind("<B1-Motion>", self._on_drag1)
        cv.bind("<ButtonRelease-1>", self._on_release)
        cv.bind("<Double-Button-1>", self._on_double)
        cv.bind("<Motion>", self._on_motion)
        cv.bind("<ButtonPress-2>", self._pan_start)
        cv.bind("<B2-Motion>", self._pan_move)
        cv.bind("<MouseWheel>", self._on_wheel)
        cv.bind("<Button-4>", lambda e: self._zoom_at(e.x, e.y, 1.15))
        cv.bind("<Button-5>", lambda e: self._zoom_at(e.x, e.y, 1 / 1.15))
        cv.bind("<Configure>", lambda e: self.redraw())
        for key, _label, short in TOOLS:
            cv.bind(f"<Key-{short}>", lambda e, k=key: self._select_tool(k))
        cv.bind("<Escape>", lambda e: self._escape())
        cv.bind("<Return>", lambda e: self._finish_poly())
        cv.bind("<space>", self._on_space)
        cv.bind("<Tab>", self._on_tab)
        cv.bind("<Delete>", lambda e: self.delete_selection())
        cv.bind("<Control-z>", lambda e: self._undo())
        cv.bind("<Control-y>", lambda e: self._redo())
        for k, dx, dy in (("Left", -1, 0), ("Right", 1, 0),
                          ("Up", 0, 1), ("Down", 0, -1)):
            cv.bind(f"<Key-{k}>", lambda e, a=dx, b=dy: self._nudge(a, b))
        dnd.enable_drop(cv, self._on_drop, exts=(".json",))

    def to_screen(self, x, y):
        return ((x - self.vx) * self.ppf, (self.vy - y) * self.ppf)

    def to_model(self, sx, sy):
        return (self.vx + sx / self.ppf, self.vy - sy / self.ppf)

    def _pan_start(self, e):
        self._pan = (e.x, e.y, self.vx, self.vy)

    def _pan_move(self, e):
        sx, sy, vx, vy = self._pan
        self.vx = vx - (e.x - sx) / self.ppf
        self.vy = vy + (e.y - sy) / self.ppf
        self.redraw()

    def _on_wheel(self, e):
        self._zoom_at(e.x, e.y, 1.15 if e.delta > 0 else 1 / 1.15)

    def _zoom_at(self, sx, sy, factor):
        mx, my = self.to_model(sx, sy)
        self.ppf = max(0.05, min(400.0, self.ppf * factor))
        self.vx = mx - sx / self.ppf
        self.vy = my + sy / self.ppf
        self.redraw()

    def _zoom_extents(self):
        b = self.model.bounds(margin_ft=6.0)
        if not b:
            b = (-10.0, -10.0, 50.0, 40.0)
        w = max(self.cv.winfo_width(), 400)
        h = max(self.cv.winfo_height(), 300)
        self.ppf = max(0.05, min(400.0, min(w / max(b[2] - b[0], 1.0),
                                            h / max(b[3] - b[1], 1.0))))
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        self.vx = cx - w / 2 / self.ppf
        self.vy = cy + h / 2 / self.ppf
        self.redraw()

    # ------------------------------------------------------------- drawing
    def redraw(self):
        cv = self.cv
        if not cv.winfo_exists():
            return
        cv.delete("all")
        self._draw_paper_grid()
        plies = {p.name: p for p in self.model.plies}
        ratio = self.model.scale_ratio
        for op in draft.render_ops(self.model):
            self._draw_op(op, plies, ratio)
        self._draw_selection(plies)
        self._draw_overlay()

    def _ply_color(self, ply, plies):
        c = self.theme.colors
        p = plies.get(ply)
        if p is None:
            return c["fg"]
        col = p.color
        if p.halftone:
            col = mix(col, c["canvas_bg"], 0.62)
        return col

    def _dash_px(self, ltype, ratio):
        pat = draft.LINETYPES.get(ltype) or ()
        if not pat:
            return None
        return tuple(max(2, int(v * ratio / 12.0 * self.ppf)) for v in pat)

    def _draw_op(self, op, plies, ratio, tags="model", override=None):
        cv = self.cv
        kind = op[0]
        if kind == "line":
            _, x1, y1, x2, y2, ply, weight, lt = op
            a, b = self.to_screen(x1, y1), self.to_screen(x2, y2)
            cv.create_line(a[0], a[1], b[0], b[1],
                           fill=override or self._ply_color(ply, plies),
                           width=_W_PX.get(weight, 1.2),
                           dash=self._dash_px(lt, ratio), tags=tags)
        elif kind == "circle":
            _, cx, cy, r, ply, weight, lt = op
            a = self.to_screen(cx - r, cy + r)
            b = self.to_screen(cx + r, cy - r)
            cv.create_oval(a[0], a[1], b[0], b[1],
                           outline=override or self._ply_color(ply, plies),
                           width=_W_PX.get(weight, 1.2),
                           dash=self._dash_px(lt, ratio), tags=tags)
        elif kind == "ellipse":
            _, cx, cy, rx, ry, ply, weight, lt = op
            a = self.to_screen(cx - rx, cy + ry)
            b = self.to_screen(cx + rx, cy - ry)
            cv.create_oval(a[0], a[1], b[0], b[1],
                           outline=override or self._ply_color(ply, plies),
                           width=_W_PX.get(weight, 1.2), tags=tags)
        elif kind == "arc":
            _, cx, cy, r, a0, a1, ply, weight, lt = op
            a = self.to_screen(cx - r, cy + r)
            b = self.to_screen(cx + r, cy - r)
            cv.create_arc(a[0], a[1], b[0], b[1], start=a0,
                          extent=(a1 - a0), style="arc",
                          outline=override or self._ply_color(ply, plies),
                          width=_W_PX.get(weight, 1.2), tags=tags)
        elif kind == "text":
            _, x, y, s, size_key, ply, anchor, angle = op
            sx, sy = self.to_screen(x, y)
            h_px = draft.text_model_h(size_key, ratio) * self.ppf
            cv.create_text(sx, sy, text=s,
                           fill=override or self._ply_color(ply, plies),
                           font=("Segoe UI", -max(int(h_px), 7)),
                           anchor={"c": "center", "w": "w", "e": "e"
                                   }.get(anchor, "center"),
                           angle=angle, tags=tags)

    def _draw_paper_grid(self):
        """Drafting-paper feel: faint feet grid that fades in with zoom, plus
        the origin cross — never heavier than the linework above it."""
        cv, c = self.cv, self.theme.colors
        w, h = cv.winfo_width(), cv.winfo_height()
        col = mix(c["border"], c["canvas_bg"], 0.55)
        col2 = mix(c["border"], c["canvas_bg"], 0.25)
        step = 1.0 if self.ppf >= 24 else 5.0 if self.ppf >= 6 else \
            10.0 if self.ppf >= 2.4 else 50.0
        x0, y1 = self.to_model(0, 0)
        x1, y0 = self.to_model(w, h)
        gx = math.floor(x0 / step) * step
        while gx <= x1:
            sx = (gx - self.vx) * self.ppf
            major = abs(gx % (step * 5)) < 1e-9
            cv.create_line(sx, 0, sx, h, fill=col2 if major else col)
            gx += step
        gy = math.floor(y0 / step) * step
        while gy <= y1:
            sy = (self.vy - gy) * self.ppf
            major = abs(gy % (step * 5)) < 1e-9
            cv.create_line(0, sy, w, sy, fill=col2 if major else col)
            gy += step
        ox, oy = self.to_screen(0, 0)
        cv.create_line(ox - 14, oy, ox + 14, oy, fill=c["muted"])
        cv.create_line(ox, oy - 14, ox, oy + 14, fill=c["muted"])

    def _draw_selection(self, plies):
        if not self.sel:
            return
        cv, c = self.cv, self.theme.colors
        for eid in self.sel:
            ent = self.model.entity(eid)
            if ent is None:
                continue
            for x, y in self._ent_anchor_pts(ent):
                sx, sy = self.to_screen(x, y)
                cv.create_rectangle(sx - 4, sy - 4, sx + 4, sy + 4,
                                    outline=c["accent"], width=1.6,
                                    tags="model")
            for op in self._ent_ops(ent):
                self._draw_op(op, plies, self.model.scale_ratio,
                              override=c["accent"])

    def _ent_ops(self, ent):
        """Ops for one entity — used for the selection halo."""
        try:
            return draft.render_ops(self.model, include=("ent:" + ent.id,))
        except Exception:   # noqa: BLE001 -- halo is cosmetic
            return []

    # --------------------------------------------------------- hit testing
    @staticmethod
    def _seg_d(px, py, a, b):
        ax, ay = a
        bx, by = b
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy
        if ll <= 1e-12:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / ll))
        return math.hypot(px - (ax + t * dx), py - (ay + t * dy))

    def _ent_anchor_pts(self, ent):
        if ent.kind in ("door", "window"):
            host = self.model.entity(ent.props.get("host", ""))
            if host and len(host.pts) >= 2:
                t = float(ent.props.get("t", 0.5))
                (ax, ay), (bx, by) = host.pts[0], host.pts[1]
                return [(ax + t * (bx - ax), ay + t * (by - ay))]
            return []
        return list(ent.pts)

    def _hit(self, x, y):
        tol = 8.0 / self.ppf
        best, bd = None, tol
        for ent in self.model.ents:
            ply = self.model.ply(ent.ply)
            if ply is not None and (not ply.visible or ply.locked):
                continue
            d = None
            if ent.kind in ("wall", "line", "grid", "pipe"):
                pts = ent.pts
                for a, b in zip(pts, pts[1:]):
                    dd = self._seg_d(x, y, a, b)
                    d = dd if d is None else min(d, dd)
                if ent.kind == "wall" and d is not None:
                    d -= float(ent.props.get("thick_in", 5)) / 24.0
            elif ent.kind == "dim" and len(ent.pts) >= 2:
                d = min(self._seg_d(x, y, ent.pts[0], ent.pts[1]),
                        min(math.hypot(x - p[0], y - p[1])
                            for p in ent.pts))
            else:
                anchors = self._ent_anchor_pts(ent)
                if anchors:
                    d = min(math.hypot(x - p[0], y - p[1]) for p in anchors)
                    d -= 12.0 / self.ppf   # generous grab ring for symbols
            if d is not None and d < bd:
                best, bd = ent, d
        return best

    # ---------------------------------------------------------- interaction
    def _enabled_snaps(self):
        return {k for k, var in self.snap_on.items() if var.get()}

    def _snapped(self, e, anchor=None):
        x, y = self.to_model(e.x, e.y)
        shift = bool(e.state & 0x0001)
        hit = draft.snap(self.model, x, y, 10.0 / self.ppf,
                         anchor=anchor,
                         ortho=self.ortho.get() or shift,
                         enabled=self._enabled_snaps())
        if hit:
            self._snap_hit = hit
            return hit.x, hit.y
        self._snap_hit = None
        return x, y

    def _on_motion(self, e):
        anchor = self._pts[-1] if self._pts else None
        self._hover = self._snapped(e, anchor)
        x, y = self._hover
        self.pos_lbl.configure(
            text=f"{draft.fmt_ftin(x)} , {draft.fmt_ftin(y)}")
        self._draw_overlay()

    def _on_press(self, e):
        self.cv.focus_set()
        if self.tool == "select":
            self._press_select(e)
            return
        anchor = self._pts[-1] if self._pts else None
        x, y = self._snapped(e, anchor)
        getattr(self, f"_click_{self.tool}")(x, y, e)
        self._draw_overlay()

    def _on_drag1(self, e):
        if self.tool == "select" and self._drag:
            kind = self._drag[0]
            if kind == "box":
                self._drag = ("box", self._drag[1], (e.x, e.y))
            elif kind == "move":
                pass
            self._hover = self.to_model(e.x, e.y)
            self._draw_overlay()

    def _on_release(self, e):
        if self.tool != "select" or not self._drag:
            return
        kind = self._drag[0]
        if kind == "box":
            sx, sy = self._drag[1]
            ex, ey = self._drag[2] if len(self._drag) > 2 else (e.x, e.y)
            self._box_select(sx, sy, ex, ey, add=bool(e.state & 0x0001))
        elif kind == "move":
            eid, (mx0, my0) = self._drag[1], self._drag[2]
            mx1, my1 = self._snapped(e)
            dx, dy = mx1 - mx0, my1 - my0
            if abs(dx) > 1e-9 or abs(dy) > 1e-9:
                self.model.move(list(self.sel), dx, dy)
                self._after_mutate()
        self._drag = None
        self._draw_overlay()

    def _press_select(self, e):
        x, y = self.to_model(e.x, e.y)
        ent = self._hit(x, y)
        shift = bool(e.state & 0x0001)
        if ent is None:
            if not shift:
                self.sel.clear()
            self._drag = ("box", (e.x, e.y))
            self.redraw()
            self._traits_refresh()
            return
        if ent.id in self.sel:
            self._drag = ("move", ent.id, self._snapped(e))
            return
        if not shift:
            self.sel.clear()
        self.sel.add(ent.id)
        self._drag = ("move", ent.id, self._snapped(e))
        self.redraw()
        self._traits_refresh()

    def _box_select(self, sx0, sy0, sx1, sy1, add=False):
        crossing = sx1 < sx0            # right→left touches, left→right holds
        x0, y0 = self.to_model(min(sx0, sx1), max(sy0, sy1))
        x1, y1 = self.to_model(max(sx0, sx1), min(sy0, sy1))
        if not add:
            self.sel.clear()
        for ent in self.model.ents:
            ply = self.model.ply(ent.ply)
            if ply is not None and (not ply.visible or ply.locked):
                continue
            pts = self._ent_anchor_pts(ent) or ent.pts
            if not pts:
                continue
            inside = [x0 <= p[0] <= x1 and y0 <= p[1] <= y1 for p in pts]
            if (all(inside) if not crossing else any(inside)):
                self.sel.add(ent.id)
        self.redraw()
        self._traits_refresh()

    # tool click handlers ---------------------------------------------------
    def _click_wall(self, x, y, _e):
        if not self._pts:
            self._pts = [(x, y)]
            self._hint(HINTS["wall2"])
            return
        a = self._pts[-1]
        if math.hypot(x - a[0], y - a[1]) < 0.05:
            return
        self.model.add("wall", [a, (x, y)],
                       wtype=self._opt_vars["wtype"].get()
                       if "wtype" in self._opt_vars else "stud4")
        self._pts = [(x, y)]           # walls chain like a drafter draws
        self._after_mutate()
        self._flourish_seg(a, (x, y))

    def _click_line(self, x, y, _e):
        self._pts.append((x, y))
        self._hint(HINTS["line"])

    def _click_pipe(self, x, y, _e):
        self._pts.append((x, y))
        self._hint(HINTS["pipe"])

    def _finish_poly(self):
        if self.tool == "line" and len(self._pts) >= 2:
            self.model.add("line", list(self._pts))
            self._after_mutate()
        elif self.tool == "pipe" and len(self._pts) >= 2:
            v = self._opt_vars
            try:
                dia = float(v["pdia"].get()) if "pdia" in v else 4.0
            except ValueError:
                dia = 4.0
            ent = self.model.add("pipe", list(self._pts),
                                 system=v["psys"].get()
                                 if "psys" in v else "san", dia_in=dia)
            self._after_mutate()
            self._flourish_seg(self._pts[0], self._pts[-1])
            self.sel = {ent.id}
        self._pts = []
        self._draw_overlay()

    def _on_double(self, e):
        if self.tool in ("line", "pipe"):
            self._finish_poly()

    def _nearest_wall(self, x, y):
        best, bt, bd = None, 0.0, 14.0 / self.ppf
        for ent in self.model.ents:
            if ent.kind != "wall":
                continue
            (ax, ay), (bx, by) = ent.pts[0], ent.pts[1]
            dx, dy = bx - ax, by - ay
            ll = dx * dx + dy * dy
            if ll <= 1e-12:
                continue
            t = max(0.05, min(0.95,
                              ((x - ax) * dx + (y - ay) * dy) / ll))
            d = math.hypot(x - (ax + t * dx), y - (ay + t * dy))
            if d < bd:
                best, bt, bd = ent, t, d
        return best, bt

    def _click_door(self, x, y, _e):
        host, t = self._nearest_wall(x, y)
        if host is None:
            self._hint("No wall there — hover a wall to hang the door")
            return
        v = self._opt_vars
        width = draft.parse_ftin(v["dwidth"].get()) if "dwidth" in v else None
        self.model.add("door", [], host=host.id, t=t,
                       width_in=(width or 3.0) * 12.0,
                       swing=getattr(self, "_door_swing", "in"),
                       hand=getattr(self, "_door_hand", "l"))
        self._after_mutate()

    def _click_window(self, x, y, _e):
        host, t = self._nearest_wall(x, y)
        if host is None:
            self._hint("No wall there — hover a wall to set the window")
            return
        v = self._opt_vars
        width = draft.parse_ftin(v["wwidth"].get()) if "wwidth" in v else None
        self.model.add("window", [], host=host.id, t=t,
                       width_in=(width or 4.0) * 12.0)
        self._after_mutate()

    def _click_fixture(self, x, y, _e):
        v = self._opt_vars
        rot = 0.0
        try:
            rot = float(v["rot"].get()) if "rot" in v else 0.0
        except ValueError:
            pass
        self.model.add("fixture", [(x, y)], stencil=self._last_stencil,
                       rot=rot, flip=False)
        self._after_mutate()
        self._flourish_ring(x, y)

    def _click_grid(self, x, y, e):
        if not self._pts:
            self._pts = [(x, y)]
            self._hint(HINTS["grid2"])
            return
        a = self._pts.pop(0)
        # grids are datum lines: hard-ortho unless Shift objects
        if not (e.state & 0x0001):
            if abs(x - a[0]) > abs(y - a[1]):
                y = a[1]
            else:
                x = a[0]
        axis = "num" if abs(x - a[0]) < abs(y - a[1]) else "alpha"
        label = (self._opt_vars.get("glabel") and
                 self._opt_vars["glabel"].get().strip()) or \
            self.model.next_grid_label(axis)
        if self._opt_vars.get("glabel"):
            self._opt_vars["glabel"].set("")
        self.model.add("grid", [a, (x, y)], label=label, bubble="both")
        self._after_mutate()
        self._hint(HINTS["grid"])

    def _click_dim(self, x, y, _e):
        self._pts.append((x, y))
        if len(self._pts) == 1:
            self._hint(HINTS["dim2"])
        elif len(self._pts) == 2:
            self._hint(HINTS["dim3"])
        else:
            a, b, w = self._pts
            self._pts = []
            self.model.add("dim", [a, b, w])
            self._after_mutate()

    def _click_room(self, x, y, _e):
        v = self._opt_vars
        name = (v["rname"].get().strip() or "ROOM") if "rname" in v \
            else "ROOM"
        num = (v["rnum"].get().strip() or "101") if "rnum" in v else "101"
        self.model.add("room", [(x, y)], name=name.upper(), number=num)
        # auto-increment a trailing integer: 101 -> 102, 1.04 -> 1.05
        m = re.search(r"(\d+)$", num)
        if m and "rnum" in v:
            nxt = str(int(m.group(1)) + 1).zfill(len(m.group(1)))
            v["rnum"].set(num[:m.start(1)] + nxt)
        self._after_mutate()

    def _click_text(self, x, y, _e):
        v = self._opt_vars
        s = v["ttext"].get().strip() if "ttext" in v else ""
        if not s:
            self._hint("Type the note text in the options bar first")
            return
        self.model.add("text", [(x, y)], text=s,
                       size=v["tsize"].get() if "tsize" in v else "body")
        self._after_mutate()

    def _click_callout(self, x, y, _e):
        v = self._opt_vars
        self.model.add("callout", [(x, y)],
                       detail=(v["cdet"].get().strip() or "1")
                       if "cdet" in v else "1",
                       sheet=(v["csheet"].get().strip() or "A-501")
                       if "csheet" in v else "A-501")
        self._after_mutate()

    def _click_select(self, x, y, e):   # pragma: no cover - press handles it
        pass

    # keyboard ---------------------------------------------------------------
    def _on_space(self, e):
        if self.tool == "fixture" and "rot" in self._opt_vars:
            try:
                r = float(self._opt_vars["rot"].get())
            except ValueError:
                r = 0.0
            self._opt_vars["rot"].set(str((r + 90.0) % 360.0))
            self._draw_overlay()
        elif self.tool == "door":
            self._door_hand = "r" if getattr(self, "_door_hand", "l") == "l" \
                else "l"
        elif self.tool == "select" and self._last_draw:
            self._select_tool(self._last_draw)    # the drafting reflex:
        return "break"                            # Space repeats the tool

    def _on_tab(self, e):
        if self.tool == "door":
            self._door_swing = "out" \
                if getattr(self, "_door_swing", "in") == "in" else "in"
        return "break"      # Tab must never walk focus off the board

    def _escape(self):
        if self._pts:
            # never throw away committed work: a polyline with 2+ vertexes
            # lands as drawn, only the elastic segment dies
            if self.tool in ("line", "pipe") and len(self._pts) >= 2:
                self._finish_poly()
            else:
                self._pts = []
            self._hint(HINTS.get(self.tool, ""))
        elif self.tool != "select":
            self._select_tool("select")
        else:
            self.sel.clear()
            self.redraw()
            self._traits_refresh()
        self._draw_overlay()

    def _nudge(self, dx, dy):
        if not self.sel:
            return
        step = 1.0 / 12.0
        self.model.move(list(self.sel), dx * step, dy * step)
        self._after_mutate()

    def delete_selection(self):
        if not self.sel:
            return
        n = self.model.remove(list(self.sel))
        self.sel.clear()
        if n:
            self._after_mutate()
            self.status.set(f"Deleted {n} element(s)", "ok")

    def _undo(self):
        if self.model.undo():
            self.sel.clear()
            self._after_mutate(record=False)

    def _redo(self):
        if self.model.redo():
            self.sel.clear()
            self._after_mutate(record=False)

    # ------------------------------------------------------------- overlay
    def _draw_overlay(self):
        cv, c = self.cv, self.theme.colors
        cv.delete("ov")
        ac = c["accent"]
        if self._drag and self._drag[0] == "box" and len(self._drag) > 2:
            (sx, sy), (ex, ey) = self._drag[1], self._drag[2]
            crossing = ex < sx
            cv.create_rectangle(sx, sy, ex, ey, tags="ov",
                                outline=c["ok"] if crossing else ac,
                                dash=(4, 3) if crossing else None)
        if self._hover is None:
            return
        hx, hy = self._hover
        # snap glyph: square end / triangle mid / X cross / circle near
        if self._snap_hit is not None:
            sx, sy = self.to_screen(self._snap_hit.x, self._snap_hit.y)
            k = self._snap_hit.kind
            g = 5
            if k == "end":
                cv.create_rectangle(sx - g, sy - g, sx + g, sy + g,
                                    outline=ac, width=1.6, tags="ov")
            elif k == "mid":
                cv.create_polygon(sx, sy - g, sx - g, sy + g, sx + g, sy + g,
                                  outline=ac, fill="", width=1.6, tags="ov")
            elif k == "x":
                cv.create_line(sx - g, sy - g, sx + g, sy + g, fill=ac,
                               width=1.6, tags="ov")
                cv.create_line(sx - g, sy + g, sx + g, sy - g, fill=ac,
                               width=1.6, tags="ov")
            elif k == "perp":
                cv.create_line(sx - g, sy + g, sx + g, sy + g, fill=ac,
                               width=1.6, tags="ov")
                cv.create_line(sx, sy + g, sx, sy - g, fill=ac, width=1.6,
                               tags="ov")
            elif k in ("grid", "ortho"):
                cv.create_polygon(sx, sy - g, sx + g, sy, sx, sy + g,
                                  sx - g, sy, outline=ac, fill="",
                                  width=1.4, tags="ov")
            else:
                cv.create_oval(sx - g, sy - g, sx + g, sy + g, outline=ac,
                               tags="ov")
            cv.create_text(sx + 10, sy - 10, text=_SNAP_LABEL.get(k, k),
                           anchor="w", fill=ac, font=("Segoe UI", 8),
                           tags="ov")
        # rubber band + live temp dimension
        if self._pts and self.tool in ("wall", "line", "grid", "dim",
                                       "pipe"):
            a = self._pts[-1]
            sa, sb = self.to_screen(*a), self.to_screen(hx, hy)
            cv.create_line(sa[0], sa[1], sb[0], sb[1], fill=ac,
                           dash=(5, 3), tags="ov")
            d = math.hypot(hx - a[0], hy - a[1])
            ang = math.degrees(math.atan2(hy - a[1], hx - a[0])) % 180
            cv.create_text((sa[0] + sb[0]) / 2 + 12,
                           (sa[1] + sb[1]) / 2 - 12, anchor="w", tags="ov",
                           text=f"{draft.fmt_ftin(d)}  ∠{ang:.0f}°",
                           fill=ac, font=("Segoe UI", 9, "bold"))
            if self.tool == "dim" and len(self._pts) == 2:
                s0 = self.to_screen(*self._pts[0])
                cv.create_line(s0[0], s0[1], sa[0], sa[1], fill=ac,
                               dash=(2, 3), tags="ov")
        # fixture ghost follows the cursor
        if self.tool == "fixture":
            try:
                rot = float(self._opt_vars["rot"].get()) \
                    if "rot" in self._opt_vars else 0.0
            except ValueError:
                rot = 0.0
            try:
                ops = draft.stencil_ops(self._last_stencil, hx, hy, rot,
                                        False)
            except Exception:   # noqa: BLE001 -- ghost is cosmetic
                ops = []
            plies = {p.name: p for p in self.model.plies}
            ghost = mix(ac, c["canvas_bg"], 0.35)
            for op in ops:
                self._draw_op(op, plies, self.model.scale_ratio, tags="ov",
                              override=ghost)

    # ------------------------------------------------------------ flourish
    def _flourish_seg(self, a, b):
        """The weave: a bright thread runs the new wall's length, then lets
        the ink stand on its own."""
        if fx.quality() == "off":
            return
        cv, c = self.cv, self.theme.colors
        sa, sb = self.to_screen(*a), self.to_screen(*b)

        def upd(t):
            if not cv.winfo_exists():
                return
            cv.delete("weave")
            x = sa[0] + (sb[0] - sa[0]) * t
            y = sa[1] + (sb[1] - sa[1]) * t
            cv.create_line(sa[0], sa[1], x, y, fill=c["accent"],
                           width=3.0, tags="weave")

        fx.animate(cv, "weave", 0.0, 1.0, 170, upd, easing="ease_out_quad",
                   on_done=lambda: cv.winfo_exists()
                   and cv.delete("weave"))

    def _flourish_ring(self, x, y):
        if fx.quality() == "off":
            return
        cv, c = self.cv, self.theme.colors
        sx, sy = self.to_screen(x, y)

        def upd(t):
            if not cv.winfo_exists():
                return
            cv.delete("ring")
            r = 6 + 22 * t
            cv.create_oval(sx - r, sy - r, sx + r, sy + r, tags="ring",
                           outline=mix(c["accent"], c["canvas_bg"], t))

        fx.animate(cv, "ring", 0.0, 1.0, 260, upd, easing="ease_out_quad",
                   on_done=lambda: cv.winfo_exists() and cv.delete("ring"))

    # -------------------------------------------------------------- traits
    def _build_traits(self, body):
        right = ttk.Frame(body, width=224)
        right.pack(side="left", fill="y", padx=(0, 8), pady=4)
        right.pack_propagate(False)
        ttk.Label(right, text="Traits", font=("Segoe UI", 10, "bold")
                  ).pack(anchor="w")
        self.traits = ttk.Frame(right)
        self.traits.pack(fill="both", expand=True, pady=(4, 0))
        self._traits_refresh()

    def _traits_refresh(self):
        for w in self.traits.winfo_children():
            w.destroy()
        f = self.traits

        def row(label):
            r = ttk.Frame(f)
            r.pack(fill="x", pady=1)
            ttk.Label(r, text=label, style="Muted.TLabel", width=9
                      ).pack(side="left")
            return r

        if not self.sel:
            self._view_traits(f, row)
            return
        ents = [self.model.entity(i) for i in self.sel]
        ents = [e for e in ents if e is not None]
        if not ents:
            self._view_traits(f, row)
            return
        kinds = {e.kind for e in ents}
        if len(ents) > 1:
            ttk.Label(f, text=f"{len(ents)} selected "
                      f"({', '.join(sorted(kinds))})").pack(anchor="w")
            self._ply_row(row, ents)
            ttk.Button(f, text="Delete selection",
                       command=self.delete_selection).pack(anchor="w",
                                                           pady=6)
            return
        ent = ents[0]
        ttk.Label(f, text=f"{ent.kind.capitalize()}  ·  {ent.id}",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self._ply_row(row, ents)

        def entry(label, key, width=12, parse=None):
            r = row(label)
            var = tk.StringVar(value=str(ent.props.get(key, "")))
            e = ttk.Entry(r, width=width, textvariable=var)
            e.pack(side="left")

            def commit(_e=None):
                val = var.get().strip()
                if parse:
                    val = parse(val)
                    if val is None:
                        return
                self.model.update(ent.id, **{key: val})
                self._after_mutate()
            e.bind("<Return>", commit)
            e.bind("<FocusOut>", commit)
            return var

        def combo(label, key, values, current=None):
            r = row(label)
            var = tk.StringVar(value=current or str(ent.props.get(key, "")))
            cb = ttk.Combobox(r, width=14, state="readonly", values=values,
                              textvariable=var)
            cb.pack(side="left")
            cb.bind("<<ComboboxSelected>>", lambda _e: (
                self.model.update(ent.id, **{key: var.get()}),
                self._after_mutate()))
            return var

        if ent.kind == "wall":
            keys = list(draft.WALL_TYPES)
            r = row("Type")
            cb = ttk.Combobox(r, width=18, state="readonly",
                              values=[draft.WALL_TYPES[k]["label"]
                                      for k in keys])
            cur = ent.props.get("wtype", "stud4")
            cb.current(keys.index(cur) if cur in keys else 0)

            def set_type(_e):
                k = keys[cb.current()]
                self.model.update(ent.id, wtype=k,
                                  thick_in=draft.WALL_TYPES[k]["thick_in"])
                self._after_mutate()
            cb.bind("<<ComboboxSelected>>", set_type)
            cb.pack(side="left")
            entry("Thick (in)", "thick_in", 7, parse=lambda s: (
                float(s) if s.replace(".", "", 1).isdigit() else None))
            lf = draft.fmt_ftin(math.hypot(
                ent.pts[1][0] - ent.pts[0][0], ent.pts[1][1] - ent.pts[0][1]))
            ttk.Label(f, text=f"Length {lf}", style="Muted.TLabel"
                      ).pack(anchor="w", pady=(3, 0))
        elif ent.kind == "door":
            entry("Width (in)", "width_in", 7)
            combo("Swing", "swing", ["in", "out"])
            combo("Hand", "hand", ["l", "r"])
        elif ent.kind == "window":
            entry("Width (in)", "width_in", 7)
        elif ent.kind == "fixture":
            keys = list(draft.STENCILS)
            r = row("Stencil")
            cb = ttk.Combobox(r, width=18, state="readonly",
                              values=[draft.STENCILS[k]["label"]
                                      for k in keys])
            cur = ent.props.get("stencil", "wc")
            cb.current(keys.index(cur) if cur in keys else 0)
            cb.bind("<<ComboboxSelected>>", lambda _e: (
                self.model.update(ent.id, stencil=keys[cb.current()]),
                self._after_mutate()))
            cb.pack(side="left")
            entry("Rotation", "rot", 7, parse=lambda s: (
                float(s) if s.lstrip("-").replace(".", "", 1).isdigit()
                else None))
        elif ent.kind == "pipe":
            from .. import pipewright as pw
            keys = list(pw.SYSTEMS)
            r = row("System")
            cbp = ttk.Combobox(r, width=14, state="readonly",
                               values=[pw.SYSTEMS[k]["label"] for k in keys])
            cur = ent.props.get("system", "san")
            cbp.current(keys.index(cur) if cur in keys else 0)
            cbp.bind("<<ComboboxSelected>>", lambda _e: (
                self.model.update(ent.id, system=keys[cbp.current()]),
                self._after_mutate()))
            cbp.pack(side="left")
            entry("Dia (in)", "dia_in", 6, parse=lambda s: (
                float(s) if s.replace(".", "", 1).isdigit() else None))
            entry("Slope in/ft", "slope_in_ft", 6)
            entry("IE start ft", "invert_ft", 8)
            length = sum(math.hypot(b[0] - a[0], b[1] - a[1])
                         for a, b in zip(ent.pts, ent.pts[1:]))
            ttk.Label(f, text=f"Run {draft.fmt_ftin(length)}",
                      style="Muted.TLabel").pack(anchor="w", pady=(3, 0))
        elif ent.kind == "grid":
            entry("Label", "label", 6)
            combo("Bubble", "bubble", ["a", "b", "both"])
        elif ent.kind == "room":
            entry("Name", "name")
            entry("Number", "number", 8)
        elif ent.kind == "text":
            entry("Text", "text", 20)
            combo("Size", "size", ["body", "sub", "title"])
        elif ent.kind == "callout":
            entry("Detail", "detail", 6)
            entry("Plate", "sheet", 10)
        ttk.Button(f, text="Delete", command=self.delete_selection
                   ).pack(anchor="w", pady=6)

    def _ply_row(self, row, ents):
        r = row("Ply")
        names = [p.name for p in self.model.plies]
        var = tk.StringVar(value=ents[0].ply if len(
            {e.ply for e in ents}) == 1 else "· varies ·")
        cb = ttk.Combobox(r, width=12, state="readonly", values=names,
                          textvariable=var)
        cb.pack(side="left")

        def set_ply(_e):
            for ent in ents:
                ent.ply = var.get()
            self._after_mutate()
        cb.bind("<<ComboboxSelected>>", set_ply)

    def _view_traits(self, f, row):
        ttk.Label(f, text="Draft", font=("Segoe UI", 10, "bold")
                  ).pack(anchor="w")
        r = row("Title")
        tv = tk.StringVar(value=self.model.title)
        e1 = ttk.Entry(r, width=16, textvariable=tv)
        e1.pack(side="left")
        e1.bind("<FocusOut>", lambda _e: setattr(self.model, "title",
                                                 tv.get().strip()))
        r = row("Number")
        nv = tk.StringVar(value=self.model.number)
        e2 = ttk.Entry(r, width=10, textvariable=nv)
        e2.pack(side="left")
        e2.bind("<FocusOut>", lambda _e: setattr(self.model, "number",
                                                 nv.get().strip()))
        r = row("Scale")
        labels = [s[0] for s in draft.SCALES]
        ratios = [s[1] for s in draft.SCALES]
        cb = ttk.Combobox(r, width=14, state="readonly", values=labels)
        cb.current(ratios.index(self.model.scale_ratio)
                   if self.model.scale_ratio in ratios else 2)
        cb.bind("<<ComboboxSelected>>", lambda _e: (
            setattr(self.model, "scale_ratio", ratios[cb.current()]),
            self.redraw()))
        cb.pack(side="left")
        r = row("Plate")
        self._sheet_var = getattr(self, "_sheet_var",
                                  tk.StringVar(value="ARCH D"))
        ttk.Combobox(r, width=10, state="readonly",
                     values=list(draft.SHEET_SIZES),
                     textvariable=self._sheet_var).pack(side="left")

        ttk.Separator(f, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(f, text="Tally", font=("Segoe UI", 10, "bold")
                  ).pack(anchor="w")
        ttk.Label(f, style="Muted.TLabel",
                  text="live takeoff — feeds Reckoner").pack(anchor="w")
        self._tally_labels = {}
        for key, cap in (("wall_lf", "Wall LF"), ("doors", "Doors"),
                         ("windows", "Windows"), ("fixt", "Fixtures"),
                         ("rooms", "Rooms")):
            r = ttk.Frame(f)
            r.pack(fill="x")
            ttk.Label(r, text=cap, style="Muted.TLabel", width=9
                      ).pack(side="left")
            lbl = ttk.Label(r, text="0", font=("Segoe UI", 12, "bold"))
            lbl.pack(side="left")
            self._tally_labels[key] = (lbl, fx.CountUp(lbl, "{:,.0f}"))
        self._tally_update()

    def _tally_update(self):
        if not hasattr(self, "_tally_labels") or not self._tally_labels:
            return
        try:
            s = self.model.stats()
        except Exception:   # noqa: BLE001 -- tally must never block drawing
            return
        vals = {"wall_lf": s.get("wall_lf", 0.0),
                "doors": s.get("doors", 0), "windows": s.get("windows", 0),
                "fixt": sum(s.get("fixtures", {}).values()),
                "rooms": s.get("rooms", 0)}
        for key, (lbl, counter) in list(self._tally_labels.items()):
            if not lbl.winfo_exists():
                self._tally_labels = {}
                return
            counter.to(float(vals.get(key, 0)), dur=420)

    # ---------------------------------------------------------- status strip
    def _build_strip(self):
        strip = ttk.Frame(self, padding=(8, 2, 8, 4))
        strip.pack(fill="x", side="bottom")
        self.hint_lbl = ttk.Label(strip, style="Muted.TLabel", text="")
        self.hint_lbl.pack(side="left")
        ttk.Button(strip, text="Zoom fit", style="Tool.TButton",
                   command=self._zoom_extents).pack(side="right", padx=1)
        ttk.Button(strip, text="Redo", style="Tool.TButton",
                   command=self._redo).pack(side="right", padx=1)
        ttk.Button(strip, text="Undo", style="Tool.TButton",
                   command=self._undo).pack(side="right", padx=1)
        self.pos_lbl = ttk.Label(strip, style="Muted.TLabel", text="")
        self.pos_lbl.pack(side="right", padx=8)
        ttk.Checkbutton(strip, text="Ortho", variable=self.ortho
                        ).pack(side="right")
        for key, cap in (("grid", "Grd"), ("perp", "Perp"), ("x", "Int"),
                         ("mid", "Mid"), ("end", "End")):
            ttk.Checkbutton(strip, text=cap, variable=self.snap_on[key]
                            ).pack(side="right")
        ttk.Label(strip, text="Plumbline:", style="Muted.TLabel"
                  ).pack(side="right", padx=(12, 2))

    def _hint(self, text):
        if self.hint_lbl.winfo_exists():
            self.hint_lbl.configure(text=text)

    # ------------------------------------------------------------ lifecycle
    def _select_tool(self, key):
        self.tool = key
        if key != "select":
            self._last_draw = key
        self._pts = []
        self._drag = None
        for k, b in self._tool_btns.items():
            b.configure(style="ToolOn.TButton" if k == key
                        else "Tool.TButton")
        self._rebuild_opts()
        self._hint(HINTS.get(key, ""))
        self._draw_overlay()

    def _after_mutate(self, record=True):
        self.redraw()
        self._tally_update()
        self._update_title()

    def _update_title(self):
        name = os.path.basename(self.path) if self.path else "new draft"
        dirty = " ●" if getattr(self.model, "dirty", False) else ""
        self.file_lbl.configure(text=f"  {name}{dirty}")

    def refresh_all(self):
        self._fill_binder()
        self.redraw()
        self._tally_update()
        self._update_title()

    def _on_theme(self, _c):
        if self.cv.winfo_exists():
            self.theme.style_canvas(self.cv)
            self.redraw()

    # ------------------------------------------------------------ file ops
    def _confirm_discard(self) -> bool:
        if not getattr(self.model, "dirty", False):
            return True
        return messagebox.askyesno(
            "The Loft", "The current draft has unsaved changes — discard "
                        "them?")

    def new_file(self):
        if not self._confirm_discard():
            return
        self.model = draft.DraftModel()
        self.path = None
        self.sel.clear()
        self.refresh_all()
        self._zoom_extents()
        self._traits_refresh()

    def open_file(self, path=None):
        if not self._confirm_discard():
            return
        p = path or filedialog.askopenfilename(
            filetypes=[("Loft draft", "*.loft.json"), ("All", "*.*")])
        if not p:
            return
        try:
            self.model = draft.DraftModel.load(p)
        except Exception as e:      # noqa: BLE001 -- bad file must not crash
            messagebox.showerror("The Loft", f"Could not open draft:\n{e}")
            return
        self.path = p
        self.sel.clear()
        self.refresh_all()
        self._zoom_extents()
        self._traits_refresh()
        if self.on_opened:
            self.on_opened(p)

    on_opened = None                    # app hook: recents

    def save(self):
        if not self.path:
            self.save_as()
            return
        try:
            self.model.save(self.path)
        except Exception as e:      # noqa: BLE001
            messagebox.showerror("The Loft", f"Save failed:\n{e}")
            return
        self._update_title()
        self.status.set("Draft saved", "ok")

    def save_as(self):
        p = filedialog.asksaveasfilename(
            defaultextension=".loft.json",
            initialfile=(self.model.number or "draft") + ".loft.json",
            filetypes=[("Loft draft", "*.loft.json")])
        if not p:
            return
        self.path = p
        self.save()
        if self.on_opened:
            self.on_opened(p)

    def _on_drop(self, paths):
        lofts = [p for p in paths if p.lower().endswith(".loft.json")]
        if lofts:
            self.open_file(lofts[0])

    # ------------------------------------------------------------- exports
    def export_plate(self):
        if not self.model.ents:
            toast(self.root, self.theme, "Nothing drafted yet", "info")
            return
        out = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            initialfile=(self.model.number or "plate") + ".pdf",
            filetypes=[("PDF", "*.pdf")])
        if not out:
            return
        sheet = getattr(self, "_sheet_var", None)
        sheet = sheet.get() if sheet else "ARCH D"
        model = self.model

        def done(res, err):
            if err:
                self.status.set(f"Plate failed: {err}", "err")
                return
            self._plates.append(out)
            self._fill_binder()
            toast(self.root, self.theme,
                  f"Plate written at {res.get('scale', '?')}")
            open_path(out)

        run_bg(self, lambda: draft.plate_pdf(model, out, sheet=sheet), done)

    def export_dxf(self):
        if not self.model.ents:
            toast(self.root, self.theme, "Nothing drafted yet", "info")
            return
        out = filedialog.asksaveasfilename(
            defaultextension=".dxf",
            initialfile=(self.model.number or "draft") + ".dxf",
            filetypes=[("DXF", "*.dxf")])
        if not out:
            return
        model = self.model

        def done(n, err):
            if err:
                self.status.set(f"DXF failed: {err}", "err")
                return
            self._plates.append(out)
            self._fill_binder()
            toast(self.root, self.theme, f"DXF written — {n} entities")

        run_bg(self, lambda: draft.to_dxf(model, out), done)

    def export_png(self):
        if not self.model.ents:
            toast(self.root, self.theme, "Nothing drafted yet", "info")
            return
        out = filedialog.asksaveasfilename(
            defaultextension=".png", initialfile="draft.png",
            filetypes=[("PNG", "*.png")])
        if not out:
            return
        model = self.model

        def done(_p, err):
            if err:
                self.status.set(f"PNG failed: {err}", "err")
                return
            self._plates.append(out)
            self._fill_binder()
            toast(self.root, self.theme, "PNG written")

        run_bg(self, lambda: draft.to_png(model, out), done)

    def export_tally(self):
        lines = draft.takeoff_lines(self.model)
        if any(e.kind == "pipe" for e in self.model.ents):
            from .. import pipewright as pw
            lines = lines + pw.takeoff(self.model)
        if not lines:
            toast(self.root, self.theme, "Nothing to tally yet", "info")
            return
        out = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile="tally.csv",
            filetypes=[("CSV", "*.csv")])
        if not out:
            return
        import csv
        tmp = out + ".part"
        with open(tmp, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["subject", "kind", "qty", "unit"])
            for ln in lines:
                w.writerow([ln.subject, ln.kind, f"{ln.qty:g}", ln.unit])
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, out)
        toast(self.root, self.theme,
              f"Tally CSV — {len(lines)} line(s), ready for Reckoner")

    def send_to_bim(self):
        if not any(e.kind == "wall" for e in self.model.ents):
            toast(self.root, self.theme, "Draft some walls first", "info")
            return
        ans = simpledialog.askstring(
            "To 3D", "Wall height and floor count (e.g.  10, 1):",
            initialvalue="10, 1", parent=self)
        if not ans:
            return
        try:
            parts = [v.strip() for v in ans.split(",")]
            height = float(parts[0])
            floors = int(parts[1]) if len(parts) > 1 else 1
        except (ValueError, IndexError):
            messagebox.showwarning("To 3D", "Format:  height, floors")
            return
        try:
            model3d = draft.to_bim(self.model, wall_height=height,
                                   floors=floors)
            if any(e.kind == "pipe" for e in self.model.ents):
                from .. import pipewright as pw
                pipes3d = pw.to_bim(self.model)
                model3d.segments.extend(pipes3d.segments)
                have = {s[0] for s in model3d.systems}
                model3d.systems.extend(s for s in pipes3d.systems
                                       if s[0] not in have)
        except Exception as e:      # noqa: BLE001
            messagebox.showerror("To 3D", str(e))
            return
        if self.on_bim:
            self.on_bim(model3d)
            toast(self.root, self.theme, "Draft extruded into the 3D model"
                  + (" — pipes ride at their inverts"
                     if any(e.kind == "pipe" for e in self.model.ents)
                     else ""))

    def grids_to_fieldstitch(self):
        pts = draft.grid_points(self.model)
        if not pts:
            toast(self.root, self.theme,
                  "Draw crossing grid lines first — their intersections "
                  "become layout points", "info")
            return
        fs = self.get_fieldstitch() if self.get_fieldstitch else None
        job = getattr(fs, "job", None) if fs else None
        if job is None or job.cal is None:
            toast(self.root, self.theme,
                  "Open a plan in Fieldstitch Layout and set its scale "
                  "first — grid points need the world frame", "info")
            return
        from ..fieldstitch import PointLayer
        if not job.layer("GRID"):
            job.add_layer(PointLayer(name="GRID", color="#3f6fe0"))
        page = fs.viewer.page_no if getattr(fs, "viewer", None) else 1
        n = 0
        for x, y, label in pts:
            px, py = job.from_world(n=y, e=x)
            job.add_point(page, px, py, desc=f"GRID {label}", layer="GRID")
            n += 1
        if hasattr(fs, "refresh_points"):
            fs.refresh_points()
        toast(self.root, self.theme,
              f"{n} grid intersection(s) → Fieldstitch layout points")

    # ---------------------------------------------------------- Pipewright
    def _sel_pipe(self):
        for eid in self.sel:
            ent = self.model.entity(eid)
            if ent is not None and ent.kind == "pipe":
                return ent
        return None

    def pipe_slope(self):
        """The command the whole feature was named for: 1/8 or 1/4 per
        foot, invert elevations propagated down the network."""
        from .. import pipewright as pw
        ent = self._sel_pipe()
        if ent is None:
            self._hint("Select a pipe run first (V), then Slope run…")
            return
        ans = simpledialog.askstring(
            "Slope run", "Slope in/ft, start invert ft  "
                         "(e.g.  1/8, 98.5):", initialvalue="1/8, 100.0",
            parent=self)
        if not ans:
            return
        try:
            parts = [v.strip() for v in ans.split(",")]
            num = parts[0]
            slope = (float(num.split("/")[0]) / float(num.split("/")[1])
                     if "/" in num else float(num))
            invert = float(parts[1]) if len(parts) > 1 else None
        except (ValueError, IndexError, ZeroDivisionError):
            messagebox.showwarning("Slope run", "Format:  1/8, 98.5")
            return
        try:
            res = pw.slope_run(self.model, ent.id, slope,
                               start_invert_ft=invert)
        except ValueError as e:
            self.status.set(str(e), "err")
            return
        self._after_mutate()
        msg = res["report"]
        if res.get("warnings"):
            msg += "  ⚠ " + "; ".join(res["warnings"])
            self.status.set(msg, "err")
        else:
            self.status.set(msg, "ok")
        toast(self.root, self.theme,
              f"Sloped {res['changed']} run(s) — total fall "
              f"{res['total_fall']}")

    def pipe_cap(self):
        from .. import pipewright as pw
        res = pw.cap_open_ends(self.model)
        if res["changed"]:
            self._after_mutate()
        toast(self.root, self.theme, res["report"])

    def pipe_check(self):
        from .. import pipewright as pw
        warns = pw.check(self.model)
        if not warns:
            toast(self.root, self.theme, "Pipewright: no findings — clean")
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("Pipewright check — findings, never silent fixes")
        dlg.transient(self.root)
        frm = ttk.Frame(dlg, padding=10)
        frm.pack(fill="both", expand=True)
        from .widgets import make_tree
        frame, tree = make_tree(
            frm, self.theme,
            [("lvl", "LVL"), ("code", "CODE"), ("msg", "FINDING")],
            (44, 110, 420), height=min(12, max(4, len(warns))))
        frame.pack(fill="both", expand=True)
        for i, w in enumerate(warns):
            tree.insert("", "end", iid=str(i),
                        values=(w["level"], w["code"], w["msg"]))

        def jump(_e):
            sel = tree.selection()
            if sel:
                w = warns[int(sel[0])]
                if w.get("ent_id"):
                    self.sel = {w["ent_id"]}
                    self.redraw()
                    self._traits_refresh()
        tree.bind("<Double-Button-1>", jump)
        ttk.Label(frm, style="Muted.TLabel",
                  text="double-click a finding to select its run · "
                       "minimums say 'verify against project code'"
                  ).pack(anchor="w", pady=(4, 0))
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    # ------------------------------------------------------------- palette
    def commands(self):
        return [
            ("New draft (Loft)", "Loft", self.new_file),
            ("Open draft…", "Loft", self.open_file),
            ("Save draft", "Loft", self.save),
            ("Export plate PDF", "Loft", self.export_plate),
            ("Export DXF", "Loft", self.export_dxf),
            ("Draft → 3D model", "Loft", self.send_to_bim),
            ("Grid points → Fieldstitch", "Loft",
             self.grids_to_fieldstitch),
            ("Tally CSV (takeoff)", "Loft", self.export_tally),
            ("Pipewright: cap open ends", "Loft", self.pipe_cap),
            ("Pipewright: check the piping", "Loft", self.pipe_check),
            ("Pipewright: slope selected run", "Loft", self.pipe_slope),
        ]
