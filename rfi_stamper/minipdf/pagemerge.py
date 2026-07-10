"""The Shuttle, compositor — merge a self-authored overlay onto a plan page.

Replaces ``pypdf.Transformation`` + ``merge_transformed_page`` for the one
mode the stamper uses (``expand=False``, CropBox-anchored).  Plan content is
NEVER decoded or rewritten: ``/Contents`` becomes an array wrapping the
untouched original streams —

    pre  = ``q``                      (isolate the plan's graphics state)
    post = ``Q q <ctm> cm <overlay ops> Q``

which is spec-legal (a contents array is one logical stream, and our
segments end at operator boundaries).  The overlay is Planloom's OWN
minipdf page, so its content inventory is known and its ``/Fn`` font names
can be re-keyed safely; plan-page names are never touched.

The four CTMs are the closed form of ``stamp._viewer_to_media``'s
field-verified transforms (do NOT port pypdf's Transformation algebra —
its ``translate`` is a device-space post-add, and replicating the API is
how the 180°-flip bug gets re-derived).  ``w, h`` are the UNROTATED
CropBox dims, ``x0, y0`` its lower-left corner:

    /Rotate 0:    1  0  0  1  x0      y0
    /Rotate 90:   0  1 -1  0  w+x0    y0        (viewer (x,y) -> (w-y, x))
    /Rotate 180: -1  0  0 -1  w+x0    h+y0
    /Rotate 270:  0 -1  1  0  x0      h+y0
"""
from __future__ import annotations

import re

from .content import fmt_num
from .parse import Name, PdfError, Ref, Stream, decode_stream


def overlay_ctm(rotation: int, crop_w: float, crop_h: float,
                crop_x0: float, crop_y0: float) -> tuple:
    """The 6-tuple ``a b c d e f`` mapping viewer space onto page space."""
    r = rotation % 360
    if r == 90:
        return (0, 1, -1, 0, crop_w + crop_x0, crop_y0)
    if r == 180:
        return (-1, 0, 0, -1, crop_w + crop_x0, crop_h + crop_y0)
    if r == 270:
        return (0, -1, 1, 0, crop_x0, crop_h + crop_y0)
    return (1, 0, 0, 1, crop_x0, crop_y0)


_ALLOWED_RES = {"/Font", "/ProcSet", "/XObject"}


def add_overlay(writer, wpage, ov_reader, rotation: int, crop) -> None:
    """Composite page 1 of ``ov_reader`` (a minipdf overlay) onto ``wpage``.

    ``crop`` is ``(x0, y0, w, h)`` — the UNROTATED CropBox of the plan page
    (the overlay/finder work in the rendered viewer window, which is the
    CropBox, NOT the MediaBox — anchoring on the MediaBox shifts every box
    off its cleared window when a sheet is plotter-trimmed).
    """
    ovp = ov_reader.pages[0]
    st = ov_reader.resolve(ovp.get("/Contents"))
    if isinstance(st, list):
        if len(st) != 1:
            raise PdfError("overlay page must carry one content stream")
        st = ov_reader.resolve(st[0])
    if not isinstance(st, Stream):
        raise PdfError("overlay page has no content stream")
    ops = decode_stream(st, ov_reader.resolve)

    ov_res = ov_reader.resolve(ovp.get("/Resources")) or {}
    for k in ov_res:
        if k not in _ALLOWED_RES:               # ours — the inventory is known
            raise PdfError(f"unexpected overlay resource {k}")

    d = wpage.dict
    # copy-on-write the page's effective resources: shared/inherited dicts
    # must never be mutated or the injected fonts leak across pages
    res = writer.resolve(d.get("/Resources"))
    res = dict(res) if isinstance(res, dict) else {}
    ops = _inject(writer, ov_reader, ov_res, res, "/Font", "PLF", ops)
    ops = _inject(writer, ov_reader, ov_res, res, "/XObject", "PLX", ops)
    d["/Resources"] = res

    a, b, c, dd, e, f = overlay_ctm(rotation, crop[2], crop[3],
                                    crop[0], crop[1])
    ctm = " ".join(fmt_num(v) for v in (a, b, c, dd, e, f)).encode("ascii")
    pre = writer.add_stream(b"q\n")
    post = writer.add_stream(b"Q\nq\n" + ctm + b" cm\n" + ops + b"\nQ")
    cur = d.get("/Contents")
    cur = cur if isinstance(cur, list) else ([] if cur is None else [cur])
    d["/Contents"] = [pre] + list(cur) + [post]


def _inject(writer, ov_reader, ov_res: dict, res: dict, cat: str,
            stem: str, ops: bytes) -> bytes:
    """Import one overlay resource category under collision-free fresh keys
    (``/PLF1…``), rewriting the *overlay's own* ops — never plan content."""
    src = ov_reader.resolve(ov_res.get(cat))
    if not isinstance(src, dict) or not src:
        return ops
    have = writer.resolve(res.get(cat))
    have = dict(have) if isinstance(have, dict) else {}
    n = 1
    for k in sorted(src):
        while f"/{stem}{n}" in have:
            n += 1
        nk = f"/{stem}{n}"
        n += 1
        have[nk] = writer._import_val(ov_reader, src[k])
        ops = re.sub(re.escape(k.encode("latin-1")) + rb"(?=[\s/\[<(])",
                     nk.encode("latin-1"), ops)
    res[cat] = have
    return ops
