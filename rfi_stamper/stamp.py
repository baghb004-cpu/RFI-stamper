"""Draw note boxes and merge them onto plan pages (any /Rotate value)."""
from __future__ import annotations

import io
import os

from pypdf import PdfReader, PdfWriter, Transformation
from reportlab.pdfgen import canvas

from .layout import (BORDER, F_BOD, F_HDR, GAP, L_BOD, L_HDR, PAD, RED,
                     S_BOD, S_HDR, layout_entries)


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


def _viewer_to_media(rotation, media_w, media_h, media_x0, media_y0):
    """Transformation mapping overlay drawn in viewer space onto the
    unrotated media space, so the page /Rotate re-displays it upright.
    The 90-degree case is field-verified on rotated Arch-E1 plan sets; every
    stamped page is pixel-diff verified afterwards, so a nonconforming
    producer fails loudly instead of shipping a bad overlay."""
    r = rotation % 360
    if r == 0:
        op = Transformation()
    elif r == 90:
        op = Transformation().rotate(90).translate(tx=media_w, ty=0)
    elif r == 180:
        op = Transformation().rotate(180).translate(tx=media_w, ty=media_h)
    elif r == 270:
        op = Transformation().rotate(270).translate(tx=0, ty=media_h)
    else:
        op = Transformation()
    if media_x0 or media_y0:
        op = op.translate(tx=media_x0, ty=media_y0)
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
            c = canvas.Canvas(buf, pagesize=(info.view_w, info.view_h))
            for b in boxes:
                draw_box(c, b["x"], b["ytop"], b["w"], b["entries"])
            c.save()
            buf.seek(0)
            ov = PdfReader(buf).pages[0]
            op = _viewer_to_media(info.rotation, info.media_w, info.media_h,
                                  info.media_x0, info.media_y0)
            page.merge_transformed_page(ov, op, expand=False)
        writer.add_page(page)

    if appendix:
        first = index.info(1)
        vw, vh = first.view_w, first.view_h
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(vw, vh))
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

    # atomic write: never leave a truncated overlay at the final path
    tmp = out_path + ".part"
    with open(tmp, "wb") as f:
        writer.write(f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out_path)
