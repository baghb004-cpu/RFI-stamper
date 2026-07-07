"""3D BIM-lite wireframe viewer on a plain tk canvas.

Painter's-algorithm renderer: segments and sheet planes are depth-sorted far
to near and redrawn as canvas items.  Idle cost is zero — the scene redraws
only on interaction, resize, or theme change; there is no continuous render
loop.  Adaptive detail keeps orbiting smooth: each frame's draw time is
measured and, past ~28 ms, the segment set is decimated for the next frame;
full detail is restored when frames are fast again / interaction ends.

Interactions: left-drag orbit, middle-drag (or shift+left) pan, wheel zoom
toward the cursor, double-click fit.  Clicking a sheet plane or its label
chip calls on_open_sheet(page_no, label).
"""
from __future__ import annotations

import math
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np

from .. import bim
from .theme import FAMILY

SLOW_FRAME = 0.028      # above this, drop detail
FAST_FRAME = 0.012      # below this, climb back toward full detail
MIN_LOD = 0.2
HINT = "drag orbit · middle pan · wheel zoom · click a sheet chip to open it"


class Bim3DViewer(ttk.Frame):
    def __init__(self, parent, theme, on_open_sheet=None):
        super().__init__(parent)
        self.theme = theme
        self.on_open_sheet = on_open_sheet
        self.model = None
        self.cam = bim.Camera()
        self._lod = 1.0             # 1.0 = every segment; <1 = decimated
        self._press = None          # (x, y, yaw0, pitch0) while orbiting
        self._pan0 = None           # (x, y, target0) while panning
        self._moved = 0.0
        self._cfg_after = None
        self._restore_after = None

        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Button(bar, text="Demo model", style="Tool.TButton",
                   command=self.load_demo).pack(side="left", padx=(0, 2))
        ttk.Button(bar, text="Open OBJ…", style="Tool.TButton",
                   command=self.open_obj).pack(side="left", padx=2)
        self.proj_btn = ttk.Button(bar, text="Persp", width=6,
                                   style="Tool.TButton",
                                   command=self.toggle_ortho)
        self.proj_btn.pack(side="left", padx=2)
        ttk.Button(bar, text="Fit", style="Tool.TButton",
                   command=self.fit).pack(side="left", padx=2)
        self.legend = ttk.Frame(bar)
        self.legend.pack(side="left", padx=12)
        self.hint = ttk.Label(bar, text=HINT, style="Muted.TLabel")
        self.hint.pack(side="right", padx=4)

        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        cv = self.canvas
        cv.bind("<Enter>", lambda e: cv.focus_set())
        cv.bind("<ButtonPress-1>", self._on_press)
        cv.bind("<B1-Motion>", self._on_drag)
        cv.bind("<ButtonRelease-1>", self._on_release)
        cv.bind("<Double-Button-1>", lambda e: self.fit())
        cv.bind("<ButtonPress-2>", self._on_pan_start)
        cv.bind("<B2-Motion>", self._on_pan)
        cv.bind("<ButtonRelease-2>", lambda e: self._end_interaction())
        cv.bind("<MouseWheel>", self._on_wheel)
        cv.bind("<Button-4>", lambda e: self._zoom(1 / 1.15, e.x, e.y))
        cv.bind("<Button-5>", lambda e: self._zoom(1.15, e.x, e.y))
        cv.bind("<Configure>", self._on_configure)

        theme.register(self._on_theme)

    # ------------------------------------------------------------ public ---
    def set_model(self, model) -> None:
        self.model = model
        self._lod = 1.0
        self._build_legend()
        self._fit_instant()
        self._render()

    def add_sheet(self, label: str, page_no: int, elevation: float) -> None:
        if self.model is None:
            self.model = bim.Model()
        bim.add_sheet_plane(self.model, label, page_no, elevation)
        self._render()

    def load_demo(self):
        self.set_model(bim.demo_building())

    def open_obj(self):
        path = filedialog.askopenfilename(
            parent=self, title="Open OBJ",
            filetypes=[("OBJ model", "*.obj"), ("All files", "*.*")])
        if not path:
            return
        try:
            self.set_model(bim.load_obj(path))
        except (ValueError, OSError) as e:
            messagebox.showerror("Open OBJ", str(e), parent=self)

    def toggle_ortho(self):
        self.cam.ortho = not self.cam.ortho
        self.proj_btn.configure(text="Ortho" if self.cam.ortho else "Persp")
        self._render()

    def fit(self):
        """Frame the model; short eased yaw/dist transition when fx exists."""
        if self.model is None:
            return
        yaw1, pitch1, dist1, target1 = self._fit_params()
        try:
            from . import fx                    # optional; fall back silently
            cam = self.cam
            y0, p0, d0 = cam.yaw, cam.pitch, cam.dist
            t0 = np.asarray(cam.target, dtype=float)
            t1 = np.asarray(target1, dtype=float)

            def step(t):
                cam.yaw = y0 + (yaw1 - y0) * t
                cam.pitch = p0 + (pitch1 - p0) * t
                cam.dist = d0 + (dist1 - d0) * t
                cam.target = tuple(t0 + (t1 - t0) * t)
                self._render()
            fx.animate(self, step, ms=260)
        except Exception:                       # noqa: BLE001 -- no fx module
            self._fit_instant()
            self._render()

    # ------------------------------------------------------------ camera ---
    def _fit_params(self):
        (mnx, mny, mnz), (mxx, mxy, mxz) = self.model.bounds()
        target = ((mnx + mxx) / 2.0, (mny + mxy) / 2.0, (mnz + mxz) / 2.0)
        radius = max(1e-3, 0.5 * math.dist((mnx, mny, mnz), (mxx, mxy, mxz)))
        half = math.tan(math.radians(max(self.cam.fov, 1.0)) * 0.5)
        dist = radius / half * 1.35
        d = bim.Camera()                        # dataclass defaults
        return d.yaw, d.pitch, dist, target

    def _fit_instant(self):
        yaw, pitch, dist, target = self._fit_params()
        self.cam.yaw, self.cam.pitch = yaw, pitch
        self.cam.dist, self.cam.target = dist, target

    def _axes(self):
        """(right, up) world unit vectors of the view plane (matches bim)."""
        yr = math.radians(self.cam.yaw)
        pr = math.radians(self.cam.pitch)
        right = np.array([math.cos(yr), math.sin(yr), 0.0])
        fwd = np.array([-math.sin(yr) * math.cos(pr),
                        math.cos(yr) * math.cos(pr), -math.sin(pr)])
        return right, np.cross(right, fwd)

    def _world_per_px(self):
        h = max(self.canvas.winfo_height(), 2)
        half = math.tan(math.radians(max(self.cam.fov, 1.0)) * 0.5)
        return 2.0 * self.cam.dist * half / h

    # ------------------------------------------------------- interactions ---
    def _on_press(self, e):
        self._moved = 0.0
        if e.state & 0x1:                       # shift+left -> pan
            self._pan0 = (e.x, e.y, np.asarray(self.cam.target, dtype=float))
            self._press = None
        else:
            self._press = (e.x, e.y, self.cam.yaw, self.cam.pitch)
            self._pan0 = None

    def _on_drag(self, e):
        if self._pan0 is not None:
            self._pan_to(e)
            return
        if self._press is None:
            return
        x0, y0, yaw0, pitch0 = self._press
        self._moved = max(self._moved, abs(e.x - x0) + abs(e.y - y0))
        self.cam.yaw = yaw0 + (e.x - x0) * 0.4
        self.cam.pitch = max(-89.0, min(89.0, pitch0 + (e.y - y0) * 0.4))
        self._render()

    def _on_release(self, e):
        if self._press is not None and self._moved < 3:
            self._click(e.x, e.y)
        self._press = None
        self._pan0 = None
        self._end_interaction()

    def _on_pan_start(self, e):
        self._pan0 = (e.x, e.y, np.asarray(self.cam.target, dtype=float))

    def _on_pan(self, e):
        self._pan_to(e)

    def _pan_to(self, e):
        if self._pan0 is None:
            return
        x0, y0, t0 = self._pan0
        self._moved = max(self._moved, abs(e.x - x0) + abs(e.y - y0))
        wpp = self._world_per_px()
        right, up = self._axes()
        t = t0 - right * (e.x - x0) * wpp + up * (e.y - y0) * wpp
        self.cam.target = tuple(float(v) for v in t)
        self._render()

    def _on_wheel(self, e):
        self._zoom(1 / 1.15 if e.delta > 0 else 1.15, e.x, e.y)
        return "break"

    def _zoom(self, factor, x, y):
        """dist *= factor, drifting the target toward the cursor point."""
        cv = self.canvas
        w = max(cv.winfo_width(), 2)
        h = max(cv.winfo_height(), 2)
        wpp = self._world_per_px()
        right, up = self._axes()
        off = right * (x - w / 2.0) * wpp - up * (y - h / 2.0) * wpp
        t = np.asarray(self.cam.target, dtype=float) + off * (1.0 - factor)
        self.cam.target = tuple(float(v) for v in t)
        self.cam.dist = max(0.05, min(1e5, self.cam.dist * factor))
        self._render()
        self._sched_restore()

    def _click(self, x, y):
        """Hit-test sheet chips/planes at a click point (topmost first)."""
        if self.model is None or self.on_open_sheet is None:
            return
        cv = self.canvas
        for item in reversed(cv.find_overlapping(x - 3, y - 3, x + 3, y + 3)):
            for tag in cv.gettags(item):
                if tag.startswith("sheet:"):
                    idx = int(tag.split(":", 1)[1])
                    if 0 <= idx < len(self.model.planes):
                        pl = self.model.planes[idx]
                        self.on_open_sheet(pl.page_no, pl.label)
                    return

    # --------------------------------------------------- adaptive detail ---
    def _end_interaction(self):
        if self._lod < 1.0:
            self._lod = 1.0
            self._render()

    def _sched_restore(self):
        if self._restore_after:
            self.after_cancel(self._restore_after)
        self._restore_after = self.after(240, self._end_interaction)

    def _on_configure(self, _e):
        if self._cfg_after:
            self.after_cancel(self._cfg_after)
        self._cfg_after = self.after(50, self._render)

    def _on_theme(self, colors):
        if not self.canvas.winfo_exists():
            return
        self.canvas.configure(bg=colors["canvas_bg"])
        self._build_legend()
        self._render()

    # ------------------------------------------------------------ legend ---
    def _build_legend(self):
        for child in self.legend.winfo_children():
            child.destroy()
        if self.model is None:
            return
        c = self.theme.colors
        for name, color in self.model.systems:
            tk.Label(self.legend, text=f"■ {name}", fg=color, bg=c["bg"],
                     font=(FAMILY, 9)).pack(side="left", padx=(0, 8))

    # ------------------------------------------------------------ render ---
    def _render(self):
        cv = self.canvas
        if not cv.winfo_exists():
            return
        t0 = time.perf_counter()
        c = self.theme.colors
        w = cv.winfo_width()
        h = cv.winfo_height()
        if w < 4 or h < 4:                      # never-mapped canvas
            w, h = 800, 600
        cv.delete("all")
        m = self.model
        if m is None or (not m.segments and not m.planes):
            self._draw_empty(w, h, c)
            return
        cam = self.cam

        self._draw_grid(w, h, c)

        # decimate when the last frame was slow
        segs = m.segments
        if self._lod < 0.999 and len(segs) > 60:
            step = max(1, round(1.0 / self._lod))
            segs = segs[::step]

        # one projection call for every endpoint + plane corner
        pts = [p for s in segs for p in (s.a, s.b)]
        for pl in m.planes:
            pts.extend(pl.corners)
        scr = bim.project_points(pts, cam, w, h)

        items = []                              # (depth, draw_fn) painter list
        for i, s in enumerate(segs):
            a, b = scr[2 * i], scr[2 * i + 1]
            if a[2] <= 1e-6 or b[2] <= 1e-6:    # behind the camera -> cull
                continue
            depth = (a[2] + b[2]) / 2.0
            prox = max(0.6, min(2.4, cam.dist / max(depth, 1e-6)))
            width = max(1.0, s.width * prox)
            items.append((depth, ("line", (a[0], a[1], b[0], b[1]),
                                  s.color, width)))
        chips = []
        base = 2 * len(segs)
        for j, pl in enumerate(m.planes):
            quad = scr[base + 4 * j: base + 4 * j + 4]
            if np.any(quad[:, 2] <= 1e-6):
                continue
            depth = float(quad[:, 2].mean())
            coords = [v for p in quad for v in (p[0], p[1])]
            items.append((depth, ("plane", coords, pl.color, j)))
            cx = float(quad[:, 0].mean())
            cy = float(quad[:, 1].mean())
            chips.append((cx, cy, j, pl))

        items.sort(key=lambda it: -it[0])       # far first
        for _, it in items:
            if it[0] == "line":
                _, xy, color, width = it
                cv.create_line(*xy, fill=color, width=width, tags="seg")
            else:
                _, coords, color, j = it
                cv.create_polygon(*coords, fill=color, stipple="gray25",
                                  outline=color, width=1.4,
                                  tags=("plane", f"sheet:{j}"))

        # label chips on top of everything
        for cx, cy, j, pl in chips:
            text = f"{pl.label}  ·  p.{pl.page_no}" if pl.label else \
                f"p.{pl.page_no}"
            tid = cv.create_text(cx, cy, text=text, fill="#ffffff",
                                 font=(FAMILY, 9, "bold"),
                                 tags=("chip", f"sheet:{j}"))
            x1, y1, x2, y2 = cv.bbox(tid)
            rid = cv.create_rectangle(x1 - 7, y1 - 3, x2 + 7, y2 + 3,
                                      fill=pl.color, outline="",
                                      tags=("chip", f"sheet:{j}"))
            cv.tag_raise(tid, rid)

        # adapt detail from measured draw time (applies to the next frame)
        dt = time.perf_counter() - t0
        if dt > SLOW_FRAME:
            self._lod = max(MIN_LOD, self._lod * 0.6)
        elif dt < FAST_FRAME and self._lod < 1.0:
            self._lod = min(1.0, self._lod * 1.6)

    def _draw_grid(self, w, h, c):
        """Fine ground grid under everything (not depth-sorted with model)."""
        (mnx, mny, mnz), (mxx, mxy, _) = self.model.bounds()
        span = max(mxx - mnx, mxy - mny, 1.0)
        step = _nice_step(span / 8.0)
        cx, cy = (mnx + mxx) / 2.0, (mny + mxy) / 2.0
        half = span * 0.75
        gx0 = math.floor((cx - half) / step) * step
        gx1 = math.ceil((cx + half) / step) * step
        gy0 = math.floor((cy - half) / step) * step
        gy1 = math.ceil((cy + half) / step) * step
        z = min(mnz, 0.0)
        pts = []
        x = gx0
        while x <= gx1 + 1e-9:
            pts.append((x, gy0, z))
            pts.append((x, gy1, z))
            x += step
        y = gy0
        while y <= gy1 + 1e-9:
            pts.append((gx0, y, z))
            pts.append((gx1, y, z))
            y += step
        scr = bim.project_points(pts, self.cam, w, h)
        for i in range(0, len(pts), 2):
            a, b = scr[i], scr[i + 1]
            if a[2] <= 1e-6 or b[2] <= 1e-6:
                continue
            self.canvas.create_line(a[0], a[1], b[0], b[1], fill=c["muted"],
                                    width=1, stipple="gray50", tags="grid")

    def _draw_empty(self, w, h, c):
        """Friendly empty state: little axonometric cube + a nudge."""
        cv = self.canvas
        x, y = w / 2.0, h / 2.0
        s = 34
        pts = [(x - s, y - 8), (x, y - 30), (x + s, y - 8), (x, y + 14),
               (x - s, y - 52), (x, y - 74), (x + s, y - 52)]
        for a, b in ((0, 1), (1, 2), (2, 3), (3, 0), (0, 4), (1, 5), (2, 6),
                     (4, 5), (5, 6)):
            cv.create_line(*pts[a], *pts[b], fill=c["muted"], width=1.6)
        cv.create_text(x, y + 52, text="No model loaded", fill=c["muted"],
                       font=(FAMILY, 15, "bold"))
        cv.create_text(x, y + 76, fill=c["muted"], font=(FAMILY, 10),
                       text="Demo model builds a sample — or Open OBJ…")


def _nice_step(raw: float) -> float:
    """Round a raw spacing up to a tidy 1/2/5 x 10^k value."""
    raw = max(raw, 1e-6)
    mag = 10.0 ** math.floor(math.log10(raw))
    for mult in (1.0, 2.0, 5.0, 10.0):
        if raw <= mult * mag:
            return mult * mag
    return 10.0 * mag
