"""App Integrations section: file-based bridges to other software.  Planloom
is offline by policy, so every integration is a local file another tool reads
— spreadsheets, calendar apps, other PDF/project tools.  No cloud, ever."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import integrations
from . import fx
from .theme import mix, section_color
from .widgets import open_path, run_bg, toast


class IntegrationsSection(ttk.Frame):
    def __init__(self, parent, theme, status, root, get_project, on_change,
                 route_paths):
        super().__init__(parent)
        self.theme, self.status, self.root = theme, status, root
        self.get_project = get_project
        self.on_change = on_change
        self.route_paths = route_paths
        col = section_color("integrations")
        self.header = fx.GradientHeader(
            self, theme, height=58,
            stops=[(0.0, col), (1.0, mix(col, theme.colors["bg"], 0.75))],
            title="App Integrations",
            subtitle="File-based bridges: spreadsheets, calendars, other PDF "
                     "tools — everything stays on this machine")
        self.header.pack(fill="x")

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        note = ttk.Label(body, style="Muted.TLabel",
                         text="● Offline by design: an 'integration' here is a "
                              "local file another program opens. Nothing is "
                              "uploaded, synced, or phoned home.")
        note.pack(anchor="w", pady=(0, 10))

        grid = ttk.Frame(body)
        grid.pack(fill="both", expand=True)
        actions = {
            "csv-tasks": [("Export CSV", self.export_tasks),
                          ("Import CSV", self.import_tasks)],
            "csv-punch": [("Export CSV", self.export_punch)],
            "csv-budget": [("Export CSV", self.export_budget)],
            "csv-change-orders": [("Export CSV", self.export_cos)],
            "ics-schedule": [("Export .ics", self.export_ics)],
            "json-bundle": [("Export bundle", self.export_bundle),
                            ("Import bundle", self.import_bundle)],
            "drop-folder": [("Scan folder…", self.scan_folder)],
        }
        for i, conn in enumerate(integrations.REGISTRY):
            card = tk.Frame(grid, highlightthickness=1)
            card.grid(row=i // 3, column=i % 3, padx=(0, 12), pady=(0, 12),
                      sticky="nsew")
            grid.columnconfigure(i % 3, weight=1)
            theme.register(lambda c, w=card: w.configure(
                bg=c["card"], highlightbackground=c["border"]))
            t = tk.Label(card, text=conn.name, font=("Segoe UI", 11, "bold"))
            t.pack(anchor="w", padx=12, pady=(10, 1))
            dr = tk.Label(card, text=f"{conn.direction} · "
                                     f"{', '.join(conn.formats)}",
                          font=("Segoe UI", 8, "bold"))
            dr.pack(anchor="w", padx=12)
            d = tk.Label(card, text=conn.desc, font=("Segoe UI", 9),
                         wraplength=280, justify="left")
            d.pack(anchor="w", padx=12, pady=(3, 6))
            theme.register(lambda c, a=t, b=d, e=dr: (
                a.configure(bg=c["card"], fg=c["fg"]),
                b.configure(bg=c["card"], fg=c["muted"]),
                e.configure(bg=c["card"], fg=col)))
            btns = ttk.Frame(card)
            btns.pack(anchor="e", padx=10, pady=(0, 10))
            for label, cmd in actions.get(conn.key, []):
                ttk.Button(btns, text=label, command=cmd).pack(side="left",
                                                               padx=2)

    # ------------------------------------------------------------- helpers
    def _proj(self):
        proj = self.get_project()
        if not proj:
            messagebox.showinfo("Planloom", "Open or create a project first "
                                            "(Home).")
        return proj

    def _export(self, label, default_name, fn, filetypes):
        proj = self._proj()
        if not proj:
            return
        out = filedialog.asksaveasfilename(
            defaultextension=os.path.splitext(default_name)[1],
            initialfile=default_name, filetypes=filetypes)
        if not out:
            return

        def done(n, err):
            if err:
                self.status.set(f"{label} failed: {err}", "err")
                return
            toast(self.root, self.theme, f"{label}: {n} item(s) → "
                                         f"{os.path.basename(out)}")

        run_bg(self, lambda: fn(proj, out), done)

    # ------------------------------------------------------------- actions
    def export_tasks(self):
        self._export("Tasks CSV", "tasks.csv", integrations.export_tasks_csv,
                     [("CSV", "*.csv")])

    def import_tasks(self, path=None):
        proj = self._proj()
        if not proj:
            return
        p = path or filedialog.askopenfilename(filetypes=[("CSV", "*.csv")])
        if not p:
            return

        def done(n, err):
            if err:
                self.status.set(f"Import failed: {err}", "err")
                return
            toast(self.root, self.theme, f"Imported {n} task(s)")
            self.on_change()

        run_bg(self, lambda: integrations.import_tasks_csv(proj, p), done)

    def export_punch(self):
        self._export("Punch CSV", "punch_list.csv",
                     integrations.export_punch_csv, [("CSV", "*.csv")])

    def export_budget(self):
        self._export("Budget CSV", "budget.csv",
                     integrations.export_budget_csv, [("CSV", "*.csv")])

    def export_cos(self):
        self._export("Change orders CSV", "change_orders.csv",
                     integrations.export_change_orders_csv,
                     [("CSV", "*.csv")])

    def export_ics(self):
        self._export("Schedule .ics", "schedule.ics",
                     integrations.export_schedule_ics,
                     [("iCalendar", "*.ics")])

    def export_bundle(self):
        proj = self._proj()
        if not proj:
            return
        out = filedialog.asksaveasfilename(
            defaultextension=".zip", initialfile="project_bundle.zip",
            filetypes=[("Zip", "*.zip")])
        if not out:
            return

        def done(_p, err):
            if err:
                self.status.set(f"Bundle failed: {err}", "err")
                return
            toast(self.root, self.theme, "Project bundle exported")

        run_bg(self, lambda: integrations.export_bundle(proj, out), done)

    def import_bundle(self):
        p = filedialog.askopenfilename(filetypes=[("Zip", "*.zip")])
        if not p:
            return

        def done(newproj, err):
            if err:
                self.status.set(f"Bundle import failed: {err}", "err")
                return
            # merge into the open project if any, else report contents
            proj = self.get_project()
            if proj:
                moved = 0
                from ..project import KINDS
                for kind in KINDS:
                    for item in newproj.items(kind):
                        proj.add(kind, item)
                        moved += 1
                toast(self.root, self.theme,
                      f"Bundle merged: {moved} item(s)")
                self.on_change()
            else:
                toast(self.root, self.theme,
                      "Bundle read — open a project to merge it", "info")

        run_bg(self, lambda: integrations.import_bundle(p), done)

    def scan_folder(self):
        folder = filedialog.askdirectory(title="Drop folder to scan")
        if not folder:
            return

        def done(res, err):
            if err:
                self.status.set(f"Scan failed: {err}", "err")
                return
            n = sum(len(v) for v in res.values())
            toast(self.root, self.theme,
                  f"Found {len(res['plans'])} plan set(s), "
                  f"{len(res['rfis'])} RFI file(s), "
                  f"{len(res['other'])} other")
            if res["plans"] or res["rfis"]:
                if messagebox.askyesno(
                        "Drop folder",
                        f"Route {n} file(s) to the right tools now?"):
                    self.route_paths(res["plans"] + res["rfis"])

        run_bg(self, lambda: integrations.scan_drop_folder(folder), done)

    def handle_drop(self, paths):
        """A dropped CSV imports tasks directly; other files scan as a folder
        drop via Home routing."""
        csvs = [p for p in paths if p.lower().endswith(".csv")]
        if csvs:
            self.import_tasks(csvs[0])
        elif paths:
            self.route_paths(paths)

    def refresh(self):
        pass

    def commands(self):
        return [
            ("Export tasks CSV", "Integrations", self.export_tasks),
            ("Import tasks CSV", "Integrations", self.import_tasks),
            ("Export schedule to calendar (.ics)", "Integrations",
             self.export_ics),
            ("Export project bundle", "Integrations", self.export_bundle),
            ("Scan a drop folder", "Integrations", self.scan_folder),
        ]
