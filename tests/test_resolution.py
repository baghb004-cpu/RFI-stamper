"""Self-contained tests for rfi_stamper.resolution — the RFI status lifecycle.

Builds a tiny fake plan set (one rotation-0 page, one /Rotate 90 page) and two
fake RFI PDFs the same way tests/smoke_test.py does, then exercises:

* ResolutionStore roundtrip / history / seed (never downgrades) / counts
* status_suffix formatting
* layout.make_entries with statuses (suffix appended) and without (unchanged)
* FULL pipeline.run with statuses={...}: verify_ok True and the suffix text
  extractable from the stamped output
* pickup_pdf: verified items excluded, next-step phrasing, opens in fitz
* regression: subprocess-run tests/smoke_test.py must exit 0

No project data needed.  Run:  python3.12 tests/test_resolution.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz                                           # noqa: E402
from pypdf import PdfReader, PdfWriter               # noqa: E402
from rfi_stamper.minipdf.pagesizes import letter     # noqa: E402
from rfi_stamper.minipdf import canvas               # noqa: E402

from rfi_stamper import layout, pipeline, resolution  # noqa: E402
from rfi_stamper.resolution import (                  # noqa: E402
    LABELS, NEXT_STEP, STATUSES, ResolutionStore, pickup_pdf, status_suffix)

VW, VH = 1224.0, 792.0        # viewer size of both fake sheets (11x17 landscape)


# ------------------------------------------------- fixtures (as smoke_test) --

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
    b1 = io.BytesIO()
    c = canvas.Canvas(b1, pagesize=(VW, VH))
    _draw_sheet(c, "T-1.01", "RESOLUTION TEST PLAN — SHEET ONE")
    c.save()
    b2 = io.BytesIO()
    c = canvas.Canvas(b2, pagesize=(VH, VW))             # media 792 x 1224
    c.translate(VH, 0)
    c.rotate(90)
    _draw_sheet(c, "T-1.02", "RESOLUTION TEST PLAN — SHEET TWO")
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
                 "Attachments:", "reso.pdf  1 MB"]:
        c.setFont("Helvetica", 10)
        c.drawString(54, y, line)
        y -= 16
    c.save()


def build_fixtures(tmp):
    plan = os.path.join(tmp, "plan.pdf")
    make_plan(plan)
    rfi_dir = os.path.join(tmp, "rfis")     # separate dir so the plan PDF
    os.makedirs(rfi_dir)                    # itself is not parsed as an RFI
    make_rfi(os.path.join(rfi_dir, "RFI001.pdf"), "001", "Fake Pipe Routing",
             "T-1.01",
             "Where should the fake pipe route around the three fixtures?",
             "Route the fake pipe per detail 5 on this sheet and coordinate "
             "with the architect before rough-in.")
    make_rfi(os.path.join(rfi_dir, "RFI002.pdf"), "002", "Missing Cleanout",
             "T-1.02",
             "No cleanout is shown at the end of the fake run. Please advise.",
             "")   # unanswered
    index, rows = pipeline.scan(plan, [rfi_dir], log=lambda m: None)
    assert [r.record.number for r in rows] == ["001", "002"], \
        [r.record.number for r in rows]
    m = {r.record.number: r for r in rows}
    assert m["001"].pages == [1] and m["002"].pages == [2], "fixture mapping"
    assert m["001"].record.has_answer and not m["002"].record.has_answer
    return plan, index, rows


def page_texts(pdf_path):
    doc = fitz.open(pdf_path)
    texts = [p.get_text() for p in doc]
    doc.close()
    return texts


def expect(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc:
        return
    raise AssertionError(f"expected {exc.__name__} from {fn!r}{args!r}")


# ---------------------------------------------------------------- suffixes --

def test_status_suffix():
    assert status_suffix("answered") == " · ANSWERED", \
        repr(status_suffix("answered"))
    assert status_suffix("in_work") == " · IN WORK"
    assert status_suffix("open") == " · OPEN"
    assert status_suffix("fixed") == " · FIXED"
    assert status_suffix("verified") == " · VERIFIED"
    assert status_suffix("") == ""
    assert status_suffix(None) == ""            # type: ignore[arg-type]
    assert status_suffix("bogus") == ""
    assert status_suffix(" Answered ") == " · ANSWERED"   # lenient input
    # every known status has a label and (except verified) a next step
    for s in STATUSES:
        assert LABELS[s], s
        assert s == "verified" or NEXT_STEP[s], s


# ------------------------------------------------------------------- store --

def test_store(tmp, records):
    plan = os.path.join(tmp, "store_plan.pdf")
    with open(plan, "wb") as f:                 # store never opens the PDF
        f.write(b"%PDF-fake")
    st = ResolutionStore(plan)
    assert st.path == plan + ResolutionStore.SUFFIX
    assert st.get("001") == "" and st.statuses() == {}
    assert st.counts() == {s: 0 for s in STATUSES}

    # validation
    expect(ValueError, st.set, "001", "done")
    expect(ValueError, st.set, "", "open")

    # set + history + autosave
    st.set("001", "answered", note="answer received", author="pm")
    st.set("001", "in_work")
    st.set("1", "fixed")                        # '1' normalizes to '001'
    assert st.get("001") == "fixed"
    hist = st.history("001")
    assert [e["status"] for e in hist] == ["answered", "in_work", "fixed"], hist
    assert hist[0]["note"] == "answer received" and hist[0]["author"] == "pm"
    assert all(e["ts"] for e in hist), "timestamps must be recorded"

    # sidecar exists, is versioned JSON, and no temp file was left behind
    assert os.path.isfile(st.path), "autosave did not write the sidecar"
    assert not os.path.exists(st.path + ".part"), "temp file left behind"
    with open(st.path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["version"] == 1 and "rfis" in data, data.keys()

    # roundtrip: a fresh store on the same plan autoloads everything
    st2 = ResolutionStore(plan)
    assert st2.get("001") == "fixed"
    assert [e["status"] for e in st2.history("001")] == \
        ["answered", "in_work", "fixed"]

    # seed: untracked -> answered/open by rec.has_answer; NEVER downgrades
    added = st2.seed_from_records(records)
    assert added == 1, f"only 002 was untracked, added={added}"
    assert st2.get("001") == "fixed", "seed downgraded a hand-set status"
    assert st2.get("002") == "open"
    # re-seed is a no-op
    assert st2.seed_from_records(records) == 0
    assert st2.get("001") == "fixed" and st2.get("002") == "open"

    # seed on a fresh store derives from has_answer
    st3 = ResolutionStore(os.path.join(tmp, "other_plan.pdf"))
    assert st3.seed_from_records(records) == 2
    assert st3.get("001") == "answered" and st3.get("002") == "open"

    # statuses / counts
    assert st2.statuses() == {"001": "fixed", "002": "open"}
    c = st2.counts()
    assert c == {"open": 1, "answered": 0, "in_work": 0, "fixed": 1,
                 "verified": 0}, c

    # in-memory store (no plan path) works but cannot autosave
    st4 = ResolutionStore()
    st4.set("007", "open")
    assert st4.get("007") == "open"
    expect(ValueError, st4.save)
    side = os.path.join(tmp, "explicit.rfistatus.json")
    st4.save(side)
    st5 = ResolutionStore()
    st5.load(side)
    assert st5.get("007") == "open"


# ------------------------------------------------------------ make_entries --

def test_make_entries(records):
    before = layout.make_entries(records)
    # without statuses: byte-identical to the pre-hook construction
    for (num, hdr, body), r in zip(before, records):
        assert num == r.number
        assert hdr == f"RFI {r.number} — {layout.clip(r.title, 46).upper()}"
    assert layout.make_entries(records) == before
    assert layout.make_entries(records, statuses=None) == before
    assert layout.make_entries(records, statuses={}) == before

    # with statuses: suffix appended to the header, body untouched
    stat = {"001": "answered", "002": "open"}
    after = layout.make_entries(records, statuses=stat)
    for (num, hdr, body), (num0, hdr0, body0) in zip(after, before):
        assert hdr == hdr0 + status_suffix(stat[num]), hdr
        assert body == body0
    # unknown status / untracked number -> header unchanged
    assert layout.make_entries(records, statuses={"001": "bogus"}) == before
    assert layout.make_entries(records, statuses={"999": "fixed"}) == before


# ------------------------------------------------------------ full pipeline --

def test_pipeline_with_statuses(tmp, plan, index, rows):
    store = ResolutionStore(plan)
    store.seed_from_records([r.record for r in rows])
    assert store.statuses() == {"001": "answered", "002": "open"}

    out = os.path.join(tmp, "plan_RFI_overlay.pdf")
    rep = pipeline.run(plan, out_path=out, rows=rows, index=index,
                       statuses=store.statuses(), log=lambda m: None)
    assert rep.verify_ok, "verification failed — see report in " + tmp
    assert 1 in rep.placements and 2 in rep.placements, rep.placements.keys()
    assert not rep.appendix, "nothing should have fallen to the appendix"

    texts = page_texts(out)
    assert "· ANSWERED" in texts[0], \
        "suffix missing from stamped note on sheet one:\n" + texts[0]
    assert "· OPEN" in texts[1], \
        "suffix missing from stamped note on sheet two (/Rotate 90)"
    # the note itself is still there around the suffix
    assert "RFI 001" in texts[0] and "FAKE PIPE ROUTING" in texts[0]

    # and without statuses the stamped output carries no suffix (regression)
    out2 = os.path.join(tmp, "plan_RFI_overlay_nostatus.pdf")
    rep2 = pipeline.run(plan, out_path=out2, rows=rows, index=index,
                        log=lambda m: None)
    assert rep2.verify_ok
    plain = page_texts(out2)
    assert "·" not in plain[0] and "·" not in plain[1], \
        "suffix leaked into a run without statuses"


# -------------------------------------------------------------- pickup pdf --

def test_pickup_pdf(tmp, plan, index, rows):
    store = ResolutionStore(plan)      # reloads the seeded sidecar from above
    store.set("001", "verified", note="walked the site")
    out = os.path.join(tmp, "pickup.pdf")

    res = pickup_pdf(rows, index, store, out, log=lambda m: None)
    assert res["items"] == 1 and res["rows"] == 1, res
    assert res["out_path"] == out and os.path.isfile(out)

    doc = fitz.open(out)
    text = "\n".join(p.get_text() for p in doc)
    doc.close()
    assert "DESIGNER PICKUP SHEET" in text
    # RFI 002 (open) is listed with its sheet, status label and next step
    assert "T-1.02" in text and "002" in text and "OPEN" in text
    assert "Answer pending" in text and "do not build" in text
    # RFI 001 is verified -> excluded entirely
    assert "Fake Pipe Routing" not in text, "verified item must be excluded"
    assert "Incorporate answer" not in text

    # with nothing verified, both rows appear, sorted by sheet then RFI,
    # and an untracked answered RFI derives status "answered"
    store2 = ResolutionStore()         # empty: both RFIs untracked
    out2 = os.path.join(tmp, "pickup_all.pdf")
    res2 = pickup_pdf(rows, index, store2, out2, title="PICKUP TWO",
                      log=lambda m: None)
    assert res2["items"] == 2, res2
    doc = fitz.open(out2)
    text2 = "\n".join(p.get_text() for p in doc)
    doc.close()
    assert "PICKUP TWO" in text2
    assert "ANSWERED" in text2 and "Incorporate answer; mark In Work" in text2
    assert text2.find("T-1.01") < text2.find("T-1.02"), "sheet sort order"


# -------------------------------------------------------------- regression --

def test_smoke_regression():
    here = os.path.dirname(os.path.abspath(__file__))
    r = subprocess.run([sys.executable, os.path.join(here, "smoke_test.py")],
                       cwd=os.path.dirname(here),
                       capture_output=True, text=True)
    assert r.returncode == 0, \
        f"smoke_test.py regressed:\n{r.stdout}\n{r.stderr}"


def main():
    tmp = tempfile.mkdtemp(prefix="rfi_reso_")
    plan, index, rows = build_fixtures(tmp)
    records = [r.record for r in rows]

    test_status_suffix()
    test_store(tmp, records)
    test_make_entries(records)
    test_pipeline_with_statuses(tmp, plan, index, rows)
    test_pickup_pdf(tmp, plan, index, rows)
    test_smoke_regression()

    print("RESOLUTION TESTS PASSED  (store roundtrip/history/seed/counts, "
          "suffix format, make_entries hook, full pipeline verify + stamped "
          "suffix, pickup sheet, smoke regression)")
    print("outputs in", tmp)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("RESOLUTION TEST FAILED:", e)
        sys.exit(1)
