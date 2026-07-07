"""Self-contained test for rfi_stamper.batch.

Builds two tiny fake plan sets (each a rotation-0 page + a /Rotate 90 page with
detectable sheet numbers) and a couple of fake RFI PDFs that reference those
sheets, then runs ``batch_stamp`` over both plans plus one bogus (nonexistent)
plan path.  Asserts ordering, on-disk output, per-plan verification, resilient
error capture, and the ``batch_summary`` tally.

No project data needed.  Run:  python3.12 tests/test_batch.py
"""
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pypdf import PdfReader, PdfWriter               # noqa: E402
from reportlab.lib.pagesizes import letter           # noqa: E402
from reportlab.pdfgen import canvas                  # noqa: E402

from rfi_stamper import batch                        # noqa: E402

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


def make_plan(path, sheet1, sheet2, tag):
    """Two-page plan set: page 1 rotation-0, page 2 portrait media + /Rotate 90."""
    b1 = io.BytesIO()
    c = canvas.Canvas(b1, pagesize=(VW, VH))
    _draw_sheet(c, sheet1, f"BATCH TEST {tag} — SHEET ONE")
    c.save()
    # page 2: portrait media + /Rotate 90; pre-rotate the canvas so viewer
    # (x,y) -> media (Wm - y, x), the convention stamp._viewer_to_media uses.
    b2 = io.BytesIO()
    c = canvas.Canvas(b2, pagesize=(VH, VW))             # media 792 x 1224
    c.translate(VH, 0)
    c.rotate(90)
    _draw_sheet(c, sheet2, f"BATCH TEST {tag} — SHEET TWO")
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
                 "Attachments:", "batch.pdf  1 MB"]:
        c.setFont("Helvetica", 10)
        c.drawString(54, y, line)
        y -= 16
    c.save()


def main():
    tmp = tempfile.mkdtemp(prefix="rfi_batch_")

    # Both plans share the same two sheet numbers so the shared RFI pile maps
    # to both; the bodies differ so the outputs are genuinely distinct plans.
    plan_a = os.path.join(tmp, "planA.pdf")
    plan_b = os.path.join(tmp, "planB.pdf")
    make_plan(plan_a, "T-1.01", "T-1.02", "A")
    make_plan(plan_b, "T-1.01", "T-1.02", "B")

    rfi_dir = os.path.join(tmp, "rfis")
    os.makedirs(rfi_dir)
    make_rfi(os.path.join(rfi_dir, "RFI001.pdf"), "001", "Fake Pipe Routing",
             "T-1.01",
             "Where should the fake pipe route around the three fixtures?",
             "Route the fake pipe per detail 5 on this sheet and coordinate "
             "with the architect before rough-in.")
    make_rfi(os.path.join(rfi_dir, "RFI002.pdf"), "002", "Missing Cleanout",
             "T-1.02",
             "No cleanout is shown at the end of the fake run. Please advise.",
             "")   # unanswered

    out_dir = os.path.join(tmp, "out")

    # --- happy path: two real plans -------------------------------------
    calls = []

    def progress(i, n, plan_path):
        calls.append((i, n, plan_path))

    items = batch.batch_stamp([plan_a, plan_b], [rfi_dir], out_dir=out_dir,
                              log=lambda m: None, progress=progress)

    assert len(items) == 2, f"expected 2 items, got {len(items)}"
    assert [it.plan_path for it in items] == [plan_a, plan_b], "order not preserved"
    for it in items:
        assert it.error == "", f"unexpected error for {it.plan_path}: {it.error}"
        assert it.verify_ok is True, f"verify not ok for {it.plan_path}"
        assert it.report is not None, "report missing on success"
        assert it.out_path and os.path.isfile(it.out_path), \
            f"output not written: {it.out_path}"
        assert it.out_path.startswith(out_dir), \
            f"output not in out_dir: {it.out_path}"

    # distinct output files, one per plan, in order
    assert items[0].out_path != items[1].out_path, "outputs collided"
    assert os.path.basename(items[0].out_path) == "planA_RFI_overlay.pdf", \
        items[0].out_path
    assert os.path.basename(items[1].out_path) == "planB_RFI_overlay.pdf", \
        items[1].out_path

    # progress fired once per plan, zero-based, in order
    assert calls == [(0, 2, plan_a), (1, 2, plan_b)], f"progress calls: {calls}"

    s = batch.batch_summary(items)
    assert s == {"total": 2, "passed": 2, "failed": 0, "verified": 2}, s

    # --- resilience: a bogus plan path must not sink the batch ----------
    bogus = os.path.join(tmp, "does_not_exist.pdf")
    items2 = batch.batch_stamp([plan_a, plan_b, bogus], [rfi_dir],
                               out_dir=out_dir, log=lambda m: None)
    assert len(items2) == 3, f"expected 3 items, got {len(items2)}"
    assert [it.plan_path for it in items2] == [plan_a, plan_b, bogus], \
        "order not preserved with bogus plan"

    good1, good2, bad = items2
    assert good1.error == "" and good1.verify_ok is True, "plan A regressed"
    assert good2.error == "" and good2.verify_ok is True, "plan B regressed"
    assert os.path.isfile(good1.out_path) and os.path.isfile(good2.out_path)

    assert bad.error != "", "bogus plan should have recorded an error"
    assert bad.verify_ok is False, "bogus plan should not verify"
    assert bad.report is None, "bogus plan should have no report"

    s2 = batch.batch_summary(items2)
    assert s2 == {"total": 3, "passed": 2, "failed": 1, "verified": 2}, s2

    print("BATCH TESTS PASSED  (two plan sets stamped + verified, per-plan "
          "error isolation, summary tally)")
    print("outputs in", tmp)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("BATCH TEST FAILED:", e)
        sys.exit(1)
