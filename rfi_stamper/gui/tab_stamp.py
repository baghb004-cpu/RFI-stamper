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
        self.scanned_plan = None     # the plan the current index/rows belong to
        self._running = False        # guards re-entry via palette/keyboard
        self.on_scanned = None       # app hook: (plan_path)
        self.on_stamped = None       # app hook: (verify_ok, out_path)
        self.get_statuses = None     # app hook: () -> {rfi#: status} | None
                                     # (Resolution Board statuses ride into
                                     # the stamped note headers)
        self.drop_hint = "Stamp RFIs — plan set first, then RFI files"

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

        # ------------------------------------------------- dashboard tiles
        self.tiles = ttk.Frame(self, style="Panel.TFrame")
        self._tile_vars = {}
        for key, cap in (("rfis", "RFIs FOUND"), ("answered", "ANSWERED"),
                         ("sheets", "SHEETS MATCHED"), ("unmatched", "UNMATCHED")):
            cell = ttk.Frame(self.tiles, style="Panel.TFrame", padding=(18, 8))
            cell.pack(side="left", expand=True, fill="x")
            v = tk.StringVar(value="–")
            ttk.Label(cell, textvariable=v, style="Stat.TLabel").pack(anchor="w")
            ttk.Label(cell, text=cap, style="StatCap.TLabel").pack(anchor="w")
            self._tile_vars[key] = v
        # (packed after the first scan, above the mapping table)

        # ---------------------------------------------------------- mapping
        r4 = ttk.LabelFrame(self, text="Mapping review — double-click a Sheets "
                                       "cell to edit (semicolon-separated)")
        r4.pack(fill="both", expand=True, pady=4)
        self.map_frame = r4
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
        self.log_btn = ttk.Button(r5, text="RFI log PDF…", state="disabled",
                                  command=self.export_log)
        self.log_btn.pack(side="left", padx=6)
        self.link_var = tk.BooleanVar(value=True)
        lc = ttk.Checkbutton(r5, text="hyperlink sheet refs in output",
                             variable=self.link_var)
        lc.pack(side="left", padx=6)
        Tooltip(lc, "After stamping, add clickable links from every sheet "
                    "reference to its page — works in any PDF viewer.", theme)
        ttk.Button(r5, text="Batch…", command=self.batch_dialog).pack(side="right")
        self.last_report = None

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
        if self._running:
            return
        plan = self.plan_var.get().strip()
        rfis = list(self.rfi_list.get(0, "end"))
        if not plan or not rfis:
            messagebox.showwarning("RFI Stamper",
                                   "Pick a plan set PDF and add RFI files first.")
            return
        self._running = True
        self.scan_btn.configure(state="disabled")
        self.run_btn.configure(state="disabled")
        self.status.set("Scanning…")

        def work():
            return pipeline.scan(plan, rfis, log=self.log.say)

        def done(result, err):
            self._running = False
            self.scan_btn.configure(state="normal")
            if err:
                self.log.say(f"!! scan failed: {err}")
                self.status.set("Scan failed — see log", "err")
                return
            self.index, self.rows = result
            self.scanned_plan = plan
            self.fill_tree()
            self.show_stats()
            self.run_btn.configure(state="normal")
            self.csv_btn.configure(state="normal")
            self.status.set(f"{len(self.rows)} RFI(s) mapped — review, then stamp", "ok")
            if self.on_scanned:
                self.on_scanned(plan)

        run_bg(self, work, done)

    def show_stats(self):
        """Big-number dashboard: the scan at a glance."""
        rows = self.rows or []
        total = len(rows)
        answered = sum(1 for r in rows if r.record.has_answer)
        unmatched = sum(1 for r in rows if not r.pages)
        sheets = len({p for r in rows for p in r.pages})
        self._tile_vars["rfis"].set(str(total))
        self._tile_vars["answered"].set(
            f"{answered} ({answered * 100 // total}%)" if total else "0")
        self._tile_vars["sheets"].set(str(sheets))
        self._tile_vars["unmatched"].set(str(unmatched))
        if not self.tiles.winfo_ismapped():
            self.tiles.pack(fill="x", pady=(2, 4), before=self.map_frame)

    def handle_drop(self, paths):
        """Full-window drop routing: first PDF becomes the plan set when none
        is picked yet; everything else joins the RFI list."""
        rest = []
        for p in paths:
            if (p.lower().endswith(".pdf") and not os.path.isdir(p)
                    and not self.plan_var.get().strip()):
                self.plan_var.set(p)
            else:
                rest.append(p)
        self.add_paths(rest)

    def fill_tree(self):
        self.tree.delete(*self.tree.get_children())
        for row in self.rows:
            sheets = ";".join(self.index.info(p).sheet for p in row.pages)
            self.tree.insert("", "end", iid=row.record.number, values=(
                row.record.number, row.record.title, sheets, row.via,
                "yes" if row.record.has_answer else "no"))

    def edit_cell(self, event):
        if self._running:
            return           # mapping is frozen while a scan/stamp is running
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
        if self._running or not self.rows:
            return
        plan = self.plan_var.get().strip()
        if plan != self.scanned_plan:
            # the index/rows describe the scanned plan's pages; stamping a
            # different file with them would place notes on the wrong sheets
            messagebox.showwarning(
                "RFI Stamper", "The plan set changed since the scan — run "
                               "'1  Scan & map' again first.")
            return
        self._running = True
        self.out_path = os.path.splitext(plan)[0] + "_RFI_overlay.pdf"
        self.run_btn.configure(state="disabled")
        self.scan_btn.configure(state="disabled")
        self.open_btn.configure(state="disabled")
        self.status.set("Stamping…")
        rows, index = self.rows, self.index    # frozen: edit_cell is blocked
        add_links = self.link_var.get()
        statuses = self.get_statuses() if self.get_statuses else None

        def work():
            rep = pipeline.run(plan, out_path=self.out_path, rows=rows,
                               index=index, summarizer=OfflineSummarizer(),
                               statuses=statuses, log=self.log.say)
            if rep.verify_ok and add_links:
                # native GoTo links from every sheet reference to its page —
                # done after verify so it never affects the pixel-diff check
                try:
                    from .. import hyperlink
                    stats = hyperlink.auto_link(self.out_path, self.out_path,
                                                index=index, log=self.log.say)
                    self.log.say(f"  hyperlinked {stats.links_added} reference(s)")
                except Exception as e:      # noqa: BLE001 -- links are a bonus
                    self.log.say(f"  (hyperlinking skipped: {e})")
            return rep

        def done(rep, err):
            self._running = False
            self.run_btn.configure(state="normal")
            self.scan_btn.configure(state="normal")
            if err:
                self.log.say(f"!! run failed: {err}")
                self.status.set("Run failed — see log", "err")
                return
            self.last_report = rep
            self.log_btn.configure(state="normal")
            if rep.verify_ok:
                self.status.set("VERIFIED — nothing covered", "ok")
                self.open_btn.configure(state="normal")
            else:
                self.status.set("VERIFICATION FAILED — see log/report", "err")
                messagebox.showerror("RFI Stamper", "Verification failed — review "
                                     "the log before issuing.")
            if self.on_stamped:
                self.on_stamped(rep.verify_ok, self.out_path)

        run_bg(self, work, done)

    def export_log(self):
        if not self.last_report:
            return
        p = filedialog.asksaveasfilename(
            defaultextension=".pdf", initialfile="RFI_log.pdf",
            filetypes=[("PDF", "*.pdf")])
        if not p:
            return
        from .. import transmittal

        def work():
            return transmittal.rfi_log_pdf(self.last_report, p, log=self.log.say)

        def done(res, err):
            if err:
                self.status.set(f"RFI log failed: {err}", "err")
                return
            self.status.set(f"RFI log written ({res['rows']} rows, "
                            f"{res['pages']} pages)", "ok")
            open_path(p)

        run_bg(self, work, done)

    def batch_dialog(self):
        """Stamp several plan sets against the current RFI list in one run."""
        rfis = list(self.rfi_list.get(0, "end"))
        if not rfis:
            messagebox.showinfo("Batch", "Add RFI files first — they'll be "
                                         "stamped onto every plan set you pick.")
            return
        plans = filedialog.askopenfilenames(
            title="Pick plan-set PDFs to stamp (each gets its own output)",
            filetypes=[("PDF", "*.pdf")])
        if not plans:
            return
        out_dir = filedialog.askdirectory(title="Output folder") or None
        self.status.set(f"Batch stamping {len(plans)} plan set(s)…")
        from .. import batch
        from ..summarize import OfflineSummarizer

        def work():
            return batch.batch_stamp(list(plans), rfis, out_dir=out_dir,
                                     summarizer=OfflineSummarizer(),
                                     log=self.log.say)

        def done(items, err):
            if err:
                self.status.set(f"Batch failed: {err}", "err")
                return
            s = batch.batch_summary(items)
            self.status.set(f"Batch done: {s['verified']}/{s['total']} verified, "
                            f"{s['failed']} failed", "ok" if not s['failed'] else "err")
            for it in items:
                self.log.say(f"  {os.path.basename(it.plan_path)}: "
                             + ("OK " + it.out_path if it.verify_ok
                                else "FAILED " + (it.error or "verify")))

        run_bg(self, work, done)
