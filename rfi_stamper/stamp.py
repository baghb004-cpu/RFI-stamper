"""Draw note boxes and merge them onto plan pages (any /Rotate value)."""
from __future__ import annotations

import io
import os

from pypdf import PdfReader, PdfWriter, Transformation

from .layout import (BORDER, F_BOD, F_HDR, GAP, L_BOD, L_HDR, PAD, RED,
                     S_BOD, S_HDR, layout_entries)


def _new_canvas(buf, pagesize):
    """The overlay canvas, selectable by the PLOOM_PDF_ENGINE env var.

    Defaults to reportlab (unchanged behavior); ``PLOOM_PDF_ENGINE=minipdf``
    routes to Planloom's from-scratch writer.  Both expose the same
    ``Canvas(buf, pagesize=(w, h))`` surface, so this is the only switch — the
    two are held pixel-identical by ``tests/test_minipdf_parity.py``.
    """
    if os.environ.get("PLOOM_PDF_ENGINE", "reportlab").lower() == "minipdf":
        from .minipdf.canvas import Canvas
        return Canvas(buf, pagesize=pagesize)
    from reportlab.pdfgen import canvas
    return canvas.Canvas(buf, pagesize=pagesize)


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


def _viewer_to_media(rotation, crop_w, crop_h, crop_x0, crop_y0):
    """Transformation mapping an overlay drawn in viewer space onto the page's
    unrotated PDF user space, so the page /Rotate re-displays it upright and it
    lands inside the visible CropBox.

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


def stamp_pdf(plan_path, out_path, placements, index, appendix=None):
    """placements: {page_no: [ {x, ytop, w, entries}, ... ]}
    appendix: optional list of (title, entries) rendered as extra pages."""
    reader = PdfReader(plan_path)
    writer = PdfWriter()
    for i, page in enumerate(reader.pages, start=1):
        boxes = placements.get(i)
        if boxes:
            info = index.info(i)
            buf = io.BytesIO()
            c = _new_canvas(buf, (info.view_w, info.view_h))
            for b in boxes:
                draw_box(c, b["x"], b["ytop"], b["w"], b["entries"])
            c.save()
            buf.seek(0)
            ov = PdfReader(buf).pages[0]
            # Anchor on the CropBox (what the viewer/finder actually render),
            # read straight from this page so a trimmed CropBox lands correctly.
            cb = page.cropbox
            op = _viewer_to_media(info.rotation, float(cb.width), float(cb.height),
                                  float(cb.left), float(cb.bottom))
            page.merge_transformed_page(ov, op, expand=False)
        writer.add_page(page)

    if appendix:
        first = index.info(1)
        vw, vh = first.view_w, first.view_h
        buf = io.BytesIO()
        c = _new_canvas(buf, (vw, vh))
        margin, col_w = 60.0, min(430.0, vw * 0.42)
        x, ytop = margin, vh - margin
        c.setFont(F_HDR, 13)
        c.setFillColorRGB(*RED)
        c.drawString(x, ytop, "RFI NOTES \u2014 NO CLEAR SPACE FOUND ON SHEET")
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
        for p in PdfReader(buf).pages:
            writer.add_page(p)

    # Deliver clean, reproducible bytes: drop the /Info dictionary pypdf would
    # otherwise stamp with a /Producer and wall-clock dates.  That removes an
    # NDA metadata leak and makes the merged output byte-deterministic (pypdf's
    # /ID is content-derived), matching the from-scratch writer's own policy.
    try:
        writer.metadata = None
    except Exception:                        # older pypdf without the setter
        pass

    # atomic write: never leave a truncated overlay at the final path
    tmp = out_path + ".part"
    with open(tmp, "wb") as f:
        writer.write(f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out_path)
