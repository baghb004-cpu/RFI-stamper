"""Plans & BIM section: Plan Viewing (the markup editor), the Loft (draft a
plan from a blank sheet), As-Built Drawings (record-set compare + as-built
markup flow), and the 3D BIM viewer with 2D sheets placed in 3D space."""
from __future__ import annotations

from tkinter import ttk

from . import fx
from .bim3d import Bim3DViewer
from .tab_compare import CompareTab
from .tab_draft import LoftTab
from .tab_fieldstitch import FieldstitchTab
from .tab_markup import MarkupTab
from .theme import mix, section_color


class AsBuiltPanel(ttk.Frame):
    """As-builts = the record set + what actually got built.  Compare the
    contract set against the field set, then red-line the deltas with the
    AS-BUILT tool presets in Plan Viewing."""

    def __init__(self, parent, theme, status, open_in_viewer):
        super().__init__(parent)
        row = ttk.Frame(self, padding=(10, 6))
        row.pack(fill="x")
        ttk.Label(row, text="▍As-Built Drawings",
                  font=("Segoe UI", 14, "bold"),
                  foreground=section_color("plans")).pack(side="left")
        ttk.Label(row, style="Muted.TLabel",
                  text="  1) overlay contract vs field set below · 2) red-line "
                       "deltas with the AS-BUILT presets in Plan Viewing"
                  ).pack(side="left")
        ttk.Button(row, text="Open in Plan Viewing →",
                   command=open_in_viewer).pack(side="right")
        self.compare = CompareTab(self, theme, status)
        self.compare.pack(fill="both", expand=True)


class PlansSection(ttk.Frame):
    def __init__(self, parent, theme, status, root, author=""):
        super().__init__(parent)
        col = section_color("plans")
        self.header = fx.GradientHeader(
            self, theme, height=58,
            stops=[(0.0, col), (1.0, mix(col, theme.colors["bg"], 0.75))],
            title="Plans & BIM",
            subtitle="View and mark up sheets · record the as-builts · walk "
                     "the model with your drawings placed in 3D")
        self.header.pack(fill="x")
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=4, pady=4)
        self.nb = nb

        self.markup = MarkupTab(nb, theme, status, author=author)
        nb.add(self.markup, text="  Plan Viewing  ")

        self.loft = LoftTab(nb, theme, status, root,
                            on_bim=self._loft_to_3d,
                            get_fieldstitch=lambda: self.fieldstitch)
        nb.add(self.loft, text="  The Loft  ")

        self.fieldstitch = FieldstitchTab(nb, theme, status, root,
                                          on_pins=self._pins_to_3d,
                                          get_loft=lambda: self.loft)
        nb.add(self.fieldstitch, text="  Fieldstitch Layout  ")

        self.asbuilt = AsBuiltPanel(nb, theme, status,
                                    lambda: nb.select(self.markup))
        nb.add(self.asbuilt, text="  As-Built Drawings  ")

        self.bim = Bim3DViewer(nb, theme, on_open_sheet=self._open_sheet)
        nb.add(self.bim, text="  BIM Viewer  ")
        # place the open plan's sheets into the model as floor planes
        bar = ttk.Frame(self.bim)
        ttk.Button(bar, text="⌂ From plan…", command=self._extrude_plan
                   ).pack(side="left", padx=(0, 4))
        ttk.Button(bar, text="Place open plan's sheets in 3D",
                   command=self._place_sheets).pack(side="left")
        bar.place(relx=1.0, y=4, anchor="ne", x=-8)

    def _extrude_plan(self):
        """Your own floor plan becomes the 3D model: vector linework from the
        Fieldstitch plan is extruded into walls, in the same world frame as
        the layout points — so pins land inside the real building."""
        from tkinter import messagebox, simpledialog
        job = self.fieldstitch.job
        if not job or not job.pdf_path:
            messagebox.showinfo(
                "From plan", "Open the plan in Fieldstitch Layout first — "
                             "the model shares its scale and basepoint so "
                             "layout pins land inside the building.")
            self.nb.select(self.fieldstitch)
            return
        if job.cal is None:
            messagebox.showinfo("From plan",
                                "Set the Fieldstitch scale first (scale ▾).")
            self.nb.select(self.fieldstitch)
            return
        ans = simpledialog.askstring(
            "From plan", "Wall height and floor count —  height, floors "
                         "(e.g.  10, 3):", initialvalue="10, 1", parent=self)
        if not ans:
            return
        try:
            parts = [v.strip() for v in ans.split(",")]
            height = float(parts[0])
            floors = int(parts[1]) if len(parts) > 1 else 1
        except (ValueError, IndexError):
            messagebox.showwarning("From plan", "Format:  height, floors")
            return
        page = self.fieldstitch.viewer.page_no
        pdf = job.pdf_path
        # snapshot the georeference into a detached job (house rule: workers
        # get plain data) — the live job can gain points / a new basepoint /
        # a new scale on the UI thread while the worker reads it
        from .. import fieldstitch as fs
        snap = fs.LayoutJob()               # no pdf_path: no sidecar I/O
        snap.scale = dict(job.scale) if job.scale else None
        snap.units = job.units
        snap.base_page_xy = tuple(job.base_page_xy)
        snap.base_world = tuple(job.base_world)
        snap.rotation_deg = float(job.rotation_deg)
        from .widgets import run_bg, toast

        def work():
            from .. import extrude
            return extrude.model_from_plan(pdf, page_no=page, job=snap,
                                           wall_height=height, floors=floors)

        def done(res, err):
            if err:
                messagebox.showerror("From plan", str(err))
                return
            model, stats = res
            self.bim.set_model(model)
            self.nb.select(self.bim)
            toast(self.winfo_toplevel(), self.bim.theme,
                  f"Extruded {stats['walls']} wall(s) × {stats['floors']} "
                  f"floor(s) from the plan")
            if self.fieldstitch.job and self.fieldstitch.job.points:
                self.fieldstitch.push_pins()   # pins land inside the model

        run_bg(self, work, done)

    def _loft_to_3d(self, model):
        """A drafted plan extrudes straight into the BIM viewer."""
        self.bim.set_model(model)
        self.nb.select(self.bim)

    def _pins_to_3d(self, pins):
        """Fieldstitch points arrive as world-coordinate 3D pins."""
        import rfi_stamper.bim as bim
        if self.bim.model is None:
            self.bim.set_model(bim.demo_building())
        self.bim.set_pins(pins)
        self.nb.select(self.bim)

    def _open_sheet(self, page_no, label):
        """2D ↔ 3D link: clicking a sheet plane in the model opens that sheet
        in Plan Viewing."""
        self.nb.select(self.markup)
        if self.markup.viewer.doc:
            self.markup.viewer.goto(page_no)

    def _place_sheets(self):
        v = self.markup.viewer
        if not v.doc:
            from tkinter import messagebox
            messagebox.showinfo("BIM", "Open a plan set in Plan Viewing "
                                       "first.")
            return
        # one plane per sheet, stacked at floor heights, labeled with the
        # detected sheet number where the navigator found one
        labels = {}
        for iid in self.markup.sheet_tree.get_children():
            labels[int(iid)] = self.markup.sheet_tree.item(iid, "text").strip()
        n = min(v.page_count, 8)
        for i in range(1, n + 1):
            self.bim.add_sheet(labels.get(i, f"PAGE {i}"), i,
                               elevation=(i - 1) * 12.0 + 0.5)

    def refresh(self):
        pass

    def commands(self):
        return ([("Open BIM demo model", "Plans",
                  lambda: self.nb.select(self.bim)),
                 ("Place plan sheets in 3D", "Plans", self._place_sheets)]
                + self.loft.commands() + self.fieldstitch.commands()
                + self.markup.commands() + self.asbuilt.compare.commands())
