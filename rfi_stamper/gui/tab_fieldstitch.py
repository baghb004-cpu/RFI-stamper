"""Fieldstitch studio: place numbered layout points on a plan (or a blank
grid sheet), organize them in Strata layers, give them real-world N/E/Z
coordinates, and export the kit the crew's tablet expects — no CAD needed.

Wow layer: glowing point markers with a placement pulse, a live crosshair
coordinate HUD, and every point doubles as a 3D pin in the BIM viewer.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk

import fitz

from .. import fieldstitch as fs
from ..markups import measure
from . import dnd, fx
from .theme import FAMILY, mix, section_color
from .widgets import Tooltip, make_tree, open_path, run_bg, toast

# same architectural/metric presets the markup tab uses
SCALES = (
    [(f'{lbl}" = 1\'-0"', (1.0 / x) / 72.0, "ft") for lbl, x in
     (("1/16", 1 / 16), ("3/32", 3 / 32), ("1/8", 1 / 8), ("3/16", 3 / 16),
      ("1/4", 1 / 4), ("3/8", 3 / 8), ("1/2", 1 / 2), ("3/4", 3 / 4),
      ("1", 1.0), ("1-1/2", 1.5), ("3", 3.0))]
    + [(f"1:{n}", n * 0.0254 / 72.0, "m") for n in (50, 100, 200, 250, 500)]
)

KIT_LABELS = [
    ("bowline", "Bowline Kit — PNEZD CSV + DXF (robotic-total-station tablets)"),
    ("clovehitch", "Clovehitch Kit — XLSX + DXF (grid layout tablets)"),
    ("fullspool", "Full Spool — CSV + XLSX + DXF + job JSON"),
]


class FieldstitchTab(ttk.Frame):
    def __init__(self, parent, theme, status, root, on_pins=None):
        super().__init__(parent)
        self.theme = theme
        self.status = status
        self.root = root
        self.on_pins = on_pins          # app hook: pins ready for the 3D view
        self.job: fs.LayoutJob | None = None
        self.tool = "place"
        self.selection: str | None = None
        self._drag_id = None
        self.accent = section_color("plans")

        # ------------------------------------------------------- toolbar
        tb = ttk.Frame(self, padding=(6, 4))
        tb.pack(fill="x")
        ttk.Button(tb, text="Open PDF…", command=self.open_pdf).pack(side="left")
        ttk.Button(tb, text="Blank grid sheet", command=self.blank_sheet
                   ).pack(side="left", padx=2)
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=6)
        self.tool_btns = {}
        for name, label, tip in (("place", "⨁ Place", "Click to drop the next "
                                  "numbered point"),
                                 ("select", "➤ Select", "Click a point to "
                                  "select; drag to move; Del deletes"),
                                 ("basepoint", "◎ Basepoint", "Click the page "
                                  "point that matches a known N/E")):
            b = ttk.Button(tb, text=label, style="Tool.TButton",
                           command=lambda n=name: self.set_tool(n))
            b.pack(side="left", padx=1)
            Tooltip(b, tip, theme)
            self.tool_btns[name] = b
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Label(tb, text="prefix").pack(side="left")
        self.prefix_var = tk.StringVar(value="CP-")
        ttk.Entry(tb, textvariable=self.prefix_var, width=6).pack(side="left")
        ttk.Label(tb, text="next #").pack(side="left", padx=(6, 0))
        self.num_var = tk.StringVar(value="1")
        ttk.Spinbox(tb, from_=1, to=99999, textvariable=self.num_var,
                    width=6).pack(side="left")
        ttk.Label(tb, text="suffix").pack(side="left", padx=(6, 0))
        self.suffix_var = tk.StringVar(value="")
        ttk.Entry(tb, textvariable=self.suffix_var, width=5).pack(side="left")
        ttk.Label(tb, text="elev").pack(side="left", padx=(6, 0))
        self.elev_var = tk.StringVar(value="0")
        ttk.Entry(tb, textvariable=self.elev_var, width=7).pack(side="left")
        self.export_btn = ttk.Menubutton(tb, text="⇥ Export kit",
                                         style="Accent.TButton")
        self.export_btn.pack(side="right", padx=2)
        menu = tk.Menu(self.export_btn, tearoff=0)
        for key, label in KIT_LABELS:
            menu.add_command(label=label,
                             command=lambda k=key: self.export_kit(k))
        menu.add_separator()
        menu.add_command(label="Import points CSV…", command=self.import_csv)
        self.export_btn.configure(menu=menu)
        self.scale_btn = ttk.Menubutton(tb, text="scale ▾")
        self.scale_btn.pack(side="right", padx=6)
        smenu = tk.Menu(self.scale_btn, tearoff=0)
        for label, rpp, unit in SCALES:
            smenu.add_command(label=label,
                              command=lambda l=label, r=rpp, u=unit:
                              self.set_scale(l, r, u))
        self.scale_btn.configure(menu=smenu)

        # --------------------------------------------------------- panes
        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body, padding=4)
        body.add(left, weight=0)
        ttk.Label(left, text="Strata", style="Title.TLabel").pack(anchor="w")
        ttk.Label(left, style="Muted.TLabel",
                  text="layers · visibility · color").pack(anchor="w")
        frame, self.ltree = make_tree(
            left, theme, [("on", "👁"), ("name", "LAYER"), ("cat", "CATEGORY")],
            (34, 110, 100), height=7)
        frame.pack(fill="both", expand=True, pady=4)
        self.ltree.bind("<Button-1>", self._layer_click)
        self.ltree.bind("<Double-1>", self._layer_color)
        row = ttk.Frame(left)
        row.pack(fill="x")
        ttk.Button(row, text="＋", width=3, command=self.add_layer
                   ).pack(side="left")
        ttk.Button(row, text="Color", command=self._layer_color
                   ).pack(side="left", padx=2)
        Tooltip(self.ltree, "Click the 👁 cell to toggle visibility.\n"
                            "Double-click a layer to recolor it.", theme)
        ttk.Label(left, text="Basepoint", style="Title.TLabel"
                  ).pack(anchor="w", pady=(10, 0))
        self.base_lbl = ttk.Label(left, style="Muted.TLabel",
                                  text="N 1000.000\nE 1000.000\nrot 0.0°")
        self.base_lbl.pack(anchor="w")
        ttk.Button(left, text="Rotation…", command=self.set_rotation
                   ).pack(fill="x", pady=2)

        center = ttk.Frame(body)
        body.add(center, weight=4)
        from .viewer import PDFViewer
        self.viewer = PDFViewer(center, theme, on_render=self.redraw_points)
        self.viewer.pack(fill="both", expand=True)
        cv = self.viewer.canvas
        cv.bind("<ButtonPress-1>", self.on_press)
        cv.bind("<B1-Motion>", self.on_drag)
        cv.bind("<ButtonRelease-1>", self.on_release)
        cv.bind("<Motion>", self.on_hover, add="+")
        cv.bind("<Leave>", lambda e: cv.delete("hud"), add="+")
        dnd.enable_drop(cv, lambda p: p and self.open_pdf(p[0]),
                        exts=(".pdf",))

        right = ttk.Frame(body, padding=4)
        body.add(right, weight=2)
        bar2 = ttk.Frame(right)
        bar2.pack(fill="x")
        ttk.Label(bar2, text="Points", style="Title.TLabel").pack(side="left")
        self.count_lbl = ttk.Label(bar2, text="0", style="Muted.TLabel")
        self.count_lbl.pack(side="left", padx=6)
        self.filter_var = tk.StringVar()
        fe = ttk.Entry(bar2, textvariable=self.filter_var, width=14)
        fe.pack(side="right")
        Tooltip(fe, "Filter points (label, layer, description)", theme)
        self.filter_var.trace_add("write", lambda *_: self.fill_table())
        frame, self.ptree = make_tree(
            right, theme,
            [("label", "POINT"), ("pg", "PG"), ("n", "N"), ("e", "E"),
             ("z", "Z"), ("desc", "DESC"), ("layer", "LAYER")],
            (80, 34, 76, 76, 56, 120, 76), height=14)
        frame.pack(fill="both", expand=True, pady=4)
        self.ptree.bind("<<TreeviewSelect>>", self._table_select)
        row2 = ttk.Frame(right)
        row2.pack(fill="x")
        ttk.Button(row2, text="Delete", command=self.delete_sel
                   ).pack(side="left")
        ttk.Button(row2, text="Renumber", command=self.renumber
                   ).pack(side="left", padx=4)
        ttk.Button(row2, text="Pins → 3D", command=self.push_pins
                   ).pack(side="right")

        self.set_tool("place")

    # ------------------------------------------------------------- setup
    def open_pdf(self, path=None):
        path = path or filedialog.askopenfilename(
            filetypes=[("PDF", "*.pdf")])
        if not path:
            return
        self.viewer.open(path)
        self.job = fs.LayoutJob(path)      # sidecar autoload
        self._sync_from_job()
        self.fill_layers()
        self.fill_table()
        self.status.set(f"Fieldstitch: {os.path.basename(path)} — "
                        f"{len(self.job.points)} point(s)", "ok")

    def blank_sheet(self):
        """No CAD, no PDF? Draw on a fresh gridded sheet."""
        out = os.path.join(os.path.expanduser("~"), ".planloom",
                           "blank_grid.pdf")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        doc = fitz.open()
        page = doc.new_page(width=1224, height=792)
        for x in range(24, 1224, 24):
            page.draw_line((x, 0), (x, 792), color=(0.85, 0.87, 0.9),
                           width=0.4 if x % 120 else 0.9)
        for y in range(24, 792, 24):
            page.draw_line((0, y), (1224, y), color=(0.85, 0.87, 0.9),
                           width=0.4 if y % 120 else 0.9)
        page.insert_text((30, 30), "FIELDSTITCH GRID SHEET", fontsize=12,
                         color=(0.55, 0.58, 0.62))
        doc.save(out)
        doc.close()
        self.open_pdf(out)

    def _sync_from_job(self):
        j = self.job
        self.prefix_var.set(j.prefix or self.prefix_var.get())
        self.suffix_var.set(j.suffix)
        self.num_var.set(str(j.next_num))
        self._show_base()

    def _push_to_job(self):
        j = self.job
        if not j:
            return
        j.prefix = self.prefix_var.get()
        j.suffix = self.suffix_var.get()
        try:
            j.next_num = max(1, int(self.num_var.get()))
        except ValueError:
            pass

    def _show_base(self):
        j = self.job
        if not j:
            return
        n, e = j.base_world
        self.base_lbl.configure(
            text=f"N {n:,.3f}\nE {e:,.3f}\nrot {j.rotation_deg:.1f}°")

    # ------------------------------------------------------------- tools
    def set_tool(self, name):
        self.tool = name
        for n, b in self.tool_btns.items():
            b.configure(style="ToolOn.TButton" if n == name
                        else "Tool.TButton")
        self.viewer.canvas.configure(
            cursor={"place": "crosshair", "basepoint": "target"}.get(
                name, "arrow"))

    def set_scale(self, label, rpp, unit):
        if not self.job:
            return
        self.job.scale = measure.ScaleCal(real_per_pt=rpp, unit=unit).to_dict()
        self.job.units = unit if unit in ("ft", "m") else "ft"
        self.job.save()
        self.scale_btn.configure(text=f"scale: {label} ▾")
        self.fill_table()
        self.status.set(f"Fieldstitch scale {label}", "ok")

    def set_rotation(self):
        if not self.job:
            return
        v = simpledialog.askstring("Plan rotation",
                                   "Rotation of plan north (degrees CCW):",
                                   initialvalue=str(self.job.rotation_deg),
                                   parent=self)
        if v is None:
            return
        try:
            self.job.rotation_deg = float(v)
        except ValueError:
            return
        self.job.save()
        self._show_base()
        self.fill_table()

    # ------------------------------------------------------- mouse events
    def on_press(self, event):
        if not self.job or not self.viewer.doc:
            return
        x, y = self.viewer.event_page_xy(event)
        if self.tool == "place":
            self._push_to_job()
            try:
                elev = float(self.elev_var.get() or 0)
            except ValueError:
                elev = 0.0
            p = self.job.add_point(self.viewer.page_no, x, y, elev=elev)
            self.num_var.set(str(self.job.next_num))
            self.redraw_points()
            self.fill_table()
            self._pulse(p)
            self.status.set(f"placed {self.job.composed(p)}", "ok")
        elif self.tool == "basepoint":
            ans = simpledialog.askstring(
                "Basepoint", "World coordinates at this point —  N,E "
                             "(e.g. 5000, 2000):", parent=self)
            if not ans:
                return
            try:
                n, e = (float(v.strip()) for v in ans.split(","))
            except ValueError:
                messagebox.showwarning("Basepoint", "Format:  N, E")
                return
            self.job.base_page_xy = (x, y)
            self.job.base_world = (n, e)
            self.job.save()
            self._show_base()
            self.fill_table()
            self.redraw_points()
            self.set_tool("place")
        else:                                   # select
            self.selection = self._hit(x, y)
            self._drag_id = self.selection
            self.redraw_points()
            if self.selection:
                self.ptree.selection_set(self.selection)

    def on_drag(self, event):
        if self.tool == "select" and self._drag_id and self.job:
            p = self.job.get(self._drag_id)
            if p:
                p.x, p.y = self.viewer.event_page_xy(event)
                self.redraw_points()

    def on_release(self, _event):
        if self._drag_id and self.job:
            self.job.save()
            self.fill_table()
        self._drag_id = None

    def _hit(self, x, y):
        best, best_d = None, 81.0               # 9pt hit radius
        for p in self.job.points_on(self.viewer.page_no):
            d = (p.x - x) ** 2 + (p.y - y) ** 2
            if d < best_d:
                best, best_d = p.id, d
        return best

    def on_hover(self, event):
        """Crosshair + live coordinate HUD."""
        cv = self.viewer.canvas
        cv.delete("hud")
        if not self.job or not self.viewer.doc:
            return
        x, y = self.viewer.event_page_xy(event)
        cx, cy = cv.canvasx(event.x), cv.canvasy(event.y)
        col = mix(self.accent, self.theme.colors["fg"], 0.25)
        cv.create_line(cx - 14, cy, cx + 14, cy, fill=col, tags="hud")
        cv.create_line(cx, cy - 14, cx, cy + 14, fill=col, tags="hud")
        try:
            probe = fs.LayoutPoint.new(page=self.viewer.page_no, x=x, y=y)
            n, e, _z = self.job.to_world(probe)
            txt = f"N {n:,.2f}   E {e:,.2f}"
        except Exception:   # noqa: BLE001 -- no scale yet
            txt = f"x {x:.1f}pt   y {y:.1f}pt   (set scale for N/E)"
        t = cv.create_text(cx + 18, cy - 16, text=txt, anchor="w",
                           fill=self.theme.colors["fg"],
                           font=(FAMILY, 9, "bold"), tags="hud")
        box = cv.bbox(t)
        r = cv.create_rectangle(box[0] - 4, box[1] - 2, box[2] + 4,
                                box[3] + 2, fill=self.theme.colors["panel"],
                                outline=col, tags="hud")
        cv.tag_raise(t, r)

    # ---------------------------------------------------------- rendering
    def redraw_points(self, _page=None):
        cv = self.viewer.canvas
        cv.delete("pt")
        if not self.job or not self.viewer.doc:
            return
        s = self.viewer.scale
        vis = {ly.name for ly in self.job.layers if ly.visible}
        colors = {ly.name: ly.color for ly in self.job.layers}
        for p in self.job.points_on(self.viewer.page_no):
            if p.layer not in vis:
                continue
            col = colors.get(p.layer, "#d84c3f")
            cx, cy = p.x * s, p.y * s
            r = max(4.0, 5.5 * s ** 0.5)
            sel = p.id == self.selection
            cv.create_oval(cx - r - 3, cy - r - 3, cx + r + 3, cy + r + 3,
                           outline=col, width=2 if sel else 1,
                           tags=("pt",))                     # halo ring
            cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=col,
                           outline="white", width=1.2, tags=("pt",))
            cv.create_line(cx - r - 5, cy, cx + r + 5, cy, fill=col,
                           tags=("pt",))
            cv.create_line(cx, cy - r - 5, cx, cy + r + 5, fill=col,
                           tags=("pt",))
            t = cv.create_text(cx + r + 6, cy - r - 4, anchor="w",
                               text=self.job.composed(p), fill=col,
                               font=(FAMILY, 9, "bold"), tags=("pt",))
            box = cv.bbox(t)
            bgr = cv.create_rectangle(box[0] - 2, box[1] - 1, box[2] + 2,
                                      box[3] + 1, fill="white", outline="",
                                      tags=("pt",))
            cv.tag_raise(t, bgr)

    def _pulse(self, p):
        """Placement pulse: an expanding, fading ring — pure wow."""
        if fx.quality() == "off":
            return
        cv = self.viewer.canvas
        s = self.viewer.scale
        cx, cy = p.x * s, p.y * s
        col = next((ly.color for ly in self.job.layers
                    if ly.name == p.layer), "#d84c3f")

        def ring(t):
            cv.delete("pulse")
            if t >= 1.0 or not cv.winfo_exists():
                return
            r = 8 + 26 * t
            cv.create_oval(cx - r, cy - r, cx + r, cy + r,
                           outline=mix(col, self.theme.colors["canvas_bg"],
                                       t), width=2.5, tags="pulse")

        fx.animate(cv, "pulse", 0.0, 1.0, 420, ring, easing="ease_out_quad",
                   on_done=lambda: cv.winfo_exists() and cv.delete("pulse"))

    # ------------------------------------------------------------- strata
    def fill_layers(self):
        self.ltree.delete(*self.ltree.get_children())
        if not self.job:
            return
        for ly in self.job.layers:
            self.ltree.insert("", "end", iid=ly.name, values=(
                "●" if ly.visible else "○", ly.name, ly.category),
                tags=(ly.name,))
            self.ltree.tag_configure(ly.name, foreground=ly.color)

    def add_layer(self):
        if not self.job:
            return
        name = simpledialog.askstring("Strata", "New layer name:",
                                      parent=self)
        if not name:
            return
        self.job.add_layer(fs.PointLayer(name=name))
        self.job.save()
        self.fill_layers()

    def _layer_click(self, event):
        iid = self.ltree.identify_row(event.y)
        if not iid or not self.job:
            return
        if self.ltree.identify_column(event.x) == "#1":     # 👁 toggle
            ly = self.job.layer(iid)
            if ly:
                ly.visible = not ly.visible
                self.job.save()
                self.fill_layers()
                self.redraw_points()

    def _layer_color(self, _e=None):
        sel = self.ltree.selection()
        if not sel or not self.job:
            return
        ly = self.job.layer(sel[0])
        if not ly:
            return
        _, hexcol = colorchooser.askcolor(ly.color, parent=self)
        if hexcol:
            ly.color = hexcol
            self.job.save()
            self.fill_layers()
            self.redraw_points()

    # -------------------------------------------------------------- table
    def fill_table(self):
        self.ptree.delete(*self.ptree.get_children())
        if not self.job:
            return
        q = self.filter_var.get().strip().lower()
        n_shown = 0
        for p in self.job.points:
            label = self.job.composed(p)
            hay = f"{label} {p.layer} {p.desc} {p.category}".lower()
            if q and q not in hay:
                continue
            try:
                n, e, z = self.job.to_world(p)
                vals = (label, p.page, f"{n:,.3f}", f"{e:,.3f}",
                        f"{z:,.2f}", p.desc, p.layer)
            except Exception:   # noqa: BLE001 -- no scale yet
                vals = (label, p.page, f"({p.x:.0f}pt)", f"({p.y:.0f}pt)",
                        f"{p.elev:,.2f}", p.desc, p.layer)
            self.ptree.insert("", "end", iid=p.id, values=vals)
            n_shown += 1
        self.count_lbl.configure(text=f"{n_shown} shown / "
                                      f"{len(self.job.points)} total")

    def _table_select(self, _e):
        sel = self.ptree.selection()
        if sel and self.job:
            self.selection = sel[0]
            p = self.job.get(sel[0])
            if p and p.page != self.viewer.page_no:
                self.viewer.goto(p.page)
            self.redraw_points()

    def delete_sel(self):
        if not self.job:
            return
        for iid in self.ptree.selection():
            self.job.remove(iid)
        self.selection = None
        self.fill_table()
        self.redraw_points()

    def renumber(self):
        if self.job and messagebox.askyesno(
                "Renumber", "Renumber every point from 1 in placement "
                            "order?"):
            self.job.renumber(1)
            self.num_var.set(str(self.job.next_num))
            self.fill_table()
            self.redraw_points()

    # ------------------------------------------------------------ exports
    def export_kit(self, kit):
        if not self.job or not self.job.points:
            messagebox.showinfo("Fieldstitch", "Place some points first.")
            return
        try:
            self.job.to_world(self.job.points[0])
        except Exception:   # noqa: BLE001
            messagebox.showwarning(
                "Fieldstitch", "Set the scale (and basepoint) first so the "
                               "points get real N/E coordinates.")
            return
        out_dir = filedialog.askdirectory(title="Folder for the export kit")
        if not out_dir:
            return
        job = self.job

        def work():
            return fs.export_kit(job, out_dir, kit)

        def done(res, err):
            if err:
                self.status.set(f"Export failed: {err}", "err")
                return
            names = ", ".join(os.path.basename(f) for f in res["files"])
            toast(self.root, self.theme,
                  f"{kit.capitalize()} kit: {res['points']} point(s) → "
                  f"{names}")
            open_path(out_dir)

        run_bg(self, work, done)

    def import_csv(self):
        if not self.job:
            messagebox.showinfo("Fieldstitch", "Open a PDF first.")
            return
        p = filedialog.askopenfilename(filetypes=[("CSV", "*.csv *.txt")])
        if not p:
            return

        def done(n, err):
            if err:
                self.status.set(f"Import failed: {err}", "err")
                return
            toast(self.root, self.theme, f"Imported {n} point(s)")
            self.fill_table()
            self.redraw_points()

        run_bg(self, lambda: fs.import_csv(self.job, p), done)

    # ---------------------------------------------------------------- 3D
    def push_pins(self):
        """Every visible point becomes a 3D pin (world E, N, Z)."""
        if not self.job or not self.job.points:
            messagebox.showinfo("Fieldstitch", "Place some points first.")
            return
        try:
            colors = {ly.name: ly.color for ly in self.job.layers}
            pins = []
            for p in self.job.points:
                n, e, z = self.job.to_world(p)
                pins.append((e, n, z, self.job.composed(p),
                             colors.get(p.layer, "#d84c3f")))
        except Exception:   # noqa: BLE001
            messagebox.showwarning("Fieldstitch",
                                   "Set the scale first — 3D pins need real "
                                   "coordinates.")
            return
        if self.on_pins:
            self.on_pins(pins)
            toast(self.root, self.theme,
                  f"{len(pins)} pin(s) sent to the BIM viewer")

    def commands(self):
        return [
            ("Fieldstitch: open PDF", "Fieldstitch", self.open_pdf),
            ("Fieldstitch: blank grid sheet", "Fieldstitch", self.blank_sheet),
            ("Fieldstitch: export Bowline kit", "Fieldstitch",
             lambda: self.export_kit("bowline")),
            ("Fieldstitch: export Clovehitch kit", "Fieldstitch",
             lambda: self.export_kit("clovehitch")),
            ("Fieldstitch: pins to 3D", "Fieldstitch", self.push_pins),
        ]
