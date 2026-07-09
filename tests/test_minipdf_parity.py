"""P2 parity gate: the from-scratch writer must be behavior-identical to
reportlab on the stamp path, and must satisfy the real pixel-diff verifier.

Two independent proofs:

* **pixel identity** — the SAME note-box overlay drawn through reportlab and
  through minipdf, rendered by fitz, is identical to the gray level (both engines
  emit non-embedded Helvetica + the same operators, so fitz rasterizes them the
  same).  This is the strict ``changed==0`` bar MINIPDF_PLAN §6 hoped for.
* **invariant 4 end-to-end** — the full stamp+verify pipeline run with
  ``PLOOM_PDF_ENGINE=minipdf`` passes verify.py on a rotation-0 page AND a
  /Rotate 90 page (the field-verified hard case), proving the from-scratch box is
  clean, correctly placed, and covers no linework.

reportlab is the oracle here, so the whole file skips if it is absent.

Run:  python3.12 tests/test_minipdf_parity.py   (GUI-free; no display needed)
"""
from __future__ import annotations

import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_N = [0]


def A(cond, msg=""):
    _N[0] += 1
    assert cond, msg


def _have_reportlab():
    try:
        import reportlab  # noqa: F401
        return True
    except Exception:
        return False


def _overlay_bytes(engine, entries, w=612, h=792, x=72, ytop=720):
    """Draw one note box through the selected engine; return the overlay PDF."""
    prev = os.environ.get("PLOOM_PDF_ENGINE")
    os.environ["PLOOM_PDF_ENGINE"] = engine
    try:
        from rfi_stamper.stamp import _new_canvas, draw_box
        buf = io.BytesIO()
        c = _new_canvas(buf, (w, h))
        draw_box(c, x, ytop, 320, entries)
        c.save()
        return buf.getvalue()
    finally:
        if prev is None:
            os.environ.pop("PLOOM_PDF_ENGINE", None)
        else:
            os.environ["PLOOM_PDF_ENGINE"] = prev


def _render(data, dpi):
    import numpy as np
    import fitz
    doc = fitz.open(stream=data, filetype="pdf")
    pix = doc[0].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, alpha=False)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).copy()
    doc.close()
    return a


def _render_file(path, dpi):
    with open(path, "rb") as f:
        return _render(f.read(), dpi)


_ENTRIES = [
    ("014", "RFI 014 — DUCT CONFLICT",
     "Reroute duct below joist; coordinate with structural before rough-in."),
    ("132", "RFI 132 — SLAB EDGE · ANSWERED",
     "Hold slab edge at gridline per detail 5/A5.1."),
    ("207", "RFI 207 — A DELIBERATELY LONG TITLE THAT MUST CLIP CLEANLY "
            "WITHOUT RUNNING PAST THE BORDER · IN WORK",
     "See sketch; ±1/4\" tolerance, 90° elbow typ."),
]


def test_overlay_pixel_identity():
    if not _have_reportlab():
        print("  (reportlab oracle absent — parity skipped)")
        return
    import numpy as np
    rl = _overlay_bytes("reportlab", _ENTRIES)
    mp = _overlay_bytes("minipdf", _ENTRIES)
    for dpi in (90, 300):        # verify.py's dpi + a sub-pixel cross-check
        a = _render(rl, dpi)
        b = _render(mp, dpi)
        A(a.shape == b.shape, f"same raster size at {dpi}dpi: {a.shape} vs {b.shape}")
        d = np.abs(a.astype(int) - b.astype(int))
        A(int(d.max()) == 0,
          f"minipdf overlay is pixel-identical to reportlab at {dpi}dpi "
          f"(max |Δ|={int(d.max())}, {(d > 25).sum()} px over verify threshold)")
    print("  overlay pixel-identity: max |Δ| = 0 at 90 and 300 dpi")


def test_appendix_text_identity():
    """The 'no clear space' appendix header path is identical too."""
    if not _have_reportlab():
        return
    import numpy as np

    def page(engine):
        prev = os.environ.get("PLOOM_PDF_ENGINE")
        os.environ["PLOOM_PDF_ENGINE"] = engine
        try:
            from rfi_stamper.stamp import _new_canvas
            from rfi_stamper.layout import F_HDR, RED
            buf = io.BytesIO()
            c = _new_canvas(buf, (612, 792))
            c.setFont(F_HDR, 13)
            c.setFillColorRGB(*RED)
            c.drawString(60, 730, "RFI NOTES — NO CLEAR SPACE FOUND ON SHEET")
            c.save()
            return buf.getvalue()
        finally:
            if prev is None:
                os.environ.pop("PLOOM_PDF_ENGINE", None)
            else:
                os.environ["PLOOM_PDF_ENGINE"] = prev

    d = np.abs(_render(page("reportlab"), 90).astype(int)
               - _render(page("minipdf"), 90).astype(int))
    A(int(d.max()) == 0, f"appendix header pixel-identical (max |Δ|={int(d.max())})")


def test_pipeline_minipdf_verifies():
    """The whole stamp+verify pipeline is clean with the from-scratch engine."""
    if not _have_reportlab():
        return
    import smoke_test
    from rfi_stamper import pipeline

    tmp = tempfile.mkdtemp(prefix="minipdf_e2e_")
    plan = os.path.join(tmp, "plan.pdf")
    out = os.path.join(tmp, "plan_RFI_overlay.pdf")
    smoke_test.make_plan(plan)
    smoke_test.make_rfi(os.path.join(tmp, "RFI001.pdf"), "001", "Fake Pipe Routing",
                        "T-1.01", "Where should the fake pipe route?",
                        "Route per detail 5 and coordinate before rough-in.")
    smoke_test.make_rfi(os.path.join(tmp, "RFI002.pdf"), "002", "Missing Cleanout",
                        "T-1.02", "No cleanout at the end of the run. Advise.", "")

    index, rows = pipeline.scan(plan, [tmp], log=lambda m: None)
    prev = os.environ.get("PLOOM_PDF_ENGINE")
    os.environ["PLOOM_PDF_ENGINE"] = "minipdf"
    try:
        rep = pipeline.run(plan, out_path=out, rows=rows, index=index,
                           log=lambda m: None)
    finally:
        if prev is None:
            os.environ.pop("PLOOM_PDF_ENGINE", None)
        else:
            os.environ["PLOOM_PDF_ENGINE"] = prev

    A(rep.verify_ok, "from-scratch overlay passes pixel-diff verify (rot-0 + /Rotate 90)")
    A(1 in rep.placements and 2 in rep.placements, "both pages stamped")
    # the DELIVERED file is now clean too: stamp_pdf drops pypdf's /Info so no
    # /Producer or wall-clock date leaks and the bytes are reproducible.
    with open(out, "rb") as f:
        data = f.read()
    A(b"/Producer" not in data and b"/CreationDate" not in data,
      "delivered stamped PDF carries no producer/date metadata")
    print("  pipeline verify_ok + metadata-clean delivery (rot-0 + /Rotate 90)")


def test_plate_parity():
    """draft.py Loft plates (curves + clip + dash) match reportlab visually.

    Plates are not verify.py-gated, so the bar is the same 25-gray-level
    threshold the verifier uses: NO pixel may differ by more than that (curve
    anti-aliasing may nudge a handful of sub-threshold edge pixels).
    """
    if not _have_reportlab():
        return
    import numpy as np
    from rfi_stamper.draft import DraftModel, plate_pdf

    def build():
        m = DraftModel()
        m.add("wall", [(0, 0), (20, 0)], wtype="stud4")
        m.add("wall", [(20, 0), (20, 12)], wtype="cmu8")
        m.add("fixture", [(5, 5)], stencil="wc", rot=0.0, flip=False)
        m.add("grid", [(2, -2), (2, 14)], label="1", bubble="both")   # bubble circles
        m.add("grid", [(-2, 6), (22, 6)], label="A", bubble="both")
        m.add("dim", [(0, 0), (20, 0), (10, -3)])
        m.add("room", [(10, 6)], name="LOBBY", number="101")
        return m

    def plate(engine):
        prev = os.environ.get("PLOOM_PDF_ENGINE")
        os.environ["PLOOM_PDF_ENGINE"] = engine
        try:
            p = os.path.join(tempfile.mkdtemp(prefix="plate_"), f"{engine}.pdf")
            return p, plate_pdf(build(), p, sheet="ARCH D")
        finally:
            if prev is None:
                os.environ.pop("PLOOM_PDF_ENGINE", None)
            else:
                os.environ["PLOOM_PDF_ENGINE"] = prev

    prl, rrl = plate("reportlab")
    pmp, rmp = plate("minipdf")
    A(rrl == rmp, f"same plate result (scale/fit/ops): {rrl} vs {rmp}")
    a = _render_file(prl, 110)
    b = _render_file(pmp, 110)
    A(a.shape == b.shape, f"same raster size: {a.shape} vs {b.shape}")
    over = int((np.abs(a.astype(int) - b.astype(int)) > 25).sum())
    A(over == 0, f"no plate pixel differs beyond the 25-gray verify threshold ({over})")
    print(f"  plate parity: 0 px over threshold (curves/clip/dash), result {rmp}")


def main():
    for fn, label in [
        (test_overlay_pixel_identity, "overlay pixel-identity vs reportlab (90+300 dpi)"),
        (test_appendix_text_identity, "appendix header pixel-identity"),
        (test_pipeline_minipdf_verifies, "full stamp+verify pipeline on the minipdf engine"),
        (test_plate_parity, "draft.py Loft plate parity (curves + clip + dash)"),
    ]:
        fn()
        print(f"PASS {label}")
    print(f"MINIPDF P2 PARITY TEST PASSED  ({_N[0]} checks)")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("MINIPDF PARITY TEST FAILED:", e)
        sys.exit(1)
