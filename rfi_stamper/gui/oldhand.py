"""The Old Hand: Planloom's ask-the-trades drawer, reachable from anywhere.

The persona is the worker who has seen everything and always tells you where
he read it.  Under the hood every answer comes from **Heartwood** — the
knowledge core (see :mod:`rfi_stamper.heartwood`) — as quoted passages,
cited summaries and clearly-labeled unverified shop notes.  Trades only, by
physics: a weak match gets an honest "not in the Heartwood yet".

The drawer slides in over ANY workspace (status-bar button, Ctrl+/, command
palette), so the bible is one keystroke away mid-stamp, mid-draft or
mid-schedule.  Engine work runs on worker threads; each operation opens its
own Heartwood handle (sqlite connections are thread-bound).
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import fx
from .widgets import make_tree, run_bg, toast

W = 420          # drawer width in px


class OldHandDrawer:
    def __init__(self, root, theme, status, get_records=None):
        self.root, self.theme, self.status = root, theme, status
        self.get_records = get_records          # answered RFIs from the scan
        self.db_path = None                     # resolved lazily; tests override
        self.open_ = False

        f = tk.Frame(root, highlightthickness=1)
        self.frame = f

        head = tk.Frame(f)
        self.head = head
        head.pack(fill="x")
        self.title = tk.Label(head, text="⚘  The Old Hand",
                              font=("Segoe UI", 13, "bold"))
        self.title.pack(side="left", padx=10, pady=8)
        self.close_btn = tk.Label(head, text="✕", cursor="hand2",
                                  font=("Segoe UI", 12))
        self.close_btn.pack(side="right", padx=10)
        self.close_btn.bind("<Button-1>", lambda e: self.toggle(False))
        self.sub = tk.Label(f, text="asks the Heartwood — trades only, "
                                    "cited, offline",
                            font=("Segoe UI", 9))
        self.sub.pack(anchor="w", padx=10)

        self.log = tk.Text(f, wrap="word", state="disabled", relief="flat",
                           padx=10, pady=8, font=("Segoe UI", 10),
                           cursor="arrow")
        self.log.pack(fill="both", expand=True, padx=8, pady=(6, 4))

        row = ttk.Frame(f)
        row.pack(fill="x", padx=8)
        self.q_var = tk.StringVar()
        self.entry = ttk.Entry(row, textvariable=self.q_var)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda e: self.ask())
        ttk.Button(row, text="Ask", style="Accent.TButton",
                   command=self.ask).pack(side="left", padx=(4, 0))

        row2 = ttk.Frame(f)
        row2.pack(fill="x", padx=8, pady=(4, 8))
        self.plain = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="plain words", variable=self.plain
                        ).pack(side="left")
        ttk.Button(row2, text="Teach…", style="Tool.TButton",
                   command=self.teach_dialog).pack(side="right", padx=2)
        ttk.Button(row2, text="Manage…", style="Tool.TButton",
                   command=self.manage_dialog).pack(side="right", padx=2)

        self._greeted = False
        theme.register(self._on_theme)   # last: recolors need the widgets

    # ------------------------------------------------------------- engine
    def _path(self):
        if self.db_path is None:
            from .. import heartwood
            self.db_path = heartwood.default_path()
        return self.db_path

    def _work(self, fn, done):
        """Run fn(hw) on a worker with a fresh engine handle."""
        path = self._path()

        def job():
            from .. import heartwood
            with heartwood.Heartwood(path) as hw:
                return fn(hw)

        run_bg(self.frame, job, done)

    # ------------------------------------------------------------- drawer
    def _on_theme(self, c):
        self.frame.configure(bg=c["panel"], highlightbackground=c["border"])
        self.head.configure(bg=c["panel"])
        self.title.configure(bg=c["panel"], fg=c["fg"])
        self.close_btn.configure(bg=c["panel"], fg=c["muted"])
        self.sub.configure(bg=c["panel"], fg=c["muted"])
        self.log.configure(bg=c["log_bg"], fg=c["fg"],
                           selectbackground=c["sel_bg"])
        self.log.tag_configure("you", foreground=c["accent"],
                               font=("Segoe UI", 10, "bold"))
        self.log.tag_configure("cite", foreground=c["muted"],
                               font=("Segoe UI", 8))
        self.log.tag_configure("note", foreground=c["warn"])
        self.log.tag_configure("refused", foreground=c["muted"],
                               font=("Segoe UI", 10, "italic"))
        self.log.tag_configure("conf", foreground=c["ok"],
                               font=("Segoe UI", 8, "bold"))
        self.log.tag_configure("chip", foreground=c["accent"],
                               underline=True)
        self.log.tag_configure("block", lmargin1=6, lmargin2=6,
                               spacing1=2, spacing3=4)

    def toggle(self, show=None):
        want = (not self.open_) if show is None else bool(show)
        if want == self.open_:
            return
        self.open_ = want
        if want:
            self.frame.place(relx=1.0, y=0, anchor="ne", relheight=1.0,
                             width=W, x=W)
            self.frame.lift()
            if fx.quality() == "off":
                self.frame.place_configure(x=0)
            else:
                fx.animate(self.frame, "slide", W, 0, 240,
                           lambda v: self.frame.winfo_exists()
                           and self.frame.place_configure(x=int(v)),
                           easing="ease_out_quad")
            self.entry.focus_set()
            if not self._greeted:
                self._greeted = True
                self._append("The Old Hand is listening. Ask anything from "
                             "the trades — every answer is cited from the "
                             "Heartwood, or honestly refused.\n", ("refused",))
        else:
            fx.cancel(self.frame, "slide")
            if fx.quality() == "off":
                self.frame.place_forget()
            else:
                fx.animate(self.frame, "slide", 0, W, 200,
                           lambda v: self.frame.winfo_exists()
                           and self.frame.place_configure(x=int(v)),
                           easing="ease_out_quad",
                           on_done=lambda: self.frame.winfo_exists()
                           and self.frame.place_forget())

    def _append(self, text, tags=(), click=None):
        self.log.configure(state="normal")
        if click is not None:
            tag = f"click{self.log.index('end')}"
            self.log.insert("end", text, tags + (tag, "block"))
            self.log.tag_bind(tag, "<Button-1>", click)
        else:
            self.log.insert("end", text, tags + ("block",))
        self.log.configure(state="disabled")
        self.log.see("end")

    # ---------------------------------------------------------------- ask
    def ask(self, question=None):
        q = (question or self.q_var.get()).strip()
        if not q:
            return
        self.q_var.set("")
        self._append(f"\nYou:  {q}\n", ("you",))
        mode = "plain" if self.plain.get() else "quote"

        def done(res, err):
            if err:
                self._append(f"(the Heartwood is unreachable: {err})\n",
                             ("refused",))
                return
            self._render(q, res)

        self._work(lambda hw: hw.ask(q, mode=mode), done)

    def _render(self, q, res):
        if res.get("refused"):
            self._append(res.get("message", "Not in the knowledge base "
                                            "yet.") + "\n", ("refused",))
        for b in res.get("blocks", []):
            if b.get("unverified") or b.get("kind") == "note":
                self._append("🛠 SHOP NOTE — UNVERIFIED\n", ("note",))
            cid = b.get("chunk_id")

            def used(_e, c=cid, qq=q):
                self._work(lambda hw: hw.mark_used(qq, c),
                           lambda *_a: None)
                toast(self.root, self.theme,
                      "Noted — the Old Hand remembers what helped")

            self._append(b.get("text", "") + "\n",
                         ("note",) if b.get("unverified") else (),
                         click=used)
        conf = res.get("confidence", 0.0)
        if not res.get("refused"):
            bars = "▮" * max(1, min(5, int(conf * 5 + 0.5)))
            self._append(f"confidence {bars} {conf:.2f}   "
                         f"(click a passage that helped)\n", ("conf",))
        rel = res.get("related") or []
        if rel:
            self._append("related: ", ("cite",))
            for term in rel[:6]:
                self._append(term, ("chip",),
                             click=lambda _e, t=term: (self.q_var.set(t),
                                                       self.entry.focus_set()))
                self._append("  ", ())
            self._append("\n", ())

    # -------------------------------------------------------------- teach
    def teach_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Teach the Old Hand")
        dlg.transient(self.root)
        dlg.geometry("520x300")
        frm = ttk.Frame(dlg, padding=12)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Teach the Old Hand", style="Title.TLabel"
                  ).pack(anchor="w")
        ttk.Label(frm, style="Muted.TLabel",
                  text="Lands as a shop note — UNVERIFIED until you trust "
                       "it in Manage. It can never silently become gospel."
                  ).pack(anchor="w", pady=(0, 6))
        txt = tk.Text(frm, height=8, wrap="word")
        self.theme.style_text(txt)
        txt.pack(fill="both", expand=True)
        row = ttk.Frame(frm)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="Author:", style="Muted.TLabel").pack(side="left")
        av = tk.StringVar()
        ttk.Entry(row, width=16, textvariable=av).pack(side="left", padx=4)

        def save():
            body = txt.get("1.0", "end").strip()
            if len(body) < 12:
                messagebox.showinfo("Teach", "Give it at least a sentence.",
                                    parent=dlg)
                return
            self._work(lambda hw: hw.teach(body, author=av.get().strip()),
                       lambda _n, err: err or toast(
                           self.root, self.theme,
                           "Shop note saved (unverified)"))
            dlg.destroy()

        ttk.Button(row, text="Save note", style="Accent.TButton",
                   command=save).pack(side="right")
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    # ------------------------------------------------------------- manage
    def manage_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Heartwood — the knowledge core")
        dlg.transient(self.root)
        dlg.geometry("780x780")
        frm = ttk.Frame(dlg, padding=12)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Heartwood", style="Title.TLabel").pack(anchor="w")
        stat = ttk.Label(frm, style="Muted.TLabel", text="…")
        stat.pack(anchor="w", pady=(0, 6))

        btns = ttk.Frame(frm)
        btns.pack(fill="x")

        def refresh_status():
            self._work(lambda hw: hw.status(), lambda s, err: (
                stat.winfo_exists() and stat.configure(
                    text=f"{s.get('docs', 0)} documents · "
                         f"{s.get('chunks', 0)} passages · "
                         f"{s.get('vocab', 0)} terms · "
                         f"{s.get('notes_unverified', 0)} unverified "
                         f"note(s)" if not err else f"status failed: {err}")))

        def after_ingest(res, err):
            if err:
                self.status.set(f"Heartwood ingest failed: {err}", "err")
                return
            toast(self.root, self.theme,
                  f"Woven into the Heartwood: {res}")
            refresh_status()
            refresh_lists()

        def imp_tf():
            p = filedialog.askopenfilename(
                parent=dlg, title="TradeForge database",
                filetypes=[("SQLite db", "*.db"), ("All", "*.*")])
            if p:
                self._work(lambda hw: hw.import_tradeforge(p), after_ingest)

        def add_pdfs():
            paths = filedialog.askopenfilenames(
                parent=dlg, filetypes=[("PDF", "*.pdf")])
            if not paths:
                return
            self._work(lambda hw: [hw.ingest_pdf(p) for p in paths],
                       after_ingest)

        def add_text():
            p = filedialog.askopenfilename(
                parent=dlg, filetypes=[("Text/Markdown", "*.txt *.md"),
                                       ("All", "*.*")])
            if p:
                self._work(lambda hw: hw.ingest_text(
                    os.path.basename(p),
                    open(p, encoding="utf-8", errors="replace").read()),
                    after_ingest)

        def weave_rfis():
            records = self.get_records() if self.get_records else []
            if not records:
                toast(self.root, self.theme,
                      "Scan a plan + RFI pile first (Project Management)",
                      "info")
                return
            self._work(lambda hw: hw.capture_rfis(records), after_ingest)

        def rebuild():
            self._work(lambda hw: hw.rebuild(), after_ingest)

        for label, cmd in (("Import TradeForge KB…", imp_tf),
                           ("Add PDFs…", add_pdfs),
                           ("Add text…", add_text),
                           ("Weave answered RFIs", weave_rfis),
                           ("Rebuild meaning", rebuild)):
            ttk.Button(btns, text=label, command=cmd).pack(side="left",
                                                           padx=2)

        # approval queues: mined terms + shop notes
        ttk.Label(frm, text="Waiting on your judgement",
                  style="Title.TLabel").pack(anchor="w", pady=(10, 2))
        f1, t_terms = make_tree(frm, self.theme,
                                [("term", "FIELD TERM"),
                                 ("canonical", "MEANS"),
                                 ("src", "LEARNED FROM")],
                                (150, 200, 90), height=4)
        f1.pack(fill="x")
        f2, t_notes = make_tree(frm, self.theme,
                                [("note", "SHOP NOTE"),
                                 ("origin", "ORIGIN"),
                                 ("author", "AUTHOR")],
                                (360, 70, 90), height=5)
        f2.pack(fill="both", expand=True, pady=(6, 0))

        def refresh_lists():
            def done(res, err):
                if err or not t_terms.winfo_exists():
                    return
                props, notes = res
                t_terms.delete(*t_terms.get_children())
                for p in props:
                    t_terms.insert("", "end", iid=f"p{p['id']}",
                                   values=(p["term"], p["canonical"],
                                           f"§{p.get('source_chunk', '')}"))
                t_notes.delete(*t_notes.get_children())
                for n in notes:
                    t_notes.insert("", "end", iid=f"n{n['id']}",
                                   values=(n["text"][:80], n["origin"],
                                           n.get("author", "")))

            self._work(lambda hw: (hw.proposals(),
                                   hw.notes(status="unverified")), done)

        def judge(accept):
            terms = [int(i[1:]) for i in t_terms.selection()]
            notes = [int(i[1:]) for i in t_notes.selection()]

            def job(hw):
                for i in terms:
                    (hw.approve_term if accept else hw.reject_term)(i)
                for i in notes:
                    (hw.trust_note if accept else hw.reject_note)(i)
                return len(terms) + len(notes)

            self._work(job, lambda n, err: (refresh_lists(), refresh_status(),
                                            err or toast(
                self.root, self.theme,
                f"{'Trusted' if accept else 'Rejected'} {n} item(s)")))

        rowb = ttk.Frame(frm)
        rowb.pack(fill="x", pady=(6, 0))
        ttk.Button(rowb, text="Trust / approve", style="Accent.TButton",
                   command=lambda: judge(True)).pack(side="left")
        ttk.Button(rowb, text="Reject",
                   command=lambda: judge(False)).pack(side="left", padx=4)
        ttk.Label(rowb, style="Muted.TLabel",
                  text="  nothing becomes gospel without you").pack(
            side="left")

        # the Corral: provenance browser + compaction + the carry file
        ttk.Label(frm, text="Provenance — where every learned item came "
                            "from", style="Title.TLabel").pack(
            anchor="w", pady=(10, 2))
        f3, t_prov = make_tree(frm, self.theme,
                               [("kind", "KIND"),
                                ("item", "LEARNED ITEM"),
                                ("origin", "CAME FROM"),
                                ("status", "STATUS")],
                               (80, 260, 200, 90), height=6)
        f3.pack(fill="both", expand=True)
        self.prov_tree = t_prov               # construct-test handle
        prov_items: dict[str, dict] = {}      # iid -> provenance dict

        def refresh_prov():
            def done(items, err):
                if err or not t_prov.winfo_exists():
                    return
                prov_items.clear()
                t_prov.delete(*t_prov.get_children())
                for i, it in enumerate(items):
                    iid = f"v{i}"
                    prov_items[iid] = it
                    t_prov.insert("", "end", iid=iid,
                                  values=(it["kind"], it["label"],
                                          it["origin"], it["status"]))

            self._work(lambda hw: hw.provenance(), done)

        def purge_selected(confirm=True):
            picked = [prov_items[i] for i in t_prov.selection()
                      if i in prov_items]
            if not picked:
                toast(self.root, self.theme,
                      "Select a learned item to purge", "info")
                return
            if confirm and not messagebox.askyesno(
                    "Purge", f"Purge {len(picked)} learned item(s)? "
                    "Shipped thesaurus seeds are disabled, never deleted.",
                    parent=dlg):
                return
            calls = [(it["kind"], it["id"]) for it in picked]

            def job(hw):
                return sum(1 for kind, ident in calls
                           if hw.purge(kind, ident))

            self._work(job, lambda n, err: (
                refresh_prov(), refresh_lists(), refresh_status(),
                err or toast(self.root, self.theme,
                             f"Purged {n} learned item(s)")))

        def compact_now():
            def done(rep, err):
                if err:
                    toast(self.root, self.theme,
                          f"Compact failed: {err}", "err")
                    return
                sz = rep.get("db_size_mb", {})
                orphans = sum(rep.get("orphans_dropped", {}).values())
                toast(self.root, self.theme,
                      f"Compacted: {rep.get('feedback_pruned', 0)} feedback "
                      f"pruned, {rep.get('chunks_deduped', 0)} duplicate + "
                      f"{orphans} orphan row(s) dropped, "
                      f"{sz.get('before', 0):.1f} → "
                      f"{sz.get('after', 0):.1f} MB")
                for w in rep.get("warnings", []):
                    toast(self.root, self.theme, w, "info")
                refresh_status()
                refresh_prov()

            self._work(lambda hw: hw.compact(), done)

        def export_learning(path=None):
            p = path or filedialog.asksaveasfilename(
                parent=dlg, title="Export learning",
                defaultextension=".json",
                filetypes=[("Learning snapshot", "*.json"), ("All", "*.*")])
            if not p:
                return

            def done(rep, err):
                if err:
                    toast(self.root, self.theme,
                          f"Export failed: {err}", "err")
                    return
                toast(self.root, self.theme,
                      f"Learning exported: {rep['thesaurus']} term(s), "
                      f"{rep['notes']} note(s), {rep['feedback']} feedback "
                      f"row(s)")

            self._work(lambda hw: hw.snapshot(p), done)

        def import_learning(path=None):
            p = path or filedialog.askopenfilename(
                parent=dlg, title="Import learning",
                filetypes=[("Learning snapshot", "*.json"), ("All", "*.*")])
            if not p:
                return

            def done(rep, err):
                if err or (rep and rep.get("error")):
                    toast(self.root, self.theme,
                          f"Import failed: {err or rep['error']}", "err")
                    return
                toast(self.root, self.theme,
                      f"Learning imported: +{rep['thesaurus_added']} "
                      f"term(s), +{rep['notes_added']} note(s) — statuses "
                      "kept, nothing promoted")
                refresh_status()
                refresh_lists()
                refresh_prov()

            self._work(lambda hw: hw.restore(p), done)

        # construct-test handles (drive these paths without file dialogs)
        self.purge_selected = purge_selected
        self.compact_now = compact_now
        self.export_learning = export_learning
        self.import_learning = import_learning
        t_prov.bind("<Button-3>", lambda e: purge_selected())

        rowp = ttk.Frame(frm)
        rowp.pack(fill="x", pady=(6, 0))
        ttk.Button(rowp, text="Purge selected",
                   command=purge_selected).pack(side="left")
        ttk.Button(rowp, text="Compact now",
                   command=compact_now).pack(side="left", padx=4)
        ttk.Button(rowp, text="Import learning…",
                   command=import_learning).pack(side="right")
        ttk.Button(rowp, text="Export learning…",
                   command=export_learning).pack(side="right", padx=4)

        dlg.bind("<Escape>", lambda e: dlg.destroy())
        refresh_status()
        refresh_lists()
        refresh_prov()

    # ------------------------------------------------------- app plumbing
    def capture_rfis_async(self):
        """Lane-2 self-learning: answered RFIs from the latest scan land as
        unverified shop notes.  Silent unless something was captured."""
        records = self.get_records() if self.get_records else []
        if not records:
            return

        def done(res, err):
            if not err and isinstance(res, dict) and res.get("captured"):
                toast(self.root, self.theme,
                      f"{res['captured']} answered RFI(s) woven into the "
                      f"Heartwood (unverified)")

        self._work(lambda hw: hw.capture_rfis(records), done)

    def commands(self):
        return [
            ("Ask the Old Hand (trades Q&A)", "Heartwood",
             lambda: self.toggle(True)),
            ("Teach the Old Hand", "Heartwood", self.teach_dialog),
            ("Manage the Heartwood", "Heartwood", self.manage_dialog),
        ]
