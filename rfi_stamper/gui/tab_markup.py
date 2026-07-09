"""Markup & Measure tab: full offline markup editor.

Pieces: tool chest (searchable presets) | toolbar + PDF canvas | properties;
markups list below (searchable, statuses, CSV export).  Multiply, measurements
with calibrated scale and custom captions, undo/redo, drag-drop everywhere.
Data layer: rfi_stamper.markups (GUI-free, tested separately).
"""
from __future__ import annotations

import json
import os
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk

from .. import markups as mk
from ..markups import measure
from . import dnd
from .viewer import PDFViewer
from .widgets import Tooltip, make_tree, open_path, run_bg

TOOLS = [
    ("select", "Select", "V", "Click to select, drag to move, Del deletes"),
    ("pen", "Pen", "P", "Freehand vector pen"),
    ("highlighter", "Highlight", "G", "Wide translucent stroke"),
    ("line", "Line", "L", "Straight line"),
    ("arrow", "Arrow", "A", "Arrow (head at second click)"),
    ("rect", "Rect", "R", "Rectangle"),
    ("ellipse", "Oval", "E", "Ellipse"),
    ("cloud", "Cloud", "C", "Revision cloud"),
    ("callout", "Callout", "Q", "Click arrow tip, then text position"),
    ("text", "Text", "T", "Click to place a text note"),
    ("image", "Image", "I", "Drag a box, then pick an image (or drop one)"),
    ("calibrate", "Calibrate", "", "Click two points of a known dimension"),
    ("measure_length", "Length", "M", "Two-point measurement"),
    ("measure_polylength", "Polylength", "", "Click points, double-click to end"),
    ("measure_area", "Area", "", "Click boundary, double-click to close"),
    ("count", "Count", "N", "Click to drop count dots; Esc to stop"),
]
POLY_TOOLS = {"measure_polylength", "measure_area"}
TWOPT_TOOLS = {"line", "arrow", "rect", "ellipse", "cloud", "image",
               "measure_length", "calibrate"}
MEASURE_TOOLS = {"measure_length", "measure_polylength", "measure_area"}


class MarkupTab(ttk.Frame):
    def __init__(self, parent, theme, status, author=""):
        super().__init__(parent, padding=6)
        self.theme = theme
        self.status = status
        self.author = author
        self.store = None
        self.cals = {}          # page_no -> ScaleCal (per-sheet scale memory)
        self.default_cal = None
        self.tool = "select"
        self.cur_style = mk.Style()
        self.selection: set = set()
        self.undo_stack: list = []
        self.redo_stack: list = []
        self._pts = []          # in-progress polyline points (page pt)
        self._start = None      # in-progress two-point start
        self._drag_from = None  # move-drag anchor (page pt)
        self._moved = False
        self.chest = None       # ToolChest, loaded lazily
        self.on_opened = None   # app hook: called with path after open_pdf
        self.drop_hint = "Open in Markup & Measure"

        outer = ttk.Panedwindow(self, orient="vertical")
        outer.pack(fill="both", expand=True)
        top = ttk.Panedwindow(outer, orient="horizontal")
        outer.add(top, weight=4)

        # ---------------------------------------------------- tool chest
        chest_pane = ttk.Frame(top, padding=4)
        top.add(chest_pane, weight=0)
        ttk.Label(chest_pane, text="Tool Chest", style="Title.TLabel").pack(anchor="w")
        self.chest_q = tk.StringVar()
        qe = ttk.Entry(chest_pane, textvariable=self.chest_q)
        qe.pack(fill="x", pady=2)
        Tooltip(qe, "Search your saved tools", theme)
        self.chest_q.trace_add("write", lambda *_: self.fill_chest())
        self.chest_list = tk.Listbox(chest_pane, width=22, height=14,
                                     activestyle="none")
        self.chest_list.pack(fill="both", expand=True)
        theme.register(lambda c: theme.style_listbox(self.chest_list))
        self.chest_list.bind("<Double-Button-1>", self.use_preset)
        ttk.Button(chest_pane, text="Use preset", command=self.use_preset
                   ).pack(fill="x", pady=(4, 1))
        ttk.Button(chest_pane, text="Save current as preset…",
                   command=self.save_preset).pack(fill="x", pady=1)
        ttk.Button(chest_pane, text="Delete preset", command=self.del_preset
                   ).pack(fill="x", pady=1)

        # sheet navigator: thumbnails + detected sheet numbers — construction
        # users navigate by "P-2.01", not by page 37
        ttk.Label(chest_pane, text="Sheets", style="Title.TLabel"
                  ).pack(anchor="w", pady=(12, 2))
        self.sheet_tree = ttk.Treeview(chest_pane, show="tree", height=7,
                                       style="Sheets.Treeview")
        self.sheet_tree.pack(fill="both", expand=True)
        self.sheet_tree.bind("<<TreeviewSelect>>", self._goto_sheet)
        self._thumbs = []            # PhotoImage refs (tk needs live handles)

        # ------------------------------------------------------- center
        center = ttk.Frame(top)
        top.add(center, weight=5)
        tb = ttk.Frame(center)
        tb.pack(fill="x")
        ttk.Button(tb, text="Open PDF…", command=self.open_pdf).pack(side="left")
        ttk.Button(tb, text="Apply to PDF…", style="Accent.TButton",
                   command=self.apply_pdf).pack(side="right", padx=2)
        # tools on three short labeled rows — sixteen buttons never fit one
        self.tool_btns = {}
        groups = (("Draw", ("select", "pen", "highlighter", "line", "arrow",
                            "rect", "ellipse")),
                  ("Note", ("cloud", "callout", "text", "image")),
                  ("Measure", ("calibrate", "measure_length",
                               "measure_polylength", "measure_area", "count")))
        tool_by_name = {t[0]: t for t in TOOLS}
        for caption, names in groups:
            row = ttk.Frame(center)
            row.pack(fill="x", pady=(2, 0))
            ttk.Label(row, text=caption, style="Muted.TLabel", width=8
                      ).pack(side="left")
            for name in names:
                _n, label, key, tip = tool_by_name[name]
                b = ttk.Button(row, text=label, style="Tool.TButton",
                               command=lambda n=name: self.set_tool(n))
                b.pack(side="left", padx=1)
                Tooltip(b, tip + (f"  [{key}]" if key else ""), theme)
                self.tool_btns[name] = b
        self.viewer = PDFViewer(center, theme, on_render=self.redraw_markups,
                                on_page_changed=self._on_page_changed)
        self.viewer.pack(fill="both", expand=True, pady=(4, 0))
        cv = self.viewer.canvas
        cv.bind("<ButtonPress-1>", self.on_press)
        cv.bind("<B1-Motion>", self.on_motion)
        cv.bind("<ButtonRelease-1>", self.on_release)
        cv.bind("<Double-Button-1>", self.on_double)
        cv.bind("<Motion>", self.on_hover)
        dnd.enable_drop(cv, self.on_drop)
        self.scale_btn = ttk.Menubutton(tb, text="scale: not calibrated")
        self.scale_btn.pack(side="right", padx=8)
        self.scale_all_pages = tk.BooleanVar(value=False)
        self._build_scale_menu()

        # --------------------------------------------------- properties
        props = ttk.Frame(top, padding=4)
        top.add(props, weight=0)
        ttk.Label(props, text="Properties", style="Title.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w")
        self.color_btn = tk.Button(props, text="  ", width=3,
                                   command=lambda: self.pick_color("color"))
        self.fill_btn = tk.Button(props, text="none", width=5,
                                  command=lambda: self.pick_color("fill"))
        self.width_var = tk.StringVar(value="1.5")
        self.opacity_var = tk.StringVar(value="1.0")
        self.font_var = tk.StringVar(value="11")
        self.subject_var = tk.StringVar()
        self.comment_var = tk.StringVar()
        self.textlbl_var = tk.StringVar()
        self.caption_var = tk.StringVar()
        self.status_var = tk.StringVar(value="none")
        rows = [
            ("Color", self.color_btn), ("Fill", self.fill_btn),
            ("Width", ttk.Spinbox(props, from_=0.25, to=24, increment=0.25,
                                  textvariable=self.width_var, width=7)),
            ("Opacity", ttk.Spinbox(props, from_=0.1, to=1.0, increment=0.05,
                                    textvariable=self.opacity_var, width=7)),
            ("Font pt", ttk.Spinbox(props, from_=5, to=72, increment=1,
                                    textvariable=self.font_var, width=7)),
            ("Subject", ttk.Entry(props, textvariable=self.subject_var, width=16)),
            ("Comment", ttk.Entry(props, textvariable=self.comment_var, width=16)),
            ("Label", ttk.Entry(props, textvariable=self.textlbl_var, width=16)),
            ("Caption", ttk.Entry(props, textvariable=self.caption_var, width=16)),
            ("Status", ttk.Combobox(props, values=list(mk.STATUSES),
                                    textvariable=self.status_var, width=13,
                                    state="readonly")),
        ]
        for i, (lbl, w) in enumerate(rows, start=1):
            ttk.Label(props, text=lbl).grid(row=i, column=0, sticky="w", pady=1)
            w.grid(row=i, column=1, sticky="ew", pady=1)
        Tooltip(rows[8][1], "Caption template for measurement text on the "
                            "drawing. Placeholders: {value} {unit} {subject} "
                            "{comment} {text} {page} {status}", theme)
        # only push a status from 'Apply to selection' if the user actually
        # picked one since the selection last changed (else it would reset
        # every selected markup back to the combobox's stale value)
        self._status_touched = False
        rows[9][1].bind("<<ComboboxSelected>>",
                        lambda e: setattr(self, "_status_touched", True))
        r = len(rows) + 1
        ttk.Button(props, text="Apply to selection", command=self.apply_props
                   ).grid(row=r, column=0, columnspan=2, sticky="ew", pady=(6, 1))
        ttk.Button(props, text="Multiply…  (Ctrl+M)", command=self.multiply_dialog
                   ).grid(row=r + 1, column=0, columnspan=2, sticky="ew", pady=1)
        ttk.Button(props, text="Delete selection", command=self.delete_selection
                   ).grid(row=r + 2, column=0, columnspan=2, sticky="ew", pady=1)
        self.autonum_var = tk.BooleanVar(value=True)
        autonum = ttk.Checkbutton(props, text="Auto-number counts",
                                  variable=self.autonum_var)
        autonum.grid(row=r + 3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        Tooltip(autonum, "Count dots get sequential labels from the Label "
                         "field: P → P-001, P-002…  Perfect for punch lists.",
                theme)
        ttk.Label(props, text="Statuses: Alt+1..5", style="Muted.TLabel"
                  ).grid(row=r + 4, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # ------------------------------------------------- markups list
        bottom = ttk.Frame(outer, padding=(4, 2))
        outer.add(bottom, weight=2)
        lb = ttk.Frame(bottom)
        lb.pack(fill="x")
        ttk.Label(lb, text="Markups List", style="Title.TLabel").pack(side="left")
        self.list_q = tk.StringVar()
        qe2 = ttk.Entry(lb, textvariable=self.list_q, width=24)
        qe2.pack(side="left", padx=8)
        Tooltip(qe2, "Search markups (subject, comment, text, type, status)", theme)
        self.list_q.trace_add("write", lambda *_: self.fill_list())
        self.latest_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(lb, text="latest status only",
                        variable=self.latest_var).pack(side="right")
        ttk.Button(lb, text="Export CSV…", command=self.export_csv
                   ).pack(side="right", padx=6)
        frame, self.mtree = make_tree(
            bottom, theme,
            [("page", "PG"), ("type", "TYPE"), ("subject", "SUBJECT"),
             ("comment", "COMMENT"), ("text", "LABEL"), ("status", "STATUS"),
             ("value", "VALUE"), ("author", "AUTHOR")],
            (36, 90, 150, 220, 110, 80, 110, 90), height=6)
        frame.pack(fill="both", expand=True, pady=2)
        self.mtree.bind("<Double-1>", self.jump_to_markup)
        self.mtree.bind("<<TreeviewSelect>>", self.on_list_select)

        self.set_tool("select")
        self._load_chest()
        self._sync_props_from_style()

    # =================================================== shortcuts/commands
    def commands(self):
        cmds = [
            ("Open PDF for markup", "Markup", self.open_pdf),
            ("Apply markups to PDF", "Markup", self.apply_pdf),
            ("Multiply selection", "Markup", self.multiply_dialog, "Ctrl+M"),
            ("Undo markup change", "Markup", self.undo, "Ctrl+Z"),
            ("Redo markup change", "Markup", self.redo, "Ctrl+Y"),
            ("Export markups CSV", "Markup", self.export_csv),
            ("Delete selected markups", "Markup", self.delete_selection, "Del"),
        ]
        for name, label, key, _tip in TOOLS:
            cmds.append((f"Tool: {label}", "Markup",
                         lambda n=name: self.set_tool(n), key))
        for i, st in enumerate(mk.STATUSES, start=1):
            if i > 5:
                break
            cmds.append((f"Set status: {st}", "Markup",
                         lambda s=st: self.set_status(s), f"Alt+{i}"))
        return cmds

    def bind_shortcuts(self, root):
        for name, _label, key, _tip in TOOLS:
            if key:
                root.bind(f"<Key-{key.lower()}>",
                          lambda e, n=name: self._kb(e, lambda: self.set_tool(n)))
        root.bind("<Delete>", lambda e: self._kb(e, self.delete_selection))
        root.bind("<Escape>", lambda e: self.on_escape())
        root.bind("<Control-z>", lambda e: self._kb(e, self.undo))
        root.bind("<Control-y>", lambda e: self._kb(e, self.redo))
        root.bind("<Control-m>", lambda e: self._kb(e, self.multiply_dialog))
        for i, st in enumerate(mk.STATUSES, start=1):
            if i > 5:
                break
            root.bind(f"<Alt-Key-{i}>",
                      lambda e, s=st: self._kb(e, lambda: self.set_status(s)))

    def _kb(self, event, fn):
        """Run a shortcut only when this tab is visible and focus isn't typing."""
        w = event.widget
        if isinstance(w, (tk.Entry, ttk.Entry, tk.Text, ttk.Spinbox, ttk.Combobox)) \
                or w.winfo_class() in ("Entry", "TEntry", "Text", "TSpinbox",
                                       "TCombobox"):
            return
        if not self.winfo_ismapped():
            return
        fn()
        return "break"

    # ========================================================== document IO
    def open_pdf(self, path=None):
        path = path or filedialog.askopenfilename(filetypes=[("PDF", "*.pdf")])
        if not path:
            return
        path = self._maybe_unlock(path)
        self.cancel_tool()
        self.viewer.open(path)
        self.store = mk.MarkupStore(path)     # autoloads sidecar if present
        self._load_cals(path)
        self.selection.clear()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._show_cal()
        self.fill_list()
        self._load_sheets(path)
        self.status.set(f"Opened {os.path.basename(path)} — "
                        f"{len(self.store.markups)} saved markup(s)", "ok")
        if self.on_opened:
            self.on_opened(path)

    def handle_drop(self, paths):
        self.on_drop(paths)

    def _maybe_unlock(self, path):
        """If the PDF is owner/password-locked, transparently unlock a working
        copy so markup can proceed — the original is never touched."""
        try:
            from .. import pdfdoctor
            if not pdfdoctor.is_encrypted(path):
                return path
            out = os.path.splitext(path)[0] + "_unlocked.pdf"
            if pdfdoctor.unlock(path, out):
                self.status.set("PDF was locked — opened an unlocked copy", "ok")
                return out
        except Exception:   # noqa: BLE001 -- unlock is best-effort
            pass
        return path

    # ------------------------------------------------------ sheet navigator
    def _load_sheets(self, path):
        """Detect sheet numbers + render tiny thumbnails in the background;
        PhotoImages are created on the UI thread (tk requirement)."""
        self.sheet_tree.delete(*self.sheet_tree.get_children())
        self._thumbs.clear()

        def work():
            import fitz
            from ..sheets import SheetIndex
            idx = SheetIndex(path)                 # own doc handle, thread-safe
            doc = fitz.open(path)
            out = []
            for info in idx.pages:
                page = doc[info.page_no - 1]
                z = 84.0 / max(page.rect.width, 1)
                pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
                out.append((info.page_no, info.sheet, pix.tobytes("ppm")))
            doc.close()
            return out

        def done(rows, err):
            if err or not rows:
                return
            if self.viewer.path != path:
                return                      # a different PDF was opened since
            for page_no, sheet, ppm in rows:
                img = tk.PhotoImage(data=ppm)
                self._thumbs.append(img)
                self.sheet_tree.insert("", "end", iid=str(page_no),
                                       text=f"  {sheet}", image=img)

        run_bg(self, work, done)

    def _goto_sheet(self, _e):
        sel = self.sheet_tree.selection()
        if sel:
            self.viewer.goto(int(sel[0]))

    def _on_page_changed(self, _n):
        self.cancel_tool()
        self._show_cal()        # per-page scale: reflect this sheet's calibration

    def on_drop(self, paths):
        pdfs = [p for p in paths if p.lower().endswith(".pdf")]
        imgs = [p for p in paths if p.lower().endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff"))]
        if pdfs:
            self.open_pdf(pdfs[0])
        if imgs and self.store:
            cv = self.viewer.canvas
            cx = cv.canvasx(cv.winfo_width() / 2)
            cy = cv.canvasy(cv.winfo_height() / 2)
            x, y = self.viewer.canvas_to_page(cx, cy)
            self.push_undo()
            for i, ip in enumerate(imgs):
                m = mk.Markup.new(self.viewer.page_no, "image",
                                  [(x + i * 20, y + i * 20),
                                   (x + 180 + i * 20, y + 120 + i * 20)],
                                  image_path=ip, author=self.author,
                                  style=self._style_copy())
                self.store.add(m)
            self.after_change()

    # per-page scale memory: plans mix scales (plan at 1/8", details at 3/4"),
    # so each page keeps its own calibration; `default_cal` covers pages that
    # have none (e.g. a whole set stamped at one scale).
    @property
    def cal(self):
        return self.cal_for(self.viewer.page_no)

    def cal_for(self, page):
        return self.cals.get(page) or self.default_cal

    def _cal_path(self, pdf_path):
        return pdf_path + ".scale.json"

    def _load_cals(self, pdf_path):
        self.cals = {}
        self.default_cal = None
        try:
            with open(self._cal_path(pdf_path), encoding="utf-8") as f:
                d = json.load(f)
        except Exception:   # noqa: BLE001 -- no calibration yet
            return
        if isinstance(d, dict) and "pages" in d:          # per-page format
            for k, v in d.get("pages", {}).items():
                self.cals[int(k)] = measure.ScaleCal.from_dict(v)
            if d.get("default"):
                self.default_cal = measure.ScaleCal.from_dict(d["default"])
        elif isinstance(d, dict) and "real_per_pt" in d:  # legacy flat -> default
            self.default_cal = measure.ScaleCal.from_dict(d)

    def _save_cal(self):
        if not self.viewer.path:
            return
        try:
            payload = {"version": 2,
                       "pages": {str(k): c.to_dict() for k, c in self.cals.items()}}
            if self.default_cal:
                payload["default"] = self.default_cal.to_dict()
            with open(self._cal_path(self.viewer.path), "w",
                      encoding="utf-8") as f:
                json.dump(payload, f)
        except Exception:   # noqa: BLE001
            pass

    # architectural / metric scale presets: X" on paper = 1'-0" real, or 1:N.
    # real_per_pt(ft) = (12/X inches-per-ft => 1/X ft per paper inch) / 72;
    # metric: 1 pt paper = N pt real = N * 0.0254/72 m.
    _SCALES = (
        [(f'{lbl}" = 1\'-0"', (1.0 / x) / 72.0, "ft-in") for lbl, x in
         (("1/16", 1 / 16), ("3/32", 3 / 32), ("1/8", 1 / 8), ("3/16", 3 / 16),
          ("1/4", 1 / 4), ("3/8", 3 / 8), ("1/2", 1 / 2), ("3/4", 3 / 4),
          ("1", 1.0), ("1-1/2", 1.5), ("3", 3.0))]
        + [(f"1:{n}", n * 0.0254 / 72.0, "m") for n in (50, 100, 200, 250, 500)]
    )

    def _build_scale_menu(self):
        menu = tk.Menu(self.scale_btn, tearoff=0)
        menu.add_command(label="Calibrate from two points…",
                         command=lambda: self.set_tool("calibrate"))
        menu.add_checkbutton(label="Apply scale to ALL pages",
                             variable=self.scale_all_pages)
        menu.add_separator()
        for label, rpp, unit in self._SCALES:
            menu.add_command(
                label=label,
                command=lambda l=label, r=rpp, u=unit: self._use_scale(l, r, u))
        menu.add_separator()
        menu.add_command(label="Clear this page's scale", command=self._clear_scale)
        self.scale_btn.configure(menu=menu)

    def _set_page_cal(self, cal):
        """Store cal for the current page, or as the all-pages default."""
        if self.scale_all_pages.get():
            self.default_cal = cal
            self.cals.clear()
        else:
            self.cals[self.viewer.page_no] = cal

    def _use_scale(self, label, real_per_pt, unit):
        self._set_page_cal(measure.ScaleCal(real_per_pt=real_per_pt, unit=unit))
        self._save_cal()
        self._show_cal(label)
        self._recompute_measures()
        self.after_change()
        scope = "all pages" if self.scale_all_pages.get() else \
            f"sheet {self.viewer.page_no}"
        self.status.set(f"Scale {label} set for {scope}", "ok")

    def _clear_scale(self):
        self.cals.pop(self.viewer.page_no, None)
        if self.scale_all_pages.get():
            self.default_cal = None
        self._save_cal()
        self._show_cal()
        self._recompute_measures()
        self.after_change()

    def _show_cal(self, label=""):
        if self.cal:
            txt = label or f"1pt = {self.cal.real_per_pt:.4g} {self.cal.unit}"
            self.scale_btn.configure(text=f"scale: {txt} ▾")
        else:
            self.scale_btn.configure(text="scale: not calibrated ▾")

    # ============================================================= tools
    def set_tool(self, name):
        self.cancel_tool()
        self.tool = name
        for n, b in self.tool_btns.items():
            b.configure(style="ToolOn.TButton" if n == name else "Tool.TButton")
        cursor = {"select": "arrow", "text": "xterm"}.get(name, "crosshair")
        self.viewer.canvas.configure(cursor=cursor)
        self.status.set(f"Tool: {name}")

    def cancel_tool(self):
        self._pts = []
        self._start = None
        self.viewer.canvas.delete("preview")
        self.viewer.canvas.delete("hoverseg")

    def on_escape(self):
        """Esc: finish a click-to-place tool, else cancel the pending shape."""
        if self.tool == "count":
            self.set_tool("select")
        else:
            self.cancel_tool()

    def _draw_poly_preview(self):
        """Committed vertices of the in-progress polyline/polygon."""
        cv = self.viewer.canvas
        cv.delete("preview")
        pts = [self.viewer.page_to_canvas(x, y) for x, y in self._pts]
        color = self.cur_style.color or "#D01414"
        if len(pts) >= 2:
            flat = [c for p in pts for c in p]
            cv.create_line(*flat, fill=color, dash=(4, 3), tags="preview")
        for x, y in pts:
            cv.create_rectangle(x - 2, y - 2, x + 2, y + 2, outline=color,
                                tags="preview")

    def _style_copy(self):
        self._read_props_into_style()
        return mk.Style(**self.cur_style.to_dict())

    # ------------------------------------------------------- mouse events
    def on_press(self, event):
        if not self.store:
            return
        x, y = self.viewer.event_page_xy(event)
        t = self.tool
        if t == "select":
            self._press_select(event, x, y)
        elif t in TWOPT_TOOLS:
            self._start = (x, y)
        elif t in ("pen", "highlighter"):
            self._pts = [(x, y)]
        elif t in POLY_TOOLS:
            self._pts.append((x, y))
            self._draw_poly_preview()
        elif t == "text":
            txt = simpledialog.askstring("Text", "Note text:", parent=self)
            if txt:
                self.push_undo()
                self.store.add(mk.Markup.new(
                    self.viewer.page_no, "text", [(x, y)], text=txt,
                    author=self.author, subject=self.subject_var.get(),
                    style=self._style_copy()))
                self.after_change()
        elif t == "count":
            self.push_undo()
            label = self.textlbl_var.get() or self.subject_var.get() or "count"
            if self.autonum_var.get():
                prefix = label.rstrip("-0123456789 ").strip() or "C"
                seq = sum(1 for m in self.store.markups
                          if m.type == "count"
                          and m.text.startswith(prefix + "-")) + 1
                label = f"{prefix}-{seq:03d}"
            self.store.add(mk.Markup.new(
                self.viewer.page_no, "count", [(x, y)], text=label,
                subject=self.subject_var.get() or label, author=self.author,
                style=self._style_copy()))
            self.after_change()
        elif t == "callout":
            self._pts.append((x, y))
            if len(self._pts) == 2:
                tip, box = self._pts[0], self._pts[1]
                txt = simpledialog.askstring("Callout", "Callout text:",
                                             parent=self)
                self.cancel_tool()
                if txt:
                    self.push_undo()
                    self.store.add(mk.Markup.new(
                        self.viewer.page_no, "callout", [box, tip], text=txt,
                        author=self.author, subject=self.subject_var.get(),
                        style=self._style_copy()))
                    self.after_change()
            else:
                self.status.set("Callout: now click where the text should sit")

    def _press_select(self, event, x, y):
        cv = self.viewer.canvas
        hit = None
        # canvas hit first — topmost item wins (find_overlapping is bottom-up)
        for item in reversed(cv.find_overlapping(cv.canvasx(event.x) - 2,
                                                 cv.canvasy(event.y) - 2,
                                                 cv.canvasx(event.x) + 2,
                                                 cv.canvasy(event.y) + 2)):
            tags = cv.gettags(item)
            if "mk" in tags:
                hit = next((t[3:] for t in tags if t.startswith("id:")), None)
                if hit:
                    break
        if not hit:
            # unfilled shapes only hit on their outline; fall back to bbox so a
            # click inside a rect/ellipse/cloud still selects it (topmost first)
            for m in reversed(self.store.for_page(self.viewer.page_no)):
                x0, y0, x1, y1 = m.bbox()
                if x0 - 3 <= x <= x1 + 3 and y0 - 3 <= y <= y1 + 3:
                    hit = m.id
                    break
        if hit:
            if event.state & 0x1:            # shift extends
                self.selection.symmetric_difference_update({hit})
            elif hit not in self.selection:
                self.selection = {hit}
            self._drag_from = (x, y)
            self._moved = False
        else:
            self.selection = set()
            self._drag_from = None
        self._status_touched = False
        self.redraw_markups()

    def on_motion(self, event):
        if not self.store:
            return
        x, y = self.viewer.event_page_xy(event)
        t = self.tool
        cv = self.viewer.canvas
        if t == "select" and self._drag_from and self.selection:
            if not self._moved:
                self.push_undo()
                self._moved = True
            dx, dy = x - self._drag_from[0], y - self._drag_from[1]
            self._drag_from = (x, y)
            for mid in self.selection:
                m = self.store.get(mid)
                # never move markups on other pages (list selection can span
                # pages; dragging must only affect what the user can see)
                if m and m.page == self.viewer.page_no:
                    m.points = [(px + dx, py + dy) for px, py in m.points]
            self.redraw_markups()
        elif t in TWOPT_TOOLS and self._start:
            cv.delete("preview")
            x0, y0 = self.viewer.page_to_canvas(*self._start)
            x1, y1 = self.viewer.page_to_canvas(x, y)
            color = self.cur_style.color or "#D01414"
            if t in ("rect", "cloud", "image"):
                cv.create_rectangle(x0, y0, x1, y1, outline=color,
                                    dash=(4, 3), tags="preview")
            elif t == "ellipse":
                cv.create_oval(x0, y0, x1, y1, outline=color, dash=(4, 3),
                               tags="preview")
            else:
                cv.create_line(x0, y0, x1, y1, fill=color, dash=(4, 3),
                               tags="preview")
        elif t in ("pen", "highlighter") and self._pts:
            lx, ly = self._pts[-1]
            if (x - lx) ** 2 + (y - ly) ** 2 > 2:
                self._pts.append((x, y))
                x0, y0 = self.viewer.page_to_canvas(lx, ly)
                x1, y1 = self.viewer.page_to_canvas(x, y)
                w = self.cur_style.width * self.viewer.scale
                cv.create_line(x0, y0, x1, y1, fill=self.cur_style.color,
                               width=w * (3 if t == "highlighter" else 1),
                               capstyle="round", tags="preview")

    def on_hover(self, event):
        if self.tool in POLY_TOOLS and self._pts:
            cv = self.viewer.canvas
            cv.delete("hoverseg")
            x0, y0 = self.viewer.page_to_canvas(*self._pts[-1])
            cv.create_line(x0, y0, cv.canvasx(event.x), cv.canvasy(event.y),
                           fill=self.cur_style.color, dash=(2, 3),
                           tags="hoverseg")

    def on_release(self, event):
        if not self.store:
            return
        x, y = self.viewer.event_page_xy(event)
        t = self.tool
        if t == "select":
            if self._moved:
                self.after_change()
            self._drag_from = None
            return
        if t in TWOPT_TOOLS and self._start:
            p0, p1 = self._start, (x, y)
            self.cancel_tool()
            if abs(p0[0] - p1[0]) < 1 and abs(p0[1] - p1[1]) < 1:
                return
            if t == "calibrate":
                self._finish_calibrate(p0, p1)
                return
            kw = {}
            if t == "image":
                ip = filedialog.askopenfilename(filetypes=[
                    ("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.tif *.tiff")])
                if not ip:
                    return
                kw["image_path"] = ip
            self.push_undo()
            m = mk.Markup.new(self.viewer.page_no, t, [p0, p1],
                              author=self.author, subject=self.subject_var.get(),
                              comment=self.comment_var.get(),
                              caption_template=self.caption_var.get(),
                              style=self._style_copy(), **kw)
            self._set_measure(m)
            self.store.add(m)
            self.after_change()
        elif t in ("pen", "highlighter") and self._pts:
            pts = self._pts
            self.cancel_tool()
            if len(pts) > 1:
                self.push_undo()
                self.store.add(mk.Markup.new(
                    self.viewer.page_no, t, pts, author=self.author,
                    subject=self.subject_var.get(), style=self._style_copy()))
                self.after_change()

    def on_double(self, event):
        if self.tool in POLY_TOOLS and len(self._pts) >= 2:
            pts = self._pts[:]
            t = self.tool
            self.cancel_tool()
            self.viewer.canvas.delete("hoverseg")
            if t == "measure_area" and len(pts) < 3:
                return
            self.push_undo()
            m = mk.Markup.new(self.viewer.page_no, t, pts, author=self.author,
                              subject=self.subject_var.get(),
                              comment=self.comment_var.get(),
                              caption_template=self.caption_var.get(),
                              style=self._style_copy())
            self._set_measure(m)
            self.store.add(m)
            self.after_change()
        elif self.tool == "select":
            # double-click a markup -> edit its text/comment quickly
            if len(self.selection) == 1:
                m = self.store.get(next(iter(self.selection)))
                if m:
                    txt = simpledialog.askstring(
                        "Edit", "Text / label:", initialvalue=m.text, parent=self)
                    if txt is not None:
                        self.push_undo()
                        m.text = txt
                        self.after_change()

    def _finish_calibrate(self, p0, p1):
        ans = simpledialog.askstring(
            "Calibrate scale",
            "Real length of the picked segment (e.g.  24 ft   3.5 m   18 in):",
            parent=self)
        if not ans:
            return
        parts = ans.strip().split()
        try:
            val = float(parts[0])
            unit = parts[1] if len(parts) > 1 else "ft"
        except (ValueError, IndexError):
            messagebox.showwarning("Calibrate", "Format:  <number> <unit>")
            return
        try:
            self._set_page_cal(measure.ScaleCal.calibrate(p0, p1, val, unit))
        except ValueError as e:
            messagebox.showwarning("Calibrate", str(e))
            return
        self._save_cal()
        self._show_cal()
        self._recompute_measures()
        self.after_change()
        self.status.set(f"Calibrated: {val} {unit} segment", "ok")

    def _set_measure(self, m):
        cal = self.cal_for(m.page)          # each markup measured in its page's scale
        if m.type in MEASURE_TOOLS and cal:
            m.measure_value = measure.compute(m, cal)
            m.measure_unit = cal.unit

    def _recompute_measures(self):
        if not self.store:
            return
        for m in self.store.markups:
            self._set_measure(m)

    # ====================================================== change handling
    UNDO_LIMIT = 1000       # effectively unlimited for a session; caps memory

    def push_undo(self):
        if self.store:
            self.undo_stack.append([m.to_dict() for m in self.store.markups])
            del self.undo_stack[:-self.UNDO_LIMIT]
            self.redo_stack.clear()

    def undo(self):
        self._swap_state(self.undo_stack, self.redo_stack)

    def redo(self):
        self._swap_state(self.redo_stack, self.undo_stack)

    def _swap_state(self, pop_from, push_to):
        if not self.store or not pop_from:
            return
        push_to.append([m.to_dict() for m in self.store.markups])
        snap = pop_from.pop()
        self.store.markups[:] = [mk.Markup.from_dict(d) for d in snap]
        self.selection = {mid for mid in self.selection
                          if self.store.get(mid)}
        self.after_change()

    def after_change(self):
        if self.store:
            try:
                self.store.save()
            except Exception as e:      # noqa: BLE001
                self.status.set(f"sidecar save failed: {e}", "err")
        self.redraw_markups()
        self.fill_list()

    # ============================================================ rendering
    def redraw_markups(self, _page=None):
        cv = self.viewer.canvas
        cv.delete("mk")
        cv.delete("selbox")
        if not self.store or not self.viewer.doc:
            return
        for m in self.store.for_page(self.viewer.page_no):
            self._draw(cv, m)
        for mid in self.selection:
            m = self.store.get(mid)
            if m and m.page == self.viewer.page_no:
                x0, y0, x1, y1 = m.bbox()
                cx0, cy0 = self.viewer.page_to_canvas(x0 - 3, y0 - 3)
                cx1, cy1 = self.viewer.page_to_canvas(x1 + 3, y1 + 3)
                cv.create_rectangle(cx0, cy0, cx1, cy1, dash=(3, 3),
                                    outline="#3b82f6", width=1.4, tags="selbox")
        if self._pts:
            # a render wipes the canvas (delete("all")); an in-progress
            # polyline/polygon preview must survive it — e.g. the debounced
            # fit-width render ~50 ms after open, or any zoom/resize mid-draw
            # (page changes clear _pts via cancel_tool first, so this never
            # leaks a preview across pages).
            self._draw_poly_preview()

    def _draw(self, cv, m):
        s = self.viewer.scale
        tags = ("mk", f"id:{m.id}")
        st = m.style
        pts = [self.viewer.page_to_canvas(x, y) for x, y in m.points]
        flat = [c for p in pts for c in p]
        w = max(1.0, st.width * s)
        t = m.type
        if t in ("pen", "measure_polylength"):
            if len(flat) >= 4:
                cv.create_line(*flat, fill=st.color, width=w, smooth=(t == "pen"),
                               capstyle="round", tags=tags)
        elif t == "highlighter":
            if len(flat) >= 4:
                cv.create_line(*flat, fill=st.color, width=w * 3, smooth=True,
                               capstyle="round", stipple="gray50", tags=tags)
        elif t in ("line", "measure_length"):
            cv.create_line(*flat, fill=st.color, width=w, tags=tags)
        elif t == "arrow":
            cv.create_line(*flat, fill=st.color, width=w, arrow="last",
                           arrowshape=(10 * s, 12 * s, 4 * s), tags=tags)
        elif t == "rect":
            cv.create_rectangle(*flat[:4], outline=st.color, width=w,
                                fill=st.fill or "", tags=tags)
        elif t == "ellipse":
            cv.create_oval(*flat[:4], outline=st.color, width=w,
                           fill=st.fill or "", tags=tags)
        elif t == "cloud":
            (x0, y0), (x1, y1) = m.points[0], m.points[1]
            arc = mk.cloud_path_points(min(x0, x1), min(y0, y1),
                                       max(x0, x1), max(y0, y1))
            cpts = [c for x, y in arc for c in self.viewer.page_to_canvas(x, y)]
            cv.create_line(*cpts, fill=st.color, width=w, tags=tags)
        elif t == "callout":
            (bx, by), (tx, ty) = pts[0], pts[1]
            fs = max(6, int(st.font_size * s))
            tid = cv.create_text(bx, by, text=m.text or "…", anchor="w",
                                 fill=st.color, font=("Segoe UI", fs),
                                 tags=tags)
            box = cv.bbox(tid)
            cv.create_rectangle(box[0] - 3, box[1] - 2, box[2] + 3, box[3] + 2,
                                outline=st.color, width=w, tags=tags)
            cv.create_line(box[0] - 3, (box[1] + box[3]) / 2, tx, ty,
                           fill=st.color, width=w,
                           arrow="last", tags=tags)
            cv.tag_raise(tid)
        elif t == "text":
            fs = max(6, int(st.font_size * s))
            cv.create_text(*pts[0], text=m.text, anchor="nw", fill=st.color,
                           font=("Segoe UI", fs), tags=tags)
        elif t == "image":
            cv.create_rectangle(*flat[:4], outline=st.color, width=w,
                                dash=(5, 3), tags=tags)
            cv.create_text((flat[0] + flat[2]) / 2, (flat[1] + flat[3]) / 2,
                           text="🖼 " + os.path.basename(m.image_path or "image"),
                           fill=st.color, tags=tags)
        elif t == "measure_area":
            if len(flat) >= 6:
                cv.create_polygon(*flat, outline=st.color, width=w, fill="",
                                  tags=tags)
        elif t == "count":
            cx, cy = pts[0]
            r = 6 * s
            cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=st.color,
                           outline="", tags=tags)
        # measurement / custom captions on the drawing itself
        cap = measure.caption_for(m, self.cal)
        if cap and t != "text":
            if t == "measure_length":
                ax = (pts[0][0] + pts[1][0]) / 2
                ay = (pts[0][1] + pts[1][1]) / 2 - 10
            elif t == "measure_area":
                ax = sum(p[0] for p in pts) / len(pts)
                ay = sum(p[1] for p in pts) / len(pts)
            elif t == "count":
                ax, ay = pts[0][0] + 9 * s, pts[0][1]
            else:
                ax, ay = pts[-1][0], pts[-1][1] - 10
            fs = max(6, int(m.style.font_size * s * 0.9))
            tid = cv.create_text(ax, ay, text=cap, fill=st.color,
                                 font=("Segoe UI", fs, "bold"), tags=tags)
            box = cv.bbox(tid)
            bg = cv.create_rectangle(box, fill="white", outline="", tags=tags)
            cv.tag_raise(tid, bg)

    # ======================================================== markups list
    def fill_list(self):
        self.mtree.delete(*self.mtree.get_children())
        if not self.store:
            return
        q = self.list_q.get().strip()
        items = self.store.search(q) if q else self.store.markups
        for m in items:
            cap = measure.caption_for(m, self.cal_for(m.page))
            self.mtree.insert("", "end", iid=m.id, values=(
                m.page, m.type, m.subject, m.comment, m.text, m.status,
                cap or (f"{m.measure_value:.2f} {m.measure_unit}"
                        if m.measure_value else ""), m.author))

    def on_list_select(self, _e):
        sel = set(self.mtree.selection())
        if sel:
            self.selection = sel
            self._status_touched = False
            self.redraw_markups()

    def jump_to_markup(self, _e):
        sel = self.mtree.selection()
        if not sel or not self.store:
            return
        m = self.store.get(sel[0])
        if m:
            self.selection = {m.id}
            self.viewer.goto(m.page)

    def set_status(self, status):
        if not self.store or not self.selection:
            return
        self.push_undo()
        for mid in self.selection:
            try:
                self.store.set_status(mid, status)
            except Exception:   # noqa: BLE001
                pass
        self.after_change()
        self.status.set(f"Status → {status} on {len(self.selection)} markup(s)",
                        "ok")

    def export_csv(self):
        if not self.store or not self.store.markups:
            messagebox.showinfo("Markups", "No markups to export.")
            return
        p = filedialog.asksaveasfilename(defaultextension=".csv",
                                         filetypes=[("CSV", "*.csv")])
        if p:
            self.store.to_csv(p, latest_status_only=self.latest_var.get())
            self.status.set(f"Markup summary exported → {p}", "ok")

    # ========================================================== properties
    def pick_color(self, which):
        initial = self.cur_style.color if which == "color" \
            else (self.cur_style.fill or "#ffffff")
        _, hexcol = colorchooser.askcolor(initial, parent=self)
        if hexcol:
            if which == "color":
                self.cur_style.color = hexcol
            else:
                self.cur_style.fill = hexcol
            self._sync_props_from_style()

    def _read_props_into_style(self):
        try:
            self.cur_style.width = float(self.width_var.get())
            self.cur_style.opacity = float(self.opacity_var.get())
            self.cur_style.font_size = float(self.font_var.get())
        except ValueError:
            pass

    def _sync_props_from_style(self):
        st = self.cur_style
        self.color_btn.configure(bg=st.color)
        self.fill_btn.configure(bg=st.fill or self.theme.colors["panel"],
                                text="" if st.fill else "none")
        self.width_var.set(str(st.width))
        self.opacity_var.set(str(st.opacity))
        self.font_var.set(str(int(st.font_size)))

    def apply_props(self):
        if not self.store or not self.selection:
            return
        self.push_undo()
        self._read_props_into_style()
        for mid in self.selection:
            m = self.store.get(mid)
            if not m:
                continue
            m.style = mk.Style(**vars(self.cur_style))
            if self.subject_var.get():
                m.subject = self.subject_var.get()
            if self.comment_var.get():
                m.comment = self.comment_var.get()
            if self.textlbl_var.get():
                m.text = self.textlbl_var.get()
            if self.caption_var.get():
                m.caption_template = self.caption_var.get()
            if self._status_touched and self.status_var.get() != m.status:
                self.store.set_status(m.id, self.status_var.get())
        self.after_change()

    def delete_selection(self):
        if not self.store or not self.selection:
            return
        self.push_undo()
        for mid in list(self.selection):
            self.store.remove(mid)
        self.selection.clear()
        self.after_change()

    # ============================================================ multiply
    def multiply_dialog(self):
        if not self.store or not self.selection:
            messagebox.showinfo("Multiply", "Select markup(s) to multiply first.")
            return
        dlg = tk.Toplevel(self)
        dlg.title("Multiply markups")
        dlg.transient(self.winfo_toplevel())
        dlg.grab_set()
        frm = ttk.Frame(dlg, padding=12)
        frm.pack(fill="both", expand=True)
        mode = tk.StringVar(value="linear")
        ttk.Radiobutton(frm, text="Linear run", variable=mode,
                        value="linear").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(frm, text="Grid", variable=mode,
                        value="grid").grid(row=0, column=1, sticky="w")
        vals = {}
        fields = [("copies", "Copies", "5"), ("dx", "Offset X (pt)", "36"),
                  ("dy", "Offset Y (pt)", "0"), ("rows", "Rows", "3"),
                  ("cols", "Columns", "3")]
        for i, (key, lbl, dv) in enumerate(fields, start=1):
            ttk.Label(frm, text=lbl).grid(row=i, column=0, sticky="w", pady=2)
            v = tk.StringVar(value=dv)
            ttk.Entry(frm, textvariable=v, width=9).grid(row=i, column=1, pady=2)
            vals[key] = v

        def go():
            try:
                copies = int(vals["copies"].get())
                dx = float(vals["dx"].get())
                dy = float(vals["dy"].get())
                rows = int(vals["rows"].get())
                cols = int(vals["cols"].get())
            except ValueError:
                messagebox.showwarning("Multiply", "Numbers only.", parent=dlg)
                return
            self.push_undo()
            made = 0
            for mid in list(self.selection):
                m = self.store.get(mid)
                if not m:
                    continue
                try:
                    if mode.get() == "grid":
                        copies_list = mk.multiply(m, 0, dx, dy, rows=rows,
                                                  cols=cols)
                    else:
                        copies_list = mk.multiply(m, copies, dx, dy)
                except ValueError as e:
                    messagebox.showwarning("Multiply", str(e), parent=dlg)
                    return
                for c in copies_list:
                    self._set_measure(c)
                    self.store.add(c)
                    made += 1
            self.after_change()
            self.status.set(f"Multiplied → {made} new markup(s)", "ok")
            dlg.destroy()

        ttk.Button(frm, text="Multiply", style="Accent.TButton", command=go
                   ).grid(row=len(fields) + 1, column=0, columnspan=2,
                          sticky="ew", pady=(8, 0))
        dlg.bind("<Return>", lambda e: go())
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    # ========================================================== tool chest
    def _load_chest(self):
        def work():
            return mk.ToolChest()

        def done(chest, err):
            if err:
                self.status.set(f"tool chest unavailable: {err}", "err")
                return
            self.chest = chest
            self.fill_chest()

        run_bg(self, work, done)

    def fill_chest(self):
        if not self.chest:
            return
        q = self.chest_q.get().strip()
        self._chest_view = self.chest.search(q) if q else list(self.chest.presets)
        self.chest_list.delete(0, "end")
        for p in self._chest_view:
            self.chest_list.insert("end", f" {p.name}  ({p.type})")

    def _picked_preset(self):
        sel = self.chest_list.curselection()
        if not sel or not self.chest:
            return None
        return self._chest_view[sel[0]]

    def use_preset(self, _e=None):
        p = self._picked_preset()
        if not p:
            return
        self.cur_style = mk.Style(**vars(p.style))
        self.subject_var.set(p.subject)
        self.caption_var.set(p.caption_template)
        if p.text:
            self.textlbl_var.set(p.text)
        self._sync_props_from_style()
        self.set_tool(p.type if p.type in {t[0] for t in TOOLS} else "rect")
        self.status.set(f"Preset '{p.name}' active", "ok")

    def save_preset(self):
        if not self.chest:
            return
        name = simpledialog.askstring("Tool Chest", "Preset name:", parent=self)
        if not name:
            return
        self._read_props_into_style()
        self.chest.add(mk.ToolPreset(
            name=name, type=self.tool if self.tool != "select" else "rect",
            style=mk.Style(**vars(self.cur_style)),
            subject=self.subject_var.get(),
            caption_template=self.caption_var.get(),
            text=self.textlbl_var.get()))
        self.chest.save()
        self.fill_chest()

    def del_preset(self):
        p = self._picked_preset()
        if p and messagebox.askyesno("Tool Chest", f"Delete preset '{p.name}'?"):
            self.chest.remove(p.name)
            self.chest.save()
            self.fill_chest()

    # ============================================================== output
    def apply_pdf(self):
        if not self.store or not self.viewer.path:
            messagebox.showinfo("Markup", "Open a PDF first.")
            return
        if not self.store.markups:
            messagebox.showinfo("Markup", "No markups to apply.")
            return
        default = os.path.splitext(self.viewer.path)[0] + "_markedup.pdf"
        out = filedialog.asksaveasfilename(defaultextension=".pdf",
                                           initialfile=os.path.basename(default),
                                           filetypes=[("PDF", "*.pdf")])
        if not out:
            return
        flatten = messagebox.askyesno(
            "Apply markups", "Flatten markups into page content?\n\n"
            "Yes = permanent (prints everywhere)\nNo = editable annotations")
        if getattr(self, "_applying", False):
            return
        self._applying = True
        src = self.viewer.path
        # deep-copy so the worker never mutates the live store (thread safety,
        # and the caption resolution below must not stick to saved markups)
        markups = [mk.Markup.from_dict(m.to_dict()) for m in self.store.markups]
        cals = {m.page: self.cal_for(m.page) for m in markups}

        def work():
            for m in markups:
                if m.type in MEASURE_TOOLS or m.type == "count":
                    cap = measure.caption_for(m, cals.get(m.page))
                    if cap and not m.comment:
                        m.comment = cap
            return mk.apply_to_pdf(src, out, markups, flatten=flatten)

        def done(res, err):
            self._applying = False
            if err:
                self.status.set(f"apply failed: {err}", "err")
                messagebox.showerror("Markup", f"Apply failed:\n{err}")
                return
            self.status.set(f"Wrote {res['annots']} annotation(s) → {out}", "ok")
            if messagebox.askyesno("Markup", "Done. Open the result?"):
                open_path(out)

        run_bg(self, work, done)
