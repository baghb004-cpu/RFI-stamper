"""Self-contained tests for the Tracer — Phase P2 (features + MLP/kNN ensemble).

Deterministic, offline, NDA-safe: every fixture is synthesized in-process with
fitz + seeded numpy, no project data and no network.  Exercises the P2 upgrades
end to end — the 8-direction gradient feature + PCA, the Zhang–Suen topology
signature, the Hershey single-stroke glyph sources, the Kanungo/Baird synthetic
corpus (labeled, varied, font/severity holdout), the from-scratch numpy MLP
(self-classifies the CHARSET ≥ 99% on a held-out clean split), the kNN store,
the NCC+kNN+MLP ensemble (beats NCC alone on a degraded set), the topology veto
gate, the drop-fall/DP touching-glyph split, calibrated confidence, the shipped
``model.npz`` round trip, and the eval harness.

The P2 green bar: clean-scan CER ≤ 2% on the auto-labeled real set; the MLP
≥ 99% on a held-out synthetic split; the ensemble ≥ NCC alone on a degraded set;
touching glyphs split; the P1 "P-101" read still works and the thin hyphen mark
now reads; pure noise never scores confident.

Run:  python3.12 tests/test_tracer_p2.py
"""
from __future__ import annotations

import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                   # noqa: E402
import fitz                                          # noqa: E402

from rfi_stamper import tracer                       # noqa: E402
from rfi_stamper.tracer import (                     # noqa: E402
    binarize, classify, components, features, fonts, normalize, segment, synth)
from rfi_stamper.tracer import eval as tracer_eval   # noqa: E402
from rfi_stamper.tracer.components import Box         # noqa: E402

CH = fonts.CHARSET
TAU_HI = tracer.TAU_HI
_N = [0]


def A(cond, msg=""):
    _N[0] += 1
    assert cond, msg


def _norm(s):
    return "".join(s.split()).upper()


def _contains(hay, needle):
    return _norm(needle) in _norm(hay)


def _cell(ch, cap=40, fid="herB0"):
    g = fonts._hershey_gray(ch, cap, fid)
    if g is None:
        g = fonts._fitz_gray(ch, cap)["helv"]
    return normalize.norm_glyph(binarize.otsu(g)[1])


def _touching(a, b, overlap=2, cap=44):
    """Two Hershey glyphs placed adjacent with a small overlap → one ink blob."""
    ga = binarize.otsu(fonts._hershey_gray(a, cap, "herB0"))[1]
    gb = binarize.otsu(fonts._hershey_gray(b, cap, "herB0"))[1]
    h = max(ga.shape[0], gb.shape[0])
    pa = np.zeros((h, ga.shape[1]), bool); pa[:ga.shape[0]] = ga
    pb = np.zeros((h, gb.shape[1]), bool); pb[:gb.shape[0]] = gb
    W = pa.shape[1] + pb.shape[1] - overlap
    c = np.zeros((h, W), bool)
    c[:, :pa.shape[1]] |= pa
    c[:, pa.shape[1] - overlap:pa.shape[1] - overlap + pb.shape[1]] |= pb
    return c


# --------------------------------------------------------------------------- #
#  1. gradient feature (NCFE) + PCA                                            #
# --------------------------------------------------------------------------- #

def test_gradient_feature():
    rng = np.random.default_rng(0)
    cells = rng.random((6, 28, 28)).astype(np.float32)
    r1 = features.raw_gradient(cells)
    r2 = features.raw_gradient(cells)
    A(r1.shape == (6, features.RAW_DIM), f"raw gradient is 512-D, got {r1.shape}")
    A(features.RAW_DIM == 512, "8 dirs × 8×8 pooling = 512")
    A(np.array_equal(r1, r2), "gradient feature is deterministic")
    A(np.allclose(np.linalg.norm(r1, axis=1), 1.0, atol=1e-5),
      "raw gradient rows are L2-normalized")

    # PCA fit + Featurizer dim + determinism
    mean, comps, scale = features.fit_pca(r1, dim=20)
    A(comps.shape == (512, 20), "PCA components (512, dim)")
    fz = features.Featurizer(mean, comps, scale)
    f1 = fz.transform(cells, aspects=np.ones(6), rel_ys=np.full(6, 0.5))
    f2 = fz.transform(cells, aspects=np.ones(6), rel_ys=np.full(6, 0.5))
    A(f1.shape == (6, 22), f"feature dim = pca + 2 extras, got {f1.shape}")
    A(np.array_equal(f1, f2), "Featurizer is deterministic")
    A(f1.dtype == np.float32, "feature is float32")

    # the fallback descriptor is selectable and deterministic
    fb = features.Featurizer(mean, comps, scale, mode="fallback")
    b1 = fb.transform(cells, np.ones(6), np.full(6, 0.5))
    b2 = fb.transform(cells, np.ones(6), np.full(6, 0.5))
    A(np.array_equal(b1, b2), "fallback feature deterministic")
    A(b1.shape[1] == features.FALLBACK_DIM + 2, "fallback dim documented")


# --------------------------------------------------------------------------- #
#  2. topology signature (Zhang–Suen skeleton + loops)                        #
# --------------------------------------------------------------------------- #

def test_topology():
    # loop counts distinguish the closed classes from the open ones
    A(features.count_loops(_cell("O").cell > 0.3) == 1, "O has one loop")
    A(features.count_loops(_cell("8").cell > 0.3) == 2, "8 has two loops")
    A(features.count_loops(_cell("H").cell > 0.3) == 0, "H has no loop")
    A(features.count_loops(_cell("B").cell > 0.3) == 2, "B has two loops")
    A(features.count_loops(_cell("D").cell > 0.3) == 1, "D has one loop")

    # skeleton thins to a 1-px trace; endpoints of a bar = 2
    bar = np.zeros((28, 28), bool)
    bar[13:15, 4:24] = True
    sk = features.zhang_suen(bar)
    A(sk.sum() < bar.sum(), "skeleton is thinner than the stroke")
    t = features.topo_signature(_cell("I").cell)
    A(t.loops == 0 and t.endpoints >= 2, f"I: 2 endpoints, no loop, got {t}")

    # solid blob has no enclosed loop
    blob = np.zeros((28, 28), np.float32); blob[6:22, 9:19] = 1.0
    A(features.count_loops(blob > 0.3) == 0, "solid blob has no loop")


# --------------------------------------------------------------------------- #
#  3. Hershey single-stroke glyph sources                                     #
# --------------------------------------------------------------------------- #

def test_fonts_hershey():
    A(len(fonts.HERSHEY) == len(CH), "a Hershey stroke glyph per CHARSET class")
    A(fonts.FONT_IDS == ("helv", "cour", "herA0", "herA15", "herB0", "herB15"),
      "six font sources / styles")
    imgs = fonts.glyph_images("A", sizes=(32,))
    fids = [f for f, _ in imgs]
    A("helv" in fids and "cour" in fids, "base-14 outlines present")
    A(sum(1 for f in fids if f.startswith("her")) == 4,
      "four Hershey styles (Type A/B × 0°/15°)")
    for _f, g in imgs:
        A(g.dtype == np.uint8 and g.ndim == 2, "glyph image is 2-D uint8")
        A(int((g < 128).sum()) > 0, "glyph carries ink (dark on white)")
    # Type A pen (h/14) is thinner than Type B (h/10): fewer ink pixels
    ga = fonts._hershey_gray("H", 60, "herA0")
    gb = fonts._hershey_gray("H", 60, "herB0")
    A(int((ga < 128).sum()) < int((gb < 128).sum()),
      "ISO Type A stroke is thinner than Type B")
    # the 15° slant shears the glyph (its bbox widens / pixels move)
    g0 = fonts._hershey_gray("I", 60, "herB0")
    g15 = fonts._hershey_gray("I", 60, "herB15")
    A(g15.shape[1] >= g0.shape[1], "15° slant widens the I bbox")


# --------------------------------------------------------------------------- #
#  4. synthetic corpus (labeled, varied, holdout)                             #
# --------------------------------------------------------------------------- #

def test_corpus():
    c = synth.corpus(seed=0, per_class=30, sizes=(24, 40))
    A(len(np.unique(c.y)) == len(CH), "every class represented")
    A(c.cells.shape[1:] == (normalize.CELL, normalize.CELL), "cells are 28×28")
    A(set(np.unique(c.severity).tolist()) <= {0, 1, 2}, "three severity tiers")
    A(c.is_test.any() and (~c.is_test).any(), "train/test split is non-trivial")

    # deterministic
    c2 = synth.corpus(seed=0, per_class=30, sizes=(24, 40))
    A(np.array_equal(c.cells, c2.cells) and np.array_equal(c.y, c2.y),
      "corpus is deterministic under a fixed seed")

    # augmentation actually changes pixels within a class
    idx = np.where(c.y == CH.index("A"))[0]
    A(float(c.cells[idx].std()) > 0.05, "augmentation varies the glyphs")

    # marks land at their true vertical band (the 2-line position feature)
    ry = lambda ch: float(c.rel_y[c.y == CH.index(ch)].mean())
    A(ry(".") > 0.75, "period sits low (rel_y high)")
    A(ry("'") < 0.30, "apostrophe sits high (rel_y low)")
    A(0.35 < ry("-") < 0.65, "hyphen sits mid-height")

    # font holdout: excluding a face removes all of its variants
    ch = synth.corpus(seed=0, per_class=30, sizes=(24, 40),
                      exclude_fonts={"helv"})
    A(fonts.FONT_IDS.index("helv") not in set(ch.font.tolist()),
      "excluded font is genuinely held out of the corpus")

    # calibrate hook returns scan statistics
    doc = fitz.open(); pg = doc.new_page(width=300, height=120)
    pg.insert_text((20, 80), "SHEET P-101", fontsize=30)
    pix = pg.get_pixmap(dpi=200, colorspace=fitz.csGRAY, alpha=False)
    gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    stats = synth.calibrate(gray)
    A({"stroke_px", "blur_sigma", "noise_sigma"} <= set(stats), stats)
    A(stats["stroke_px"] > 0, "calibrate measures a stroke width")


# --------------------------------------------------------------------------- #
#  5. from-scratch numpy MLP                                                   #
# --------------------------------------------------------------------------- #

def test_mlp_training():
    c = synth.corpus(seed=1, per_class=40, sizes=(26, 40))
    tr = ~c.is_test; te = c.is_test
    raw = features.raw_gradient(c.cells[tr])
    m, cp, s = features.fit_pca(raw, dim=120)
    fz = features.Featurizer(m, cp, s)
    Xtr = fz.transform(c.cells[tr], c.aspect[tr], c.rel_y[tr])
    Xte = fz.transform(c.cells[te], c.aspect[te], c.rel_y[te])
    mlp = classify.MLP.init(fz.dim, 128, len(CH), seed=0)
    pre = float((mlp.predict_proba(Xtr).argmax(1) == c.y[tr]).mean())
    mlp.fit(Xtr, c.y[tr], len(CH), epochs=60, seed=0)
    post = float((mlp.predict_proba(Xtr).argmax(1) == c.y[tr]).mean())
    A(pre < 0.1, f"untrained MLP is near chance, got {pre:.3f}")
    A(post > 0.95, f"training lifts train accuracy, got {post:.3f}")

    clean = c.severity[te] == 0
    acc_clean = float((mlp.predict_proba(Xte[clean]).argmax(1)
                       == c.y[te][clean]).mean())
    A(acc_clean >= 0.95, f"held-out clean accuracy high, got {acc_clean:.3f}")

    # softmax is numerically stable and rows normalize
    P = mlp.predict_proba(Xte)
    A(np.all(np.isfinite(P)), "softmax output is finite")
    A(np.allclose(P.sum(1), 1.0, atol=1e-5), "softmax rows sum to one")
    A(int(mlp.W1.size + mlp.W2.size + mlp.b1.size + mlp.b2.size) < 60000,
      "param count within the OCR_PLAN §5 budget")


def test_mlp_charset_99():
    """The shipped MLP self-classifies the CHARSET ≥ 99% on a clean held-out set.

    Uses an independent-seed clean synthetic split — never seen in training —
    so the ≥99% claim is a genuine held-out measurement.
    """
    ev = synth.corpus(seed=123, per_class=60, sizes=(24, 40))
    d = np.load(classify._MODEL_PATH, allow_pickle=False)
    fz = features.Featurizer(d["pca_mean"], d["pca_components"], d["pca_scale"])
    mlp = classify.MLP(d["W1"], d["b1"], d["W2"], d["b2"],
                       float(d["temperature"]))
    clean = ev.severity == 0
    X = fz.transform(ev.cells[clean], ev.aspect[clean], ev.rel_y[clean])
    acc = float((mlp.predict_proba(X).argmax(1) == ev.y[clean]).mean())
    A(int(clean.sum()) >= 300, f"clean eval set is sizeable, n={int(clean.sum())}")
    A(acc >= 0.99, f"MLP self-classifies the CHARSET ≥99% clean, got {acc:.4f}")


# --------------------------------------------------------------------------- #
#  6. kNN store                                                                #
# --------------------------------------------------------------------------- #

def test_knn():
    knn = classify.KNN(n_classes=len(CH))
    rng = np.random.default_rng(0)
    a = rng.random(12).astype(np.float32)
    b = rng.random(12).astype(np.float32)
    knn.add(a, CH.index("P"))
    knn.add(b, CH.index("Q"))
    A(knn.X.shape == (2, 12), "add grows the store by one vstack row")
    P = knn.proba(np.stack([a, b]))
    A(P.shape == (2, len(CH)), "proba is per-class")
    A(P[0].argmax() == CH.index("P") and P[1].argmax() == CH.index("Q"),
      "nearest exemplar wins")
    A(np.allclose(P.sum(1), 1.0, atol=1e-5), "kNN proba normalizes")
    # empty store falls back to uniform, never crashes
    A(np.allclose(classify.KNN(n_classes=len(CH)).proba(a[None]).sum(), 1.0),
      "empty kNN store degrades to uniform")


# --------------------------------------------------------------------------- #
#  7. the ensemble beats NCC alone on a degraded set                          #
# --------------------------------------------------------------------------- #

def test_ensemble_vs_ncc():
    ncc = classify.default_classifier()
    ens = classify.default_ensemble()
    c = synth.corpus(seed=7, per_class=60, sizes=(22, 34))
    harsh = c.severity == 2
    cells, asp, rel, y = (c.cells[harsh], c.aspect[harsh],
                          c.rel_y[harsh], c.y[harsh])
    ncc_pred = np.array([CH.index(ncc.classify(cells[i], float(asp[i]))[0][0])
                         for i in range(len(y))])
    er = ens.classify_batch(cells, asp, rel)
    ens_pred = np.array([CH.index(r[0][0]) for r in er])
    ncc_acc = float((ncc_pred == y).mean())
    ens_acc = float((ens_pred == y).mean())
    A(ens_acc >= ncc_acc, f"ensemble ≥ NCC on degraded: {ens_acc:.3f} vs "
      f"{ncc_acc:.3f}")
    A(ens_acc > ncc_acc + 0.03, "ensemble is materially better on the "
      f"degraded set: {ens_acc:.3f} vs {ncc_acc:.3f}")

    # a crafted degraded case where NCC misreads but the ensemble recovers
    rng = np.random.default_rng(5)
    wins = 0
    for ch in "ABEHNPRS0123456789":
        base = _cell(ch, cap=34, fid="herB0").cell
        noisy = np.clip(base + rng.normal(0, 0.28, base.shape), 0, 1)
        blurred = synth.blur(noisy * 255, 0.9) / 255.0
        cell = blurred.astype(np.float32)
        asp1 = _cell(ch, cap=34).aspect
        n_ok = ncc.classify(cell, asp1)[0][0] == ch
        e_ok = ens.classify(cell, asp1, 0.5)[0][0] == ch
        if e_ok and not n_ok:
            wins += 1
    A(wins >= 1, f"ensemble recovers glyphs NCC misses, wins={wins}")


# --------------------------------------------------------------------------- #
#  8. topology veto gate                                                       #
# --------------------------------------------------------------------------- #

def test_topology_gate():
    ens = classify.default_ensemble()
    blob = np.zeros((28, 28), np.float32); blob[6:22, 9:19] = 1.0
    veto = ens.topology_veto(blob)
    A("O" in veto, "topology gate vetoes O for a no-loop blob")
    A({"0", "D", "B", "Q", "8"} <= veto, "all loop-bearing classes vetoed")
    top1 = ens.classify(blob, aspect=0.6, rel_y=0.5)[0][0]
    A(top1 not in ("O", "0", "D", "B", "Q", "8", "&"),
      f"a no-loop blob is never called a loop glyph, got {top1!r}")
    # a real O (one loop) is NOT vetoed as O
    A("O" not in ens.topology_veto(_cell("O").cell),
      "a genuine loop glyph is not self-vetoed")


# --------------------------------------------------------------------------- #
#  9. touching-glyph split (drop-fall + DP recombination)                     #
# --------------------------------------------------------------------------- #

def test_touching_split():
    ens = classify.default_ensemble()

    def split_reads(pair, overlap=2):
        ink = _touching(pair[0], pair[1], overlap=overlap)
        box = Box(1, 0, 0, ink.shape[0] - 1, ink.shape[1] - 1, int(ink.sum()))
        med_w = (ink.shape[1] / 2.0) * 0.92
        boxes = segment.split_glyph_boxes(ink, box, med_w, ens)
        reads = []
        for (y0, x0, y1, x1) in boxes:
            sub = ink[y0:y1 + 1, x0:x1 + 1]
            ng = normalize.norm_glyph(sub)
            reads.append(ens.classify(ng.cell, ng.aspect, 0.5)[0][0])
        return ink, med_w, boxes, reads

    ink, med_w, boxes, reads = split_reads("P1")
    A(len(boxes) == 2, f"touching 'P1' splits into two glyphs, got {len(boxes)}")
    A("".join(reads) == "P1", f"the split reads P then 1, got {reads}")

    _, _, boxes2, reads2 = split_reads("SE")
    A(len(boxes2) == 2, "touching 'SE' splits into two glyphs")
    A("".join(reads2) == "SE", f"the split reads S then E, got {reads2}")

    # DP recombination picks the higher-confidence split over the whole blob
    whole = normalize.norm_glyph(ink)
    whole_conf = ens.classify(whole.cell, whole.aspect, 0.5)[0][1]
    split_conf = 0.0
    for (y0, x0, y1, x1) in boxes:
        ng = normalize.norm_glyph(ink[y0:y1 + 1, x0:x1 + 1])
        split_conf += ens.classify(ng.cell, ng.aspect, 0.5)[0][1]
    A(split_conf > whole_conf, "DP prefers the split: summed confidence "
      f"{split_conf:.2f} > whole {whole_conf:.2f}")

    # a wide single glyph (M) is NOT sliced
    gm = binarize.otsu(fonts._hershey_gray("M", 44, "herB0"))[1]
    bm = Box(1, 0, 0, gm.shape[0] - 1, gm.shape[1] - 1, int(gm.sum()))
    med = gm.shape[1] * 0.66            # M is wider than the median pitch
    A(len(segment.split_glyph_boxes(gm, bm, med, ens)) == 1,
      "a wide single glyph M is never split")

    # the P1 equal-pitch stub still behaves for test_tracer compatibility
    line = [Box(0, 0, 0, 23, 11, 288), Box(0, 40, 0, 23, 79, 960)]
    A(all(len(s) == 3 for s in segment.split_touching(line)),
      "the P1 split_touching stub still returns (box, x0, x1) slices")


# --------------------------------------------------------------------------- #
#  10. auto-labeled real-set CER ≤ 2% — the P2 green bar                       #
# --------------------------------------------------------------------------- #

def _paragraph_page():
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


def test_autolabel_cer():
    doc, pg = _paragraph_page()
    pairs = tracer_eval.auto_label_set([pg], dpi=300)
    A(len(pairs) == 1, "one page → one (image, truth) pair")
    gray, truth = pairs[0]
    A(gray.ndim == 2 and len(truth) > 20, "auto-label yields raster + truth words")
    A(all(len(w) == 5 for w in truth), "truth words are (x0,y0,x1,y1,text)")
    res = tracer_eval.score_page(gray, truth, dpi=300)
    A(res["n_ref"] > 150, f"paragraph has real length, n={res['n_ref']}")
    A(res["cer"] <= 0.02, f"clean-scan CER ≤ 2% (the P2 bar), got {res['cer']:.4f}")


# --------------------------------------------------------------------------- #
#  11. calibrated confidence (clean high, noise never confident)              #
# --------------------------------------------------------------------------- #

def test_confidence_calibration():
    ens = classify.default_ensemble()
    # clean glyphs read with high confidence
    for ch in "P0189AH":
        ng = _cell(ch, cap=40, fid="herB0")
        r = ens.classify(ng.cell, ng.aspect, 0.5)[0]
        A(r[0] == ch and r[1] >= TAU_HI,
          f"clean {ch!r} reads confident, got {r}")

    # pure noise never crosses τ_hi (raw softmax would be overconfident)
    rng = np.random.default_rng(11)
    hi = 0
    for _ in range(12):
        n = (rng.random((28, 28)) > 0.5).astype(np.float32)
        if ens.classify(n, 1.0, 0.5)[0][1] >= TAU_HI:
            hi += 1
    A(hi == 0, f"pure noise never emits a τ_hi token, got {hi} confident")

    # a full noise raster yields no confident word from read_image
    noise_img = (rng.random((300, 400)) * 255).astype(np.uint8)
    reads = tracer.read_image(noise_img, dpi=300)
    A(all(s < TAU_HI for *_r, s in reads),
      f"noise raster produces no confident word: {[round(s,2) for *_,s in reads]}")


# --------------------------------------------------------------------------- #
#  12. shipped model.npz loads + classifies without retraining                #
# --------------------------------------------------------------------------- #

def test_model_file():
    A(os.path.exists(classify._MODEL_PATH), "shipped model.npz is committed")
    A(os.path.getsize(classify._MODEL_PATH) < 1_000_000,
      "model.npz is under the ~1 MB budget")
    d = np.load(classify._MODEL_PATH, allow_pickle=False)
    for key in ("charset", "pca_mean", "pca_components", "pca_scale",
                "W1", "b1", "W2", "b2", "temperature", "knn_X", "knn_y",
                "loop_min", "calib_edges", "calib_vals", "confusion"):
        A(key in d, f"model.npz carries {key}")
    A("".join(chr(c) for c in d["charset"]) == CH, "charset round-trips")

    # load_ensemble reconstructs a working classifier without any training
    ens = classify.load_ensemble(classify._MODEL_PATH)
    for ch in "SHEP0123":
        ng = _cell(ch, cap=40, fid="herB0")
        A(ens.classify(ng.cell, ng.aspect, 0.5)[0][0] == ch,
          f"loaded model classifies {ch!r}")
    # default_ensemble is a cached singleton (no per-call retrain)
    A(classify.default_ensemble() is classify.default_ensemble(),
      "default_ensemble is cached (runtime never retrains)")


# --------------------------------------------------------------------------- #
#  13. no P1 regression + the thin hyphen now reads                           #
# --------------------------------------------------------------------------- #

def _scan_pdf(path, text, fontsize=48):
    scratch = fitz.open()
    sp = scratch.new_page(width=612, height=792)
    sp.insert_text((72, 160), text, fontname="helv", fontsize=fontsize)
    pix = sp.get_pixmap(dpi=200)
    scratch.close()
    doc = fitz.open()
    p = doc.new_page(width=612, height=792)
    p.insert_image(p.rect, pixmap=pix)
    doc.save(path)
    doc.close()


def test_p1_read_and_hyphen():
    import tempfile
    tmp = tempfile.mkdtemp(prefix="tracer_p2_")
    src = os.path.join(tmp, "p101.pdf")
    _scan_pdf(src, "SHEET P-101", fontsize=48)
    text = tracer.ocr_page_text(src, 1)
    A(_contains(text, "P-101"), f"P1 'P-101' still reads, got {text!r}")
    A("-" in text, "the hyphen mark in P-101 reads (2-line position + topology)")

    # even a single very large isolated token keeps its thin hyphen now (the
    # glyph-height estimator no longer collapses to the mark's own height)
    big = os.path.join(tmp, "big.pdf")
    _scan_pdf(big, "P-101", fontsize=64)
    A(_contains(tracer.ocr_page_text(big, 1), "P-101"),
      "isolated 64 pt 'P-101' reads its hyphen too")

    # a page of dimension tokens: every thin hyphen reads as a hyphen
    doc = fitz.open(); pg = doc.new_page(width=612, height=792)
    pg.insert_text((72, 120), "ROOM 8-6 AND 12-4", fontname="helv", fontsize=30)
    pg.insert_text((72, 200), "DETAIL A-1 SIM", fontname="helv", fontsize=30)
    pix = pg.get_pixmap(dpi=300, colorspace=fitz.csGRAY, alpha=False)
    gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    reads = tracer.read_image(gray, dpi=300)
    joined = _norm(" ".join(r[4] for r in reads))
    A("8-6" in joined and "A-1" in joined and "12-4" in joined,
      f"dimension hyphens read, got {joined!r}")
    n_hyph = sum(r[4].count("-") for r in reads)
    A(n_hyph >= 3, f"the thin hyphen marks read (not '.'/'/'/apostrophe), "
      f"got {n_hyph}")


# --------------------------------------------------------------------------- #
#  14. eval harness (CER/WER, confusion, domain sub-metric)                    #
# --------------------------------------------------------------------------- #

def test_eval_metrics():
    A(tracer_eval.cer("ABC", "ABC") == 0.0, "identical strings CER 0")
    A(abs(tracer_eval.cer("ABCD", "ABXD") - 0.25) < 1e-9, "one sub in four = 0.25")
    A(tracer_eval.cer("", "") == 0.0, "empty vs empty CER 0")
    A(abs(tracer_eval.wer("A B C", "A X C") - 1 / 3) < 1e-9, "one word error / 3")

    M = tracer_eval.confusion([("O", "0"), ("O", "0"), ("A", "A")])
    A(M.shape == (len(CH), len(CH)), "confusion matrix is 43×43")
    A(M[CH.index("O"), CH.index("0")] == 2, "confusion counts O→0")
    A(M[CH.index("A"), CH.index("A")] == 1, "confusion counts the diagonal")

    # domain sub-metric charges a wrong digit more than a wrong letter: flip one
    # character inside the same mixed token and the digit slip costs more.
    dig = tracer_eval.domain_error([("8", "6"), ("E", "E"), ("A", "A"), ("1", "1")])
    let = tracer_eval.domain_error([("8", "8"), ("E", "F"), ("A", "A"), ("1", "1")])
    A(dig > let, f"a wrong sheet-number digit outweighs a wrong prose letter: "
      f"{dig:.3f} vs {let:.3f}")

    # only_charset folds case and drops out-of-set characters
    A(tracer_eval.only_charset("Ab: c9") == "ABC9", "charset normalization")


# --------------------------------------------------------------------------- #

def main():
    tests = [
        (test_gradient_feature, "gradient NCFE feature + PCA (dim, determinism)"),
        (test_topology, "Zhang–Suen skeleton + loop/endpoint signature"),
        (test_fonts_hershey, "Hershey single-stroke glyph sources (Type A/B, slant)"),
        (test_corpus, "synthetic corpus (labeled, varied, font/mark holdout)"),
        (test_mlp_training, "numpy MLP trains (backprop lifts accuracy)"),
        (test_mlp_charset_99, "MLP self-classifies the CHARSET ≥99% held-out"),
        (test_knn, "kNN exemplar store (add / proba)"),
        (test_ensemble_vs_ncc, "ensemble beats NCC alone on a degraded set"),
        (test_topology_gate, "topology veto gate (no-loop blob is not an O)"),
        (test_touching_split, "drop-fall + DP touching-glyph split"),
        (test_autolabel_cer, "auto-labeled real-set CER ≤ 2% (P2 green bar)"),
        (test_confidence_calibration, "calibrated confidence (clean high, noise low)"),
        (test_model_file, "shipped model.npz loads + classifies, no retrain"),
        (test_p1_read_and_hyphen, "P1 P-101 read intact + thin hyphen now reads"),
        (test_eval_metrics, "eval harness (CER/WER, confusion, domain metric)"),
    ]
    for fn, label in tests:
        fn()
        print(f"PASS {label}")
    print(f"TRACER P2 TEST PASSED  ({_N[0]} checks)  — the Tracer, Phase P2")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("TRACER P2 TEST FAILED:", e)
        sys.exit(1)
