"""Combine PDFs tab: drag-drop ordering, per-file page ranges and rotation,
bookmarks per source; plus split/extract.  Engine: rfi_stamper.merge."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from .. import merge
from . import dnd
from .widgets import DropZone, LogConsole, Tooltip, make_tree, open_path, run_bg


class MergeTab(ttk.Frame):
    def __init__(self, parent, theme, status):
        super().__init__(parent, padding=10)
        self.theme = theme
        self.status = status
        self.items: list[merge.MergeItem] = []
        self._drag_iid = None
        self._running = False        # guards re-entry via palette/keyboard

        DropZone(self, theme,
                 "Drop PDFs here to combine — drag rows to reorder, "
                 "double-click Pages / Rotation to edit",
                 self.add_paths, exts=(".pdf",), browse=self.add_files,
                 height=46).pack(fill="x")

        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True, pady=6)
        frame, self.tree = make_tree(
            mid, theme,
            [("n", "#"), ("file", "FILE"), ("pages", "PAGES"),
             ("rot", "ROTATION"), ("count", "PAGE COUNT"), ("bm", "BOOKMARK")],
            (30, 330, 110, 80, 90, 180), height=10)
        frame.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", self.edit_cell)
        self.tree.bind("<ButtonPress-1>", self._drag_start, add="+")
        self.tree.bind("<B1-Motion>", self._drag_move)
        Tooltip(self.tree, "Drag rows to reorder. Double-click PAGES for a range "
                           "like 1-3,7,9-  •  ROTATION cycles 0/90/180/270  •  "
                           "BOOKMARK renames the outline entry", theme)
        dnd.enable_drop(self.tree, self.add_paths, exts=(".pdf",))

        bcol = ttk.Frame(mid)
        bcol.pack(side="left", padx=6)
        for txt, cmd in (("Add…", self.add_files), ("Remove", self.remove_sel),
                         ("▲ Up", lambda: self.nudge(-1)),
                         ("▼ Down", lambda: self.nudge(1)),
                         ("Clear", self.clear)):
            ttk.Button(bcol, text=txt, command=cmd).pack(fill="x", pady=1)

        opts = ttk.Frame(self)
        opts.pack(fill="x", pady=2)
        self.bm_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Add a bookmark per source file",
                        variable=self.bm_var).pack(side="left")
        ttk.Label(opts, text="Output:").pack(side="left", padx=(16, 4))
        self.out_var = tk.StringVar()
        ttk.Entry(opts, textvariable=self.out_var).pack(side="left", fill="x",
                                                        expand=True)
        ttk.Button(opts, text="…", width=3, command=self.pick_out).pack(side="left",
                                                                        padx=4)
        self.combine_btn = ttk.Button(opts, text="Combine", style="Accent.TButton",
                                      command=self.combine)
        self.combine_btn.pack(side="left", padx=4)
        self.open_btn = ttk.Button(opts, text="Open result", state="disabled",
                                   command=lambda: self._out
                                   and open_path(self._out))
        self.open_btn.pack(side="left")
        self._out = None

        # ------------------------------------------------------------ split
        sp = ttk.LabelFrame(self, text="Split / extract")
        sp.pack(fill="x", pady=(8, 2))
        r = ttk.Frame(sp)
        r.pack(fill="x", padx=4, pady=4)
        ttk.Label(r, text="PDF:").pack(side="left")
        self.split_var = tk.StringVar()
        se = ttk.Entry(r, textvariable=self.split_var)
        se.pack(side="left", fill="x", expand=True, padx=4)
        dnd.enable_drop(se, lambda p: p and self.split_var.set(p[0]), exts=(".pdf",))
        ttk.Button(r, text="…", width=3, command=self.pick_split).pack(side="left")
        r2 = ttk.Frame(sp)
        r2.pack(fill="x", padx=4, pady=(0, 4))
        self.split_mode = tk.StringVar(value="every")
        ttk.Radiobutton(r2, text="Every", variable=self.split_mode,
                        value="every").pack(side="left")
        self.every_var = tk.StringVar(value="1")
        ttk.Entry(r2, textvariable=self.every_var, width=4).pack(side="left")
        ttk.Label(r2, text="page(s)").pack(side="left", padx=(2, 12))
        ttk.Radiobutton(r2, text="Ranges", variable=self.split_mode,
                        value="ranges").pack(side="left")
        self.ranges_var = tk.StringVar(value="1-3; 4-")
        rentry = ttk.Entry(r2, textvariable=self.ranges_var, width=24)
        rentry.pack(side="left", padx=2)
        Tooltip(rentry, "Semicolon-separated ranges, one output file each:\n"
                        "1-3; 4-10; 11-", self.theme)
        ttk.Button(r2, text="Split", command=self.split).pack(side="left", padx=10)

        self.log = LogConsole(self, theme, height=5)
        self.log.pack(fill="both", expand=True, pady=(6, 0))

    def commands(self):
        return [
            ("Add PDFs to combine", "Combine", self.add_files),
            ("Combine PDFs now", "Combine", self.combine),
            ("Split a PDF", "Combine", self.split),
            ("Clear combine list", "Combine", self.clear),
        ]

    # --------------------------------------------------------------- items
    def add_paths(self, paths):
        for p in paths:
            if os.path.isdir(p):
                self.add_paths(sorted(
                    os.path.join(p, f) for f in os.listdir(p)
                    if f.lower().endswith(".pdf")))
                continue
            if not p.lower().endswith(".pdf"):
                continue
            try:
                n = merge.pdf_page_count(p)
            except Exception as e:      # noqa: BLE001
                self.log.say(f"!! cannot read {os.path.basename(p)}: {e}")
                continue
            item = merge.MergeItem(path=p,
                                   bookmark=os.path.splitext(os.path.basename(p))[0])
            self.items.append(item)
            self.tree.insert("", "end", values=(len(self.items),
                                                os.path.basename(p), "all", "0°",
                                                n, item.bookmark))
        self._renumber()
        if not self.out_var.get() and self.items:
            self.out_var.set(os.path.join(os.path.dirname(self.items[0].path),
                                          "combined.pdf"))

    def add_files(self):
        self.add_paths(filedialog.askopenfilenames(filetypes=[("PDF", "*.pdf")]))

    def remove_sel(self):
        for iid in reversed(self.tree.selection()):
            idx = self.tree.index(iid)
            self.tree.delete(iid)
            del self.items[idx]
        self._renumber()

    def clear(self):
        self.tree.delete(*self.tree.get_children())
        self.items.clear()

    def nudge(self, delta):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        idx = self.tree.index(iid)
        new = max(0, min(len(self.items) - 1, idx + delta))
        if new == idx:
            return
        self.items.insert(new, self.items.pop(idx))
        self.tree.move(iid, "", new)
        self._renumber()

    def _drag_start(self, event):
        self._drag_iid = self.tree.identify_row(event.y)

    def _drag_move(self, event):
        if not self._drag_iid:
            return
        target = self.tree.identify_row(event.y)
        if not target or target == self._drag_iid:
            return
        src, dst = self.tree.index(self._drag_iid), self.tree.index(target)
        self.items.insert(dst, self.items.pop(src))
        self.tree.move(self._drag_iid, "", dst)
        self._renumber()

    def _renumber(self):
        for i, iid in enumerate(self.tree.get_children(), start=1):
            self.tree.set(iid, "n", i)

    def edit_cell(self, event):
        iid = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not iid:
            return
        idx = self.tree.index(iid)
        item = self.items[idx]
        if col == "#3":      # pages
            cur = "" if item.pages in ("", "all") else item.pages
            new = simpledialog.askstring(
                "Page range", "Pages to pull (e.g. 1-3,7,9- ; empty = all):",
                initialvalue=cur, parent=self)
            if new is None:
                return
            new = new.strip()
            if new:
                try:
                    merge.parse_page_range(new, merge.pdf_page_count(item.path))
                except ValueError as e:
                    messagebox.showwarning("Combine", str(e))
                    return
            item.pages = new
            self.tree.set(iid, "pages", new or "all")
        elif col == "#4":    # rotation cycles
            item.rotation = (item.rotation + 90) % 360
            self.tree.set(iid, "rot", f"{item.rotation}°")
        elif col == "#6":    # bookmark
            new = simpledialog.askstring("Bookmark", "Outline title for this file:",
                                         initialvalue=item.bookmark, parent=self)
            if new is not None:
                item.bookmark = new.strip()
                self.tree.set(iid, "bm", item.bookmark)

    # -------------------------------------------------------------- actions
    def pick_out(self):
        p = filedialog.asksaveasfilename(defaultextension=".pdf",
                                         filetypes=[("PDF", "*.pdf")])
        if p:
            self.out_var.set(p)

    def combine(self):
        if self._running:
            return
        if not self.items:
            messagebox.showwarning("Combine", "Add at least one PDF first.")
            return
        out = self.out_var.get().strip()
        if not out:
            self.pick_out()
            out = self.out_var.get().strip()
            if not out:
                return
        self._running = True
        self.combine_btn.configure(state="disabled")
        self.status.set("Combining…")
        items = [merge.MergeItem(**vars(i)) for i in self.items]
        bm = self.bm_var.get()

        def work():
            return merge.merge_pdfs(items, out, bookmarks=bm, log=self.log.say)

        def done(res, err):
            self._running = False
            self.combine_btn.configure(state="normal")
            if err:
                self.log.say(f"!! combine failed: {err}")
                self.status.set("Combine failed — see log", "err")
                return
            self._out = res["out_path"]
            self.open_btn.configure(state="normal")
            self.status.set(f"Combined {res['files']} file(s) → "
                            f"{res['pages']} pages", "ok")
            self.log.say(f"wrote {self._out}")

        run_bg(self, work, done)

    def pick_split(self):
        p = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf")])
        if p:
            self.split_var.set(p)

    def split(self):
        if self._running:
            return
        src = self.split_var.get().strip()
        if not src:
            messagebox.showwarning("Split", "Pick a PDF to split first.")
            return
        out_dir = filedialog.askdirectory(title="Output folder for the pieces")
        if not out_dir:
            return
        kw = {}
        if self.split_mode.get() == "every":
            try:
                kw["every"] = max(1, int(self.every_var.get()))
            except ValueError:
                messagebox.showwarning("Split", "'Every' needs a number of pages.")
                return
        else:
            kw["ranges"] = self.ranges_var.get().strip()

        self._running = True

        def work():
            return merge.split_pdf(src, out_dir, log=self.log.say, **kw)

        def done(paths, err):
            self._running = False
            if err:
                self.log.say(f"!! split failed: {err}")
                self.status.set("Split failed — see log", "err")
                return
            self.status.set(f"Split into {len(paths)} file(s)", "ok")
            for p in paths:
                self.log.say(f"wrote {p}")

        run_bg(self, work, done)
