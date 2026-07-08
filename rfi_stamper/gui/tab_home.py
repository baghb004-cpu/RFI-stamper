"""Home: the Planloom landing — animated blueprint hero, project bar, one
card per workspace section, recent files, and a giant routed drop zone."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, ttk

from . import fx
from .theme import SECTIONS, mix
from .widgets import DropZone

CARD_KEYS = ("field", "project", "plans", "reporting", "integrations",
             "truth")
CARD_DESCS = {
    "field": "Tasks, schedule, punch list\nand inspections.",
    "project": "RFIs stamped & resolved, submittals,\nCOs, budget, documents, specs.",
    "plans": "View and mark up sheets, draft\nin the Loft, walk the 3D model.",
    "reporting": "Snapshots, logs, pickup\nsheets and field forms.",
    "integrations": "File bridges to spreadsheets,\ncalendars and other tools.",
    "truth": "Animated KPIs and a rules-\nbased insight feed.",
}


def round_rect(cv, x0, y0, x1, y1, r, **kw):
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return cv.create_polygon(pts, smooth=True, **kw)


class SectionCard(tk.Canvas):
    W, H = 300, 104

    def __init__(self, parent, theme, key, command):
        super().__init__(parent, width=self.W, height=self.H,
                         highlightthickness=0, cursor="hand2")
        self.theme, self.key = theme, key
        self._hover = False
        self.bind("<Button-1>", lambda e: command())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        theme.register(lambda c: self._draw())

    def _set_hover(self, on):
        self._hover = on
        self._draw()

    def _draw(self):
        c = self.theme.colors
        meta = SECTIONS[self.key]
        self.configure(bg=c["bg"])
        self.delete("all")
        accent = meta["color"]
        fill = mix(accent, c["card"], 0.90 if not self._hover else 0.82)
        round_rect(self, 3, 3, self.W - 3, self.H - 3, 13, fill=fill,
                   outline=accent if self._hover else c["border"],
                   width=1.6)
        self.create_rectangle(3, 26, 7, self.H - 26, fill=accent, outline="")
        self.create_text(30, self.H / 2, text=meta["glyph"], fill=accent,
                         font=("Segoe UI", 22, "bold"))
        self.create_text(60, 30, text=meta["label"], anchor="w", fill=c["fg"],
                         font=("Segoe UI", 12, "bold"))
        self.create_text(60, 64, text=CARD_DESCS[self.key], anchor="w",
                         fill=c["muted"], font=("Segoe UI", 9),
                         justify="left", width=self.W - 72)


class HomeTab(ttk.Frame):
    def __init__(self, parent, theme, status, goto_section, project_ops,
                 recent, on_recent):
        """goto_section(key); project_ops = {"name":fn->str, "new":fn,
        "open":fn}; on_recent(path, kind)."""
        super().__init__(parent)
        self.theme = theme
        self.on_recent = on_recent
        self.project_ops = project_ops

        # animated blueprint hero + a slowly orbiting wireframe building
        self.hero = tk.Canvas(self, height=128, highlightthickness=0)
        self.hero.pack(fill="x")
        self.backdrop = fx.BlueprintBackdrop(self.hero, theme)
        theme.register(lambda c: self._draw_hero())
        self.hero.bind("<Configure>", lambda e: self._draw_hero())
        self.backdrop.start()
        self._spin_yaw = -30.0
        self._spin_on = False
        self.hero.bind("<Map>", lambda e: self._start_spin(), add="+")
        self._start_spin()

        # bottom drop zone reserves its space before `body` claims the rest
        self._router = None
        DropZone(self, theme,
                 "Drop anything — a plan set, RFI files, PDFs to combine — "
                 "Planloom routes it", self._route,
                 browse=lambda: goto_section("plans"), height=88,
                 big=True).pack(fill="x", side="bottom", padx=32,
                                pady=(6, 16))

        body = ttk.Frame(self, padding=(32, 12))
        body.pack(fill="both", expand=True)

        # project bar
        pbar = ttk.Frame(body)
        pbar.pack(fill="x", pady=(0, 12))
        ttk.Label(pbar, text="Project:", style="Sub.TLabel").pack(side="left")
        self.proj_lbl = ttk.Label(pbar, text="none open",
                                  style="Title.TLabel")
        self.proj_lbl.pack(side="left", padx=8)
        ttk.Button(pbar, text="New project…", style="Accent.TButton",
                   command=project_ops["new"]).pack(side="left", padx=4)
        ttk.Button(pbar, text="Open project…",
                   command=project_ops["open"]).pack(side="left")
        ttk.Label(pbar, style="Muted.TLabel",
                  text="   a project is one local .ploom.json file — tasks, "
                       "punch, budget, statuses, all of it").pack(side="left")

        grid = ttk.Frame(body)
        grid.pack(anchor="w")
        for i, key in enumerate(CARD_KEYS):
            card = SectionCard(grid, theme, key,
                               lambda k=key: goto_section(k))
            card.grid(row=i // 3, column=i % 3, padx=(0, 16), pady=(0, 14))

        self.recent_box = ttk.Frame(body)
        self.recent_box.pack(fill="x", anchor="w", pady=(6, 0))
        self.show_recent(recent)

    # ------------------------------------------------------------- hero
    def _draw_hero(self):
        cv = self.hero
        c = self.theme.colors
        cv.delete("hero")
        w = max(cv.winfo_width(), 10)
        cv.configure(bg=mix(c["accent"], c["bg"], 0.92))
        x = 36
        t1 = cv.create_text(x, 46, anchor="w", fill=c["fg"], tags="hero",
                            font=("Segoe UI", 30, "bold"), text="PLAN")
        cv.create_text(cv.bbox(t1)[2] + 2, 46, anchor="w", fill=c["accent"],
                       tags="hero", font=("Segoe UI", 30, "bold"),
                       text="LOOM")
        cv.create_text(x + 2, 84, anchor="w", fill=c["muted"], tags="hero",
                       font=("Segoe UI", 11),
                       text="The offline construction workspace that weaves "
                            "RFI answers straight into the sheets.")
        cv.create_text(x + 2, 106, anchor="w", fill=c["muted"], tags="hero",
                       font=("Segoe UI", 8, "bold"),
                       text="●  100% OFFLINE — NOTHING EVER LEAVES THIS "
                            "MACHINE")
        cv.create_line(w - 260, 100, w - 40, 100, fill=c["accent"], width=2,
                       tags="hero")
        for i in range(10):
            cv.create_line(w - 250 + i * 22, 88, w - 250 + i * 22, 112,
                           fill=mix(c["accent"], c["bg"], 0.5), tags="hero")

    # tiny two-story wireframe that slowly orbits at the hero's right edge —
    # ambient, ≤ one eased cycle per 14 s, stops the instant the tab unmaps
    _HERO_SEGS = None

    @classmethod
    def _hero_model(cls):
        if cls._HERO_SEGS is None:
            segs = []
            for z in (0.0, 10.0, 20.0):                    # slab outlines
                segs += [((0, 0, z), (36, 0, z)), ((36, 0, z), (36, 24, z)),
                         ((36, 24, z), (0, 24, z)), ((0, 24, z), (0, 0, z))]
            for x, y in ((0, 0), (36, 0), (36, 24), (0, 24), (18, 0),
                         (18, 24)):                        # columns
                segs.append(((x, y, 0.0), (x, y, 20.0)))
            # gable roof: a slope pair at each end + the ridge line
            segs += [((0, 0, 20), (18, 0, 27)), ((18, 0, 27), (36, 0, 20)),
                     ((0, 24, 20), (18, 24, 27)), ((18, 24, 27), (36, 24, 20)),
                     ((18, 0, 27), (18, 24, 27))]
            segs += [((2, 2, 4), (34, 2, 4)), ((34, 2, 4), (34, 22, 4))]
            cls._HERO_SEGS = segs
        return cls._HERO_SEGS

    def _draw_spin(self):
        cv = self.hero
        if not cv.winfo_exists():
            return
        cv.delete("hero3d")
        import rfi_stamper.bim as bim
        c = self.theme.colors
        cam = bim.Camera(yaw=self._spin_yaw, pitch=20.0, dist=95.0,
                         target=(18.0, 12.0, 9.0))
        segs = self._hero_model()
        pts = [p for s in segs for p in s]
        scr = bim.project_points(pts, cam, 250, 240)
        w = max(cv.winfo_width(), 400)
        ox, oy = w - 300, -46
        line_col = mix(c["accent"], c["bg"], 0.45)
        for i in range(len(segs)):
            a, b = scr[2 * i], scr[2 * i + 1]
            if a[2] <= 0 or b[2] <= 0:
                continue
            cv.create_line(a[0] + ox, a[1] + oy, b[0] + ox, b[1] + oy,
                           fill=line_col, width=1.2, tags="hero3d")

    def _start_spin(self):
        if self._spin_on or fx.quality() != "full":
            if fx.quality() != "full":
                self._draw_spin()      # static frame on reduced/off
            return
        self._spin_on = True

        def cycle(t):
            if not self.hero.winfo_ismapped():
                self._spin_on = False
                fx.cancel(self.hero, "spin")
                return
            self._spin_yaw = -30.0 + 360.0 * t
            self._draw_spin()

        def again():
            self._spin_on = False
            self._start_spin()

        fx.animate(self.hero, "spin", 0.0, 1.0, 14000, cycle,
                   easing="linear", on_done=again)

    def set_project_name(self, name):
        self.proj_lbl.configure(text=name or "none open")

    def set_router(self, cb):
        self._router = cb

    def _route(self, paths):
        if self._router:
            self._router(paths)

    def show_recent(self, recent):
        for w in self.recent_box.winfo_children():
            w.destroy()
        if not recent:
            return
        ttk.Label(self.recent_box, text="Recent",
                  style="Title.TLabel").pack(anchor="w", pady=(0, 4))
        glyphs = {"markup": "✎", "plan": "▣", "combine": "⧉", "compare": "⇄",
                  "project": "◆", "loft": "⊿"}
        for item in recent[:5]:
            if not isinstance(item, dict):      # corrupt prefs -> skip
                continue
            path, kind = item.get("path", ""), item.get("kind", "markup")
            row = ttk.Frame(self.recent_box)
            row.pack(anchor="w", fill="x")
            lbl = ttk.Label(
                row, style="Sub.TLabel", cursor="hand2",
                text=f"{glyphs.get(kind, '·')}  {os.path.basename(path)}"
                     f"    —  {os.path.dirname(path)}")
            lbl.pack(anchor="w", pady=1)
            lbl.bind("<Button-1>",
                     lambda e, p=path, k=kind: self.on_recent(p, k))

    def commands(self):
        return [("New project", "Project", self.project_ops["new"]),
                ("Open project", "Project", self.project_ops["open"])]
