"""Field Management section: Task Management, Scheduling (animated Gantt),
Punch List, Inspections, and the Daybook progress journal — all local."""
from __future__ import annotations

import datetime as _dt
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..project import Inspection, PunchItem, ScheduleItem, Task
from . import fx
from .crud import CrudPanel, Field
from .theme import mix, section_color
from .widgets import Tooltip, make_tree, open_path, run_bg, toast


class ScheduleView(ttk.Frame):
    """Canvas Gantt: month grid, colored bars with % complete fill, today
    line.  Bars sweep in with an eased animation on refresh."""

    ROW_H = 30
    LEFT_W = 190

    def __init__(self, parent, theme, get_project):
        super().__init__(parent)
        self.theme = theme
        self.get_project = get_project
        self.canvas = tk.Canvas(self, height=260, highlightthickness=0)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        theme.register(lambda c: self.refresh(animate=False))
        self.canvas.bind("<Configure>", lambda e: self.refresh(animate=False))
        self._anim_t = 1.0

    def refresh(self, animate=True):
        if animate and fx.quality() != "off":
            fx.animate(self.canvas, "gantt", 0.0, 1.0, 550,
                       self._draw_at, easing="ease_out_quad")
        else:
            self._draw_at(1.0)

    def _draw_at(self, t):
        self._anim_t = t
        cv = self.canvas
        if not cv.winfo_exists():
            return
        c = self.theme.colors
        cv.delete("all")
        cv.configure(bg=c["panel"])
        proj = self.get_project()
        items = [it for it in (proj.items("schedule") if proj else [])
                 if it.start and it.end]
        w = max(cv.winfo_width(), 400)
        if not items:
            cv.create_text(w / 2, 110, fill=c["muted"], font=("Segoe UI", 12),
                           text="No scheduled activities yet — add one below.\n"
                                "Bars, % complete, and the today line appear "
                                "here.", justify="center")
            return
        try:
            d0 = min(_dt.date.fromisoformat(i.start) for i in items)
            d1 = max(_dt.date.fromisoformat(i.end) for i in items)
        except ValueError:
            cv.create_text(w / 2, 110, fill=c["err"],
                           text="A schedule item has a bad date "
                                "(use YYYY-MM-DD).")
            return
        d0 -= _dt.timedelta(days=2)
        d1 += _dt.timedelta(days=3)
        span = max((d1 - d0).days, 1)
        px_day = (w - self.LEFT_W - 16) / span
        top = 28
        h = top + len(items) * self.ROW_H + 10
        cv.configure(scrollregion=(0, 0, w, h))

        def x_of(date):
            return self.LEFT_W + (date - d0).days * px_day

        # weekend shading behind everything else
        wknd = mix(c["border"], c["panel"], 0.72)
        d = d0
        while d <= d1:
            if d.weekday() >= 5:
                x = x_of(d)
                cv.create_rectangle(x, top, x + px_day, h, fill=wknd,
                                    outline="")
            d += _dt.timedelta(days=1)

        # month/week grid
        d = d0
        while d <= d1:
            if d.day == 1 or d == d0:
                x = x_of(d)
                cv.create_line(x, top - 6, x, h, fill=c["border"])
                cv.create_text(x + 4, 12, anchor="w", fill=c["muted"],
                               font=("Segoe UI", 8, "bold"),
                               text=d.strftime("%b %Y").upper())
            elif d.weekday() == 0:
                x = x_of(d)
                cv.create_line(x, top, x, h, fill=mix(c["border"], c["panel"],
                                                      0.5))
            d += _dt.timedelta(days=1)

        base = section_color("field")
        for row, it in enumerate(items):
            y = top + row * self.ROW_H
            cv.create_text(8, y + self.ROW_H / 2, anchor="w", fill=c["fg"],
                           font=("Segoe UI", 9), width=self.LEFT_W - 14,
                           text=it.title)
            try:
                s = _dt.date.fromisoformat(it.start)
                e = _dt.date.fromisoformat(it.end)
            except ValueError:
                continue
            x0, x1 = x_of(s), x_of(e + _dt.timedelta(days=1))
            x1 = x0 + (x1 - x0) * self._anim_t          # sweep-in
            color = it.color or base
            cv.create_rectangle(x0, y + 6, x1, y + self.ROW_H - 6,
                                fill=mix(color, c["panel"], 0.55),
                                outline=color, width=1.2)
            if it.pct:
                cv.create_rectangle(
                    x0, y + 6, x0 + (x1 - x0) * min(it.pct, 100) / 100.0,
                    y + self.ROW_H - 6, fill=color, outline="")
            if it.crew:
                cv.create_text(min(x1 + 6, w - 8), y + self.ROW_H / 2,
                               anchor="w", fill=c["muted"],
                               font=("Segoe UI", 8), text=it.crew)
        today = _dt.date.today()
        if d0 <= today <= d1:
            x = x_of(today)
            cv.create_line(x, top - 6, x, h, fill=c["accent"], width=2,
                           dash=(5, 3))
            cv.create_text(x + 4, top - 12, anchor="w", fill=c["accent"],
                           font=("Segoe UI", 8, "bold"), text="TODAY")


class DaybookPanel(ttk.Frame):
    """The foreman's daily journal: what happened, measurements taken,
    comments, photo references — one PDF log away from the owner's meeting."""

    WEATHER = ("clear", "cloudy", "rain", "snow", "wind", "heat", "freeze")

    def __init__(self, parent, theme, status, root, get_project, author=""):
        super().__init__(parent, padding=(10, 6))
        self.theme, self.status, self.root = theme, status, root
        self.get_project = get_project
        self.author = author
        self.store = None
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Label(bar, text="▍Daybook", font=("Segoe UI", 14, "bold"),
                  foreground=section_color("field")).pack(side="left")
        self.counts_lbl = ttk.Label(bar, text="", style="Muted.TLabel")
        self.counts_lbl.pack(side="left", padx=10)
        ttk.Button(bar, text="Daybook PDF…", style="Accent.TButton",
                   command=self.export_pdf).pack(side="right", padx=2)
        ttk.Button(bar, text="＋ New entry", command=self.new_entry
                   ).pack(side="right", padx=2)
        ttk.Button(bar, text="Delete", command=self.delete_sel
                   ).pack(side="right", padx=2)
        frame, self.tree = make_tree(
            self, theme,
            [("date", "DATE"), ("crew", "CREW"), ("weather", "WEATHER"),
             ("summary", "WORK PERFORMED"), ("meas", "MEASUREMENTS"),
             ("photos", "PHOTOS")],
            (90, 110, 80, 320, 190, 60), height=12)
        frame.pack(fill="both", expand=True, pady=6)
        self.tree.bind("<Double-1>", self._open_photos)
        Tooltip(self.tree, "Photos are file references — nothing is copied "
                           "or uploaded.  Double-click an entry to view its "
                           "photos in Lookout (360° panoramas supported).",
                theme)

    def _open_photos(self, _e=None):
        """Double-click an entry → its photos open in the Lookout viewer;
        2:1 equirectangular shots become drag-to-look-around panoramas."""
        store = self._ensure_store()
        sel = self.tree.selection()
        if not store or not sel:
            return
        entry = store.get(sel[0])
        if not entry or not entry.photos:
            return
        from . import pano
        existing = [p for p in entry.photos if os.path.exists(p)]
        if not existing:
            messagebox.showinfo(
                "Lookout", "This entry's photo files aren't reachable from "
                           "this machine (they're stored as references).")
            return
        if len(existing) == 1:
            if pano.open_lookout(self.winfo_toplevel(), self.theme,
                                 existing[0]) is None:
                messagebox.showwarning("Lookout",
                                       "Could not read that image.")
            return
        dlg = tk.Toplevel(self)
        dlg.title("Lookout — pick a photo")
        dlg.transient(self.winfo_toplevel())
        lb = tk.Listbox(dlg, width=70, height=min(12, len(existing)))
        self.theme.style_listbox(lb)
        lb.pack(fill="both", expand=True, padx=10, pady=10)
        for p in existing:
            lb.insert("end", p)

        def open_sel(_e=None):
            cur = lb.curselection()
            if cur:
                pano.open_lookout(self.winfo_toplevel(), self.theme,
                                  existing[cur[0]])
        lb.bind("<Double-Button-1>", open_sel)
        ttk.Button(dlg, text="Open in Lookout", command=open_sel
                   ).pack(pady=(0, 10))

    def _ensure_store(self):
        proj = self.get_project()
        if not proj or not proj.path:
            return None
        from .. import daybook
        if self.store is None or self.store.base_path != proj.path:
            self.store = daybook.DaybookStore(proj.path)
        return self.store

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        store = self._ensure_store()
        if not store:
            self.counts_lbl.configure(
                text="open or create a project (Home) to start the journal")
            return
        for e in store.by_date():
            self.tree.insert("", "end", iid=e.id, values=(
                e.date, e.crew, e.weather, e.summary,
                " | ".join(e.measurements)[:80], len(e.photos)))
        c = store.counts()
        self.counts_lbl.configure(
            text=f"{c['entries']} entr(ies) · {c['days']} day(s) · "
                 f"{c['photos']} photo ref(s)")

    def new_entry(self):
        store = self._ensure_store()
        if not store:
            messagebox.showinfo("Daybook", "Open or create a project first "
                                           "(Home).")
            return
        dlg = tk.Toplevel(self)
        dlg.title("Daybook — new entry")
        dlg.transient(self.winfo_toplevel())
        dlg.grab_set()
        frm = ttk.Frame(dlg, padding=14)
        frm.pack(fill="both", expand=True)
        vars_ = {"date": tk.StringVar(value=_dt.date.today().isoformat()),
                 "crew": tk.StringVar(), "weather": tk.StringVar(
                     value=self.WEATHER[0])}
        ttk.Label(frm, text="Date").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(frm, textvariable=vars_["date"], width=14).grid(
            row=0, column=1, sticky="w")
        ttk.Label(frm, text="Crew").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(frm, textvariable=vars_["crew"], width=30).grid(
            row=1, column=1, sticky="ew")
        ttk.Label(frm, text="Weather").grid(row=2, column=0, sticky="w",
                                            pady=2)
        ttk.Combobox(frm, textvariable=vars_["weather"],
                     values=list(self.WEATHER), state="readonly", width=12
                     ).grid(row=2, column=1, sticky="w")
        ttk.Label(frm, text="Work performed").grid(row=3, column=0,
                                                   sticky="nw", pady=2)
        summary = tk.Text(frm, width=48, height=3, font=("Segoe UI", 10))
        self.theme.style_text(summary)
        summary.grid(row=3, column=1, sticky="ew", pady=2)
        ttk.Label(frm, text="Measurements\n(one per line)").grid(
            row=4, column=0, sticky="nw", pady=2)
        meas = tk.Text(frm, width=48, height=3, font=("Segoe UI", 10))
        self.theme.style_text(meas)
        meas.grid(row=4, column=1, sticky="ew", pady=2)
        ttk.Label(frm, text="Comments").grid(row=5, column=0, sticky="nw",
                                             pady=2)
        comments = tk.Text(frm, width=48, height=2, font=("Segoe UI", 10))
        self.theme.style_text(comments)
        comments.grid(row=5, column=1, sticky="ew", pady=2)
        photos: list[str] = []
        plbl = ttk.Label(frm, text="0 photo ref(s)", style="Muted.TLabel")

        def add_photos():
            for p in filedialog.askopenfilenames(
                    parent=dlg, filetypes=[("Images",
                                            "*.jpg *.jpeg *.png *.heic "
                                            "*.tif *.tiff"), ("All", "*.*")]):
                photos.append(p)
            plbl.configure(text=f"{len(photos)} photo ref(s)")

        row6 = ttk.Frame(frm)
        row6.grid(row=6, column=1, sticky="w", pady=4)
        ttk.Button(row6, text="Add photos…", command=add_photos
                   ).pack(side="left")
        plbl.pack(side="left", padx=8)

        def save():
            store.add(
                date=vars_["date"].get().strip(),
                crew=vars_["crew"].get().strip(),
                weather=vars_["weather"].get(),
                summary=summary.get("1.0", "end").strip(),
                comments=comments.get("1.0", "end").strip(),
                measurements=[ln.strip() for ln in
                              meas.get("1.0", "end").splitlines()
                              if ln.strip()],
                photos=list(photos), author=self.author)
            dlg.destroy()
            self.refresh()
            toast(self.root, self.theme, "Daybook entry logged")

        btns = ttk.Frame(frm)
        btns.grid(row=7, column=1, sticky="e", pady=(8, 0))
        ttk.Button(btns, text="Log it", style="Accent.TButton",
                   command=save).pack(side="right")
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(
            side="right", padx=6)
        frm.columnconfigure(1, weight=1)

    def delete_sel(self):
        store = self._ensure_store()
        if not store:
            return
        sel = self.tree.selection()
        if sel and messagebox.askyesno("Daybook",
                                       f"Delete {len(sel)} entr(ies)?"):
            for iid in sel:
                store.remove(iid)
            self.refresh()

    def export_pdf(self):
        store = self._ensure_store()
        if not store or not store.entries:
            messagebox.showinfo("Daybook", "Log an entry first.")
            return
        out = filedialog.asksaveasfilename(
            defaultextension=".pdf", initialfile="daybook.pdf",
            filetypes=[("PDF", "*.pdf")])
        if not out:
            return
        from .. import daybook

        def done(_r, err):
            if err:
                self.status.set(f"Daybook PDF failed: {err}", "err")
                return
            toast(self.root, self.theme, "Daybook PDF ready")
            open_path(out)

        run_bg(self, lambda: daybook.daybook_pdf(store, out), done)


class FieldSection(ttk.Frame):
    def __init__(self, parent, theme, status, get_project, on_change,
                 root=None, author=""):
        super().__init__(parent)
        self.header = fx.GradientHeader(
            self, theme, height=58,
            stops=[(0.0, section_color("field")),
                   (1.0, mix(section_color("field"), theme.colors["bg"], 0.75))],
            title="Field Management",
            subtitle="Tasks · schedule · punch list · inspections — all local, "
                     "all offline")
        self.header.pack(fill="x")
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=4, pady=4)
        self.nb = nb

        self.tasks = CrudPanel(
            nb, theme, status, get_project, "tasks", "Task Management",
            columns=[("title", "TASK", 240), ("assignee", "ASSIGNEE", 110),
                     ("status", "STATUS", 80), ("priority", "PRI", 60),
                     ("due", "DUE", 90), ("linked_sheet", "SHEET", 80)],
            fields=[Field("title", "Task"), Field("desc", "Details",
                                                  "multiline"),
                    Field("assignee", "Assignee"),
                    Field("status", "Status", "choice",
                          ["todo", "doing", "blocked", "done"]),
                    Field("priority", "Priority", "choice",
                          ["low", "med", "high"]),
                    Field("due", "Due", "date"),
                    Field("linked_sheet", "Linked sheet")],
            factory=Task, section="field",
            empty_hint="Assign work to the crew and link each task to the "
                       "sheet it lives on.",
            on_change=on_change)
        nb.add(self.tasks, text="  Task Management  ")

        sched = ttk.Frame(nb)
        self.gantt = ScheduleView(sched, theme, get_project)
        self.gantt.pack(fill="both", expand=True, padx=4, pady=(4, 0))
        self.schedule = CrudPanel(
            sched, theme, status, get_project, "schedule", "Activities",
            columns=[("title", "ACTIVITY", 240), ("start", "START", 90),
                     ("end", "END", 90), ("crew", "CREW", 120),
                     ("pct", "%", 50)],
            fields=[Field("title", "Activity"), Field("start", "Start", "date"),
                    Field("end", "End", "date"), Field("crew", "Crew"),
                    Field("pct", "% complete", "number")],
            factory=ScheduleItem, section="field",
            on_change=lambda: (self.gantt.refresh(), on_change()))
        self.schedule.pack(fill="both", expand=True)
        nb.add(sched, text="  Scheduling  ")

        self.punch = CrudPanel(
            nb, theme, status, get_project, "punch", "Punch List",
            columns=[("title", "ITEM", 260), ("location", "LOCATION", 140),
                     ("sheet", "SHEET", 80), ("status", "STATUS", 80),
                     ("assignee", "ASSIGNEE", 110)],
            fields=[Field("title", "Punch item"),
                    Field("location", "Location"),
                    Field("sheet", "Sheet"),
                    Field("status", "Status", "choice",
                          ["open", "ready", "closed"]),
                    Field("assignee", "Assignee")],
            factory=PunchItem, section="field",
            empty_hint="Tip: numbered punch dots placed in Plans & BIM → Plan "
                       "Viewing export straight to CSV; log the ones that "
                       "need chasing here.",
            on_change=on_change)
        nb.add(self.punch, text="  Punch List  ")

        self.inspections = CrudPanel(
            nb, theme, status, get_project, "inspections", "Inspections",
            columns=[("title", "INSPECTION", 240), ("date", "DATE", 90),
                     ("inspector", "INSPECTOR", 130),
                     ("status", "STATUS", 90)],
            fields=[Field("title", "Inspection"), Field("date", "Date",
                                                        "date"),
                    Field("inspector", "Inspector"),
                    Field("status", "Status", "choice",
                          ["scheduled", "passed", "failed"]),
                    Field("notes", "Notes", "multiline")],
            factory=Inspection, section="field",
            empty_hint="Print a Safety Inspection form from Reporting → "
                       "Forms, walk the site, then log the result here.",
            on_change=on_change)
        nb.add(self.inspections, text="  Inspection  ")

        self.daybook = DaybookPanel(nb, theme, status, root or self,
                                    get_project, author=author)
        nb.add(self.daybook, text="  Daybook  ")

    def refresh(self):
        for p in (self.tasks, self.schedule, self.punch, self.inspections):
            p.refresh()
        self.gantt.refresh(animate=False)
        self.daybook.refresh()

    def commands(self):
        return [
            ("Add task", "Field", self.tasks.add_dialog),
            ("Add schedule activity", "Field", self.schedule.add_dialog),
            ("Add punch item", "Field", self.punch.add_dialog),
            ("Add inspection", "Field", self.inspections.add_dialog),
            ("Daybook: new entry", "Field", self.daybook.new_entry),
            ("Daybook: export PDF", "Field", self.daybook.export_pdf),
        ]
