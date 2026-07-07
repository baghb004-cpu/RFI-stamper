"""Field Management section: Task Management, Scheduling (animated Gantt),
Punch List, and Inspections — all driven by the shared local Project store."""
from __future__ import annotations

import datetime as _dt
import tkinter as tk
from tkinter import ttk

from ..project import Inspection, PunchItem, ScheduleItem, Task
from . import fx
from .crud import CrudPanel, Field
from .theme import mix, section_color


def _iso(d: _dt.date) -> str:
    return d.isoformat()


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


class FieldSection(ttk.Frame):
    def __init__(self, parent, theme, status, get_project, on_change):
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

    def refresh(self):
        for p in (self.tasks, self.schedule, self.punch, self.inspections):
            p.refresh()
        self.gantt.refresh(animate=False)

    def commands(self):
        return [
            ("Add task", "Field", self.tasks.add_dialog),
            ("Add schedule activity", "Field", self.schedule.add_dialog),
            ("Add punch item", "Field", self.punch.add_dialog),
            ("Add inspection", "Field", self.inspections.add_dialog),
        ]
