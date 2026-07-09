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
* **a documented degraded tier** — a blurred + noised photocopy of the same page
  is scored and its CER asserted under a LOOSE ceiling (≤ 15 %), so the honest
  degraded number is tracked in the suite, never a hard fail.  WER is reported.
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
    A(res["cer"] <= 0.02,
      f"clean auto-labeled CER ≤ 2% (parity bar), got {res['cer']:.4f}")

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
    for s in _SHEETS:
        truth = canon(*SHEET_TOKEN.search(s).groups())
        for sev, sd in conds:
            if sev == 0:
                g = _render_token(s)
            else:
                g = _degrade(_render_token(s),
                             0.6 if sev == 1 else 0.9, 6 if sev == 1 else 12,
                             hash((s, sd)) % 99999)
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
#  3. documented degraded tier — CER under a LOOSE ceiling (tracked, not hard) #
# --------------------------------------------------------------------------- #

def test_degraded_tier():
    doc, pg = _paragraph_page()
    gray, truth = tracer_eval.auto_label_set([pg], dpi=300)[0]
    # a light-photocopy degradation of the very same page (small 13-pt text)
    dg = _degrade(gray, blur_sigma=0.7, noise_sigma=8, seed=2)
    res = tracer_eval.score_page(dg, truth, dpi=300)

    # LOOSE ceiling: the honest degraded number is tracked in the suite, never a
    # hard fail — degraded-photocopy CER is the real risk per OCR_PLAN §8.
    A(res["cer"] <= 0.15,
      f"degraded-photocopy CER under the loose ceiling, got {res['cer']:.4f}")

    A(res["cer"] > 0.0,
      "the degraded tier genuinely degrades (has errors to track)")
    apairs = tracer_eval.align_pairs(res["ref"], res["hyp"])
    M = tracer_eval.confusion(apairs)
    A(M.shape == (len(CHARSET), len(CHARSET)), "degraded confusion matrix 43×43")
    # off-diagonal substitutions may be 0: degraded errors are segmentation-
    # dominated (insertions/deletions), OCR_PLAN §8's "segmentation is where
    # accuracy bleeds" — the domain metric still charges every mismatch.
    dom = tracer_eval.domain_error(apairs)
    A(dom > 0.0, "the degraded tier's domain error is non-zero")

    _REPORT["degraded_cer"] = res["cer"]
    _REPORT["degraded_wer"] = res["wer"]
    _REPORT["degraded_domain_err"] = dom
    _REPORT["degraded_confusion_offdiag"] = int(M.sum() - np.trace(M))
    doc.close()


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
        (test_clean_cer_bar, "clean auto-labeled real set CER ≤ 2% (parity)"),
        (test_sheet_field_accuracy, "sheet-number field accuracy ≥ 99% (index)"),
        (test_degraded_tier, "documented degraded tier CER ≤ 15% (tracked)"),
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
    print(f"  degraded: CER {_REPORT['degraded_cer']*100:5.2f}%   "
          f"WER {_REPORT['degraded_wer']*100:5.2f}%   "
          f"domain {_REPORT['degraded_domain_err']*100:.2f}%  "
          f"confusion off-diag={_REPORT['degraded_confusion_offdiag']}  "
          f"(honest photocopy residual)")
    print(f"TRACER P4 EVAL TEST PASSED  ({_N[0]} checks)  — the Tracer, Phase P4")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("TRACER P4 EVAL TEST FAILED:", e)
        sys.exit(1)
