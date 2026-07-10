"""Draw note boxes and merge them onto plan pages (any /Rotate value)."""
from __future__ import annotations

import io
import os

from .layout import (BORDER, F_BOD, F_HDR, GAP, L_BOD, L_HDR, PAD, RED,
                     S_BOD, S_HDR, layout_entries)


def _new_canvas(buf, pagesize):
    """The overlay canvas, selectable by the PLOOM_PDF_ENGINE env var.

    Defaults to Planloom's own from-scratch writer (``minipdf``);
    ``PLOOM_PDF_ENGINE=reportlab`` opts back into the retired library as a
    dev-box parity oracle (it is no longer a shipped dependency).  Both expose
    the same ``Canvas(buf, pagesize=(w, h))`` surface, and the two are held
    pixel-identical by ``tests/test_minipdf_parity.py``.
    """
    if os.environ.get("PLOOM_PDF_ENGINE", "minipdf").lower() == "reportlab":
        from reportlab.pdfgen import canvas
        return canvas.Canvas(buf, pagesize=pagesize)
    from .minipdf.canvas import Canvas
    return Canvas(buf, pagesize=pagesize)


def draw_box(c, x, ytop, w, entries):
    h, items = layout_entries(entries, w)
    y0 = ytop - h
    c.setFillColorRGB(1, 1, 1)
    c.setStrokeColorRGB(*RED)
    c.setLineWidth(BORDER)
    c.rect(x, y0, w, h, stroke=1, fill=1)
    c.setFillColorRGB(*RED)
    y = ytop - PAD
    for hdr, blines in items:
        y -= L_HDR
        c.setFont(F_HDR, S_HDR)
        c.drawString(x + PAD, y + (L_HDR - S_HDR) / 2.0, hdr)
        c.setFont(F_BOD, S_BOD)
        for ln in blines:
            y -= L_BOD
            c.drawString(x + PAD, y + (L_BOD - S_BOD) / 2.0, ln)
        y -= GAP
    return h


def _draw_page_overlay(boxes, view_w, view_h) -> io.BytesIO:
    """One page's note boxes -> a single-page overlay PDF buffer."""
    buf = io.BytesIO()
    c = _new_canvas(buf, (view_w, view_h))
    for b in boxes:
        draw_box(c, b["x"], b["ytop"], b["w"], b["entries"])
    c.save()
    buf.seek(0)
    return buf


def _draw_appendix(appendix, vw, vh) -> io.BytesIO:
    """The labeled appendix pages (unplaceable notes) -> a PDF buffer."""
    buf = io.BytesIO()
    c = _new_canvas(buf, (vw, vh))
    margin, col_w = 60.0, min(430.0, vw * 0.42)
    x, ytop = margin, vh - margin
    c.setFont(F_HDR, 13)
    c.setFillColorRGB(*RED)
    c.drawString(x, ytop, "RFI NOTES — NO CLEAR SPACE FOUND ON SHEET")
    ytop -= 26
    for title, entries in appendix:
        h, _ = layout_entries(entries, col_w)
        if ytop - h - 24 < margin:
            if x + 2 * col_w + 40 < vw:
                x += col_w + 40
                ytop = vh - margin - 26
            else:
                c.showPage()
                x, ytop = margin, vh - margin
        c.setFont(F_HDR, 10.5)
        c.setFillColorRGB(*RED)
        c.drawString(x, ytop, title)
        ytop -= 6
        draw_box(c, x, ytop, col_w, entries)
        ytop -= h + 26
    c.save()
    buf.seek(0)
    return buf


def _viewer_to_media(rotation, crop_w, crop_h, crop_x0, crop_y0):
    """pypdf ``Transformation`` mapping viewer space onto the page's unrotated
    PDF user space (the ORACLE path only — the mini backend uses the closed-
    form CTM table in ``minipdf.pagemerge.overlay_ctm``, the same math).

    The overlay/finder work in the rendered viewer window, which fitz produces
    from the CropBox -- so the anchor is the CropBox, NOT the MediaBox. When a
    sheet's CropBox is a trimmed sub-window of a larger MediaBox (common CAD /
    plotter output), anchoring on the MediaBox origin shifts every box off its
    cleared window. crop_w/crop_h are the UNROTATED CropBox dimensions;
    crop_x0/crop_y0 its lower-left in PDF user space. (When CropBox == MediaBox
    with a zero origin this reduces to the original transform.)

    The 90-degree case is field-verified on rotated Arch-E1 plan sets; every
    stamped page is pixel-diff verified afterwards, so a nonconforming producer
    fails loudly instead of shipping a bad overlay."""
    from pypdf import Transformation
    r = rotation % 360
    if r == 90:
        op = Transformation().rotate(90).translate(tx=crop_w, ty=0)
    elif r == 180:
        op = Transformation().rotate(180).translate(tx=crop_w, ty=crop_h)
    elif r == 270:
        op = Transformation().rotate(270).translate(tx=0, ty=crop_h)
    else:
        op = Transformation()
    if crop_x0 or crop_y0:
        op = op.translate(tx=crop_x0, ty=crop_y0)
    return op


def _atomic_write_bytes(write_fn, out_path):
    """atomic write: never leave a truncated overlay at the final path"""
    tmp = out_path + ".part"
    with open(tmp, "wb") as f:
        write_fn(f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out_path)


def stamp_pdf(plan_path, out_path, placements, index, appendix=None):
    """placements: {page_no: [ {x, ytop, w, entries}, ... ]}
    appendix: optional list of (title, entries) rendered as extra pages.

    Backend per ``PLOOM_PDF_IO``: the Shuttle (``mini``, default) composites
    the overlay by wrapping the untouched plan content streams in a q/Q
    array with one closed-form CTM — plan content is never decoded;
    ``pypdf`` keeps the retired library as a dev-box parity oracle."""
    if os.environ.get("PLOOM_PDF_IO", "mini").lower() == "pypdf":
        return _stamp_pypdf(plan_path, out_path, placements, index, appendix)

    from .minipdf.io import Reader, Writer, add_overlay
    reader = Reader(plan_path)
    writer = Writer()
    for i, page in enumerate(reader.pages, start=1):
        wp = writer.add_page(page)
        boxes = placements.get(i)
        if boxes:
            info = index.info(i)
            ov = Reader(_draw_page_overlay(boxes, info.view_w, info.view_h))
            # Anchor on the CropBox (what the viewer/finder actually render),
            # read straight from this page so a trimmed CropBox lands right.
            cb = page.cropbox
            add_overlay(writer, wp, ov, info.rotation,
                        (cb.left, cb.bottom, cb.width, cb.height))
    if appendix:
        first = index.info(1)
        ax = Reader(_draw_appendix(appendix, first.view_w, first.view_h))
        for p in ax.pages:
            writer.add_page(p)
    _atomic_write_bytes(writer.write, out_path)


def _stamp_pypdf(plan_path, out_path, placements, index, appendix):
    """The retired-oracle path (byte-for-byte the pre-v5 behavior)."""
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(plan_path)
    writer = PdfWriter()
    for i, page in enumerate(reader.pages, start=1):
        boxes = placements.get(i)
        if boxes:
            info = index.info(i)
            buf = _draw_page_overlay(boxes, info.view_w, info.view_h)
            ov = PdfReader(buf).pages[0]
            cb = page.cropbox
            op = _viewer_to_media(info.rotation, float(cb.width),
                                  float(cb.height), float(cb.left),
                                  float(cb.bottom))
            page.merge_transformed_page(ov, op, expand=False)
        writer.add_page(page)
    if appendix:
        first = index.info(1)
        buf = _draw_appendix(appendix, first.view_w, first.view_h)
        for p in PdfReader(buf).pages:
            writer.add_page(p)
    # the oracle library would stamp /Info with a /Producer and wall-clock
    # dates — an NDA metadata leak; drop it (the mini writer structurally
    # cannot emit /Info at all)
    try:
        writer.metadata = None
    except Exception:                        # older pypdf without the setter
        pass
    _atomic_write_bytes(writer.write, out_path)
