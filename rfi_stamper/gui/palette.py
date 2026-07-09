"""Command palette (Ctrl+K): fuzzy-search every feature, action, and
preference and run it from the keyboard — feature discovery without menus."""
from __future__ import annotations

import tkinter as tk


class Command:
    __slots__ = ("name", "category", "run", "shortcut")

    def __init__(self, name, category, run, shortcut=""):
        self.name, self.category, self.run, self.shortcut = (
            name, category, run, shortcut)

    @property
    def label(self):
        s = f"{self.category}: {self.name}"
        return s + (f"   [{self.shortcut}]" if self.shortcut else "")


def fuzzy_score(query: str, target: str) -> float:
    """Subsequence match score; higher is better, -1 = no match."""
    q, t = query.lower(), target.lower()
    if not q:
        return 0.0
    if q in t:
        return 100.0 - t.index(q) - 0.3 * len(t)
    score, ti = 0.0, 0
    for ch in q:
        found = t.find(ch, ti)
        if found < 0:
            return -1.0
        score += 1.0 / (1 + found - ti)
        ti = found + 1
    return score - 0.05 * len(t)


class CommandPalette:
    def __init__(self, root, theme):
        self.root, self.theme = root, theme
        self.commands: list[Command] = []
        self.win = None

    def register(self, name, category, run, shortcut=""):
        self.commands.append(Command(name, category, run, shortcut))

    def register_many(self, items):
        for it in items:
            self.register(*it)

    def open(self, _e=None):
        if self.win is not None and self.win.winfo_exists():
            self.win.lift()
            return
        c = self.theme.colors
        self.win = tk.Toplevel(self.root)
        self.win.wm_overrideredirect(True)
        self.win.configure(bg=c["border"])
        w = 560
        x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_rooty() + 90
        self.win.wm_geometry(f"{w}x340+{x}+{y}")
        inner = tk.Frame(self.win, bg=c["panel"])
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.var = tk.StringVar()
        entry = tk.Entry(inner, textvariable=self.var, font=("Segoe UI", 12),
                         bg=c["entry_bg"], fg=c["fg"], insertbackground=c["fg"],
                         relief="flat")
        entry.pack(fill="x", padx=10, pady=(10, 6), ipady=5)
        self.listbox = tk.Listbox(inner, activestyle="none", font=("Segoe UI", 10),
                                  bg=c["panel"], fg=c["fg"],
                                  selectbackground=c["sel_bg"],
                                  selectforeground=c["sel_fg"],
                                  highlightthickness=0, relief="flat")
        self.listbox.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.var.trace_add("write", lambda *_: self._refresh())
        entry.bind("<Return>", self._run_selected)
        entry.bind("<Escape>", lambda e: self.close())
        entry.bind("<Down>", lambda e: self._move(1))
        entry.bind("<Up>", lambda e: self._move(-1))
        self.listbox.bind("<Double-Button-1>", self._run_selected)
        self.listbox.bind("<Return>", self._run_selected)
        self.win.bind("<FocusOut>", self._maybe_close)
        self.win.bind("<Escape>", lambda e: self.close())
        self._matches = []
        self._refresh()
        entry.focus_set()

    def close(self):
        if self.win is not None:
            self.win.destroy()
            self.win = None

    def _maybe_close(self, _e):
        self.root.after(120, lambda: (
            self.win and self.win.winfo_exists()
            and self.win.focus_displayof() is None and self.close()))

    def _refresh(self):
        q = self.var.get().strip()
        scored = [(fuzzy_score(q, c.label), i, c)
                  for i, c in enumerate(self.commands)]
        scored = sorted(((s, i, c) for s, i, c in scored if s >= 0),
                        key=lambda t: (-t[0], t[1]))
        self._matches = [c for _, _, c in scored[:40]]
        self.listbox.delete(0, "end")
        for c in self._matches:
            self.listbox.insert("end", "  " + c.label)
        if self._matches:
            self.listbox.selection_set(0)

    def _move(self, delta):
        if not self._matches:
            return "break"
        cur = self.listbox.curselection()
        i = (cur[0] if cur else 0) + delta
        i = max(0, min(len(self._matches) - 1, i))
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(i)
        self.listbox.see(i)
        return "break"

    def _run_selected(self, _e=None):
        cur = self.listbox.curselection()
        if not cur or cur[0] >= len(self._matches):
            return
        cmd = self._matches[cur[0]]
        self.close()
        self.root.after(10, cmd.run)
