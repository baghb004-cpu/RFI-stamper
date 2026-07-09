"""Self-contained smoke test: builds a tiny fake plan set (one rotation-0 page,
one /Rotate 90 page) and two fake RFI PDFs, runs the full pipeline, and asserts
sheet detection, mapping, answer parsing, and pixel-diff verification.

No project data needed.  Run after any change:  python tests/smoke_test.py
"""
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pypdf import PdfReader, PdfWriter               # noqa: E402
from rfi_stamper.minipdf.pagesizes import letter     # noqa: E402
from rfi_stamper.minipdf import canvas               # noqa: E402

from rfi_stamper import pipeline                     # noqa: E402

VW, VH = 1224.0, 792.0        # viewer size of both fake sheets (11x17 landscape)


def _draw_sheet(c, sheet_no, title):
    """Draw fake plan content in VIEWER coordinates on the current canvas."""
    c.setLineWidth(1.5)
    c.rect(30, 30, VW - 60, VH - 60)                    # border
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(VW / 2, VH - 70, title)
    c.setLineWidth(1.0)
    for i in range(3):                                   # fake linework, center
        c.rect(430 + i * 130, 330, 100, 180)
    c.setFont("Helvetica", 9)
    c.drawString(470, 300, "FAKE KEYNOTE 1  3/4\" CW UP")
    c.setFont("Helvetica-Bold", 14)
    c.drawString(VW - 110, 45, sheet_no)                 # title-block corner


def make_plan(path):
    # page 1: normal orientation, media == viewer
    b1 = io.BytesIO()
    c = canvas.Canvas(b1, pagesize=(VW, VH))
    _draw_sheet(c, "T-1.01", "SMOKE TEST PLAN \u2014 SHEET ONE")
    c.save()
    # page 2: portrait media + /Rotate 90 (like real Arch sets).  Pre-rotating
    # the canvas by translate(Wm,0);rotate(90) maps viewer (x,y) -> media
    # (Wm - y, x), the same convention stamp._viewer_to_media uses.
    b2 = io.BytesIO()
    c = canvas.Canvas(b2, pagesize=(VH, VW))             # media 792 x 1224
    c.translate(VH, 0)
    c.rotate(90)
    _draw_sheet(c, "T-1.02", "SMOKE TEST PLAN \u2014 SHEET TWO")
    c.save()
    b1.seek(0)
    b2.seek(0)
    w = PdfWriter()
    w.add_page(PdfReader(b1).pages[0])
    p2 = PdfReader(b2).pages[0]
    p2.rotate(90)
    w.add_page(p2)
    with open(path, "wb") as f:
        w.write(f)


def make_rfi(path, num, title, ref, question, answer):
    c = canvas.Canvas(path, pagesize=letter)
    y = 740
    for line in ["Request for Information",
                 f"Document: {num}",
                 f"Title: {title}",
                 f"Plan Ref: {ref}",
                 "Question:", question,
                 "Answer:", answer,
                 "Attachments:", "smoke.pdf  1 MB"]:
        c.setFont("Helvetica", 10)
        c.drawString(54, y, line)
        y -= 16
    c.save()


def main():
    tmp = tempfile.mkdtemp(prefix="rfi_smoke_")
    plan = os.path.join(tmp, "plan.pdf")
    out = os.path.join(tmp, "plan_RFI_overlay.pdf")
    make_plan(plan)
    make_rfi(os.path.join(tmp, "RFI001.pdf"), "001", "Fake Pipe Routing", "T-1.01",
             "Where should the fake pipe route around the three fixtures?",
             "Route the fake pipe per detail 5 on this sheet and coordinate "
             "with the architect before rough-in.")
    make_rfi(os.path.join(tmp, "RFI002.pdf"), "002", "Missing Cleanout", "T-1.02",
             "No cleanout is shown at the end of the fake run. Please advise.",
             "")   # unanswered

    index, rows = pipeline.scan(plan, [tmp], log=lambda m: None)
    sheets = [p.sheet for p in index.pages]
    assert sheets == ["T-1.01", "T-1.02"], f"sheet detection: {sheets}"
    m = {r.record.number: r for r in rows}
    assert m["001"].pages == [1] and m["001"].via == "planref", m["001"]
    assert m["002"].pages == [2] and m["002"].via == "planref", m["002"]
    assert m["001"].record.has_answer is True
    assert m["002"].record.has_answer is False

    rep = pipeline.run(plan, out_path=out, rows=rows, index=index,
                       log=lambda m: None)
    assert rep.verify_ok, "verification failed \u2014 see report in " + tmp
    assert 1 in rep.placements and 2 in rep.placements, rep.placements.keys()
    assert not rep.appendix, "nothing should have fallen to the appendix"
    print("SMOKE TEST PASSED  (rotation-0 page, /Rotate 90 page, mapping, "
          "answer parse, placement, pixel-diff verify)")
    print("outputs in", tmp)


if __name__ == "__main__":
    main()
