"""Project Management section: RFIs (the stamping heart of Planloom), the
RFI Resolution Board, Submittals, Change Orders, Budget, Document Management,
and Specifications."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import resolution, submittal
from ..project import BudgetLine, ChangeOrder, DocEntry
from . import dnd, fx
from .crud import CrudPanel, Field
from .tab_merge import MergeTab
from .tab_pdftools import PdfToolsTab
from .tab_stamp import StampTab
from .theme import mix, section_color
from .widgets import DropZone, Tooltip, make_tree, open_path, run_bg, toast

STATUS_COLORS = {"open": "#d99c20", "answered": "#3f6fe0",
                 "in_work": "#8b5cf6", "fixed": "#2f9e62",
                 "verified": "#177245"}


class ResolutionBoard(ttk.Frame):
    """The origin story, over-engineered: a designer picks up the set and
    knows what to fix and what's already done.  Kanban columns per status;
    drag a card to advance it; statuses ride into the stamped note headers
    and onto the printable Designer Pickup Sheet."""

    def __init__(self, parent, theme, status, stamp_tab, root):
        super().__init__(parent, padding=8)
        self.theme = theme
        self.status = status
        self.stamp = stamp_tab
        self.root = root
        self.store = None
        self._drag = None            # (number, ghost_id)

        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Label(bar, text="▍RFI Resolution Board",
                  font=("Segoe UI", 14, "bold"), foreground=STATUS_COLORS["in_work"]
                  ).pack(side="left")
        ttk.Label(bar, style="Muted.TLabel",
                  text="  drag a card to advance it — statuses stamp onto the "
                       "sheets on the next run").pack(side="left")
        ttk.Button(bar, text="Designer Pickup Sheet…", style="Accent.TButton",
                   command=self.pickup).pack(side="right", padx=2)
        ttk.Button(bar, text="Sync from scan", command=self.sync).pack(
            side="right", padx=2)

        self.canvas = tk.Canvas(self, highlightthickness=0, height=380)
        self.canvas.pack(fill="both", expand=True, pady=(8, 0))
        theme.register(lambda c: self.redraw())
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)

    # ------------------------------------------------------------- data
    def _plan(self):
        return self.stamp.scanned_plan

    def sync(self):
        plan = self._plan()
        if not plan or not self.stamp.rows:
            messagebox.showinfo("Resolution",
                                "Run '1  Scan & map' on the RFIs tab first — "
                                "the board tracks the scanned RFIs.")
            return
        self.store = resolution.ResolutionStore(plan)
        added = self.store.seed_from_records(
            [r.record for r in self.stamp.rows])
        self.redraw()
        self.status.set(f"Resolution board synced ({added} new item(s))", "ok")

    def _ensure_store(self):
        plan = self._plan()
        if plan and (self.store is None or getattr(self.store, "plan_path",
                                                   None) != plan):
            try:
                self.store = resolution.ResolutionStore(plan)
            except Exception:   # noqa: BLE001
                self.store = None
        return self.store

    def _cards(self):
        """[(number, title, status)] from the scan rows + store."""
        store = self._ensure_store()
        if not store or not self.stamp.rows:
            return []
        stat = store.statuses()
        out = []
        for row in self.stamp.rows:
            n = row.record.number
            if n in stat:
                out.append((n, row.record.title, stat[n]))
        return out

    # ---------------------------------------------------------- rendering
    def redraw(self):
        cv = self.canvas
        if not cv.winfo_exists():
            return
        c = self.theme.colors
        cv.delete("all")
        cv.configure(bg=c["bg"])
        w = max(cv.winfo_width(), 500)
        cards = self._cards()
        cols = list(resolution.STATUSES)
        cw = (w - 12) / len(cols)
        self._colw = cw
        by_status: dict = {s: [] for s in cols}
        for n, title, st in cards:
            by_status.setdefault(st, []).append((n, title))
        maxrows = max([len(v) for v in by_status.values()] + [1])
        h = max(90 + maxrows * 64, 320)
        cv.configure(scrollregion=(0, 0, w, h))
        for i, st in enumerate(cols):
            x0 = 6 + i * cw
            color = STATUS_COLORS[st]
            cv.create_rectangle(x0, 6, x0 + cw - 8, h - 6, outline="",
                                fill=mix(color, c["bg"], 0.93))
            cv.create_rectangle(x0, 6, x0 + cw - 8, 34, outline="",
                                fill=mix(color, c["bg"], 0.75))
            cv.create_text(x0 + 10, 20, anchor="w", fill=color,
                           font=("Segoe UI", 10, "bold"),
                           text=f"{resolution.LABELS[st]}  ·  "
                                f"{len(by_status[st])}")
            for j, (n, title) in enumerate(by_status[st]):
                self._card(x0 + 8, 44 + j * 64, cw - 24, n, title, color)
        if not cards:
            cv.create_text(w / 2, 150, fill=c["muted"], justify="center",
                           font=("Segoe UI", 12),
                           text="Scan RFIs on the RFIs tab, then 'Sync from "
                                "scan'.\nEvery RFI becomes a card you can walk "
                                "from OPEN to VERIFIED.")

    def _card(self, x, y, w, number, title, color):
        cv = self.canvas
        c = self.theme.colors
        tag = f"card_{number}"
        cv.create_rectangle(x, y, x + w, y + 54, fill=c["panel"],
                            outline=c["border"], width=1, tags=(tag, "card"))
        cv.create_rectangle(x, y, x + 4, y + 54, fill=color, outline="",
                            tags=(tag, "card"))
        cv.create_text(x + 12, y + 14, anchor="w", fill=c["fg"],
                       font=("Segoe UI", 10, "bold"), text=f"RFI {number}",
                       tags=(tag, "card"))
        cv.create_text(x + 12, y + 34, anchor="w", fill=c["muted"],
                       font=("Segoe UI", 8), width=w - 20,
                       text=(title or "")[:80], tags=(tag, "card"))

    # ------------------------------------------------------------- drag
    def _press(self, event):
        cv = self.canvas
        for item in cv.find_overlapping(event.x, event.y, event.x, event.y):
            tags = cv.gettags(item)
            num = next((t[5:] for t in tags if t.startswith("card_")), None)
            if num:
                self._drag = num
                cv.itemconfigure(f"card_{num}", stipple="gray50")
                return

    def _motion(self, event):
        if self._drag:
            self.canvas.configure(cursor="fleur")

    def _release(self, event):
        cv = self.canvas
        cv.configure(cursor="")
        if not self._drag:
            return
        num, self._drag = self._drag, None
        col = int(max(0, min(len(resolution.STATUSES) - 1,
                             (event.x - 6) // self._colw)))
        new_status = resolution.STATUSES[col]
        store = self._ensure_store()
        if store and store.statuses().get(num) != new_status:
            store.set(num, new_status)
            self.status.set(f"RFI {num} → {resolution.LABELS[new_status]}",
                            "ok")
            toast(self.root, self.theme,
                  f"RFI {num} → {resolution.LABELS[new_status]}")
        self.redraw()

    # ------------------------------------------------------------ pickup
    def pickup(self):
        store = self._ensure_store()
        if not store or not self.stamp.rows or not self.stamp.index:
            messagebox.showinfo("Pickup", "Scan & sync first.")
            return
        out = filedialog.asksaveasfilename(
            defaultextension=".pdf", initialfile="designer_pickup.pdf",
            filetypes=[("PDF", "*.pdf")])
        if not out:
            return
        rows, index = self.stamp.rows, self.stamp.index

        def work():
            return resolution.pickup_pdf(rows, index, store, out)

        def done(res, err):
            if err:
                self.status.set(f"Pickup sheet failed: {err}", "err")
                return
            self.status.set(f"Pickup sheet: {res.get('items', 0)} item(s)",
                            "ok")
            toast(self.root, self.theme, "Designer Pickup Sheet ready")
            open_path(out)

        run_bg(self, work, done)


class SubmittalPanel(ttk.Frame):
    def __init__(self, parent, theme, status, root):
        super().__init__(parent, padding=8)
        self.theme, self.status, self.root = theme, status, root
        self.records = []
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Label(bar, text="▍Submittals", font=("Segoe UI", 14, "bold"),
                  foreground=section_color("project")).pack(side="left")
        ttk.Button(bar, text="Log PDF…", style="Accent.TButton",
                   command=self.log_pdf).pack(side="right", padx=2)
        ttk.Button(bar, text="Parse register…", command=self.browse).pack(
            side="right", padx=2)
        DropZone(self, theme, "Drop submittal registers / packages here",
                 self.parse_paths, browse=self.browse, height=42
                 ).pack(fill="x", pady=6)
        frame, self.tree = make_tree(
            self, theme,
            [("number", "NO."), ("spec", "SPEC SECTION"), ("title", "TITLE"),
             ("status", "STATUS"), ("bic", "BALL IN COURT")],
            (110, 110, 300, 150, 130), height=12)
        frame.pack(fill="both", expand=True)
        for st, col in (("Approved", "#2f9e62"), ("Approved as Noted",
                                                  "#2f9e62"),
                        ("Revise & Resubmit", "#d99c20"),
                        ("Rejected", "#d64545"), ("Pending", "#3f6fe0")):
            self.tree.tag_configure(st, foreground=col)

    def browse(self):
        paths = filedialog.askopenfilenames(
            filetypes=[("Registers", "*.pdf *.txt *.zip"), ("All", "*.*")])
        if paths:
            self.parse_paths(list(paths))

    def parse_paths(self, paths):
        self.status.set("Parsing submittals…")

        def work():
            return submittal.parse_submittals(paths)

        def done(recs, err):
            if err:
                self.status.set(f"Parse failed: {err}", "err")
                return
            self.records = recs
            self.tree.delete(*self.tree.get_children())
            for r in recs:
                self.tree.insert("", "end", values=(
                    r.number, r.spec_section, r.title, r.status,
                    r.ball_in_court), tags=(r.status,))
            self.status.set(f"{len(recs)} submittal(s) parsed", "ok")

        run_bg(self, work, done)

    def log_pdf(self):
        if not self.records:
            messagebox.showinfo("Submittals", "Parse a register first.")
            return
        out = filedialog.asksaveasfilename(
            defaultextension=".pdf", initialfile="submittal_log.pdf",
            filetypes=[("PDF", "*.pdf")])
        if not out:
            return
        recs = self.records

        def work():
            return submittal.submittal_log_pdf(recs, out)

        def done(_res, err):
            if err:
                self.status.set(f"Log failed: {err}", "err")
                return
            toast(self.root, self.theme, "Submittal log written")
            open_path(out)

        run_bg(self, work, done)


class SpecsPanel(ttk.Frame):
    def __init__(self, parent, theme, status, get_project, on_change):
        super().__init__(parent, padding=8)
        self.theme, self.status = theme, status
        self.get_project = get_project
        self.on_change = on_change
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Label(bar, text="▍Specifications", font=("Segoe UI", 14, "bold"),
                  foreground=section_color("project")).pack(side="left")
        ttk.Label(bar, style="Muted.TLabel",
                  text="  CSI MasterFormat sections, parsed straight from the "
                       "spec book").pack(side="left")
        ttk.Button(bar, text="Import spec book…", style="Accent.TButton",
                   command=self.import_specs).pack(side="right")
        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, pady=6)
        left = ttk.Frame(body)
        body.add(left, weight=1)
        frame, self.tree = make_tree(
            left, theme, [("section", "SECTION"), ("title", "TITLE")],
            (100, 260), height=14)
        frame.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._show)
        right = ttk.Frame(body)
        body.add(right, weight=2)
        self.text = tk.Text(right, wrap="word", state="disabled",
                            font=("Segoe UI", 10))
        self.text.pack(fill="both", expand=True)
        theme.register(lambda c: theme.style_text(self.text))
        self.refresh()

    def import_specs(self):
        proj = self.get_project()
        if not proj:
            messagebox.showinfo("Planloom", "Open or create a project first.")
            return
        paths = filedialog.askopenfilenames(
            filetypes=[("Spec book", "*.pdf *.txt"), ("All", "*.*")])
        if not paths:
            return
        self.status.set("Parsing spec book…")

        def work():
            from ..project import parse_spec
            return parse_spec(list(paths))

        def done(secs, err):
            if err:
                self.status.set(f"Spec parse failed: {err}", "err")
                return
            for s in secs:
                proj.add("specs", s)
            self.refresh()
            self.on_change()
            self.status.set(f"{len(secs)} spec section(s) imported", "ok")

        run_bg(self, work, done)

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        proj = self.get_project()
        for s in (proj.items("specs") if proj else []):
            self.tree.insert("", "end", iid=s.id,
                             values=(s.section, s.title))

    def _show(self, _e):
        sel = self.tree.selection()
        proj = self.get_project()
        if not sel or not proj:
            return
        s = proj.get("specs", sel[0])
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        if s:
            self.text.insert("1.0", f"{s.section} — {s.title}\n\n{s.text}")
        self.text.configure(state="disabled")


class ProjectSection(ttk.Frame):
    def __init__(self, parent, theme, status, root, get_project, on_change):
        super().__init__(parent)
        col = section_color("project")
        self.header = fx.GradientHeader(
            self, theme, height=58,
            stops=[(0.0, col), (1.0, mix(col, theme.colors["bg"], 0.75))],
            title="Project Management",
            subtitle="RFIs stamped, tracked and resolved · submittals · "
                     "change orders · budget · documents · specs")
        self.header.pack(fill="x")
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=4, pady=4)
        self.nb = nb

        self.stamp = StampTab(nb, theme, status)
        nb.add(self.stamp, text="  RFIs  ")
        self.board = ResolutionBoard(nb, theme, status, self.stamp, root)
        nb.add(self.board, text="  Resolution Board  ")
        # statuses ride into the stamped headers on every run
        self.stamp.get_statuses = self._statuses_for_stamp
        prev_hook = self.stamp.on_scanned
        def scanned(plan, _prev=prev_hook):
            if _prev:
                _prev(plan)
            self.board.sync()
        self.stamp.on_scanned = scanned

        self.submittals = SubmittalPanel(nb, theme, status, root)
        nb.add(self.submittals, text="  Submittals  ")

        self.change_orders = CrudPanel(
            nb, theme, status, get_project, "change_orders", "Change Orders",
            columns=[("number", "NO.", 70), ("title", "TITLE", 280),
                     ("amount", "AMOUNT $", 110), ("status", "STATUS", 100),
                     ("days_impact", "DAYS", 60)],
            fields=[Field("number", "CO number"), Field("title", "Title"),
                    Field("amount", "Amount ($)", "number"),
                    Field("status", "Status", "choice",
                          ["draft", "submitted", "approved", "rejected"]),
                    Field("days_impact", "Schedule days", "number")],
            factory=ChangeOrder, section="project", on_change=on_change)
        nb.add(self.change_orders, text="  Change Orders  ")

        budget_wrap = ttk.Frame(nb)
        self.budget = CrudPanel(
            budget_wrap, theme, status, get_project, "budget", "Budget",
            columns=[("code", "CODE", 80), ("desc", "DESCRIPTION", 280),
                     ("budget", "BUDGET $", 110),
                     ("committed", "COMMITTED $", 110),
                     ("spent", "SPENT $", 110)],
            fields=[Field("code", "Cost code"), Field("desc", "Description"),
                    Field("budget", "Budget ($)", "number"),
                    Field("committed", "Committed ($)", "number"),
                    Field("spent", "Spent ($)", "number")],
            factory=BudgetLine, section="project",
            on_change=lambda: (self._budget_meter(), on_change()))
        self.budget.pack(fill="both", expand=True)
        side = ttk.Frame(budget_wrap)
        side.place(relx=1.0, y=6, anchor="ne", x=-220)
        self.meter = fx.Meter(side, theme, width=110, height=110,
                              color=col, label="spent")
        self.meter.pack()
        nb.add(budget_wrap, text="  Budget  ")
        self.get_project = get_project

        docs = ttk.Frame(nb)
        dnb = ttk.Notebook(docs)
        dnb.pack(fill="both", expand=True)
        self.doc_register = CrudPanel(
            dnb, theme, status, get_project, "documents", "Document Register",
            columns=[("title", "TITLE", 240), ("category", "CATEGORY", 110),
                     ("rev", "REV", 60), ("path", "FILE", 320)],
            fields=[Field("title", "Title"),
                    Field("category", "Category", "choice",
                          ["plans", "specs", "rfi", "submittal", "contract",
                           "photo", "other"]),
                    Field("rev", "Revision"), Field("path", "File path")],
            factory=DocEntry, section="project", on_change=on_change)
        dnb.add(self.doc_register, text=" Register ")
        self.merge = MergeTab(dnb, theme, status)
        dnb.add(self.merge, text=" Combine ")
        self.pdftools = PdfToolsTab(dnb, theme, status, root)
        dnb.add(self.pdftools, text=" PDF Tools ")
        nb.add(docs, text="  Documents  ")

        self.specs = SpecsPanel(nb, theme, status, get_project, on_change)
        nb.add(self.specs, text="  Specifications  ")

    def _statuses_for_stamp(self):
        store = self.board._ensure_store()
        return store.statuses() if store else None

    def _budget_meter(self):
        proj = self.get_project()
        if not proj:
            return
        total = sum(b.budget for b in proj.items("budget")) or 1.0
        spent = sum(b.spent for b in proj.items("budget"))
        self.meter.set(min(100.0, spent / total * 100.0))

    def refresh(self):
        for p in (self.change_orders, self.budget, self.doc_register):
            p.refresh()
        self.specs.refresh()
        self._budget_meter()

    def commands(self):
        return ([("Sync resolution board", "RFIs", self.board.sync),
                 ("Designer pickup sheet", "RFIs", self.board.pickup),
                 ("Parse submittal register", "Project",
                  self.submittals.browse),
                 ("Add change order", "Project",
                  self.change_orders.add_dialog),
                 ("Add budget line", "Project", self.budget.add_dialog),
                 ("Import spec book", "Project", self.specs.import_specs)]
                + self.stamp.commands() + self.merge.commands()
                + self.pdftools.commands())
