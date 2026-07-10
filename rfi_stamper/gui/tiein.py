"""The Tie-In — connect everything once, dead simple.

A tie-in is where new plumbing meets the existing system: one fitting,
done right, and everything flows.  This dialog is Planloom's tie-in —
one screen, four choices, no jargon:

* where your plan sets live,
* where RFI files land (the drop folder),
* where exports should go,
* which field-tablet kit your crew's hardware reads.

Everything is optional, saved to ~/.planloom, and every consumer treats
it as a DEFAULT, never a lock: the Fieldstitch Export stage becomes one
tap (your kit, your folder, date-stamped), and file dialogs open where
your files actually are.  Offline as always — a "connection" here is a
folder on your own disk.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, ttk

from . import prefs
from .theme import section_color

#: (pref key, label, hint)
_FOLDERS = (
    ("plans_dir", "Plan sets live in", "open dialogs start here"),
    ("rfi_dir", "RFI files land in", "the drop folder other tools fill"),
    ("export_dir", "Exports go to", "field kits + reports, one tap"),
)


def settings() -> dict:
    """The saved Tie-In (empty dict when never set up)."""
    return dict(prefs.load().get("tiein", {}))


def initialdir(key: str) -> str | None:
    """A saved folder for file dialogs (None when unset/missing)."""
    d = settings().get(key, "")
    return d if d and os.path.isdir(d) else None


class TieInDialog:
    """One screen that makes everything else one tap."""

    def __init__(self, root, theme, status):
        self.status = status
        from .tab_fieldstitch import KIT_LABELS
        cur = settings()
        dlg = tk.Toplevel(root)
        dlg.title("The Tie-In — connect your stuff")
        dlg.transient(root)
        self.dlg = dlg
        frm = ttk.Frame(dlg, padding=16)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="▍The Tie-In", font=("Segoe UI", 14, "bold"),
                  foreground=section_color("integrations")).pack(anchor="w")
        ttk.Label(frm, style="Muted.TLabel",
                  text="Tell Planloom where things live — once. "
                       "Everything else becomes one tap. All local, "
                       "all offline.").pack(anchor="w", pady=(0, 10))
        self.vars: dict = {}
        for key, label, hint in _FOLDERS:
            row = ttk.Frame(frm)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, width=16).pack(side="left")
            v = tk.StringVar(value=cur.get(key, ""))
            self.vars[key] = v
            ttk.Entry(row, textvariable=v, width=42).pack(
                side="left", fill="x", expand=True, padx=4)
            ttk.Button(row, text="Browse…",
                       command=lambda vv=v: self._browse(vv)
                       ).pack(side="left")
            ttk.Label(row, text=hint, style="Muted.TLabel").pack(
                side="left", padx=6)
        row = ttk.Frame(frm)
        row.pack(fill="x", pady=(10, 0))
        ttk.Label(row, text="Field tablet kit", width=16).pack(side="left")
        self.kit_labels = list(KIT_LABELS)
        self.kit_var = tk.StringVar()
        cb = ttk.Combobox(row, textvariable=self.kit_var, state="readonly",
                          width=58, values=[lb for _k, lb in KIT_LABELS])
        cb.pack(side="left", padx=4)
        for k, lb in KIT_LABELS:
            if k == cur.get("kit"):
                self.kit_var.set(lb)
        ttk.Label(frm, style="Muted.TLabel", justify="left", text=(
            "With a kit and an export folder set, the Fieldstitch Export "
            "stage writes your tablet's files in ONE tap — date-stamped, "
            "no dialogs.")).pack(anchor="w", pady=(8, 0))
        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text="Save the Tie-In", style="Accent.TButton",
                   command=self.save).pack(side="left")
        ttk.Button(btns, text="Close",
                   command=dlg.destroy).pack(side="right")
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    def _browse(self, var):
        d = filedialog.askdirectory(parent=self.dlg,
                                    initialdir=var.get() or None)
        if d:
            var.set(d)

    def save(self):
        p = prefs.load()
        tie = p.setdefault("tiein", {})
        for key, _label, _hint in _FOLDERS:
            tie[key] = self.vars[key].get().strip()
        kit = next((k for k, lb in self.kit_labels
                    if lb == self.kit_var.get()), "")
        if kit:
            tie["kit"] = kit
        prefs.save(p)
        self.status.set("Tie-In saved — Fieldstitch Export is now one tap",
                        "ok")
        self.dlg.destroy()
