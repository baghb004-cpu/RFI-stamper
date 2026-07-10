# OCR_PLAN.md — the Tracer: a from-scratch OCR engine to retire Tesseract

**Goal:** remove Planloom's last external binary dependency (Tesseract) and
replace it with a from-scratch engine in pure **Python + numpy + PyMuPDF
(fitz)** — no new dependencies, no pretrained models, no cloud, fully offline.
Working name for the engine/persona: **the Tracer** (it traces the lettering
off a scanned sheet). Distilled from an 8-agent industry-standards research
pass; every number below is sourced from that research.

> **Naming:** user-facing = *the Tracer*. Code = a drop-in `rfi_stamper/ocr/`
> package that preserves the existing `ocr.py` public API. "Tesseract" appears
> here only as the dependency being removed.

---

## 0. What's needed to start building (the checklist)

Nothing external. Everything is already in the repo's stack:

- [x] **numpy** — already a dependency (all DSP/CC/features/classifier).
- [x] **PyMuPDF (fitz)** — already a dependency (raster render + searchable
      text layer via `insert_text(render_mode=3)`).
- [x] **A drop-in target** — `rfi_stamper/ocr.py` public API is known and
      small (§6); the new `ocr/` package re-exports it so no caller changes.
- [x] **Training data with no downloads** — synthesized at build/first-run
      time from Hershey single-stroke vector fonts (public domain) + fitz
      base-14 outlines, degraded with Kanungo/Baird noise (§3). No client
      scans shipped (NDA-safe).
- [x] **Free ground truth** — vector CAD pages rasterized then scored against
      `fitz.get_text("words")` give unlimited labeled real lettering at zero
      human cost (§6 test plan).
- [x] **Existing domain assets to reuse as a language model** — `core.SHEET_TOKEN`
      + `sheets.py` sheet index, `holler.parse_dimension`/`format_ftin`,
      Heartwood trade vocabulary, `restate.py` number-lock, `layout.py`
      content-pixel rule, `stamp._viewer_to_media` rotation transform.

**Decision to make before P1:** confirm the v1 scope line — "scanned-plan
lettering in isolated-text regions (title-block sheet numbers, note/keynote
blocks, room labels, bubbles)", with dimension-line-fused and hatch-embedded
text an explicit honest SKIP. (Recommended — see §8.)

**Then build in the four staged phases of §7** (P1→P4), each shipping green,
with Tesseract removed only in P4 after the eval harness proves parity.

---

## 1. Verdict & scope

**Feasible for this narrow domain, with one clearly-bounded risk to scope
honestly.** Construction lettering is a radically narrower OCR problem than
general document OCR, and that narrowness is what makes a from-scratch numpy
engine plausibly competitive *here and only here*. Four properties collapse
the problem:

1. **Near-fixed font space** — single-stroke sans-serif technical lettering
   (ISO 3098 Type A/B, ASME Y14.2), a handful of monoline typefaces, not the
   open universe of fonts.
2. **All-uppercase convention** — removes case ambiguity, ~halves class count.
3. **Tiny character set** — ~40–60 classes (A–Z, 0–9, ~15–20 marks) vs 200+
   for general OCR.
4. **Structured, position-regular content** — sheet number in the title-block
   corner, feet-inch dimensions, room tags, grid bubbles, keynotes, left-
   aligned all-caps notes — each with a grammar and a location prior the app
   can already validate.

**Expected accuracy:** clean 300-dpi rasters → **CER ≤ 1–2% (char acc ≥
98–99%)**, within a small delta of Tesseract (~99.0–99.2% clean). On
*structured* tokens (sheet numbers, dimensions), the app's existing grammar +
sheet-index cross-checks can **exceed** Tesseract. It underperforms on the
hard cases (text fused with linework, touching/broken glyphs on 3rd-gen
photocopies, sub-legible small text, dense whole-sheet throughput) — those are
scoped as known SKIPs and confidence-routed to review, never shipped as noise.

**Scope guardrails that make this safe to ship staged:**
- **OCR targets raster/scanned pages ONLY.** Vector pages already extract
  perfectly via `fitz.get_text("words")` and are untouched. On mixed pages,
  OCR only the residual raster ink not covered by vector text.
- **This replaces an OPTIONAL feature** — today's `ocr.py` is an optional
  wrapper, so the new engine ships behind a confidence gate in phases;
  Tesseract removal is the last step.
- **Output is indistinguishable from the vector path** — results are fitz-style
  word tuples `(x0,y0,x1,y1,text,...)` in viewer page points, so `sheets.py`,
  `hyperlink.py`, and `markups/` consume OCR with zero new plumbing.

---

## 2. Pipeline (end to end, in order)

Pixel constants assume 300 dpi and are **derived from measured stroke width
per sheet** — fixed constants don't survive across scans.

1. **Render / grayscale** — `page.get_pixmap(dpi=300, colorspace=fitz.csGRAY)`;
   reshape `pix.samples`. Read `pix.xres/yres`, resample to square pixels if
   non-square. Color/cyan prints: per-channel Otsu, pick max between-class
   variance (blue channel usually best on cyanotype). RGB→gray = Rec.601
   `0.299R+0.587G+0.114B`.
2. **Polarity + DPI/x-height normalize** — decide polarity from the title-block
   corner (not the whole page). Measure cap-height via rough Otsu → CC median,
   or stroke width via ink distance-transform mode. Upscale (bilinear/bicubic
   numpy) to **x-height 20–30 px, cap-height 20–40 px, stroke 3–4 px**; never
   exceed ~30 px x-height. If x-height < ~15 px → **flag, do not
   upscale-and-pretend** (verify culture).
3. **Background/illumination normalize** (degraded only, gated by a flatness
   score) — low-freq background via large boxcar (integral image, window ≈ 8×
   stroke ≈ 30–50 px), `flat = clip(gray/(bg+1)*mean_bg)`; mask ink out of the
   background estimate first. Skip on clean rasters.
4. **Binarize** — flatness router: **flat → global Otsu** (256-bin, max
   between-class variance, 1 pass); **non-flat → local Sauvola** via two
   integral images `T = m·(1 + k·(s/R − 1))`. Variants: Wolf-Jolion
   (low-contrast), Phansalkar (faded diazo). Reuse `layout.py`'s
   `gray < 225` rule where a global threshold suffices.
5. **Deskew** — test 0/90/180/270 first, then residual skew θ∈[−15°,+15°]
   (coarse 0.5–1°, refine 0.1°) maximizing `sum(row_sum²)` on text-only
   components; drawings' orthogonal linework gives a strong skew signal.
6. **Text/linework separation** (the hard part) — (a) long-run linework
   removal (morphological open, SE 1×L and L×1, L ≈ 2× glyph width ≈ 40–50 px);
   on mixed pages subtract the app's own vector line geometry as a prior. (b)
   run-based 8-connected component labeling (two-pass union-find over RLE runs,
   numpy). (c) histogram-derived geometric filtering (stroke 3–5 px, cap 20–45
   px, fill 0.10–0.60; elongation gate = longer side > 4× glyph height AND
   elongation > 8 — never a bare aspect ratio, which deletes `I 1 l - ' " /`);
   whitelist round baseline dots.
7. **Segmentation → lines → words → chars** — lines by horizontal projection
   valleys, **2-line cap/baseline model** (uppercase); words by adaptive gap
   (Wong), grammar-routed dimension/sheet tokens skip word-splitting; chars
   CC-first, merge broken glyphs, split touching glyphs (width > 1.3× median →
   n = round(width/pitch), cuts at projection valleys refined by a drop-fall
   {down, down-left, down-right} path), over-segment + DP recombination on
   hard cases.
8. **Orientation normalize** — coarse axis from H-vs-V projection peakiness;
   resolve 90/270 + up/down by running the classifier at 4 rotations (max
   summed confidence); break 180° ambiguity (N/Z, M/W, 6/9) by picking the
   orientation yielding the **most in-lexicon tokens** across the block
   (self-supervised — generic OCR can't do this).
9. **Glyph normalize** — optional deslant (0° vs 15° hypotheses, or moment
   shear); crop to ink bbox → aspect-preserving **area-average** downsample
   (never nearest-neighbor, never stretch-to-square) → center by center-of-mass
   in a fixed cell; **fit longer side to 20×20, center in 28×28** (32/48 for
   the richer alphabet); append raw aspect ratio + baseline-relative position
   as extra feature dims.
10. **Feature extract** — primary **8-direction gradient feature (NCFE)**:
    Sobel → 8 orientation planes → 8×8 Gaussian pooling = 512-D → PCA/whiten to
    ~120–160-D. Cheap floor: direction-zoning 4×4×4 = 64-D + projections 32-D +
    profiles 64-D + crossings 16-D + structural 12-D ≈ 188-D. Structural gate
    (Zhang–Suen skeleton): endpoints/junctions/loops ≈ 8–16-D. Hu moments
    **non-invariant only** (rotation invariance harms — collapses 6/9, N/Z).
11. **Classify** — NCC template + kNN memory + small numpy MLP ensemble, argmax
    + confidence (see §3).
12. **Confidence** — per-char calibrated margin (temperature/margin-ratio, raw
    softmax is overconfident); per-word length-normalized geometric mean
    `exp(mean(log p_i))`, blended `score = α·logP_channel + (1−α)·logP_LM`,
    α ≈ 0.6.
13. **Lexicon/grammar post-correct** — route by field grammar (sheet number,
    feet-inches, room name), then noisy-channel lexicon correction with a domain
    confusion matrix + char n-gram back-off + **number-lock fail-closed** on all
    digit strings (see §4).
14. **Write searchable layer** — rebuild each OCR'd page as a NEW /Rotate 0 page
    sized `pixmap/(dpi/72)`, place the raster full-page, write one invisible run
    per word: `page.insert_text(baseline, word, render_mode=3, fontname="helv",
    fontsize≈cap_height_pts)`, anchored bottom-left, fontsize/advance scaled so
    invisible width ≈ image word width. Then `verify.py`-style pixel-diff to
    prove the raster is untouched.

---

## 3. The classifier decision

**Primary: an ensemble whose runtime workhorse is a from-scratch numpy MLP
over the 8-direction gradient feature, backed by an NCC template bank and a
kNN memory.**

- **Gradient feature + small MLP** is the proven accuracy-per-compute winner on
  machine print (~98–99%). Architecture: **144–160-D → 128–256 ReLU/tanh →
  ~40–60 classes ≈ 47k–51k params (~190 KB fp32)**, trained by hand-written
  mini-batch SGD/backprop (cross-entropy, He/Xavier init, stable softmax) in
  seconds-to-minutes on the synthetic corpus. Inference = 2 matmuls, whole
  sheet batched, sub-ms flat latency.
- **NCC template bank** (~40 classes × ~5 templates × 400 floats ≈ 320 KB) —
  the high-precision voter and the vehicle for **per-project font adaptation**:
  a scanned set is usually one firm's title-block font, so after a handful of
  human confirmations, replace class templates with mean glyphs harvested from
  *this* document → few-shot near-perfect on the rest of the set. (Tesseract
  cannot do this — the genuine structural edge.)
- **kNN store** (~12k exemplars × 144-D ≈ 6.9 MB) — the self-learning memory;
  every correction is one `np.vstack`, no retraining; condense with k-means
  past ~50k.
- **Ensemble disagreement** routes exactly the uncertain glyphs to the existing
  human mapping-review step (active learning).

**Fallback: tiny numpy CNN** (1–2 conv 8→16 filters + 2×2 pool + FC + softmax
on 32×32, ~20–60k params, im2col+matmul) — buys ≤ ~0.5–1.5% over the gradient
MLP, materially harder to implement correctly offline; escalate only if
features genuinely underperform.

**Synthetic training-data recipe (no downloads, fully offline):**
- **Three license-clean font sources:** (A) fitz base-14 outlines (Courier ≈
  uniform-stroke CAD, Helvetica ≈ modern title blocks); (B) public-domain
  **Hershey single-stroke vector fonts** (~95-glyph ASCII Gothic set) stroked
  in numpy at pen width **h/10 (Type B), h/14 (Type A)**, both 0° and 15° slant;
  (C) hand-vectorized strokes for marks missing from base-14 (Ø ° ± fractions).
  **No proprietary CAD font (*.shx) named or shipped** — describe by ISO 3098
  style only.
- **Sizes:** ~40–60 classes at cap-height 15–60 px, at 200/300/400 dpi.
- **Kanungo + Baird degradation (all numpy):** Gaussian blur σ 0.5–1.5 px;
  noise σ 5–20 gray + S&P 0.1–1%; 3×3 erode/dilate (toner); affine skew ±2–3°;
  low-freq background gradient; 8×8 DCT quantize (JPEG blocking); random
  threshold. **~100–300 variants/class → ~4k–200k exemplars.** Hold out fonts
  AND strings.
- **Calibrate augmentation to the user's real scans** — measure stroke width,
  blur, noise σ from the first rendered pages and bias the synthetic grid
  toward them (closes the synthetic-to-real gap that sinks synthetic-only OCR).

**Self-learning correction loop (two-lane, human-gated — mirrors Heartwood/the
voice recognizer):** every GUI correction records `(glyph bitmap, true char,
box)`. **Auto lane:** high-confidence, grammar-verified tokens auto-append to
the kNN store. **Human-gated lane:** corrections reviewed before they change
the shipped template bank/MLP; provenance-tagged (synthetic / auto / human),
confirmed labels outrank synthetic, promotion caps (like the thesaurus miner
and the Corral) — prevents drift/poisoning. Persist a per-firm "font profile"
sidecar keyed by producer, auto-selected when a new set matches.

---

## 4. Domain leverage

- **Narrow closed charset** — a hard geometric CC prefilter rejects
  linework/hatch with no training data; precompute each class's topological
  signature (endpoints/junctions/loops) as a **hard gate** vetoing impossible
  proposals (a proposed "O" with zero loops is rejected).
- **Uppercase** — no case ambiguity, 2-line model, vertical mark position is a
  free disambiguator.
- **Structured fields + location priors** — search the **sheet-number region
  first** (right ≤25% × bottom ≤25%): highest value, spatially isolated,
  succeeds even when dense-geometry OCR fails.
- **The app's own assets become a free language model** — reuse
  `core.SHEET_TOKEN` + `canon()/canon_loose()` against the known `sheets.py`
  index, the `GHS_LINE` MSDS guard, `zfill(3)`; reuse
  `holler.parse_dimension`/`format_ftin` for dimensions; snap alphabetic tokens
  to Heartwood KB/thesaurus/room/CSI vocabulary within Levenshtein-1. Lifts a
  raw 96–98% character engine to ~99%+ **field** accuracy; grammar-validated
  tokens auto-harvest as new exemplars.
- **Number-lock (port `restate.py`)** — digit strings NEVER dictionary-snapped;
  corrected only by the digit-restricted confusion model (0/O, 1/7/I, 5/6/8,
  2/Z) + field grammar, only when unique + high-confidence; refuse any
  correction that changes the numeric multiset. A scanned 8' can never silently
  become 6'.
- **Vector-vs-raster fusion** — vector text always wins; per page, if
  `get_text("words")` yields adequate text (existing `needs_ocr()`,
  `_MIN_CHARS=12`), skip OCR; on mixed pages, zero out pixels inside vector-text
  bboxes so OCR runs only on residual ink, then merge word tuples. Free
  supervision, no double-reading, compute only where needed.

---

## 5. Numeric constants table

| Parameter | Value |
|---|---|
| Render/OCR DPI | 300 default; 200 ok for ≥1/8" text; **150 recovery floor**; 400–600 only if cap-height < ~22 px. Never OCR at 90 dpi (verify-only). |
| Target after normalize | x-height 20–30 px, cap-height 20–40 px, stroke 3–4 px; ≤ ~30 px x-height |
| Min reliable glyph | cap-height ≥ 20 px robust, ~16 px marginal, collapses < 10 px; per-glyph gate ~14 px |
| Cap-height @300 dpi | 1/8"=37.5 px, 3/32"=28 px, 3/16"=56 px, 1/4"=75 px; ISO 2.5 mm=29.5 px, 3.5 mm=41 px |
| Cap-height @200/150 | 1/8": 25 / 18.75 px; 3/32": 18.75 / 14 px |
| ISO stroke | d = h/10 (Type B) or h/14 (Type A); char width ≈ 0.6h; slant 0° or 15°; heights 1.8/2.5/3.5/5/7/10/14/20 mm |
| Otsu | 256-bin histogram, max between-class variance, 1 pass |
| Sauvola | window 15×15 (or 2–3× stroke), k = 0.2 (0.34–0.5 stained), R = 128, 2 integral images |
| Niblack / Wolf-Jolion / Phansalkar | Niblack k=−0.2; WJ k≈0.5 global-min norm; Phansalkar k=0.25 R=0.5 p=2 q=10 on [0,1] |
| Background window | ≈ 8× stroke ≈ 30–50 px; `clip(gray/(bg+1)*mean_bg)` |
| Content-pixel rule | gray < 225 (reuse `layout.py`) or Sauvola-adaptive |
| Median denoise | 3×3 only (larger erases single-stroke lettering) |
| CC despeckle | drop area < 8–12 px @300 (scale by (dpi/300)²), min bbox side < 3 px; drop bbox > ~50% sheet; whitelist round dots |
| CC glyph gate | stroke 3–5 px, cap 20–45 px, fill 0.10–0.60, aspect 0.35–1.3 |
| CC max-size gate | reject if bbox side > ~4× median glyph height (~150–180 px) OR ink area > few× text peak |
| CC elongation gate | reject only if longer side > 4× glyph height AND elongation > 8 (protects I 1 l - ' " /) |
| Line-removal SE | 1×L and L×1, L ≈ 2× glyph width ≈ 40–50 px; delete runs > ~2–3× glyph height |
| Deskew | 0/90/180/270 first; then −15..+15° coarse 0.5–1° + refine 0.1°; maximize `sum(row_sum²)` |
| RLSA (labels) | hsv ≈ 0.8× glyph height (24–30 px), vsv ≈ 0.4× (12–15 px) — ~10× smaller than paragraph values |
| Docstrum | k=5 NN; angle ±10° around peaks (0° & 90°); link if NN dist < ~2.5× char-pitch |
| Touching-CC split | trigger width > ~1.3× median; n = round(width/pitch); valleys within ±0.3× char-width; drop-fall {down, DL, DR} |
| Glyph cell | longer side → 20×20 (or 24/32/48), aspect-preserving area-average, center-of-mass in 28×28 |
| Feature dims | gradient NCFE 512-D → PCA ~120–160-D; HOG 5×5×9=144-D; zoning 4×4=16 / 5×5=25; direction-zoning 4×4×4=64; projections 32–64; profiles 64; crossings 16; structural 8–16; Hu 7 (tie-break); raw 20×20=400 / 32×32=1024 |
| MLP | 144–160 → 128–256 → ~40–60 classes ≈ 47k–51k params |
| CNN fallback | 1–2 conv (8→16, 3×3/5×5) + 2×2 pool + FC + softmax on 32×32, ~20–60k params, ~1–5 ms/glyph |
| NCC bank | ~40 classes × ~5 templates × 400 floats ≈ 320 KB |
| kNN store | ~12k × 144-D × 4 B ≈ 6.9 MB; condense past ~50k |
| Confusion matrix | ~45×45 P(observed\|true) float32, column-renormalized; + merge/split entries |
| Edit distance | δ=1 high-confidence; δ≤2 max; cap δ < ceil(len/3); weighted costs from −log P(edit) |
| SymSpell | precompute deletes to distance 2; ~0.1 ms/query; dict few thousand terms |
| Char n-gram | 3-gram, add-k k≈0.01, ≤ ~45³≈91k cells (sparse), back-off to bi/unigram |
| Word confidence | `exp(mean(log p_i))`; blend α ≈ 0.6 |
| Confidence thresholds | auto-accept τ_hi ≈ 0.90 (+ ensemble margin + lexicon); hard-reject τ_lo ≈ 0.60; 0.60–0.90 → review; NCC auto-add ≥ 0.90 |
| Accuracy targets | clean 300 dpi CER ≤ 1–2% (measured 0.00%); speckled/noisy scan CER ~0 (v4.7.1 noise-robust glyph height); touching/broken-glyph photocopy CER 3–15% (P5 split+merge lattice measured 0.00%; the gen-3 double-weld tier ~16% is the tracked residual); sheet-number field acc ≥ 99% (measured 100%); WER ≈ 1−(1−CER)^L, L≈5 |
| Page pixel budget | ARCH-D @300 ≈ 78 MP; ARCH-E @300 ≈ 155 MP; **tile > ~100 MP** (1024–2048 px tiles + window/128 px halo) |
| Timing | rasterize < 1 s; Sauvola full ARCH-D ~0.1–0.3 s; CC (numpy union-find) 0.3–2 s (**bottleneck**); feature+classify thousands of glyphs < 0.1 s; ROI-first well under a few seconds |

---

## 6. Module & test plan

**Drop-in for `rfi_stamper/ocr.py`; new modules under `rfi_stamper/ocr/`**
(make `ocr.py` re-export the package's public API so callers are untouched).

**Public API to preserve exactly** (callers: `__main__.py`,
`gui/tab_pdftools.py`, `tests/test_ocr.py`):
- `needs_ocr(path, page_no=None, min_chars=12) -> bool`
- `ocr_pdf(path, out_path, dpi=300, language="eng", skip_text_pages=True, log=print) -> {"pages_ocred","pages_total","out_path"}`
- `ocr_page_text(path, page_no, dpi=300, language="eng") -> str`
- `tesseract_available() -> bool` (returns True; `path="builtin"`, `langs=["eng"]`)
- `tesseract_info() -> {"available","path","tessdata","langs"}`
- `class OcrUnavailable(RuntimeError)` (kept for compat; effectively never fires)
- Preserve the atomic save (`out_path+".part"` → fsync → `os.replace`,
  `garbage=3`, `deflate=True`), never mutate input, preserve page count and page
  rect within 1 pt (tests assert this).

**Package layout:**
```
rfi_stamper/ocr/__init__.py   public API facade (the preserved signatures)
rfi_stamper/ocr/render.py     fitz raster, gray, polarity, DPI/x-height normalize, tiling
rfi_stamper/ocr/binarize.py   Otsu + integral-image Sauvola/Wolf-Jolion/Phansalkar, flatness router
rfi_stamper/ocr/deskew.py     projection-profile skew + 0/90/180/270 orientation
rfi_stamper/ocr/linework.py   long-run/morphological line removal, vector-line subtraction prior
rfi_stamper/ocr/components.py run-based 8-conn union-find CC + geometric/histogram filters
rfi_stamper/ocr/segment.py    line/word/char segmentation, drop-fall split, DP recombination
rfi_stamper/ocr/normalize.py  glyph deslant/crop/scale/center (MNIST protocol)
rfi_stamper/ocr/features.py   gradient NCFE, zoning, projections, Hu, skeleton/structural
rfi_stamper/ocr/classify.py   NCC + kNN + numpy MLP ensemble, calibrated confidence
rfi_stamper/ocr/fonts.py      Hershey stroking + fitz base-14 rendering (synthetic glyphs)
rfi_stamper/ocr/synth.py      Kanungo/Baird degradation augmentation, corpus generator
rfi_stamper/ocr/lexicon.py    confusion matrix, SymSpell, char n-gram, field grammars, number-lock
rfi_stamper/ocr/searchable.py rebuild-as-/Rotate-0, render_mode=3 word writer, coord mapping
rfi_stamper/ocr/profile.py    per-firm font profile sidecar + self-learning stores
rfi_stamper/ocr/eval.py       CER/WER/field-acc harness + confusion sub-metric
```

**Searchable-layer specifics:** rebuild each OCR'd page as a NEW /Rotate 0 page
sized `pixmap/(dpi/72)`, place raster full-page, write text in pixel/zoom coords
(mirrors current `pdfocr` consumption). If ever writing onto a rotated original,
**reuse `stamp._viewer_to_media`** — never re-derive it (CLAUDE.md gotcha: the
obvious alternative renders 180° flipped, only pixel-diff caught it). Run a
`verify.py`-style pixel-diff to prove the raster is untouched.

**From-scratch test/eval plan** (`tests/`, deterministic, NDA-safe):
- **(a)** Synthetic + Kanungo-degraded corpus, generated deterministically at
  run time; sweep params until synthetic CER matches the real 36-RFI blind set.
  Hold out fonts and strings.
- **(b)** Auto-labeled real set = vector CAD sheets rasterized then scored
  against `fitz.get_text("words")` (text AND boxes) — unlimited real ground
  truth at zero human cost, validates box alignment too.
- **(c)** Hand-labeled scanned set (~20–50 sheets) for sheet numbers /
  dimensions / room names — the only honest degraded-scan metric.
- **Harness:** pure-Python Levenshtein → CER/WER/char-accuracy (de-speckle
  before scoring), a 45×45 confusion matrix, and a domain sub-metric weighting
  sheet-number/dimension errors above note errors; score with the app's own
  case-fold/whitespace normalization (`_contains` in `tests/test_ocr.py`).
  **Targets:** clean CER ≤ 1–2% within a small delta of Tesseract on the shared
  corpus; sheet-number field accuracy ≥ 99%. Extend `tests/run_all.py`.

---

## 7. Build phases (each ships green, incremental)

**P1 — Preprocess + CC + searchable-layer scaffold (template matching).**
render/gray/polarity/DPI-normalize → Otsu + integral-image Sauvola (flatness
router) → deskew (0/90/180/270 + fine) → long-run line removal → run-based
union-find CC + geometric filters → glyph normalize → **NCC template matching**
against synthetic Hershey/base-14 prototypes → rebuild-as-/Rotate-0
`render_mode=3` word writer (DPI/coordinate/rotation correct, pixel-diff
verified). Preserve the full public API. Ship behind a confidence gate;
Tesseract still present. *Green:* `tests/test_ocr.py` passes unchanged; title-
block sheet numbers read on clean vector-derived rasters.

**P2 — Features + kNN/MLP + synthetic training.**
gradient NCFE + zoning/structural features; synthetic corpus (3 font sources ×
Type A/B × 0°/15° × Kanungo/Baird sweep); train the numpy MLP; wire the NCC +
kNN + MLP ensemble with calibrated confidence + topology gate; segmentation
upgrades (drop-fall split, broken-glyph merge, DP recombination). *Green:*
clean-scan CER ≤ 1–2% on the auto-labeled real set.

**P3 — Lexicon/grammar post-correction + self-learning.**
field grammars (`core.SHEET_TOKEN`/`canon_loose` + sheet-index cross-check,
`holler.parse_dimension`/`format_ftin`, room lexicon), noisy-channel correction
with domain confusion matrix + SymSpell over the Heartwood vocabulary + char
n-gram back-off, number-lock fail-closed, garbage rejection (τ_hi/τ_lo),
two-lane human-gated self-learning feeding kNN store + confusion matrix +
per-firm font profile. *Green:* sheet-number field accuracy ≥ 99%; number-lock
proven in tests.

**P4 — Eval harness + Tesseract removal.**
full CER/WER/field-acc harness in `tests/run_all.py`; match Tesseract within a
small delta on the shared corpus; confidence-route uncertain reads to the
appendix/review channel; then **remove the Tesseract dependency** and its
build/PyInstaller references. *Green:* regression harness green vs baseline;
`build_windows.bat` produces a smaller binary with no external OCR engine; CLI
`*_report.txt` ends in PASS.

---

## 8. Risks & honest limits

- **Text fused with linework** (dimension text on dimension lines, labels
  welded to bubbles, text in angled hatch) — CC merges glyph+line into one
  blob; axis-aligned line removal can't touch diagonal linework. **Explicit v1
  SKIP** (mirrors `backcheck.py`'s honest-SKIP list); mitigated on mixed pages
  by subtracting the app's own vector line geometry.
- **Touching/broken glyphs on 3rd-gen photocopies** — needs drop-fall split +
  DP recombination; clean CER easy, degraded-photocopy CER is the real risk.
- **Sub-legible small/dense text** — half-size sets at 150 dpi drop 1/8" text
  below the recovery floor. **Detect effective glyph height and flag/refuse
  rather than return garbage** — never upscale-and-pretend below ~15 px x-height.
- **Unusual/hand fonts & heavy degradation** — synthetic corpus spans
  ISO-3098-shaped fonts only; genuinely hand-lettered sheets fall outside it.
- **Real-word errors** (S-101 → valid S-107) — caught only by cross-checking
  the document's own sheet index + dimension grammar + number-lock, never the
  dictionary.
- **Raw throughput** — pure-Python CC over 67–155 MP sheets is
  tens-of-seconds; acceptable for an offline batch tool, mitigated by ROI-first,
  tiling, run-length CC, vectorized classification — budget for it, run pages in
  the background.
- **Segmentation, not classification, is where accuracy bleeds** — spend the
  engineering budget on text/linework separation, touching-glyph splitting, and
  augmentation realism, not exotic classifiers. **(v4.7.1 confirmed this the
  hard way.** The "~11% degraded photocopy" residual was not classification and
  not even touching-glyph splitting — it was the glyph-height SCALE: speckle
  floods the CC set with 1–2 px blobs, the median glyph height collapses toward
  the noise height, and every thin glyph is then gated out as linework.
  Excluding sub-despeckle boxes before `components._median_glyph_h`'s median
  took a speckled-scan CER from 11.39% to ~0. The genuine remaining residual is
  the touching/broken glyphs and sub-legible small text above.)

**Honest fallback stance:** scope v1 as **"scanned-plan lettering only"**
(isolated-text regions — sheet number, note/keynote blocks, room labels,
bubbles), dimension-line-fused and hatch-embedded text an explicit SKIP; every
uncertain read confidence-routed to the existing human mapping-review /
appendix (invariants 4 & 5). **Keep the optional Tesseract path available as a
power-user fallback until P4's eval harness proves parity** on the shared
corpus, then remove it — the staged design makes that a clean, reversible
cutover. The decisive genuine edge over a frozen engine is structural:
per-project font adaptation and lexicon/grammar-constrained decoding driven by
the app's own trade vocabulary and sheet index, so a firm's own hand of sheets
makes the engine monotonically better — offline, forever.
