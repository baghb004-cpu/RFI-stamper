"""Lookout: an offline 360° panorama viewer for site photos.

Equirectangular images (the 2:1 output of one-shot site cameras) are
reprojected to a live perspective view in pure numpy — drag to look around,
wheel to zoom the field of view.  Ordinary photos open in a plain fitted
view.  Everything stays on this machine; images are read via fitz, never
uploaded anywhere.
"""
from __future__ import annotations

import math
import os
import tkinter as tk
from tkinter import ttk

import fitz
import numpy as np

from .theme import FAMILY

# cached normalized camera-ray grids keyed by (out_w, out_h, fov_deg)
_GRID_CACHE: dict = {}
_GRID_CACHE_MAX = 8


def load_image_rgb(path: str) -> np.ndarray:
    """Read any common image (or a PDF's first page) to an RGB HxWx3 array."""
    try:
        pix = fitz.Pixmap(path)
    except Exception:   # noqa: BLE001 -- not an image codec: try as document
        with fitz.open(path) as doc:            # PDF "photo" -> render page 1
            pix = doc[0].get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
    if pix.colorspace is None or pix.colorspace.n != 3:
        pix = fitz.Pixmap(fitz.csRGB, pix)      # gray / CMYK -> RGB
    if pix.alpha:
        pix = fitz.Pixmap(pix, 0)               # conversion keeps alpha: drop
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, 3)
    return img.copy()


def is_panorama(img: np.ndarray) -> bool:
    """Equirectangular panoramas are 2:1 (within ~6%)."""
    h, w = img.shape[:2]
    return h > 0 and abs(w / (2.0 * h) - 1.0) < 0.06


def _cam_grid(out_w: int, out_h: int, fov_deg: float) -> np.ndarray:
    key = (out_w, out_h, round(fov_deg, 1))
    grid = _GRID_CACHE.get(key)
    if grid is None:
        f = 0.5 * out_w / math.tan(math.radians(fov_deg) * 0.5)
        u = np.arange(out_w, dtype=np.float32) - out_w / 2.0 + 0.5
        v = np.arange(out_h, dtype=np.float32) - out_h / 2.0 + 0.5
        uu, vv = np.meshgrid(u, v)
        d = np.stack([uu, -vv, np.full_like(uu, f)], axis=-1)
        d /= np.linalg.norm(d, axis=-1, keepdims=True)
        grid = d
        if len(_GRID_CACHE) >= _GRID_CACHE_MAX:
            _GRID_CACHE.pop(next(iter(_GRID_CACHE)))
        _GRID_CACHE[key] = grid
    return grid


def reproject(img: np.ndarray, yaw_deg: float, pitch_deg: float,
              fov_deg: float = 75.0, out_w: int = 960,
              out_h: int = 600) -> np.ndarray:
    """Equirect -> perspective. yaw + turns right, pitch + looks up.
    Returns an out_h x out_w x 3 uint8 view."""
    H, W = img.shape[:2]
    d = _cam_grid(out_w, out_h, fov_deg)
    yp = math.radians(pitch_deg)
    yy = math.radians(yaw_deg)
    cp, sp = math.cos(yp), math.sin(yp)
    cy, sy = math.cos(yy), math.sin(yy)
    x, y, z = d[..., 0], d[..., 1], d[..., 2]
    # pitch about the x (right) axis (positive looks up), then yaw about
    # the y (up) axis (positive turns right)
    y2 = y * cp + z * sp
    z2 = -y * sp + z * cp
    x3 = x * cy + z2 * sy
    z3 = -x * sy + z2 * cy
    lon = np.arctan2(x3, z3)                       # -pi..pi
    lat = np.arcsin(np.clip(y2, -1.0, 1.0))        # -pi/2..pi/2
    px = ((lon / (2.0 * math.pi) + 0.5) * W).astype(np.int32) % W
    py = np.clip(((0.5 - lat / math.pi) * H).astype(np.int32), 0, H - 1)
    return img[py, px]


def _to_photo(arr: np.ndarray) -> tk.PhotoImage:
    h, w = arr.shape[:2]
    header = f"P6 {w} {h} 255 ".encode()
    return tk.PhotoImage(data=header + np.ascontiguousarray(arr).tobytes())


class LookoutViewer(tk.Toplevel):
    """Drag to look around, wheel to zoom FOV, double-click to recenter."""

    def __init__(self, root, theme, path: str):
        img = load_image_rgb(path)      # BEFORE the window: an unreadable
        super().__init__(root)          # file must not orphan a Toplevel
        self.theme = theme
        self.title("Lookout — " + os.path.basename(path))
        self.geometry("1000x640")
        c = theme.colors
        self.configure(bg=c["bg"])
        self.img = img
        self.pano = is_panorama(self.img)
        self.yaw, self.pitch, self.fov = 0.0, 0.0, 75.0
        self._photo = None
        self._press = None
        self._pending = False

        bar = ttk.Frame(self)
        bar.pack(fill="x")
        mode = ("360° panorama — drag to look around · wheel zooms the view"
                if self.pano else
                "flat photo (not 2:1 equirectangular) — shown fitted")
        ttk.Label(bar, text="  ◎ " + mode, style="Muted.TLabel").pack(
            side="left", pady=3)
        ttk.Button(bar, text="Close", command=self.destroy).pack(
            side="right", padx=4)
        self.canvas = tk.Canvas(self, highlightthickness=0, bg=c["canvas_bg"])
        self.canvas.pack(fill="both", expand=True)
        cv = self.canvas
        cv.bind("<Configure>", lambda e: self._schedule())
        if self.pano:
            cv.bind("<ButtonPress-1>", self._start)
            cv.bind("<B1-Motion>", self._drag)
            cv.bind("<Double-Button-1>", self._reset)
            cv.bind("<MouseWheel>", self._wheel)
            cv.bind("<Button-4>", lambda e: self._zoom(-6))
            cv.bind("<Button-5>", lambda e: self._zoom(6))
        self.bind("<Escape>", lambda e: self.destroy())

    # ---------------------------------------------------------- interaction
    def _start(self, e):
        self._press = (e.x, e.y, self.yaw, self.pitch)

    def _drag(self, e):
        if not self._press:
            return
        x0, y0, yaw0, pitch0 = self._press
        scale = self.fov / max(self.canvas.winfo_width(), 1)
        self.yaw = yaw0 - (e.x - x0) * scale
        self.pitch = max(-85.0, min(85.0, pitch0 + (e.y - y0) * scale))
        self._schedule()

    def _reset(self, _e=None):
        self.yaw, self.pitch, self.fov = 0.0, 0.0, 75.0
        self._schedule()

    def _wheel(self, e):
        self._zoom(-6 if e.delta > 0 else 6)

    def _zoom(self, delta):
        self.fov = max(28.0, min(100.0, self.fov + delta))
        self._schedule()

    # -------------------------------------------------------------- render
    def _schedule(self):
        if not self._pending:
            self._pending = True
            self.after_idle(self._render)

    def _render(self):
        self._pending = False
        cv = self.canvas
        if not cv.winfo_exists():
            return
        w = max(cv.winfo_width(), 40)
        h = max(cv.winfo_height(), 40)
        if self.pano:
            # cap the warp size; canvas scales the rest visually
            out_w = min(w, 1100)
            out_h = min(h, 700)
            arr = reproject(self.img, self.yaw, self.pitch, self.fov,
                            out_w, out_h)
        else:
            ih, iw = self.img.shape[:2]
            s = min(w / iw, h / ih, 1.0)
            step = max(1, int(round(1.0 / max(s, 1e-6))))
            arr = self.img[::step, ::step]
        self._photo = _to_photo(arr)
        cv.delete("all")
        cv.create_image(w // 2, h // 2, image=self._photo)
        if self.pano:
            cv.create_text(10, h - 14, anchor="w",
                           fill=self.theme.colors["muted"],
                           font=(FAMILY, 9),
                           text=f"yaw {self.yaw:+.0f}°  pitch "
                                f"{self.pitch:+.0f}°  fov {self.fov:.0f}°")


def open_lookout(root, theme, path: str) -> LookoutViewer | None:
    """Open a photo in the Lookout viewer; None if it can't be read."""
    try:
        return LookoutViewer(root, theme, path)
    except Exception:   # noqa: BLE001 -- unreadable/missing image
        return None
