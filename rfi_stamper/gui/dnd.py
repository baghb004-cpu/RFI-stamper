"""Drag-and-drop plumbing — Planloom's own, from scratch (tkinterdnd2 retired).

Two layers, split so the OS-specific part stays tiny and the logic stays
testable:

* **The router** (this module, pure Tk + stdlib): every drop target registers
  here (`enable_drop`).  A platform backend feeds it window-level events —
  ``drag_enter`` / ``drag_move(x, y)`` / ``drag_leave`` / ``drop(x, y, paths)``
  in SCREEN coordinates — and the router synthesizes what the old per-widget
  protocol provided: hover enter/leave per target as the cursor crosses it,
  extension filtering, and delivery of the drop to the most specific viewable
  target containing the point (fallback: the toplevel's own handler, which is
  the full-window overlay).  Callbacks are deferred with ``after(20)`` so no
  widget mutates while the OS drag loop is still on the stack.
* **The backend** (:mod:`dnd_win32`, pure ctypes): a real OLE ``IDropTarget``
  on the shipped platform.  No package, no bundled binary.  Where no backend
  exists (POSIX dev boxes, odd sessions) ``HAS_DND`` stays False and every
  target degrades to its click-to-browse path exactly as before — DnD is an
  enhancement, never a requirement.

The public surface is unchanged: ``HAS_DND``, ``make_root``,
``parse_drop_paths``, ``enable_drop(widget, callback, exts=, on_enter=,
on_leave=)``.
"""
from __future__ import annotations

import os
import tkinter as tk

#: True once a native backend is actually registered on the root window.
HAS_DND = False

#: Deferral before a drop callback may touch widgets: the OS drop handshake
#: (a nested modal loop on the drag source's side) must unwind first.
DROP_DEFER_MS = 20


class Router:
    """Per-toplevel drop-target registry + event routing (backend-agnostic)."""

    def __init__(self, root):
        self.root = root
        self.targets: list[dict] = []      # {widget, callback, exts, enter, leave}
        self.backend_live = False          # a native backend feeds THIS router
        self._hover = None                 # target currently under the cursor
        self._inside = False               # a drag is over the window

    # -- registration ------------------------------------------------------ #
    def add(self, widget, callback, exts=None, on_enter=None, on_leave=None):
        entry = {"widget": widget, "callback": callback,
                 "exts": tuple(exts) if exts else None,
                 "enter": on_enter, "leave": on_leave}
        self.targets.append(entry)
        # prune on destroy so rebuilt panels/dialogs never leave stale targets
        # (a dead entry is harmless to routing but grows forever otherwise)
        widget.bind("<Destroy>",
                    lambda e, w=widget: e.widget is w and self.remove(w),
                    add="+")

    def remove(self, widget):
        if self._hover is not None and self._hover["widget"] is widget:
            self._hover = None
        self.targets = [t for t in self.targets if t["widget"] is not widget]

    # -- geometry ---------------------------------------------------------- #
    @staticmethod
    def _contains(widget, x, y) -> bool:
        try:
            if not widget.winfo_viewable():
                return False
            wx, wy = widget.winfo_rootx(), widget.winfo_rooty()
            return (wx <= x < wx + widget.winfo_width()
                    and wy <= y < wy + widget.winfo_height())
        except Exception:                  # destroyed mid-drag
            return False

    def _target_at(self, x, y):
        """Most specific (smallest-area) viewable target containing (x, y);
        the toplevel itself (the overlay's registration) only as fallback."""
        best, best_area = None, None
        for t in self.targets:
            w = t["widget"]
            if w is self.root or not self._contains(w, x, y):
                continue
            area = w.winfo_width() * w.winfo_height()
            if best is None or area < best_area:
                best, best_area = t, area
        if best is not None:
            return best
        for t in self.targets:             # root-level fallback (overlay)
            if t["widget"] is self.root:
                return t
        return None

    def _root_hooks(self, kind):
        return [t[kind] for t in self.targets
                if t["widget"] is self.root and t[kind]]

    # -- backend event feed -------------------------------------------------#
    def drag_enter(self):
        self._inside = True
        for hook in self._root_hooks("enter"):
            self._safe(hook)

    def drag_move(self, x, y):
        if not self._inside:
            self.drag_enter()
        t = self._target_at(x, y)
        if t is not None and t["widget"] is self.root:
            t = None                       # root fallback is not a hover target
        if t is not self._hover:
            if self._hover is not None and self._hover["leave"]:
                self._safe(self._hover["leave"])
            if t is not None and t["enter"]:
                self._safe(t["enter"])
            self._hover = t

    def drag_leave(self):
        self._inside = False
        if self._hover is not None and self._hover["leave"]:
            self._safe(self._hover["leave"])
        self._hover = None
        for hook in self._root_hooks("leave"):
            self._safe(hook)

    def drop(self, x, y, paths):
        """Route a completed drop; OLE sends Drop INSTEAD of a final DragLeave,
        so the window-level leave hooks fire first (the overlay must hide)."""
        self.drag_leave()
        t = self._target_at(x, y)
        if t is None or not paths:
            return False
        use = filter_paths(paths, t["exts"])
        if not use:
            return False
        cb = t["callback"]
        self.root.after(DROP_DEFER_MS, lambda: cb(use))
        return True

    @staticmethod
    def _safe(fn):
        try:
            fn()
        except Exception:                  # noqa: BLE001 -- hover is sugar
            pass


#: toplevel widget -> Router
_routers: dict = {}


def _router_for(widget) -> Router:
    top = widget.winfo_toplevel()
    r = _routers.get(top)
    if r is None:
        r = _routers[top] = Router(top)
        top.bind("<Destroy>",
                 lambda e, t=top: e.widget is t and _routers.pop(t, None),
                 add="+")
    return r


def filter_paths(paths, exts=None) -> list:
    """Extension filter (directories always pass — the scanner walks them)."""
    if not exts:
        return list(paths)
    low = tuple(e.lower() for e in exts)
    return [p for p in paths if p.lower().endswith(low) or os.path.isdir(p)]


def parse_drop_paths(widget: tk.Misc, data: str, exts=None) -> list:
    """Split a Tcl-list drop string into paths (brace-quoted spaces survive),
    optionally filtered by extension.  Kept for callers holding raw strings."""
    paths = list(widget.tk.splitlist(data))
    return filter_paths(paths, exts)


def make_root() -> tk.Tk:
    """Create the app root and attach the platform drag-drop backend (if any)."""
    global HAS_DND
    root = tk.Tk()
    try:
        from . import dnd_win32
        router = _router_for(root)
        router.backend_live = bool(dnd_win32.attach(root, router))
        HAS_DND = router.backend_live
    except Exception:                      # noqa: BLE001 -- DnD never blocks startup
        HAS_DND = False
    return root


def enable_drop(widget: tk.Misc, callback, exts=None,
                on_enter=None, on_leave=None) -> bool:
    """Register ``widget`` as a drop target; callback(list_of_paths).

    Registration always succeeds (the router is platform-neutral); the return
    value says whether REAL OS drag-drop is live FOR THIS WINDOW, so callers
    can advertise their click-to-browse fallback when it is not.  (Only the
    app root has a native backend registered on its window frame — a target
    inside a secondary Toplevel/dialog gets its own router but no OS events,
    and must honestly report False.)
    """
    try:
        router = _router_for(widget)
        router.add(widget, callback, exts=exts,
                   on_enter=on_enter, on_leave=on_leave)
    except Exception:                      # noqa: BLE001 -- never break the app
        return False
    return router.backend_live
