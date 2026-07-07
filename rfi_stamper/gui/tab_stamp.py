"""Stamp RFIs tab: plan set + RFI files -> mapped, stamped, pixel-verified.
Cliff-note summaries are generated fully offline (rfi_stamper.summarize)."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from .. import pipeline
from ..summarize import OfflineSummarizer
from . import dnd
from .widgets import DropZone, LogConsole, Tooltip, make_tree, open_path, run_bg


class StampTab(ttk.Frame):
    def __init__(self, parent, theme, status):
        super().__init__(parent, padding=10)
        self.theme = theme
        self.status = status
        self.index = None
        self.rows = None
        self.out_path = None

        # ---------------------------------------------------------- plan set
        r1 = ttk.Frame(self)
        r1.pack(fill="x")
        ttk.Label(r1, text="Plan set PDF:").pack(side="left")
        self.plan_var = tk.StringVar()
        e = ttk.Entry(r1, textvariable=self.plan_var)
        e.pack(side="left", fill="x", expand=True, padx=6)
        dnd.enable_drop(e, lambda p: p and self.plan_var.set(p[0]), exts=(".pdf",))
        ttk.Button(r1, text="Browse…", command=self.pick_plan).pack(side="left")

        DropZone(self, theme, "Drop the plan-set PDF here",
                 lambda p: p and self.plan_var.set(p[0]), exts=(".pdf",),
                 browse=self.pick_plan, height=40).pack(fill="x", pady=(4, 8))

        # --------------------------------------------------------- RFI files
        r2 = ttk.LabelFrame(self, text="RFI files — any firm's format: PDFs, "
                                       "export packages, text dumps, folders")
        r2.pack(fill="x", pady=4)
        left = ttk.Frame(r2)
        left.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        self.rfi_list = tk.Listbox(left, height=5, selectmode="extended")
        self.rfi_list.pack(fill="both", expand=True)
        theme.register(lambda c: theme.style_listbox(self.rfi_list))
        dnd.enable_drop(self.rfi_list, self.add_paths)
        DropZone(left, theme, "Drop RFI files / folders here", self.add_paths,
                 browse=self.add_files, height=34).pack(fill="x", pady=(4, 0))
        bcol = ttk.Frame(r2)
        bcol.pack(side="left", padx=4, pady=4)
        ttk.Button(bcol, text="Add files…", command=self.add_files).pack(fill="x")
        ttk.Button(bcol, text="Add folder…", command=self.add_folder).pack(fill="x", pady=2)
        ttk.Button(bcol, text="Remove", command=self.remove_sel).pack(fill="x")

        # ------------------------------------------------------------- scan
        r3 = ttk.Frame(self)
        r3.pack(fill="x", pady=4)
        ttk.Label(r3, style="Muted.TLabel",
                  text="Notes are written offline — no data leaves this machine."
                  ).pack(side="left")
        self.scan_btn = ttk.Button(r3, text="1  Scan & map", style="Accent.TButton",
                                   command=self.scan)
        self.scan_btn.pack(side="right")

        # ---------------------------------------------------------- mapping
        r4 = ttk.LabelFrame(self, text="Mapping review — double-click a Sheets "
                                       "cell to edit (semicolon-separated)")
        r4.pack(fill="both", expand=True, pady=4)
        frame, self.tree = make_tree(
            r4, theme,
            [("rfi", "RFI"), ("title", "TITLE"), ("sheets", "SHEETS"),
             ("via", "VIA"), ("answered", "ANSWERED")],
            (55, 320, 210, 80, 70), height=8)
        frame.pack(fill="both", expand=True, padx=4, pady=4)
        self.tree.bind("<Double-1>", self.edit_cell)
        Tooltip(self.tree, "via: planref = labeled reference (high confidence), "
                           "body = token found in text (glance at it), "
                           "manual = your edit, unmatched = goes to appendix",
                theme)

        # -------------------------------------------------------------- run
        r5 = ttk.Frame(self)
        r5.pack(fill="x", pady=4)
        self.run_btn = ttk.Button(r5, text="2  Stamp & verify", state="disabled",
                                  style="Accent.TButton", command=self.stamp)
        self.run_btn.pack(side="left")
        self.open_btn = ttk.Button(r5, text="Open result", state="disabled",
                                   command=lambda: self.out_path
                                   and open_path(self.out_path))
        self.open_btn.pack(side="left", padx=6)
        self.csv_btn = ttk.Button(r5, text="Export mapping CSV…", state="disabled",
                                  command=self.export_csv)
        self.csv_btn.pack(side="left", padx=6)

        self.log = LogConsole(self, theme, height=7)
        self.log.pack(fill="both", expand=True, pady=(4, 0))

    # ------------------------------------------------------------- commands
    def commands(self):
        return [
            ("Scan & map RFIs", "Stamp", self.scan),
            ("Stamp & verify", "Stamp", self.stamp),
            ("Export mapping CSV", "Stamp", self.export_csv),
            ("Pick plan set PDF", "Stamp", self.pick_plan),
            ("Add RFI files", "Stamp", self.add_files),
        ]

    # --------------------------------------------------------------- inputs
    def pick_plan(self):
        p = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf")])
        if p:
            self.plan_var.set(p)

    def add_paths(self, paths):
        for p in paths:
            self.rfi_list.insert("end", p)

    def add_files(self):
        self.add_paths(filedialog.askopenfilenames(
            filetypes=[("RFI files", "*.pdf *.zip *.txt"), ("All", "*.*")]))

    def add_folder(self):
        p = filedialog.askdirectory()
        if p:
            self.rfi_list.insert("end", p)

    def remove_sel(self):
        for i in reversed(self.rfi_list.curselection()):
            self.rfi_list.delete(i)

    # ---------------------------------------------------------------- scan
    def scan(self):
        plan = self.plan_var.get().strip()
        rfis = list(self.rfi_list.get(0, "end"))
        if not plan or not rfis:
            messagebox.showwarning("RFI Stamper",
                                   "Pick a plan set PDF and add RFI files first.")
            return
        self.scan_btn.configure(state="disabled")
        self.status.set("Scanning…")

        def work():
            return pipeline.scan(plan, rfis, log=self.log.say)

        def done(result, err):
            self.scan_btn.configure(state="normal")
            if err:
                self.log.say(f"!! scan failed: {err}")
                self.status.set("Scan failed — see log", "err")
                return
            self.index, self.rows = result
            self.fill_tree()
            self.run_btn.configure(state="normal")
            self.csv_btn.configure(state="normal")
            self.status.set(f"{len(self.rows)} RFI(s) mapped — review, then stamp", "ok")

        run_bg(self, work, done)

    def fill_tree(self):
        self.tree.delete(*self.tree.get_children())
        for row in self.rows:
            sheets = ";".join(self.index.info(p).sheet for p in row.pages)
            self.tree.insert("", "end", iid=row.record.number, values=(
                row.record.number, row.record.title, sheets, row.via,
                "yes" if row.record.has_answer else "no"))

    def edit_cell(self, event):
        item = self.tree.identify_row(event.y)
        if not item or self.tree.identify_column(event.x) != "#3" or not self.rows:
            return
        cur = self.tree.set(item, "sheets")
        new = simpledialog.askstring(
            "Edit sheets", f"Sheets for RFI {item} (semicolon-separated):",
            initialvalue=cur, parent=self)
        if new is None:
            return
        row = next(r for r in self.rows if r.record.number == item)
        pages, bad = [], []
        for tok in new.replace(",", ";").split(";"):
            tok = tok.strip().upper()
            if not tok:
                continue
            p = self.index.match(tok)
            (pages if p else bad).append(p or tok)
        if bad:
            messagebox.showwarning("RFI Stamper",
                                   "Not in this plan set: " + ", ".join(bad))
        row.pages, row.via = sorted(set(pages)), "manual"
        self.tree.set(item, "sheets",
                      ";".join(self.index.info(p).sheet for p in row.pages))
        self.tree.set(item, "via", "manual")

    def export_csv(self):
        if not self.rows:
            return
        p = filedialog.asksaveasfilename(defaultextension=".csv",
                                         filetypes=[("CSV", "*.csv")])
        if p:
            pipeline.rows_to_csv(self.index, self.rows, p)
            self.log.say(f"mapping written to {p}")

    # --------------------------------------------------------------- stamp
    def stamp(self):
        if not self.rows:
            return
        plan = self.plan_var.get().strip()
        self.out_path = os.path.splitext(plan)[0] + "_RFI_overlay.pdf"
        self.run_btn.configure(state="disabled")
        self.open_btn.configure(state="disabled")
        self.status.set("Stamping…")

        def work():
            return pipeline.run(plan, out_path=self.out_path, rows=self.rows,
                                index=self.index, summarizer=OfflineSummarizer(),
                                log=self.log.say)

        def done(rep, err):
            self.run_btn.configure(state="normal")
            if err:
                self.log.say(f"!! run failed: {err}")
                self.status.set("Run failed — see log", "err")
                return
            if rep.verify_ok:
                self.status.set("VERIFIED — nothing covered", "ok")
                self.open_btn.configure(state="normal")
            else:
                self.status.set("VERIFICATION FAILED — see log/report", "err")
                messagebox.showerror("RFI Stamper", "Verification failed — review "
                                     "the log before issuing.")

        run_bg(self, work, done)
