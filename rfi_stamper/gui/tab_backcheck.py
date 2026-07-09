"""The Backcheck: Planloom's instant peer-check panel.

The senior reviewer's red-pen pass, automated.  Runs the deterministic
:mod:`rfi_stamper.backcheck` rules over the open plan PDF or the current Loft
draft, lists every finding with the rule that produced it, jumps to each
location, and writes the findings back onto the drawing as real markup
annotations (severity-colored clouds + comment callouts) or logs a recurring
one as a Heartwood lesson.

Honest by design: it flags only what it can PROVE from the geometry and text
it can read, and it SHOWS you what it could not check (GD&T, molding draft
angles, sleeve clashes need data a 2D plan/BIM app does not carry).
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import backcheck
from .theme import section_color
from .widgets import make_tree, open_path, run_bg, toast

SEV_COLOR = {"blocker": "#c1121f", "major": "#e8590c",
             "minor": "#e0a800", "info": "#3f6fe0"}
SEV_CHIP = {"blocker": "■ BLOCK", "major": "■ MAJOR", "minor": "■ minor",
            "info": "· info"}
CATS = ("data", "ambiguity", "geometry", "standards", "lessons", "dfx")
CAT_LABEL = {"data": "Technical data", "ambiguity": "Ambiguous / incomplete",
             "geometry": "Geometry flaws", "standards": "Non-conformance",
             "lessons": "Lessons learned", "dfx": "DFX / constructability"}


class BackcheckTab(ttk.Frame):
    def __init__(self, parent, theme, status, get_loft=None,
                 get_plan=None, goto_loft=None, goto_plan=None,
                 hw_path_provider=None):
        super().__init__(parent)
        self.theme, self.status = theme, status
        self.get_loft = get_loft            # -> LoftTab
        self.get_plan = get_plan            # -> (path, PDFViewer) or (None, None)
        self.goto_loft = goto_loft          # switch the notebook to the Loft
        self.goto_plan = goto_plan          # switch to Plan Viewing
        self.hw_path_provider = hw_path_provider
        self.report: backcheck.Report | None = None
        self._rows: dict[str, backcheck.Finding] = {}
        self._source_pdf = None             # path checked, for markup writing

        bar = ttk.Frame(self, padding=(10, 8, 10, 2))
        bar.pack(fill="x")
        ttk.Label(bar, text="▍The Backcheck", font=("Segoe UI", 14, "bold"),
                  foreground=section_color("plans")).pack(side="left")
        ttk.Label(bar, style="Muted.TLabel",
                  text="  the instant peer check — every finding cites its "
                       "rule").pack(side="left")
        ttk.Button(bar, text="Check the Loft", style="Accent.TButton",
                   command=self.check_loft).pack(side="right", padx=2)
        ttk.Button(bar, text="Check open plan",
                   command=self.check_plan).pack(side="right", padx=2)
        ttk.Button(bar, text="Check file…",
                   command=self.check_file).pack(side="right", padx=2)

        filt = ttk.Frame(self, padding=(10, 0, 10, 4))
        filt.pack(fill="x")
        ttk.Label(filt, text="Show", style="Muted.TLabel").pack(side="left")
        self.sev_vars = {}
        for sev in ("blocker", "major", "minor", "info"):
            v = tk.BooleanVar(value=True)
            self.sev_vars[sev] = v
            ttk.Checkbutton(filt, text=sev.capitalize(), variable=v,
                            command=self._fill).pack(side="left", padx=(4, 0))
        ttk.Label(filt, text="  category", style="Muted.TLabel").pack(
            side="left", padx=(10, 2))
        self.cat_var = tk.StringVar(value="all")
        cb = ttk.Combobox(filt, width=20, state="readonly",
                          textvariable=self.cat_var,
                          values=["all"] + [CAT_LABEL[c] for c in CATS])
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda _e: self._fill())
        self.summary = ttk.Label(filt, style="Muted.TLabel", text="")
        self.summary.pack(side="right")

        frame, self.tree = make_tree(
            self, theme,
            [("sev", "SEVERITY"), ("cat", "CATEGORY"), ("code", "CODE"),
             ("finding", "FINDING"), ("where", "WHERE")],
            (90, 130, 150, 460, 90), height=16)
        frame.pack(fill="both", expand=True, padx=10, pady=4)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: None)
        self.tree.bind("<Double-Button-1>", lambda _e: self.jump())
        for sev, col in SEV_COLOR.items():
            self.tree.tag_configure(sev, foreground=col)

        act = ttk.Frame(self, padding=(10, 2, 10, 8))
        act.pack(fill="x")
        ttk.Button(act, text="Jump to location",
                   command=self.jump).pack(side="left")
        ttk.Button(act, text="Write markups on the plan…",
                   style="Accent.TButton",
                   command=self.write_markups).pack(side="left", padx=6)
        ttk.Button(act, text="Mark on the Loft",
                   command=self.mark_loft).pack(side="left")
        ttk.Button(act, text="Log as lesson",
                   command=self.log_lesson).pack(side="left", padx=6)
        self.skip_btn = ttk.Button(act, text="Not checked…",
                                   command=self.show_skipped)
        self.skip_btn.pack(side="right")
        self.detail = ttk.Label(self, style="Muted.TLabel", text="",
                                padding=(10, 0, 10, 8), wraplength=1000)
        self.detail.pack(fill="x", side="bottom")

    # -------------------------------------------------------------- running
    def _run(self, work, label):
        self.status.set(f"Backcheck: {label}…")

        def done(rep, err):
            if err:
                self.status.set(f"Backcheck failed: {err}", "err")
                return
            self.report = rep
            rep.sort()
            self._fill()
            n = len(rep.findings)
            b = len(rep.by_severity("blocker"))
            toast(self.winfo_toplevel(), self.theme,
                  f"Backcheck: {n} finding(s)"
                  + (f", {b} blocker(s)" if b else "") + f" — {label}")
            self.status.set(f"Backcheck: {n} finding(s) — {label}", "ok")

        run_bg(self, work, done)

    def check_loft(self):
        loft = self.get_loft() if self.get_loft else None
        if loft is None or not getattr(loft, "model", None) or \
                not loft.model.ents:
            messagebox.showinfo("Backcheck", "Draft something in the Loft "
                                             "first.")
            return
        model = loft.model
        hw = self._hw()
        self._source_pdf = None
        self._run(lambda: backcheck.check_loft(model, heartwood_path=hw),
                  "the Loft draft")

    def check_plan(self):
        path, _viewer = (self.get_plan() if self.get_plan else (None, None))
        if not path or not str(path).lower().endswith(".pdf"):
            messagebox.showinfo("Backcheck", "Open a plan PDF in Plan "
                                             "Viewing first.")
            return
        hw = self._hw()
        self._source_pdf = path
        self._run(lambda: backcheck.check_pdf(path, heartwood_path=hw),
                  os.path.basename(path))

    def check_file(self):
        p = filedialog.askopenfilename(
            filetypes=[("Checkable", "*.pdf *.dxf *.obj *.loft.json"),
                       ("All", "*.*")])
        if not p:
            return
        hw = self._hw()
        self._source_pdf = p if p.lower().endswith(".pdf") else None
        self._run(lambda: backcheck.check(p, heartwood_path=hw),
                  os.path.basename(p))

    def _hw(self):
        if self.hw_path_provider:
            try:
                return self.hw_path_provider()
            except Exception:   # noqa: BLE001 -- lessons are optional
                return None
        return None

    # --------------------------------------------------------------- fill
    def _visible(self, f):
        if not self.sev_vars.get(f.severity, tk.BooleanVar(value=True)).get():
            return False
        cat = self.cat_var.get()
        if cat != "all" and CAT_LABEL.get(f.category) != cat:
            return False
        return True

    def _fill(self):
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        if self.report is None:
            self.summary.configure(text="")
            return
        shown = 0
        for f in self.report.findings:
            if not self._visible(f):
                continue
            where = (f"p.{f.page}" if f.page else
                     (f"{f.where[0]:.0f},{f.where[1]:.0f}"
                      if f.where and len(f.where) == 2 else "—"))
            self.tree.insert(
                "", "end", iid=f.id, tags=(f.severity,),
                values=(SEV_CHIP.get(f.severity, f.severity),
                        CAT_LABEL.get(f.category, f.category), f.code,
                        f.title, where))
            self._rows[f.id] = f
            shown += 1
        st = self.report.stats
        bysev = st.get("by_severity", {})
        self.summary.configure(
            text=f"{shown} shown / {len(self.report.findings)} total   ·   "
                 f"{bysev.get('blocker', 0)} blocker  "
                 f"{bysev.get('major', 0)} major  "
                 f"{bysev.get('minor', 0)} minor  "
                 f"{bysev.get('info', 0)} info")
        nskip = len(st.get("skipped", []))
        self.skip_btn.configure(
            text=f"Not checked ({nskip})…" if nskip else "Not checked…")

    def _selected(self):
        sel = self.tree.selection()
        return self._rows.get(sel[0]) if sel else None

    # --------------------------------------------------------------- jump
    def jump(self):
        f = self._selected()
        if f is None:
            return
        self.detail.configure(
            text=f"{f.code} · {f.severity.upper()} · {f.detail}  →  "
                 f"{f.suggestion}   [rule: {f.rule}]")
        if f.source == "pdf" and f.page and self.goto_plan:
            self.goto_plan()
            _path, viewer = self.get_plan()
            if viewer is not None and viewer.doc is not None:
                try:
                    viewer.goto(f.page)
                except Exception:   # noqa: BLE001
                    pass
        elif f.source in ("loft", "pipe") and self.goto_loft:
            self.goto_loft()
            loft = self.get_loft()
            if loft is not None:
                if f.ent_ids:
                    loft.sel = set(f.ent_ids)
                if f.where and len(f.where) == 2:
                    self._center_loft(loft, f.where[0], f.where[1])
                loft.redraw()
                loft._traits_refresh()
                if f.where and len(f.where) == 2:
                    try:
                        loft._flourish_ring(f.where[0], f.where[1])
                    except Exception:   # noqa: BLE001 -- flourish is cosmetic
                        pass

    @staticmethod
    def _center_loft(loft, x, y):
        w = max(loft.cv.winfo_width(), 400)
        h = max(loft.cv.winfo_height(), 300)
        loft.vx = x - w / 2 / loft.ppf
        loft.vy = y + h / 2 / loft.ppf

    # ------------------------------------------------------------ markups
    def write_markups(self):
        if self.report is None or not self.report.findings:
            messagebox.showinfo("Backcheck", "Run a check first.")
            return
        if not self._source_pdf:
            messagebox.showinfo(
                "Backcheck", "Writing markup annotations needs a plan PDF. "
                             "Check an open plan or a PDF file, or use "
                             "'Mark on the Loft' for a draft.")
            return
        out = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            initialfile=os.path.splitext(
                os.path.basename(self._source_pdf))[0] + "_backcheck.pdf",
            filetypes=[("PDF", "*.pdf")])
        if not out:
            return
        rep, src = self.report, self._source_pdf

        def done(n, err):
            if err:
                self.status.set(f"Markup write failed: {err}", "err")
                return
            toast(self.winfo_toplevel(), self.theme,
                  f"Wrote {n} finding annotation(s) — clouds + comments")
            open_path(out)

        run_bg(self, lambda: backcheck.write_markup_pdf(rep, src, out), done)

    def mark_loft(self):
        """Drop the findings onto a Q-BACK ply as text marks — real Loft
        entities, cleared in one delete of the ply."""
        loft = self.get_loft() if self.get_loft else None
        if loft is None or self.report is None:
            return
        pts = backcheck.loft_finding_points(self.report)
        if not pts:
            messagebox.showinfo("Backcheck", "No Loft-located findings to "
                                             "mark.")
            return
        from .. import draft
        model = loft.model
        if model.ply("Q-BACK") is None:
            model.add_ply(draft.Ply(name="Q-BACK", color="#c1121f",
                                    weight="light"))
        for x, y, sev, code, _detail in pts:
            model.add("text", [(x, y)], text=code, size="body", ply="Q-BACK")
        if self.goto_loft:
            self.goto_loft()
        loft.refresh_all()
        toast(self.winfo_toplevel(), self.theme,
              f"Marked {len(pts)} finding(s) on the Q-BACK ply "
              f"(delete the ply to clear)")

    def log_lesson(self):
        f = self._selected()
        if f is None:
            messagebox.showinfo("Backcheck", "Select a finding first.")
            return
        hw = self._hw()
        if not hw:
            messagebox.showinfo("Backcheck", "The Heartwood is not available "
                                             "— lessons need it.")
            return

        def done(_n, err):
            if err:
                self.status.set(f"Lesson failed: {err}", "err")
                return
            toast(self.winfo_toplevel(), self.theme,
                  "Logged as a lesson (unverified — trust it in the Old "
                  "Hand's Manage screen to catch repeats)")

        run_bg(self, lambda: backcheck.record_lesson(hw, f), done)

    def show_skipped(self):
        if self.report is None:
            messagebox.showinfo("Backcheck", "Run a check first.")
            return
        sk = self.report.stats.get("skipped", [])
        if not sk:
            messagebox.showinfo("Backcheck — not checked",
                                "Everything in scope for this source was "
                                "checked.")
            return
        lines = ["The Backcheck is honest about what it cannot prove from a "
                 "2D plan / BIM draft:\n"]
        for s in sk:
            lines.append(f"• {s['code']}: {s['reason']}")
        messagebox.showinfo("Backcheck — not checked", "\n\n".join(lines))

    def commands(self):
        return [
            ("Backcheck the Loft draft", "Backcheck", self.check_loft),
            ("Backcheck the open plan", "Backcheck", self.check_plan),
            ("Backcheck a file…", "Backcheck", self.check_file),
        ]

    def refresh(self):
        pass
