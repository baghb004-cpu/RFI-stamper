"""Compare / Overlay tab: auto-align two drawing revisions and view the
differences as a color overlay (base = red, revision = blue, unchanged = dark).
Engine: rfi_stamper.align — deterministic image registration, fully offline."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import align, merge
from . import dnd
from .widgets import DropZone, LogConsole, Tooltip, open_path, run_bg


def np_to_photo(arr) -> tk.PhotoImage:
    """HxWx3 uint8 -> PhotoImage via an in-memory PPM."""
    h, w = arr.shape[:2]
    header = f"P6 {w} {h} 255 ".encode()
    return tk.PhotoImage(data=header + arr.tobytes())


class _DocPick(ttk.Frame):
    def __init__(self, parent, theme, title, on_change):
        super().__init__(parent)
        self.on_change = on_change
        ttk.Label(self, text=title, style="Title.TLabel").pack(anchor="w")
        self.has_file = lambda: bool(self.var.get().strip())
        self.var = tk.StringVar()
        r = ttk.Frame(self)
        r.pack(fill="x")
        e = ttk.Entry(r, textvariable=self.var)
        e.pack(side="left", fill="x", expand=True)
        dnd.enable_drop(e, self._drop, exts=(".pdf",))
        ttk.Button(r, text="…", width=3, command=self.browse).pack(side="left", padx=2)
        ttk.Label(r, text="page").pack(side="left", padx=(6, 2))
        self.page = tk.StringVar(value="1")
        sp = ttk.Spinbox(r, from_=1, to=9999, textvariable=self.page, width=5)
        sp.pack(side="left")
        DropZone(self, theme, f"Drop {title} here", self._drop, exts=(".pdf",),
                 browse=self.browse, height=36).pack(fill="x", pady=(3, 0))

    def _drop(self, paths):
        if paths:
            self.var.set(paths[0])
            try:
                n = merge.pdf_page_count(paths[0])
            except Exception:   # noqa: BLE001
                n = 1
            self.page.set("1")
            self.on_change(n)

    def browse(self):
        p = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf")])
        if p:
            self._drop([p])

    @property
    def path(self):
        return self.var.get().strip()

    @property
    def page_no(self):
        try:
            return max(1, int(self.page.get()))
        except ValueError:
            return 1


class CompareTab(ttk.Frame):
    def __init__(self, parent, theme, status):
        super().__init__(parent, padding=10)
        self.theme = theme
        self.status = status
        self.align_result = None
        self._photo = None
        self._out = None
        self.on_compared = None      # app hook: called with output path
        self.drop_hint = "Compare — first PDF fills Base, second fills Overlay"

        top = ttk.Frame(self)
        top.pack(fill="x")
        self.a = _DocPick(top, theme, "Base (old revision)", lambda n: None)
        self.a.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.b = _DocPick(top, theme, "Overlay (new revision)", lambda n: None)
        self.b.pack(side="left", fill="x", expand=True)

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=6)
        self.auto_btn = ttk.Button(bar, text="⚡ Auto Align", style="Accent.TButton",
                                   command=self.auto_align)
        self.auto_btn.pack(side="left")
        Tooltip(self.auto_btn, "Registers the two pages automatically "
                               "(translation + small rotation) so you compare "
                               "content, not paper shift.", theme)
        self.rot_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="search rotation", variable=self.rot_var
                        ).pack(side="left", padx=6)
        self.align_lbl = ttk.Label(bar, text="not aligned", style="Muted.TLabel")
        self.align_lbl.pack(side="left", padx=10)
        ttk.Label(bar, text="nudge:").pack(side="left", padx=(14, 2))
        for txt, dx, dy in (("←", -1, 0), ("→", 1, 0), ("↑", 0, -1), ("↓", 0, 1)):
            ttk.Button(bar, text=txt, width=3, style="Tool.TButton",
                       command=lambda dx=dx, dy=dy: self.nudge(dx, dy)
                       ).pack(side="left")
        ttk.Button(bar, text="reset", style="Tool.TButton",
                   command=self.reset_align).pack(side="left", padx=4)
        self.preview_btn = ttk.Button(bar, text="Preview", command=self.preview)
        self.preview_btn.pack(side="right")
        self.pdf_btn = ttk.Button(bar, text="Save overlay PDF…",
                                  command=self.save_pdf)
        self.pdf_btn.pack(side="right", padx=6)
        self.vdiff_btn = ttk.Button(bar, text="Vector diff PDF…",
                                    command=self.save_vector_diff)
        self.vdiff_btn.pack(side="right")
        Tooltip(self.vdiff_btn, "The Slipsheet: a vector redline — removed "
                                "linework dashed red, added solid blue, "
                                "change regions boxed and numbered. Needs "
                                "vector (CAD-exported) pages.", theme)
        self.open_btn = ttk.Button(bar, text="Open result", state="disabled",
                                   command=lambda: self._out and open_path(self._out))
        self.open_btn.pack(side="right")

        legend = ttk.Frame(self)
        legend.pack(fill="x")
        for color, txt in (("#c81e1e", "base only (removed)"),
                           ("#1e50c8", "overlay only (added)"),
                           ("#282828", "unchanged")):
            sw = tk.Canvas(legend, width=14, height=10, highlightthickness=0)
            sw.create_rectangle(0, 0, 14, 10, fill=color, outline="")
            sw.pack(side="left", padx=(8, 2))
            ttk.Label(legend, text=txt, style="Muted.TLabel").pack(side="left")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, pady=4)
        self.canvas = tk.Canvas(body, highlightthickness=0)
        hsb = ttk.Scrollbar(body, orient="horizontal", command=self.canvas.xview)
        vsb = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=hsb.set, yscrollcommand=vsb.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        theme.register(lambda c: theme.style_canvas(self.canvas))
        self.canvas.bind("<ButtonPress-1>",
                         lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind("<B1-Motion>",
                         lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))

        self.log = LogConsole(self, theme, height=4)
        self.log.pack(fill="x", pady=(4, 0))

    def commands(self):
        return [
            ("Auto Align documents", "Compare", self.auto_align),
            ("Preview overlay", "Compare", self.preview),
            ("Save overlay PDF", "Compare", self.save_pdf),
        ]

    def handle_drop(self, paths):
        """First dropped PDF fills Base, the next fills Overlay."""
        for p in paths:
            if not p.lower().endswith(".pdf"):
                continue
            if not self.a.has_file():
                self.a._drop([p])
            elif not self.b.has_file():
                self.b._drop([p])
            else:
                self.b._drop([p])   # both full: newest revision replaces overlay

    # -------------------------------------------------------------- actions
    def _ready(self):
        if not self.a.path or not self.b.path:
            messagebox.showwarning("Compare", "Pick both PDFs first.")
            return False
        return True

    def _show_align(self):
        r = self.align_result
        if r is None:
            self.align_lbl.configure(text="not aligned")
        else:
            self.align_lbl.configure(
                text=f"dx {r.dx:+.1f}pt  dy {r.dy:+.1f}pt  rot {r.rotation:+.2f}°  "
                     f"confidence {r.score:.2f}")

    def auto_align(self):
        if not self._ready():
            return
        self.auto_btn.configure(state="disabled")
        self.status.set("Auto-aligning…")
        # snapshot tk-backed values on the UI thread; work() must be tk-free
        a_path, a_pg = self.a.path, self.a.page_no
        b_path, b_pg = self.b.path, self.b.page_no
        rot = self.rot_var.get()

        def work():
            return align.auto_align(a_path, b_path, base_page=a_pg,
                                    overlay_page=b_pg, try_rotation=rot)

        def done(res, err):
            self.auto_btn.configure(state="normal")
            if err:
                self.log.say(f"!! auto-align failed: {err}")
                self.status.set("Auto Align failed — see log", "err")
                return
            self.align_result = res
            self._show_align()
            self.status.set("Aligned — preview or save the overlay", "ok")
            self.preview()

        run_bg(self, work, done)

    def nudge(self, dx, dy):
        if self.align_result is None:
            self.align_result = align.AlignResult()
        self.align_result.dx += dx
        self.align_result.dy += dy
        self._show_align()
        self.preview()

    def reset_align(self):
        self.align_result = None
        self._show_align()

    def preview(self):
        if not self._ready():
            return
        a_path, a_pg = self.a.path, self.a.page_no
        b_path, b_pg = self.b.path, self.b.page_no
        r = self.align_result
        self.status.set("Rendering preview…")

        def work():
            return align.comparison_image(a_path, b_path, base_page=a_pg,
                                          overlay_page=b_pg, align=r, dpi=110)

        def done(img, err):
            if err:
                self.log.say(f"!! preview failed: {err}")
                self.status.set("Preview failed — see log", "err")
                return
            self._photo = np_to_photo(img)
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, image=self._photo, anchor="nw")
            self.canvas.configure(scrollregion=(0, 0, img.shape[1], img.shape[0]))
            self.status.set("Preview ready — drag to pan", "ok")

        run_bg(self, work, done)

    def save_vector_diff(self):
        """The Slipsheet: vector redline PDF (drawdiff.redline_pdf)."""
        if not self._ready():
            return
        default = os.path.splitext(self.a.path)[0] + "_redline.pdf"
        out = filedialog.asksaveasfilename(defaultextension=".pdf",
                                           initialfile=os.path.basename(
                                               default),
                                           filetypes=[("PDF", "*.pdf")])
        if not out:
            return
        a_path, a_pg = self.a.path, self.a.page_no
        b_path, b_pg = self.b.path, self.b.page_no
        r = self.align_result if self.align_result is not None else "auto"

        def work():
            from .. import drawdiff
            return drawdiff.redline_pdf(a_path, b_path, out, base_page=a_pg,
                                        rev_page=b_pg, align=r,
                                        log=self.log.say), out

        def done(res, err):
            if err:
                self.log.say(f"!! vector diff failed: {err}")
                self.status.set("Vector diff failed — see log", "err")
                return
            rep, path = res
            t = rep["totals"]
            self._out = path
            self.open_btn.configure(state="normal")
            self.log.say(
                f"wrote {path} — {len(rep['regions'])} change region(s), "
                f"{t['added']} added / {t['removed']} removed piece(s)")
            for wtxt in rep["warnings"]:
                self.log.say(f"  !! {wtxt}")
            self.status.set(
                f"Vector diff: {len(rep['regions'])} change region(s)",
                "ok")

        run_bg(self, work, done)

    def save_pdf(self):
        if not self._ready():
            return
        default = os.path.splitext(self.a.path)[0] + "_compare.pdf"
        out = filedialog.asksaveasfilename(defaultextension=".pdf",
                                           initialfile=os.path.basename(default),
                                           filetypes=[("PDF", "*.pdf")])
        if not out:
            return
        a_path, a_pg = self.a.path, self.a.page_no
        b_path, b_pg = self.b.path, self.b.page_no
        r = self.align_result

        def work():
            align.make_comparison_pdf(a_path, b_path, out, base_page=a_pg,
                                      overlay_page=b_pg, align=r,
                                      log=self.log.say)
            return out

        def done(res, err):
            if err:
                self.log.say(f"!! overlay PDF failed: {err}")
                self.status.set("Overlay PDF failed — see log", "err")
                return
            self._out = res
            self.open_btn.configure(state="normal")
            self.status.set("Overlay PDF written", "ok")
            self.log.say(f"wrote {res}")
            if self.on_compared:
                self.on_compared(res)

        run_bg(self, work, done)
