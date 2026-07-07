"""Canvas PDF viewer: zoom at cursor, pan, page nav, dark-invert rendering.

Coordinates: markups and tools work in *viewer page points* — origin top-left,
y down, matching fitz's rotated page.rect — and the canvas shows the page at
`scale` px/pt at canvas origin (0,0).  page<->canvas transforms are exact.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import fitz

MAX_PIXELS = 30_000_000     # render cap; protects RAM on Arch-E1 at high zoom


class PDFViewer(ttk.Frame):
    def __init__(self, parent, theme, on_page_changed=None, on_render=None):
        super().__init__(parent)
        self.theme = theme
        self.on_page_changed = on_page_changed
        self.on_render = on_render
        self.doc = None
        self.path = ""
        self.page_no = 1
        self.zoom = 1.0
        self.invert = False
        self._photo = None          # keep a reference or tk garbage-collects it

        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Button(bar, text="◀", width=3, style="Tool.TButton",
                   command=self.prev_page).pack(side="left", padx=(0, 2))
        self.page_var = tk.StringVar(value="–")
        self.page_entry = ttk.Entry(bar, textvariable=self.page_var, width=5,
                                    justify="center")
        self.page_entry.pack(side="left")
        self.page_entry.bind("<Return>", self._goto_entry)
        self.count_lbl = ttk.Label(bar, text="/ 0", style="Muted.TLabel")
        self.count_lbl.pack(side="left", padx=(3, 6))
        ttk.Button(bar, text="▶", width=3, style="Tool.TButton",
                   command=self.next_page).pack(side="left", padx=(0, 10))
        ttk.Button(bar, text="−", width=3, style="Tool.TButton",
                   command=lambda: self.zoom_by(1 / 1.25)).pack(side="left")
        ttk.Button(bar, text="+", width=3, style="Tool.TButton",
                   command=lambda: self.zoom_by(1.25)).pack(side="left", padx=2)
        ttk.Button(bar, text="Fit width", style="Tool.TButton",
                   command=self.fit_width).pack(side="left", padx=2)
        ttk.Button(bar, text="Fit page", style="Tool.TButton",
                   command=self.fit_page).pack(side="left", padx=2)
        self.zoom_lbl = ttk.Label(bar, text="", style="Muted.TLabel")
        self.zoom_lbl.pack(side="left", padx=8)
        self.sheet_lbl = ttk.Label(bar, text="", style="Muted.TLabel")
        self.sheet_lbl.pack(side="right", padx=4)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
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

        cv = self.canvas
        cv.bind("<Control-MouseWheel>", self._wheel_zoom)
        cv.bind("<MouseWheel>", self._wheel_scroll)
        cv.bind("<Shift-MouseWheel>", self._wheel_scroll_h)
        for btn, fn in (("<Button-4>", 1), ("<Button-5>", -1)):     # X11 wheel
            cv.bind(btn, lambda e, d=fn: self._x11_wheel(e, d))
        cv.bind("<ButtonPress-2>", lambda e: cv.scan_mark(e.x, e.y))
        cv.bind("<B2-Motion>", lambda e: cv.scan_dragto(e.x, e.y, gain=1))
        cv.bind("<Prior>", lambda e: self.prev_page())
        cv.bind("<Next>", lambda e: self.next_page())

    # ------------------------------------------------------------- document
    def open(self, path: str):
        self.close()
        self.doc = fitz.open(path)
        self.path = path
        self.page_no = 1
        self.after(50, self.fit_width)
        self._update_bar()

    def close(self):
        if self.doc is not None:
            try:
                self.doc.close()
            except Exception:   # noqa: BLE001
                pass
        self.doc = None
        self.path = ""
        self.canvas.delete("all")
        self._photo = None
        self._update_bar()

    def reload(self):
        """Re-open the same file (after an external write) keeping position."""
        if not self.path:
            return
        path, page = self.path, self.page_no
        self.open(path)
        self.goto(page)

    @property
    def page_count(self) -> int:
        return len(self.doc) if self.doc is not None else 0

    @property
    def page(self):
        return self.doc[self.page_no - 1] if self.doc is not None else None

    @property
    def scale(self) -> float:
        return self.zoom      # px per pt (zoom 1.0 -> 72 dpi)

    # ------------------------------------------------------------ navigation
    def goto(self, n: int):
        if not self.doc:
            return
        n = max(1, min(self.page_count, int(n)))
        changed = n != self.page_no
        self.page_no = n
        self.render()
        if changed and self.on_page_changed:
            self.on_page_changed(n)

    def next_page(self):
        self.goto(self.page_no + 1)

    def prev_page(self):
        self.goto(self.page_no - 1)

    def _goto_entry(self, _e):
        try:
            self.goto(int(self.page_var.get().strip()))
        except ValueError:
            self._update_bar()

    # ----------------------------------------------------------------- zoom
    def zoom_by(self, factor: float, focus=None):
        self.set_zoom(self.zoom * factor, focus=focus)

    def set_zoom(self, z: float, focus=None):
        if not self.doc:
            return
        r = self.page.rect
        z = max(0.05, min(z, (MAX_PIXELS / max(r.width * r.height, 1)) ** 0.5))
        if focus is None:
            w = self.canvas.winfo_width() or 1
            h = self.canvas.winfo_height() or 1
            focus = (w / 2, h / 2)
        px, py = self.canvas_to_page(self.canvas.canvasx(focus[0]),
                                     self.canvas.canvasy(focus[1]))
        self.zoom = z
        self.render()
        # keep the focused page point under the cursor
        cw = max(r.width * z, 1)
        ch = max(r.height * z, 1)
        self.canvas.xview_moveto(max(0.0, (px * z - focus[0]) / cw))
        self.canvas.yview_moveto(max(0.0, (py * z - focus[1]) / ch))

    def fit_width(self):
        if not self.doc:
            return
        w = self.canvas.winfo_width() or 800
        self.zoom = max(0.05, (w - 4) / self.page.rect.width)
        self.render()

    def fit_page(self):
        if not self.doc:
            return
        w = self.canvas.winfo_width() or 800
        h = self.canvas.winfo_height() or 600
        r = self.page.rect
        self.zoom = max(0.05, min((w - 4) / r.width, (h - 4) / r.height))
        self.render()

    def set_invert(self, on: bool):
        self.invert = on
        self.render()

    # --------------------------------------------------------------- render
    def render(self):
        if not self.doc:
            return
        pix = self.page.get_pixmap(matrix=fitz.Matrix(self.zoom, self.zoom),
                                   alpha=False)
        if self.invert:
            pix.invert_irect(pix.irect)
        self._photo = tk.PhotoImage(data=pix.tobytes("ppm"))
        cv = self.canvas
        cv.delete("all")
        cv.create_image(0, 0, image=self._photo, anchor="nw", tags=("pageimg",))
        cv.configure(scrollregion=(0, 0, pix.width, pix.height))
        self._update_bar()
        if self.on_render:
            self.on_render(self.page_no)

    def _update_bar(self):
        self.page_var.set(str(self.page_no) if self.doc else "–")
        self.count_lbl.configure(text=f"/ {self.page_count}")
        self.zoom_lbl.configure(text=f"{self.zoom * 100:.0f}%" if self.doc else "")

    # ---------------------------------------------------------- coordinates
    def page_to_canvas(self, x: float, y: float):
        return x * self.scale, y * self.scale

    def canvas_to_page(self, cx: float, cy: float):
        return cx / self.scale, cy / self.scale

    def event_page_xy(self, event):
        """Page pt coordinates of a mouse event on the canvas."""
        return self.canvas_to_page(self.canvas.canvasx(event.x),
                                   self.canvas.canvasy(event.y))

    # -------------------------------------------------------------- wheels
    def _wheel_zoom(self, e):
        self.zoom_by(1.15 if e.delta > 0 else 1 / 1.15, focus=(e.x, e.y))
        return "break"

    def _wheel_scroll(self, e):
        self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
        return "break"

    def _wheel_scroll_h(self, e):
        self.canvas.xview_scroll(-1 if e.delta > 0 else 1, "units")
        return "break"

    def _x11_wheel(self, e, direction):
        if e.state & 0x4:          # Control held -> zoom
            self.zoom_by(1.15 if direction > 0 else 1 / 1.15, focus=(e.x, e.y))
        elif e.state & 0x1:        # Shift -> horizontal
            self.canvas.xview_scroll(-direction, "units")
        else:
            self.canvas.yview_scroll(-direction, "units")
        return "break"
