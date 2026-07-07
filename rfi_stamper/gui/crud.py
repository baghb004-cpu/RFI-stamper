"""Schema-driven module panel: one component drives Task Management, Punch
List, Inspections, Change Orders, Budget, and Document lists.

Give it a Project kind, the tree columns, and the editor fields; it renders a
section-colored header, live status chips, a searchable list with color-coded
statuses, and an add/edit dialog — consistent behavior across every module
for free.
"""
from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field
from tkinter import messagebox, ttk

from . import theme as theme_mod
from .widgets import Tooltip, make_tree

STATUS_COLORS = {
    "todo": "#d99c20", "open": "#d99c20", "draft": "#d99c20",
    "scheduled": "#d99c20", "submitted": "#3f6fe0",
    "doing": "#3f6fe0", "in_work": "#3f6fe0", "ready": "#3f6fe0",
    "blocked": "#d64545", "failed": "#d64545", "rejected": "#d64545",
    "done": "#2f9e62", "closed": "#2f9e62", "passed": "#2f9e62",
    "approved": "#2f9e62", "verified": "#2f9e62",
}


@dataclass
class Field:
    key: str
    label: str
    kind: str = "text"          # text / multiline / choice / number / date
    choices: list = field(default_factory=list)
    default: str = ""


class CrudPanel(ttk.Frame):
    def __init__(self, parent, theme, status, get_project, kind: str,
                 title: str, columns, fields, factory, section: str,
                 empty_hint: str = "", on_change=None):
        """columns: [(attr, HEADING, width)]; fields: [Field]; factory: the
        project dataclass (uses .new(**kw)); get_project() -> Project|None."""
        super().__init__(parent, padding=(10, 6))
        self.theme = theme
        self.status = status
        self.get_project = get_project
        self.kind = kind
        self.fields = fields
        self.factory = factory
        self.on_change = on_change
        self.accent = theme_mod.section_color(section)

        head = ttk.Frame(self)
        head.pack(fill="x")
        self.title_lbl = tk.Label(head, text="▍" + title,
                                  font=("Segoe UI", 14, "bold"))
        self.title_lbl.pack(side="left")
        theme.register(lambda c: self.title_lbl.configure(
            bg=c["bg"], fg=self.accent))
        self.chips = ttk.Frame(head)
        self.chips.pack(side="left", padx=16)
        ttk.Button(head, text="＋ Add", style="Accent.TButton",
                   command=self.add_dialog).pack(side="right", padx=2)
        ttk.Button(head, text="Edit", command=self.edit_sel).pack(side="right",
                                                                  padx=2)
        ttk.Button(head, text="Delete", command=self.delete_sel).pack(
            side="right", padx=2)
        self.q = tk.StringVar()
        qe = ttk.Entry(head, textvariable=self.q, width=20)
        qe.pack(side="right", padx=8)
        Tooltip(qe, "Filter this list", theme)
        self.q.trace_add("write", lambda *_: self.refresh())

        if empty_hint:
            self.hint = ttk.Label(self, text=empty_hint, style="Muted.TLabel")
            self.hint.pack(anchor="w", pady=(2, 0))
        else:
            self.hint = None

        self.columns = columns
        frame, self.tree = make_tree(
            self, theme, [(a, h) for a, h, _w in columns],
            [w for _a, _h, w in columns], height=11)
        frame.pack(fill="both", expand=True, pady=6)
        self.tree.bind("<Double-1>", lambda e: self.edit_sel())
        self.tree.bind("<Button-3>", self._context)
        for st, col in STATUS_COLORS.items():
            self.tree.tag_configure("st_" + st, foreground=col)
        self.refresh()

    # ------------------------------------------------------------- data
    def _items(self):
        proj = self.get_project()
        if not proj:
            return []
        items = proj.items(self.kind)
        q = self.q.get().strip().lower()
        if q:
            items = [it for it in items
                     if q in " ".join(str(getattr(it, a, ""))
                                      for a, _h, _w in self.columns).lower()]
        return items

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        items = self._items()
        for it in items:
            vals = []
            for attr, _h, _w in self.columns:
                v = getattr(it, attr, "")
                if isinstance(v, float):
                    v = f"{v:,.2f}"
                vals.append(v)
            st = str(getattr(it, "status", "")).lower()
            self.tree.insert("", "end", iid=it.id, values=vals,
                             tags=("st_" + st,) if st in STATUS_COLORS else ())
        self._refresh_chips(items)
        if self.hint:
            (self.hint.pack(anchor="w", pady=(2, 0)) if not items
             else self.hint.pack_forget())

    def _refresh_chips(self, items):
        for w in self.chips.winfo_children():
            w.destroy()
        counts = {}
        for it in items:
            st = str(getattr(it, "status", "") or "—")
            counts[st] = counts.get(st, 0) + 1
        c = self.theme.colors
        for st, n in sorted(counts.items()):
            col = STATUS_COLORS.get(st.lower(), c["muted"])
            lbl = tk.Label(self.chips, text=f" {st}: {n} ",
                           font=("Segoe UI", 9, "bold"),
                           fg=col, bg=c["panel"], padx=4, pady=1)
            lbl.pack(side="left", padx=2)

    def _need_project(self):
        if not self.get_project():
            messagebox.showinfo("Planloom", "Open or create a project first "
                                            "(Home tab).")
            return False
        return True

    # ------------------------------------------------------------ actions
    def add_dialog(self):
        if self._need_project():
            self._editor(None)

    def edit_sel(self):
        sel = self.tree.selection()
        if sel and self._need_project():
            item = self.get_project().get(self.kind, sel[0])
            if item:
                self._editor(item)

    def delete_sel(self):
        sel = self.tree.selection()
        if not sel or not self._need_project():
            return
        if not messagebox.askyesno("Delete", f"Delete {len(sel)} item(s)?"):
            return
        proj = self.get_project()
        for iid in sel:
            proj.remove(self.kind, iid)
        self._changed()

    def _context(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        if iid not in self.tree.selection():
            self.tree.selection_set(iid)
        status_field = next((f for f in self.fields
                             if f.key == "status" and f.choices), None)
        menu = tk.Menu(self, tearoff=0)
        if status_field:
            for choice in status_field.choices:
                menu.add_command(
                    label=f"Status → {choice}",
                    command=lambda ch=choice: self._set_status(ch))
            menu.add_separator()
        menu.add_command(label="Edit…", command=self.edit_sel)
        menu.add_command(label="Delete", command=self.delete_sel)
        menu.tk_popup(event.x_root, event.y_root)

    def _set_status(self, status):
        proj = self.get_project()
        for iid in self.tree.selection():
            item = proj.get(self.kind, iid)
            if item is not None and hasattr(item, "status"):
                item.status = status
        proj.save()
        self._changed()

    def _changed(self):
        self.refresh()
        if self.on_change:
            self.on_change()

    # ------------------------------------------------------------- editor
    def _editor(self, item):
        dlg = tk.Toplevel(self)
        dlg.title(("Edit" if item else "Add") + " item")
        dlg.transient(self.winfo_toplevel())
        dlg.grab_set()
        c = self.theme.colors
        dlg.configure(bg=c["bg"])
        frm = ttk.Frame(dlg, padding=14)
        frm.pack(fill="both", expand=True)
        vars_: dict = {}
        for i, f in enumerate(self.fields):
            ttk.Label(frm, text=f.label).grid(row=i, column=0, sticky="nw",
                                              pady=3, padx=(0, 8))
            cur = getattr(item, f.key, f.default) if item else f.default
            if f.kind == "multiline":
                w = tk.Text(frm, width=42, height=4, font=("Segoe UI", 10))
                self.theme.style_text(w)
                w.insert("1.0", str(cur))
                vars_[f.key] = w
            elif f.kind == "choice":
                v = tk.StringVar(value=str(cur) or (f.choices[0] if f.choices
                                                    else ""))
                w = ttk.Combobox(frm, textvariable=v, values=f.choices,
                                 state="readonly", width=24)
                vars_[f.key] = v
            else:
                v = tk.StringVar(value=str(cur))
                w = ttk.Entry(frm, textvariable=v, width=42)
                vars_[f.key] = v
                if f.kind == "date":
                    Tooltip(w, "Date as YYYY-MM-DD", self.theme)
            w.grid(row=i, column=1, sticky="ew", pady=3)
        frm.columnconfigure(1, weight=1)

        def collect() -> dict | None:
            out = {}
            for f in self.fields:
                v = vars_[f.key]
                raw = (v.get("1.0", "end").strip() if isinstance(v, tk.Text)
                       else v.get().strip())
                if f.kind == "number":
                    try:
                        out[f.key] = float(raw or 0)
                    except ValueError:
                        messagebox.showwarning("Planloom",
                                               f"{f.label} must be a number.",
                                               parent=dlg)
                        return None
                else:
                    out[f.key] = raw
            return out

        def save():
            vals = collect()
            if vals is None:
                return
            proj = self.get_project()
            if item is None:
                proj.add(self.kind, self.factory.new(**vals))
            else:
                for k, val in vals.items():
                    setattr(item, k, val)
                proj.save()
            dlg.destroy()
            self._changed()

        btns = ttk.Frame(frm)
        btns.grid(row=len(self.fields), column=0, columnspan=2, sticky="ew",
                  pady=(10, 0))
        ttk.Button(btns, text="Save", style="Accent.TButton",
                   command=save).pack(side="right")
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(
            side="right", padx=6)
        dlg.bind("<Return>", lambda e: save())
        dlg.bind("<Escape>", lambda e: dlg.destroy())
