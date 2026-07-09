# MINIPDF_PLAN.md — retiring reportlab (from-scratch PDF writer) and tkinterdnd2 (native drag-drop)

**Status:** COMPLETE — Track A (reportlab retired, v4.8.0) and Track B (tkinterdnd2 retired,
v4.9.0) both shipped. Phase history:
- **P1 foundation** — `rfi_stamper/minipdf/` (WinAnsi `encoding`, reportlab-exact `metrics`,
  `content`-stream builder, `document` serializer) emits a valid, deterministic, `qpdf --check`-clean
  PDF; `string_width` matches the reportlab oracle to machine epsilon (`tests/test_minipdf.py`).
- **P2 stamp cutover** — a reportlab-`canvas`-shaped `minipdf.Canvas` façade; `stamp.py` selects the
  engine via `PLOOM_PDF_ENGINE` (default reportlab). The from-scratch overlay is **pixel-identical to
  reportlab** (max |Δ|=0 at 90+300 dpi) and passes the real `verify.py` end-to-end on rot-0 + /Rotate
  90; the delivered file is now metadata-clean (`stamp.py` drops pypdf's `/Info`)
  (`tests/test_minipdf_parity.py`).

- **P2b Loft plates** — the façade gained `Color`/`HexColor`, `setFillColor`/`setStrokeColor`,
  `beginPath`/`clipPath`, and Bézier `circle`/`arc`/`ellipse`; `draft.py`'s `plate_pdf` is
  engine-selectable. Plates render within the 25-gray verify threshold of reportlab everywhere
  (0 px over threshold; only sub-threshold curve AA differs). **Direct-canvas tier complete.**

- **P3 flow/table engine + ALL consumers cut over** — `minipdf/flow.py` (ParagraphStyle, Paragraph,
  Spacer, HRFlowable, TableStyle, Table with header-repeating pagination, SimpleDocTemplate) +
  `pagesizes.py`. Cut over behind the flag: **transmittal** (+ delegating resolution/crewpass/daybook/
  submittal), **reports** (forms/daily/snapshot; a `_NumberedCanvas` factory dispatches by engine),
  and **fieldpro** (landscape ledger + stake sheet; raster thumbnail → "no thumbnail" fallback). The
  canvas duck-types reportlab `Color`, so modules' existing colour constants drive both engines.
  Tables are a new layout engine → clean but not pixel-identical to platypus (reports aren't
  verify.py-gated); validated structurally (valid/paginated/header-repeat/footer/round-trip, `qpdf`).

- **P4 default flip + P5 reportlab RETIRED (v4.8.0).** The zero-re-baseline flip: the three
  "failing" tests were ONE canvas-semantics bug (reportlab's `showPage()` *ends* a page — the next
  page materializes lazily on the first draw, so the pervasive trailing `showPage(); save()` idiom
  adds no blank page; plus reportlab's Helvetica-12 default font). With lazy-page semantics +
  default-font parity in `minipdf.Canvas`, **the whole suite passes on the from-scratch engine with
  zero test edits**. Then: `layout.py` measures via `minipdf.metrics` (oracle-equal to 1e-13);
  `transmittal`/`reports`/`fieldpro` are minipdf-only; `stamp`/`draft` default to minipdf with
  `PLOOM_PDF_ENGINE=reportlab` as the dev-box parity-oracle opt-in; test fixtures build plans with
  `minipdf.Canvas`; reportlab is OUT of requirements.txt and `excludes=["reportlab"]` guards both
  PyInstaller Analysis blocks. A meta-path blocker proof imports every module and produces
  stamp/table/form PDFs with reportlab unavailable.

**Track A COMPLETE (v4.8.0): Planloom generates every PDF with its own engine.** reportlab remains
only an optional dev-box install for the parity-oracle tests (which skip cleanly without it).

**Track B COMPLETE (v4.9.0): tkinterdnd2 RETIRED.** `gui/dnd.py` is a pure per-toplevel drop
**Router** (unchanged public surface; per-target hover synthesis, smallest-viewable-target routing,
ext filtering, toplevel fallback = the overlay hook, leave-on-drop, `after(20)` deferral) fed by
`gui/dnd_win32.py` — a from-scratch ctypes OLE `IDropTarget` (7-slot vtable, CF_HDROP +
`DragQueryFileW`, `QueryGetData` drop-effect, top-level-frame HWND registration, pinned COM refs,
revoke-on-destroy, honest `HAS_NATIVE`). `overlay.py` uses the façade only; requirements/spec carry
neither retired library; the seam is tested with synthetic backend events under xvfb (a real OS drag
cannot be synthesized headlessly — the OLE half gets the real-Windows smoke listed in HANDOFF).

**Both tracks of this plan are done.** The dossiers below remain as the build's reference record.
**Scope:** two independent efforts — **(1)** a from-scratch "mini-pdf" writer to retire `reportlab`,
and **(2)** removing `tkinterdnd2` (graceful, with an optional native drag-drop shim).
**Provenance:** synthesized from an 8-agent parallel research pass (6 agents on the PDF writer, 2 on
drag-drop; one audited the actual repo). Full per-track dossiers are appended as Appendices A–H.
**Non-negotiable gate:** invariant 4 (pixel-diff verification) governs everything the writer produces.
The from-scratch OCR retirement of Tesseract (OCR_PLAN P1→P4) is the precedent: stage behind a flag,
prove parity, then delete.

---

## 0. Verdict

**Feasible and in-character for this codebase — but bigger than "base-14 text + rectangles."** The
repo audit (Appendix D) found `reportlab` is used **two ways**:

- **Direct canvas** (easy, ~700–1,000 LOC): `stamp.py` note overlays, `draft.py` Loft plate PDFs —
  `rect`/`line`/`drawString`/colors/`saveState`/`translate`/`rotate`/`clip`. This is the part that
  matches the original "tiny slice" intuition and sits **inside the pixel-diff gate**, so it's the
  highest-confidence place to start.
- **A platypus flow/table engine** (the real cost, ~900–1,400 LOC): `transmittal.py` (RFI-log / generic
  tables), `reports.py`, `fieldpro.py` (As-Staked Ledger, landscape), `resolution.py` Designer Pickup
  Sheet — `Table`/`TableStyle`/`Paragraph`/`SimpleDocTemplate`/`simpleSplit`, per-cell word-wrap,
  multi-page pagination, repeatable headers, and a `_NumberedCanvas` two-pass "Page X of Y".

**Total ≈ 1,800–2,600 LOC, bimodal complexity.** Font/metrics code is small but the highest
silent-failure risk; the table engine is where most lines and most pagination risk live.

**The one thing that makes or breaks it:** `layout.py` imports `reportlab.pdfbase.pdfmetrics.stringWidth`
to decide where note headers truncate. If the from-scratch width tables differ from reportlab's by a
fraction of a point, line-break/box-height decisions shift, red text can graze linework, and
`verify.py` FAILs. **AFM Core-14 metric parity (kerning ignored) is a hard invariant, not cosmetics.**

**Recommendation:** do it in phases, gated on a `stringWidth`-parity test and a dual-render pixel-diff
parity harness, keeping `reportlab` as a **dev/test-only oracle** until the real 36-RFI blind-test
corpus renders identically. Retire it from the *shipped runtime* first; delete it from the *dev env*
last. Treat DnD as a separate, much smaller effort that can land first as a quick win.

---

## 1. Industry expectations — (1) the mini-pdf writer

What a production-grade, non-embedded base-14 PDF writer is expected to get right (consolidated from
Appendices A, B, C, F):

**Structure & conformance**
- Target **PDF 1.4** with a **classic cross-reference table** (one debuggable code path; universal
  viewer floor). No object/xref streams — they save nothing on low-object-count files and cost
  compatibility.
- Emit the mandatory **binary-marker comment** (`%` + 4 bytes ≥ 0x80, canonically `0xE2E3CFD3`) on line 2.
- **Byte-exact xref:** contiguous `0..Size` subsection, **20-byte records**, free object 0 at
  `0000000000 65535 f`, offsets counted in **bytes** from file start, `startxref` at the `xref`
  keyword, `%%EOF` at the end. Off-by-one offsets are the #1 hand-writer bug.
- **Exact per-stream `/Length`** (bytes between the LF after `stream` and the LF before `endstream`).
- Passes `qpdf --check` and `mutool clean` with **no repairs/reconstruction** — "opens in Chrome" is
  not the bar (fitz/pypdf silently rebuild a broken xref and *hide* the bug in tests while Acrobat fails).

**Fonts, encoding, metrics (the bug-prone core — Appendix C)**
- Base-14 `/Type1` fonts (`Helvetica`, `Helvetica-Bold`) referenced by exact name, `/Encoding
  /WinAnsiEncoding`, **never embedded**, no `/Widths`, no `FontDescriptor` (legal pre-PDF/A).
- **Single-byte WinAnsi**, never UTF-8. The app's non-ASCII glyphs live in the 0x80–0x9F band:
  em dash `—` = **0x97**, ellipsis `…` = 0x85, bullet/middot `·` = 0xB7, degree `°` = 0xB0, ± = 0xB1.
  Encoding with `latin-1` *raises* on `—`/`…`; encoding with `errors='ignore'` *silently drops* them.
- Advance widths from the **Adobe Core-14 AFM** tables (1000-unit em), keyed by **glyph name** (never
  the AFM `C`/StandardEncoding column — that mismaps em-dash, bullet, quotes). **Kerning ignored** to
  match reportlab.
- The **same encoder feeds both width-measurement and drawing**, so layout math and rendered ink can
  never diverge.

**Content stream & coordinates (Appendix B)**
- Minimal operator vocabulary: text `BT/ET Tf Td/Tm/T* TL Tj`, path `m l c re h`, paint `S f B n`,
  clip `W n`, state `q Q cm w d`, color `rg RG g G`. Build a path fully, then paint once. **Balance
  every `q`/`Q`** (a leftover clip/color corrupts the *second* stacked note — invisible until a
  multi-RFI sheet is tested).
- Default user space is **bottom-left, +y up, 1 unit = 1/72"**. **Keep the existing split:** the writer
  draws in CropBox y-up space; **all `/Rotate` + crop-offset stays in pypdf's field-verified
  `stamp._viewer_to_media`** — do NOT duplicate rotation in the writer (the documented `/Rotate 90`
  gotcha renders 180°-flipped, caught only by pixel-diff).
- Escape `(`, `)`, `\` in literal strings (RFI titles/answers contain parens), or use hex strings.

**Determinism & privacy**
- **No timestamps.** Omit `/Producer`, `/CreationDate`, `/ModDate`; derive a **content-hash `/ID`**.
  This protects both the pixel-diff baseline (reproducible bytes) and the NDA/offline posture
  (reportlab currently leaks a timestamped `/Producer`). Extend the same suppression to the **pypdf
  merge step** on the stamp path, or delivered stamped PDFs still carry metadata.
- LF-only line endings; fixed-decimal numbers (no scientific notation — `1e-05` is illegal PDF).

**Compression (Appendix F)**
- `FlateDecode` via **stdlib `zlib`** (already inside the offline boundary) — but ship **uncompressed
  in v1** (diff-readable), add Flate later, and only when it actually shrinks (>~200 B streams). A
  **from-scratch DEFLATE is 400–700 LOC of risk with zero observable benefit** — explicitly declined.
- **PDF/A is out of scope** (would force embedded fonts + OutputIntent/ICC + XMP for zero current need).

---

## 2. Industry expectations — (2) native drag-and-drop

Consolidated from Appendices G and H:

- Drag-drop is **OS-protocol-specific** — there is no portable single implementation. Windows =
  OLE `IDropTarget` (or the simpler legacy `WM_DROPFILES`); Linux/X11 = freedesktop **XDND**
  ClientMessage handshake; macOS = Cocoa `registerForDraggedTypes`.
- A soft dependency is **feature-detected and degrades to a first-class fallback** (here: the Browse
  pickers the GUI already ships) — never crashes when absent. `tkinterdnd2` is a thin wrapper over the
  compiled `tkdnd` Tcl extension, so removing it also **removes bundled native binaries** from the exe.
- Expected UX: drag-enter affordance, correct copy/no-drop cursor feedback, multi-file + Unicode
  filename support, type-filter at enter time, never block the UI thread on drop, always ship a
  keyboard/click alternative.
- **Headless CI tests the parse+route seam** with synthetic events under xvfb — you **cannot** synthesize
  a real OS drag headlessly; trying produces flaky tests.

**The pragmatic reality for this app.** The GUI **already degrades gracefully** (`HAS_DND=False` →
Browse). The *only* hard `tkinterdnd2` coupling is `make_root()` (`TkinterDnD.Tk()` → `tk.Tk()`).
Baseline removal is **~40–70 LOC** + spec/requirements edits + headless tests. Native DnD is real,
platform-specific work: Windows OLE `IDropTarget` ~200–280 LOC (COM vtable lifetime / STA / reentrancy
hazards) or `WM_DROPFILES` ~100 LOC (loses the hover overlay); X11 XDND ~300–450 LOC (WM-compat risk);
macOS not worth it from ctypes. **Since the product ships Windows exes and Linux only runs the test
suite, the recommendation is: remove `tkinterdnd2` with graceful fallback, and — if you want to keep
the cosmetic drop feature — add a Windows-only ctypes shim. Do not build X11 or macOS native.**

---

## 3. The reportlab contract the writer must satisfy (Appendix D)

The drop-in public surface (so the 6 call sites change only their import):

| Surface | Used by | Complexity |
|---|---|---|
| `Canvas`, `setFont`, `drawString`/`drawCentredString`/`drawRightString`, `rect`, `line`, `setFillColorRGB`/`setStrokeColorRGB`, `setLineWidth`, `showPage`, `save`, canvas `stringWidth` | stamp, draft, all | Moderate |
| module-level `stringWidth(text, font, size)` (AFM-backed, reportlab-identical) | **layout.py** (gates box geometry) | **Hard invariant** |
| `saveState`/`restoreState`, `translate`, `rotate`, `beginPath`/`clipPath`, `setDash`, `circle`/`arc`/`ellipse` (Bézier) | draft.py Loft plates | Moderate–Hard |
| `Color`/`HexColor` (HexColor raises on bad input, callers catch) | draft, fieldpro | Trivial |
| `_NumberedCanvas` (named import; deferred-footer two-pass "Page X of Y") | reports, fieldpro | Moderate |
| `Table`/`TableStyle`/`Paragraph`/`Spacer`/`HRFlowable`/`SimpleDocTemplate.build(story, canvasmaker=)`/`simpleSplit`, `letter`/`landscape` | transmittal, reports, fieldpro, resolution | **Hard (the big cost)** |
| `drawImage`/`ImageReader` (fieldpro plan thumbnail — has a "no thumbnail" fallback) | fieldpro only | Optional — can be dropped |

**Hard cases to respect:** table pagination height math must match cell wrapping **exactly** (a
different wrap count moves a row to another page and changes every "Page X of Y" and the pixel image);
`col_widths` in `rfi_log_pdf` (44/218/116/58/68) are tuned so headers never wrap; a cell taller than a
page must raise the same guard reportlab does (transmittal caps cells at 2000 chars); `setDash([])`
must actually reset (or dashes leak into chrome); `clipPath` must truly clip (or a plate bleeds into
the title strip).

---

## 4. What a successful build needs (the checklist — Appendices E, H)

**Correctness gate (the writer)**
- [ ] `stringWidth` parity test: mini-pdf width == reportlab width to ≤ 0.01 pt over a corpus including
      the app's non-ASCII glyphs — **gate the whole cutover on this**.
- [ ] `verify.verify(...)` (the real one: dpi=90, csGRAY, DIFF_THRESH=25, DARK_THRESH=225) returns
      `ok==True` with `under==0, outside==0, inside>300`, untouched pages 0 px changed — never weakened.
- [ ] Differential **old(reportlab)-vs-new(minipdf) pixmap diff** at 90 dpi (default `changed==0`) with a
      300 dpi cross-check to distinguish real drift from AA fringe.
- [ ] `get_text("words")` round-trip: identical word sequence + per-word bbox ≤ 0.35 pt (catches
      encoding/kerning errors a pixel diff of a blank box would miss).
- [ ] Structural validation on 100% of a golden corpus: `qpdf --check` clean, `mutool clean` no repairs,
      `pdftotext` exact text; **corpus** = empty page, single box, stacked notes, appendix, multi-row
      multi-page table, oversized cell, landscape, `/Rotate 90/180/270` + CropBox inset, escaped/unicode
      strings, long clipping header, status-suffix headers.
- [ ] Deterministic output (fixed/omitted metadata, content-hash `/ID`, stable object order) — byte-hash
      reproducibility test on standalone deliverables.

**Project gate (both efforts)**
- [ ] `python tests/run_all.py` green, GUI under `xvfb` (the supported interpreter is **python3.12**).
- [ ] **No new runtime dependency** — net dependency count goes **down** (reportlab and tkinterdnd2 out;
      `zlib`/`ctypes` are stdlib).
- [ ] `offline_guard` active, **zero network imports** (grep the diff).
- [ ] Banned vendor/person/product-name scrub before every commit.
- [ ] AFM Core-14 metrics **bundled into the frozen exe** (recommend hard-coded Python literals for the
      one-file build; reading from `reportlab`'s site-packages works in dev and breaks once frozen).
- [ ] Docs updated: `CLAUDE.md` (invariants/repo map/gotchas), `HANDOFF.md`, `ROADMAP.md`, `README.md`,
      and this plan; version bump.
- [ ] `build_windows.bat` emits **both** one-file exes; the CLI smoke `*_report.txt` ends in **PASS**;
      **smoke the frozen exe** (AFM-not-bundled and `/Rotate` bugs only appear frozen).
- [ ] DnD: `rfi_stamper.spec` edited in **both** the GUI *and* CLI Analysis blocks; `parse_drop_paths`
      keeps using `widget.tk.splitlist` (a naive `str.split()` mangles braced Windows paths with spaces).

---

## 5. Phased, non-one-shot rollout

**Track A — mini-pdf writer** (gated, reversible, reportlab kept as oracle throughout):

1. **P0 — Decide.** Lock the open decisions in §6.
2. **P1 — Core + metrics.** `minipdf.py`: objects/xref/trailer, content-stream builder, WinAnsi encoder,
   `/Font` dicts, **AFM width tables**. Ship the `stringWidth`-parity test first; nothing proceeds until
   it's green against reportlab.
3. **P2 — Direct-canvas paths (inside the pixel gate).** Route `stamp.py` overlays and `draft.py` plates
   through a `PLOOM_PDF_ENGINE` flag. Dual-render + pixel-diff parity harness (both via fitz, assert
   box-identical). Highest confidence — this is where verify.py already guards.
4. **P3 — The flow/table engine.** `Table`/`Paragraph`/`SimpleDocTemplate`/`_NumberedCanvas` for
   transmittal/reports/ledger/pickup. Golden-diff **per module** (submittal, resolution, crewpass,
   daybook, snapshot delegate through these).
5. **P4 — Flip the default.** Run the **36-RFI blind-test corpus** + full suite + all structural
   validators. Multiple clean dual-render runs before deleting anything.
6. **P5 — Retire.** Remove reportlab from the **shipped runtime** (spec/requirements); keep it as a
   **dev/test oracle** until confidence is total; decide whether the engine flag stays as an escape hatch.
   Final build, docs, version bump.

**Track B — tkinterdnd2** (independent; can ship first as a quick win):

- **PA — Graceful removal.** Rewrite `gui/dnd.py` to drop the import while preserving the public surface
  (`HAS_DND`, `make_root`, `parse_drop_paths`, `enable_drop`, `DND_FILES`); `make_root()` → `tk.Tk()`;
  baseline `HAS_DND=False`. Strip `tkinterdnd2` from `requirements.txt` and **both** spec blocks. Add
  headless `test_gui_construct.py` assertions (HAS_DND False under xvfb; `parse_drop_paths` handles
  braced spaced paths + ext filter; DropZone advertises Browse).
- **PB — Optional Windows shim.** If you want to keep drop-on-Windows: a pure-ctypes `WM_DROPFILES`
  backend (~100 LOC, no hover overlay) or the heavier OLE `IDropTarget` (~250 LOC, keeps hover). Behind
  the same `enable_drop`/`HAS_DND` façade. No package, Windows-only, network-free.

---

## 6. Open decisions for you (these shape the build)

1. **Table/flow engine — the biggest fork.** Faithfully reproduce platypus (safest for goldens, the big
   cost) **vs** refactor the table producers into a from-scratch direct-canvas paginator (simpler code,
   but *changes* output for 6 delegating modules and re-baselines their goldens)? *(Recommend: faithful
   reproduction — it preserves existing output and byte goldens.)*
2. **DnD scope.** Remove entirely (rely on Browse) **vs** keep a Windows-only ctypes shim **vs** full
   native? *(Recommend: remove + optional Windows shim; no X11/macOS.)*
3. **reportlab as a dev/test-only oracle** after removal from runtime (rot-proof parity, keeps a dev
   dep) **vs** frozen golden pixmaps (pins the fitz version)? *(Recommend: keep as dev/test oracle.)*
4. **AFM metrics packaging:** hard-coded Python literals **vs** a bundled data file via `_MEIPASS`?
   *(Recommend: literals — most robust for one-file.)*
5. **Determinism:** make byte-reproducible output (content-hash `/ID`, no timestamps) a firm requirement?
   *(Recommend: yes — protects the pixel baseline and NDA posture.)*
6. **Pixel tolerance:** strict `changed==0` at 90 dpi **vs** a bounded AA delta strictly inside box
   footprints? *(Recommend: strict — achievable with non-embedded base-14 + deterministic fitz.)*
7. **`drawImage` (fieldpro plan thumbnail):** implement a real image XObject **vs** always take the
   existing "no thumbnail" fallback and drop image support? *(Recommend: drop it — it already has a
   fallback.)*
8. **Version scheme:** DnD removal as a patch (v4.7.x), mini-pdf default-flip as a minor (v4.8.0)?

---

## 7. Risk register (top hazards)

| Risk | Why it bites | Mitigation |
|---|---|---|
| AFM width drift vs reportlab | `layout.py` truncation → box geometry → text over linework → verify FAIL | Metrics from same Adobe Core-14 AFM; kerning off; parity test gates cutover |
| Wrong WinAnsi byte for `—`/`·`/`…` | glyph silently dropped; box may still PASS pixel-diff while shipping empty note | shared encoder for width+draw; `get_text` round-trip test |
| Off-by-one xref / wrong `/Length` | fitz/pypdf silently repair (hides bug in tests); Acrobat fails | qpdf `--check` + mutool + smoke the frozen exe |
| Unbalanced `q`/`Q` | 2nd stacked note renders wrong | stacked-notes fixture in corpus |
| `/Rotate 90` CTM convention | 180°-flip, only pixel-diff catches | keep rotation in `stamp._viewer_to_media`; all-4-rotation matrix |
| Table wrap ≠ reportlab | row moves page → "Page X of Y" + pixels change | reproduce platypus wrap exactly; per-module golden-diff |
| AFM not bundled in exe | `stringWidth` crashes only when frozen | hard-code metrics; smoke frozen build |
| Windows COM callback GC'd | use-after-free hard crash | keep permanent refs to vtable/callbacks/object |
| reportlab metadata leak | timestamp/Producer under NDA + breaks byte-repro | suppress in writer AND pypdf merge step |

---

## Appendices — full per-track research dossiers

Each appendix is the verbatim output of one research agent (industry norms, must-haves, pitfalls,
effort, open questions, cited sources are in the workflow record).

- **Appendix A** — PDF file structure & object model
- **Appendix B** — content-stream operators, coordinate system & graphics/text state
- **Appendix C** — base-14 fonts, AFM metrics, WinAnsi encoding & text measurement
- **Appendix D** — reportlab API-surface audit of this repo (the drop-in contract)
- **Appendix E** — validation, QA & the pixel-diff verification gate
- **Appendix F** — compression, xref variants, file size, version & viewer compatibility
- **Appendix G** — native OS drag-and-drop protocols via ctypes
- **Appendix H** — tkinterdnd2 removal, PyInstaller/build/CI & the successful-build synthesis

---


# Appendix A

## Track 1 — PDF File Structure & Object Model (the mini-pdf writer)

### 1. Scope grounded in actual app usage

reportlab is called in exactly six modules with a *tiny* surface. A grep of `rfi_stamper/` shows the complete API footprint the replacement must cover:

| reportlab call | Frequency | PDF content-stream equivalent |
|---|---|---|
| `canvas.Canvas(buf, pagesize=(w,h))` | every producer | file + one `/Page` with `/MediaBox [0 0 w h]` |
| `setFillColorRGB(r,g,b)` / `setStrokeColorRGB` | stamp, reports, draft | `r g b rg` / `R G B RG` |
| grayscale `fill=(i%2==0)` (scale bar) | draft, reports | `g` / `G` |
| `setLineWidth(w)` | stamp, draft | `w w_ w` (operator `w`) |
| `rect(x,y,w,h,stroke=,fill=)` | all | `x y w h re` + `S`/`f`/`B`/`n` |
| `line(x0,y0,x1,y1)` | reports, draft | `x0 y0 m x1 y1 l S` |
| `setFont(name,size)` + `drawString(x,y,s)` | all | `BT /F1 size Tf x y Td (s) Tj ET` |
| `stringWidth(s,font,size)` | reports, resolution, layout | **AFM metrics table** (no PDF output) |
| `saveState/translate/rotate/restoreState` + `clip.rect()` | **draft.py plate PDF** | `q`, `cm`, `re W n`, `Q` |
| `showPage()` / `save()` | all | new page object / write xref+trailer |

Only two font programs are ever named: **Helvetica** and **Helvetica-Bold** (base-14, never embedded). Colors are flat RGB or gray. No images, no transparency, no gradients, no Unicode beyond Latin-1 (the code already funnels text through a `_latin()` helper). This is squarely inside what a hand-written writer does well.

**Two consumers, both must parse the output:**
1. **pypdf** reads each stamp overlay back via `PdfReader(buf)` then `page.merge_transformed_page(...)` — so overlays must have a clean classic xref and a single `/Page`.
2. **PyMuPDF (fitz)** renders the report PDFs for the pixel-diff verifier and for on-screen preview.

The hard constraint from invariant #4: **every stamped page is pixel-diff verified** — the only rendered change may be the intended box. That makes *rendering stability across fitz + Acrobat + Chrome* a correctness requirement, not a nicety, and makes **byte-for-byte deterministic output** highly desirable (a re-run must reproduce the same file so the diff baseline is stable and the NDA/offline posture leaks no timestamps).

### 2. Target PDF version and conformance

**Target `%PDF-1.4`** (optionally 1.5). Rationale:
- 1.4 (2001) is the universal floor — every viewer since Acrobat 5 renders it, and it predates object/xref *streams* so we can emit the simple, debuggable **classic cross-reference table**. 1.5+ *permits* cross-reference streams but does not require them; staying at 1.4 keeps the writer to one code path.
- Nothing the app draws needs a 1.5+ feature. Transparency groups, optional-content, and object streams are all unused.
- 1.4 is what reportlab itself defaults near, so downstream behavior (fitz/pypdf/Acrobat) is already proven against this vintage.

**PDF/A archival conformance is NOT required** and should be explicitly declined for v1. PDF/A-1b/2b would force: embedded font programs (kills the base-14 no-embed simplification), an `OutputIntent` with an ICC profile, `XMP` metadata, `/ID`, and `DocumentID`/`InstanceID` — a large surface for zero current requirement (construction RFI overlays are working documents, not archival masters). Note it as an *open question* for the reporting/transmittal deliverables if a client ever mandates archival, but it is out of scope for retiring reportlab.

**"Valid" vs "opens-but-subtly-broken."** A file can satisfy a lenient reader (pypdf, Chrome) yet be malformed. The failure modes that matter here:
- **xref byte offsets off by even one byte** → Acrobat says "damaged, being repaired," fitz may silently rebuild the xref (masking the bug in tests) while Acrobat throws. The offsets are the #1 hand-writer bug.
- **`/Length` of a content stream wrong** → viewers read past/short of `endstream`; content truncates or leaks garbage → *fails pixel-diff*.
- **Missing free object 0 / wrong generation** → "Root object invalid."
- **Unbalanced `q`/`Q`** in the content stream → later drawing inherits a leftover clip or color; on a multi-note sheet the second box renders wrong → *fails pixel-diff*.
- **Unescaped `(`, `)`, `\` in a literal string** → text after the paren vanishes or the object dict corrupts.

### 3. File skeleton (ISO 32000-1 §7.5)

```
%PDF-1.4
%âãÏÓ                     <- 4 bytes >=128 (0xE2E3CFD3) on line 2, tells
                            transfer tools the file is binary. REQUIRED-ish.
1 0 obj … endobj          <- indirect objects, body
…
xref                      <- classic cross-reference table
0 5
0000000000 65535 f 
0000000015 00000 n 
…
trailer
<< /Size 5 /Root 1 0 R /ID [<hex><hex>] >>
startxref
<byte offset of the 'xref' keyword>
%%EOF
```

Every line/token separated by a single LF (`\n`, 0x0A). Pick LF and never mix — CRLF changes byte offsets and is a classic source of drift.

### 4. The eight object types (§7.3)

| Type | Syntax | Notes for this writer |
|---|---|---|
| Boolean | `true` / `false` | — |
| Numeric | `12` `0.84` | emit fixed decimals, **no scientific notation**, no trailing junk; clamp/round to e.g. 4 dp for determinism |
| String (literal) | `(text)` | escape `\( \) \\`; may escape non-ASCII as `\ddd` octal |
| String (hex) | `<48656C…>` | used for `/ID` |
| Name | `/Helvetica` `/F1` | `#` -escape bytes outside `! .. ~` |
| Array | `[0 0 612 792]` | MediaBox, ID |
| Dictionary | `<< /Type /Page … >>` | — |
| Stream | `<< /Length n >> stream\n…\nendstream` | the content stream; `/Length` must be exact byte count between the LF after `stream` and the LF before `endstream` |
| Null | `null` | rarely needed |

### 5. Document tree (§7.7)

`Catalog → Pages → Page(s)`. Copy-pasteable **single-page** template (US-Letter, 612×792):

```
%PDF-1.4
%âãÏÓ
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
   /Resources << /Font << /F1 5 0 R /F2 6 0 R >> >>
   /Contents 4 0 R >>
endobj
4 0 obj
<< /Length 74 >>
stream
1 0 0 RG 0.72 w 72 72 468 648 re S
BT /F1 12 Tf 90 700 Td (RFI 014 — DUCT CONFLICT) Tj ET
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica
   /Encoding /WinAnsiEncoding >>
endobj
6 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold
   /Encoding /WinAnsiEncoding >>
endobj
xref
0 7
0000000000 65535 f 
0000000015 00000 n 
0000000064 00000 n 
0000000123 00000 n 
0000000287 00000 n 
0000000420 00000 n 
0000000517 00000 n 
trailer
<< /Size 7 /Root 1 0 R /ID [<0102…> <0102…>] >>
startxref
614
%%EOF
```

**Two-page** support = append page objects to `/Kids` and bump `/Count`:

```
2 0 obj
<< /Type /Pages /Kids [3 0 R 7 0 R] /Count 2 >>
endobj
…
7 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
   /Resources << /Font << /F1 5 0 R /F2 6 0 R >> >>
   /Contents 8 0 R >>
endobj
```

Practical builder pattern: **buffer every object body first, then compute offsets** (you cannot know offset N until objects `<N` are serialized). Assign object numbers lazily as objects are registered; keep a `list[bytes]`; the xref is a second pass over cumulative byte lengths. A shared `/Resources`/font dict can be a single indirect object referenced by every page (fewer objects, deterministic).

### 6. The cross-reference table — exact byte format (§7.5.4)

This is the part that must be **byte-perfect**. Each entry is **exactly 20 bytes**:

```
nnnnnnnnnn ggggg k EOL
```
- 10-digit zero-padded byte offset (`%010d`)
- one space
- 5-digit zero-padded generation (`%05d`)
- one space
- one keyword char: `n` (in-use) or `f` (free)
- a **2-byte end-of-line**: either `SP LF` (`0x20 0x0A`) or `CR LF`. Pick `" \n"` (space+LF) so the record is `10+1+5+1+1+2 = 20` bytes. ISO requires exactly 20; readers rely on it for random access.

The subsection header `0 7\n` means "7 entries starting at object 0." **Object 0 is mandatory and always the head of the free list:**
```
0000000000 65535 f 
```
offset 0, generation 65535, free. Every other object this writer emits is generation `00000`, keyword `n`, offset = byte position of the `N 0 obj` token from file start.

Since this writer never does incremental updates, one contiguous subsection `0 <Size>` covers everything and all generations are 0 — the simplest legal case.

### 7. Trailer, /ID, startxref, determinism

```
trailer
<< /Size N /Root 1 0 R /ID [ <hex1> <hex2> ] >>
startxref
<offset of the word 'xref'>
%%EOF
```
- `/Size` = total object count **including object 0** (i.e., highest object number + 1).
- `/Root` → the Catalog.
- `/ID` — two byte-strings. ISO 32000-2 recommends it for every file; pypdf/encryption paths want it. **For determinism, derive it from content, not a clock/UUID:** e.g. `md5(all_object_bytes)` as both halves. This gives a stable, offline, reproducible ID with no entropy leak.
- **Omit `/Producer`, `/CreationDate`, `/ModDate`** (or set `/Producer (Planloom)` as a fixed literal). reportlab stamps a timestamped Producer/CreationDate by default — that is exactly what breaks byte-reproducibility and leaks metadata. A `/Info` dict is optional; skip it or keep it constant. This directly serves the pixel-diff baseline and the NDA posture (invariant #1).
- `startxref` = byte offset of the `x` in `xref`. Off-by-one here is the classic "damaged" bug.

### 8. Text, fonts, and metrics (the `stringWidth` replacement)

The app calls `stringWidth()` to right-justify, wrap (`_wrap`), center check-glyphs, and — critically — to compute box widths in `layout.py`. **This must be reproduced exactly or box geometry shifts and the finder/verifier disagree.** reportlab's widths come from the **Adobe Core-14 AFM files** (freely redistributable; Adobe-Core14-AFM license). Plan:
- Ship a small static table: for Helvetica and Helvetica-Bold, the 256 WinAnsi glyph advance widths (units = 1/1000 em). `width_pt = sum(afm[ch]) / 1000 * font_size`.
- These numbers must **match reportlab's to the integer /1000** so existing layouts render identically (regression-test `stringWidth` old-vs-new across a character corpus before switching).
- Text encoding: declare `/Encoding /WinAnsiEncoding` on each font. Then a code point like `–` (en dash, U+2013) → byte `0x96` in WinAnsi. The app already normalizes to Latin-ish text via `_latin()`; extend that to a WinAnsi encoder (`str → bytes`) with a safe fallback (`?`) for anything outside the 256-glyph set. **Do NOT emit raw UTF-8 in a `(...)` string** — Type1 base-14 text is single-byte-encoded; UTF-8 bytes render as mojibake and *fail pixel-diff*.
- Literal-string escaping: replace `\`→`\\`, `(`→`\(`, `)`→`\)`. Optionally octal-escape bytes ≥ 0x80 (`\226`) to keep the file 7-bit-clean.
- Text positioning: `BT /F1 size Tf x y Td (str) Tj ET`. Note reportlab's `drawString(x,y)` places the **baseline** at `y`; the app already accounts for this (e.g. `y + (L_HDR - S_HDR)/2`). Keep the same baseline semantics so no vertical drift.

### 9. Graphics operators needed (§8, §9)

A complete operator set for this app:

```
q  Q                 save / restore graphics state (for clip + rotate)
cm  (a b c d e f)    concat matrix — rotation/translation in draft.py
w                    line width
r g b RG / rg        stroke / fill RGB color
g / G                gray fill / stroke (scale bar, bars)
re                   append rectangle to path
m  l                 moveto / lineto
S  s                 stroke / closeStroke
f  F  f*             fill (nonzero / even-odd)
B  b                 fill+stroke
W  W*  n             clip (then no-op paint) — draft.py clip.rect()
BT ET  Tf  Td/Tm  Tj text
```

**Rotated text** (`draft.py` rotates plate labels): emulate `saveState; translate(x,y); rotate(θ); drawString(0,0,s); restoreState` as:
```
q  cosθ sinθ -sinθ cosθ x y cm  BT /F1 s Tf 0 0 Td (…) Tj ET  Q
```
**Clipping** (`clip.rect`): `q  x y w h re W n  …draw…  Q`. Every `q` needs its matching `Q`; a stack-depth assertion in the writer catches the unbalanced case before it ships.

### 10. Integration constraints specific to this repo

- **Overlay round-trip through pypdf.** `stamp.py` builds a one-page overlay, then `PdfReader(buf).pages[0]` and `merge_transformed_page`. The overlay's `/MediaBox` must equal the viewer page size `(view_w, view_h)` exactly, and the content must live in the page content stream (pypdf merges the content stream + resources). Keep the overlay to **one page, one content stream, base-14 fonts** — pypdf then rewrites xref on its own output, so the *final* delivered file's xref is pypdf's, not ours. That means for **stamp overlays**, our xref only has to be good enough for pypdf to parse (lenient). For **reports/transmittal/forms/draft plate PDFs**, our file *is* the deliverable and its xref must be perfect (Acrobat-grade).
- **fitz must render it** for `verify.py`. Test the writer's output through `fitz.open(bytes)` + `get_pixmap` early.
- Content-stream compression: **v1 should emit uncompressed content streams** (no `/Filter /FlateDecode`). Simpler, debuggable, diff-readable. Add optional `zlib`-based FlateDecode later (stdlib `zlib`, still offline) if file size matters — construction plan overlays are tiny so this is low priority.
- Keep a single module (proposed `rfi_stamper/minipdf.py`) exposing a **reportlab-`canvas`-shaped façade** (`Canvas`, `setFont`, `drawString`, `rect`, `line`, `setFillColorRGB`, `setStrokeColorRGB`, `setLineWidth`, `saveState`/`restoreState`, `translate`/`rotate`, `showPage`, `save`, and a module-level `stringWidth`) so the six call sites change only their import. This shrinks the diff and de-risks the pixel-diff regression.

### 11. Validation strategy

- **Golden pixel-diff:** render old (reportlab) vs new (minipdf) output of every producer at the app's 90-dpi render setting; require ≤ the existing diff threshold. This is the acceptance gate and it aligns with invariant #4.
- **Structural validation:** run **qpdf --check** and **veraPDF** (or `mutool clean -s`) over sample outputs in CI-adjacent dev testing (these are dev-time tools, not shipped — offline policy intact). qpdf catches bad offsets/`/Length`; a file that passes `qpdf --check` and re-renders identically in fitz + Acrobat + Chrome is the bar.
- **Round-trip:** open every output with pypdf and fitz; assert page count, MediaBox, and that text is extractable (`fitz get_text` returns the strings) — proves encoding is right.
- Keep the existing `tests/smoke_test.py` (rotation-0 + /Rotate 90) green; it is the field-verified guard for the overlay path.


# Appendix B

## Track 2 — Content-Stream Operators, Coordinate System & Graphics/Text State

This section specifies the PDF *content-stream* layer for the from-scratch "mini-pdf" writer: the exact operator subset, the coordinate model, the graphics/text state machine, string encoding, and a fully worked stream for Planloom's signature note box. It is scoped to **text + vector-only** output (base-14 fonts, rectangles, lines, flat RGB fills, positioned-text tables). File structure (objects, xref, trailer, stream length) and font/AFM width metrics are separate tracks; this track produces the *bytes between `stream`/`endstream`* and defines how coordinates map.

All operators and semantics below are per **ISO 32000-1:2008** (the PDF 1.7 spec; PDF 2.0 / ISO 32000-2 is operator-compatible for this subset). Section references cite ISO 32000-1.

### 1. Coordinate system (get this exact or verification fails)

- **Default user space origin is the BOTTOM-LEFT corner of the page; +x is right, +y is UP.** The unit is the *point* = **1/72 inch** exactly (ISO 32000-1 §8.3.2.3). A US-Arch-E sheet 34×22 in is `2448 × 1584` pt.
- The page's coordinate space is anchored to the **MediaBox**; a **CropBox** only clips what is displayed. Planloom's finder/overlay work in the *rendered viewer window*, which fitz derives from the **CropBox** — so the writer emits a page whose "logical" origin the caller treats as the CropBox lower-left, and the existing pypdf `_viewer_to_media` `Transformation` re-anchors onto the MediaBox and applies `/Rotate` at merge time. **Keep that split**: the mini-pdf writer draws in a clean bottom-left/y-up space sized to the CropBox (exactly as reportlab's canvas did); rotation/crop offset stays in the pypdf merge step (`stamp._viewer_to_media`), which is field-verified.
- **This is the opposite of Planloom's markup layer**, which is documented as *viewer page points, top-left origin, y DOWN* (`markups/model.py:5`, and the CLAUDE.md markup gotcha). Any code path that hands the writer a top-left/y-down coordinate MUST flip:

  ```
  y_pdf = page_height_pt - y_viewer        # point → point, same page height
  ```
  For a rectangle given as a top-left `(x, y_top, w, h)` viewer rect, the PDF `re` origin (its lower-left) is `x , (page_h - y_top - h)`. reportlab hid this because its canvas is already bottom-left/y-up and Planloom's stamp path (`stamp.draw_box`) already computes in y-up (`y0 = ytop - h`, baselines subtract downward). The writer only needs to accept the *same y-up numbers* `draw_box` already produces — do **not** re-introduce a flip inside the writer or boxes land mirrored and fail the pixel diff.

#### 1.1 The three matrices

| Matrix | Set by | Space it defines | Notes |
|---|---|---|---|
| **CTM** (current transformation matrix) | `cm` (concatenates), initialised by page | user space → device space | Composition of all `cm` since the last `q`. Restored by `Q`. |
| **Text matrix `Tm`** | `Tm` (sets absolutely), `Td`/`TD`/`T*` (translate) | text space → user space | Reset to identity by **`BT`**. Updated after each show by text advance. |
| **Text line matrix `Tlm`** | `Td`/`TD`/`Tm`/`T*` | records line start | `Tm` and `Tlm` are set together by `Tm`; `T*` = `0 -TL Td` relative to `Tlm`. |

Effective glyph placement = `Trm = [Tfs·Th 0 0 Tfs 0 Trise] × Tm × CTM` (ISO 32000-1 §9.4.4). For Planloom's flat, unrotated, unscaled text you only ever need `Tm = [1 0 0 1 tx ty]` and font size via `Tf` — no text scaling, no rise, `Th`=100%.

- A PDF matrix is the row vector form `[a b c d e f]` meaning
  `x' = a·x + c·y + e`, `y' = b·x + d·y + f`.
  - Identity: `1 0 0 1 0 0`.
  - Translate `(e,f)`: `1 0 0 1 e f`.
  - Scale `(sx,sy)`: `sx 0 0 sy 0 0`.
  - Rotate θ: `cosθ sinθ -sinθ cosθ 0 0`.
  - Rotate about a pivot `(px,py)`: translate(px,py) · rotate(θ) · translate(-px,-py); emit as one `cm` or as `q … cm … Q`.

#### 1.2 Placing / centering / rotating (recipes)

- **Left-aligned text at baseline `(x,y)`** (what `drawString` did): `BT /F1 <size> Tf x y Td (str) Tj ET`.
- **Center text horizontally in `[x0,x1]`**: `w = stringWidth(str, font, size)` (from AFM widths track); `tx = x0 + (x1-x0-w)/2`. There is **no "center" operator** — you compute the advance yourself.
- **Right-align**: `tx = x1 - w`.
- **Vertical centering of a cap-height line in a band of height `H`** (Planloom's `y + (L - size)/2` idiom): baseline = `band_bottom + (H - size)/2` approx; the app already precomputes this, so the writer just receives the baseline.
- **Rotated text** (e.g. a vertical table label): wrap in `q`, emit a rotation `cm` about the anchor, draw text at the anchor, `Q`. Prefer `cm` over baking rotation into `Tm` so the *text* matrix stays a pure translation and multi-line `T*` leading still works.
- **Rectangles never need rotation here** — page `/Rotate` is handled downstream by pypdf; the writer always emits axis-aligned `re`.

### 2. Operator reference (the required subset)

Numbers are pushed onto the operand stack, operator pops them. Whitespace = space/tab/CR/LF/FF/NUL. Real numbers use `.`; **no exponent, no `+` sign, locale-independent** — always format with an explicit C-locale formatter (see §5).

| Op | Operands | Category | Meaning / usage in Planloom |
|---|---|---|---|
| `q` | — | gstate | Push graphics state. |
| `Q` | — | gstate | Pop graphics state. Every box wrapped `q … Q` so color/line-width don't leak between boxes/pages. |
| `cm` | a b c d e f | gstate | Concatenate matrix onto CTM. (Writer itself rarely emits; merge step does.) |
| `w` | lineWidth | gstate | Stroke width in user-space units. Planloom border = `1.2` (`BORDER`). |
| `J` | cap | gstate | Line cap (0 butt,1 round,2 square). Default 0 fine. |
| `j` | join | gstate | Line join (0 miter,1 round,2 bevel). Default 0. |
| `M` | limit | gstate | Miter limit. Default 10; irrelevant for axis-aligned `re`. |
| `d` | array phase | gstate | Dash pattern, e.g. `[3 2] 0 d`; `[] 0 d` = solid. |
| `rg` | r g b | color | **Set nonstroking (fill) RGB.** `1 1 1 rg` = white fill. |
| `RG` | r g b | color | **Set stroking RGB.** `0.84 0.06 0.06 RG` = Planloom red. |
| `g` / `G` | gray | color | Set fill / stroke DeviceGray. |
| `k` / `K` | c m y k | color | Set fill / stroke DeviceCMYK. (Not needed — keep RGB to match the pixel-verified look.) |
| `m` | x y | path | Move-to; begin new subpath. |
| `l` | x y | path | Line-to. |
| `c` | x1 y1 x2 y2 x3 y3 | path | Cubic Bézier (two control pts). |
| `v` | x2 y2 x3 y3 | path | Bézier, first control = current point. |
| `y` | x1 y1 x3 y3 | path | Bézier, second control = endpoint. |
| `re` | x y w h | path | **Append rectangle** (x,y = lower-left). Equivalent to `x y m (x+w) y l (x+w)(y+h) l x (y+h) l h`. The box outline. |
| `h` | — | path | Close current subpath (adds line back to its start). |
| `S` | — | paint | Stroke path. |
| `s` | — | paint | `h` then `S`. |
| `f` (or `F`) | — | paint | Fill (nonzero winding). |
| `f*` | — | paint | Fill (even-odd). |
| `B` | — | paint | **Fill then stroke** — the box: one `re` + `B` gives white fill + red border in a single path. |
| `B*` | — | paint | Fill (even-odd) then stroke. |
| `b` / `b*` | — | paint | Close, then fill+stroke. |
| `n` | — | paint | No-op paint (ends path; used to apply a clip without painting). |
| `W` / `W*` | — | clip | Intersect clip path (nonzero / even-odd). Takes effect at the next paint op; pair with `n`: `… re W n`. Only needed if a body line must be hard-clipped to the inner box; Planloom instead *pre-fits* text (`_fit_header`, `wrap`) so clipping is optional. |
| `BT` / `ET` | — | text | Begin / end text object. Resets `Tm`,`Tlm` to identity at `BT`. Text ops are legal only between them. |
| `Tf` | font size | text | Set font resource + size, e.g. `/F1 9.2 Tf`. `/F1` names an entry in the page `/Resources /Font` dict (file-structure track). |
| `Td` | tx ty | text | Move to line start `(tx,ty)` **relative to current line matrix**; sets `Tm=Tlm=translate·Tlm`. |
| `TD` | tx ty | text | `-ty TL` then `tx ty Td` (sets leading as a side effect). |
| `Tm` | a b c d e f | text | Set text & line matrix **absolutely** (not relative). Use for the first line of each box. |
| `T*` | — | text | Next line: `0 -TL Td`. Needs `TL` set. |
| `TL` | leading | text | Set leading (used by `T*`,`TD`). Planloom line pitches: `L_HDR=11.6`, `L_BOD=9.5`. |
| `Tc` | charSpace | text | Extra spacing per char (user-space units, pre-scale). Default 0. |
| `Tw` | wordSpace | text | Extra spacing per **byte-0x20** space. Default 0. (Note: affects single-byte code 32 only.) |
| `Tj` | (string) | text | Show one string. |
| `TJ` | [array] | text | Show array of strings & numeric adjustments; each number moves by `-num/1000 × Tfs` (kerning / manual justification). Planloom rarely needs it — pre-measured layout — but it's the tool for tight tabular alignment. |

**Deliberately excluded** (not needed, keeps the writer small & the output trivially verifiable): `Ts` (rise), `Tz` (horizontal scaling), `Tr` (render mode — default 0 fill is what red text needs), `'` and `"` (show-with-newline shortcuts — spell out `T*`+`Tj` for clarity), inline images `BI/ID/EI`, XObjects `Do`, shadings `sh`, marked content `BDC/EMC`, extended gstate `gs`, and all `ICCBased`/pattern color operators (`cs/CS/scn/SCN`). Sticking to DeviceRGB + base-14 avoids color-management and OutputIntent complications entirely.

### 3. Graphics/text state model (invariants for the writer)

1. **Initial state per page**: CTM = identity (device default), no current path, DeviceGray black fill & stroke, line width 1, solid, `Tf` **undefined** (a stream that shows text before any `Tf` is an error — always emit `Tf` inside every `BT…ET`).
2. **`q`/`Q` bracket every logical object.** Color, line width, dash, clip, and CTM are part of graphics state and restored by `Q`; **text state (`Tf/Tc/Tw/TL/Tm`) is NOT saved by `q`** per ISO 32000-1 §9.3.1 — it persists across `BT/ET` but is scoped inside the stream. Practical rule: set every text parameter you rely on inside each `BT` block; never assume a prior `Tf` survives.
3. **Path then paint, exactly once.** A path built by `m/l/c/re` is invisible until a painting op (`S/f/B/n`). After painting, the path is cleared. Do not interleave text ops inside a path — finish the path (`B`) before `BT`.
4. **Color op scope**: `rg`/`g`/`k` set *fill*; `RG`/`G`/`K` set *stroke*; **fill color also colors text** (text is filled by default render mode). So `0.84 0.06 0.06 rg` immediately before `BT` is what makes the header/body red.
5. **Numbers are unitless user-space values**; never emit `pt`/`px` suffixes.

### 4. String encoding & escaping (a top source of silent corruption)

A base-14 simple font uses **single-byte codes**. Planloom must declare each font with **`/Encoding /WinAnsiEncoding`** (ISO 32000-1 §D.2, Annex D) so codes map to the Latin-1-plus-typographic set the app actually uses. Then every show-string is emitted as bytes in *that* encoding — **not UTF-8**.

**Two string forms:**

- **Literal string** `( … )`: bytes wrapped in parens. Must escape:
  - `\(` → `(` , `\)` → `)` (or keep balanced parens unescaped — an equal, nested-balanced count is legal, but *always escaping is safer*).
  - `\\` → `\`.
  - `\n \r \t \b \f` for LF/CR/tab/backspace/formfeed; a raw CR or CR/LF *inside* a literal is collapsed to a single `\n` by the reader, so escape any literal newline you actually want.
  - `\ddd` octal (1–3 digits) for any byte, incl. high-bit WinAnsi codes and control bytes. `\0` is NUL.
  - A backslash-newline is a **line continuation** (emits nothing) — useful to wrap long strings in source, but never rely on it for data.
- **Hex string** `< … >`: two hex digits per byte, whitespace allowed, odd final digit padded with `0`. E.g. `<52464920>` = `"RFI "`. **Recommended for any string containing non-ASCII or parens** — it sidesteps all escaping. A robust writer can emit hex unconditionally.

**WinAnsi code points the app relies on** (these are exactly the chars in `layout.py`/`resolution.py` — reportlab mapped them for free; the from-scratch writer MUST):

| Char | Unicode | WinAnsi byte | Octal in literal | Appears in |
|---|---|---|---|---|
| `—` em dash | U+2014 | `0x97` | `\227` | `RFI NNN — TITLE` header |
| `·` middle dot | U+00B7 | `0xB7` | `\267` | ` · ANSWERED` status suffix |
| `…` ellipsis | U+2026 | `0x85` | `\205` | `clip()` / `_fit_header` truncation |
| `–` en dash | U+2013 | `0x96` | `\226` | possible in RFI body text |
| `“ ” ‘ ’` quotes | U+201C/D, U+2018/9 | `0x93 0x94 0x91 0x92` | `\223`… | body prose |
| `°` degree | U+00B0 | `0xB0` | `\260` | dimensions/notes |

**Pitfall:** a code point **not in WinAnsiEncoding** (e.g. `→` U+2192, or CJK) has *no single byte* and will silently drop or render as a blank/`.notdef`. The writer needs a `unicode→WinAnsi` table with a defined fallback: substitute (`—`→`-`, `…`→`...`, smart quotes→ASCII) or a visible sentinel, and — because a dropped glyph changes the rendered pixels — the substitution must be deterministic so the pixel-diff stays stable across runs. Build the map from the WinAnsiEncoding table in ISO 32000-1 Annex D.2; verify against a known reference (the encoding is also reproduced in the PDFBox `WinAnsiEncoding` class and Adobe's `PDFEncoding` docs).

### 5. Number formatting (verification-stability requirement)

- Format with a fixed C-locale formatter, e.g. up to **~4 decimal places, trailing zeros stripped**, never scientific notation, `.` decimal separator, minus as `-`. (`"%.4f"` then strip.) A comma decimal from a locale-sensitive format produces a corrupt stream.
- Coordinates should be quantized deterministically (e.g. round to 3–4 dp) so **the same input yields byte-identical streams** — Planloom diffs rendered pixels, and a stable stream also lets you diff bytes in tests. reportlab already rounds; match its precision (2–3 dp is plenty at 72 dpi) to reproduce the pixel-verified geometry within the `SEARCH_PAD`/`PAD_PX` slack noted in the placement/verify gotcha.

### 6. Worked content stream — Planloom's signature note box

Target: a thin **red-outlined** (`0.84 0.06 0.06`), **white-filled** rectangle with a **bold red Helvetica-Bold** header line and a **Helvetica** body line, matching `stamp.draw_box`. Assume the CropBox-sized page and box geometry the app computes:

```
box lower-left  x   = 40      (= placement x)
box lower-left  y0  = 60      (= ytop - h)
box width       w   = 230
box height      h   = 48      → ytop = y0 + h = 108
BORDER w            = 1.2
PAD                 = 10
header font  /F1 = Helvetica-Bold  size S_HDR = 9.2  pitch L_HDR = 11.6
body   font  /F2 = Helvetica       size S_BOD = 7.7  pitch L_BOD = 9.5
```

Baselines follow the app's own math (`y + (L - size)/2` vertical centring):
- header baseline `= ytop - PAD - L_HDR + (L_HDR - S_HDR)/2 = 108 - 10 - 11.6 + 1.2 = 87.6`
- body baseline   `= ytop - PAD - L_HDR - L_BOD + (L_BOD - S_BOD)/2 = 108 - 10 - 11.6 - 9.5 + 0.9 = 77.8`
- text x `= x + PAD = 50`

Content stream bytes (the em dash `—` is WinAnsi `0x97`, shown here as octal `\227`):

```
q
1 1 1 rg
0.84 0.06 0.06 RG
1.2 w
40 60 230 48 re
B
0.84 0.06 0.06 rg
BT
/F1 9.2 Tf
11.6 TL
50 87.6 Td
(RFI 042 \227 SLAB EDGE CONDITION) Tj
ET
BT
/F2 7.7 Tf
50 77.8 Td
(Q: Confirm slab edge detail at grid B/3. A: See revised detail 5/S-501.) Tj
ET
Q
```

Equivalent header line as a **hex string** (escaping-proof; `RFI 042 — SLAB EDGE CONDITION`):

```
BT /F1 9.2 Tf 50 87.6 Td <52464920303432209753414220454447452043...> Tj ET
```

Notes on this stream:
- **One `re` + `B`** paints white interior and red border together — matches reportlab's `rect(..., stroke=1, fill=1)`; fill uses the `rg` color set *before* the path (white), stroke uses `RG` (red). The `rg`/`RG` you set before `re` are what `B` consumes.
- The **`0.84 0.06 0.06 rg` after `B`** switches the *fill* color to red so the following text (default render mode 0 = fill) is red.
- Each text line is its own `BT…ET` here for clarity; a multi-line body would instead set `TL` once and use `T*`:
  `BT /F2 7.7 Tf 9.5 TL 50 77.8 Td (line1) Tj T* (line2) Tj T* (line3) Tj ET`.
- `/F1`,`/F2` must resolve in the page `/Resources << /Font << /F1 … /F2 … >> >>` (file-structure/AFM track); both declared `/Subtype /Type1 /BaseFont /Helvetica-Bold` (resp. `/Helvetica`) `/Encoding /WinAnsiEncoding`.
- Wrapping in `q … Q` guarantees the red stroke/fill and 1.2 line width don't bleed onto the next box or a subsequent page's default black.

### 7. Rendering & verification correctness checklist

The HARD invariant is that fitz re-renders each stamped page and the *only* changed pixels are the intended box (`verify.py`, `DIFF_THRESH=25`). For that the stream must:
1. Produce geometry within reportlab's old rounding so the box lands in the same cleared window (respect `PAD_PX`/`SEARCH_PAD` slack).
2. Never leave the graphics state dirty (balanced `q`/`Q`, path always painted or `n`-ended).
3. Emit fills/strokes as **opaque DeviceRGB** (no `gs`/alpha) so white interior fully covers and the red is exactly `(0.84,0.06,0.06)` after fitz's color pipeline — same values reportlab used, so pixels match.
4. Encode every glyph via WinAnsi consistently, so text pixels are deterministic across runs (a machine/locale-dependent glyph would flip the diff).


# Appendix C

## Track 3 — Base-14 Fonts, AFM Metrics, Encoding & Text Measurement

This is the failure-prone core of the reportlab replacement. Everything the writer draws is
positioned text (`drawString`/`drawCentredString`/`drawRightString`) plus rectangles/lines, and
**box geometry is derived from text width** (`layout.stringWidth`, `_fit_header`, `wrap`). A width
that is off by a few units, or a glyph encoded to the wrong byte, silently shifts the header/clip
math and can push red ink past the box border onto linework — which the hard pixel-diff invariant
(`verify.py`) then reports as a page FAIL. So encoding and metrics must be *exactly* right and
*exactly* consistent with the bytes actually drawn.

### 3.1 The 14 standard ("Core 14") fonts

ISO 32000-1 §9.6.2.2 guarantees every conforming reader ships these 14 without embedding:

| Family | Members |
|---|---|
| Helvetica | `Helvetica`, `Helvetica-Bold`, `Helvetica-Oblique`, `Helvetica-BoldOblique` |
| Times | `Times-Roman`, `Times-Bold`, `Times-Italic`, `Times-BoldItalic` |
| Courier | `Courier`, `Courier-Bold`, `Courier-Oblique`, `Courier-BoldOblique` |
| Symbol / Dingbats | `Symbol`, `ZapfDingbats` |

**This app only uses `Helvetica`, `Helvetica-Bold`, and `Helvetica-Oblique`** (grep: `layout.py`
header=`Helvetica-Bold` 9.2 / body=`Helvetica` 7.7; `stamp.py`; `reports.py`; `transmittal.py`;
`draft.py`; `fieldpro.py` uses `Helvetica-Oblique` for footnotes). Ship width tables for those
three; add `Courier`/`Times` only if a monospaced/serif table surface appears. Symbol and
ZapfDingbats are **not** used — and the app deliberately dodges them: `reports._CHECK_GLYPH = "X"`
(an ASCII glyph), not a Dingbats check mark, so no non-WinAnsi font is ever needed. Keep it that way.

Because these are standard fonts, `/FontFile` is **never** present — no embedding, no descriptor
required (pre-PDF-2.0). PyMuPDF renders them from its own base-14 substitutes; output must render
identically across Acrobat/PyMuPDF, which is guaranteed only if we use their standard names + a
named standard encoding.

### 3.2 The `/Font` dictionary for a non-embedded Type1 base font

```
5 0 obj                          % one per (family) actually used
<< /Type /Font
   /Subtype /Type1
   /BaseFont /Helvetica-Bold     % exact standard name, case-sensitive
   /Encoding /WinAnsiEncoding    % named encoding — see §3.3
>>
endobj
```

Referenced from each page's resources:

```
/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >>
```

Notes / expectations:
- For the standard 14, `/FirstChar`, `/LastChar`, `/Widths`, and `/FontDescriptor` **may be
  omitted** (ISO 32000-1 §9.6.2.2). This is legal and is what a minimal correct writer emits.
- PDF 2.0 and **PDF/A** require those arrays even for standard fonts; **veraPDF** (the reference
  PDF/A validator) will flag their absence. The app is **not** PDF/A, so omit them — but record this
  as a known non-conformance so a future PDF/A goal doesn't get blindsided.
- Do **not** put `/Encoding` on `Symbol`/`ZapfDingbats` if ever added — they carry built-in
  encodings; a `/WinAnsiEncoding` override corrupts them.

### 3.3 StandardEncoding vs WinAnsiEncoding vs `/Differences`

- **StandardEncoding** is the Type1 default when `/Encoding` is *omitted*. It **lacks** the em dash
  at any byte we would emit, and puts the apostrophe/quotes at *different* codes than we expect.
  **Never rely on the default.**
- **`/WinAnsiEncoding`** = the reader's built-in table equivalent to Windows CP-1252 (ISO 32000-1
  Annex D, Table D.2). It covers **every glyph this app draws** (Latin letters/digits/punct, em/en
  dash, ellipsis, middle dot, degree, plus-minus, curly quotes, bullet, fractions, `Ø`). **Use this
  named encoding — no `/Differences` needed.**
- **`/Differences`** array is only required for glyphs *outside* WinAnsi (e.g. a Dingbats check).
  The app has none, so skip it. (If a special glyph is ever introduced, add a `/Differences` remap
  onto an unused byte rather than switching fonts.)

WinAnsiEncoding differs from PDF's `/StandardEncoding` **and** from ISO-8859-1 (Latin-1). The
dangerous divergence is the **0x80–0x9F band**: Latin-1 reserves it for C1 control codes, but
CP-1252/WinAnsi place printable punctuation there (em dash, en dash, ellipsis, curly quotes,
bullet). A naive `str.encode("latin-1")` **raises** on these (they are U+20xx, > 0xFF) or, with
`errors="ignore"`, **silently deletes them**.

### 3.4 Exact WinAnsi mapping for every character this app emits

Decimal / hex are the **single byte to write in the content-stream string**. "@Latin-1?" = "does
this byte sit at the character's Unicode low-byte position?" — **No** means a Latin-1-based encoder
mis-handles it. WX columns are the Adobe Core-14 AFM advance widths (1000-unit em).

| Char | Unicode | AFM glyph name | WinAnsi dec | WinAnsi hex | Helv WX | Helv-Bold WX | @Latin-1? | Used by |
|---|---|---|---|---|---|---|---|---|
| space | U+0020 | `space` | 32 | 0x20 | 278 | 278 | yes | everywhere |
| `"` | U+0022 | `quotedbl` | 34 | 0x22 | 355 | 474 | yes | inch marks, quotes |
| `'` | U+0027 | `quotesingle` | 39 | 0x27 | 191 | 238 | yes | foot marks `12'` |
| `-` | U+002D | `hyphen` | 45 | 0x2D | 333 | 333 | yes | sheet nums, ranges |
| `·` | U+00B7 | `periodcentered` | 183 | 0xB7 | 278 | 278 | **yes** | status suffix ` · ANSWERED` |
| `°` | U+00B0 | `degree` | 176 | 0xB0 | 400 | 400 | **yes** | `fieldpro` angles/temps |
| `±` | U+00B1 | `plusminus` | 177 | 0xB1 | 584 | 584 | **yes** | `fieldpro` tolerances |
| `Ø` | U+00D8 | `Oslash` | 216 | 0xD8 | 778 | 778 | **yes** | pipe diameter |
| `½ ¼ ¾` | U+00BD/BC/BE | `onehalf`… | 189/188/190 | 0xBD/BC/BE | 834 | 889 | **yes** | dimensions |
| `—` | U+2014 | `emdash` | **151** | **0x97** | 1000 | 1000 | **NO** | header `RFI NNN — TITLE` (ubiquitous) |
| `–` | U+2013 | `endash` | **150** | **0x96** | 556 | 556 | **NO** | ranges |
| `…` | U+2026 | `ellipsis` | **133** | **0x85** | 1000 | 1000 | **NO** | `clip()` / `_fit_header` truncation |
| `•` | U+2022 | `bullet` | **149** | **0x95** | 350 | 350 | **NO** | list markers |
| `'` | U+2019 | `quoteright` | **146** | **0x92** | 222 | 278 | **NO** | smart apostrophe |
| `'` | U+2018 | `quoteleft` | **145** | **0x91** | 222 | 278 | **NO** | smart quote |
| `"` | U+201C | `quotedblleft` | **147** | **0x93** | 333 | 500 | **NO** | smart quote |
| `"` | U+201D | `quotedblright` | **148** | **0x94** | 333 | 500 | **NO** | smart quote |

The five **bold "NO" rows in 0x80–0x9F are the whole ballgame**: the em dash (in *every* stamped
header) and the ellipsis (every clipped string) live here. `weaver.py` already normalizes smart
quotes to straight ASCII, and `reports._latin()` runs report text through `cp1252` — but the
**stamp path (`layout.py`→`stamp.py`) passes raw `—`/`…` straight to the drawing call with
no sanitizer**. The from-scratch encoder is therefore the *only* defense on the note-box path.

Full-coverage requirement: don't hand-list only these — build the complete `unicode → (winansi_byte,
glyph_name)` table for all of CP-1252 (0x20–0xFF plus the 0x80–0x9F punctuation) so arbitrary RFI/
report text renders. Adobe's canonical source is `bestfit1252.txt` (unicode.org) and ISO 32000-1
Annex D Table D.2.

### 3.5 AFM Core-14 metrics — where widths come from and how to key them

- Widths come from the **Adobe Core-14 AFM files** (`Helvetica.afm`, `Helvetica-Bold.afm`,
  `Helvetica-Oblique.afm`), format per **Adobe Tech Note #5004 (AFM Spec)**. Every glyph line is
  `C <code> ; WX <width> ; N <glyphname> ; B …`. All values are in a **1000-unit em**; rendered
  advance = `WX * size / 1000`.
- **Widths MUST be keyed by glyph NAME, never by the AFM `C` column.** The `C` field is the glyph's
  code in **StandardEncoding**, which is *not* WinAnsi. Proof (from the real Helvetica AFM):

  | glyph | AFM `C` (Standard) | WinAnsi byte | collision risk |
  |---|---|---|---|
  | `emdash` | 208 (0xD0) | 151 (0x97) | totally different code |
  | `periodcentered` | 180 (0xB4) | 183 (0xB7) | — |
  | `bullet` | 183 (0xB3) | 149 (0x95) | **`C`=183 == periodcentered's WinAnsi byte** |
  | `ellipsis` | 188 (0xBC) | 133 (0x85) | — |
  | `quotesingle` | 169 (0xA9) | 39 (0x27) | — |
  | `quoteright` | 39 (0x27) | 146 (0x92) | **`C`=39 == quotesingle's WinAnsi byte** |

  If you build the width lookup as `C → WX` and index it by the WinAnsi byte you output, the middle
  dot borrows the bullet's width and the apostrophe borrows quotesingle's — every upper-range width
  is quietly wrong. This is the same class of bug as pdfkit #137. **Design: one table
  `char → (byte, glyphname)`, a second `glyphname → WX` per font; width sums the `WX`.**
- **Per-font tables are mandatory.** Helvetica ≠ Helvetica-Bold: e.g. `quotedbl` 355→474, `R`
  722→722 (same) but `f`,`t`,`I`,`'` differ. The header is **Bold**, the body is **Regular** — a
  single shared table corrupts header centering/clipping.
- **Kerning: skip it.** The AFM files carry `KPX` pair-kerning data, but reportlab's `stringWidth`
  does **not** apply it and simple `Tj` text shows unkerned glyphs. To keep widths identical to the
  current (reportlab-baselined) placement/clip results, the from-scratch `stringWidth` must **also**
  ignore kerning. State this explicitly so nobody "improves" it and re-baselines every golden map.

### 3.6 `stringWidth(text, font, size)` — algorithm spec

```
def string_width(text, font, size):          # returns float points, no rounding
    widths   = WX[font]                       # {glyphname: int}  per-font, 1000-em
    to_glyph = CHAR_TO_WINANSI                # {unicode_char: (byte, glyphname)}
    total = 0
    for ch in text:
        g = to_glyph.get(ch)
        if g is None:                         # unmappable → SAME fallback the writer draws
            name = FALLBACK_GLYPH             # e.g. 'question' if byte 0x3F '?' is emitted
        else:
            name = g[1]
        total += widths.get(name, widths[FALLBACK_GLYPH])
    return total * size / 1000.0
```

Hard requirements:
1. **The fallback glyph used for width MUST equal the byte actually written.** `reports._latin`
   maps unmappables to `'?'` via `cp1252 "replace"`; match that (width of `question`, WX 556).
   If layout measures a dropped char as width 0 but the writer emits `?`, the box under-sizes and
   text overruns the border → verification FAIL.
2. **Return a float; round only at geometry boundaries.** `layout.find_spot` rounds placement to
   0.1 pt and `verify.py` tolerates ±1 px at 90 dpi; premature rounding inside the width sum can
   drift a header edge across a frame line. Preserve full precision, matching reportlab's float
   return.
3. Aim for **bit-parity with reportlab's `stringWidth`** while both coexist: a test that asserts
   `abs(new - rl) < 1e-9` over a text corpus is the cheapest correctness guarantee, and it can be
   deleted when reportlab is removed.

Sanity anchors (Helvetica, from the real AFM): `space`=278, `hyphen`=333, `emdash`=1000,
`ellipsis`=1000, `periodcentered`=278, `degree`=400, `zero`=556, `M`=833, `I`=278,
`quotedbl`=355, `quotesingle`=191.

### 3.7 Greedy word-wrap spec (must reproduce `layout.wrap`)

```
def wrap(text, font, size, width):
    lines, line = [], ""
    for word in text.split():                 # collapses all whitespace runs
        trial = (line + " " + word).strip()
        if string_width(trial, font, size) <= width:
            line = trial
        else:
            if line: lines.append(line)
            line = word                        # word starts a fresh line
    if line: lines.append(line)
    return lines
```

- **Greedy, first-fit, no hyphenation** — identical to the current implementation; do not "improve"
  it or line counts change and box heights shift.
- The measured trial **includes the joining space** (width 278 @1000). Getting the space width wrong
  systematically mis-wraps every multi-word paragraph.
- **Known latent risk to preserve/flag:** a single token wider than `width` is placed on its own
  line and *overflows* — `wrap` has no guard. In a note body that could draw red past the inner
  border onto linework → verification FAIL. The header path is protected by `_fit_header` (trims the
  title with `…`, re-appending the ` · STATUS` suffix so it's never clipped); the body path is not.
  The port must keep `_fit_header`'s two measurements exact, since it is what stops long titles from
  failing verification.

### 3.8 Centering, right-align, and the text operator

reportlab primitives the app relies on map to trivial content-stream math:

```
drawString(x, y, s):          x_left = x
drawCentredString(xc, y, s):  x_left = xc - string_width(s)/2      # draft.py N arrow, plate labels
drawRightString(xr, y, s):    x_left = xr - string_width(s)        # reports/transmittal footers
```

Emit:

```
BT /F1 9.2 Tf 1 0 0 rg  x_left  y  Td  (…escaped bytes…) Tj ET
```

`y` is the **baseline** (same convention as reportlab). `1 0 0 rg` = the app's red
`RGB(0.84,0.06,0.06)` is set via `rg`/`RG`; the box uses `re`+`S`/`f`.

**Literal-string escaping (ISO 32000-1 §7.3.4.2) is a silent-corruption trap.** Inside `( … )` you
MUST backslash-escape `(`, `)`, and `\`. An RFI title like `RFI 12 (REV A)` or a Windows path in a
report will otherwise unbalance the parens and produce a corrupt/blank content stream → the page's
only-intended-change invariant fails (or the box renders empty and *passes*, shipping a blank note).
reportlab handles this today; the writer must. Bytes ≥ 0x80 may be written raw in a byte string (or
as `\ddd` octal) — both are fine; just be consistent.

### 3.9 Where a wrong encoding or metric silently corrupts output

| # | Mistake | Silent symptom |
|---|---|---|
| 1 | Key widths by AFM `C` not glyph name | Upper-range widths wrong (dot↔bullet, `'`↔`'`); headers mis-clip |
| 2 | `str.encode("latin-1")` | em dash/ellipsis/curly quotes raise or vanish → `RFI 001  TITLE` |
| 3 | `cp1252` undefined slots (0x81,0x8D,0x8F,0x90,0x9D) | `encode` raises; must define them (WinAnsi maps to bullet) or use explicit table |
| 4 | Layout width ≠ byte drawn (fallback mismatch) | Box under-sizes; red overruns border → **verify FAIL** |
| 5 | Unescaped `( ) \` in strings | Corrupt stream → blank/garbled page (may pass as blank note) |
| 6 | Applying KPX kerning | Widths drift from golden baselines; headers re-clip |
| 7 | Shared Helvetica table for Bold header | Header centering/`_fit_header` off by ~5–15% |
| 8 | Missing-glyph width defaults to 0 | Overlapping/overflowing ink; verify FAIL |
| 9 | Rounding inside the width sum | ±1 px drift grazes frame lines → false-FAIL flakiness |
| 10 | Degree/±/middle-dot "work" under latin-1 (they're at Latin-1 positions) | **False confidence** — masks the encoder bug until the first em dash |

### 3.10 Recommended data-generation approach

Do **not** hand-type width tables. At build time, parse Adobe's canonical AFMs (Tech Note #5004
format) for the three Helvetica variants, emit generated Python dicts (`WX_HELVETICA = {...}`,
etc.), and check a SHA against the source AFMs. Ship the generated module. Add tests asserting the
§3.6 sanity anchors and (while reportlab is still present) `string_width == reportlab.stringWidth`
over a corpus including `— · … ° ± " ' ( )`. The generated `char→(byte,glyphname)` table comes from
`bestfit1252.txt` + Annex D. This turns a transcription-error minefield into a build-time,
checksummed artifact.


# Appendix D

## Track 4 — Reportlab API-Surface Audit: the mini-pdf writer's required public surface

This is a complete, code-verified inventory of every reportlab symbol the Planloom package actually touches. Six modules import reportlab directly (`stamp.py`, `layout.py`, `transmittal.py`, `reports.py`, `fieldpro.py`, `draft.py`); four more (`resolution.py`, `crewpass.py`, `daybook.py`, `submittal.py`) reach reportlab **only** by delegating to `transmittal.table_pdf`. Nothing else in the package produces PDFs through reportlab. (Test files import `reportlab.pdfgen.canvas` and `A4/landscape/letter` to fabricate fixture PDFs — they are not app surface, but the replacement must keep those fixtures buildable or the tests get rewritten.)

The surface splits cleanly into **two products**:

1. **A direct-canvas "mini-pdf" writer** — `pdfgen.canvas.Canvas` plus `pdfmetrics.stringWidth`. This is what stamps note boxes (`stamp.py`), fits headers (`layout.py`), paints forms and KPI pages (`reports.py`), and draws the Loft plate and stake-package sheet (`draft.py`, `fieldpro.py`). It is imperative "put ink here" drawing.
2. **A platypus flow/table engine** — `SimpleDocTemplate`, `Table`, `TableStyle`, `Paragraph`, `Spacer`, `HRFlowable`, `ParagraphStyle`. This is used **only** in `transmittal.py` (and `fieldpro.py`'s As-Staked Ledger), but it is the single most-reused output path in the app (every log/register/snapshot table funnels through `table_pdf`). It is the hard part of this track.

A critical de-risking finding for the pixel-diff invariant: **reportlab does NOT perform the stamp's rotation math.** In `stamp.py` the reportlab canvas always draws the overlay *upright* at `pagesize=(view_w, view_h)`; the `/Rotate 90/180/270` transform that must match fitz lives in `stamp._viewer_to_media` and is applied by **pypdf** `Transformation`/`merge_transformed_page` — which stays. So the mini-writer only has to emit a single correct, upright page; it never has to reproduce rotation-matched geometry itself.

### The authoritative table (reportlab API → used by → purpose → complexity)

| reportlab API | Used by (module) | Purpose | Replacement complexity |
|---|---|---|---|
| `pdfgen.canvas.Canvas(buf, pagesize=(w,h))` | stamp, transmittal, reports, fieldpro, draft | Root of every direct-drawn PDF; buffer + page size | **moderate** (own doc/page/xref/content-stream writer) |
| `.setFont(name, size)` | all six | Select Helvetica / Helvetica-Bold / Helvetica-Oblique | trivial (`/F# size Tf`) |
| `.drawString(x, y, s)` | all six | Left-anchored text | moderate (BT/Td/Tj + string escaping + WinAnsi) |
| `.drawCentredString(x, y, s)` | draft | Centred text (title strip, north arrow, scale bar) | trivial (needs `stringWidth`) |
| `.drawRightString(x, y, s)` | transmittal, reports | Right-anchored "Page X of Y" / date | trivial (needs `stringWidth`) |
| `.rect(x, y, w, h, stroke, fill)` | stamp, reports, fieldpro, draft | Note box, KPI/budget boxes, checkboxes, legend swatches, border, scale bar | trivial (`re` + `S`/`f`/`B`) |
| `.line(x0,y0,x1,y1)` | transmittal, reports, fieldpro, draft | Rules, footers, title-strip dividers, vector linework | trivial (`m`/`l`/`S`) |
| `.circle(x,y,r, stroke, fill)` | draft, fieldpro | Plotted circles, pins, north arrow | moderate (4-Bézier arc approximation) |
| `.arc(x0,y0,x1,y1, start, extent)` | draft | Loft arc render op | **hard** (multi-segment Bézier arc from bbox+angles; match reportlab's decomposition) |
| `.ellipse(x0,y0,x1,y1)` | draft | Loft ellipse render op | moderate (4-Bézier ellipse) |
| `.setFillColorRGB(r,g,b)` / `.setStrokeColorRGB` | stamp, reports, fieldpro, draft | Flat RGB ink | trivial (`rg`/`RG`) |
| `.setFillColor(color)` / `.setStrokeColor(color)` | transmittal, reports, fieldpro, draft | Same, via a `colors.Color`/`HexColor` object | trivial (unwrap to rgb) |
| `.setLineWidth(w)` | all but layout | Stroke weight | trivial (`w`) |
| `.setDash([...])` | draft | Linetype dash patterns; `setDash([])` resets to solid | moderate (`[...] phase d`) |
| `.translate(dx,dy)` / `.rotate(deg)` | draft | Rotated dimension/leader text | moderate (`cm` matrix concat) |
| `.saveState()` / `.restoreState()` | transmittal, draft | Isolate footer + rotated-text + clipped-content state | moderate (`q`/`Q`; must snapshot graphics state) |
| `.beginPath()` + `.clipPath(p, stroke, fill)`; `path.rect(...)` | draft | Clip plate content to the drawing window so a non-fit can't bleed into the title strip | moderate (`re W n`) |
| `.drawImage(ImageReader, x,y, width, height)` | fieldpro | Plan-thumbnail raster on the stake sheet (best-effort, `try/except`) | **hard** (image XObject: PNG/JPEG decode + `/DCTDecode` or `/FlateDecode`, `Do`) — or drop to the existing "no thumbnail" fallback |
| `.stringWidth(s, font, size)` (canvas method) | fieldpro | Legend column advance | trivial once metrics exist |
| `.setTitle(s)` | reports, fieldpro, draft | `/Title` in doc info dict | trivial |
| `.showPage()` | all six | End page / start next | moderate (flush content stream, new page obj) |
| `.save()` | all six | Finalize document | moderate (xref table + trailer) |
| `pdfbase.pdfmetrics.stringWidth(s, font, size)` (module fn) | **layout**, reports | Header-fit trimming (`_fit_header`), body word-wrap (`wrap`), KPI key/value advance | **hard** — needs real Adobe Core-14 AFM widths, byte-identical to reportlab (see pitfalls) |
| `lib.pagesizes.letter` | transmittal, reports, fieldpro, draft | 612×792 pt page constant | trivial (a tuple) |
| `lib.pagesizes.landscape(size)` | fieldpro | Landscape As-Staked Ledger | trivial (swap w/h) |
| `lib.colors.Color(r,g,b)` | transmittal, reports | Palette constants (ACCENT, INK, zebra, gridlines) | trivial (small dataclass) |
| `lib.colors.HexColor("#rrggbb")` | fieldpro, draft | Layer colors from user hex strings; raises `ValueError` on bad input (caught) | trivial (hex→rgb, raise ValueError) |
| `lib.colors.white` / `lib.colors.black` | transmittal, reports, draft | Named colors | trivial |
| `lib.utils.simpleSplit(text, font, size, width)` | reports | Wrap form-field text to width | moderate (reimplement with own `stringWidth`) |
| `lib.utils.ImageReader(bytesio)` | fieldpro | Wrap PNG bytes for `drawImage` | pairs with `drawImage` (**hard**/optional) |
| `lib.styles.ParagraphStyle(...)` | transmittal, fieldpro | Font/size/leading/color/spacing for flowables | moderate (data holder consumed by the flow engine) |
| `platypus.Paragraph(xml, style)` | transmittal, fieldpro | Wrapped, `<br/>`-aware cell/title text | **hard** (mini-HTML: escape, `<br/>`, measure+wrap to width, multi-line height) |
| `platypus.Table(data, colWidths, repeatRows=1, style=)` + `.setStyle` | transmittal, fieldpro | The core auto-laid table | **hard** (row-height from tallest cell, column widths, header `repeatRows` on every page break) |
| `platypus.TableStyle([...commands...])` + `.add(...)` | transmittal, fieldpro | BACKGROUND, TEXTCOLOR, GRID, BOX, LINEBELOW, VALIGN, ALIGN, {L,R,TOP,BOTTOM}PADDING, per-row zebra | **hard** (cell-range command model + paint order) |
| `platypus.Spacer(w,h)` | fieldpro | Vertical gap in the story | trivial |
| `platypus.HRFlowable(width, thickness, color, spaceBefore, spaceAfter)` | transmittal, fieldpro | Red rule under the title | trivial |
| `platypus.SimpleDocTemplate(buf, pagesize, margins, title=).build(story, canvasmaker=)` | transmittal, fieldpro | Page frame + margins + **multi-page pagination** of the flowable story; the `canvasmaker` hook injects `_NumberedCanvas` | **hard** (frame fill, flowable split across pages, canvasmaker plumbing) |
| **`_NumberedCanvas(canvas.Canvas)` internals** — `self.__dict__` snapshot/restore, `self._startPage()`, `self._pagesize`, `Canvas.showPage(self)`, `Canvas.save(self)` | transmittal (imported by reports + fieldpro) | Two-pass "Page X of Y": buffer per-page state on `showPage`, replay on `save` to stamp the known total | **hard** (the writer must expose an equivalent deferred-footer / total-page mechanism) |

Not used anywhere in the app (so the writer can skip them): `beginText`/`textObject`/`textLines`, `bezier`, `wedge`, `roundRect`, `linearGradient`, `setLineCap`/`setLineJoin`, `lib.units` (inch/mm/cm — the code multiplies by `72.0` inline), `pagesizes.A4` (tests only), `LongTable`, `getSampleStyleSheet`, `PageBreak`/`CondPageBreak`/`KeepTogether`/`FrameBreak`, `onFirstPage`/`onLaterPages`, and any `platypus.Image`/`Frame`/`PageTemplate`/`BaseDocTemplate`. Encoding is implicitly WinAnsi (`reports._latin` pre-forces `cp1252`); no `pdfbase.pdfdoc`, no font embedding, no `TTFont`/`registerFont` — **base-14 only, exactly Helvetica / Helvetica-Bold / Helvetica-Oblique.**

### Fonts and metrics — the precise contract

- Three base-14 faces appear: **Helvetica**, **Helvetica-Bold** (both drawn *and measured*), and **Helvetica-Oblique** (drawn only — `fieldpro` footnotes, `draft` never). So the PDF needs three `/Type1 /BaseFont` resources with `/Encoding /WinAnsiEncoding`, and the metric engine needs AFM width tables for **Helvetica and Helvetica-Bold only**.
- `stringWidth` is called on Helvetica and Helvetica-Bold at sizes 7.5–26 pt. It is `sum(width[char]/1000 * size)` with the Adobe AFM per-glyph widths; **reportlab ignores kerning by default**, so the reimplementation must ignore kerning too or line breaks diverge.
- WinAnsi is load-bearing: the note text carries `—` (em dash, 0x97), `·` (middle dot, 0xB7), `…` (ellipsis, 0x85) — all present in WinAnsiEncoding and in the AFM width table, so they measure and render. Keep `_latin`'s cp1252 coercion.

### The genuinely painful things to reproduce

- **Table auto-layout + row wrapping + `repeatRows` header** (`transmittal.table_pdf`): compute each row's height as the max wrapped-cell height at that column width, split the table across pages when the running height exceeds the frame, and re-emit the header row at the top of every page. This is the crux; `_auto_widths` (content-weighted column sizing) feeds it.
- **Multi-page flow / pagination** (`SimpleDocTemplate.build`): fill a frame top-down with title → subtitle → HRFlowable → Table, breaking flowables across pages. Must be deterministic and match the margins (`leftMargin=rightMargin=topMargin=54`, `bottomMargin=54+18`).
- **`_NumberedCanvas` two-pass total-page count**: the whole "Page X of Y" feature depends on snapshotting per-page canvas state and replaying it after the total is known. This is imported *by name* from `transmittal` into `reports.py` and `fieldpro.py`, so the replacement must keep the class (or a drop-in with the same constructor `footer_note=`, `count_holder=` and `showPage`/`save` semantics).
- **`Paragraph` mini-HTML**: `_cell_text` emits XML-escaped text with `<br/>` breaks; the paragraph must parse that, wrap to the column width using the same metrics, and report a height. Full HTML is not needed (only `<br/>` and entity escapes), which bounds the work.
- **Arc/ellipse Bézier decomposition** (`draft.plate_pdf`): `c.arc`/`c.ellipse`/`c.circle` must match reportlab's curve approximation closely enough that the plate renders identically; the plate is not pixel-diff-gated the way stamps are, but it is a user-facing deliverable.
- **`drawImage` raster embedding** (`fieldpro` thumbnail): the only image path; it is best-effort inside `try/except` and already has a graphical "no thumbnail" fallback, so it can be deferred or dropped without breaking the deliverable.


# Appendix E

## Track 5 — Validation, QA & the Pixel-Diff Verification Gate

This track owns the question **"how do we prove the from-scratch `minipdf` writer is a safe drop-in for reportlab?"** It has two layers: (A) generic, industry-standard PDF validation (structural correctness of the bytes we emit) and (B) the app-specific acceptance gate — the hard invariant that `fitz` renders our output pixel-identically (within tolerance) to today's reportlab output, on every stamped page, forever.

The governing invariant is **Invariant 4** (`CLAUDE.md`): *"every stamped page's only rendered change is the box itself (diff > 25 gray levels), nothing pre-existing under any box footprint, untouched pages pixel-identical."* This is enforced today by `rfi_stamper/verify.py`; the writer swap must not weaken it, and this track's job is to guarantee that.

### 0. Ground truth: what the current gate actually does

`rfi_stamper/verify.py::verify()` is the shipped enforcement. It is the spec the new writer must satisfy, so quote it precisely:

- Renders **every page** of both the pre-stamp plan and the post-stamp output to **grayscale** pixmaps via `doc[p].get_pixmap(dpi=90, colorspace=fitz.csGRAY, alpha=False)` and reshapes `pix.samples` to a `uint8` H×W array.
- Diff mask `d = |pre - post| > DIFF_THRESH` (`DIFF_THRESH = 25` gray levels, from `layout.py`).
- **Untouched pages** (no placements): require `d.sum() == 0` — *bit-for-bit identical rendering*, zero pixels changed. This is the strictest clause and the one most sensitive to writer differences.
- **Stamped pages**: build a `mask` from each box's exact cleared window (`b["occ"]`, the pixel window the finder cleared — deliberately *not* recomputed, per the "±1 px drift" gotcha). Require:
  - `under == 0` — no pre-existing dark content (`pre < DARK_THRESH`, `DARK_THRESH = 225`) under any box footprint;
  - `outside == 0` — zero changed pixels outside the box masks;
  - `inside > 300` — the box actually rendered (sanity floor).
- Appendix pages (added beyond the original page count) are logged `OK` and not diffed.

**Consequence for the writer swap:** the "untouched page = 0 px changed" clause means that for any plan page we do **not** stamp, the output bytes for that page are passed through unchanged by `pypdf` (they never touch our writer) — so those pages are automatically safe. The risk surface is entirely the **overlay content stream** our writer produces and how `fitz` rasterizes it: box strokes, fills, and text. If our overlay renders even one glyph a half-pixel differently such that a pixel crosses the 25-level threshold *outside* the `occ` window, verification FAILs loudly. That is the whole ballgame.

### 1. Layer A — Industry-standard PDF structural validation

Before we even get to pixels, the bytes must be a valid PDF. A production writer is expected to pass the standard toolchain that every PDF library is measured against. Run all of these in CI against the golden corpus (§4).

| Tool | Invocation | What it catches | Why it matters here |
|---|---|---|---|
| **qpdf** (`--check`) | `qpdf --check out.pdf` | Syntactic validity: broken/ës xref tables & xref **streams** ("cross-reference stream data has the wrong size"), objects qpdf had to **reconstruct** ("xref not found, attempting reconstruction"), damaged object streams. A clean `--check` means syntactically valid (though *not* that content conforms). | Our #1 gate for "did we write the xref table and offsets correctly." Any "attempting to reconstruct cross-reference table" warning = our `startxref`/offsets are wrong. |
| **qpdf** (`--json`, `--show-xref`) | `qpdf --show-xref out.pdf` | Dumps the object → byte-offset table qpdf computed; compare against what we intended. | Pinpoints a single wrong `/Length` or offset. |
| **mutool** (MuPDF, same engine as fitz) | `mutool clean -s -gg out.pdf fixed.pdf; mutool info out.pdf` | `clean` re-serializes and reports structural problems; `info` lists pages, fonts, and page geometry. Because MuPDF **is** fitz, this is the most predictive of our runtime renderer's opinion. | If `mutool clean` warns or "repairs," fitz saw something wrong. Treat mutool warnings as blocking. |
| **Ghostscript** | `gs -o /dev/null -sDEVICE=nullpage -dPDFSTOPONERROR out.pdf` | A second, independent interpreter. Stops on malformed content streams, unbalanced graphics state, bad operators. | Catches things MuPDF is lenient about; guards against shipping a file that a client's Adobe/GS-based viewer chokes on. |
| **pdftotext** (Poppler) | `pdftotext out.pdf - ` | Text round-trips out with correct characters, spacing, and reading order. | Verifies our `Tj`/`TJ` text operators and WinAnsi encoding are correct at the *content* level (complements the pixel gate, which can't tell "O" from "0"). |
| **veraPDF** (optional / aspirational) | `verapdf --flavour <auto> out.pdf` | Formal PDF/A "shall" rules (validation profiles). Reports failed checks; `--format json`. | We are **not** targeting PDF/A, so this is a nice-to-have stretch goal, not a gate. Documented here so the orchestrator can decide. |
| **Adobe Acrobat Preflight** | manual, GUI | The industry reference preflight. | Manual spot-check on the golden corpus at release; not automatable offline. |

**Common structural failures a hand-rolled writer produces (the checklist to defend against):**

1. **Wrong xref byte offsets.** The classic. Every object's offset in the xref table must be the exact byte position of its `N 0 obj` from file start. Off-by-one from a stray `\r` or counting characters instead of bytes → qpdf "attempting reconstruction," some viewers show a blank page.
2. **Wrong `/Length` on a stream.** Must equal the exact byte count between `stream\n` and `\nendstream`. Wrong length truncates or over-reads the content stream → missing box, or garbage operators → GS error.
3. **Missing/!misplaced `%%EOF`** or missing `startxref` line, or `startxref` pointing at the wrong byte. File "damaged."
4. **Dangling / free object references** — a `/Contents 5 0 R` where object 5 doesn't exist, or a trailer `/Root` pointing nowhere.
5. **Unbalanced graphics state `q`/`Q`.** Every `q` (save) needs a matching `Q` (restore). Unbalanced → GS/Preflight error, and state leaks corrupt later drawing. (Our overlay is one page, one content stream — keep it strictly balanced.)
6. **Unbalanced `BT`/`ET`** (begin/end text) or text operators outside `BT…ET`.
7. **Trailer errors:** missing `/Size` (must be highest object number + 1), missing `/Root`.
8. **Encoding drift:** a byte > 127 written raw when the font is declared `/WinAnsiEncoding` but the glyph isn't at that codepoint → wrong character or blank.

**Structural acceptance for Layer A:** `qpdf --check` clean (no warnings), `mutool clean` no repairs, `gs` no errors, `pdftotext` exact text match — on **100% of the golden corpus**, in CI.

### 2. Layer B — The app-specific pixel-diff acceptance gate

This is the load-bearing work. The strategy is **differential rendering against the incumbent**: for a frozen corpus of documents, generate the PDF **twice** — once with today's reportlab path (the "oracle") and once with the new `minipdf` writer — render both through `fitz` to grayscale pixmaps, and require them to be pixel-identical (or within a tiny, justified tolerance).

#### 2.1 Two complementary comparison methods

**(a) Pixel diff (the primary gate)** — mirrors `verify.render_gray` exactly:

```python
def gray(doc, i, dpi=90):
    pix = doc[i].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)

old = gray(fitz.open(reportlab_out), i)
new = gray(fitz.open(minipdf_out),  i)
assert old.shape == new.shape                      # same media/crop → same pixmap size
diff = np.abs(old.astype(np.int16) - new.astype(np.int16))
changed = int((diff > 25).sum())                   # 25 == verify's DIFF_THRESH
```
Acceptance tiers (see §5): ideally `changed == 0`; a justified fallback tolerates a **bounded, spatially-confined** anti-aliasing delta strictly *inside* box footprints.

**Crucially, the real gate is stronger than "old vs new looks same":** run the *actual* `verify.verify(plan, minipdf_out, placements, index)` on the new output and require `ok == True`. That directly asserts `under==0 / outside==0 / inside>300` against the *original plan*, which is what production enforces. The old-vs-new diff is the developer-facing microscope; `verify()` passing is the shipping gate.

**(b) Text round-trip via `get_text("words")`** — pixels can't distinguish `O`/`0`/`·`/`-`, so assert content + geometry:

```python
ow = fitz.open(reportlab_out)[i].get_text("words")  # (x0,y0,x1,y1,word,bno,lno,wno)
nw = fitz.open(minipdf_out)[i].get_text("words")
assert [w[4] for w in ow] == [w[4] for w in nw]                     # same words, order
for a, b in zip(ow, nw):
    assert all(abs(a[k]-b[k]) <= 0.35 for k in range(4))           # positions ≤ tol pt
```
This catches encoding bugs, glyph-width/kerning drift, and word-position drift that the width-fit logic (`layout.stringWidth`) depends on.

#### 2.2 The tightest coupling: `layout.stringWidth` (do not overlook)

`rfi_stamper/layout.py` imports `from reportlab.pdfbase.pdfmetrics import stringWidth` and uses it to **width-fit** note headers (Invariant 2 / test `#05`): it truncates the title with an ellipsis until `stringWidth(fitted, "Helvetica-Bold", 9.2) <= inner`, then appends the ` · STATUS` suffix. If the new writer ships its **own** Helvetica/Helvetica-Bold width table and it disagrees with reportlab's by even fractions of a point, the *truncation decision changes* — a header keeps or drops one more character — which moves glyphs and **changes the rasterized pixels**, failing the old-vs-new diff even though both files are individually valid.

Therefore the writer's font-metrics module and `layout`'s width function must come from the **same Adobe Core-14 AFM tables** (the canonical `Helvetica.afm` / `Helvetica-Bold.afm` char widths, in 1000-unit em space: `width_pt = sum(afm_widths[ord(c)]) / 1000 * fontsize`). Requirement: **`minipdf.string_width` must equal reportlab's `stringWidth` to ≤ 0.01 pt for every character in our glyph repertoire** (verified by a direct unit test over the corpus's actual strings). If they match, the width-fit branch takes identical decisions and this whole class of failure disappears. This is the single most important correctness link in the swap.

### 3. The concrete TEST MATRIX

Each row is generated by **both** writers and subjected to: Layer-A tools, old↔new pixel diff, `get_text("words")` round-trip, and (where a plan exists) the real `verify.verify`. Rendered at the production **90 dpi**, plus a **300 dpi** cross-check to surface sub-pixel drift the 90-dpi grid hides.

| # | Case | What it stresses | Pass condition |
|---|---|---|---|
| 1 | **Empty / untouched page** (plan page with no placement) | Passthrough; untouched-page clause | `verify` reports `0px changed`; old↔new identical |
| 2 | **Single note box**, short header + 2-line body | Rectangle stroke (RGB .84,.06,.06), white fill, red text; core style | old↔new `changed==0`; `verify` `under=0 outside=0 inside>300` |
| 3 | **Multiple stacked notes in one box** (`layout.make_entries` multi-record) | Multi-entry vertical stacking, box growth | words round-trip; pixel-identical |
| 4 | **Labeled appendix page** (`stamp` appendix for unplaceable/unmatched) | Full-page generated text page, not diffed by `verify` but must be structurally valid + text-correct | Layer-A clean; `pdftotext` matches; old↔new identical |
| 5 | **Full transmittal / RFI-log table**, multi-row **multi-page** (`transmittal.table_pdf`) | Positioned-text tables, row/column geometry, pagination, header repeat | per-page pixel-identical; page count matches; words round-trip |
| 6 | **Oversized single cell** (test_reb_tables `#14`, 4000-word cell) | Cell clamp `_CELL_MAX` + pagination without crash | no exception; `_cell_text` bounded; renders |
| 7 | **Landscape page** (11×17, 1224×792 like `smoke_test`) | Media/CropBox handling, wide geometry | identical |
| 8 | **/Rotate 90 overlay** (`smoke_test` page 2) | `stamp._viewer_to_media` `rotate(90).translate(tx=media_w)` convention — the field-verified gotcha | `verify` passes on the rotated page (this is the one pixel-diff *originally* caught) |
| 8b | **/Rotate 180 & 270 + CropBox inset** (test_reb_stamp `#01`) | All four rotations with `origin != 0` CropBox | `verify` OK all rotations; `inside != 0` |
| 9 | **Unusual strings**: parens `()` (must be escaped as `\(` `\)` in PDF string literals), unicode em-dash `—`, middot `·` (the status separator), curly quotes, ellipsis `…`, backslash | String-literal escaping + WinAnsi/glyph mapping; these are the exact chars our headers/bodies use | words round-trip exactly; no missing glyphs; pixel-identical |
| 10 | **Very long header that clips** (test_reb_stamp `#05`) | `layout.stringWidth`-driven truncation parity; suffix preserved | truncation identical to reportlab; header ≤ inner width; suffix intact |
| 11 | **Resolution status suffix** headers (` · ANSWERED`, ` · IN_WORK`, …) | Suffix appended after title clip (Invariant 2) | suffix never truncated; identical |
| 12 | **All base-14 usages sweep**: `reports.py`, `resolution.py` (Designer Pickup Sheet), `draft.py`, `fieldpro.py` (As-Staked Ledger), `crewpass.py`, `daybook.py` | Every module that currently emits PDF via reportlab | each module's golden PDF matches old↔new |

**Character repertoire note for cases 9/11:** the PDF string escaping rules are non-negotiable — inside a `(...)` string, `(`→`\(`, `)`→`\)`, `\`→`\\`; the em-dash `—`, `·` `·` (middot, 0xB7 in WinAnsi), `…` `…` (0x85 in WinAnsi), and curly quotes must map to their **WinAnsiEncoding** byte or the glyph vanishes. This is exactly where a hand-rolled writer breaks; the `get_text` round-trip is the tripwire.

### 4. The golden / regression corpus

- **Location:** `tests/golden/` (PDFs) + `tests/golden/expected/` (per-page rendered `.npy` pixmaps or a manifest of hashes). Keep it small and deterministic.
- **Two flavors per case:** the *fixtures* (fake plans, built by `smoke_test._draw_sheet`-style helpers) and the *expected outputs*. Because reportlab is the incumbent oracle, the cleanest approach is **oracle-on-the-fly**: in CI, generate the reportlab version at test time and diff against the minipdf version — no stored bytes needed, and it can't rot. Store only the *fixtures*. (This does require keeping reportlab installed in the **test/dev** environment during the transition even after it's removed from the shipped app — see Open Questions.)
- **Frozen-bytes fallback:** if reportlab is removed from dev too, freeze the reportlab-rendered pixmaps as `.npy` once, hash them, and diff future minipdf output against the frozen pixmaps. Risk: a fitz version bump re-rasterizes and invalidates the frozen set (mitigated by pinning the fitz version in the test env and regenerating deliberately).
- **Determinism requirements (offline policy):** the writer must emit **byte-reproducible** output — no timestamps in `/CreationDate`/`/ModDate` (or a fixed seed value like `D:20000101000000Z`), no random object ordering, no `/ID` derived from wall-clock (use a fixed/hashed `/ID`). This makes both the bytes and the pixmaps diffable and lets us assert a stable SHA-256 per golden file. No network, no font downloads (base-14 are metric-only, non-embedded). Seed anything stochastic.

### 5. Acceptance criteria — "is the writer shippable?"

The writer ships only when **all** hold, in CI, offline, deterministically:

1. **Structural:** `qpdf --check` clean, `mutool clean` no repairs, `gs` no errors, `pdftotext` exact — on 100% of the corpus.
2. **Pixel gate (primary):** for every stamped fixture, the real `verify.verify(plan, minipdf_out, placements, index)` returns `ok == True` (`under==0`, `outside==0`, `inside>300`, untouched pages `0px`). **This is the invariant; it is non-negotiable and cannot be relaxed.**
3. **Differential parity:** old↔new grayscale diff at 90 dpi has `changed == 0` on ≥ 95% of pages, and on the remainder `changed` is (a) strictly confined **inside** box footprints, (b) ≤ a fixed small budget (proposed: ≤ 0.02% of page pixels), and (c) every such pixel is a ≤ N-level anti-aliasing delta (proposed N configurable, justified per case). **No changed pixel may ever fall outside a box footprint** — that alone fails the build. Also verified at 300 dpi to prove the delta is AA edge-fringe, not geometry error.
4. **Text parity:** `get_text("words")` word sequence identical and per-word bbox within ≤ 0.35 pt on 100% of the corpus.
5. **Metrics parity:** `minipdf.string_width == reportlab.stringWidth` to ≤ 0.01 pt across the corpus's full character set (unit test), so `layout` width-fit decisions are unchanged.
6. **Regression suite green:** `tests/smoke_test.py` (rotation-0 + /Rotate 90), `test_reb_stamp.py` (all-rotation CropBox, header fit), `test_reb_tables.py`, `test_transmittal.py`, `test_reports.py`, `test_resolution.py` all pass under `tests/run_all.py` (GUI under `xvfb-run -a`).
7. **CI wiring:** a new `tests/test_minipdf_golden.py` runs the full matrix and is added to `tests/run_all.py`; it must be self-contained (build fixtures in-process like `smoke_test`), require no display for the non-GUI parts, and touch no network.

**Recommended default:** hold the bar at **`changed == 0` at 90 dpi** for every case. Base-14 fonts are metric-defined and non-embedded, box geometry is integer-derived, and fitz renders deterministically — so byte-different-but-pixel-identical is genuinely achievable, and a strict zero removes all argument about "acceptable" AA drift. Only fall back to the bounded-tolerance tier (criterion 3) if a specific, understood glyph-hinting delta proves unavoidable, and document exactly which case and why.

### 6. Process / rollout QA

- **Shadow mode:** during transition, generate **both** outputs in the pipeline behind a flag and assert equality on real runs (a self-check that costs one extra render). Ship minipdf only after N clean shadow runs.
- **Per-module cutover:** migrate + gate one reportlab consumer at a time (`layout`/`stamp` first since they're inside the pixel gate; then `transmittal`, `reports`, `resolution`, `draft`, `fieldpro`, `crewpass`, `daybook`). Each cutover is a separate golden-diff PR.
- **PyInstaller smoke:** after the swap, rebuild `rfi_stamper.spec` on Windows (`build_windows.bat`) and re-run the CLI (`--scan-only` + full run); the `*_report.txt` must end in **PASS**. Confirm no reportlab hidden-import is still pulled in (removing it shrinks the exe and proves the dependency is truly gone).


# Appendix F

## Track 6 — Structure, Compression, Xref, Versioning & Viewer Compatibility

This track governs the *container* choices of the from-scratch "mini-pdf" writer: which PDF version to declare, how to lay out the cross-reference section, whether/how to Flate-compress streams, what (if any) metadata to emit, and how large the output should be. The overriding constraint is the project's **pixel-diff verification invariant** plus its **offline / NDA / reproducible-output** posture. The good news for this track: the writer only ever emits base-14 text, vector rectangles/lines, and flat RGB fills — none of which need any PDF ≥ 1.5 feature — so the *simplest, most universally compatible* structural choices are also the correct ones.

### 0. How the writer's output is actually consumed (this changes the calculus)

Two distinct consumption paths exist in the repo, and they impose different requirements:

| Path | Producer | Consumer of writer bytes | Final serializer | What must be byte-correct |
|---|---|---|---|---|
| **Stamp overlay** (`stamp.py`) | writer emits a 1-page transparent overlay | `pypdf.PdfReader(buf)` parses it, `merge_transformed_page` composites onto the plan page | **pypdf** `PdfWriter.write()` | The writer's **content-stream operators** (they survive verbatim); pypdf re-emits its own xref/trailer/Info, so the writer's container bytes are discarded here |
| **Standalone deliverable** (`transmittal.py`, `reports.py`, `resolution.py`, `submittal.py`, `daybook.py`, `crewpass.py`, appendix-only) | writer emits the whole document | ships directly to disk / to fitz | **the writer itself** | The **entire file structure** — header, objects, xref offsets, trailer, `/ID` |

Consequence #1: on the stamp path, pypdf is the final authority on xref style, `/Info`, and `/ID` of the delivered PDF — the writer cannot control those, so **the writer's structural choices only need to be (a) parseable by pypdf and (b) render-identical under fitz.** Consequence #2: on the standalone path, the writer's structural bytes *are* the deliverable and are what NDA/reproducibility policy applies to. Design the writer to be correct for the harder standalone case; the stamp case is then automatically satisfied.

Consequence #3 for the pixel-diff invariant: `verify.py` compares each page's **post-stamp render against its pre-stamp render of the same run** (diff must be only the box). It does **not** compare against reportlab's historical output. Therefore the new writer does **not** need glyph-for-glyph parity with reportlab — it only needs its own box to render correctly and *only* under the box footprint. Font-shape fidelity to reportlab is a non-requirement; internal render determinism is the requirement.

### 1. Target PDF version — recommend **`%PDF-1.4`**

Every feature the app uses (base-14 non-embedded fonts, `re`/`l`/`f`/`S` path ops, `rg`/`RG` flat color, `Tj`/`TJ` text, `FlateDecode`, classic xref) is valid since **PDF 1.2–1.4**. There is no reason to declare 1.5+ and doing so would only invite xref-stream expectations. ISO 32000-1:2008 (PDF 1.7) is a strict superset, so 1.4 files are trivially readable by every modern viewer, fitz, and pypdf.

- **Header:** `%PDF-1.4\n` followed immediately by a **binary marker comment** — a comment line containing ≥4 bytes > 127. Canonical: `%\xE2\xE3\xCF\xD3\n`. Omitting this makes some transfer agents/validators treat the file as text and mangle it; qpdf and Acrobat both expect it. This is a classic first-build omission.
- On the **stamp path** the declared version of the *delivered* file is governed by pypdf, which sets it to the max of the merged inputs (plan sets are commonly 1.4–1.7). The writer's 1.4 overlay never downgrades a 1.7 plan. No action needed.
- Do **not** target PDF 2.0 (ISO 32000-2): no benefit here, and slightly worse tooling ubiquity.

### 2. Cross-reference: recommend the **classic xref TABLE**, not xref streams

PDF 1.5 introduced **cross-reference streams** and **object streams** (compressing many objects into one Flate stream) to shrink files heavy in small indirect objects. Tradeoffs:

| | Classic xref table (1.0+) | Xref stream + object streams (1.5+) |
|---|---|---|
| Viewer compatibility | Universal, incl. ancient/embedded viewers | Fine in modern viewers; some legacy/print-RIP and a few govt e-filing validators choke |
| Human-debuggable | Yes — ASCII, greppable | No — binary Flate blob |
| Implementation cost | ~1 function, 20-byte fixed records | Must build the `/Type /ObjStm` packing + a `/Type /XRef` stream with `/W` field widths + `/Index` |
| Size win on THIS app | ~0 (few, large-ish objects per file) | Real only when there are *hundreds* of tiny objects |
| pypdf/fitz read support | Perfect | Perfect |

The app's documents have a *handful* of objects per page (catalog, pages, page, font, content, plus a resource dict) — object streams save essentially nothing here because the win comes from amortizing per-object overhead across *many* objects. **Recommend the classic table.** It is simpler, greppable (a real asset given the pixel-diff/repro test discipline), and maximally compatible.

Exact table format — each entry is **exactly 20 bytes**: `%010d` offset, `SP`, `%05d` generation, `SP`, type char (`n`/`f`), then a **2-byte EOL** (`\r\n` or `SP\n`). Subsection header `first count`. Byte offsets are counted from the **very start of the file** (including the `%PDF` header).

```
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
...
xref
0 6
0000000000 65535 f 
0000000015 00000 n 
0000000074 00000 n 
0000000131 00000 n 
0000000275 00000 n 
0000000400 00000 n 
trailer
<< /Size 6 /Root 1 0 R /ID [<a1b2...><a1b2...>] >>
startxref
612
%%EOF
```

Object 0 is always the free-list head `0000000000 65535 f`. `/Size` = highest object number + 1. `startxref` = byte offset of the `xref` keyword. Terminate with `%%EOF`. **The single most common defect is an off-by-N byte offset** (usually from EOL width or forgetting the header length) — pypdf will attempt an xref *reconstruction* fallback if offsets are wrong, which masks the bug on the stamp path but leaves standalone files fragile in strict viewers. Offsets must be exact; write objects to a buffer and record `buf.tell()` immediately before each `obj`.

### 3. Compression — recommend **`FlateDecode` via stdlib `zlib`**, and **skip from-scratch DEFLATE**

Content streams should be filtered with `/Filter /FlateDecode`. Use `zlib.compress(data, 9)` — this produces the zlib wrapper (2-byte header + DEFLATE + Adler-32) that `FlateDecode` expects per ISO 32000 (PDF's FlateDecode == RFC 1950 zlib, whose payload is RFC 1951 DEFLATE). The `/Length` must be the **compressed** byte count.

```
4 0 obj
<< /Length 128 /Filter /FlateDecode >>
stream
<128 bytes of zlib data>
endstream
endobj
```

- **From-scratch DEFLATE (RFC 1951) is an OPTIONAL purity exercise with no functional value here and real cost.** Unlike Tesseract/OOXML/DXF (which were replaced to remove a *dependency* or gain control), `zlib` is in the **Python standard library** — replacing it removes no third-party dependency, is not required for offline operation, and buys nothing a viewer can observe. A correct, competitive DEFLATE encoder (LZ77 match-finder + dynamic Huffman + block splitting) is ~400–700 LOC of medium-high-complexity code with a large correctness/perf surface; a naive stored-blocks-only encoder is trivial but produces *larger* output than uncompressed-with-overhead. **Recommendation: use `zlib`. If "no non-stdlib-but-still-not-ours" purity is ever demanded, note that zlib is stdlib and therefore already inside the offline boundary.**
- **When to compress:** Flate has ~11–18 bytes of fixed overhead; below ~a few hundred bytes it can *grow* a stream. A single stamped-sheet overlay content stream (a red rectangle + two short text lines) is often only 200–600 bytes, where Flate saves little or nothing. Two clean policies: **(A) always Flate** (simplest, universally supported, worst case +~15 bytes on tiny streams — negligible), or **(B) Flate only when it wins**, i.e. compress, and if `len(compressed) >= len(raw)` emit the stream raw with no `/Filter`. Recommend **(A) always Flate** for uniformity unless the golden-hash repro tests favor the readability of raw operators (see §6). Never Flate the xref table or trailer (classic table is plain ASCII by definition).

### 4. Metadata policy — **omit `/Info`, omit XMP, emit a deterministic `/ID`**

This is where Track 6 intersects the NDA + reproducible-output invariants most sharply.

- **`/Info` dictionary: omit it entirely.** ISO 32000 makes `/Info` optional. reportlab today auto-stamps `/Producer` (its name+version) and `/CreationDate`/`/ModDate` (wall-clock) — both are (a) NDA-adjacent metadata leakage and (b) nondeterministic, defeating byte-reproducible output. Emitting **no** Info dict is the cleanest fix and mirrors the intent of the existing `pdfdoctor.strip_metadata`. (If a title is ever wanted, emit only `<< /Title (...) >>` and nothing else — never Producer/dates.)
- **XMP metadata stream: do not emit one.** XMP is only mandatory for PDF/A conformance, which is **not** a goal (the app isn't validated against veraPDF/PDF-A). Skipping XMP avoids a second timestamp/tool-name leakage vector.
- **`/ID` file identifier: emit a deterministic, content-derived pair.** ISO 32000 §14.4 says `/ID` *should* be based on content + time and *recommends* (does not require) it for every file; pypdf and many workflows warn when it's absent. For reproducibility, derive it from content, **not** the clock: `id = md5(b"".join(all_content_stream_bytes) + catalog_bytes).hexdigest()`, then `/ID [ <id> <id> ]` (both elements equal for a freshly-created file — first = original, second = current, which are identical at creation). This satisfies the spec's "based on content," yields identical bytes for identical input (git-friendly, testable), and leaks nothing time- or machine-specific.
- **Note the stamp path:** pypdf re-emits its own `/Info` and `/ID` on the merged output. To keep delivered stamped PDFs equally clean/deterministic, the *pypdf* write step should also be told to suppress Producer/dates and set a fixed `/ID` (pypdf exposes metadata setters; this is a small adjacent task the orchestrator should track even though it lives outside the writer). Pixel-diff itself is indifferent to metadata bytes, but the reproducibility/NDA guarantees are not.

### 5. Linearization & incremental update — **neither is needed**

- **Linearization ("Fast Web View", Annex F / qpdf `--linearize`)** reorganizes the file so a viewer can render page 1 while byte-streaming the rest over HTTP. This is an offline desktop tool with no byte-range server in the loop — **skip it.** It would add a whole second-pass hint-table generator for zero benefit and would fight determinism.
- **Incremental update** (appending new objects + a second xref keyed by `/Prev`) exists for signing/annotating an *existing* file in place. The writer always authors *fresh* documents, and pypdf likewise does a full rewrite on merge — **skip it.** Always emit a single, complete xref section.

### 6. Expected file sizes

Sizes are dominated by the *plan content*, which the writer never touches (it passes through pypdf verbatim). The writer's own contribution is small:

| Artifact | Uncompressed (writer's contribution) | With FlateDecode | Notes |
|---|---|---|---|
| One stamped-sheet **overlay** content stream (1 box, 2 text lines) | ~250–700 B operators; full 1-page overlay file ~1.5–3 KB | content stream ~150–400 B; often a net wash on tiny streams | On the stamp path this is *discarded structure* — only the operators survive into pypdf's output; net add to the merged plan page is sub-KB |
| **Appendix** page (several stacked note boxes) | ~2–6 KB | ~1–2.5 KB | Standalone-ish; still small |
| **30-row transmittal / RFI-log** table (lines + positioned text) | content stream ~8–20 KB; file ~10–25 KB | content stream ~3–6 KB; file ~5–12 KB | This is where Flate first earns its keep |
| **Large plan-set batch** (`batch.py`, many sheets) | overlay adds ~sub-KB × N sheets | ~same | Flate on overlays is near-irrelevant; the plan pages dominate at MB scale regardless |

**Takeaway:** Flate is *low-value for stamps* (the writer's structural bytes are thrown away by pypdf, and per-page operator streams are tiny) and *modestly valuable for standalone multi-page reports/transmittals*. It is cheap enough (one stdlib call) that applying it uniformly is the right default; it simply won't move the needle on stamped plan sets, where size is set by the underlying drawings.

### 7. Interaction with the retained libraries

- **pypdf (merger, and final serializer on the stamp path):** must be able to `PdfReader`-parse the writer's overlay. Requirements: exact xref offsets, exact `/Length`, correct `stream`/`endstream` keywording (`stream` **must** be followed by CRLF or LF, and `endstream` on its own line), valid single-root catalog → pages → page → content chain, and a `/MediaBox` matching the plan page (the code sizes the canvas from `info.view_w/h`). Because pypdf re-serializes with a **classic xref table** by default and preserves existing stream filters (it does *not* recompress unless `compress_content_streams` is called), the writer's `FlateDecode` streams pass through untouched. Do **not** hand pypdf object streams — mixing is legal but pointless here.
- **fitz / PyMuPDF (renderer + verifier):** MuPDF is highly tolerant and substitutes its own base-14 Helvetica for the non-embedded `/Helvetica`(-Bold). Because the verification baseline is rendered by the *same* pipeline, absolute glyph shape vs reportlab is irrelevant — only self-consistent, deterministic rendering matters. Declare `/BaseFont /Helvetica` (and `/Helvetica-Bold`) with `/Subtype /Type1` and `/Encoding /WinAnsiEncoding`, **no `/Widths` and no `FontDescriptor`** (permitted for the standard-14; widths are implied from Adobe's built-in AFM metrics that every conforming reader — MuPDF included — carries). This is exactly what reportlab emits today for these fonts, so render behavior under fitz is unchanged. (Text-positioning/encoding details are Tracks 1–2's domain; Track 6's only stake is that the font object is a normal indirect object referenced from the page `/Resources`, so it slots into the xref like any other.)

### 8. Concrete recommendation (drop-in defaults)

1. **Header** `%PDF-1.4\n%\xE2\xE3\xCF\xD3\n`; delivered stamp version is left to pypdf.
2. **Xref:** classic 20-byte-entry **table** + `trailer` + `startxref` + `%%EOF`. No xref/object streams. No incremental update. No linearization.
3. **Compression:** `FlateDecode` (`zlib.compress(data, 9)`) on content streams, applied uniformly (or with a "raw if it doesn't shrink" guard). **Do not build a from-scratch DEFLATE** — zlib is stdlib and already inside the offline boundary.
4. **Metadata:** **no `/Info`, no XMP.** `/ID [ <h> <h> ]` with `h = md5(content ‖ catalog)` — deterministic, content-addressed, timestamp-free. Extend the same suppress-Producer/fixed-`/ID` policy to the pypdf write step so delivered stamped PDFs are equally clean and reproducible.
5. **Sizes:** budget sub-KB writer contribution per stamped sheet, ~5–12 KB for a Flate'd 30-row transmittal. Plan-set size is governed by the underlying drawings, not this writer.

Adopt a **golden byte-hash reproducibility test** for the standalone deliverables (transmittal, resolution pickup sheet, a fixed appendix) to lock the determinism guarantee, plus the existing fitz render/pixel-diff check to lock render correctness — together they pin both the container and the pixels.


# Appendix G

## Track 7 — Native OS Drag-and-Drop via ctypes (retire `tkinterdnd2`)

### 0. What we actually depend on today (integration surface)

The whole feature funnels through **one thin module**, `rfi_stamper/gui/dnd.py`, plus the full-window `gui/overlay.py`. The public surface a replacement must reproduce is tiny:

| Symbol | Contract |
|---|---|
| `make_root() -> tk.Tk` | Returns a root. `tkinterdnd2` needs `TkinterDnD.Tk()` (it sources the tkdnd Tcl package into the interp); a native ctypes backend needs **no special root** — it can return a plain `tk.Tk()`. |
| `enable_drop(widget, cb, exts, on_enter, on_leave) -> bool` | Register `widget` as a file drop target. Returns `True` if OS DnD is live. |
| `parse_drop_paths(widget, data, exts) -> list[str]` | Split platform payload into file paths, ext-filter (dirs always pass). |
| Virtual events | `<<DropEnter>>`, `<<DropLeave>>`, `<<Drop>>` (event carries `.data`). `overlay.py` binds all three on the **root** and on a transient child `Canvas`. |

Two properties make this track **low-risk**: (a) every drop target *already* has a click-to-browse equivalent, so the fallback (`HAS_DND=False`) is a shipped, tested path; (b) the surface is 4 functions. A native backend only has to re-emit the three virtual events with a `.data` string, or call `cb(paths)` directly — nothing downstream changes.

### 1. How `tkinterdnd2` works today

`tkinterdnd2` is a **pure-Python shim over the Tcl/C extension `tkdnd`** (Petasis). It does *not* implement any protocol in Python:

1. `TkinterDnD.Tk()` runs `tkdnd::initialise` — `package require tkdnd` loads the compiled `libtkdnd*.{dll,so,dylib}` into the Tcl interpreter and sources its `.tcl` glue. The Python wheel bundles these binaries under `tkinterdnd2/tkdnd/<platform>/`.
2. `widget.drop_target_register(DND_FILES)` calls the Tcl command `tkdnd::drop_target register <path> {DND_Files}`. **Inside the C extension**, tkdnd does the real platform work:
   - **Windows:** `OleInitialize` + `RegisterDragDrop(hwnd, pIDropTarget)` — an OLE2 `IDropTarget` implemented in C. It reads `CF_HDROP` via `DragQueryFile`.
   - **Linux/X11:** the freedesktop **XDND protocol, version 5** (same as Qt/GTK). It sets `XdndAware` and handles the `Xdnd*` ClientMessages by hooking Tk's C event dispatch (`Tk_CreateGenericHandler`).
   - **macOS/Aqua:** Cocoa `NSDraggingDestination` on the Tk `NSView`.
3. On a drop, the C extension synthesizes Tk **virtual events** (`<<Drop>>`, `<<DropEnter>>`, `<<DropPosition>>`, `<<DropLeave>>`) and stuffs the payload into the event's `%D` substitution (`event.data`) as a Tcl list of paths. `tkinterdnd2`'s `dnd_bind` is just `bind` for those virtual events.

**Key insight for the rewrite:** the C extension's *only* job is to (a) register a native drop target on the widget's OS window handle and (b) turn a native drop into `cb(list_of_paths)` on the Tk thread. Everything else (overlay, routing, ext-filter) is already ours in Python. So a ctypes backend replaces ~one C file's worth of platform glue, not a framework.

The OS window handle for every platform comes from **`widget.winfo_id()`** — HWND on Windows, X11 `Window` XID on Linux, and an `NSView*`-ish pointer on Aqua.

---

### 2. WINDOWS — OLE `IDropTarget` in ctypes (the strong target)

This is the primary ship target (one-file exe *built on Windows*) and the cleanest to do from ctypes. The Win32 OLE DnD API has been stable since the 1990s.

#### 2.1 Sequence
```
OleInitialize(NULL)                      # once, on the Tk main (STA) thread
hwnd = widget.winfo_id()                 # HWND of the Tk toplevel/child
RegisterDragDrop(hwnd, pIDropTarget)     # pIDropTarget = our ctypes COM object
... Tk mainloop pumps messages -> OLE invokes our vtable callbacks ...
RevokeDragDrop(hwnd)                     # per window, before it is destroyed
OleUninitialize()                        # at shutdown
```

#### 2.2 Building a COM object in pure ctypes
A COM interface is a pointer to a pointer to a **vtable** (array of `__stdcall` function pointers). `IDropTarget` has 7 slots — the 3 `IUnknown` methods then 4 of its own:

```
slot 0  QueryInterface(this, riid*, ppv**) -> HRESULT
slot 1  AddRef(this)  -> ULONG
slot 2  Release(this) -> ULONG
slot 3  DragEnter(this, pDataObj*, grfKeyState, pt(POINTL by value), pdwEffect*) -> HRESULT
slot 4  DragOver (this, grfKeyState, pt, pdwEffect*)                              -> HRESULT
slot 5  DragLeave(this)                                                           -> HRESULT
slot 6  Drop     (this, pDataObj*, grfKeyState, pt, pdwEffect*)                   -> HRESULT
```

Shape it with `WINFUNCTYPE` (stdcall) prototypes and a `Structure` whose `_fields_` are those function-pointer types:

```python
LPVOID, HRESULT, ULONG, DWORD = c_void_p, c_long, c_ulong, c_ulong
class POINTL(Structure):     _fields_ = [("x", c_long), ("y", c_long)]

QI   = WINFUNCTYPE(HRESULT, LPVOID, POINTER(GUID), POINTER(LPVOID))
ADDR = WINFUNCTYPE(ULONG,   LPVOID)
ENTER= WINFUNCTYPE(HRESULT, LPVOID, LPVOID, DWORD, POINTL, POINTER(DWORD))
OVER = WINFUNCTYPE(HRESULT, LPVOID, DWORD, POINTL, POINTER(DWORD))
LEAVE= WINFUNCTYPE(HRESULT, LPVOID)
DROP = WINFUNCTYPE(HRESULT, LPVOID, LPVOID, DWORD, POINTL, POINTER(DWORD))

class IDropTargetVtbl(Structure):
    _fields_ = [("QueryInterface",QI),("AddRef",ADDR),("Release",ADDR),
                ("DragEnter",ENTER),("DragOver",OVER),
                ("DragLeave",LEAVE),("Drop",DROP)]
class IDropTargetObj(Structure):
    _fields_ = [("lpVtbl", POINTER(IDropTargetVtbl))]
```

**Lifetime is the #1 gotcha:** you must keep Python references to *every* `WINFUNCTYPE` callback, the `IDropTargetVtbl` instance, and the `IDropTargetObj` instance for the life of the window. If any is GC'd, OLE calls a freed pointer → hard crash. Store them on the module/GUI object.

#### 2.3 Callback bodies
- **`QueryInterface`**: compare `*riid` bytes against `IID_IUnknown {00000000-0000-0000-C000-000000000046}` and `IID_IDropTarget {00000122-0000-0000-C000-000000000046}`. On match, set `ppv[0] = cast(pointer(obj), LPVOID)`, `AddRef`, return `S_OK (0)`. Else `ppv[0]=None`, return `E_NOINTERFACE (0x80004002)`.
- **`AddRef`/`Release`**: maintain an int; since we hold one object per window for the process, returning a constant ≥1 and never freeing is acceptable (leak is bounded and intentional).
- **`DragEnter`/`DragOver`**: probe the data object for `CF_HDROP`; set `*pdwEffect = DROPEFFECT_COPY (1)` if present else `DROPEFFECT_NONE (0)`; return `S_OK`. `DROPEFFECT_COPY` is what drives the "+" copy cursor — required UX. Fire `<<DropEnter>>` here (defer via `after(0)`).
- **`DragLeave`**: fire `<<DropLeave>>`; return `S_OK`.
- **`Drop`**: extract paths (below), fire `<<Drop>>`/`cb(paths)`, set effect, return `S_OK`.

#### 2.4 Reading the file list (`IDataObject` -> `CF_HDROP`)
`pDataObj` is itself a raw COM interface — call it by hand through its vtable. `IDataObject::GetData` is **slot 3** (`QI,AddRef,Release,GetData,...`):

```python
FORMATETC{ cfFormat=CF_HDROP(15), ptd=NULL, dwAspect=DVASPECT_CONTENT(1),
           lindex=-1, tymed=TYMED_HGLOBAL(1) }
STGMEDIUM stg
hr = pDataObj.lpVtbl.GetData(pDataObj, &fmt, &stg)     # slot 3
hglobal = stg.u.hGlobal
hdrop   = GlobalLock(hglobal)
n = DragQueryFileW(hdrop, 0xFFFFFFFF, NULL, 0)          # count
for i in range(n):
    length = DragQueryFileW(hdrop, i, NULL, 0)
    buf = create_unicode_buffer(length+1)
    DragQueryFileW(hdrop, i, buf, length+1)
    paths.append(buf.value)
GlobalUnlock(hglobal); ReleaseStgMedium(&stg)
```

Use the **W (wide/UTF-16)** entry point — unicode filenames are non-negotiable (matches the offline-doc, arbitrary-filename reality). `DragQueryFile` and `DROPFILES`/`CF_HDROP` are documented on MS Learn; passing `0xFFFFFFFF` returns the count.

#### 2.5 Apartment / message-pump interplay with Tk
- OLE DnD requires the registering thread to be an **STA** (single-threaded apartment). `OleInitialize` enters STA. Do it on the **Tk main thread** *before* `RegisterDragDrop`, and never from a worker thread.
- The target-side callbacks (`DragEnter`…`Drop`) are delivered **through the normal Windows message queue that Tk's `mainloop` already pumps** (OLE marshals via a hidden `OleMainThreadWndClass` window). So callbacks run **on the Tk thread** → it is safe to touch Tk widgets. But the drop happens inside a **nested modal loop** driven by the *source's* `DoDragDrop`; mutating/destroying Tk widgets synchronously inside `Drop` can re-enter and corrupt state. **Keep the existing `root.after(20, …)` deferral** (overlay.py already does this) — it lets OLE's `Drop` return `S_OK` before we rebuild widgets.
- `winfo_id()` on Windows returns a real child HWND for each Tk window, so per-widget registration works; but registering the **toplevel** HWND once and routing by hit-test is simpler and matches the full-window overlay design.

#### 2.6 Windows verdict
- **Feasibility:** High. Textbook ctypes-COM.
- **LOC:** ~200–280 (GUID struct + `IsEqualGUID`, vtable/prototypes, 7 callbacks, `IDataObject` call-through, register/revoke, Tk event bridge).
- **Reliability risk:** Low **once** callback lifetime and STA rules are honored; both failure modes are deterministic crashes caught immediately in smoke test.
- **Maintenance:** Very low — API frozen for 30 years.

---

### 3. LINUX / X11 — freedesktop XDND v5 in ctypes (the tricky target)

The wire protocol (XDND v5) is fully specified and not hard; the **hard part is *receiving* the events inside a Tk process** without the C-level `Tk_CreateGenericHandler` hook that tkdnd uses.

#### 3.1 Protocol (what must happen)
Atoms via `XInternAtom`: `XdndAware, XdndEnter, XdndPosition, XdndStatus, XdndLeave, XdndDrop, XdndFinished, XdndSelection, XdndActionCopy, XdndTypeList`, plus type atom `text/uri-list` (and `text/plain` fallback).

1. **Advertise:** set property `XdndAware` (type `XA_ATOM`, format 32) = version `5` on the target `Window`.
2. **`XdndEnter`** (ClientMessage): `data.l[0]`=source win; `l[1]` high byte = protocol version, bit0 = "more than 3 types, see `XdndTypeList` property"; `l[2..4]` = first 3 offered type atoms. Remember whether `text/uri-list` is offered.
3. **`XdndPosition`**: `l[2]` = `(x<<16)|y` in **root** coords, `l[3]` = timestamp, `l[4]` = action atom. Reply with **`XdndStatus`** (`XSendEvent` to source): `l[0]`=our win, `l[1]` bit0 = *will accept*, `l[2]/l[3]` = a rect within which we won't ask again (send 0 to be asked every move), `l[4]` = the action we accept (`XdndActionCopy`).
4. **`XdndLeave`**: hide overlay, no reply.
5. **`XdndDrop`**: `l[0]`=source, `l[2]`=timestamp. Call `XConvertSelection(XdndSelection, text/uri-list, <prop>, our_win, time)`; the data arrives as a **`SelectionNotify`**. Read it with `XGetWindowProperty` → a `text/uri-list`: `file://host/abs/path` URIs, **CRLF-separated**, **percent-encoded** (`%20`→space). Then send **`XdndFinished`**: `l[0]`=our win, `l[1]` bit0 = success, `l[2]` = action performed.

#### 3.2 The receive problem (design fork)
`ClientMessage`/`SelectionNotify` events sent to our `Window` are delivered to **whichever X client owns that connection to the window** — i.e. **Tk's** Xlib `Display`, not a second `Display` we open via ctypes on the same XID. tkinter does **not** surface raw `ClientMessage`s to Python, so you cannot `bind` them. Three viable strategies:

| Strategy | How | Trade-off |
|---|---|---|
| **A. Own overlay X window** (recommended) | Via `libX11` ctypes, `XCreateWindow` a child (or InputOnly) window over the drop region, sized to the toplevel; set `XdndAware` on **it**; `XSelectInput` for the XDND messages on **our** `Display`; pump with a periodic `root.after(15, drain)` calling `XPending`/`XNextEvent`. Map/raise it only while a drag is in progress isn't possible (you must be `XdndAware` before the drag) — so keep an InputOnly overlay always present, resized on `<Configure>`. | Cleanest ownership of events; ~carefully manage geometry & stacking so it doesn't steal Tk pointer events (InputOnly + `input=False`… but then XDND needs it to receive — use a normal child that forwards). Extra `Display` fd + polling. |
| **B. Xlib event tap on Tk's window** | Not possible from Python without Tk's generic-handler hook; would require a C shim → defeats the purpose. | Rejected. |
| **C. Second Display + XSendEvent quirks** | Open own `Display`, set `XdndAware` on Tk's XID. Fails: source sends ClientMessage to the window → routed to Tk's connection, our `XNextEvent` never sees it. | Does not work reliably. |

Strategy **A** is what a production build should use: one InputOnly-ish child window per toplevel that *is* the XDND drop target, geometry-synced to the toplevel, events pumped from a Tk `after` loop (cooperates with the fx single-scheduler rule — register a bounded poll, not a free-running `after`; drain only while a drag is active by arming on first `XdndEnter`... but you must be Aware at rest, so keep a lightweight 15 ms poll that self-disarms between drags via `XdndLeave`/`XdndDrop`).

#### 3.3 URI decode
`text/uri-list` → split on CRLF, drop comment lines starting `#`, strip `file://<host>` prefix, `urllib.parse.unquote` (stdlib, offline-safe), and reject non-`file:` schemes. Feed the resulting local paths into the existing `parse_drop_paths` ext-filter.

#### 3.4 Wayland / XWayland caveat
Native Wayland has its **own** DnD (`wl_data_device`) that is **not** reachable this way and is compositor-mediated. But Tk runs under **XWayland** (there is no native-Wayland Tk), so the **X11/XDND path works unchanged** inside XWayland; drags **between** a Wayland-native app and the XWayland client are bridged by the compositor (Mutter/KWin) and generally work for `text/uri-list`. Document: "Linux DnD requires X11 or XWayland; a pure-Wayland Tk does not exist, so this is moot in practice." Also note some tiling WMs / `XSendEvent` synthetic-event filtering can drop `XdndStatus` — test under GNOME/KDE/XWayland.

#### 3.5 Linux verdict
- **Feasibility:** Medium. Protocol is easy; the event-reception plumbing (Strategy A) is the real work and is fiddly.
- **LOC:** ~300–450 (libX11 ctypes decls for ~15 functions + `XEvent` union, atom cache, overlay window + geometry sync, XDND state machine, selection round-trip, URI decode).
- **Reliability risk:** Medium — depends on WM/compositor honoring synthetic events; geometry sync of the overlay window is a source of edge bugs.
- **Maintenance:** Medium — X11 API is stable, but the overlay-window hack is subtle and easy to regress.

---

### 4. macOS — Cocoa `NSDraggingDestination` (the weak target — recommend NOT native)

Native mac DnD means registering an `NSView` with `registerForDraggedTypes:` and implementing `draggingEntered:`/`draggingUpdated:`/`performDragOperation:` from `NSDraggingDestination`, reading `NSFilenamesPboardType` / `NSPasteboardTypeFileURL` off the dragging pasteboard.

From **pure ctypes (no pyobjc)** this requires driving the Objective-C runtime by hand:
- `objc_getClass`, `sel_registerName`, `objc_msgSend` (with correct per-call `restype`/`argtypes` casts — a classic ctypes footgun; struct returns need `objc_msgSend_stret`).
- To *receive* drops you must **add methods to a class**: either subclass `NSView` with `objc_allocateClassPair` + `class_addMethod(cls, sel, IMP, "B@:@")` where `IMP` is a ctypes `CFUNCTYPE` callback with correct **type-encoding strings** (`v@:@`, `B@:@`, `Q@:@` for `NSDragOperation`), or **swizzle Tk's `TKContentView`**. Getting the Tk `NSView` from `winfo_id()` on Aqua is itself non-obvious (it is not a clean documented pointer across Tk versions), and there is exactly **one** `TKContentView` per toplevel that Tk itself manages — adding dragging-destination methods risks conflicting with Tk's own event handling.

- **Feasibility:** Low from pure ctypes; genuinely hard and version-fragile.
- **LOC:** ~250–400 of brittle objc-runtime glue.
- **Reliability risk:** High. Type-encoding mistakes are silent memory corruption; `winfo_id`→NSView mapping is undocumented and has changed.
- **Maintenance:** High.
- **Recommendation:** **Do not** implement mac native DnD in ctypes. Ship mac with the **click-to-browse fallback** (already fully functional) — or, if a mac GUI build is ever produced, keep `tkinterdnd2` *only* on that platform. Given the product ships one-file exes **built on Windows** and tests under **xvfb (Linux)**, macOS is not a shipping target today, so this costs nothing.

---

### 5. Recommended plan

1. **Windows: implement native ctypes `IDropTarget`.** Highest value, cleanest, matches the actual ship target. ~250 LOC.
2. **Linux: implement XDND v5 via Strategy A** *if* Linux GUI drag-drop matters to users; otherwise the browse fallback already passes under xvfb. ~350 LOC.
3. **macOS: no native DnD** — browse fallback only.
4. Keep `dnd.py` as the single façade. Add a `backends/` split (`_win_ole.py`, `_x11_xdnd.py`) selected by `sys.platform`; each backend exposes exactly `enable_drop(widget, cb, exts, on_enter, on_leave) -> bool` and emits the same virtual events. `HAS_DND` stays the honest capability flag; when no backend is live everything degrades to browse exactly as now.
5. **Remove `tkinterdnd2` from `requirements.txt`; simplify `rfi_stamper.spec`** — delete `collect_data_files("tkinterdnd2")`, the `tkdnd_datas`, and both `hiddenimports=["tkinterdnd2"]`. This is a **packaging win**: the tkdnd Tcl/binary load-path under PyInstaller **onefile** is a known headache (the `.tcl`+shared-lib must be found at `_MEIPASS`), and a pure-ctypes backend ships **zero data files** and imports only `ctypes` (always present). Smaller exe, one fewer wheel, no Tcl-package resolution at runtime.

### 6. Offline-policy note
`ctypes`, `ole32/user32/libX11` are OS libraries, not network clients — this **strengthens** the offline posture (removes a bundled third-party binary) and does not touch `offline_guard`. No new imports of socket/urllib.

### 7. Testing
- Windows: a build-time smoke that creates a hidden Tk root, registers, and (manually or via `SendMessage`-driven synthetic `WM_DROPFILES`… note: OLE DnD ≠ `WM_DROPFILES`, so true automation needs an OLE `IDropSource`) — pragmatically, gate native-DnD behind a manual smoke on the Windows build box and keep the **automated** test on `parse_drop_paths`/URI-decode/ext-filter (pure functions, no display).
- Linux/xvfb: unit-test the XDND state machine and `text/uri-list` decoder as pure functions; a full synthetic-drop integration test can drive `XSendEvent` from a second client under xvfb (feasible but ~100 extra LOC of test harness).
- Guard test: assert `enable_drop` returns `False` and the app still launches when no backend is available (the existing browse path).


# Appendix H

## Track 8 — Drag-and-Drop Removal, Build/Packaging, and the "Successful Build" Gate

This section covers three things: (1) retiring `tkinterdnd2` with a graceful degrade to the
existing "Browse" pickers (plus an optional Windows-native shim), (2) the cross-cutting
build/packaging checklist that BOTH the mini-pdf writer (Track 1–7) and this DnD change must clear
before shipping, and (3) a phased, non-one-shot rollout for the mini-pdf writer so it lands in
stages behind pixel-diff parity gates.

### 1. Current DnD architecture — it is already optional by design

The codebase was written so drag-and-drop is pure sugar. `rfi_stamper/gui/dnd.py` try-imports
`tkinterdnd2`, sets `HAS_DND`, and every consumer already tolerates its absence:

| Site | Coupling to tkinterdnd2 | Already degrades? |
|------|-------------------------|-------------------|
| `dnd.make_root()` | returns `TkinterDnD.Tk()` else `tk.Tk()` | Yes — falls back to plain `tk.Tk` |
| `dnd.enable_drop()` | returns `False` when `not HAS_DND` | Yes — callers key off the bool |
| `widgets.DropZone` | appends `"(click to browse)"` when `enable_drop` returns False; `browse` click always wired | Yes |
| `overlay.DropOverlay.__init__` | `if not dnd.HAS_DND: return` before any `drop_target_register` | Yes |
| ~11 `enable_drop(...)` callers (`tab_stamp`, `tab_merge`, `tab_markup`, `tab_project`, `tab_draft`, `tab_pdftools`, `tab_compare`, `tab_fieldstitch`) | all pass a `browse=`/picker or ignore the return | Yes |

**Consequence:** the hard dependency is a single line — `make_root()` needing `TkinterDnD.Tk()`. The
GUI already runs end-to-end with `HAS_DND=False` (this is exactly what the headless test exercises,
since `tkinterdnd2` is typically absent under xvfb today). Removal is therefore low-risk; the design
work is deciding whether to *keep* OS drag-drop via a native path or drop it.

### 2. Decision: graceful removal (recommended) with an optional Windows-native shim

`tkinterdnd2` is a Python wrapper around the compiled **tkdnd** Tcl extension (ships `.dll`/`.so`
binaries + Tcl glue collected via `collect_data_files`). Replacing it with a *cross-platform*
from-scratch DnD engine means implementing two unrelated OS protocols:

- **Windows:** OLE `IDropTarget` (`RegisterDragDrop` / `RevokeDragDrop`, a COM vtable with
  `DragEnter/DragOver/DragLeave/Drop`, `IDataObject` + `CF_HDROP` unpacking via
  `DragQueryFileW`) — or the far simpler legacy **`WM_DROPFILES`** path (`DragAcceptFiles(hwnd,
  TRUE)` from `shell32`, then intercept message `0x0233` in the window proc and read paths with
  `DragQueryFileW`). File-drop is *all* Planloom uses, so `WM_DROPFILES` is sufficient.
- **Linux/X11:** the freedesktop **XDND** protocol — a multi-round `XdndEnter/Position/Status/
  Drop/Finished` `ClientMessage` handshake with selection-transfer negotiation. Substantial, and
  Linux is **not a shipping target** (the product ships Windows one-file exes; Linux only runs the
  test suite under xvfb).

**Recommendation:** ship **graceful removal** as the baseline — delete the dependency, let
`HAS_DND` be permanently False, and rely on the Browse pickers that already exist on every drop
zone. Optionally add a **Windows-only `WM_DROPFILES` ctypes shim** behind the same
`enable_drop()`/`HAS_DND` facade so the cosmetic feature survives on the actual shipping platform
with zero new *packaged* dependency (pure `ctypes` against `shell32`/`user32`, which are always
present on Windows). This keeps the offline invariant trivially intact (no network, no bundled
native lib) and removes the `collect_data_files` binary blob from the build.

#### 2a. Exact changes — `dnd.py`

Rewrite `dnd.py` to drop the `tkinterdnd2` import entirely and provide the same public surface
(`HAS_DND`, `make_root`, `parse_drop_paths`, `enable_drop`, and the `DND_FILES` sentinel that
`overlay.py` references). Baseline (graceful) form:

```python
"""Drag-and-drop plumbing.  OS file-drop is optional and best-effort; every
drop target ALSO works by click-to-browse, so nothing depends on it. No
third-party DnD package — Windows uses a pure-ctypes WM_DROPFILES shim; other
platforms degrade to Browse."""
from __future__ import annotations
import os, sys, tkinter as tk

DND_FILES = "DND_Files"        # kept as a sentinel; overlay.py imports it
_backend = None                # set by the Windows shim below, else None

def _load_backend():
    global _backend
    if sys.platform == "win32":
        try:
            from . import dnd_win32 as _b   # ctypes WM_DROPFILES shim
            _backend = _b
        except Exception:
            _backend = None
_load_backend()
HAS_DND = _backend is not None

def make_root() -> tk.Tk:
    return tk.Tk()             # no special toplevel needed anymore

def parse_drop_paths(widget, data, exts=None) -> list:
    paths = list(widget.tk.splitlist(data)) if isinstance(data, str) else list(data)
    if exts:
        low = tuple(e.lower() for e in exts)
        paths = [p for p in paths if p.lower().endswith(low) or os.path.isdir(p)]
    return paths

def enable_drop(widget, callback, exts=None, on_enter=None, on_leave=None) -> bool:
    if _backend is None:
        return False
    try:
        return _backend.register(widget, lambda paths: callback(
            parse_drop_paths(widget, paths, exts)), on_enter, on_leave)
    except Exception:
        return False
```

If the team chooses **pure graceful removal with no shim**, collapse this to `_backend = None`
unconditionally (delete `dnd_win32`), so `HAS_DND` is always `False`. Either way `parse_drop_paths`
must keep accepting a real drop string (Tcl-list of paths, possibly brace-wrapped for spaces) —
`widget.tk.splitlist` is the correct, still-available splitter.

The optional `dnd_win32.py` shim (Windows only, no packaged deps):

```python
# ctypes WM_DROPFILES: DragAcceptFiles + subclass GWLP_WNDPROC, read CF_HDROP.
import ctypes
from ctypes import wintypes
WM_DROPFILES = 0x0233
shell32, user32 = ctypes.windll.shell32, ctypes.windll.user32
def register(widget, on_paths, on_enter=None, on_leave=None) -> bool:
    hwnd = widget.winfo_id()             # HWND of the tk widget
    shell32.DragAcceptFiles(hwnd, True)
    # SetWindowLongPtrW(hwnd, GWLP_WNDPROC=-4, new_proc); in new_proc, on
    # WM_DROPFILES call DragQueryFileW(hDrop, i, buf, n) for i in range(count),
    # then DragFinish(hDrop); route the list to on_paths(...); else CallWindowProc.
    ...
    return True
```

Note the shim registers on a per-widget HWND, matching the current per-widget `enable_drop`
contract; the full-window `overlay.py` path can register on the root's HWND. `on_enter/on_leave`
have no `WM_DROPFILES` equivalent (that hover feedback is OLE-only), so the overlay's dashed-frame
animation simply won't appear under the shim — an acceptable cosmetic loss; the drop itself works.

#### 2b. Exact changes — `widgets.py` and `overlay.py`

- **`widgets.DropZone`**: no code change required. It already reads `dnd.enable_drop(...)`'s bool
  and appends `"(click to browse)"` on False, and always binds `browse`. Verify the label still
  reads sensibly when `HAS_DND` is always False (it does).
- **`overlay.DropOverlay`**: it references `dnd.DND_FILES`, `root.drop_target_register`, and
  `root.dnd_bind`. Under graceful removal `dnd.HAS_DND` is False, so the `__init__` early-returns
  before touching those tk methods — **no change needed, but it is now dead-ish code** on non-shim
  builds. Recommend leaving it intact (it guards correctly and re-lights if a shim is present) OR,
  if the shim can't drive `<<DropEnter>>`, gate the overlay behind a capability flag
  (`dnd.HAS_HOVER`) so it only activates with a real OLE backend. Cleanest: add
  `HAS_HOVER = False` to `dnd.py` and have `DropOverlay.__init__` return unless `dnd.HAS_HOVER`.

#### 2c. Exact changes — `rfi_stamper.spec`

Remove all three tkinterdnd2 hooks. Diff:

```python
# DELETE the collect_data_files import + try/except block:
-from PyInstaller.utils.hooks import collect_data_files
-try:
-    tkdnd_datas = collect_data_files("tkinterdnd2")
-except Exception:
-    tkdnd_datas = []
# In BOTH Analysis() calls:
-    datas=tkdnd_datas + [("assets/planloom.png", "assets")] + _tracer_model,
+    datas=[("assets/planloom.png", "assets")] + _tracer_model,
-    hiddenimports=["tkinterdnd2"],
+    hiddenimports=[],
# (cli Analysis: datas=tkdnd_datas + _tracer_model  ->  datas=_tracer_model)
```

This is a real size/robustness win: it drops the bundled `tkdnd*.dll` + Tcl package files from both
one-file exes and removes a PyInstaller warning-source.

#### 2d. Exact changes — `requirements.txt`

Delete the `tkinterdnd2>=0.4` line. Add a one-line comment noting OS file-drop is a pure-ctypes
Windows extra with no package (mirroring the existing Tracer/OCR note style). No new runtime dep is
introduced — invariant preserved.

### 3. Testing drag-drop (and its absence) headlessly under xvfb

You cannot synthesize a real OS drag under xvfb (no window manager negotiates it, and `WM_DROPFILES`
needs a live Win32 message pump). The industry pattern is to test **the seam, not the OS**: prove
the fallback path is wired and prove the path-parsing/routing logic is correct with a synthetic
event. Add to `tests/test_gui_construct.py`:

```python
# ---- DnD degrades gracefully and routes correctly (headless) ----------
from rfi_stamper.gui import dnd
# 1. Absence path: under xvfb no OS backend is present -> pickers still work.
assert dnd.HAS_DND is False            # (True only on a Win shim build)
# 2. make_root produced a usable plain root (already asserted by app build).
# 3. Path-splitting handles Tcl-list drops incl. brace-wrapped spaced paths.
paths = dnd.parse_drop_paths(root, "{C:/a b/plan.pdf} C:/x.pdf",
                             exts=(".pdf",))
assert paths == ["C:/a b/plan.pdf", "C:/x.pdf"]
# 4. Ext filter + directory passthrough.
assert dnd.parse_drop_paths(root, tmp, exts=(".pdf",)) == [tmp]  # dir kept
# 5. Simulate a drop reaching a tab WITHOUT the OS: call the same callback
#    enable_drop would have called, proving routing is backend-independent.
app.integrations.handle_drop([csvp])   # already covered — keep as the
                                       # "drop routes to import" regression
# 6. DropZone label advertises Browse when OS DnD is off.
from rfi_stamper.gui.widgets import DropZone
dz = DropZone(root, app.theme, "Drop a plan set", on_paths=lambda p: None,
              exts=(".pdf",), browse=lambda: None)
assert "browse" in dz._text.lower()
```

If a Windows shim ships, add a **Windows-only** unit test (guarded by
`sys.platform == "win32"`) that constructs the shim's path list from a fabricated `CF_HDROP`
buffer or, more simply, calls the shim's internal `_paths_from_hdrop` against a stubbed
`DragQueryFileW` — never depends on a real drag. Under xvfb/Linux this test is skipped exactly like
`test_gui_construct` gates itself. The key discipline: **DnD tests must be pure logic + synthetic
data; zero real-mouse, zero WM dependence** so `tests/run_all.py` stays green headlessly.

### 4. Cross-cutting "WHAT A SUCCESSFUL BUILD NEEDS" (both efforts)

A single gate both the mini-pdf writer and the DnD change must pass before either ships. Run top to
bottom; every box green.

**A. Tests green**
- [ ] `python tests/run_all.py` exits 0; GUI test runs under `xvfb-run -a` (or SKIPs cleanly on no-display) and prints `GUI CONSTRUCT TEST PASSED`.
- [ ] `tests/smoke_test.py` (rotation-0 + `/Rotate 90` end-to-end stamping) passes.
- [ ] Every module-level test that writes a PDF via the writer (`test_reb_stamp`, `test_resolution`, `test_submittal`, `test_pdfdoctor`, `test_project`, `test_merge`, `test_batch`, `test_fieldstitch*`, `test_daybook`, `test_crewpass`, plus the Loft `plate_pdf`/`ledger_pdf`/`report_pdf` paths) passes byte- **and** render-check.

**B. Pixel-diff / verify invariant (the hard one)**
- [ ] `verify.py` PASSES on every stamped page: the only rendered change is the intended box (diff > 25 gray levels), nothing pre-existing under any box footprint, untouched pages pixel-identical. This is non-negotiable and must NOT be weakened.
- [ ] Both the DnD change (which touches no PDF path) and the mini-pdf writer produce identical verify results to the reportlab baseline — see the phased parity harness in §6.

**C. PDF conformance (mini-pdf writer only, but gate it here)**
- [ ] Every generated PDF opens clean in **PyMuPDF/fitz** (already the app's own renderer — it is the ground-truth consumer) and in at least one external validator: `qpdf --check out.pdf` reports no errors/warnings, and `pdftotext`/`mutool clean` round-trip without repair.
- [ ] Base-14 font metrics come from the **Adobe Core-14 AFM** tables; text uses **WinAnsiEncoding**; `/Type1 Helvetica`/`Helvetica-Bold` non-embedded. Optionally spot-check with **veraPDF** (note: PDF/A would require embedding — NOT a goal here; use veraPDF only for structural sanity, not PDF/A pass).
- [ ] xref/`startxref`/trailer well-formed per **ISO 32000-1**; `pypdf.PdfReader(out)` and a subsequent `pypdf` **merge** succeed (the merge path is a shipping consumer and a strict parser).

**D. No new runtime deps / offline intact**
- [ ] `requirements.txt` has **fewer** lines, not more (tkinterdnd2 gone; reportlab gone only at the final phase). No package added for either effort.
- [ ] Offline invariant: `offline_guard.is_active()` still True by default; grep the diff for `socket`, `urllib`, `requests`, `http.client`, `ssl`, `smtplib` — zero outbound network imports. The Windows DnD shim uses only `ctypes`/`shell32`/`user32` (local OS, not network).

**E. Naming / privacy scrub**
- [ ] No company/project/person names, and (extended owner rule) **no third-party vendor/product names** in new code, comments, docs, or commit messages. "tkinterdnd2"/"reportlab" may appear only in removal notes/changelog, not as living dependencies. Product names come from the HANDOFF.md registry.

**F. Docs updated**
- [ ] `CLAUDE.md`: repo map + invariants updated (mini-pdf writer replaces reportlab in the module descriptions; DnD note updated to "pure-ctypes Windows shim / Browse fallback, no package").
- [ ] `HANDOFF.md`: new SHIPPED round entries with version tags; naming registry unchanged.
- [ ] `ROADMAP.md`: mark the "retire reportlab" and "retire tkinterdnd2" phases; add follow-ups.
- [ ] `README.md`: dependency list trimmed; build instructions unchanged.
- [ ] **New PLAN doc** (e.g. `MINIPDF_PLAN.md`, staged P1–Pn like `OCR_PLAN.md`) capturing the phased writer rollout and the parity harness, plus a short DnD-removal note.

**G. Version + VCS**
- [ ] Version bump (current line is ~v4.7.1; DnD removal → a patch/minor, mini-pdf writer default-flip → a minor, e.g. v4.8.0). Bump wherever the string lives and in HANDOFF round headers.
- [ ] Work on a branch (not default); commit with the required `Co-Authored-By` / `Claude-Session` trailers; push only when asked.

**H. Windows build produces both exes + CLI smoke ends in PASS**
- [ ] On Windows: `build_windows.bat` → `dist\Planloom.exe` + `dist\planloom-cli.exe`, both self-contained, no console errors.
- [ ] CLI smoke: `planloom-cli stamp -p plans.pdf -r rfi_dir --scan-only map.csv` then a full `--map` run; the `*_report.txt` **ends in PASS** (verify gate inside the frozen exe).
- [ ] GUI exe cold-launches, opens a PDF, stamps, verify PASSES — proving the mini-pdf writer works **inside the frozen build** (font AFM data must be bundled/embedded, see §5).

### 5. PyInstaller one-file gotchas (both efforts)

- **Bundled data files must ship and be found at runtime.** The mini-pdf writer needs the Core-14
  AFM metric tables. Do **not** rely on them living on disk — either hard-code the 14 width arrays
  as Python literals (simplest, most robust for `--onefile`) or add them to `datas=` in the spec
  and resolve via the existing `resource_path()`/`sys._MEIPASS` pattern the repo already uses for
  `assets/` and `tracer/model.npz`. A writer that reads AFM from `site-packages/reportlab/...` will
  work in dev and **silently break in the frozen exe** once reportlab is deleted.
- **`--onefile` unpacks to a temp dir (`_MEIPASS`) on every launch** → cold-start latency and
  antivirus scanning of the extracted tree. Removing tkinterdnd2's `tkdnd` binaries and (later)
  reportlab shrinks the archive, *improving* cold start and reducing AV surface.
- **Antivirus / SmartScreen false positives** are common for unsigned PyInstaller one-file exes
  (bootloader pattern). Fewer bundled native `.dll`s (tkdnd gone) reduces heuristic hits; if
  signing is available, sign both exes. Document the AV caveat in README.
- **`hiddenimports` hygiene:** dropping `"tkinterdnd2"` removes a resolved-but-optional import
  warning. Confirm no *new* hidden import is needed — the mini-pdf writer should be pure
  `stdlib`+`zlib` (for `/FlateDecode` streams), which PyInstaller picks up automatically. If it uses
  `struct`/`zlib`/`hashlib` only, nothing to add.
- **Two Analysis blocks stay in sync:** both the GUI and CLI spec sections currently duplicate the
  tkdnd datas + hiddenimports; edit **both**, or the CLI exe keeps a stale reference.
- **tkinter/Tcl is still bundled** (the GUI needs it) — removing tkinterdnd2 does not remove tk
  itself; don't over-prune.
- **Determinism:** `--onefile` timestamps differ per build; the pixel-diff/verify gate is what
  proves output stability, not build byte-identity. Run the CLI smoke inside the exe, not just from
  source, because `_MEIPASS` path resolution only fails when frozen.

### 6. Phased, non-one-shot rollout for the mini-pdf writer

Ship the writer in stages so the pixel-diff invariant can catch any regression before the old engine
is gone. reportlab is used broadly here — not just `canvas` primitives in `stamp.py`/`layout.py`,
but **platypus flowables** (`Paragraph`, `Table`, `ParagraphStyle`, `simpleSplit`, `stringWidth`)
in `transmittal.py`, `reports.py`, `fieldpro.py`, and `draft.py`. The writer must therefore cover
positioned text, rects/lines, flat RGB fills, **text wrapping**, and **simple tables** — plan the
phases around that surface.

| Phase | Goal | Gate to advance |
|-------|------|-----------------|
| **P0 — Metrics + primitives** | New `minipdf.py`: object/xref/trailer writer, Core-14 AFM widths, `stringWidth`, WinAnsiEncoding text, rect/line/fill ops, `/FlateDecode` streams. No caller switched. | Unit tests: byte-valid PDF; fitz + qpdf clean; `stringWidth` matches reportlab within ≤0.01pt across the Core-14 charset. |
| **P1 — Behind a flag, parallel render** | Add `PLOOM_PDF_ENGINE=minipdf` env/flag. `stamp.py` can emit via either engine. Keep reportlab default. | A **parity harness** renders the SAME stamp job with both engines and pixel-diffs the rasterized pages (fitz at 90–200 dpi): boxes identical within the verify tolerance; text baselines/positions within ≤1px. |
| **P2 — Text-block + table flowables** | Reimplement the platypus surface actually used (wrapped paragraphs, the RFI-log/ledger/register tables). Port `transmittal`, `reports`, `fieldpro`, `draft`, `resolution`, `daybook`, `crewpass` one module at a time behind the flag. | Each ported module's existing test passes on the new engine; parity harness pixel-diffs each generated PDF (log, ledger, forms, plate) against the reportlab baseline. |
| **P3 — Flip the default** | Default engine → `minipdf`; reportlab reachable only via the flag as a fallback. | Full `tests/run_all.py` green with the new default; **verify PASSES on every stamped page**; CLI smoke `*_report.txt` ends PASS; GUI exe smoke on Windows. Soak: run the blind-test corpus (36 RFIs × 16-sheet plumbing set) — 16/16 sheets, 0 missed refs, verify all-pass, matching the documented baseline. |
| **P4 — Delete reportlab** | Remove `reportlab` import sites, the fallback flag, and the `reportlab>=4.0` requirement. | Re-run A–H checklist; `grep -rn reportlab rfi_stamper/` returns only changelog/removal notes; requirements shrinks; version minor bump; HANDOFF round entry. |

**Why phased:** the verify invariant means a 1-pixel text-baseline drift is a hard FAIL, not a
cosmetic nit. Running both writers in parallel and pixel-diffing (P1–P2) turns "did the from-scratch
writer render identically?" into an automated, per-page assertion **before** the old engine is
removed — the same staged discipline (`P1→P4`, retire only at the last phase) the project already
used to retire Tesseract in `OCR_PLAN.md`. The env/flag lets P3 flip the default reversibly, so a
field-discovered regression is a one-line rollback, not a re-release.

**DnD rollout, by contrast, is single-phase:** the app already runs with `HAS_DND=False`, so the
removal (delete dep, trim spec/requirements, add the fallback/parse tests, optional Windows shim)
ships in one change gated only by A/D/E/F/G/H above — no parallel-run harness needed.
