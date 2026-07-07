"""Drag-and-drop plumbing.  Real OS drag-drop via tkinterdnd2 when installed;
every drop target also works by click-to-browse, so nothing depends on it."""
from __future__ import annotations

import tkinter as tk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except Exception:   # noqa: BLE001 -- optional dependency
    DND_FILES = None
    TkinterDnD = None
    HAS_DND = False


def make_root() -> tk.Tk:
    return TkinterDnD.Tk() if HAS_DND else tk.Tk()


def parse_drop_paths(widget: tk.Misc, data: str, exts=None) -> list:
    """Split a <<Drop>> event's data into paths, optionally filtered by ext."""
    paths = list(widget.tk.splitlist(data))
    if exts:
        low = tuple(e.lower() for e in exts)
        import os
        paths = [p for p in paths
                 if p.lower().endswith(low) or os.path.isdir(p)]
    return paths


def enable_drop(widget: tk.Misc, callback, exts=None,
                on_enter=None, on_leave=None) -> bool:
    """Make `widget` accept file drops; callback(list_of_paths).
    Returns True when OS drag-drop is actually active."""
    if not HAS_DND:
        return False
    try:
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind("<<Drop>>", lambda e: callback(
            parse_drop_paths(widget, e.data, exts)))
        if on_enter:
            widget.dnd_bind("<<DropEnter>>", lambda e: on_enter())
        if on_leave:
            widget.dnd_bind("<<DropLeave>>", lambda e: on_leave())
        return True
    except Exception:   # noqa: BLE001 -- never let DnD break the app
        return False
