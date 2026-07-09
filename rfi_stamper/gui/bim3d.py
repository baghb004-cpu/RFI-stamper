"""3D BIM-lite viewer on a plain tk canvas — wireframe plus Phase D uplift.

Painter's-algorithm renderer: segments and sheet planes are depth-sorted far
to near and redrawn as canvas items.  Idle cost is zero — the scene redraws
only on interaction, resize, or theme change; there is no continuous render
loop.  Adaptive detail keeps orbiting smooth: each frame's draw time is
measured and, past ~28 ms, the segment set is decimated for the next frame;
full detail is restored when frames are fast again / interaction ends.

Phase D (all pure canvas, no GPU):

* Shaded mode — when the model carries ``bim.Face`` quads they are drawn as
  flat-shaded polygons, depth-sorted among themselves (painter's algorithm
  by face-centroid camera distance) and laid down BENEATH the wireframe
  lines.  Flat shade = face color mixed toward the canvas bg by the face
  normal against a fixed light — pure math, no alpha.  Defaults ON at fx
  quality "full", OFF otherwise; always user-toggleable.  hidden_systems
  and the Horizon Slice cull faces exactly like segments: a face is dropped
  when its CENTROID z sits above the cut (centroid test, not polygon
  clipping — cheap, and it matches the segment-midpoint rule).
* Pipe solids — segments carrying ``radius > 0`` (Pipewright sets
  dia_in/24) render as 8-sided prisms in shaded mode, replacing their
  centerline; wireframe mode keeps them as plain lines.
* Slope exaggeration — the "slope ×N" slider scales only the z-delta of
  pipe segments/solids about the model's z-midpoint AT RENDER TIME; the
  model is never mutated and the Horizon Slice culls on true z.
* Walk mode — first-person camera at eye height 5'-6" above z=0 (above the
  Horizon band floor while slicing; a model far from z=0 falls back to its
  own floor).  WASD/arrows step on the xy plane — one fixed step per key
  press, hold-to-walk comes from OS key-repeat, NO free-running loop.
  Mouse drag turns (pitch clamped ±60°); Esc or the button restores the
  saved orbit camera.  A "you are here" chip shows the position in feet.
* Isometric presets — NE/NW/SE/SW buttons tween yaw to 45/135/225/315 and
  pitch to 30 through one fx.animate call (quality "off" snaps).
* Depth cueing — line and face colors mix toward the canvas bg with camera
  distance (bucketed continuous fade, subtle); skipped at quality "off".
* True depth — shaded mode can rasterize the face set through
  ``rfi_stamper.raster`` (numpy z-buffer, per-pixel depth) and blit ONE
  full-canvas image beneath the wireframe overlays, fixing painter's
  interpenetration (a pipe through a wall resolves per pixel) and adding
  silhouette outlines.  Defaults ON at fx quality "full" only (the
  old-hardware promise keeps "reduced"/"off" on the painter), always
  user-toggleable.  Drags render at half resolution + hexagon pipe prisms
  and refine on release; a model past ~6k triangles, or one still slow at
  half-res, falls back to the painter with an honest hint note.  The
  ground grid becomes thin ground-plane quads in this mode, so the
  building correctly occludes it.
* Measure — two picks (vertex > edge > face priority: 12 px vertex snap,
  6 px edge snap, then a ray cast against visible faces; picks THROUGH
  geometry, the wireframe-viewer norm) with a live rubber band, showing a
  dashed tape with the surveying triple SD/HD/VD plus ΔN/ΔE, azimuth and
  pipe slope — all from bim.measure3d (= fieldpro.deltas, so the tape
  agrees with the As-Staked Ledger to the last digit).  TRUE geometry
  even when the slope slider distorts the drawing; a third click clears,
  Esc exits.
* Section box — a 6-plane axis-aligned interrogation box with REAL
  clipping (Liang-Barsky on segments, Sutherland-Hodgman on faces, pipe
  centerlines clipped then re-extruded; sheet planes/pins in-out by
  centroid).  Face-center handles drag their plane (end-on handles dim
  and refuse — no divide-by-zero flings), double-click a handle resets
  that plane, "Section" off restores the full model.  Clipping is
  view-independent and cached, so orbiting stays cheap.  No caps: the
  geometry is open quads/prisms, not solids — the last-moved plane glows
  instead (the honest substitute).  Composes with the Horizon Slice,
  which keeps its documented centroid-cull behavior.

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

from .. import bim, raster
from .theme import FAMILY

SLOW_FRAME = 0.028      # above this, drop detail
FAST_FRAME = 0.012      # below this, climb back toward full detail
MIN_LOD = 0.2
RASTER_MAX_TRIS = 6000  # past this, honest painter fallback
HINT = "drag orbit · middle pan · wheel zoom · click a sheet chip to open it"

_ISO_YAW = {"NE": 45.0, "NW": 135.0, "SE": 225.0, "SW": 315.0}


def _fx_quality() -> str:
    try:
        from . import fx
        return fx.quality()
    except Exception:                       # noqa: BLE001 -- no fx module
        return "full"


def _mix(c1, c2, t: float) -> str:
    return "#%02x%02x%02x" % raster.mix_rgb(c1, c2, t)


class Bim3DViewer(ttk.Frame):
    WALK_EYE_FT = 5.5           # first-person eye height
    WALK_STEP_FT = 2.0          # one key press = one step
    MEASURE_SNAP_PX = 12.0      # vertex snap aperture (classic CAD ~10 px)
    EDGE_SNAP_PX = 6.0          # edge snap aperture

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
        self._slice_frac = 1.0      # Horizon Slice: fraction of height shown
        self.pins = []              # layout pins: (x, y, z, label, color)
        self.hidden_systems = set() # Strata: systems toggled off via legend
        self._walk = None           # {"x","y","z","yaw","pitch"} in walk mode
        self._saved_cam = None      # orbit camera to restore on walk exit
        self.measuring = False      # 3D measure mode on/off
        self._measure_pts = []      # up to 2 picked (true, drawn, kind)
        self._photo = None          # raster blit; MUST stay referenced
        self._raster_slow = False   # sticky painter fallback for this model
        self.section = None         # {"mn": [xyz], "mx": [xyz]} when active
        self._sec_cache = None      # (key, segs, cut_flags, faces)
        self._sec_drag = None       # (k, x0, y0, mn0, mx0, u) mid-drag
        self._sec_last = None       # last-moved plane k, for the glow

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
        # Horizon Slice: live elevation section cut
        self.slice_var = tk.DoubleVar(value=100.0)
        sl = ttk.Scale(bar, from_=8.0, to=100.0, variable=self.slice_var,
                       length=130, command=self._on_slice)
        sl.pack(side="right", padx=(4, 10))
        ttk.Label(bar, text="Horizon Slice", style="Muted.TLabel"
                  ).pack(side="right")

        # Phase D toolbar row: shaded / walk / measure / iso / slope
        bar2 = ttk.Frame(self)
        bar2.pack(fill="x")
        self.shaded_var = tk.BooleanVar(
            master=self, value=_fx_quality() == "full")
        ttk.Checkbutton(bar2, text="Shaded", variable=self.shaded_var,
                        command=self._render).pack(side="left", padx=(0, 6))
        # True depth = raster z-buffer instead of the painter sort; defaults
        # ON at quality "full" only (old hardware stays on the painter)
        self.raster_var = tk.BooleanVar(
            master=self, value=_fx_quality() == "full")
        ttk.Checkbutton(bar2, text="True depth", variable=self.raster_var,
                        command=self._on_raster_toggle
                        ).pack(side="left", padx=(0, 6))
        self.walk_btn = ttk.Button(bar2, text="Walk", style="Tool.TButton",
                                   command=self.toggle_walk)
        self.walk_btn.pack(side="left", padx=2)
        self.measure_btn = ttk.Button(bar2, text="Measure",
                                      style="Tool.TButton",
                                      command=self.toggle_measure)
        self.measure_btn.pack(side="left", padx=2)
        # 6-plane section box: real clipping (Liang-Barsky segments,
        # Sutherland-Hodgman faces), draggable face-center handles
        self.section_var = tk.BooleanVar(master=self, value=False)
        ttk.Checkbutton(bar2, text="Section", variable=self.section_var,
                        command=self._on_section_toggle
                        ).pack(side="left", padx=(6, 2))
        ttk.Separator(bar2, orient="vertical").pack(side="left", fill="y",
                                                    padx=6, pady=2)
        for corner in ("NE", "NW", "SE", "SW"):
            ttk.Button(bar2, text=corner, width=3, style="Tool.TButton",
                       command=lambda cc=corner: self.iso_view(cc)
                       ).pack(side="left", padx=1)
        self.slope_var = tk.DoubleVar(master=self, value=1.0)
        sc = ttk.Scale(bar2, from_=1.0, to=10.0, variable=self.slope_var,
                       length=110, command=self._on_slope)
        sc.pack(side="right", padx=(4, 10))
        self.slope_lbl = ttk.Label(bar2, text="slope ×1",
                                   style="Muted.TLabel")
        self.slope_lbl.pack(side="right")

        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        cv = self.canvas
        cv.bind("<Enter>", lambda e: cv.focus_set())
        cv.bind("<ButtonPress-1>", self._on_press)
        cv.bind("<B1-Motion>", self._on_drag)
        cv.bind("<ButtonRelease-1>", self._on_release)
        cv.bind("<Double-Button-1>", self._on_double)
        cv.bind("<ButtonPress-2>", self._on_pan_start)
        cv.bind("<B2-Motion>", self._on_pan)
        cv.bind("<ButtonRelease-2>", lambda e: self._end_interaction())
        cv.bind("<MouseWheel>", self._on_wheel)
        cv.bind("<Button-4>", lambda e: self._zoom(1 / 1.15, e.x, e.y))
        cv.bind("<Button-5>", lambda e: self._zoom(1.15, e.x, e.y))
        cv.bind("<Configure>", self._on_configure)
        # walk-mode steps: one fixed step per press; holding a key repeats
        # via OS key-repeat — never a free-running after loop (fx house rule)
        for ks in ("w", "a", "s", "d", "W", "A", "S", "D",
                   "Up", "Down", "Left", "Right"):
            cv.bind(f"<KeyPress-{ks}>", self._on_key)
        cv.bind("<Escape>", self._on_escape)

        theme.register(self._on_theme)

    # ------------------------------------------------------------ public ---
    def set_model(self, model) -> None:
        if self._walk is not None:          # new model: quietly leave walk
            self._walk = None
            self._saved_cam = None
            self.walk_btn.configure(text="Walk")
        self._measure_pts = []              # stale world points
        self.model = model
        self._lod = 1.0
        self._raster_slow = False           # new model: re-measure raster
        self.section = None                 # stale box bounds
        self.section_var.set(False)
        self._sec_cache = None
        self._sec_drag = None
        self._sec_last = None
        self._build_legend()
        self._fit_instant()
        self._fly_in()

    def set_pins(self, pins) -> None:
        """Layout pins in world coords: [(x, y, z, label, color), ...]."""
        self.pins = list(pins)
        self._render()

    @property
    def walking(self) -> bool:
        return self._walk is not None

    def _on_slice(self, _v=None):
        self._slice_frac = float(self.slice_var.get()) / 100.0
        self._render()

    def _on_slope(self, _v=None):
        v = round(float(self.slope_var.get()), 1)
        self.slope_lbl.configure(text=f"slope ×{v:g}")
        self._render()

    def _on_raster_toggle(self):
        self._raster_slow = False           # explicit ask: re-measure
        self._render()

    def _set_hint(self, note=None):
        txt = note or HINT
        if self.hint.cget("text") != txt:
            self.hint.configure(text=txt)

    # ------------------------------------------------------- section box ---
    def _sec_full_box(self):
        """Model bounds padded 0.5% of span — the box's reset/initial state
        (the clip's inclusive eps makes the pad belt-and-braces)."""
        (mnx, mny, mnz), (mxx, mxy, mxz) = self.model.bounds()
        pad = [max((hi - lo) * 0.005, 0.01)
               for lo, hi in ((mnx, mxx), (mny, mxy), (mnz, mxz))]
        return ([mnx - pad[0], mny - pad[1], mnz - pad[2]],
                [mxx + pad[0], mxy + pad[1], mxz + pad[2]])

    def _on_section_toggle(self):
        if self.section_var.get() and self.model is not None:
            mn, mx = self._sec_full_box()
            self.section = {"mn": mn, "mx": mx}
        else:
            self.section = None
            self.section_var.set(False)
        self._sec_cache = None
        self._sec_drag = None
        self._sec_last = None
        self._render()

    def _section_geo(self, segs, z_cut):
        """(clipped segments, cut-flag pairs, clipped model faces) for the
        active box.  Clipping is view-independent, so it is CACHED — orbit,
        pan and zoom re-render from the cache (the zero-idle promise);
        invalidated on box drag, model change, legend toggle, slice."""
        sec = self.section
        key = (id(self.model), tuple(sec["mn"]), tuple(sec["mx"]),
               frozenset(self.hidden_systems), z_cut)
        if self._sec_cache is not None and self._sec_cache[0] == key:
            return self._sec_cache[1], self._sec_cache[2], self._sec_cache[3]
        mn, mx = sec["mn"], sec["mx"]
        out_s, out_c = [], []
        for s in segs:
            r = bim.clip_segment_box(s.a, s.b, mn, mx)
            if r is None:
                continue
            (a2, b2), flags = r
            if not flags[0] and not flags[1]:
                out_s.append(s)             # untouched: keep the original
            else:
                out_s.append(bim.Segment(a2, b2, s.color, s.width,
                                         s.system, s.radius))
            out_c.append(flags)
        out_f = []
        for f in getattr(self.model, "faces", ()) or ():
            if f.system in self.hidden_systems:
                continue
            fz = sum(p[2] for p in f.pts) / len(f.pts)
            if z_cut is not None and fz > z_cut:
                continue
            pts = bim.clip_poly_box(f.pts, mn, mx)
            if len(pts) < 3:
                continue
            out_f.append(f if len(pts) == len(f.pts)
                         and all(tuple(float(c) for c in a) == b
                                 for a, b in zip(f.pts, pts))
                         else bim.Face(pts, f.color, f.system))
        self._sec_cache = (key, out_s, out_c, out_f)
        return out_s, out_c, out_f

    def _sec_face_quad(self, k):
        """World corners of box plane k (k = axis*2 + side, side 1 = max)."""
        axis, side = divmod(k, 2)
        mn, mx = self.section["mn"], self.section["mx"]
        v = mx[axis] if side else mn[axis]
        oa, ob = [i for i in range(3) if i != axis]
        quad = []
        for da, db in ((0, 0), (1, 0), (1, 1), (0, 1)):
            p = [0.0, 0.0, 0.0]
            p[axis] = v
            p[oa] = mx[oa] if da else mn[oa]
            p[ob] = mx[ob] if db else mn[ob]
            quad.append(tuple(p))
        return quad

    def _sec_axis_px(self, k, w, h):
        """Screen px per +1 world unit along plane k's axis at its center
        (2-vector), or None when the axis is end-on to the camera (norm:
        an unusable handle is skipped, never divided by)."""
        axis, _side = divmod(k, 2)
        quad = self._sec_face_quad(k)
        c = tuple(sum(p[i] for p in quad) / 4.0 for i in range(3))
        c2 = list(c)
        c2[axis] += 1.0
        scr = bim.project_points([c, tuple(c2)], self.cam, w, h)
        if scr[0, 2] <= 1e-6 or scr[1, 2] <= 1e-6:
            return None
        u = (float(scr[1, 0] - scr[0, 0]), float(scr[1, 1] - scr[0, 1]))
        if u[0] * u[0] + u[1] * u[1] < 4.0:
            return None
        return u

    def _section_handle_at(self, x, y):
        """Plane index k of a box handle under the cursor, or None."""
        cv = self.canvas
        for item in reversed(cv.find_overlapping(x - 4, y - 4,
                                                 x + 4, y + 4)):
            for tag in cv.gettags(item):
                if tag.startswith("boxface:"):
                    return int(tag.split(":", 1)[1])
        return None

    def _draw_section_box(self, w, h, c):
        """Box gizmo: 12 accent edges, 6 face-center drag handles (square,
        tag boxface:k; dimmed when end-on), last-moved plane glows with the
        Horizon-Slice cut-plane idiom."""
        cv = self.canvas
        mn, mx = self.section["mn"], self.section["mx"]
        corners = [(x0, y0, z0) for z0 in (mn[2], mx[2])
                   for y0 in (mn[1], mx[1]) for x0 in (mn[0], mx[0])]
        centers = []
        for k in range(6):
            quad = self._sec_face_quad(k)
            centers.append(tuple(sum(p[i] for p in quad) / 4.0
                                 for i in range(3)))
        scr = bim.project_points(corners + centers, self.cam, w, h)
        edges = ((0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6),
                 (5, 7), (0, 4), (1, 5), (2, 6), (3, 7))
        col = c["accent"]
        for i, j in edges:
            if scr[i, 2] <= 1e-6 or scr[j, 2] <= 1e-6:
                continue
            cv.create_line(scr[i, 0], scr[i, 1], scr[j, 0], scr[j, 1],
                           fill=col, width=1.2, dash=(5, 3),
                           tags="sectionbox")
        if self._sec_last is not None:      # glow the last-moved plane
            quad = bim.project_points(self._sec_face_quad(self._sec_last),
                                      self.cam, w, h)
            if not np.any(quad[:, 2] <= 1e-6):
                coords = [v for p in quad for v in (p[0], p[1])]
                cv.create_polygon(*coords, fill=col, stipple="gray12",
                                  outline=col, width=2.0, tags="sectionbox")
        for k in range(6):
            p = scr[8 + k]
            if p[2] <= 1e-6:
                continue
            usable = self._sec_axis_px(k, w, h) is not None
            fill = col if usable else c["muted"]
            cv.create_rectangle(p[0] - 5, p[1] - 5, p[0] + 5, p[1] + 5,
                                fill=fill, outline=c["panel"], width=1.0,
                                tags=("sectionbox", "boxhandle",
                                      f"boxface:{k}"))

    def _cancel_cam_anim(self):
        """User input owns the camera: stop any fly-in / fit / iso tween so
        it can't keep overwriting yaw/pitch/dist mid-drag."""
        try:
            from . import fx
            fx.cancel(self, "flyin")
            fx.cancel(self, "fit")
            fx.cancel(self, "iso")
        except Exception:                       # noqa: BLE001 -- no fx module
            pass

    def _fly_in(self):
        """Cinematic approach on load: swing in from high and far."""
        try:
            from . import fx
            fx.cancel(self, "fit")              # never fight a running fit
            fx.cancel(self, "iso")
            if fx.quality() == "off":
                self._render()
                return
            cam = self.cam
            yaw1, pitch1, dist1 = cam.yaw, cam.pitch, cam.dist
            cam.yaw, cam.pitch, cam.dist = yaw1 - 70.0, 58.0, dist1 * 2.8

            def step(t):
                cam.yaw = (yaw1 - 70.0) + 70.0 * t
                cam.pitch = 58.0 + (pitch1 - 58.0) * t
                cam.dist = dist1 * (2.8 - 1.8 * t)
                self._render()

            fx.animate(self, "flyin", 0.0, 1.0, 900, step,
                       easing="ease_in_out_cubic")
        except Exception:                       # noqa: BLE001 -- no fx
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
        """Frame the model; short eased yaw/dist transition when fx exists.
        No-op while walking — the walker owns the camera (Esc exits)."""
        if self.model is None or self._walk is not None:
            return
        yaw1, pitch1, dist1, target1 = self._fit_params()
        try:
            from . import fx                    # optional; fall back silently
            fx.cancel(self, "flyin")            # never fight a running fly-in
            fx.cancel(self, "iso")
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

            fx.animate(self, "fit", 0.0, 1.0, 320, step,
                       easing="ease_in_out_cubic")
        except Exception:                       # noqa: BLE001 -- no fx module
            self._fit_instant()
            self._render()

    def iso_view(self, corner: str) -> None:
        """Isometric preset: yaw NE/NW/SE/SW -> 45/135/225/315, pitch 30,
        through ONE fx tween (quality "off" snaps straight to the end by
        fx.animate's contract).  Takes the nearest angular route."""
        yaw1 = _ISO_YAW.get(str(corner).upper())
        if self.model is None or yaw1 is None:
            return
        if self._walk is not None:
            self._walk_exit(restore=True)
        cam = self.cam
        y0, p0 = cam.yaw, cam.pitch
        y1 = y0 + ((yaw1 - y0 + 180.0) % 360.0 - 180.0)

        def step(t):
            cam.yaw = y0 + (y1 - y0) * t
            cam.pitch = p0 + (30.0 - p0) * t
            self._render()

        try:
            from . import fx
            fx.cancel(self, "flyin")
            fx.cancel(self, "fit")
            fx.animate(self, "iso", 0.0, 1.0, 260, step,
                       easing="ease_in_out_cubic")
        except Exception:                       # noqa: BLE001 -- no fx module
            step(1.0)

    # -------------------------------------------------------------- walk ---
    def toggle_walk(self):
        if self._walk is not None:
            self._walk_exit(restore=True)
        else:
            self._walk_enter()

    def _walk_enter(self):
        """First-person camera at eye height above z=0 — above the Horizon
        band floor while slicing; a model living far from z=0 falls back to
        its own floor so the walker never spawns under/over the geometry."""
        if self.model is None:
            return
        self._cancel_cam_anim()
        cam = self.cam
        self._saved_cam = (cam.yaw, cam.pitch, cam.dist, cam.target,
                           cam.ortho)
        (mnx, mny, mnz), (mxx, mxy, mxz) = self.model.bounds()
        base = mnz if self._slice_frac < 0.999 else 0.0
        eye = base + self.WALK_EYE_FT
        if not (mnz - 2.0 <= eye <= mxz + 2.0):
            eye = mnz + self.WALK_EYE_FT
        self._walk = {"x": (mnx + mxx) / 2.0, "y": (mny + mxy) / 2.0,
                      "z": eye, "yaw": cam.yaw, "pitch": 0.0}
        cam.ortho = False                   # walking is perspective-only
        cam.dist = 2.0                      # target sits just ahead (below)
        self.walk_btn.configure(text="Exit (Esc)")
        self.canvas.focus_set()
        self._walk_apply()

    def _walk_exit(self, restore: bool = True):
        if self._walk is None:
            return
        self._walk = None
        self.walk_btn.configure(text="Walk")
        if restore and self._saved_cam is not None:
            cam = self.cam
            (cam.yaw, cam.pitch, cam.dist, cam.target,
             cam.ortho) = self._saved_cam
        self._saved_cam = None
        self._render()

    def _walk_apply(self):
        """Reuse the orbit Camera math for first person: with eye = target -
        fwd * dist, placing target = eye_pos + fwd * dist puts the eye
        exactly at the walker's head."""
        st = self._walk
        cam = self.cam
        cam.yaw, cam.pitch = st["yaw"], st["pitch"]
        yr = math.radians(st["yaw"])
        pr = math.radians(st["pitch"])
        fwd = (-math.sin(yr) * math.cos(pr),
               math.cos(yr) * math.cos(pr), -math.sin(pr))
        d = cam.dist
        cam.target = (st["x"] + fwd[0] * d, st["y"] + fwd[1] * d,
                      st["z"] + fwd[2] * d)
        self._render()

    def walk_key(self, keysym: str) -> bool:
        """One walk step (w/up forward, s/down back, a/left and d/right
        strafe) on the xy plane; returns True when handled."""
        if self._walk is None:
            return False
        mv = {"w": (1, 0), "up": (1, 0), "s": (-1, 0), "down": (-1, 0),
              "a": (0, -1), "left": (0, -1),
              "d": (0, 1), "right": (0, 1)}.get(str(keysym).lower())
        if mv is None:
            return False
        f, st = mv
        yr = math.radians(self._walk["yaw"])
        fxv, fyv = -math.sin(yr), math.cos(yr)      # forward, xy plane
        rxv, ryv = math.cos(yr), math.sin(yr)       # strafe right
        self._walk["x"] += (fxv * f + rxv * st) * self.WALK_STEP_FT
        self._walk["y"] += (fyv * f + ryv * st) * self.WALK_STEP_FT
        self._walk_apply()
        return True

    def _on_key(self, e):
        if self.walk_key(getattr(e, "keysym", "")):
            return "break"

    def _on_escape(self, _e=None):
        if self.measuring:
            self.toggle_measure()               # off + clear the tape
        elif self._walk is not None:
            self._walk_exit(restore=True)

    # ------------------------------------------------------------ measure ---
    def toggle_measure(self):
        self.measuring = not self.measuring
        self._measure_pts = []
        if self.measuring:                      # rubber band: motion events
            self.canvas.bind("<Motion>", self._measure_motion)
        else:                                   # only — never an after loop
            self.canvas.unbind("<Motion>")
            self.canvas.delete("rubber")
        self.measure_btn.configure(
            text="Measuring… (Esc)" if self.measuring else "Measure")
        self._render()

    def _measure_motion(self, e):
        """Live rubber band from the first pick to the cursor."""
        cv = self.canvas
        cv.delete("rubber")
        if len(self._measure_pts) != 1:
            return
        w = max(cv.winfo_width(), 4)
        h = max(cv.winfo_height(), 4)
        scr = bim.project_points([self._measure_pts[0][1]], self.cam, w, h)
        if scr[0, 2] <= 1e-6:
            return
        cv.create_line(scr[0, 0], scr[0, 1], e.x, e.y,
                       fill=self.theme.colors["accent"], width=1.2,
                       dash=(4, 4), tags="rubber")

    def _measure_click(self, x, y):
        """Measure pick via _pick (vertex > edge > face).  Two points draw
        the dashed tape; a third click clears it.  The label always reads
        the TRUE geometry, even when the slope slider distorts the
        drawing."""
        if len(self._measure_pts) >= 2:
            self._measure_pts = []
            self._render()
            return
        hit = self._pick(x, y)
        if hit is None:
            return
        self._measure_pts.append(
            (hit["true_pt"], hit["drawn_pt"], hit["kind"]))
        self.canvas.delete("rubber")
        self._render()

    def _pick(self, x, y):
        """Priority pick at a screen point: vertex (12 px) > edge (6 px) >
        face (ray, front-most t).  Candidates are the same visible /
        section-clipped / drawn geometry the frame renders; endpoints
        MANUFACTURED by the section clip are not vertices (edge snap still
        reaches them, honestly, as points on an edge).  Vertex/edge snaps
        pick through geometry (the wireframe-viewer norm — said in the
        hint); slope-exaggerated pipes pick on DRAWN, report TRUE.
        Returns {"kind", "true_pt", "drawn_pt", "depth"} or None."""
        m = self.model
        if m is None:
            return None
        (mnx, mny, mnz), (mxx, mxy, mxz) = m.bounds()
        z_cut = None
        if self._slice_frac < 0.999:
            z_cut = mnz + (mxz - mnz) * self._slice_frac
        segs = [s for s in m.segments
                if s.system not in self.hidden_systems]
        if z_cut is not None:
            segs = [s for s in segs if (s.a[2] + s.b[2]) * 0.5 <= z_cut]
        cuts = [(False, False)] * len(segs)
        sec_faces = None
        if self.section is not None:
            segs, cuts, sec_faces = self._section_geo(segs, z_cut)
        exag = float(self.slope_var.get())
        z_mid = (mnz + mxz) / 2.0
        stretch = abs(exag - 1.0) > 1e-9
        w = max(self.canvas.winfo_width(), 4)
        h = max(self.canvas.winfo_height(), 4)
        if w < 4 or h < 4:
            w, h = 800, 600

        true_pts, drawn_pts, vert_ok = [], [], []
        for i, s in enumerate(segs):
            ca, cb = cuts[i]
            piped = stretch and getattr(s, "radius", 0.0) > 0.0
            for p, cut in ((s.a, ca), (s.b, cb)):
                p = tuple(float(v) for v in p)
                true_pts.append(p)
                drawn_pts.append(bim.exaggerate_z(p, z_mid, exag)
                                 if piped else p)
                vert_ok.append(not cut)
        if drawn_pts:
            scr = bim.project_points(drawn_pts, self.cam, w, h)
            best, best_key = None, (float(self.MEASURE_SNAP_PX), 0.0)
            for i in range(len(drawn_pts)):     # vertex pass
                if not vert_ok[i] or scr[i, 2] <= 1e-6:
                    continue
                key = (math.hypot(scr[i, 0] - x, scr[i, 1] - y),
                       float(scr[i, 2]))       # tie -> nearer vertex
                if key < best_key:
                    best, best_key = i, key
            if best is not None:
                return {"kind": "vertex", "true_pt": true_pts[best],
                        "drawn_pt": drawn_pts[best],
                        "depth": float(scr[best, 2])}
            best, best_d, best_t = None, float(self.EDGE_SNAP_PX), 0.0
            for i in range(len(segs)):          # edge pass
                a, b = scr[2 * i], scr[2 * i + 1]
                if a[2] <= 1e-6 or b[2] <= 1e-6:
                    continue
                abx, aby = b[0] - a[0], b[1] - a[1]
                ln2 = abx * abx + aby * aby
                if ln2 < 1e-12:
                    continue
                t = max(0.0, min(1.0, ((x - a[0]) * abx
                                       + (y - a[1]) * aby) / ln2))
                d = math.hypot(a[0] + t * abx - x, a[1] + t * aby - y)
                if d < best_d:
                    best, best_d, best_t = i, d, t
            if best is not None:
                i, t = best, best_t
                ta, tb = true_pts[2 * i], true_pts[2 * i + 1]
                da, db = drawn_pts[2 * i], drawn_pts[2 * i + 1]
                a, b = scr[2 * i], scr[2 * i + 1]
                return {"kind": "edge",
                        "true_pt": tuple(ta[k] + t * (tb[k] - ta[k])
                                         for k in range(3)),
                        "drawn_pt": tuple(da[k] + t * (db[k] - da[k])
                                          for k in range(3)),
                        "depth": float(a[2] + t * (b[2] - a[2]))}
        if bool(self.shaded_var.get()):         # face pass: visible faces
            if sec_faces is not None:
                flist = sec_faces
            else:
                flist = [f for f in (getattr(m, "faces", ()) or ())
                         if f.system not in self.hidden_systems
                         and (z_cut is None or sum(p[2] for p in f.pts)
                              / len(f.pts) <= z_cut)]
            tris = [tr for f in flist for tr in bim.fan_tris(f.pts)]
            if tris:
                o, d = bim.screen_ray(self.cam, x, y, w, h)
                tv = bim.ray_triangles(o, d, np.asarray(tris))
                j = int(np.argmin(tv))
                if np.isfinite(tv[j]):
                    hp = tuple(float(v) for v in (o + tv[j] * d))
                    eye, _r, _u, fwd = bim.basis(self.cam)
                    return {"kind": "face", "true_pt": hp, "drawn_pt": hp,
                            "depth": float((np.asarray(hp) - eye) @ fwd)}
        return None

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
        self._cancel_cam_anim()
        self._moved = 0.0
        if self.section is not None and self._walk is None \
                and not self.measuring:
            k = self._section_handle_at(e.x, e.y)
            if k is not None:                   # grab a box handle
                w = max(self.canvas.winfo_width(), 4)
                h = max(self.canvas.winfo_height(), 4)
                u = self._sec_axis_px(k, w, h)
                if u is not None:
                    self._sec_drag = (k, e.x, e.y,
                                      list(self.section["mn"]),
                                      list(self.section["mx"]), u)
                self._press = None              # end-on handle: consume,
                self._pan0 = None               # never orbit through it
                return
        if self._walk is not None:              # walk: drag turns, no pan
            self._press = (e.x, e.y, self._walk["yaw"], self._walk["pitch"])
            self._pan0 = None
            return
        if e.state & 0x1:                       # shift+left -> pan
            self._pan0 = (e.x, e.y, np.asarray(self.cam.target, dtype=float))
            self._press = None
        else:
            self._press = (e.x, e.y, self.cam.yaw, self.cam.pitch)
            self._pan0 = None

    def _on_drag(self, e):
        if self._sec_drag is not None:          # move one box plane
            k, x0, y0, mn0, mx0, u = self._sec_drag
            axis, side = divmod(k, 2)
            move = (((e.x - x0) * u[0] + (e.y - y0) * u[1])
                    / (u[0] * u[0] + u[1] * u[1]))
            gap = max((mx0[axis] - mn0[axis]) * 0.01, 1e-6)
            sec = self.section
            if side:
                sec["mx"][axis] = max(mn0[axis] + gap, mx0[axis] + move)
            else:
                sec["mn"][axis] = min(mx0[axis] - gap, mn0[axis] + move)
            self._sec_last = k
            self._sec_cache = None
            self._render()
            return
        if self._walk is not None:
            if self._press is None:
                return
            x0, y0, yaw0, pitch0 = self._press
            self._moved = max(self._moved, abs(e.x - x0) + abs(e.y - y0))
            self._walk["yaw"] = yaw0 + (e.x - x0) * 0.3
            self._walk["pitch"] = max(-60.0, min(60.0,
                                                 pitch0 + (e.y - y0) * 0.3))
            self._walk_apply()
            return
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
        if self._sec_drag is not None:
            self._sec_drag = None
            self._end_interaction()
            return
        if self._press is not None and self._moved < 3:
            if self.measuring:
                self._measure_click(e.x, e.y)
            elif self._walk is None:
                self._click(e.x, e.y)
        self._press = None
        self._pan0 = None
        self._end_interaction()

    def _on_double(self, e):
        """Double-click a box handle resets that plane to the model bound;
        anywhere else keeps the classic double-click-to-fit."""
        if self.section is not None:
            k = self._section_handle_at(e.x, e.y)
            if k is not None:
                axis, side = divmod(k, 2)
                mn, mx = self._sec_full_box()
                if side:
                    self.section["mx"][axis] = mx[axis]
                else:
                    self.section["mn"][axis] = mn[axis]
                self._sec_last = k
                self._sec_cache = None
                self._render()
                return
        self.fit()

    def _on_pan_start(self, e):
        if self._walk is not None:
            return
        self._cancel_cam_anim()
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
        if self._walk is not None:              # the walker's feet zoom
            return
        self._cancel_cam_anim()
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
        """Strata-style legend: every system chip is a click-to-toggle."""
        for child in self.legend.winfo_children():
            child.destroy()
        if self.model is None:
            return
        c = self.theme.colors
        for name, color in self.model.systems:
            off = name in self.hidden_systems
            lbl = tk.Label(self.legend,
                           text=("□ " if off else "■ ") + name,
                           fg=c["muted"] if off else color, bg=c["bg"],
                           font=(FAMILY, 9), cursor="hand2")
            lbl.pack(side="left", padx=(0, 8))
            lbl.bind("<Button-1>",
                     lambda e, n=name: self.toggle_system(n))

    def toggle_system(self, name: str) -> None:
        if name in self.hidden_systems:
            self.hidden_systems.discard(name)
        else:
            self.hidden_systems.add(name)
        self._build_legend()
        self._render()

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
        if m is None or (not m.segments and not m.planes
                         and not getattr(m, "faces", None)):
            self._draw_empty(w, h, c)
            return
        cam = self.cam

        # Horizon Slice: cut everything above z_cut (live section cut)
        (mnx, mny, mnz), (mxx, mxy, mxz) = m.bounds()
        z_cut = None
        if self._slice_frac < 0.999:
            z_cut = mnz + (mxz - mnz) * self._slice_frac

        # decimate when the last frame was slow
        segs = m.segments
        if self.hidden_systems:
            segs = [s for s in segs if s.system not in self.hidden_systems]
        if z_cut is not None:
            segs = [s for s in segs
                    if (s.a[2] + s.b[2]) * 0.5 <= z_cut]
        sec_faces = None
        if self.section is not None:            # real clipping, cached
            segs, _cuts, sec_faces = self._section_geo(segs, z_cut)
        if self._lod < 0.999 and len(segs) > 60:
            step = max(1, round(1.0 / self._lod))
            segs = segs[::step]

        # slope exaggeration: render-time z-delta scale about the model's
        # z-midpoint, PIPE segments only (radius > 0).  The model is never
        # mutated and the slice above culls on TRUE z — the slider is a
        # pure display distortion.
        exag = float(self.slope_var.get())
        z_mid = (mnz + mxz) / 2.0
        stretch = abs(exag - 1.0) > 1e-9

        def draw_pt(pt, s):
            if stretch and getattr(s, "radius", 0.0) > 0.0:
                return bim.exaggerate_z(pt, z_mid, exag)
            return pt

        # shaded mode: pipe solids replace their centerlines
        shaded = bool(self.shaded_var.get())
        if shaded:
            pipe_segs = [s for s in segs if getattr(s, "radius", 0.0) > 0.0]
            line_segs = [s for s in segs
                         if getattr(s, "radius", 0.0) <= 0.0]
        else:
            pipe_segs, line_segs = [], segs
        raster_pref = (shaded and bool(self.raster_var.get())
                       and not self._raster_slow)
        interacting = (self._press is not None or self._pan0 is not None
                       or self._sec_drag is not None)
        drag_lod = raster_pref and interacting

        planes = m.planes
        vis_planes = [(j, pl) for j, pl in enumerate(planes)
                      if z_cut is None
                      or sum(cnr[2] for cnr in pl.corners) / 4.0 <= z_cut]
        pins = [p for p in self.pins if z_cut is None or p[2] <= z_cut]
        if self.section is not None:            # markers: centroid in/out
            smn, smx = self.section["mn"], self.section["mx"]

            def _inbox(px, py, pz):
                return all(smn[i] - 1e-9 <= v <= smx[i] + 1e-9
                           for i, v in enumerate((px, py, pz)))

            vis_planes = [
                (j, pl) for j, pl in vis_planes
                if _inbox(sum(cr[0] for cr in pl.corners) / 4.0,
                          sum(cr[1] for cr in pl.corners) / 4.0,
                          sum(cr[2] for cr in pl.corners) / 4.0)]
            pins = [p for p in pins if _inbox(p[0], p[1], p[2])]

        # one projection call for every endpoint + plane corner + pin
        span = max(mxx - mnx, mxy - mny, mxz - mnz, 1.0)
        pin_h = span * 0.05
        pts = [draw_pt(p, s) for s in line_segs for p in (s.a, s.b)]
        for _j, pl in vis_planes:
            pts.extend(pl.corners)
        for px, py, pz, _lbl, _col in pins:
            pts.append((px, py, pz))
            pts.append((px, py, pz + pin_h))
        scr = bim.project_points(pts, cam, w, h)

        # faces: model quads (walls) + pipe prisms; hidden_systems and the
        # Horizon Slice cull them exactly like segments (centroid-z test —
        # cheap approximation of clipping, matching the midpoint rule)
        faces = []
        if shaded:
            if sec_faces is not None:           # already filtered + clipped
                for f in sec_faces:
                    faces.append((f, False))
            else:
                for f in getattr(m, "faces", ()) or ():
                    if f.system in self.hidden_systems:
                        continue
                    fz = sum(p[2] for p in f.pts) / len(f.pts)
                    if z_cut is not None and fz > z_cut:
                        continue
                    faces.append((f, False))
            for s in pipe_segs:
                # raster drag LOD: hexagon prisms, no caps (back on release)
                tf = bim.tube_faces(draw_pt(s.a, s), draw_pt(s.b, s),
                                    s.radius, sides=6 if drag_lod else 8,
                                    color=s.color, system=s.system)
                if drag_lod:
                    tf = tf[:-2]
                for f in tf:
                    faces.append((f, True))
        fscr = None
        if faces:
            fpts = [p for f, _pipe in faces for p in f.pts]
            fscr = bim.project_points(fpts, cam, w, h)

        # depth cueing: this frame's near/far span; skipped at quality "off"
        d_near = d_far = None
        if _fx_quality() != "off":
            parts = [scr[:, 2]] if len(scr) else []
            if fscr is not None and len(fscr):
                parts.append(fscr[:, 2])
            if parts:
                alld = np.concatenate(parts)
                alld = alld[alld > 1e-6]
                if alld.size >= 2:
                    lo, hi = float(alld.min()), float(alld.max())
                    if hi - lo > 1e-6:
                        d_near, d_far = lo, hi
        bg = c["canvas_bg"]
        fade_cache: dict = {}

        def fade(color, depth):
            """Far geometry dims toward the canvas bg (6 buckets, subtle)."""
            if d_near is None:
                return color
            t = (depth - d_near) / (d_far - d_near)
            b = int(max(0.0, min(1.0, t)) * 6 + 0.5)
            if b <= 0:
                return color
            key = (color, b)
            got = fade_cache.get(key)
            if got is None:
                got = fade_cache[key] = _mix(color, bg, 0.45 * b / 6.0)
            return got

        # shaded faces: raster z-buffer (per-pixel depth, one blitted image)
        # when enabled and sane; painter's algorithm by face-centroid
        # distance otherwise.  Either way faces sit BENEATH the wireframe.
        tri_est = sum(max(0, len(f.pts) - 2) for f, _p in faces)
        use_raster = bool(raster_pref and faces and tri_est <= RASTER_MAX_TRIS)
        note = None
        if raster_pref and faces and not use_raster:
            note = "model too heavy for true depth — painter mode"
        elif shaded and self.raster_var.get() and self._raster_slow:
            note = "true depth too slow here — painter mode"
        rscale = 1.0
        if use_raster:
            rscale = self._raster_blit(faces, fscr, fade, w, h, c,
                                       interacting)
        else:
            self._photo = None
            self._draw_grid(w, h, c)
        if fscr is not None and not use_raster:
            shade_cache: dict = {}
            polys = []
            idx = 0
            for f, is_pipe in faces:
                n = len(f.pts)
                quad = fscr[idx: idx + n]
                idx += n
                if np.any(quad[:, 2] <= 1e-6):  # behind the camera -> cull
                    continue
                depth = float(quad[:, 2].mean())
                lamb = raster.lambert_bucket(raster.face_normal(f))
                skey = (f.color, lamb)          # bucket: reuse mixed colors
                base = shade_cache.get(skey)
                if base is None:
                    base = shade_cache[skey] = "#%02x%02x%02x" % \
                        raster.shade(f.color, lamb, bg)
                coords = [v for q in quad for v in (q[0], q[1])]
                polys.append((depth, coords, fade(base, depth), is_pipe))
            polys.sort(key=lambda it: -it[0])   # far first
            for _d, coords, fill, is_pipe in polys:
                cv.create_polygon(*coords, fill=fill, outline=fill,
                                  width=1.0,
                                  tags=(("face", "pipe3d") if is_pipe
                                        else ("face",)))

        items = []                              # (depth, draw_fn) painter list
        for i, s in enumerate(line_segs):
            a, b = scr[2 * i], scr[2 * i + 1]
            if a[2] <= 1e-6 or b[2] <= 1e-6:    # behind the camera -> cull
                continue
            depth = (a[2] + b[2]) / 2.0
            prox = max(0.6, min(2.4, cam.dist / max(depth, 1e-6)))
            width = max(1.0, s.width * prox)
            items.append((depth, ("line", (a[0], a[1], b[0], b[1]),
                                  fade(s.color, depth), width)))
        chips = []
        base = 2 * len(line_segs)
        for k, (j, pl) in enumerate(vis_planes):
            quad = scr[base + 4 * k: base + 4 * k + 4]
            if np.any(quad[:, 2] <= 1e-6):
                continue
            depth = float(quad[:, 2].mean())
            coords = [v for p in quad for v in (p[0], p[1])]
            items.append((depth, ("plane", coords, pl.color, j)))
            cx = float(quad[:, 0].mean())
            cy = float(quad[:, 1].mean())
            chips.append((cx, cy, j, pl))

        # layout pins: stem + glowing head + label, depth-sorted with the rest
        pbase = base + 4 * len(vis_planes)
        for i, (_px, _py, _pz, lbl, col) in enumerate(pins):
            a = scr[pbase + 2 * i]              # base of the stem
            b = scr[pbase + 2 * i + 1]          # head
            if a[2] <= 1e-6 or b[2] <= 1e-6:
                continue
            depth = float(b[2])
            prox = max(0.7, min(2.6, cam.dist / max(depth, 1e-6)))
            items.append((depth, ("pin", (a[0], a[1], b[0], b[1]),
                                  col, lbl, 4.0 * prox)))

        items.sort(key=lambda it: -it[0])       # far first
        for _, it in items:
            if it[0] == "line":
                _, xy, color, width = it
                cv.create_line(*xy, fill=color, width=width, tags="seg")
            elif it[0] == "pin":
                _, (ax, ay, bx, by), col, lbl, r = it
                cv.create_line(ax, ay, bx, by, fill=col, width=2.0,
                               tags="pin")
                cv.create_oval(bx - r - 2, by - r - 2, bx + r + 2,
                               by + r + 2, outline=col, width=1.0,
                               tags="pin")                       # halo
                cv.create_oval(bx - r, by - r, bx + r, by + r, fill=col,
                               outline="", tags="pin")
                if lbl:
                    cv.create_text(bx + r + 4, by, text=lbl, anchor="w",
                                   fill=col, font=(FAMILY, 8, "bold"),
                                   tags="pin")
            else:
                _, coords, color, j = it
                cv.create_polygon(*coords, fill=color, stipple="gray25",
                                  outline=color, width=1.4,
                                  tags=("plane", f"sheet:{j}"))

        # glowing cut plane where the Horizon Slice bites
        if z_cut is not None and mxz > mnz:
            quad = bim.project_points(
                [(mnx, mny, z_cut), (mxx, mny, z_cut),
                 (mxx, mxy, z_cut), (mnx, mxy, z_cut)], cam, w, h)
            if not np.any(quad[:, 2] <= 1e-6):
                coords = [v for p in quad for v in (p[0], p[1])]
                cv.create_polygon(*coords, fill=c["accent"],
                                  stipple="gray12", outline=c["accent"],
                                  width=2.0, tags="cut")

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

        if self.section is not None:
            self._draw_section_box(w, h, c)
        self._draw_measure(w, h, c)
        if self._walk is not None:
            self._draw_hud(h, c)

        # adapt detail from measured draw time (applies to the next frame)
        dt = time.perf_counter() - t0
        if dt > SLOW_FRAME:
            self._lod = max(MIN_LOD, self._lod * 0.6)
            if use_raster and rscale < 0.999:   # still slow at half-res:
                self._raster_slow = True        # honest sticky fallback
                note = "true depth too slow here — painter mode"
        elif dt < FAST_FRAME and self._lod < 1.0:
            self._lod = min(1.0, self._lod * 1.6)
        if note is None and self.measuring:
            note = "measure: vertex > edge > face snap · picks through · Esc"
        self._set_hint(note)

    def _draw_measure(self, w, h, c):
        """Measure overlay: snap markers (square = vertex, diamond = edge,
        circle = face), dashed tape, two-line surveying readout — SD/HD/VD
        plus ΔN/ΔE, azimuth and pipe slope, all from bim.measure3d (i.e.
        fieldpro.deltas — the tape agrees with the As-Staked Ledger to the
        last digit).  TRUE geometry, unaffected by slope exaggeration."""
        if not self._measure_pts:
            return
        cv = self.canvas
        drawn = [dp for _tp, dp, _k in self._measure_pts]
        scr = bim.project_points(drawn, self.cam, w, h)
        if np.any(scr[:, 2] <= 1e-6):
            return
        col = c["accent"]
        for (_tp, _dp, kind), (x, y, _d) in zip(self._measure_pts, scr):
            if kind == "vertex":
                cv.create_rectangle(x - 4, y - 4, x + 4, y + 4, outline=col,
                                    width=1.6, tags="measure")
            elif kind == "edge":
                cv.create_polygon(x, y - 5, x + 5, y, x, y + 5, x - 5, y,
                                  outline=col, fill="", width=1.6,
                                  tags="measure")
            else:
                cv.create_oval(x - 4, y - 4, x + 4, y + 4, outline=col,
                               width=1.6, tags="measure")
        if len(self._measure_pts) < 2:
            return
        (ax, ay, _), (bx, by, _) = scr
        cv.create_line(ax, ay, bx, by, fill=col, width=1.6, dash=(6, 4),
                       tags="measure")
        from ..draft import fmt_ftin            # lazy: engine-side module
        a3 = self._measure_pts[0][0]
        b3 = self._measure_pts[1][0]
        r = bim.measure3d(a3, b3)
        vd = r["vd"] or 0.0
        sign = "-" if vd < -1e-9 else "+" if vd > 1e-9 else ""
        text = (f"SD {fmt_ftin(r['sd'])}   HD {fmt_ftin(r['hd'])}   "
                f"VD {sign}{fmt_ftin(abs(vd))}\n"
                f"ΔN {r['dn']:+.2f}  ΔE {r['de']:+.2f}  "
                f"az {r['azimuth']:.1f}°")
        if r["slope_in_ft"] is not None and abs(r["slope_in_ft"]) >= 0.005:
            text += f"  slope {abs(r['slope_in_ft']):.2f}\"/ft"
        tid = cv.create_text((ax + bx) / 2.0, (ay + by) / 2.0 - 20,
                             text=text, justify="center",
                             fill=col, font=(FAMILY, 9, "bold"),
                             tags="measure")
        x1, y1, x2, y2 = cv.bbox(tid)
        rid = cv.create_rectangle(x1 - 4, y1 - 2, x2 + 4, y2 + 2,
                                  fill=c["panel"], outline=col,
                                  tags="measure")
        cv.tag_raise(tid, rid)

    def _draw_hud(self, h, c):
        """Walk-mode "you are here" chip: position in feet + the exits."""
        st = self._walk
        from ..draft import fmt_ftin            # lazy: engine-side module
        txt = (f"you are here   E {fmt_ftin(st['x'])}   "
               f"N {fmt_ftin(st['y'])}   eye {fmt_ftin(st['z'])}"
               "   ·   WASD/arrows walk · drag turn · Esc exit")
        cv = self.canvas
        tid = cv.create_text(14, h - 16, anchor="w", text=txt, fill=c["fg"],
                             font=(FAMILY, 9, "bold"), tags="hud")
        x1, y1, x2, y2 = cv.bbox(tid)
        rid = cv.create_rectangle(x1 - 8, y1 - 4, x2 + 8, y2 + 4,
                                  fill=c["panel"], outline=c["accent"],
                                  tags="hud")
        cv.tag_raise(tid, rid)

    def _grid_lattice(self):
        """Ground-grid line endpoints [((x,y,z), (x,y,z)), ...] shared by
        the canvas grid and its rasterized ground-quad counterpart."""
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
        lines = []
        x = gx0
        while x <= gx1 + 1e-9:
            lines.append(((x, gy0, z), (x, gy1, z)))
            x += step
        y = gy0
        while y <= gy1 + 1e-9:
            lines.append(((gx0, y, z), (gx1, y, z)))
            y += step
        return lines

    def _draw_grid(self, w, h, c):
        """Fine ground grid under everything (not depth-sorted with model)."""
        pts = [p for ab in self._grid_lattice() for p in ab]
        scr = bim.project_points(pts, self.cam, w, h)
        for i in range(0, len(pts), 2):
            a, b = scr[i], scr[i + 1]
            if a[2] <= 1e-6 or b[2] <= 1e-6:
                continue
            self.canvas.create_line(a[0], a[1], b[0], b[1], fill=c["muted"],
                                    width=1, stipple="gray50", tags="grid")

    def _grid_faces(self, color):
        """The grid as thin ground-plane quads for the rasterizer — unlike
        the canvas grid, the building then correctly occludes it."""
        wid = max(self._world_per_px(), 1e-6) * 0.9     # ~1 px wide
        out = []
        for (xa, ya, z), (xb, yb, _z2) in self._grid_lattice():
            dx, dy = xb - xa, yb - ya
            ln = math.hypot(dx, dy)
            if ln < 1e-9:
                continue
            nx = -dy / ln * wid / 2.0
            ny = dx / ln * wid / 2.0
            out.append(bim.Face([(xa - nx, ya - ny, z), (xb - nx, yb - ny, z),
                                 (xb + nx, yb + ny, z), (xa + nx, ya + ny, z)],
                                color))
        return out

    def _raster_blit(self, faces, fscr, fade, w, h, c, interacting):
        """Rasterize the face set + ground grid through rfi_stamper.raster
        and blit ONE canvas image at (0, 0); overlays draw above it.  Half
        resolution while interacting or decimated, pixel-doubled back up
        (refine-on-release).  Returns the scale used."""
        bg = c["canvas_bg"]
        s = 0.5 if (interacting or self._lod < 0.999) else 1.0
        rw, rh = max(2, int(w * s)), max(2, int(h * s))
        shade_cache: dict = {}
        rfaces, cols = [], []
        idx = 0
        for f, _pipe in faces:
            n = len(f.pts)
            depth = float(fscr[idx: idx + n, 2].mean())
            idx += n
            lamb = raster.lambert_bucket(raster.face_normal(f))
            skey = (f.color, lamb)
            base = shade_cache.get(skey)
            if base is None:
                base = shade_cache[skey] = "#%02x%02x%02x" % \
                    raster.shade(f.color, lamb, bg)
            rfaces.append(f)
            cols.append(raster.hex_rgb(fade(base, depth)))
        n_real = len(rfaces)
        gcol = _mix(c["muted"], bg, 0.5)    # the canvas grid's stipple look
        glamb = raster.lambert_bucket((0.0, 0.0, 1.0))
        for gf in self._grid_faces(gcol):
            rfaces.append(gf)
            cols.append(raster.shade(gf.color, glamb, bg))
        frame = raster.render(rfaces, self.cam, rw, rh, bg, colors=cols)
        arr = frame.rgb
        edge = raster.outline_mask(frame, soft_from=n_real)
        if edge.any():                      # CAD-style occluding contours
            fg = np.asarray(raster.hex_rgb(c["fg"]), dtype=float)
            px = arr[edge].astype(float)
            arr[edge] = (px + (fg - px) * 0.45 + 0.5).astype(np.uint8)
        if s < 1.0:
            arr = np.repeat(np.repeat(arr, 2, axis=0), 2, axis=1)[:h, :w]
            if arr.shape[0] < h or arr.shape[1] < w:    # odd w/h: pad edge
                arr = np.pad(arr, ((0, h - arr.shape[0]),
                                   (0, w - arr.shape[1]), (0, 0)),
                             mode="edge")
        header = f"P6 {arr.shape[1]} {arr.shape[0]} 255 ".encode()
        self._photo = tk.PhotoImage(
            data=header + np.ascontiguousarray(arr).tobytes())
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo,
                                 tags="raster")
        return s

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
