# CLAUDE.md — Planloom (offline construction workspace)

Product name: **Planloom** ("weaves the answers into the sheets"); the Python
package keeps the historical name `rfi_stamper` for API stability. Core: RFI
note stamping with pixel-diff verification and a resolution lifecycle
(open→answered→in_work→fixed→verified) stamped into note headers, wrapped in
a seven-section workspace (Home, Field Management, Project Management,
Plans & BIM, Reporting, App Integrations, Ground Truth). Generalized to any
trade and any firm's RFI format. **Fully offline by policy** — see the
invariants.

## First task (if none given)

Run the test suite: `python tests/run_all.py` (GUI test needs a display;
use `xvfb-run -a` on headless Linux). Then build the Windows executables on
Windows with `build_windows.bat` and smoke the CLI (`--scan-only` + a full
run; the `*_report.txt` must end in PASS).

## Non-negotiable invariants (user-approved; do not change silently)

1. **Offline, always.** No module may import networking (socket/urllib/
   requests/http clients) for outbound use. No telemetry, no update checks,
   no cloud APIs. `offline_guard.install()` stays enabled by default in the
   GUI. This protects NDA-covered documents; treat any network addition as a
   privacy regression.
2. Note style: thin red-outlined rectangle (RGB 0.84, 0.06, 0.06), white
   fill, all text red; bold `RFI ### — SHORT SUBJECT` header (Helvetica-Bold
   9.2); 1–2 line body = question + answer/direction (Helvetica 7.7);
   multiple RFIs on one sheet stack inside one box. Constants live in
   `layout.py`. Style changes require the user's sign-off on a one-sheet
   proof first. USER-APPROVED extension: an optional resolution-status suffix
   on the header line (` · ANSWERED` etc., same font/color) — appended after
   the title clip so it is never truncated (`layout.make_entries(statuses=)`).
3. NEVER cover linework, dimensions, keynotes, or title blocks. A spot only
   qualifies if the padded window is completely free of content pixels
   (gray < 225 at 90 dpi). No exceptions, no "mostly empty".
4. Verification must PASS before anything is delivered: every stamped page's
   only rendered change is the box itself (diff > 25 gray levels), nothing
   pre-existing under any box footprint, untouched pages pixel-identical.
   `pipeline.run` enforces this; never bypass or weaken `verify.py`.
5. Anything unplaceable or unmatched goes to the labeled appendix page —
   never force a box onto a drawing.
6. Keep note boxes visually distinct from revision clouds (drawings carry
   addendum deltas); the rectangle style above does that — don't add cloud
   or bubble shapes to the stamper output.
7. No company, project, or person names in code, comments, docs, or history.
   EXTENDED (owner request): no third-party vendor/product names either
   (survey-tablet makers, CAD/BIM authoring tools). Describe compatibility by
   format only ("PNEZD CSV for robotic-total-station tablets"). Product
   naming registry lives in HANDOFF.md — use those names.

## Repo map

    rfi_stamper/core.py       RFI reading (PDF / zip-package / raw text), record
                              split + merge, field + reference parsing
    rfi_stamper/sheets.py     plan-set index: page -> sheet number, geometry
    rfi_stamper/layout.py     note text, box math, empty-rectangle finder, zones
    rfi_stamper/stamp.py      reportlab overlay + rotation-general pypdf merge,
                              appendix pages
    rfi_stamper/verify.py     pre/post render pixel-diff verification
    rfi_stamper/pipeline.py   scan -> map -> place -> stamp -> verify -> report
    rfi_stamper/summarize.py  offline extractive cliff-note summarizer
    rfi_stamper/offline_guard.py  process-wide outbound-socket kill-switch
    rfi_stamper/merge.py      combine / split / rotate engine (pypdf)
    rfi_stamper/align.py      auto-align + color overlay compare (numpy FFT)
    rfi_stamper/pdfdoctor.py  diagnose + repair/unlock/compress/rasterize/upscale/
                              linearize/strip-metadata/normalize-rotation, verify_safe
    rfi_stamper/ocr.py        offline Tesseract searchable-layer OCR (optional binary)
    rfi_stamper/hyperlink.py  auto sheet cross-linking (native GoTo links) + outline
    rfi_stamper/transmittal.py  RFI-log / generic table PDF (reportlab)
    rfi_stamper/batch.py      stamp many plan sets against one RFI pile
    rfi_stamper/submittal.py  submittal-register parser + log PDF
    rfi_stamper/resolution.py RFI resolution lifecycle: status store sidecar,
                              header suffix, Designer Pickup Sheet PDF
    rfi_stamper/project.py    shared local project store (.ploom.json): tasks,
                              schedule, punch, inspections, COs, budget, docs,
                              CSI spec parsing
    rfi_stamper/reports.py    form templates (blank + filled PDFs), project
                              snapshot report
    rfi_stamper/integrations.py file-based bridges: CSV in/out, .ics, bundles,
                              drop-folder scan — NEVER network
    rfi_stamper/bim.py        3D math + procedural building model + OBJ loader
    rfi_stamper/fieldstitch.py layout points: layers, numbering, world coords,
                              PNEZD CSV / XLSX (hand-rolled OOXML) / DXF R12
                              exporters, kits (bowline/clovehitch/fullspool)
    rfi_stamper/extrude.py    plan PDF vector linework -> extruded 3D wall model
                              in the Fieldstitch world frame
    rfi_stamper/draft.py      The Loft engine: 2D drafting model (decimal feet,
                              y-up = Fieldstitch world frame), Plies (layers),
                              Plumbline snaps, Stencils, plate PDF / DXF R12 /
                              PNG exports, bridges to reckoner/bim/fieldstitch
    rfi_stamper/daybook.py    daily progress journal store + PDF log
    rfi_stamper/reckoner.py   markup quantity takeoff + price book -> estimate
    rfi_stamper/crewpass.py   offline seat ledger + report (local JSON only)
    rfi_stamper/markups/      GUI-free markup data layer: model (+ PDF annot
                              writer), multiply, measure, toolchest
    rfi_stamper/gui/          tkinter app: app (nav shell), nav (animated
                              section bar), fx (animation framework: single
                              idle-when-done scheduler, quality tiers),
                              theme (color-theory palettes + SECTIONS hues),
                              crud (schema-driven module panels), bim3d
                              (canvas 3D viewer), dnd, widgets, palette,
                              overlay, viewer, prefs (~/.planloom), tab_draft
                              (The Loft drafting board), tab_home,
                              tab_field, tab_project (incl. ResolutionBoard),
                              tab_plansbim, tab_reporting, tab_integrations,
                              tab_truth, tab_stamp, tab_merge, tab_markup,
                              tab_compare, tab_pdftools
    rfi_stamper/__main__.py   CLI (stamp/merge/split/compare/gui); no args -> GUI
    tests/                    plain-python test scripts; tests/run_all.py runs all
    skill/rfi-overlay/        Claude skill wrapping the stamping engine
    rfi_stamper.spec          PyInstaller: Planloom (GUI) + planloom-cli
    build_windows.bat         one-click Windows build

## Commands

    python -m rfi_stamper                                    # GUI
    python -m rfi_stamper stamp -p plans.pdf -r rfi_dir --scan-only map.csv
    python -m rfi_stamper stamp -p plans.pdf -r rfi_dir --map map.csv -o out.pdf
    python -m rfi_stamper merge a.pdf b.pdf -o combined.pdf
    python -m rfi_stamper compare old.pdf new.pdf -o overlay.pdf
    python tests/run_all.py                                  # full regression
    pip install -r requirements.txt                          # deps

## Validation status

The stamping engine was blind-tested against a real project: 36 production
RFIs re-rendered as ordinary PDFs, run cold against a 16-sheet plumbing set —
16/16 sheet numbers detected, required references missed: 0, noise
references: 0, answered-set detection matched a manual audit exactly, and
verification passed on every page. The zip-package and raw-text input paths
were proven on real export files. GUI constructs under xvfb.
`tests/smoke_test.py` covers rotation-0 and /Rotate 90 end to end.

## Hard-won gotchas — do NOT re-learn these the hard way

- /Rotate 90 overlay transform is `rotate(90).translate(tx=media_w)`
  (viewer (x,y) -> media (Wm − y, x)). This was FIELD-VERIFIED; the obvious
  alternative renders 180° flipped and only pixel-diff caught it. Other
  rotations follow the same convention in `stamp._viewer_to_media`; a
  nonconforming producer fails verification loudly rather than shipping bad
  overlays.
- PyMuPDF `get_text("words")` may return UNROTATED media coordinates on
  rotated pages, and they can still sit numerically inside the viewer rect —
  you cannot detect this by bounds-checking. `sheets._detect_sheet` scores
  rotation-matrix-transformed words first, raw words as fallback.
- Document-controls exports use non-breaking spaces (\xa0) between words;
  every regex would silently miss. All ingestion is normalized in
  `core._normalize_text` — keep it that way.
- Some "RFI PDFs" are actually ZIP archives with a .pdf extension (page JPEGs
  + per-page OCR .txt + manifest). `core.read_document` sniffs magic bytes;
  never trust the extension.
- Blank `Answer:` fields are followed by junk that looks like answers:
  attachment file tables, letterhead address blocks, and verbatim question
  restatements. Defenses (all in `core.py`): label regex uses `[ \t]*` (NOT
  `\s*` — consuming the newline breaks boundary anchoring), `_SECTION_END`
  boundaries, `_JUNK_HEAD` rejection, question-restatement substring check,
  underscore form-ruling stripped in `_clean_block`, footer `_trim_tail`.
- MSDS/GHS precaution codes ("P501 – Dispose of contents...") inside
  attachments look exactly like sheet numbers. `GHS_LINE` guard skips them;
  sheet tokens found only after the first attachments marker are demoted to
  via='attachment' and never auto-mapped.
- Placement/verify rounding: the finder searches with `SEARCH_PAD =
  PAD_PX + 3` slack and each placed box carries its exact pixel window in
  `occ`; `verify.py` checks that window rather than recomputing (recomputing
  caused ±1 px drift that grazed adjacent frame lines -> false FAILs).
- Answered copies of earlier RFIs ride inside later packages; duplicate
  records are merged and the answer backfills the earlier record.
- The mapping review step (GUI table / `--scan-only` CSV) is the human
  safeguard; `via` column: planref = labeled reference (high confidence),
  body = token in text (glance), attachment = reported only, manual,
  unmatched.
- Markup coordinates are *viewer page points* (top-left origin, y down,
  fitz rotated `page.rect` space) everywhere: canvas, model, sidecar JSON,
  and the annot writer. `markups/model.apply_to_pdf` handles /Rotate pages —
  it is pixel-verified in `tests/test_markups.py`; keep that test.
- tk.PhotoImage renders from PPM bytes (`pix.tobytes("ppm")`); keep a Python
  reference to every PhotoImage or tk garbage-collects the image mid-display.
- fitz `insert_link` raises "bad page number" for a GoTo target that doesn't
  exist YET — when rebuilding a doc page-by-page, re-attach links in a second
  pass after every page exists (`pdfdoctor.normalize_rotation`).
- fitz `search_for` is substring matching: a "P-100" hit also matches P-1 and
  P-10. `hyperlink._standalone()` word-boundary-checks every hit; keep it.
- ALL animation goes through `gui/fx.py`'s single scheduler, which disarms
  when no task is active (zero idle CPU) and pauses ambient loops on <Unmap>.
  Never add a free-running `after` loop; register with fx instead.
- The animation quality tiers (full/reduced/off, `fx.set_quality`) are a user
  promise: old hardware must stay usable. `quality()=="off"` must never touch
  tk mid-animation (it jumps straight to the final state).
- Resolution statuses are keyed by zero-filled RFI numbers (matching core's
  `zfill(3)`); `ResolutionStore.seed_from_records` never downgrades an
  existing status.
- Loft (draft.py) model space is decimal feet, y UP (= Fieldstitch world
  frame, E=x N=y); the GUI canvas flips y in its view transform only. Doors/
  windows are host-parametric (`pts=[]`, everything derives from host wall +
  `t`) — they ride wall moves for free, and deleting a wall CASCADES to its
  hosted doors/windows (remove() returns the full count). Paper-relative
  sizes (text, bubbles, dash patterns) convert via
  `model_ft = paper_in * scale_ratio / 12`.

## Summaries

`summarize.py` produces the note bodies deterministically and offline
(`OfflineSummarizer.summarize(rec)`, hooked into `pipeline.run` via the
`summarizer=` parameter). When Claude itself runs this engine (see
`skill/rfi-overlay/SKILL.md`), Claude should write the cliff notes directly
via the same summarizer hook.
