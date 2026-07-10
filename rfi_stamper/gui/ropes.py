"""The Ropes — hands-on training mode ("we show you the ropes").

Pedagogy straight from the field-training playbook: *tell me and I'll
forget, show me and I'll remember, involve me and I'll understand.*
Every course opens with a lesson roadmap ("after this you will be able
to…"), every step is hands-on — the spotlighted control is the REAL
control and clicking it performs the real action — and every step
carries a "Show me" fallback that animates a pointer doing it for you
before handing control back.  "You try it!" checkpoints close each
course, exactly like a good field manual.

The spotlight has two renderers (the dnd_win32 HAS_NATIVE precedent):

* **The punch (Windows, HAS_PUNCH)** — a borderless Toplevel over the
  window with ``-alpha`` for a true grey translucent tint and
  ``-transparentcolor`` punching a fully CLEAR, CLICK-THROUGH circle
  over the target: the trainee sees the real control undimmed and
  clicks it for real; the tour advances by WATCHING the step's
  ``done_when`` state (an fx-scheduled poll — never a free-running
  after loop).
* **The fallback (everywhere else, incl. headless tests)** — an
  in-window canvas: grey stippled tint built from four edge rectangles
  + four corner patches approximating square-minus-circle (tk has no
  even-odd polygons); a click inside the clear window performs the
  step's real action for the trainee.

Both draw the section-anchored ring that settles onto the target, the
arrow, and the caption card; very large targets (whole panels) use a
rectangular window.  All motion runs through fx (quality tiers
honored; "off" renders the final state statically).

Progress persists per course in ~/.planloom (prefs["ropes"]); the
first-run offer shows once (prefs["ropes_offered"]).
"""
from __future__ import annotations

import math
import sys
import tkinter as tk
from tkinter import ttk

from . import fx, prefs
from .theme import SECTIONS, mix, section_color

PAD = 14                    # spotlight padding around the target
MAX_CIRCLE = 0.42           # of the window's short side — beyond: rect mode
HAS_PUNCH = sys.platform == "win32"     # -transparentcolor punch support
_KEY = "#fe01dc"            # improbable key color the punch makes clear


# --------------------------------------------------------------------------- #
#  course definitions                                                         #
# --------------------------------------------------------------------------- #

def _resolve(app, path: str):
    obj = app
    for part in path.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


def _nav_bbox(app, key: str):
    """Root-relative bbox of a nav section zone (the REAL nav bar)."""
    zones = getattr(app.nav, "_zones", [])
    for k, a, b in zones:
        if k == key:
            cv = app.nav.canvas
            x0 = cv.winfo_rootx() - app.root.winfo_rootx()
            y0 = cv.winfo_rooty() - app.root.winfo_rooty()
            return (x0 + a, y0, x0 + b, y0 + app.nav.HEIGHT)
    return None


_SECTION_BLURB = {
    "home": "Project bar, section cards, recents, and the smart drop zone "
            "— drop any file here and Planloom routes it.",
    "field": "Tasks, the CPM Gantt (critical path in red), punch list and "
             "inspections.",
    "project": "RFI stamping, the Resolution Board, submittals, change "
               "orders, budget, documents, specs — and the Swatchbook.",
    "plans": "Plan viewing & markup, the Loft drafting board, as-built "
             "compare, and the 3D BIM viewer.",
    "reporting": "Snapshots, logs, the Designer Pickup Sheet, and "
                 "printable field forms.",
    "integrations": "File-based bridges: CSV in/out, calendars, bundles, "
                    "drop-folder scan. Offline by design.",
    "truth": "Live KPIs and honest insights over the project data.",
}


def courses(app) -> list:
    """The course catalog. Steps: {title, body, section, target, advance,
    checkpoint}.  target: callable(app) -> widget | bbox | None."""
    out = [{
        "key": "grand_tour", "title": "The grand tour",
        "section": "home",
        "roadmap": ["Know what lives in each of the seven sections",
                    "Jump anywhere with the nav bar or Ctrl+1…7",
                    "Find any feature with the command palette (Ctrl+K)"],
        "steps": [{
            "title": "Welcome to Planloom",
            "body": "This tour walks the whole workspace, hands on: click "
                    "the highlighted spot to do the real thing, or press "
                    "“Show me” and watch. Esc leaves any time — "
                    "your progress is saved.",
            "target": None, "section": None,
        }]
        + [{
            "title": SECTIONS[k]["label"],
            "body": _SECTION_BLURB[k] + "\n\nClick the highlighted nav "
                    "item to go there.",
            "target": (lambda a, kk=k: _nav_bbox(a, kk)),
            "advance": (lambda a, kk=k: a.goto(kk)),
            "done_when": (lambda a, kk=k: a._current == kk),
            "section": None,
        } for k in ("field", "project", "plans", "reporting",
                    "integrations", "truth", "home")]
        + [{
            "title": "You try it!  The command palette",
            "body": "Everything has a command: press Ctrl+K anytime and "
                    "type what you want (“stamp”, "
                    "“gantt”, “dark”…). The Training "
                    "Center lives in the Help menu whenever you want "
                    "another course.",
            "target": None, "section": None, "checkpoint": True,
        }],
    }]

    def sec_course(key, roadmap, steps):
        out.append({"key": key.split(":")[0] if ":" in key else key,
                    "title": SECTIONS[key.split(":")[0]]["label"]
                    if key.split(":")[0] in SECTIONS else key,
                    "section": key.split(":")[0], "roadmap": roadmap,
                    "steps": steps})

    def tab_step(section, nb_path, tab_path, title, body, checkpoint=False):
        # the tour brings the trainee TO the tab (on_show selects it),
        # spotlights the live panel, and Next moves on
        return {
            "title": title, "body": body, "section": section,
            "target": (lambda a: _resolve(a, tab_path)),
            "on_show": (lambda a: _resolve(a, nb_path).select(
                _resolve(a, tab_path))),
            "spot": "rect", "checkpoint": checkpoint,
        }

    sec_course("project", [
        "Scan an RFI pile and review the mapping table",
        "Stamp with verification — and read the PASS report",
        "Build cut-sheet submittals with the Swatchbook",
    ], [
        tab_step("project", "projsec.nb", "projsec.stamp",
                 "RFI stamping",
                 "The core: scan the RFI pile, review the mapping table "
                 "(the human safeguard), stamp, and verification must PASS "
                 "before anything ships."),
        tab_step("project", "projsec.nb", "projsec.swatchbook",
                 "The Swatchbook",
                 "One stamped submittal PDF per fixture tag. The Cut "
                 "Ticket feeds it from your drawing; “Scan plan "
                 "set…” reads a whole set; the Chalk Mark checks the "
                 "model-number box for you (report-only until you flip "
                 "it)."),
        {"title": "You try it!",
         "body": "Load the reference project (button on the Swatchbook "
                 "bar), then Build All into a folder — read the build log "
                 "it writes.",
         "target": None, "section": "project", "checkpoint": True},
    ])

    sec_course("plans", [
        "Open a set and calibrate scale — or let the Story Pole verify it",
        "Measure, mark up, and count fixtures with the Reed Count",
        "Draft in the Loft; tags feed the Cut Ticket on every save",
    ], [
        tab_step("plans", "plans.nb", "plans.markup",
                 "Plan viewing & markup",
                 "Open a PDF, then use the scale button: “Auto scale — "
                 "the Story Pole” verifies every sheet from its own "
                 "dimensions and refuses anything uncertain. The Reed "
                 "Count counts fixtures at that verified scale."),
        tab_step("plans", "plans.nb", "plans.loft",
                 "The Loft",
                 "Draft real plans: walls, doors, fixtures, dims. Type a "
                 "Tag on a fixture and every save writes the pull list "
                 "(the Cut Ticket) — the Swatchbook fills itself."),
        {"title": "You try it!",
         "body": "In the Loft: pick the fixture tool, choose a stencil, "
                 "type a tag like WC-1, click to place it, and save. Then "
                 "look at the Swatchbook.",
         "target": None, "section": "plans", "checkpoint": True},
    ])

    sec_course("field", [
        "Track tasks and see the critical path",
        "Keep punch and inspections current",
    ], [
        {"title": "Field Management",
         "body": "Tasks, the Gantt with real CPM (critical chain in red, "
                 "float tails hollow), punch list, inspections. The "
                 "schedule refuses dependency cycles by NAME instead of "
                 "hanging.",
         "target": (lambda a: a.field), "section": "field", "spot": "rect"},
        {"title": "You try it!",
         "body": "Add two tasks, make one depend on the other "
                 "(“<id>+2” adds lag), and watch the critical "
                 "path repaint.",
         "target": None, "section": "field", "checkpoint": True},
    ])

    sec_course("truth", [
        "Read the live KPIs and where each insight comes from",
    ], [
        {"title": "Ground Truth",
         "body": "KPIs, gauges and sparklines over the live project — and "
                 "every insight names the rule that produced it. Nothing "
                 "here is a black box.",
         "target": (lambda a: a.truth), "section": "truth", "spot": "rect"},
    ])
    return out


# --------------------------------------------------------------------------- #
#  the tour engine                                                            #
# --------------------------------------------------------------------------- #

class RopesTour:
    """One running course: spotlight overlay + hands-on step advance."""

    def __init__(self, app, course: dict, on_end=None):
        self.app = app
        self.course = course
        self.on_end = on_end
        self.i = 0
        self.cv = None
        self._top = None         # punch mode: the overlay Toplevel
        self._strips = []        # fallback mode: the four tint strips
        self._pointer = None     # the Show-me pointer (its own tiny canvas)
        self._card_frame = None
        self.drawn = 0           # draw counter (tests wait on it)
        self._watch_id = None    # the step's done_when after-chain
        self._circle = None      # (cx, cy, r) or rect bbox of the clear hole
        self._mode = "circle"

    # ------------------------------------------------------------- control
    def start(self, at: int = 0):
        self.i = max(0, min(at, len(self.course["steps"]) - 1))
        self._show_step()

    def _teardown(self):
        if self._watch_id is not None:
            try:
                self.app.root.after_cancel(self._watch_id)
            except tk.TclError:
                pass
            self._watch_id = None
        for w in ([self.cv, self._top, self._pointer, self._card_frame]
                  + self._strips):
            if w is not None and w.winfo_exists():
                w.destroy()
        self.cv = self._top = self._pointer = self._card_frame = None
        self._strips = []

    def end(self, done=False):
        fx.cancel(self)
        self._teardown()
        p = prefs.load()
        rec = p.setdefault("ropes", {}).setdefault(self.course["key"], {})
        rec["step"] = 0 if done else self.i
        rec["done"] = bool(done or rec.get("done"))
        prefs.save(p)
        if self.on_end:
            self.on_end(done)

    def _advance(self):
        step = self.course["steps"][self.i]
        adv = step.get("advance")
        try:
            if adv is not None:
                adv(self.app)
            else:
                tgt = step.get("target")
                w = tgt(self.app) if tgt else None
                if hasattr(w, "invoke"):
                    w.invoke()
        except tk.TclError:
            pass
        self._next()

    def _next(self):
        if self.i + 1 >= len(self.course["steps"]):
            self.end(done=True)
            return
        self.i += 1
        self._show_step()

    # -------------------------------------------------------------- drawing
    def _show_step(self):
        fx.cancel(self, "watch")
        self._teardown()
        step = self.course["steps"][self.i]
        sec = step.get("section")
        if sec and self.app._current != sec:
            self.app.goto(sec)
        on_show = step.get("on_show")
        if on_show is not None:
            try:
                on_show(self.app)
            except tk.TclError:
                pass
        delay = 30 if fx.quality() == "off" else 330
        self.app.root.after(delay, lambda s=step: self._draw(s))

    def _target_bbox(self, step):
        tgt = step.get("target")
        if tgt is None:
            return None
        try:
            t = tgt(self.app)
        except tk.TclError:
            t = None
        if t is None:
            return None
        if isinstance(t, (tuple, list)) and len(t) == 4:
            return tuple(t)
        try:
            root = self.app.root
            x0 = t.winfo_rootx() - root.winfo_rootx()
            y0 = t.winfo_rooty() - root.winfo_rooty()
            return (x0, y0, x0 + t.winfo_width(), y0 + t.winfo_height())
        except tk.TclError:
            return None

    def _draw(self, step):
        if step is not self.course["steps"][self.i]:
            return                              # a later step superseded us
        root = self.app.root
        root.update_idletasks()
        W = max(root.winfo_width(), 300)
        H = max(root.winfo_height(), 200)
        c = self.app.theme.colors
        tint = mix(c["bg"], "#000000", 0.55)
        anchor = section_color(step.get("section")
                               or self.app._current or "home")
        bbox = self._target_bbox(step)
        self._circle = None
        punch = HAS_PUNCH
        if punch:
            # the punch: a translucent overlay with a truly CLEAR,
            # CLICK-THROUGH circle over the target
            top = tk.Toplevel(root)
            top.overrideredirect(True)
            top.geometry(f"{W}x{H}+{root.winfo_rootx()}"
                         f"+{root.winfo_rooty()}")
            top.attributes("-topmost", True)
            top.attributes("-alpha", 0.65)
            top.attributes("-transparentcolor", _KEY)
            self._top = top
            cv = tk.Canvas(top, highlightthickness=0, bg=tint, cursor="")
            cv.pack(fill="both", expand=True)
            self.cv = cv
            if bbox is not None:
                x0, y0, x1, y1 = bbox
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                r = math.hypot(x1 - x0, y1 - y0) / 2 + PAD
                if (step.get("spot") == "rect"
                        or r > MAX_CIRCLE * min(W, H)):
                    self._mode = "rect"
                    hole = (x0 - PAD, y0 - PAD, x1 + PAD, y1 + PAD)
                    self._circle = hole
                    cv.create_rectangle(*hole, fill=_KEY, outline="")
                    ring = cv.create_rectangle(*hole, outline=anchor,
                                               width=3)
                else:
                    self._mode = "circle"
                    self._circle = (cx, cy, r)
                    cv.create_oval(cx - r, cy - r, cx + r, cy + r,
                                   fill=_KEY, outline="")
                    ring = cv.create_oval(cx - r, cy - r, cx + r, cy + r,
                                          outline=anchor, width=3)
                self._settle_ring(cv, ring)
            self._card(step, bbox, W, H, anchor, cv)
            for w in (cv, top):
                w.bind("<Escape>", lambda e: self.end())
            cv.focus_set()
        else:
            # the fallback: four opaque tint strips leave the hole
            # physically OPEN — the real control stays visible and
            # really clickable (tk widgets cannot be translucent)
            if bbox is None:
                cv = tk.Canvas(root, highlightthickness=0, bg=c["bg"])
                cv.place(x=0, y=0, relwidth=1.0, relheight=1.0)
                cv.create_rectangle(0, 0, W, H, fill=tint, outline="",
                                    stipple="gray50")
                self.cv = cv
                cv.bind("<Escape>", lambda e: self.end())
                cv.focus_set()
            else:
                self._mode = "rect"
                x0, y0, x1, y1 = bbox
                hole = (max(0, x0 - PAD), max(0, y0 - PAD),
                        min(W, x1 + PAD), min(H, y1 + PAD))
                self._circle = hole
                rx0, ry0, rx1, ry1 = hole
                for gx, gy, gw, gh, edge in (
                        (0, 0, W, ry0, "s"), (0, ry1, W, H - ry1, "n"),
                        (0, ry0, rx0, ry1 - ry0, "e"),
                        (rx1, ry0, W - rx1, ry1 - ry0, "w")):
                    if gw <= 0 or gh <= 0:
                        continue
                    st = tk.Canvas(root, highlightthickness=0, bg=c["bg"],
                                   width=gw, height=gh)
                    st.place(x=gx, y=gy, width=gw, height=gh)
                    st.create_rectangle(0, 0, gw, gh, fill=tint,
                                        outline="", stipple="gray50")
                    ln = {"s": (0, gh - 2, gw, gh - 2),
                          "n": (0, 1, gw, 1),
                          "e": (gw - 2, 0, gw - 2, gh),
                          "w": (1, 0, 1, gh)}[edge]
                    st.create_line(*ln, fill=anchor, width=3)
                    st.bind("<Escape>", lambda e: self.end())
                    self._strips.append(st)
                if self._strips:
                    self._strips[0].focus_set()
            self._card(step, bbox, W, H, anchor, None)
        # the tour advances by WATCHING the step's state — the trainee's
        # click lands on the REAL control in both renderers.  This is a
        # step-scoped, self-terminating after-chain, NOT an ambient fx
        # loop: it must keep working at quality "off" (where the fx
        # scheduler by design ticks nothing), it exists only while a
        # done_when step is on screen, and teardown cancels it.
        dw = step.get("done_when")
        if dw is not None:
            self._watch(step, dw)
        self.drawn += 1

    def _watch(self, step, dw):
        self._watch_id = None
        if step is not self.course["steps"][self.i]:
            return
        if not (self._strips
                or (self.cv is not None and self.cv.winfo_exists())):
            return
        try:
            hit = bool(dw(self.app))
        except tk.TclError:
            return
        if hit:
            self.app.root.after(120, lambda: self._next_guarded(step))
            return
        self._watch_id = self.app.root.after(
            150, lambda: self._watch(step, dw))

    def _next_guarded(self, step_ref):
        if step_ref is self.course["steps"][self.i] and (
                self._strips or self.cv is not None):
            self._next()

    def _settle_ring(self, cv, ring):
        """One calm motion: the ring settles onto the target (no loop)."""
        if fx.quality() == "off":
            return

        def settle(t, item=ring, base=self._circle, mode=self._mode):
            if not cv.winfo_exists():
                return
            g = 18.0 * (1.0 - t)
            if mode == "circle":
                bx, by, br = base
                cv.coords(item, bx - br - g, by - br - g,
                          bx + br + g, by + br + g)
            else:
                bx0, by0, bx1, by1 = base
                cv.coords(item, bx0 - g, by0 - g, bx1 + g, by1 + g)
        fx.animate(self, "ring", 0.0, 1.0, 420, settle,
                   easing="ease_in_out_cubic")

    def _card(self, step, bbox, W, H, anchor, cv):
        cw, ch = 380, 200
        if bbox is None:
            px, py = (W - cw) / 2, (H - ch) / 2
        else:
            cx = (bbox[0] + bbox[2]) / 2
            cy = (bbox[1] + bbox[3]) / 2
            px = 40 if cx > W / 2 else W - cw - 40
            py = max(24, min(H - ch - 24,
                             cy + (90 if cy < H / 2 else -ch - 90)))
        parent = cv if cv is not None else self.app.root
        card = ttk.Frame(parent, padding=14, style="Panel.TFrame")
        n = len(self.course["steps"])
        head = ("You try it!  " if step.get("checkpoint") else "")
        ttk.Label(card, text=f"{head}{step['title']}",
                  font=("Segoe UI", 12, "bold"),
                  foreground=anchor).pack(anchor="w")
        ttk.Label(card, text=step["body"], wraplength=cw - 30,
                  justify="left").pack(anchor="w", pady=(6, 8))
        row = ttk.Frame(card)
        row.pack(fill="x")
        ttk.Label(row, text=f"{self.i + 1} / {n}",
                  style="Muted.TLabel").pack(side="left")
        ttk.Button(row, text="End tour", style="Tool.TButton",
                   command=self.end).pack(side="right", padx=2)
        if bbox is not None and (step.get("advance")
                                 or step.get("target")):
            self._showme_btn = ttk.Button(
                row, text="Show me", style="Tool.TButton",
                command=lambda: self._show_me(bbox))
            self._showme_btn.pack(side="right", padx=2)
        self._next_btn = ttk.Button(row, text="Next ▸",
                                    style="Accent.TButton",
                                    command=self._next)
        self._next_btn.pack(side="right", padx=2)
        card.bind("<Escape>", lambda e: self.end())
        if cv is not None:
            cv.create_window(px, py, window=card, anchor="nw")
            if bbox is not None:                # arrow: card edge -> target
                cx = (bbox[0] + bbox[2]) / 2
                cy = (bbox[1] + bbox[3]) / 2
                ax = px + (cw if cx > px + cw else 0)
                ay = py + ch / 2
                if self._mode == "circle":
                    bx, by, r = self._circle
                    d = math.hypot(cx - ax, cy - ay) or 1.0
                    ex = cx - (cx - ax) / d * (r + 6)
                    ey = cy - (cy - ay) / d * (r + 6)
                else:
                    ex = max(self._circle[0] - 6,
                             min(cx, self._circle[2] + 6))
                    ey = (self._circle[1] - 6 if ay < cy
                          else self._circle[3] + 6)
                    if self._circle[0] <= ax <= self._circle[2]:
                        ex = ax
                cv.create_line(ax, ay, ex, ey, fill=anchor, width=3,
                               arrow=tk.LAST, arrowshape=(14, 18, 6),
                               smooth=True)
        else:
            card.place(x=px, y=py, width=cw)
        self._card_frame = card

    # --------------------------------------------------------------- input
    def _show_me(self, bbox):
        """Animate a pointer from the card to the target, then perform the
        step for the trainee and move on (show me -> I'll remember)."""
        step = self.course["steps"][self.i]
        anchor = section_color(step.get("section")
                               or self.app._current or "home")
        try:
            wx = self._showme_btn.winfo_rootx() - self.app.root.winfo_rootx()
            wy = self._showme_btn.winfo_rooty() - self.app.root.winfo_rooty()
        except tk.TclError:
            wx, wy = 40, 40
        tx = (bbox[0] + bbox[2]) / 2
        ty = (bbox[1] + bbox[3]) / 2
        # the pointer is its own tiny placed canvas: it can cross strips,
        # the open hole and the punch overlay alike
        ptr = tk.Canvas(self.app.root, width=18, height=18,
                        highlightthickness=0, bg=anchor)
        ptr.create_oval(3, 3, 15, 15, fill="#ffffff", outline="")
        ptr.place(x=wx, y=wy)
        self._pointer = ptr

        def upd(t):
            if not ptr.winfo_exists():
                return
            ptr.place(x=wx + (tx - wx) * t - 9, y=wy + (ty - wy) * t - 9)

        def done():
            self.app.root.after(
                260 if fx.quality() != "off" else 10, self._advance)

        fx.animate(self, "showme", 0.0, 1.0, 700, upd,
                   easing="ease_in_out_cubic", on_done=done)


# --------------------------------------------------------------------------- #
#  Training Center + first-run offer                                          #
# --------------------------------------------------------------------------- #

class TrainingCenter:
    """The course catalog: lesson roadmaps, progress, start/resume."""

    def __init__(self, app):
        self.app = app
        self.cat = courses(app)
        p = prefs.load().get("ropes", {})
        dlg = tk.Toplevel(app.root)
        dlg.title("Training Center — the Ropes")
        dlg.transient(app.root)
        dlg.geometry("640x420")
        self.dlg = dlg
        frm = ttk.Frame(dlg, padding=14)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="▍The Ropes", font=("Segoe UI", 15, "bold"),
                  foreground=section_color("home")).pack(anchor="w")
        ttk.Label(frm, style="Muted.TLabel",
                  text="Tell me and I'll forget · show me and I'll "
                       "remember · involve me and I'll understand"
                  ).pack(anchor="w", pady=(0, 8))
        body = ttk.Frame(frm)
        body.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(body, height=8, exportselection=False)
        self.listbox.pack(side="left", fill="both", expand=True)
        for course in self.cat:
            rec = p.get(course["key"], {})
            mark = "✓ " if rec.get("done") else (
                "⏳ " if rec.get("step") else "▢ ")
            self.listbox.insert("end",
                                f"{mark}{course['title']} "
                                f"({len(course['steps'])} steps)")
        right = ttk.Frame(body, padding=(12, 0, 0, 0))
        right.pack(side="left", fill="both", expand=True)
        ttk.Label(right, text="After this course you will be able to:",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.road = ttk.Label(right, text="", justify="left", wraplength=280)
        self.road.pack(anchor="w", pady=(4, 8))
        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(10, 0))
        self.start_btn = ttk.Button(btns, text="Start course",
                                    style="Accent.TButton",
                                    command=self.start)
        self.start_btn.pack(side="left")
        ttk.Button(btns, text="Close",
                   command=dlg.destroy).pack(side="right")
        self.listbox.bind("<<ListboxSelect>>", self._pick)
        self.listbox.selection_set(0)
        self._pick()

    def _pick(self, _e=None):
        sel = self.listbox.curselection()
        if not sel:
            return
        course = self.cat[sel[0]]
        rec = prefs.load().get("ropes", {}).get(course["key"], {})
        self.road.configure(text="\n".join(f"• {r}"
                                           for r in course["roadmap"]))
        self.start_btn.configure(
            text=(f"Resume at step {rec['step'] + 1}"
                  if rec.get("step") else "Start course"))

    def start(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        course = self.cat[sel[0]]
        rec = prefs.load().get("ropes", {}).get(course["key"], {})
        self.dlg.destroy()
        RopesTour(self.app, course).start(rec.get("step", 0))


def first_run_offer(app):
    """The one-time “want a tutorial?” prompt.  Answering either
    way sets the flag; the Training Center stays in the Help menu."""
    p = prefs.load()
    if p.get("ropes_offered"):
        return None
    p["ropes_offered"] = True
    prefs.save(p)
    dlg = tk.Toplevel(app.root)
    dlg.title("Welcome")
    dlg.transient(app.root)
    frm = ttk.Frame(dlg, padding=18)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text="New around here?",
              font=("Segoe UI", 14, "bold"),
              foreground=section_color("home")).pack(anchor="w")
    ttk.Label(frm, wraplength=360, justify="left", text=(
        "Planloom can show you the ropes: a hands-on walkthrough of every "
        "section — you click the real buttons, it spotlights the way, and "
        "a “Show me” does any step for you.\n\nCourses live in "
        "Help → Training Center whenever you want them.")
        ).pack(anchor="w", pady=(8, 12))
    row = ttk.Frame(frm)
    row.pack(fill="x")

    def go():
        dlg.destroy()
        RopesTour(app, courses(app)[0]).start(0)

    ttk.Button(row, text="Show me the ropes", style="Accent.TButton",
               command=go).pack(side="left")
    ttk.Button(row, text="Not now",
               command=dlg.destroy).pack(side="right")
    return dlg
