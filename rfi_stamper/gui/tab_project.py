"""Project Management section: RFIs (the stamping heart of Planloom), the
RFI Resolution Board, Submittals, Change Orders, Budget, Document Management,
and Specifications."""
from __future__ import annotations

import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import resolution, submittal, swatchbook
from ..project import BudgetLine, ChangeOrder, DocEntry
from . import dnd, fx
from .crud import CrudPanel, Field
from .tab_merge import MergeTab
from .tab_pdftools import PdfToolsTab
from .tab_stamp import StampTab
from .theme import mix, section_color
from .widgets import DropZone, make_tree, open_path, run_bg, toast

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
        if not (0 <= event.x < cv.winfo_width()
                and 0 <= event.y < cv.winfo_height()):
            self.redraw()            # released outside the board: cancel,
            return                   # never advance a card by accident
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


class SwatchbookPanel(ttk.Frame):
    """The Swatchbook: plumbing cut-sheet submittal packets — one stamped
    PDF per fixture tag, components merged in spec-paragraph order, gaps
    documented in the build log (a partial package must never look like a
    full one).  Callouts resolve live against the offline component
    library; unresolved rows are loud red GAPs, never silent skips."""

    def __init__(self, parent, theme, status, root, library_root=None,
                 get_project=None):
        super().__init__(parent, padding=8)
        self.theme = theme
        self.status = status
        self.root = root
        self.get_project = get_project
        self._lib_root = library_root
        self._lib = None
        self.fixtures: list = []        # recipe packet dicts, form-built
        self._rows: list = []           # (callout, component_id | None)

        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Label(bar, text="▍The Swatchbook",
                  font=("Segoe UI", 14, "bold"),
                  foreground=section_color("project")).pack(side="left")
        ttk.Label(bar, style="Muted.TLabel",
                  text="  cut-sheet submittal packets — one stamped PDF "
                       "per fixture tag").pack(side="left")
        ttk.Button(bar, text="Build All…", style="Accent.TButton",
                   command=self.build_all).pack(side="right", padx=2)
        ttk.Button(bar, text="Load reference project",
                   command=self.load_reference).pack(side="right", padx=2)
        ttk.Button(bar, text="Import sheet…",
                   command=self.import_sheet).pack(side="right", padx=2)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, pady=(8, 0))
        left, self.tree = make_tree(
            body, theme,
            [("file", "PACKET"), ("tag", "TAG"), ("cat", "CATEGORY"),
             ("comps", "COMPONENTS"), ("gaps", "GAPS")],
            (110, 70, 170, 90, 200), height=14)
        left.pack(side="left", fill="both", expand=True)

        form = ttk.Frame(body, padding=(10, 0, 0, 0))
        form.pack(side="left", fill="y")
        r1 = ttk.Frame(form)
        r1.pack(fill="x")
        ttk.Label(r1, text="Tag").pack(side="left")
        self.tag_var = tk.StringVar()
        ttk.Entry(r1, textvariable=self.tag_var, width=9).pack(
            side="left", padx=(4, 10))
        ttk.Label(r1, text="Category").pack(side="left")
        self.cat_var = tk.StringVar()
        cats = [f"{k:02d}  {v}" for k, v in sorted(
            swatchbook.CATEGORIES.items())]
        ttk.Combobox(r1, textvariable=self.cat_var, values=cats, width=26,
                     state="readonly").pack(side="left", padx=4)
        r2 = ttk.Frame(form)
        r2.pack(fill="x", pady=(6, 0))
        ttk.Label(r2, text="Component callout").pack(side="left")
        self.callout_var = tk.StringVar()
        ent = ttk.Entry(r2, textvariable=self.callout_var, width=24)
        ent.pack(side="left", padx=4)
        ent.bind("<KeyRelease>", lambda e: self._resolve_live())
        ent.bind("<Return>", lambda e: self.add_row())
        ttk.Button(r2, text="Add", command=self.add_row).pack(side="left")
        ttk.Label(form, style="Muted.TLabel",
                  text="append '@ 4-6' to a callout for a booklet page "
                       "range").pack(fill="x")
        self.match = ttk.Label(form, text="", style="Muted.TLabel")
        self.match.pack(fill="x", pady=(2, 4))
        self.rows_box = tk.Listbox(form, height=9, width=44,
                                   activestyle="none",
                                   highlightthickness=0, relief="flat",
                                   exportselection=False)
        self.rows_box.pack(fill="x")

        def _restyle(_c=None):
            c = self.theme.colors
            self.rows_box.configure(
                bg=c["panel"], fg=c["fg"], selectbackground=c["sel_bg"],
                selectforeground=c["sel_fg"])
        theme.register(_restyle)
        _restyle()
        rb = ttk.Frame(form)
        rb.pack(fill="x", pady=(4, 8))
        for txt, cmd in (("Remove", self.remove_row),
                         ("Move up", lambda: self.move_row(-1)),
                         ("Move down", lambda: self.move_row(1))):
            ttk.Button(rb, text=txt, command=cmd).pack(side="left", padx=2)
        ttk.Button(form, text="Add fixture  →", style="Accent.TButton",
                   command=self.add_fixture_from_form).pack(fill="x")
        self.health = ttk.Label(self, text="", style="Muted.TLabel")
        self.health.pack(fill="x", pady=(6, 0))

    # ------------------------------------------------------------- library
    def library(self):
        if self._lib is None:
            root = self._lib_root or swatchbook.ensure_user_library()
            self._lib = swatchbook.Library(root)
            n_ok = sum(1 for c in self._lib.components
                       if self._lib.usable(c))
            note = (f"library: {n_ok}/{len(self._lib.components)} sheets "
                    f"installed · {len(self._lib.wanted)} on the wanted "
                    "list (request from rep / import manually)")
            if len(self._lib.issues) and n_ok == 0:
                note += " — seed kit not installed yet"
            self.health.configure(text=note)
        return self._lib

    # ------------------------------------------------------------- form
    @staticmethod
    def _split_range(text: str):
        """``'callout @ 4-6'`` -> ``('callout', (4, 6))``; no suffix -> None."""
        m = re.match(r"(.*?)\s*@\s*(\d+)\s*-\s*(\d+)\s*$", text)
        if m:
            return m.group(1).strip(), (int(m.group(2)), int(m.group(3)))
        return text, None

    def _resolve_live(self):
        callout, _rng = self._split_range(self.callout_var.get())
        c, note = self.library().resolve_ex(callout)
        if c is not None and note:
            self.match.configure(text=f"⚠ {note}", foreground="#d99c20")
        elif c is not None:
            self.match.configure(
                text=f"→ {c.manufacturer} {c.id} ({c.pages} pg)",
                foreground=self.theme.colors.get("ok", "#2f9e62"))
        elif callout.strip():
            self.match.configure(text="GAP — no library match",
                                 foreground="#d64545")
        else:
            self.match.configure(text="")

    def add_row(self, callout: str | None = None):
        text = (callout if callout is not None
                else self.callout_var.get()).strip()
        if not text:
            return
        base, _rng = self._split_range(text)
        c, note = self.library().resolve_ex(base)
        self._rows.append((text, c.id if c else None))
        mark = "SUBSTITUTION " if (c and note) else ""
        self.rows_box.insert(
            "end", f"{text}   →  {mark}{c.id if c else 'GAP'}")
        self.callout_var.set("")
        self._resolve_live()

    def remove_row(self):
        sel = self.rows_box.curselection()
        if sel:
            self._rows.pop(sel[0])
            self.rows_box.delete(sel[0])

    def move_row(self, dy: int):
        sel = self.rows_box.curselection()
        if not sel:
            return
        i, j = sel[0], sel[0] + dy
        if 0 <= j < len(self._rows):     # row order IS the merge order
            self._rows[i], self._rows[j] = self._rows[j], self._rows[i]
            txt = self.rows_box.get(i)
            self.rows_box.delete(i)
            self.rows_box.insert(j, txt)
            self.rows_box.selection_set(j)

    def add_fixture_from_form(self):
        tag = self.tag_var.get()
        if not tag.strip() or not self.cat_var.get():
            messagebox.showinfo("Swatchbook",
                                "Enter a tag and pick a category.")
            return
        if not self._rows:
            messagebox.showinfo("Swatchbook", "Add at least one component.")
            return
        prefix = int(self.cat_var.get().split()[0])
        self.add_fixture(tag, prefix,
                         [c for c, _ in self._rows])
        self._rows = []
        self.rows_box.delete(0, "end")
        self.tag_var.set("")

    def add_fixture(self, tag: str, prefix: int, callouts: list):
        """Form-independent entry (the construct test drives this).

        The original callouts are KEPT on the packet — the build re-resolves
        them against the live library, so a sheet imported after the fixture
        was entered fills its gap on the next build with no re-typing."""
        tag = swatchbook.canonical_tag(tag)      # WC1 -> WC-1, the standard
        pk = {"filename": swatchbook.packet_filename(prefix, tag),
              "tag": tag, "prefix": prefix,
              "category": swatchbook.CATEGORIES.get(prefix, ""),
              "callouts": list(callouts),
              "components": [], "missing": [], "flags": []}
        self._resolve_packet(pk)
        self._store_writeback(pk)
        self.fixtures = [p for p in self.fixtures if p["tag"] != tag] + [pk]
        self._refresh_tree()

    def _store_writeback(self, pk: dict):
        """Hand-entered callouts/category for a model-sourced tag persist
        onto its Cut Ticket row (human-owned fields — a re-census never
        touches them), so they survive restarts and model changes."""
        proj = self.get_project() if self.get_project else None
        if proj is None:
            return
        for it in proj.pull_list:
            if it.tag == pk["tag"]:
                it.callouts = list(pk["callouts"])
                it.prefix = pk["prefix"]
                it.category = pk["category"]
                it.status = "confirmed"
                pk.setdefault("flags", []).append(
                    f"model-sourced: {it.count} placed (the Cut Ticket)")
                if proj.path:
                    proj.save()
                return

    def refresh_pull(self):
        """Auto-feed: the project's Cut Ticket rows appear as proposal
        packets (loud gaps where callouts are needed).  Hand-entered
        fixtures always win a tag collision; PDFs still only build on the
        explicit Build All."""
        proj = self.get_project() if self.get_project else None
        if proj is None:
            return
        from .. import cutticket
        packets, needs = cutticket.to_packets(proj.pull_list)
        keep = [p for p in self.fixtures if p.get("origin") != "model"]
        have = {p["tag"] for p in keep}
        self.fixtures = keep + [p for p in packets if p["tag"] not in have]
        self._needs_attention = needs
        self._refresh_tree()
        if needs:
            self.status.set(
                f"Cut Ticket: {len(needs)} tag(s) need a 0-49 category "
                f"({', '.join(t for t, _ in needs[:4])}…)" if len(needs) > 4
                else f"Cut Ticket: {len(needs)} tag(s) need a 0-49 "
                     f"category ({', '.join(t for t, _ in needs)})", "warn")

    def _resolve_packet(self, pk: dict):
        """(Re-)resolve a form packet's callouts against the live library."""
        lib = self.library()
        comps, gaps, flags = [], [], []
        for i, text in enumerate(pk.get("callouts", [])):
            base, rng = self._split_range(text)
            c, note = lib.resolve_ex(base)
            if c is None:
                gaps.append(f"{text} - insert at position {i + 1}")
                continue
            comps.append({"id": c.id, "page_range": list(rng)} if rng
                         else c.id)
            if note:
                flags.append(f"{note} ({text})")
        pk["components"], pk["missing"], pk["flags"] = comps, gaps, flags

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for p in sorted(self.fixtures,
                        key=lambda p: (p["prefix"], p["tag"])):
            self.tree.insert("", "end", values=(
                p["filename"], p["tag"], p["category"],
                len(p["components"]), "; ".join(p["missing"]) or "—"))

    # ------------------------------------------------------------- actions
    def load_reference(self):
        recipes = swatchbook.load_recipes()
        self.fixtures = list(recipes["packets"])
        self._reference = recipes
        self._refresh_tree()
        self.status.set(f"{len(self.fixtures)} reference packet(s) loaded",
                        "ok")

    def import_sheet(self):
        path = filedialog.askopenfilename(
            title="Import a clean manufacturer sheet",
            filetypes=[("PDF", "*.pdf"), ("All files", "*.*")])
        if not path:
            return
        cid = os.path.splitext(os.path.basename(path))[0].lower()
        cid = "".join(ch if ch.isalnum() else "_" for ch in cid)
        try:
            c = self.library().import_pdf(path, cid)
        except (ValueError, OSError) as e:
            messagebox.showerror("Import sheet", str(e))
            return
        self._lib = None                # reload + refresh the health line
        self.library()
        for pk in self.fixtures:        # gaps close in the tree right away;
            if pk.get("callouts"):      # the next build picks them up too
                self._resolve_packet(pk)
        self._refresh_tree()
        toast(self.root, self.theme,
              f"{c.id} imported ({c.pages} pg) — packets with this gap "
              "rebuild with it now")

    def build_all(self):
        if not self.fixtures:
            messagebox.showinfo(
                "Swatchbook", "Add fixtures (or load the reference "
                "project) first.")
            return
        out = filedialog.askdirectory(title="Output folder for the packets")
        if not out:
            return
        self.status.set("Building packets…")
        # resolve the library (and any first-run kit copy + fixture
        # re-resolution) HERE, on the tk thread — run_bg's work() must
        # never touch tk, and library() updates the health label
        recipes = self._recipes()
        lib = self.library()

        def work():
            return swatchbook.build_all(recipes, lib, out,
                                        gap_fillers=True,
                                        log=lambda *a, **k: None)

        def done(res, err):
            if err:
                self.status.set(f"Build failed: {err}", "err")
                return
            n = len(res["built"])
            g = len(res["gapped"])
            toast(self.root, self.theme,
                  f"{n} packet(s) built" + (f", {g} with gaps" if g else ""))
            self._refresh_tree()
            open_path(res["log_path"])

        run_bg(self, work, done)

    def _recipes(self) -> dict:
        """The recipe dict for a build: form fixtures re-resolved against
        the LIVE library (an imported sheet fills its gap on rebuild)."""
        for pk in self.fixtures:
            if pk.get("callouts"):
                self._resolve_packet(pk)
        return {"project": "Cut sheet submittal",
                "packets": self.fixtures,
                "gap_fillers": getattr(self, "_reference",
                                       {}).get("gap_fillers", []),
                "not_built": getattr(self, "_reference",
                                     {}).get("not_built", [])}

    def build_to(self, out_dir: str) -> dict:
        """Synchronous build (tk-thread callers and the construct test)."""
        return swatchbook.build_all(self._recipes(), self.library(),
                                    out_dir, gap_fillers=True,
                                    log=lambda *a, **k: None)


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
            # dedup against what's already stored, keyed on (section, source),
            # so re-importing the same book doesn't duplicate every section
            have = {(s.section, s.source) for s in proj.specs}
            fresh = [s for s in secs if (s.section, s.source) not in have]
            if fresh:
                # one atomic save for the whole book — proj.add() would
                # rewrite the entire project file once per section
                proj.specs.extend(fresh)
                if proj.path:
                    proj.save()
            self.refresh()
            self.on_change()
            self.status.set(f"{len(fresh)} spec section(s) imported", "ok")

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


class ReckonerPanel(ttk.Frame):
    """Quantity takeoff & pricing: the count/length/area markups on a drawing
    become quantities; a local price book CSV turns them into an estimate."""

    def __init__(self, parent, theme, status, root):
        super().__init__(parent, padding=8)
        self.theme, self.status, self.root = theme, status, root
        self.lines = []
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Label(bar, text="▍Reckoner", font=("Segoe UI", 14, "bold"),
                  foreground=section_color("project")).pack(side="left")
        ttk.Label(bar, style="Muted.TLabel",
                  text="  takeoff from drawing markups · priced from a local "
                       "CSV price book").pack(side="left")
        r1 = ttk.Frame(self)
        r1.pack(fill="x", pady=(6, 2))
        ttk.Label(r1, text="Marked-up PDF:").pack(side="left")
        self.pdf_var = tk.StringVar()
        e1 = ttk.Entry(r1, textvariable=self.pdf_var)
        e1.pack(side="left", fill="x", expand=True, padx=4)
        dnd.enable_drop(e1, lambda p: p and self.pdf_var.set(p[0]),
                        exts=(".pdf",))
        ttk.Button(r1, text="…", width=3, command=self._pick_pdf
                   ).pack(side="left")
        ttk.Label(r1, text="Price book CSV:").pack(side="left", padx=(12, 0))
        self.book_var = tk.StringVar()
        e2 = ttk.Entry(r1, textvariable=self.book_var, width=28)
        e2.pack(side="left", padx=4)
        dnd.enable_drop(e2, lambda p: p and self.book_var.set(p[0]),
                        exts=(".csv",))
        ttk.Button(r1, text="…", width=3, command=self._pick_book
                   ).pack(side="left")
        ttk.Button(r1, text="Run takeoff", style="Accent.TButton",
                   command=self.run_takeoff).pack(side="left", padx=8)

        frame, self.tree = make_tree(
            self, theme,
            [("subject", "SUBJECT"), ("kind", "KIND"), ("qty", "QTY"),
             ("unit", "UNIT"), ("pages", "PAGES"), ("code", "CODE"),
             ("cost", "UNIT COST"), ("total", "TOTAL")],
            (190, 70, 90, 60, 80, 90, 90, 110), height=11)
        frame.pack(fill="both", expand=True, pady=6)

        r2 = ttk.Frame(self)
        r2.pack(fill="x")
        self.total_lbl = ttk.Label(r2, text="", style="Stat.TLabel")
        self.total_lbl.pack(side="left")
        self.match_lbl = ttk.Label(r2, text="", style="Muted.TLabel")
        self.match_lbl.pack(side="left", padx=10)
        ttk.Button(r2, text="Takeoff PDF…", command=self.export_pdf
                   ).pack(side="right", padx=2)
        ttk.Button(r2, text="Export CSV…", command=self.export_csv
                   ).pack(side="right", padx=2)

    def _pick_pdf(self):
        p = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf")])
        if p:
            self.pdf_var.set(p)

    def _pick_book(self):
        p = filedialog.askopenfilename(filetypes=[("CSV", "*.csv")])
        if p:
            self.book_var.set(p)

    @staticmethod
    def _cal_for_pdf(pdf_path):
        """Per-page scale lookup from the markup tab's .scale.json sidecar."""
        import json

        from ..markups import measure
        cals, default = {}, None
        try:
            with open(pdf_path + ".scale.json", encoding="utf-8") as f:
                d = json.load(f)
            for k, v in d.get("pages", {}).items():
                cals[int(k)] = measure.ScaleCal.from_dict(v)
            if d.get("default"):
                default = measure.ScaleCal.from_dict(d["default"])
            if "real_per_pt" in d:                        # legacy flat
                default = measure.ScaleCal.from_dict(d)
        except Exception:   # noqa: BLE001 -- no scale sidecar
            pass
        return lambda page: cals.get(page) or default

    def run_takeoff(self):
        pdf = self.pdf_var.get().strip()
        if not pdf or not os.path.exists(pdf):
            messagebox.showinfo("Reckoner", "Pick a marked-up PDF first — "
                                            "counts, lengths and areas come "
                                            "from its markups.")
            return
        book_path = self.book_var.get().strip()
        self.status.set("Running takeoff…")

        def work():
            from .. import markups as mk
            from .. import reckoner
            store = mk.MarkupStore(pdf)
            lines = reckoner.takeoff(store, self._cal_for_pdf(pdf))
            summary = None
            if book_path:
                summary = reckoner.price(lines,
                                         reckoner.PriceBook(book_path))
            return lines, summary

        def done(res, err):
            if err:
                self.status.set(f"Takeoff failed: {err}", "err")
                return
            self.lines, summary = res
            self.tree.delete(*self.tree.get_children())
            for i, ln in enumerate(self.lines):
                self.tree.insert("", "end", iid=str(i), values=(
                    ln.subject, ln.kind, f"{ln.qty:,.2f}", ln.unit,
                    ",".join(str(p) for p in ln.pages), ln.code,
                    f"{ln.unit_cost:,.2f}" if ln.unit_cost else "",
                    f"{ln.total:,.2f}" if ln.total else ""))
            if summary:
                self.total_lbl.configure(
                    text=f"$ {summary['total']:,.2f}")
                self.match_lbl.configure(
                    text=f"{summary['matched']} matched · "
                         f"{summary['unmatched']} unmatched")
            else:
                self.total_lbl.configure(text="")
                self.match_lbl.configure(
                    text=f"{len(self.lines)} line(s) — add a price book CSV "
                         "to price them")
            self.status.set(f"Takeoff: {len(self.lines)} line(s)", "ok")
            self._summary = summary

        run_bg(self, work, done)

    def export_csv(self):
        if not self.lines:
            return
        out = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile="takeoff.csv",
            filetypes=[("CSV", "*.csv")])
        if out:
            from .. import reckoner
            reckoner.export_csv(self.lines, out)
            toast(self.root, self.theme, "Takeoff CSV written")
            open_path(out)

    def export_pdf(self):
        if not self.lines:
            return
        out = filedialog.asksaveasfilename(
            defaultextension=".pdf", initialfile="takeoff.pdf",
            filetypes=[("PDF", "*.pdf")])
        if out:
            from .. import reckoner
            reckoner.takeoff_pdf(self.lines, out,
                                 summary=getattr(self, "_summary", None))
            toast(self.root, self.theme, "Takeoff PDF ready")
            open_path(out)


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
            if self.stamp.rows:
                self.board.sync()
            else:
                # a scan that found no RFIs must not pop sync()'s "scan
                # first" modal at the user — just clear any stale cards
                self.board.redraw()
        self.stamp.on_scanned = scanned

        self.submittals = SubmittalPanel(nb, theme, status, root)
        nb.add(self.submittals, text="  Submittals  ")

        self.swatchbook = SwatchbookPanel(nb, theme, status, root,
                                          get_project=get_project)
        nb.add(self.swatchbook, text="  Swatchbook  ")
        # the Cut Ticket auto-feed: entering the Swatchbook tab pulls the
        # latest model-derived rows (the Loft updates them on every save)
        nb.bind("<<NotebookTabChanged>>", self._on_tab_changed, add="+")

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

        self.reckoner = ReckonerPanel(nb, theme, status, root)
        nb.add(self.reckoner, text="  Reckoner  ")

        docs = ttk.Frame(nb)
        self.docs_tab = docs
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
        self.swatchbook.refresh_pull()
        self._budget_meter()

    def _on_tab_changed(self, _e):
        try:
            if self.nb.select() == str(self.swatchbook):
                self.swatchbook.refresh_pull()
        except tk.TclError:
            pass

    def commands(self):
        return ([("Sync resolution board", "RFIs", self.board.sync),
                 ("Designer pickup sheet", "RFIs", self.board.pickup),
                 ("Reckoner: run takeoff", "Project",
                  self.reckoner.run_takeoff),
                 ("Parse submittal register", "Project",
                  self.submittals.browse),
                 ("Add change order", "Project",
                  self.change_orders.add_dialog),
                 ("Add budget line", "Project", self.budget.add_dialog),
                 ("Import spec book", "Project", self.specs.import_specs)]
                + self.stamp.commands() + self.merge.commands()
                + self.pdftools.commands())
