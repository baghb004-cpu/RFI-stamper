"""From-scratch OS drag-and-drop backend: an OLE ``IDropTarget`` in pure ctypes.

This is the native half of the retired tkinterdnd2 wrapper, rebuilt the way
Holler rebuilt its keystroke sender: straight against the OS API (ole32 /
shell32 / kernel32 / user32 via ctypes), no package, no bundled binary, no
network.  It registers ONE drop target on the app's top-level window frame —
the OS walks up from whichever child window the cursor is over, so the whole
window is covered — and feeds the platform-neutral :class:`dnd.Router` with
screen-coordinate enter / move / leave / drop events.

Honesty rules (the HAS_SEND pattern): the module imports cleanly everywhere;
``HAS_NATIVE`` is True only where the OS API family exists, and
:func:`attach` returns False anywhere the real registration cannot happen, so
the GUI's click-to-browse fallback advertises itself truthfully.  The COM
lifetime rules from the retirement research are followed exactly: every
callback/vtable/object reference is pinned for the window's lifetime (a
garbage-collected COM callback is a hard crash), registration happens on the
Tk (STA) thread, all Tk work is bounced out of the OLE callbacks with
``after`` so nothing mutates widgets inside the source's modal drop loop, and
the target is revoked on window destroy.
"""
from __future__ import annotations

import sys

HAS_NATIVE = sys.platform == "win32"

# COM / OLE constants (ISO-stable, from the platform SDK headers)
S_OK = 0
E_NOINTERFACE = -2147467262            # 0x80004002
CF_HDROP = 15
TYMED_HGLOBAL = 1
DVASPECT_CONTENT = 1
DROPEFFECT_NONE = 0
DROPEFFECT_COPY = 1

#: pinned references: [(vtable, object, [callbacks...]), ...] — never released
#: (one small struct per toplevel for the process lifetime, by design).
_KEEPALIVE: list = []


def attach(root, router) -> bool:
    """Register a native drop target for ``root``'s window; True on success."""
    if not HAS_NATIVE:
        return False
    try:
        return _attach_win32(root, router)
    except Exception:                      # noqa: BLE001 -- degrade to Browse
        return False


def _attach_win32(root, router) -> bool:
    import ctypes
    from ctypes import wintypes

    ole32 = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

        @classmethod
        def make(cls, d1, d2, d3, *d4):
            return cls(d1, d2, d3, (ctypes.c_ubyte * 8)(*d4))

    IID_IUnknown = GUID.make(0x00000000, 0, 0,
                             0xC0, 0, 0, 0, 0, 0, 0, 0x46)
    IID_IDropTarget = GUID.make(0x00000122, 0, 0,
                                0xC0, 0, 0, 0, 0, 0, 0, 0x46)

    class POINTL(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class FORMATETC(ctypes.Structure):
        _fields_ = [("cfFormat", ctypes.c_ushort),
                    ("ptd", ctypes.c_void_p),
                    ("dwAspect", ctypes.c_uint32),
                    ("lindex", ctypes.c_long),
                    ("tymed", ctypes.c_uint32)]

    class STGMEDIUM(ctypes.Structure):
        _fields_ = [("tymed", ctypes.c_uint32),
                    ("hGlobal", ctypes.c_void_p),
                    ("pUnkForRelease", ctypes.c_void_p)]

    HRESULT = ctypes.c_long
    # IDropTarget method prototypes (stdcall; POINTL passed by value)
    QI_T = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p,
                              ctypes.POINTER(GUID),
                              ctypes.POINTER(ctypes.c_void_p))
    REF_T = ctypes.WINFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p)
    ENTER_T = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_void_p,
                                 ctypes.c_uint32, POINTL,
                                 ctypes.POINTER(ctypes.c_uint32))
    OVER_T = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_uint32,
                                POINTL, ctypes.POINTER(ctypes.c_uint32))
    LEAVE_T = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p)
    DROP_T = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_void_p,
                                ctypes.c_uint32, POINTL,
                                ctypes.POINTER(ctypes.c_uint32))

    class IDropTargetVtbl(ctypes.Structure):
        _fields_ = [("QueryInterface", QI_T), ("AddRef", REF_T),
                    ("Release", REF_T), ("DragEnter", ENTER_T),
                    ("DragOver", OVER_T), ("DragLeave", LEAVE_T),
                    ("Drop", DROP_T)]

    class DropTargetObj(ctypes.Structure):
        _fields_ = [("lpVtbl", ctypes.POINTER(IDropTargetVtbl))]

    # ---- IDataObject helpers (vtable slots: 0 QI, 1 AddRef, 2 Release,
    # ---- 3 GetData, ..., 5 QueryGetData) ---------------------------------- #
    GETDATA_T = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p,
                                   ctypes.POINTER(FORMATETC),
                                   ctypes.POINTER(STGMEDIUM))
    QUERYGET_T = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p,
                                    ctypes.POINTER(FORMATETC))

    def _dataobj_slot(pdata, index, proto):
        vtbl = ctypes.cast(pdata,
                           ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        return ctypes.cast(vtbl[index], proto)

    def _fmt_hdrop():
        return FORMATETC(CF_HDROP, None, DVASPECT_CONTENT, -1, TYMED_HGLOBAL)

    def _has_hdrop(pdata) -> bool:
        try:
            fn = _dataobj_slot(pdata, 5, QUERYGET_T)
            return fn(pdata, ctypes.byref(_fmt_hdrop())) == S_OK
        except Exception:                  # noqa: BLE001
            return False

    def _paths(pdata) -> list:
        """CF_HDROP -> [path, ...] via GetData + DragQueryFileW (wide)."""
        fmt = _fmt_hdrop()
        med = STGMEDIUM()
        fn = _dataobj_slot(pdata, 3, GETDATA_T)
        if fn(pdata, ctypes.byref(fmt), ctypes.byref(med)) != S_OK:
            return []
        try:
            hdrop = kernel32.GlobalLock(med.hGlobal)
            if not hdrop:
                return []
            try:
                out = []
                count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
                for i in range(count):
                    n = shell32.DragQueryFileW(hdrop, i, None, 0)
                    buf = ctypes.create_unicode_buffer(n + 1)
                    shell32.DragQueryFileW(hdrop, i, buf, n + 1)
                    out.append(buf.value)
                return out
            finally:
                kernel32.GlobalUnlock(med.hGlobal)
        finally:
            ole32.ReleaseStgMedium(ctypes.byref(med))

    # ---- the COM object ---------------------------------------------------- #
    refcount = ctypes.c_uint32(1)
    state = {"ok": False}

    def _qi(this, riid, ppv):
        iid = riid.contents
        for known in (IID_IUnknown, IID_IDropTarget):
            if bytes(iid) == bytes(known):
                ppv[0] = this
                refcount.value += 1
                return S_OK
        ppv[0] = None
        return E_NOINTERFACE

    def _addref(this):
        refcount.value += 1
        return refcount.value

    def _release(this):
        # pinned for the window's lifetime by design; never frees itself
        if refcount.value > 0:
            refcount.value -= 1
        return refcount.value

    def _effect(pdw, on):
        if pdw:
            pdw[0] = DROPEFFECT_COPY if on else DROPEFFECT_NONE

    def _drag_enter(this, pdata, keys, pt, pdw):
        ok = _has_hdrop(pdata)
        state["ok"] = ok
        _effect(pdw, ok)
        if ok:
            try:
                root.after(0, router.drag_enter)
            except Exception:              # noqa: BLE001
                pass
        return S_OK

    _last = {"x": None, "y": None}

    def _drag_over(this, keys, pt, pdw):
        _effect(pdw, state["ok"])
        if state["ok"] and (pt.x, pt.y) != (_last["x"], _last["y"]):
            _last["x"], _last["y"] = pt.x, pt.y
            try:
                root.after(0, lambda x=pt.x, y=pt.y: router.drag_move(x, y))
            except Exception:              # noqa: BLE001
                pass
        return S_OK

    def _drag_leave(this):
        state["ok"] = False
        try:
            root.after(0, router.drag_leave)
        except Exception:                  # noqa: BLE001
            pass
        return S_OK

    def _drop(this, pdata, keys, pt, pdw):
        _effect(pdw, state["ok"])
        paths = _paths(pdata) if state["ok"] else []
        state["ok"] = False
        if paths:
            try:
                root.after(0, lambda x=pt.x, y=pt.y, p=paths:
                           router.drop(x, y, p))
            except Exception:              # noqa: BLE001
                pass
        return S_OK

    callbacks = [QI_T(_qi), REF_T(_addref), REF_T(_release),
                 ENTER_T(_drag_enter), OVER_T(_drag_over),
                 LEAVE_T(_drag_leave), DROP_T(_drop)]
    vtbl = IDropTargetVtbl(*callbacks)
    obj = DropTargetObj(ctypes.pointer(vtbl))
    _KEEPALIVE.append((vtbl, obj, callbacks, refcount, state, _last))

    # ---- register on the top-level frame HWND (STA, Tk main thread) ------- #
    if ole32.OleInitialize(None) not in (S_OK, 1):     # S_FALSE=1: already up
        return False
    hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
    if ole32.RegisterDragDrop(hwnd, ctypes.byref(obj)) != S_OK:
        return False

    def _cleanup(event):
        if event.widget is root:
            try:
                ole32.RevokeDragDrop(hwnd)
            except Exception:              # noqa: BLE001
                pass
    root.bind("<Destroy>", _cleanup, add="+")
    return True
