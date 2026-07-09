"""PDF Tools tab: one-touch fixes for common PDF problems, all offline and
run in the background so the end user is never blocked.

Diagnose lists what's wrong with a dropped PDF and offers a fix per issue;
the big-button grid runs any fix directly.  Everything writes a new file
(never mutates the input) and pops a toast when done.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from .. import hyperlink, ocr, pdfdoctor
from . import dnd
from .widgets import DropZone, LogConsole, Tooltip, make_tree, open_path, run_bg, toast


class PdfToolsTab(ttk.Frame):
    def __init__(self, parent, theme, status, root):
        super().__init__(parent, padding=12)
        self.theme = theme
        self.status = status
        self.root = root
        self._running = False
        self._out = None
        self.drop_hint = "PDF Tools — drop a PDF to diagnose & fix"

        ttk.Label(self, text="PDF Tools", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self, style="Muted.TLabel",
                  text="Repair, unlock, compress, OCR, hyperlink and more — "
                       "one touch, in the background, never overwriting your "
                       "original.").pack(anchor="w", pady=(0, 8))

        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Label(row, text="PDF:").pack(side="left")
        self.path_var = tk.StringVar()
        e = ttk.Entry(row, textvariable=self.path_var)
        e.pack(side="left", fill="x", expand=True, padx=6)
        dnd.enable_drop(e, self._set_path, exts=(".pdf",))
        ttk.Button(row, text="Browse…", command=self.browse).pack(side="left")
        ttk.Button(row, text="Diagnose", style="Accent.TButton",
                   command=self.diagnose).pack(side="left", padx=(6, 0))

        DropZone(self, theme, "Drop a PDF here to diagnose and repair",
                 self._set_path, exts=(".pdf",), browse=self.browse,
                 height=54, big=True).pack(fill="x", pady=(6, 10))

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)

        # issues panel -----------------------------------------------------
        left = ttk.LabelFrame(body, text="Diagnosis — double-click an issue to fix it")
        left.pack(side="left", fill="both", expand=True)
        frame, self.tree = make_tree(
            left, theme,
            [("sev", "SEVERITY"), ("issue", "ISSUE"), ("detail", "DETAIL")],
            (80, 180, 320), height=9)
        frame.pack(fill="both", expand=True, padx=4, pady=4)
        self.tree.bind("<Double-1>", self._fix_selected_issue)
        self._issues = {}

        # one-touch button grid -------------------------------------------
        right = ttk.LabelFrame(body, text="One-touch fixes")
        right.pack(side="left", fill="y", padx=(10, 0))
        grid = ttk.Frame(right, padding=6)
        grid.pack(fill="both", expand=True)
        buttons = [
            ("⚕ Auto-Fix", "Unlock, repair and strip hidden metadata in one "
             "pass, verified safe.", self.auto_fix, True),
            ("🔓 Unlock", "Remove password / owner encryption.", self.unlock, False),
            ("🔧 Repair", "Rebuild a broken/corrupt PDF structure.", self.repair, False),
            ("🗜 Compress", "Downsample images and deflate to shrink the file.",
             self.compress, False),
            ("🔍 Make searchable (OCR)", "Planloom's own OCR — reads scanned "
             "title-block and large lettering, cross-checked against the set's "
             "sheet index. Built in, no install, fully offline.", self.ocr,
             False),
            ("🔗 Auto-Hyperlink", "Link every sheet reference to its page; "
             "works in any viewer.", self.autolink, False),
            ("▦ Flatten annotations", "Bake markups/forms into the page.",
             self.flatten, False),
            ("🖼 Flatten to image", "Rasterize pages (reverse-OCR / lock content).",
             self.rasterize, False),
            ("⤢ Upscale", "Re-render at high resolution.", self.upscale, False),
            ("🌐 Web-optimize", "Linearize for fast streaming.", self.linearize, False),
            ("🧹 Strip metadata", "Remove author, producer and hidden data (NDA).",
             self.strip_meta, False),
            ("⟲ Normalize rotation", "Bake /Rotate so every viewer agrees.",
             self.normalize_rot, False),
            ("🛡 Remove scripts", "Strip embedded JavaScript.", self.remove_js, False),
            ("📎 Remove attachments", "Strip embedded files.", self.remove_files, False),
        ]
        for i, (label, tip, cmd, accent) in enumerate(buttons):
            b = ttk.Button(grid, text=label, width=24, command=cmd,
                           style="Accent.TButton" if accent else "TButton")
            b.grid(row=i, column=0, sticky="ew", pady=2)
            Tooltip(b, tip, theme)
        self.open_btn = ttk.Button(grid, text="Open result", state="disabled",
                                   command=lambda: self._out and open_path(self._out))
        self.open_btn.grid(row=len(buttons), column=0, sticky="ew", pady=(8, 2))

        self.log = LogConsole(self, theme, height=5)
        self.log.pack(fill="x", pady=(8, 0))

    # ------------------------------------------------------------- commands
    def commands(self):
        return [
            ("Diagnose PDF problems", "PDF Tools", self.diagnose),
            ("Auto-Fix PDF", "PDF Tools", self.auto_fix),
            ("Unlock PDF", "PDF Tools", self.unlock),
            ("Repair PDF", "PDF Tools", self.repair),
            ("Compress PDF", "PDF Tools", self.compress),
            ("OCR PDF (make searchable — the Tracer)", "PDF Tools", self.ocr),
            ("Auto-Hyperlink sheet references", "PDF Tools", self.autolink),
            ("Flatten annotations", "PDF Tools", self.flatten),
            ("Flatten PDF to image", "PDF Tools", self.rasterize),
            ("Strip PDF metadata", "PDF Tools", self.strip_meta),
        ]

    def handle_drop(self, paths):
        for p in paths:
            if p.lower().endswith(".pdf"):
                self._set_path([p])
                return

    # --------------------------------------------------------------- inputs
    def browse(self):
        p = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf")])
        if p:
            self._set_path([p])

    def _set_path(self, paths):
        if paths:
            self.path_var.set(paths[0])
            self.diagnose()

    def _path(self):
        p = self.path_var.get().strip()
        if not p or not os.path.exists(p):
            messagebox.showwarning("PDF Tools", "Pick a PDF first.")
            return None
        return p

    def _out_path(self, suffix):
        stem, _ = os.path.splitext(self.path_var.get().strip())
        return f"{stem}_{suffix}.pdf"

    # ------------------------------------------------------------ diagnose
    def diagnose(self):
        path = self._path()
        if not path:
            return
        self.status.set("Diagnosing…")

        def work():
            return pdfdoctor.diagnose(path, log=self.log.say)

        def done(issues, err):
            self.tree.delete(*self.tree.get_children())
            self._issues.clear()
            if err:
                self.status.set(f"Diagnose failed: {err}", "err")
                return
            if not issues:
                self.tree.insert("", "end",
                                 values=("ok", "No problems found", "This PDF looks healthy."))
                self.status.set("No problems found", "ok")
                return
            for i, iss in enumerate(issues):
                iid = f"iss{i}"
                self._issues[iid] = iss
                self.tree.insert("", "end", iid=iid,
                                 values=(iss.severity.upper(), iss.title, iss.detail))
            self.status.set(f"{len(issues)} issue(s) found — double-click to fix", "ok")

        run_bg(self, work, done)

    def _fix_selected_issue(self, _e):
        sel = self.tree.selection()
        if not sel or sel[0] not in self._issues:
            return
        iss = self._issues[sel[0]]
        fixmap = {
            "unlock": self.unlock, "repair": self.repair, "compress": self.compress,
            "ocr": self.ocr, "strip_metadata": self.strip_meta,
            "remove_javascript": self.remove_js,
            "remove_embedded_files": self.remove_files,
            "normalize_rotation": self.normalize_rot, "linearize": self.linearize,
            "flatten_annotations": self.flatten,
        }
        fn = fixmap.get(iss.fix)
        if fn:
            fn()
        else:
            messagebox.showinfo("PDF Tools", f"No one-touch fix for '{iss.title}'.")

    # -------------------------------------------------------------- runner
    def _run(self, label, fn, suffix, done_msg):
        """Run an engine fn(in, out, log)->result in the background."""
        path = self._path()
        if not path or self._running:
            return
        out = self._out_path(suffix)
        self._running = True
        self.open_btn.configure(state="disabled")
        self.status.set(f"{label}…")

        def work():
            return fn(path, out)

        def done(res, err):
            self._running = False
            if err:
                self.log.say(f"!! {label} failed: {err}")
                self.status.set(f"{label} failed — see log", "err")
                toast(self.root, self.theme, f"{label} failed", "err")
                return
            self._out = out
            self.open_btn.configure(state="normal")
            msg = done_msg(res) if callable(done_msg) else done_msg
            self.status.set(msg, "ok")
            self.log.say(f"wrote {out}")
            toast(self.root, self.theme, msg)
            self.diagnose()

        run_bg(self, work, done)

    # ------------------------------------------------------------- actions
    def auto_fix(self):
        do_compress = messagebox.askyesno(
            "Auto-Fix", "Also compress (downsample images)?\n\n"
            "Yes = smaller file   No = structure only")
        self._run("Auto-Fix",
                  lambda p, o: pdfdoctor.auto_fix(p, o, do_compress=do_compress,
                                                  log=self.log.say),
                  "fixed",
                  lambda r: "Auto-Fixed: " + ", ".join(r.get("actions", [])
                                                       or ["nothing needed"]))

    def unlock(self):
        pw = ""
        if pdfdoctor.is_encrypted(self.path_var.get().strip()):
            pw = simpledialog.askstring(
                "Unlock", "Password (leave blank for owner-locked PDFs):",
                show="•", parent=self) or ""

        def fn(p, o):
            if not pdfdoctor.unlock(p, o, password=pw, log=self.log.say):
                raise RuntimeError("could not unlock — wrong password?")
            return {"out_path": o}
        self._run("Unlock", fn, "unlocked", "Unlocked")

    def repair(self):
        self._run("Repair", lambda p, o: pdfdoctor.repair(p, o, log=self.log.say),
                  "repaired", "Repaired")

    def compress(self):
        self._run("Compress",
                  lambda p, o: pdfdoctor.compress(p, o, log=self.log.say),
                  "compressed",
                  lambda r: f"Compressed {r['before']//1024} KB → "
                            f"{r['after']//1024} KB ({(1-r['ratio'])*100:.0f}% smaller)")

    def ocr(self):
        """Make a scanned PDF searchable with Planloom's built-in OCR (the
        Tracer) — always available, no external engine, fully offline.

        ``ocr.ocr_pdf`` runs the Tracer with P3 post-correction ON: a default
        trade lexicon + the document's own sheet index (auto-harvested inside
        ocr_pdf) cross-check every read, so a smudged S-1O1 snaps to the real
        S-101 in the set, number-locked."""
        self._run("OCR",
                  lambda p, o: ocr.ocr_pdf(p, o, log=self.log.say),
                  "searchable",
                  lambda r: f"OCR complete — {r['pages_ocred']}/{r['pages_total']} "
                            "page(s) made searchable (cross-checked against the "
                            "set's own sheet index)")

    # Back-compat alias: the palette command and older callers still reach the
    # single built-in OCR action through ``tracer_ocr``.
    def tracer_ocr(self):
        """Alias for :meth:`ocr` — the one built-in OCR action (the Tracer)."""
        self.ocr()

    def autolink(self):
        self._run("Auto-Hyperlink",
                  lambda p, o: hyperlink.auto_link(p, o, log=self.log.say),
                  "linked",
                  lambda r: f"Linked {r.links_added} reference(s) across "
                            f"{r.pages_touched} page(s); {r.sheets_indexed} "
                            "sheets indexed")

    def flatten(self):
        self._run("Flatten annotations",
                  lambda p, o: pdfdoctor.flatten_annotations(p, o, log=self.log.say),
                  "flattened", "Annotations flattened")

    def rasterize(self):
        dpi = simpledialog.askinteger("Flatten to image", "Resolution (DPI):",
                                      initialvalue=200, minvalue=72, maxvalue=600,
                                      parent=self) or 200
        self._run("Flatten to image",
                  lambda p, o: pdfdoctor.rasterize(p, o, dpi=dpi, log=self.log.say),
                  "raster", "Flattened to image")

    def upscale(self):
        dpi = simpledialog.askinteger("Upscale", "Target render DPI:",
                                      initialvalue=300, minvalue=150, maxvalue=600,
                                      parent=self) or 300
        self._run("Upscale",
                  lambda p, o: pdfdoctor.upscale(p, o, dpi=dpi, log=self.log.say),
                  "upscaled", "Upscaled")

    def linearize(self):
        self._run("Web-optimize",
                  lambda p, o: pdfdoctor.linearize(p, o, log=self.log.say),
                  "web", "Web-optimized (linearized)")

    def strip_meta(self):
        self._run("Strip metadata",
                  lambda p, o: pdfdoctor.strip_metadata(p, o, log=self.log.say),
                  "clean",
                  lambda r: "Metadata stripped: "
                            + (", ".join(r.get("removed", [])) or "clean already"))

    def normalize_rot(self):
        self._run("Normalize rotation",
                  lambda p, o: pdfdoctor.normalize_rotation(p, o, log=self.log.say),
                  "unrotated", "Rotation normalized")

    def remove_js(self):
        self._run("Remove scripts",
                  lambda p, o: pdfdoctor.remove_javascript(p, o, log=self.log.say),
                  "nojs", "Embedded JavaScript removed")

    def remove_files(self):
        self._run("Remove attachments",
                  lambda p, o: pdfdoctor.remove_embedded_files(p, o, log=self.log.say),
                  "noattach", "Embedded files removed")
