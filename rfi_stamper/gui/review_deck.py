"""The review deck — human confirmation of uncertain OCR reads.

Confidence-routed human-in-the-loop, the professional verification-station
shape: the reviewer sees ONLY the uncertain band (τ_lo <= conf < τ_hi)
plus every machine repair the corrector changed (index snap, lexicon
snap, grammar repair — lifted above τ_hi, so a pure mid-band filter would
hide exactly the tokens where the machine overrode the pixels).  The
IMAGE is ground truth: word crop + per-glyph strip beside the editable
text, always.  Keyboard-first: Enter accepts, Tab skips, Shift+Tab goes
back, Ctrl+Enter batch-accepts above a threshold, Esc closes.

Learning is human-gated (the profile.py contract): an accepted EDIT files
per-glyph corrections into a pending queue only when the edit length
matches the glyph count (otherwise the cell↔char alignment is unknown —
a segmentation error is not a label; the text still flows to overrides
and audit).  NOTHING reaches the kNN memory until the explicit Promote
button; promote then offers to save the memory as a per-firm font
profile (~/.planloom/fontprofiles/).  Auto-accepted reads never train.

"Apply N accepted…" re-runs the searchable writer with the accepted
texts as overrides — deterministic, never in-place PDF surgery; the
writer's pixel-diff verify re-proves the raster untouched.  Every
decision lands in an append-only local audit trail
(~/.planloom/tracer_reviews.jsonl), written atomically on close.

Tk rules honored: rows are DATA (one Treeview — never a widget or
PhotoImage per row; detail-pane-only rendering IS the virtual list);
every PhotoImage of the visible item is pinned on self._photos; key
handlers that own Tab return "break"; the deck is constructed on the UI
thread from run_bg's done callback, never in the worker.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import numpy as np

from .. import fsutil
from ..tracer import TAU_HI, classify
from ..tracer.fonts import CHARSET
from ..tracer.profile import Corrections, FontProfile
from .prefs import PREFS_DIR
from .tab_compare import np_to_photo
from .widgets import make_tree, run_bg, toast

AUDIT_PATH = os.path.join(PREFS_DIR, "tracer_reviews.jsonl")
PROFILE_DIR = os.path.join(PREFS_DIR, "fontprofiles")


def _crop_photo(gray, bbox, target_h=96):
    """Word crop -> integer-zoomed PhotoImage (gray -> RGB stack)."""
    x0, y0, x1, y1 = bbox
    crop = np.asarray(gray)[y0:y1 + 1, x0:x1 + 1]
    if crop.size == 0:
        crop = np.full((8, 8), 255, np.uint8)
    k = max(1, round(target_h / max(crop.shape[0], 1)))
    k = min(k, 8)
    big = np.repeat(np.repeat(crop, k, 0), k, 1)
    return np_to_photo(np.ascontiguousarray(np.stack([big] * 3, axis=-1)))


def _cell_photo(cell, k=3):
    """One normalized 28x28 classifier cell -> zoomed PhotoImage."""
    g = np.clip(255.0 - np.asarray(cell, np.float32) * 255.0,
                0, 255).astype(np.uint8)
    big = np.repeat(np.repeat(g, k, 0), k, 1)
    return np_to_photo(np.ascontiguousarray(np.stack([big] * 3, axis=-1)))


class ReviewDeck(tk.Toplevel):
    """One review session over one OCR run's queue (run-to-close)."""

    def __init__(self, parent, theme, items, *, src_pdf, dpi=300,
                 rerun=None, log=print, root=None, status=None):
        super().__init__(parent)
        self.title(f"Review uncertain reads — {len(items)} item(s)")
        self.theme = theme
        self.items = list(items)
        self.src_pdf = src_pdf
        self.dpi = int(dpi)
        self.rerun = rerun          # rerun(overrides, log) -> result
        self.log = log
        self.root = root or parent
        self.status = status
        self.decisions: dict = {}   # idx -> (action, final_text)
        self.corrections = Corrections()
        self.ensemble = classify.default_ensemble()   # ONE per session
        self._photos: list = []     # visible item's images (GC fence)
        self._page_gray: dict = {}  # page -> gray raster (small LRU)
        self._doc = None
        self._closed = False

        c = theme.colors
        self.configure(bg=c["bg"])
        top = ttk.Frame(self, padding=(8, 6))
        top.pack(fill="x")
        n_rep = sum(1 for it in self.items
                    if it.why.split("|")[0].endswith(
                        ("index_snap", "lexicon_snap", "grammar_repair")))
        ttk.Label(top, text=(f"{len(self.items)} uncertain read(s) · "
                             f"{n_rep} machine repair(s) to confirm — "
                             "the IMAGE is ground truth"),
                  font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Label(top, style="Muted.TLabel",
                  text="Enter accept · Tab skip · Shift+Tab back · "
                       "Ctrl+Enter batch · Esc close").pack(side="right")

        frame, self.tree = make_tree(
            self, theme,
            [("n", "#"), ("page", "pg"), ("conf", "conf"),
             ("read", "raw → text"), ("why", "why"), ("st", "status")],
            [40, 40, 60, 260, 190, 80], height=9)
        frame.pack(fill="both", expand=True, padx=8)
        for i, it in enumerate(self.items):
            arrow = f"{it.raw} → {it.text}" if it.raw != it.text else it.raw
            self.tree.insert("", "end", iid=str(i), values=(
                i + 1, it.page, f"{it.conf:.2f}", arrow, it.why, ""))
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show())

        mid = ttk.Frame(self, padding=8)
        mid.pack(fill="x")
        left = ttk.Frame(mid)
        left.pack(side="left", fill="x", expand=True)
        self.crop_lbl = tk.Label(left, bg=c["panel"], bd=0)
        self.crop_lbl.pack(anchor="w")
        self.glyph_row = ttk.Frame(left)
        self.glyph_row.pack(anchor="w", pady=(6, 0))
        right = ttk.Frame(mid)
        right.pack(side="right", padx=(12, 0))
        ttk.Label(right, text="Read as:").grid(row=0, column=0, sticky="w")
        self.entry = ttk.Entry(right, width=24, font=("Consolas", 12))
        self.entry.grid(row=0, column=1, padx=4)
        self.info_lbl = ttk.Label(right, style="Muted.TLabel",
                                  justify="left")
        self.info_lbl.grid(row=1, column=0, columnspan=2, sticky="w",
                           pady=(6, 0))

        bar = ttk.Frame(self, padding=8)
        bar.pack(fill="x")
        ttk.Button(bar, text="Accept (Enter)", style="Accent.TButton",
                   command=self.accept).pack(side="left")
        ttk.Button(bar, text="Reject read",
                   command=self.reject).pack(side="left", padx=4)
        ttk.Button(bar, text="Skip (Tab)",
                   command=self.skip).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y",
                                                   padx=8, pady=2)
        ttk.Label(bar, text="batch ≥").pack(side="left")
        self.batch_var = tk.StringVar(master=self, value=f"{TAU_HI:.2f}")
        ttk.Spinbox(bar, from_=0.5, to=1.0, increment=0.05, width=5,
                    textvariable=self.batch_var).pack(side="left", padx=2)
        ttk.Button(bar, text="Accept above (Ctrl+Enter)",
                   command=self.batch_accept).pack(side="left", padx=2)
        self.promote_btn = ttk.Button(bar, text="Promote 0 corrections…",
                                      command=self.promote, state="disabled")
        self.promote_btn.pack(side="right", padx=4)
        self.apply_btn = ttk.Button(bar, text="Apply 0 accepted…",
                                    style="Accent.TButton",
                                    command=self.apply_overrides,
                                    state="disabled")
        self.apply_btn.pack(side="right")

        for w in (self, self.entry):
            w.bind("<Return>", self._key_accept)
            w.bind("<Tab>", self._key_skip)
            w.bind("<Shift-Tab>", self._key_prev)
            w.bind("<Control-Return>", self._key_batch)
        self.bind("<Escape>", self._key_close)
        self.protocol("WM_DELETE_WINDOW", self.close)

        if self.items:
            self.tree.selection_set("0")
            self.tree.focus("0")
        self.entry.focus_set()

    # ------------------------------------------------------------ detail ---
    def _sel(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def _gray(self, page):
        """Rendered page raster at the OCR dpi (tiny per-session cache)."""
        if page in self._page_gray:
            return self._page_gray[page]
        from ..tracer.searchable import _gray_of_page
        import fitz
        if self._doc is None:
            self._doc = fitz.open(self.src_pdf)
        _pix, gray = _gray_of_page(self._doc[page - 1], self.dpi)
        if len(self._page_gray) >= 4:
            self._page_gray.pop(next(iter(self._page_gray)))
        self._page_gray[page] = gray
        return gray

    def _show(self):
        i = self._sel()
        if i is None:
            return
        it = self.items[i]
        self._photos = []           # release the previous item's images
        try:
            photo = _crop_photo(self._gray(it.page), it.bbox)
            self._photos.append(photo)
            self.crop_lbl.configure(image=photo)
        except Exception as exc:    # noqa: BLE001 -- crop must never kill it
            self.crop_lbl.configure(image="", text=f"(no crop: {exc})")
        for child in self.glyph_row.winfo_children():
            child.destroy()
        for cell, _span, ch, conf in it.glyphs[:24]:
            p = _cell_photo(cell)
            self._photos.append(p)
            cellf = ttk.Frame(self.glyph_row)
            cellf.pack(side="left", padx=1)
            tk.Label(cellf, image=p, bd=0).pack()
            style = "Muted.TLabel" if conf >= TAU_HI else "TLabel"
            ttk.Label(cellf, text=f"{ch}\n{conf:.2f}", style=style,
                      font=("Consolas", 8), justify="center").pack()
        decided = self.decisions.get(i)
        self.entry.delete(0, "end")
        self.entry.insert(0, decided[1] if decided and decided[1] is not None
                          else it.text)
        self.info_lbl.configure(text=(
            f"raw: {it.raw}    conf: {it.conf:.2f}\n"
            f"why: {it.why or '(mid-band)'}    page {it.page}"))

    # --------------------------------------------------------- decisions ---
    def _advance(self, step=1):
        i = self._sel()
        if i is None:
            return
        j = max(0, min(len(self.items) - 1, i + step))
        self.tree.selection_set(str(j))
        self.tree.focus(str(j))
        self.tree.see(str(j))

    def _mark(self, i, action, text):
        self.decisions[i] = (action, text)
        label = {"accept": "✓", "edit": "✓ edit", "reject": "✗",
                 "batch": "✓ batch"}[action]
        vals = list(self.tree.item(str(i), "values"))
        vals[-1] = label
        self.tree.item(str(i), values=vals)
        n_acc = sum(1 for a, _t in self.decisions.values() if a != "skip")
        self.apply_btn.configure(
            text=f"Apply {n_acc} accepted…",
            state="normal" if n_acc else "disabled")
        self.promote_btn.configure(
            text=f"Promote {len(self.corrections.pending)} corrections…",
            state="normal" if self.corrections.pending else "disabled")

    def accept(self):
        """Commit the Entry text; an EDIT with matching glyph count files
        per-glyph corrections (pending — nothing trains until Promote)."""
        i = self._sel()
        if i is None:
            return
        it = self.items[i]
        text = self.entry.get().strip().upper()
        if not text:
            return self.reject()
        action = "accept" if text == it.text else "edit"
        if action == "edit" and len(text) == len(it.glyphs):
            for (cell, _span, ch, _cf), tch in zip(it.glyphs, text):
                if tch != ch and tch in CHARSET:
                    self.corrections.record_correction(cell, tch)
        # length mismatch = segmentation error, not a label: no glyph-lane
        # recording; the text still flows to overrides + audit
        self._mark(i, action, text)
        self._advance()

    def reject(self):
        i = self._sel()
        if i is None:
            return
        self._mark(i, "reject", "")
        self._advance()

    def skip(self):
        self._advance()

    def batch_accept(self):
        """Accept every UNDECIDED item at/above the threshold, audit-tagged
        'batch'."""
        try:
            thr = float(self.batch_var.get())
        except ValueError:
            thr = TAU_HI
        cand = [i for i, it in enumerate(self.items)
                if i not in self.decisions and it.conf >= thr]
        if not cand:
            toast(self.root, self.theme, "nothing at/above the threshold",
                  "info")
            return
        if not messagebox.askyesno(
                "Batch accept", f"Accept {len(cand)} read(s) with conf ≥ "
                f"{thr:.2f} as written?", parent=self):
            return
        for i in cand:
            self._mark(i, "batch", self.items[i].text)

    # ----------------------------------------------------------- promote ---
    def promote(self):
        """The human gate: fold pending corrections into the kNN memory,
        then offer to persist it as a per-firm font profile."""
        n = len(self.corrections.pending)
        if not n:
            return
        if not messagebox.askyesno(
                "Promote", f"Promote {n} reviewed correction(s) into the "
                "recognizer's memory?", parent=self):
            return
        added = self.corrections.promote(self.ensemble)
        self._audit_rows = getattr(self, "_audit_rows", [])
        self._audit_rows.append({"action": "promote", "count": added})
        self._mark_refresh()
        self.log(f"  promoted {added} correction(s) to the kNN memory")
        label = simpledialog.askstring(
            "Font profile", "Save the adapted memory as a firm font "
            "profile?\nProfile label (blank = don't save):", parent=self)
        if label:
            os.makedirs(PROFILE_DIR, exist_ok=True)
            safe = "".join(ch for ch in label if ch.isalnum() or ch in "-_")
            path = os.path.join(PROFILE_DIR, f"{safe or 'profile'}.npz")
            FontProfile.from_ensemble(self.ensemble, label).save(path)
            self.log(f"  wrote font profile {path}")
            toast(self.root, self.theme, f"font profile saved: {safe}")

    def _mark_refresh(self):
        self.promote_btn.configure(
            text=f"Promote {len(self.corrections.pending)} corrections…",
            state="normal" if self.corrections.pending else "disabled")

    # ------------------------------------------------------------- apply ---
    def overrides(self) -> dict:
        """{(page, bbox): text} for every decided item (reject = '')."""
        out = {}
        for i, (action, text) in self.decisions.items():
            if action == "skip":
                continue
            it = self.items[i]
            out[(it.page, it.bbox)] = text
        return out

    def apply_overrides(self):
        """Re-run the searchable writer with the accepted texts."""
        ov = self.overrides()
        if not ov or self.rerun is None:
            return
        self.apply_btn.configure(state="disabled")

        def work():
            return self.rerun(ov, self.log)

        def done(_res, err):
            if err:
                self.log(f"!! apply failed: {err}")
                toast(self.root, self.theme, "apply failed — see log", "err")
                self.apply_btn.configure(state="normal")
                return
            toast(self.root, self.theme,
                  f"rewrote searchable text with {len(ov)} reviewed read(s)")
            if self.status is not None:
                self.status.set(f"{len(ov)} reviewed read(s) applied", "ok")

        run_bg(self, work, done)

    # -------------------------------------------------------------- audit ---
    def _audit(self):
        """Append one JSONL record per decision — append-only, local,
        atomic (read + extend + replace)."""
        rows = []
        ts = _dt.datetime.now().isoformat(timespec="seconds")
        doc = os.path.basename(self.src_pdf)
        for i, (action, text) in sorted(self.decisions.items()):
            it = self.items[i]
            rows.append({"ts": ts, "doc": doc, "page": it.page,
                         "bbox": list(it.bbox), "raw": it.raw,
                         "final": text, "action": action,
                         "conf": round(it.conf, 4), "why": it.why})
        for extra in getattr(self, "_audit_rows", []):
            rows.append(dict(extra, ts=ts, doc=doc))
        if not rows:
            return
        old = b""
        if os.path.exists(AUDIT_PATH):
            with open(AUDIT_PATH, "rb") as fh:
                old = fh.read()
        new = old + "".join(json.dumps(r) + "\n" for r in rows).encode()
        os.makedirs(PREFS_DIR, exist_ok=True)
        fsutil.atomic_write_bytes(new, AUDIT_PATH)

    def close(self):
        if self._closed:
            return
        undecided = len(self.items) - len(self.decisions)
        if undecided and not messagebox.askyesno(
                "Close review", f"{undecided} item(s) undecided — close "
                "anyway?", parent=self):
            return
        self._closed = True
        try:
            self._audit()
        finally:
            if self._doc is not None:
                self._doc.close()
            self.destroy()

    # --------------------------------------------------------------- keys ---
    def _key_accept(self, _e):
        self.accept()
        return "break"

    def _key_skip(self, _e):
        self.skip()
        return "break"              # or tk's focus traversal eats Tab

    def _key_prev(self, _e):
        self._advance(-1)
        return "break"

    def _key_batch(self, _e):
        self.batch_accept()
        return "break"

    def _key_close(self, _e):
        self.close()
        return "break"
