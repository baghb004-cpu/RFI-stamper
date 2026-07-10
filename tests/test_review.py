"""Tests for the OCR correction-review lane (Phase G).

Engine half (headless): the read_image review tap is a byte-identical
no-op when unused and queues exactly the mid-band + machine-repair
reads; write_searchable stamps pages and honors overrides (replace and
reject) with the pixel verify still green; the human-gated Corrections →
promote → FontProfile round trip.

GUI half (xvfb, self-re-exec like test_bim.py): the review deck — rows,
detail pane, accept/skip/batch handlers called directly (never synthetic
OS key events), the edit-length alignment fence, the overrides dict, and
the atomic audit trail.

Run:  python3 tests/test_review.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz                                     # noqa: E402
import numpy as np                              # noqa: E402

from rfi_stamper import ocr                     # noqa: E402
from rfi_stamper import tracer                  # noqa: E402
from rfi_stamper.tracer import TAU_HI, TAU_LO, classify, render  # noqa: E402
from rfi_stamper.tracer.profile import Corrections, FontProfile  # noqa: E402

TMP = tempfile.mkdtemp(prefix="ploom_review_")


def _quiet(*_a, **_k):
    pass


def _scanned_pdf(name, lines, size=(500, 160)):
    """Image-only (scanned-style) one-page PDF with the given text lines."""
    path = os.path.join(TMP, name)
    doc = fitz.open()
    pg = doc.new_page(width=size[0], height=size[1])
    for x, y, s in lines:
        pg.insert_text((x, y), s, fontname="helv", fontsize=22)
    pix = pg.get_pixmap(dpi=200)
    doc2 = fitz.open()
    p2 = doc2.new_page(width=size[0], height=size[1])
    p2.insert_image(p2.rect, pixmap=pix)
    doc2.save(path)
    doc.close()
    doc2.close()
    return path


# ------------------------------------------------------------- engine tap ---

def test_tap_noop_and_predicate():
    src = _scanned_pdf("tap.pdf", [(30, 60, "WALL SCHEDULE"),
                                   (30, 110, "PLUMBNG SHAFT")])
    gray, _meta = render.render_gray(src, 1, dpi=300)
    plain = tracer.read_image(gray, dpi=300)
    sink: list = []
    tapped = tracer.read_image(gray, dpi=300, review_sink=sink)
    assert plain == tapped, "review_sink changed the read output"
    # no ctx: nothing changed, so the sink holds ONLY the mid-band
    for it in sink:
        assert TAU_LO <= it.conf < TAU_HI, it
        assert it.raw == it.text and not it.why
        assert it.page == 0                     # unstamped at this layer
        assert len(it.glyphs) >= 1
        cell = it.glyphs[0][0]
        assert cell.shape == (28, 28)


def test_sink_through_ocr_pdf():
    src = _scanned_pdf("snap.pdf", [(30, 60, "WALL SCHEDULE"),
                                    (30, 110, "PLUMBNG SHAFT")])
    out = os.path.join(TMP, "snap_searchable.pdf")
    sink: list = []
    res = ocr.ocr_pdf(src, out, log=_quiet, review_sink=sink)
    assert res["pages_ocred"] == 1
    snaps = [it for it in sink
             if it.why.split("|")[0] == "word:lexicon_snap"]
    assert snaps, [(_i.raw, _i.text, _i.why) for _i in sink]
    it = snaps[0]
    # the machine repair is surfaced even though its conf was LIFTED
    assert it.raw == "PLUMBNG" and it.text == "PLUMBING", it
    assert it.conf >= TAU_HI                    # lifted past the mid-band
    assert it.page == 1                         # stamped by the writer
    doc = fitz.open(out)
    assert "PLUMBING" in doc[0].get_text()      # the snap was written
    doc.close()
    return src, out, it


def test_overrides():
    src, out, it = test_sink_through_ocr_pdf()
    # replace: the reviewer's text lands in the searchable layer
    out2 = os.path.join(TMP, "snap_fixed.pdf")
    ocr.ocr_pdf(src, out2, log=_quiet,
                overrides={(it.page, it.bbox): "FLUME"})
    doc = fitz.open(out2)
    text = doc[0].get_text()
    doc.close()
    assert "FLUME" in text and "PLUMBING" not in text, text
    # reject: empty override removes the read entirely
    out3 = os.path.join(TMP, "snap_rej.pdf")
    ocr.ocr_pdf(src, out3, log=_quiet, overrides={(it.page, it.bbox): ""})
    doc = fitz.open(out3)
    text = doc[0].get_text()
    doc.close()
    assert "PLUMBING" not in text and "FLUME" not in text, text
    # determinism: the override key (page, bbox) reproduces across runs
    sink2: list = []
    ocr.ocr_pdf(src, os.path.join(TMP, "snap2.pdf"), log=_quiet,
                review_sink=sink2)
    assert any(s.bbox == it.bbox and s.page == it.page for s in sink2)


def test_corrections_promote_profile():
    src = _scanned_pdf("cells.pdf", [(30, 60, "SHAFT WALL")])
    gray, _ = render.render_gray(src, 1, dpi=300)
    sink: list = []
    tracer.read_image(gray, dpi=300, review_sink=sink)
    # any glyph cell will do; fall back to a read even outside the queue
    if sink:
        cell = sink[0].glyphs[0][0]
    else:
        from rfi_stamper.tracer import binarize, components, normalize
        ink = binarize.binarize(gray)
        _n, boxes = components.label(ink)
        b = max(boxes, key=lambda bb: bb.w * bb.h)
        cell = normalize.norm_glyph(ink[b.y0:b.y1 + 1, b.x0:b.x1 + 1]).cell
    ens = classify.load_ensemble()              # FRESH instance (no global)
    size0 = 0 if ens.knn.X is None else len(ens.knn.X)
    corr = Corrections()
    assert corr.record_correction(cell, "S")
    assert not corr.record_correction(cell, "~")        # not in CHARSET
    size_now = 0 if ens.knn.X is None else len(ens.knn.X)
    assert size_now == size0, "recording a correction must not train"
    assert corr.promote(ens) == 1                       # the human gate
    assert len(ens.knn.X) == size0 + 1
    assert corr.pending == []
    # per-firm profile: save -> load -> apply to another fresh ensemble
    p = os.path.join(TMP, "firm.npz")
    FontProfile.from_ensemble(ens, "firmlabel").save(p)
    prof = FontProfile.load(p)
    assert prof.producer == "firmlabel"
    ens2 = classify.load_ensemble()
    n0 = 0 if ens2.knn.X is None else len(ens2.knn.X)
    assert prof.apply_to(ens2) >= 1
    assert len(ens2.knn.X) > n0


# ------------------------------------------------------------------- deck ---

def _mk_items(pdf_path):
    """Three synthetic ReviewItems anchored on a real rendered page."""
    cell = np.zeros((28, 28), np.float32)
    cell[6:22, 12:16] = 1.0

    def gl(chars, conf):
        return [(cell, (10, 10, 30, 30), ch, conf) for ch in chars]

    return [
        tracer.ReviewItem(1, (60, 60, 160, 100), "SHEFT", "SHAFT", 0.72,
                          "word:lexicon_snap", gl("SHAFT", 0.72)),
        tracer.ReviewItem(1, (60, 120, 160, 160), "WALL", "WALL", 0.95,
                          "word:in_lexicon", gl("WALL", 0.95)),
        tracer.ReviewItem(1, (200, 60, 300, 100), "P-1O1", "P-101", 0.85,
                          "sheet:index_snap|low_conf", gl("P-101", 0.85)),
        tracer.ReviewItem(1, (200, 120, 300, 160), "IIX", "IIX", 0.65,
                          "", gl("IIX", 0.65)),
    ]


def test_deck():
    import tkinter as tk

    from rfi_stamper.gui import review_deck as rd
    from rfi_stamper.gui.theme import ThemeManager

    # keep the audit out of the real home dir
    rd.AUDIT_PATH = os.path.join(TMP, "audit.jsonl")
    src = _scanned_pdf("deck.pdf", [(30, 60, "SHAFT WALL P-101")])

    root = tk.Tk()
    theme = ThemeManager(root)
    items = _mk_items(src)
    applied = {}

    def rerun(overrides, _log):
        applied.update(overrides)
        return {"ok": True}

    deck = rd.ReviewDeck(root, theme, items, src_pdf=src, dpi=200,
                         rerun=rerun, log=_quiet, root=root)
    root.update()
    assert len(deck.tree.get_children()) == 4
    # detail pane populated for the first item (crop + glyph strip)
    assert deck._photos, "detail images missing"
    assert deck.entry.get() == "SHAFT"
    assert str(deck.crop_lbl.cget("image")) != ""

    # accept as-is advances and marks
    deck.accept()
    root.update()
    assert deck.decisions[0] == ("accept", "SHAFT")
    assert deck._sel() == 1

    # skip leaves no decision
    deck.skip()
    root.update()
    assert 1 not in deck.decisions and deck._sel() == 2

    # edit with MATCHING glyph count files corrections for changed chars
    deck.entry.delete(0, "end")
    deck.entry.insert(0, "P-102")
    deck.accept()
    assert deck.decisions[2] == ("edit", "P-102")
    assert len(deck.corrections.pending) == 1           # only the changed 1
    assert deck.corrections.pending[0][1] == "2"

    # batch accept at 0.90 takes EXACTLY the undecided high-conf item
    # (item 3 at 0.65 stays undecided)
    deck.batch_var.set("0.90")
    rd.messagebox.askyesno = lambda *a, **k: True       # confirm dialogs
    deck.batch_accept()
    assert deck.decisions[1] == ("batch", "WALL")
    assert 3 not in deck.decisions

    # edit with MISMATCHED length: no glyph corrections, text still flows
    deck.tree.selection_set("0")
    deck.entry.delete(0, "end")
    deck.entry.insert(0, "SHAFTX")                      # 6 chars vs 5 glyphs
    n_pending = len(deck.corrections.pending)
    deck.accept()
    assert deck.decisions[0] == ("edit", "SHAFTX")
    assert len(deck.corrections.pending) == n_pending, \
        "misaligned edit must not file glyph corrections"

    # overrides dict carries every decision; reject writes empty text
    deck.tree.selection_set("3")
    deck.reject()
    ov = deck.overrides()
    assert ov[(1, (60, 60, 160, 100))] == "SHAFTX"
    assert ov[(1, (60, 120, 160, 160))] == "WALL"
    assert ov[(1, (200, 60, 300, 100))] == "P-102"
    assert ov[(1, (200, 120, 300, 160))] == ""

    # apply routes through the rerun callable
    deck.apply_overrides()
    for _ in range(40):
        root.update()
        if applied:
            break
        root.after(25)
    assert applied == ov

    # close writes the audit trail atomically (all items decided: no ask)
    deck.close()
    root.update()
    with open(rd.AUDIT_PATH, encoding="utf-8") as fh:
        rows = [json.loads(ln) for ln in fh if ln.strip()]
    assert len(rows) == 4
    acts = {r["action"] for r in rows}
    assert acts == {"edit", "reject", "batch"}, acts
    for r in rows:
        assert r["doc"] == "deck.pdf" and r["page"] == 1 and "bbox" in r
    root.destroy()
    print("-- deck portion ok")


# ------------------------------------------------------------------ runner ---

def main():
    try:
        test_tap_noop_and_predicate()
        print("PASS review tap: byte-identical no-op + mid-band predicate")
        test_sink_through_ocr_pdf()
        print("PASS sink through ocr_pdf: lexicon snap queued, page stamped")
        test_overrides()
        print("PASS overrides: replace, reject, deterministic keys")
        test_corrections_promote_profile()
        print("PASS human gate: record -> promote -> font-profile round trip")

        if os.environ.get("DISPLAY"):
            test_deck()
        else:
            xvfb = shutil.which("xvfb-run")
            if xvfb and not os.environ.get("_REVIEW_XVFB"):
                env = dict(os.environ, _REVIEW_XVFB="1")
                r = subprocess.run([xvfb, "-a", sys.executable,
                                    os.path.abspath(__file__)], env=env)
                raise SystemExit(r.returncode)
            print("-- no display and no xvfb-run: deck portion skipped")
        print("REVIEW TESTS PASSED")
    finally:
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
