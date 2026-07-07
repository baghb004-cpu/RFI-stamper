"""Reporting section: one-click project reports and printable field forms."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import reports
from . import fx
from .theme import mix, section_color
from .widgets import Tooltip, open_path, run_bg, toast


class ReportsPanel(ttk.Frame):
    def __init__(self, parent, theme, status, root, get_project, project_sec):
        super().__init__(parent, padding=10)
        self.theme, self.status, self.root = theme, status, root
        self.get_project = get_project
        self.project_sec = project_sec       # ProjectSection (for stamp data)
        col = section_color("reporting")
        ttk.Label(self, text="▍Reports", font=("Segoe UI", 14, "bold"),
                  foreground=col).pack(anchor="w")
        ttk.Label(self, style="Muted.TLabel",
                  text="Every report is a clean, paginated PDF written "
                       "locally — hand it to anyone.").pack(anchor="w",
                                                            pady=(0, 10))
        grid = ttk.Frame(self)
        grid.pack(anchor="w")
        cards = [
            ("Project Snapshot", "KPIs, tasks, punch, change orders and "
             "budget bars on one deck.", self.snapshot),
            ("RFI Log", "Every RFI: sheets, status, answered — from the "
             "latest stamp run.", self.rfi_log),
            ("Designer Pickup Sheet", "What to fix per sheet, with next "
             "steps, from the Resolution Board.", self.pickup),
            ("Submittal Log", "The register as a clean log (parse it under "
             "Project Management first).", self.submittal_log),
        ]
        for i, (name, desc, cmd) in enumerate(cards):
            card = tk.Frame(grid, bd=0, highlightthickness=1)
            card.grid(row=i // 2, column=i % 2, padx=(0, 14), pady=(0, 12),
                      sticky="nsew")
            self.theme.register(lambda c, w=card: w.configure(
                bg=c["card"], highlightbackground=c["border"]))
            t = tk.Label(card, text=name, font=("Segoe UI", 12, "bold"))
            t.pack(anchor="w", padx=12, pady=(10, 2))
            d = tk.Label(card, text=desc, font=("Segoe UI", 9),
                         wraplength=290, justify="left")
            d.pack(anchor="w", padx=12)
            self.theme.register(lambda c, a=t, b=d: (
                a.configure(bg=c["card"], fg=c["fg"]),
                b.configure(bg=c["card"], fg=c["muted"])))
            ttk.Button(card, text="Generate PDF", style="Accent.TButton",
                       command=cmd).pack(anchor="e", padx=12, pady=10)

    def _save_as(self, name):
        return filedialog.asksaveasfilename(
            defaultextension=".pdf", initialfile=name,
            filetypes=[("PDF", "*.pdf")])

    def _bg(self, label, work, out):
        def done(_res, err):
            if err:
                self.status.set(f"{label} failed: {err}", "err")
                return
            toast(self.root, self.theme, f"{label} ready")
            open_path(out)
        self.status.set(f"Building {label}…")
        run_bg(self, work, done)

    def snapshot(self):
        proj = self.get_project()
        if not proj:
            messagebox.showinfo("Reports", "Open or create a project first "
                                           "(Home).")
            return
        out = self._save_as("project_snapshot.pdf")
        if out:
            self._bg("Project Snapshot",
                     lambda: reports.project_snapshot_pdf(proj, out), out)

    def rfi_log(self):
        rep = self.project_sec.stamp.last_report
        if not rep:
            messagebox.showinfo("Reports", "Run a stamp first (Project "
                                           "Management → RFIs).")
            return
        out = self._save_as("RFI_log.pdf")
        if out:
            from .. import transmittal
            self._bg("RFI Log", lambda: transmittal.rfi_log_pdf(rep, out), out)

    def pickup(self):
        self.project_sec.board.pickup()

    def submittal_log(self):
        self.project_sec.submittals.log_pdf()


class FormsPanel(ttk.Frame):
    def __init__(self, parent, theme, status, root):
        super().__init__(parent, padding=10)
        self.theme, self.status, self.root = theme, status, root
        col = section_color("reporting")
        ttk.Label(self, text="▍Forms", font=("Segoe UI", 14, "bold"),
                  foreground=col).pack(anchor="w")
        ttk.Label(self, style="Muted.TLabel",
                  text="Print a blank form for the clipboard, or fill it here "
                       "and export the finished PDF.").pack(anchor="w",
                                                            pady=(0, 8))
        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        body.add(left, weight=1)
        self.listbox = tk.Listbox(left, height=10, activestyle="none")
        self.listbox.pack(fill="both", expand=True)
        theme.register(lambda c: theme.style_listbox(self.listbox))
        for t in reports.BUILTIN_TEMPLATES:
            self.listbox.insert("end", f"  {t.name}")
        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=6)
        ttk.Button(btns, text="Print blank…", command=self.blank).pack(
            side="left")
        fill_b = ttk.Button(btns, text="Fill & export…",
                            style="Accent.TButton", command=self.fill)
        fill_b.pack(side="left", padx=6)
        Tooltip(fill_b, "Opens the form fields on the right; export writes "
                        "the completed PDF.", theme)
        self.form_host = ttk.Frame(body)
        body.add(self.form_host, weight=2)
        self._vars = {}
        self._tpl = None

    def _picked(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("Forms", "Pick a form template first.")
            return None
        return reports.BUILTIN_TEMPLATES[sel[0]]

    def blank(self):
        tpl = self._picked()
        if not tpl:
            return
        out = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            initialfile=tpl.name.lower().replace(" ", "_") + ".pdf",
            filetypes=[("PDF", "*.pdf")])
        if not out:
            return

        def done(_r, err):
            if err:
                self.status.set(f"Form failed: {err}", "err")
                return
            toast(self.root, self.theme, f"{tpl.name} (blank) ready")
            open_path(out)

        run_bg(self, lambda: reports.render_blank_form(tpl, out), done)

    def fill(self):
        tpl = self._picked()
        if not tpl:
            return
        self._tpl = tpl
        for w in self.form_host.winfo_children():
            w.destroy()
        frm = ttk.Frame(self.form_host, padding=10)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=tpl.name, style="Title.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self._vars = {}
        for i, f in enumerate(tpl.fields, start=1):
            ttk.Label(frm, text=f.label).grid(row=i, column=0, sticky="nw",
                                              pady=2, padx=(0, 8))
            if f.kind == "multiline":
                w = tk.Text(frm, width=46, height=3, font=("Segoe UI", 10))
                self.theme.style_text(w)
                self._vars[f.key] = w
            elif f.kind == "check":
                v = tk.BooleanVar(value=False)
                w = ttk.Checkbutton(frm, variable=v)
                self._vars[f.key] = v
            elif f.kind == "choice":
                v = tk.StringVar(value=f.default or (f.choices[0]
                                                     if f.choices else ""))
                w = ttk.Combobox(frm, textvariable=v, values=f.choices,
                                 state="readonly", width=26)
                self._vars[f.key] = v
            else:
                v = tk.StringVar(value=f.default)
                w = ttk.Entry(frm, textvariable=v, width=46)
                self._vars[f.key] = v
            w.grid(row=i, column=1, sticky="ew", pady=2)
        frm.columnconfigure(1, weight=1)
        ttk.Button(frm, text="Export filled PDF…", style="Accent.TButton",
                   command=self._export).grid(row=len(tpl.fields) + 1,
                                              column=1, sticky="e",
                                              pady=(10, 0))

    def _export(self):
        tpl = self._tpl
        values = {}
        for k, v in self._vars.items():
            values[k] = (v.get("1.0", "end").strip() if isinstance(v, tk.Text)
                         else v.get())
        out = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            initialfile=tpl.name.lower().replace(" ", "_") + "_filled.pdf",
            filetypes=[("PDF", "*.pdf")])
        if not out:
            return

        def done(_r, err):
            if err:
                self.status.set(f"Form failed: {err}", "err")
                return
            toast(self.root, self.theme, f"{tpl.name} exported")
            open_path(out)

        run_bg(self, lambda: reports.render_filled_form(tpl, values, out),
               done)


class ReportingSection(ttk.Frame):
    def __init__(self, parent, theme, status, root, get_project, project_sec):
        super().__init__(parent)
        col = section_color("reporting")
        self.header = fx.GradientHeader(
            self, theme, height=58,
            stops=[(0.0, col), (1.0, mix(col, theme.colors["bg"], 0.75))],
            title="Reporting",
            subtitle="Snapshots, logs, pickup sheets and printable field "
                     "forms — every one a local PDF")
        self.header.pack(fill="x")
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=4, pady=4)
        self.reports = ReportsPanel(nb, theme, status, root, get_project,
                                    project_sec)
        nb.add(self.reports, text="  Reports  ")
        self.forms = FormsPanel(nb, theme, status, root)
        nb.add(self.forms, text="  Forms  ")

    def refresh(self):
        pass

    def commands(self):
        return [
            ("Project snapshot PDF", "Reporting", self.reports.snapshot),
            ("RFI log PDF", "Reporting", self.reports.rfi_log),
            ("Print a blank form", "Reporting", self.forms.blank),
        ]
