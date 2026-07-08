"""Regression tests for the stamp/layout hardening (adversarial-review rebuild):
  #01  overlay must anchor on the CropBox, so a page whose CropBox is a trimmed
       sub-window of a larger MediaBox still stamps + verifies (all /Rotate).
  #05  a long/wide note header is width-fitted so it never overruns the box
       right edge (which would print over linework and fail verification), while
       the user-approved ` · STATUS` suffix is preserved.
  #34  find_spot includes the zone far-edge endpoints, so a clear window flush
       against a zone boundary is found instead of being spilled to the appendix.

Imports only stamp/verify/layout (+fitz/pypdf/numpy) -- no core -- so it stays
fast and independent."""
import os
import sys
import tempfile
from types import SimpleNamespace

import fitz
import numpy as np
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from reportlab.pdfbase.pdfmetrics import stringWidth

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper import layout, stamp, verify  # noqa: E402


# --------------------------------------------------------- #01 CropBox anchor --

def _make_inset_plan(path, rotate):
    """One page, MediaBox 1000x800, CropBox trimmed to [120 90 900 720]
    (origin != 0), a title-block token + border, lower-left of the crop left
    clear for the finder."""
    cx0, cy0, cx1, cy1 = 120, 90, 900, 720
    doc = fitz.open()
    page = doc.new_page(width=1000, height=800)
    fcrop = fitz.Rect(cx0, 800 - cy1, cx1, 800 - cy0)     # fitz y is top-down
    page.draw_rect(fitz.Rect(fcrop.x0 + 6, fcrop.y0 + 6, fcrop.x1 - 6, fcrop.y1 - 6),
                   color=(0, 0, 0), width=1.5)
    page.draw_line(fitz.Point(fcrop.x0 + 10, fcrop.y0 + 30),
                   fitz.Point(fcrop.x1 - 10, fcrop.y0 + 30), color=(0, 0, 0), width=1)
    page.insert_text(fitz.Point(fcrop.x1 - 120, fcrop.y1 - 24), "A-101",
                     fontsize=18, color=(0, 0, 0))
    raw = path + ".raw"
    doc.save(raw)
    doc.close()
    r = PdfReader(raw)
    w = PdfWriter()
    p = r.pages[0]
    p.mediabox = RectangleObject([0, 0, 1000, 800])
    p.cropbox = RectangleObject([cx0, cy0, cx1, cy1])
    if rotate:
        p.rotate(rotate)
    w.add_page(p)
    with open(path, "wb") as fh:
        w.write(fh)
    os.remove(raw)


def _fake_index(plan_path):
    doc = fitz.open(plan_path)
    infos = {}
    for i, page in enumerate(doc, start=1):
        rect, mb = page.rect, page.mediabox
        infos[i] = SimpleNamespace(
            page_no=i, sheet="A-101", view_w=rect.width, view_h=rect.height,
            rotation=page.rotation % 360, media_w=mb.width, media_h=mb.height,
            media_x0=mb.x0, media_y0=mb.y0)
    doc.close()
    return SimpleNamespace(info=lambda i: infos[i])


def _place_one(plan_path, info, dpi=90):
    doc = fitz.open(plan_path)
    gray = verify.render_gray(doc, 1, dpi)
    doc.close()
    ii = layout.integral(gray)
    rec = SimpleNamespace(number="001", title="ROOF DRAIN OVERFLOW",
                          question="Confirm the invert elevation.", answer="",
                          has_answer=False)
    entries = layout.make_entries([rec])
    w_pt = min(400.0, info.view_w * 0.30)
    h_pt, _ = layout.layout_entries(entries, w_pt)
    got = layout.find_spot(ii, gray.shape[1], gray.shape[0], w_pt, h_pt,
                           dpi / 72.0, [])
    assert got, "finder found no spot on the clear lower-left band"
    x, ytop, occ = got
    return {1: [dict(x=x, ytop=ytop, w=w_pt, h=h_pt, entries=entries, occ=occ)]}


def test_cropbox_anchor():
    with tempfile.TemporaryDirectory() as d:
        for rotate in (0, 90, 180, 270):
            plan = os.path.join(d, f"plan{rotate}.pdf")
            out = os.path.join(d, f"out{rotate}.pdf")
            _make_inset_plan(plan, rotate)
            idx = _fake_index(plan)
            placements = _place_one(plan, idx.info(1))
            stamp.stamp_pdf(plan, out, placements, idx)
            ok, results = verify.verify(plan, out, placements, idx, dpi=90,
                                        log=lambda *_: None)
            msg = results[0][2]
            assert ok, f"rot{rotate} CropBox-inset page failed verify: {msg}"
            # the box actually rendered inside its cleared footprint
            assert "inside=0" not in msg, f"rot{rotate}: box did not render: {msg}"
    print("  #01 CropBox-anchored overlay verifies on all rotations OK")


# ------------------------------------------------------------ #05 header fit --

def test_header_width_fit():
    long_title = "ROOF DRAIN OVERFLOW PIPING AND CONNECTIONS AT PENTHOUSE MECH ROOM"
    rec = SimpleNamespace(number="001", title=long_title,
                          question="q?", answer="", has_answer=False)
    entries = layout.make_entries([rec], statuses={"001": "in_work"})
    _num, raw_hdr, _body = entries[0]
    sep = " · "
    idx = raw_hdr.rfind(sep)
    assert idx != -1, "status suffix should be present on the raw header"
    suffix = raw_hdr[idx:]
    # narrow box (smallest trial width the pipeline uses is base_w*0.72)
    for w in (400.0, 400.0 * 0.85, 400.0 * 0.72, 220.0):
        inner = w - 2 * layout.PAD
        _h, items = layout.layout_entries(entries, w)
        fitted = items[0][0]
        width = stringWidth(fitted, layout.F_HDR, layout.S_HDR)
        assert width <= inner + 0.01, f"header {width:.1f}pt overruns inner {inner:.1f}pt at w={w:.0f}"
        assert fitted.endswith(suffix), f"status suffix was clipped at w={w:.0f}: {fitted!r}"
    # a short header is returned unchanged
    short = SimpleNamespace(number="002", title="DOOR", question="q", answer="",
                            has_answer=False)
    se = layout.make_entries([short])
    _h, sit = layout.layout_entries(se, 400.0)
    assert sit[0][0] == se[0][1], "short header should be untouched"
    print("  #05 long header width-fitted, status suffix preserved OK")


# --------------------------------------------------------- #34 grid endpoint --

def test_find_spot_endpoint():
    """The only clear window sits at the far x-edge of the 'anywhere' zone,
    off the STEP grid -- reachable only because find_spot now tests endpoints."""
    W = Hh = 400
    scale = 90 / 72.0
    w_pt = h_pt = 40.0
    w_px = int(w_pt * scale) + 2 * layout.SEARCH_PAD
    X0 = int(0.035 * W)
    X1 = int(0.965 * W) - w_px            # far-edge top-left x for the last zone
    assert (X1 - X0) % layout.STEP != 0, "test needs X1 off the STEP grid"
    gray = np.zeros((Hh, W), dtype=np.uint8)     # all dark (content)
    gray[:, X1:X1 + w_px] = 255                   # one clear column band
    ii = layout.integral(gray)
    got = layout.find_spot(ii, W, Hh, w_pt, h_pt, scale, [])
    assert got, "clear window at the zone far edge should be found"
    assert got[2][0] == X1, f"expected the endpoint x={X1}, got occ={got[2]}"
    print("  #34 find_spot reaches the zone far-edge endpoint OK")


def main():
    test_cropbox_anchor()
    test_header_width_fit()
    test_find_spot_endpoint()
    print("REBUILD STAMP/LAYOUT TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
