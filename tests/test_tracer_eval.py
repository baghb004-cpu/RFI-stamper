"""The Tracer — Phase P4: the accuracy eval harness (the finish line).

Deterministic, offline, NDA-safe: every fixture is synthesized in-process with
fitz + seeded numpy, no project data and no network.  This is the artifact that
PROVES the from-scratch engine's accuracy bar before the external OCR binary is
retired (OCR_PLAN §7 P4 / §6 test plan).  It scores the Tracer through
``tracer.eval`` (CER/WER + auto-labeled real ground truth + confusion
sub-metric) and asserts, in one place:

* **clean auto-labeled real set** — rasterize the vector uppercase paragraphs the
  app itself renders, OCR them, score against ``fitz.get_text("words")``:
  **CER ≤ 2 %** (the P2 bar, re-asserted here as the parity artifact).
* **sheet-number field accuracy** with the set's own index cross-check:
  **≥ 99 %** (the P3 bar) — and the cross-check demonstrably LIFTS raw accuracy.
* **two documented degraded tiers** — (a) a *speckled* photocopy (blur + noise +
  salt-pepper) is asserted to read within the clean bar (≤ 2 %): the noise-robust
  glyph-height scale (``components._median_glyph_h``) means a scan's speckle can
  never again collapse the size gates and delete thin glyphs (``I - .``) — the
  regression guard for an 11 % residual that was ENTIRELY dropped thin marks; and
  (b) a *touching-glyph* photocopy (heavy toner spread welds neighbors) scored
  under a LOOSE ceiling (≤ 15 %) — the honest touching/broken-glyph residual of
  OCR_PLAN §8, tracked in the suite and never a hard fail.  WER is reported.
* the **confusion sub-metric** is computed over the aligned (true, pred) pairs.

There is intentionally NO Tesseract comparison: the shipped build carries no
external OCR engine, so the PASS criterion is the Tracer's ABSOLUTE bar.  (A
Tesseract engine may happen to exist on a dev box, but nothing here — and
nothing in live code — references it.)

Run:  python3.12 tests/test_tracer_eval.py
"""
from __future__ import annotations

import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                   # noqa: E402
import fitz                                          # noqa: E402

from rfi_stamper import tracer                       # noqa: E402
from rfi_stamper.tracer import eval as tracer_eval   # noqa: E402
from rfi_stamper.tracer import synth                 # noqa: E402
from rfi_stamper.tracer.fonts import CHARSET         # noqa: E402
from rfi_stamper.tracer.lexicon import Context, Tok, correct  # noqa: E402
from rfi_stamper.core import SHEET_TOKEN, canon      # noqa: E402

_N = [0]
_REPORT: dict = {}


def A(cond, msg=""):
    _N[0] += 1
    assert cond, msg


# --------------------------------------------------------------------------- #
#  Fixtures (deterministic, synthesized in-process)                           #
# --------------------------------------------------------------------------- #

def _paragraph_page():
    """A page of all-uppercase technical prose — the app's own vector render.

    Rasterized, this is a clean 300-dpi "real" scan whose ground truth is the
    very ``get_text("words")`` we score against (zero human cost, no leakage).
    """
    doc = fitz.open()
    pg = doc.new_page(width=612, height=792)
    para = ("GENERAL NOTES REFER TO SHEET P-101 FOR PLUMBING RISER DIAGRAM. "
            "ALL DIMENSIONS IN FEET AND INCHES. VERIFY IN FIELD. "
            "SEE STRUCTURAL DRAWINGS S-200 AND S-201 FOR FRAMING. "
            "FIRE RATING PER CODE. TYPICAL UNLESS NOTED OTHERWISE. "
            "MAXIMUM HEIGHT EIGHT FEET. WINDOW SCHEDULE ON SHEET A-401.")
    y = 90
    for line in textwrap.wrap(para, 46):
        pg.insert_text((60, y), line, fontname="helv", fontsize=13)
        y += 26
    return doc, pg


def _render_token(tok, cap=42, w=360, h=140):
    doc = fitz.open()
    pg = doc.new_page(width=w, height=h)
    pg.insert_text((30, 90), tok, fontname="helv", fontsize=cap)
    pix = pg.get_pixmap(dpi=300, colorspace=fitz.csGRAY, alpha=False)
    g = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).copy()
    doc.close()
    return g


def _degrade(g, blur_sigma, noise_sigma, seed):
    """A deterministic blurred + noised 'photocopy' of a raster."""
    rng = np.random.default_rng(seed)
    x = synth.blur(g.astype(float), blur_sigma)
    return np.clip(synth.add_noise(x, noise_sigma, 0.002, rng), 0, 255).astype(np.uint8)


def _degrade_touching(g, seed):
    """A deterministic heavy-toner photocopy: grayscale dilation welds neighbors.

    One 3×3 toner-gain pass fuses adjacent glyphs into single connected blobs
    before blur + noise — the genuine touching/broken-glyph regime of OCR_PLAN §8
    (where classification, not the size gate, is the residual).
    """
    rng = np.random.default_rng(seed)
    x = synth._morph(g.astype(float), dilate_ink=True)
    x = synth.blur(x, 0.8)
    return np.clip(synth.add_noise(x, 8, 0.004, rng), 0, 255).astype(np.uint8)


def _degrade_gen3(g, seed):
    """A third-generation photocopy: TWO toner-gain passes + heavier blur
    and noise.  Since P5 pulled the single-weld tier under 2%, this tier
    inherits the "genuinely degrades" role — the suite must always track
    a real, non-zero residual (OCR_PLAN §8 framing: the synthetic tier is
    a lower bound, not a promise)."""
    rng = np.random.default_rng(seed)
    x = synth._morph(g.astype(float), dilate_ink=True)
    x = synth._morph(x, dilate_ink=True)
    x = synth.blur(x, 0.9)
    return np.clip(synth.add_noise(x, 10, 0.004, rng), 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------- #
#  1. clean auto-labeled real set — CER ≤ 2 % (the parity artifact)           #
# --------------------------------------------------------------------------- #

def test_clean_cer_bar():
    doc, pg = _paragraph_page()
    pairs = tracer_eval.auto_label_set([pg], dpi=300)
    A(len(pairs) == 1, "one page → one (image, truth) pair")
    gray, truth = pairs[0]
    A(gray.ndim == 2 and len(truth) > 20, "auto-label yields raster + truth words")
    A(all(len(w) == 5 for w in truth), "truth words are (x0,y0,x1,y1,text)")

    res = tracer_eval.score_page(gray, truth, dpi=300)
    A(res["n_ref"] > 150, f"the paragraph has real length, n={res['n_ref']}")
    # P5 tightened this from ≤ 2% to EXACTLY zero: the clean tier is
    # deterministic and measures 0.00 — any lattice change that grazes a
    # clean read must fail loudly, not hide under a tolerance
    A(res["cer"] <= 1e-9,
      f"clean auto-labeled CER == 0 (P5 bar), got {res['cer']:.4f}")

    # the confusion sub-metric is COMPUTED over the aligned (true, pred) pairs
    apairs = tracer_eval.align_pairs(res["ref"], res["hyp"])
    M = tracer_eval.confusion(apairs)
    A(M.shape == (len(CHARSET), len(CHARSET)), "confusion matrix is 43×43")
    dom = tracer_eval.domain_error(apairs)
    A(0.0 <= dom <= 1.0, f"domain error in [0,1], got {dom}")

    _REPORT["clean_cer"] = res["cer"]
    _REPORT["clean_wer"] = res["wer"]
    _REPORT["clean_n"] = res["n_ref"]
    _REPORT["clean_domain_err"] = dom
    _REPORT["clean_confusion_offdiag"] = int(M.sum() - np.trace(M))
    doc.close()


# --------------------------------------------------------------------------- #
#  2. sheet-number field accuracy — ≥ 99 % via the set's own index            #
# --------------------------------------------------------------------------- #

_SHEETS = ["A-101", "P-201", "S-100", "M-401", "E-1.10", "C-501", "G-001",
           "D-410", "V-620", "A-808"]


def test_sheet_field_accuracy():
    ctx = Context.build(sheet_hints=_SHEETS)
    conds = [(0, 0), (1, 1), (2, 2)]                  # 10 × 3 = 30 samples
    tot = ok = raw_ok = 0
    for si, s in enumerate(_SHEETS):
        truth = canon(*SHEET_TOKEN.search(s).groups())
        for sev, sd in conds:
            if sev == 0:
                g = _render_token(s)
            else:
                # a DETERMINISTIC per-sample seed (not hash(), whose per-process
                # randomization made a ≥99% assertion flaky — the degradation set
                # must be identical every run)
                g = _degrade(_render_token(s),
                             0.6 if sev == 1 else 0.9, 6 if sev == 1 else 12,
                             (si * 131 + sd * 17 + 1) % 99999)
            reads = tracer.read_image(g, dpi=300)      # ROI OCR (no hints)
            joined = "".join(w[4] for w in sorted(reads, key=lambda w: w[0]))
            got = correct(Tok(joined, (9, 9, 9, 9), 0.9), None, ctx)["text"]
            mg = SHEET_TOKEN.search(got.upper())
            mr = SHEET_TOKEN.search(joined.upper())
            tot += 1
            ok += 1 if (mg and canon(*mg.groups()) == truth) else 0
            raw_ok += 1 if (mr and canon(*mr.groups()) == truth) else 0
    acc = ok / tot
    raw = raw_ok / tot
    A(tot >= 24, f"the sheet set is sizeable, n={tot}")
    A(acc >= 0.99,
      f"sheet-number field accuracy ≥ 99% (the field bar), got {acc:.4f}")
    A(acc > raw,
      f"the index cross-check LIFTS accuracy: {acc:.3f} > raw {raw:.3f}")

    _REPORT["sheet_field_acc"] = acc
    _REPORT["sheet_raw_acc"] = raw
    _REPORT["sheet_n"] = tot


# --------------------------------------------------------------------------- #
#  3. two documented degraded tiers — speckle guard + touching-glyph residual  #
# --------------------------------------------------------------------------- #

def test_degraded_tier():
    doc, pg = _paragraph_page()
    gray, truth = tracer_eval.auto_label_set([pg], dpi=300)[0]

    # (a) SPECKLED LIGHT PHOTOCOPY — the thin-glyph robustness guard.
    # Blur + Gaussian noise + salt-pepper.  Before the noise-robust glyph-height
    # scale (components._median_glyph_h), the speckle flooded the box set with
    # 1–2 px components, collapsed the median glyph height to ~1 px, and the
    # elongation size gate then deleted every thin glyph (I - .) as "linework":
    # an 11 % CER that was ENTIRELY dropped thin marks (0 substitutions).  With
    # the fix this reads within the clean bar, so we assert it tightly — a scan's
    # speckle must never again silently swallow thin marks.
    dg = _degrade(gray, blur_sigma=0.7, noise_sigma=8, seed=2)
    res = tracer_eval.score_page(dg, truth, dpi=300)
    A(res["cer"] <= 0.02,
      f"speckled-photocopy CER within the clean bar (thin-glyph robustness), "
      f"got {res['cer']:.4f}")

    # (b) TOUCHING-GLYPH PHOTOCOPY — the honest residual OCR_PLAN §8 names.
    # Heavy toner spread welds neighboring glyphs into one blob; this is the
    # genuine touching/broken-glyph limit, tracked under a LOOSE ceiling and
    # never a hard fail (here the residual is real substitutions, not the
    # spurious deletions the size-gate bug used to manufacture).
    tg = _degrade_touching(gray, seed=2)
    rest = tracer_eval.score_page(tg, truth, dpi=300)
    # P5 tightened this from the ≤ 15% loose ceiling: the split+merge
    # lattice + bigram prior + masked word crop measured 0.00% (from
    # 3.38%).  ≤ 2% stays the hard bar — the weld-masquerade regime is
    # real (gen-3 below carries it) and a zero must never become a
    # brittle promise
    A(rest["cer"] <= 0.02,
      f"touching-glyph CER ≤ 2% (the P5 bar), got {rest['cer']:.4f}")
    # word re-tokenization: welds/dilation across spaces must not fuse
    # words (was a constant 100% before P5 — partly a metric artifact,
    # partly group_words running before splitting)
    A(rest["wer"] < 0.50,
      f"touching-glyph WER < 50% (word re-tokenization), "
      f"got {rest['wer']:.4f}")
    apairs = tracer_eval.align_pairs(rest["ref"], rest["hyp"])
    M = tracer_eval.confusion(apairs)
    A(M.shape == (len(CHARSET), len(CHARSET)), "touching confusion matrix 43×43")

    # (c) GEN-3 PHOTOCOPY — the honest hard residual (double weld pass).
    g3 = _degrade_gen3(gray, seed=3)
    res3 = tracer_eval.score_page(g3, truth, dpi=300)
    A(res3["cer"] <= 0.20,
      f"gen-3 CER under the regression ceiling, got {res3['cer']:.4f}")
    A(res3["cer"] > 0.0,
      "the gen-3 tier genuinely degrades (has errors to track)")
    apairs3 = tracer_eval.align_pairs(res3["ref"], res3["hyp"])
    dom = tracer_eval.domain_error(apairs3)
    A(dom > 0.0, "the gen-3 tier's domain error is non-zero")

    _REPORT["degraded_cer"] = res["cer"]
    _REPORT["degraded_wer"] = res["wer"]
    _REPORT["touching_cer"] = rest["cer"]
    _REPORT["touching_wer"] = rest["wer"]
    _REPORT["gen3_cer"] = res3["cer"]
    _REPORT["gen3_wer"] = res3["wer"]
    _REPORT["degraded_domain_err"] = dom
    _REPORT["degraded_confusion_offdiag"] = int(M.sum() - np.trace(M))
    doc.close()


# --------------------------------------------------------------------------- #
#  3b. P5 unit fixtures — the merge move + determinism                         #
# --------------------------------------------------------------------------- #

def test_p5_units():
    # (a) broken-glyph MERGE: snap the H of SHAFT with a 2-px white column
    # (two tall non-x-overlapping pieces); the lattice's merge move must
    # rejoin them and the word must read whole
    from rfi_stamper.tracer import binarize, components
    g = _render_token("SHAFT HEIGHT NOTED", w=1100)
    ink = binarize.binarize(g)
    _, boxes = components.label(ink)
    boxes = sorted(boxes, key=lambda b: b.x0)
    h = boxes[1]
    cut = (h.x0 + h.x1) // 2
    g2 = g.copy()
    g2[:, cut:cut + 2] = 255
    reads = [w[4] for w in tracer.read_image(g2, dpi=300)]
    A(reads == ["SHAFT", "HEIGHT", "NOTED"],
      f"snapped H rejoined by the merge move, got {reads}")
    # (b) marks are NOT merge fodder: the clean page keeps its trailing
    # periods (MERGE_MIN_H fences a period from being swallowed) — pinned
    # by the clean == 0 bar and asserted here on a paragraph-size fixture
    doc2 = fitz.open()
    pg2 = doc2.new_page(width=400, height=120)
    pg2.insert_text((40, 70), "SEE NOTES. OK", fontname="helv", fontsize=13)
    pix2 = pg2.get_pixmap(dpi=300, colorspace=fitz.csGRAY, alpha=False)
    gp = np.frombuffer(pix2.samples, np.uint8).reshape(
        pix2.height, pix2.width).copy()
    doc2.close()
    reads2 = [w[4] for w in tracer.read_image(gp, dpi=300)]
    A(reads2 == ["SEE", "NOTES.", "OK"],
      f"trailing period survives the lattice, got {reads2}")
    # (c) determinism: two runs, identical output
    A(tracer.read_image(g2, dpi=300) == tracer.read_image(g2, dpi=300),
      "read_image is deterministic across runs")


# --------------------------------------------------------------------------- #
#  4. the harness's own metric tools are correct + deterministic              #
# --------------------------------------------------------------------------- #

def test_metric_tools():
    A(tracer_eval.cer("ABC", "ABC") == 0.0, "identical strings CER 0")
    A(abs(tracer_eval.cer("ABCD", "ABXD") - 0.25) < 1e-9, "one sub in four = 0.25")
    A(abs(tracer_eval.wer("A B C", "A X C") - 1 / 3) < 1e-9, "one word error / 3")

    # align_pairs backtraces a match/sub/insert/delete correctly
    p = tracer_eval.align_pairs("S-100", "S-1O0")
    A(("0", "O") in p, f"align pairs the confused 0/O, got {p}")
    A(tracer_eval.align_pairs("AB", "AB") == [("A", "A"), ("B", "B")],
      "identical strings align 1:1")
    ins = tracer_eval.align_pairs("AC", "ABC")
    A(("", "B") in ins, f"an inserted char pairs with a gap, got {ins}")
    dele = tracer_eval.align_pairs("ABC", "AC")
    A(("B", "") in dele, f"a deleted char pairs with a gap, got {dele}")

    # confusion + domain_error over aligned pairs (gaps are ignored, in CHARSET)
    M = tracer_eval.confusion(tracer_eval.align_pairs("O", "0"))
    A(M[CHARSET.index("O"), CHARSET.index("0")] == 1, "confusion counts O→0")
    dig = tracer_eval.domain_error([("8", "6"), ("E", "E"), ("A", "A")])
    let = tracer_eval.domain_error([("8", "8"), ("E", "F"), ("A", "A")])
    A(dig > let, f"a wrong digit outweighs a wrong letter: {dig:.3f} vs {let:.3f}")


# --------------------------------------------------------------------------- #

def main():
    tests = [
        (test_clean_cer_bar, "clean auto-labeled real set CER == 0 (P5 bar)"),
        (test_sheet_field_accuracy, "sheet-number field accuracy ≥ 99% (index)"),
        (test_degraded_tier,
         "degraded tiers: speckle ≤ 2% + touching ≤ 2% + gen-3 tracked"),
        (test_p5_units, "P5 units: merge move, mark fence, determinism"),
        (test_metric_tools, "CER/WER/align/confusion/domain tools correct"),
    ]
    for fn, label in tests:
        fn()
        print(f"PASS {label}")

    print("\n--- Tracer accuracy report (measured, deterministic) ---")
    print(f"  clean:    CER {_REPORT['clean_cer']*100:5.2f}%   "
          f"WER {_REPORT['clean_wer']*100:5.2f}%   "
          f"n={_REPORT['clean_n']}  "
          f"domain {_REPORT['clean_domain_err']*100:.2f}%  "
          f"confusion off-diag={_REPORT['clean_confusion_offdiag']}")
    print(f"  sheets:   field acc {_REPORT['sheet_field_acc']*100:6.2f}%   "
          f"(raw {_REPORT['sheet_raw_acc']*100:.2f}% → index cross-check)  "
          f"n={_REPORT['sheet_n']}")
    print(f"  speckle:  CER {_REPORT['degraded_cer']*100:5.2f}%   "
          f"WER {_REPORT['degraded_wer']*100:5.2f}%   "
          f"(thin-glyph robustness guard — was 11.39% before the glyph-height fix)")
    print(f"  touching: CER {_REPORT['touching_cer']*100:5.2f}%   "
          f"WER {_REPORT['touching_wer']*100:5.2f}%   "
          f"(was 3.38% / a constant 100% before the P5 lattice)")
    print(f"  gen-3:    CER {_REPORT['gen3_cer']*100:5.2f}%   "
          f"WER {_REPORT['gen3_wer']*100:5.2f}%   "
          f"domain {_REPORT['degraded_domain_err']*100:.2f}%  "
          f"(the honest hard residual, OCR_PLAN §8 — a lower bound, "
          f"not a promise)")
    print(f"TRACER P5 EVAL TEST PASSED  ({_N[0]} checks)  — the Tracer, Phase P5")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("TRACER P5 EVAL TEST FAILED:", e)
        sys.exit(1)
