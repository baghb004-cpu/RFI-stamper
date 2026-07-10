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
    rfi_stamper/minipdf/      the from-scratch PDF engine.  WRITER (retired
                              reportlab at v4.8.0): WinAnsi encoding, Core-14
                              metrics (oracle-equal to 1e-13), content-stream
                              builder, byte-exact classic-xref document (no
                              metadata, content-hash /ID), reportlab-canvas
                              facade, flow/table layout engine.  READER +
                              page surgery — the Shuttle (retired pypdf at
                              v5.0.0): parse (lenient lexer/xref/objstm/
                              recovery + strict self-check mode), graph
                              (object-graph importer + serializer + page/
                              outline writer), pagemerge (overlay compositor,
                              4 closed-form CTMs), io (pypdf-shaped facade).
                              PLOOM_PDF_ENGINE=reportlab / PLOOM_PDF_IO=pypdf
                              re-enable the retired libraries as dev-box
                              parity oracles
    rfi_stamper/stamp.py      minipdf overlay + rotation-general page merge
                              (the Shuttle), appendix pages
    rfi_stamper/verify.py     pre/post render pixel-diff verification
    rfi_stamper/pipeline.py   scan -> map -> place -> stamp -> verify -> report
    rfi_stamper/summarize.py  offline extractive cliff-note summarizer
    rfi_stamper/fsutil.py     shared atomic-write primitive (tmp+fsync+replace)
    rfi_stamper/offline_guard.py  process-wide outbound-socket kill-switch
    rfi_stamper/merge.py      combine / split / rotate engine (the Shuttle)
    rfi_stamper/align.py      auto-align + color overlay compare (numpy FFT)
    rfi_stamper/drawdiff.py   the Slipsheet: vector drawing-revision diff —
                              (theta, rho) line buckets + 1-D interval
                              algebra (splits/merges/extensions are one code
                              path), align-first, word layer, change-region
                              clustering, deterministic redline PDF (removed
                              dashed red / added solid blue / regions boxed)
    rfi_stamper/pdfdoctor.py  diagnose + repair/unlock/compress/rasterize/upscale/
                              linearize/strip-metadata/normalize-rotation, verify_safe
    rfi_stamper/ocr.py        thin facade over the Tracer (historical API names
                              kept; no external OCR binary since v4.7.0)
    rfi_stamper/tracer/       the Tracer: from-scratch OCR (pure numpy + fitz, no
                              external engine) — render/binarize/deskew/linework/
                              components/segment/normalize/fonts/classify(NCC)/
                              searchable; drop-in-compatible with ocr.py; P5
                              split+merge lattice (Viterbi + char bigram prior)
                              reads touching/broken photocopy glyphs (OCR_PLAN.md
                              staged P1-P5; Tesseract removed at P4)
    rfi_stamper/hyperlink.py  auto sheet cross-linking (native GoTo links) + outline
    rfi_stamper/transmittal.py  RFI-log / generic table PDF (minipdf flow engine)
    rfi_stamper/batch.py      stamp many plan sets against one RFI pile
    rfi_stamper/submittal.py  submittal-register parser + log PDF
    rfi_stamper/cutticket.py  the Cut Ticket: model-driven pull list — tag
                              census over Loft fixtures (explicit tags only),
                              harvest-style reconcile into the project store
                              (machine facts refresh, human callouts/notes
                              survive, orphans tombstone), Swatchbook
                              proposal packets; synced on every Loft save
    rfi_stamper/swatchbook.py the Swatchbook: plumbing cut-sheet submittal
                              builder — offline manufacturer-sheet library
                              (manifest + sha256 + alias resolution), one
                              stamped PDF per fixture tag (approved stamp,
                              0-49 numbering, spec-paragraph merge order),
                              gap-honest 00-BUILD-LOG.md; kit data in
                              rfi_stamper/data/cutsheet_library/; + the
                              Chalk Mark: certainty-gated model-number
                              checkbox marking on packet pages (off/
                              report/mark — one row, one box, pixel-
                              empty, else skip with the reason)
    rfi_stamper/setscale.py   the Story Pole: dimension-anchored autoscale,
                              witnessed — dim strings paired with their
                              dimension lines give pt/ft hypotheses; PASS
                              needs >=5 agreeing witnesses (outliers NAMED)
                              plus an independent corroborator (door swings
                              on standard leaf sizes or an agreeing title-
                              block scale note); a disagreeing note refuses
                              with the exact ratio (half-size print);
                              per-sheet verdicts, never inherited
    rfi_stamper/reedcount.py  the Reed Count: fixture-symbol auto-count on
                              vector sheets at a VERIFIED scale — strip long
                              linework + door swings, proximity-cluster,
                              normalize (24-rotation x flip pose search,
                              dilated-grid soft-F1 vs Loft-stencil
                              signatures), size-sanity hard gate, ambiguity
                              surfaced (mop vs single sink), text-labeled
                              symbols (WH) need their label, unknown tray +
                              human-gated custom symbols
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
    rfi_stamper/ifclite.py    the Draw-In: IFC/STEP (ISO 10303-21) import
                              subset — lazy two-pass parser, units-first,
                              placement chains, extruded walls/slabs/columns
                              -> bim Faces/Segments, coverage-honest report
                              (every candidate imported OR skipped w/ reason)
                              + interrogation kernel: screen_ray (inverse of
                              project_points), Möller-Trumbore ray_triangles
                              (two-sided), Liang-Barsky clip_segment_box (cut
                              flags), Sutherland-Hodgman clip_poly_box,
                              measure3d (adapter over fieldpro.deltas)
    rfi_stamper/raster.py     GUI-free numpy z-buffer rasterizer for the BIM
                              shaded mode: per-pixel depth (1/z persp, -z
                              ortho), near-plane Sutherland-Hodgman clip,
                              two-sided fill, painter-parity 12-bucket
                              lambert (single source), fid/silhouette mask;
                              gui/bim3d blits it as ONE PhotoImage
    rfi_stamper/fieldstitch.py layout points: layers, numbering (spools +
                              tombstones), statuses, witness points, world
                              coords, PNEZD/PENZD CSV (+.tag.txt, frame hash) /
                              XLSX (hand-rolled OOXML) / DXF R12 exporters,
                              kits (bowline/clovehitch/fullspool/sheetbend/
                              marlinspike), advisory import validators
    rfi_stamper/fieldpro.py   layout QA: tolerance classes, Stitch Codes,
                              delta math, as-staked pairing/commit, check-shot
                              brackets, As-Staked Ledger PDF + _qa.csv,
                              walking-route sort; two-feet/CSF/Helmert-fit
                              coordinate math, error-budget preflight,
                              station log, stake packages (day bundles)
    rfi_stamper/selvage.py    the Selvage — the wire formats (the loom's
                              self-finished edge, where the weave meets the
                              field instruments): LandXML 1.2 CgPoints,
                              GSI-8/16 fieldbook, SP-record fieldbook (.rw5),
                              DXF attribute-block tier + CAD layer-name
                              rules; ONE shared coordinate-order writer table
    rfi_stamper/harvest.py    model-to-points generators (PURE — proposals
                              only, commit happens in the GUI): gridiron,
                              wall corners, along/offset line w/ per-trade
                              stride rules, bolt cage, line intersections,
                              reharvest diff (orphans never auto-deleted)
    rfi_stamper/extrude.py    plan PDF vector linework -> extruded 3D wall model
                              in the Fieldstitch world frame
    rfi_stamper/draft.py      The Loft engine: 2D drafting model (decimal feet,
                              y-up = Fieldstitch world frame), Plies (layers),
                              Plumbline snaps, Stencils, plate PDF / DXF R12 /
                              PNG exports, bridges to reckoner/bim/fieldstitch
    rfi_stamper/pipewright.py Pipewright piping engine: Loft "pipe" runs, node
                              network, deterministic fitting derivation,
                              slope/invert solver, cap/replace/resize command
                              APIs (Weaver-shaped report dicts), code-minimum
                              checks, takeoff, sloped-3D bridge
    rfi_stamper/clash.py      Clash-Lite: deterministic interference (capsule
                              vs capsule seg-seg closed form; capsule vs wall
                              box via convex signed-distance ternary search),
                              hard/clearance/penetration/concealed/wontfit/
                              duplicate taxonomy, adjacency + ignore-below
                              false-positive discipline, per-pair clustering,
                              severity escalation, viewer pins
    rfi_stamper/backcheck.py  the Backcheck: deterministic peer-check rules
                              (31) over PDF/Loft/pipe/DXF/OBJ in 6 categories,
                              each finding cites its rule; clash-lite lane
                              (GEO-CLASH-*/STD-SLEEVE via clash.py; _RuleSkip
                              = honest can't-evaluate notes); markup bridge
                              (findings -> cloud+callout annotations), Heartwood
                              lessons lane, honest SKIP list (GD&T/molding
                              need a solid part model; sleeve on PDF sources)
    rfi_stamper/cpm.py        the Tautline: precedence-diagram CPM over
                              project.ScheduleItem (workday math, FS + lag
                              via "<id>+N" depends suffix, entered start =
                              start-no-earlier-than, TF/FF, named cycle
                              refusal) — read-only; drives the Gantt's
                              critical-red bars + hollow float tails
    rfi_stamper/daybook.py    daily progress journal store + PDF log
    rfi_stamper/squawk.py     Squawk Box speech engine: winmm capture, MFCC+DTW
                              speaker-trained recognizer (pure numpy, offline)
    rfi_stamper/weaver.py     the Weaver: typed/spoken drafting agent -> Loft/
                              Pipewright commands (ask/refuse contract)
    rfi_stamper/reckoner.py   markup quantity takeoff + price book -> estimate
    rfi_stamper/crewpass.py   offline seat ledger + report (local JSON only)
    rfi_stamper/holler.py     Holler: hands-free voice control for ANY app —
                              the Caller (spoken-measure/shape -> text grammar
                              w/ format profiles), the Songbook (Trips/Placards/
                              Fetches/Runs + JSON/CSV), the Sender (user32
                              SendInput ctypes, HAS_SEND honest dry-run intents),
                              the Router (Songbook-then-grammar), the Ticker
    rfi_stamper/heartwood/    the knowledge core ("the bible"): SQLite KB,
                              from-scratch meaning search (random-indexing
                              vectors trained on the KB + trade thesaurus),
                              TextRank digest, number-locked restate,
                              TradeForge-KB import, two-lane self-learning
                              (auto signals + human-gated shop notes) —
                              trades-only by physics, honest refusals
    rfi_stamper/markups/      GUI-free markup data layer: model (+ PDF annot
                              writer), multiply, measure, toolchest
    rfi_stamper/gui/          tkinter app: app (nav shell), nav (animated
                              section bar), fx (animation framework: single
                              idle-when-done scheduler, quality tiers),
                              theme (color-theory palettes + SECTIONS hues),
                              crud (schema-driven module panels), bim3d
                              (canvas 3D viewer), dnd (from-scratch drop
                              router; tkinterdnd2 retired at v4.9.0) +
                              dnd_win32 (ctypes OLE IDropTarget backend,
                              HAS_NATIVE honest off-Windows), widgets, palette,
                              overlay, viewer, prefs (~/.planloom), pano (offline 360° site-photo viewer), tab_fieldstitch
                              (layout-points board), oldhand
                              (the Old Hand: global Heartwood Q&A drawer,
                              Ctrl+/ from any section), review_deck (the OCR
                              correction-review deck: mid-band + machine-
                              repair queue, keyboard-first, human-gated
                              Corrections.promote + firm FontProfiles,
                              overrides re-run, JSONL audit), squawk_deck
                              (Squawk Box voice deck), holler_deck (Holler
                              floating voice companion), tab_draft
                              (The Loft drafting board), tab_home,
                              tab_field, tab_project (incl. ResolutionBoard),
                              tab_plansbim, tab_reporting, tab_integrations,
                              tab_truth, tab_stamp, tab_merge, tab_markup,
                              tab_compare, tab_pdftools, tab_backcheck
                              (the Backcheck peer-check panel)
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
- raster.py NEVER goes through `bim.project_points` — its `depth <= _EPS`
  clamp is the painter's crutch and smears behind-camera triangles across the
  frame; the rasterizer clips at z=znear in camera space, then projects with
  the SAME viewport formulas so canvas overlays land on identical pixels.
  Painter and raster share ONE shading source (`raster.shade`/`mix_rgb`,
  int(round()) quantization) — a second formula or rounding drifts colors
  between modes. Raster golden tests use yaw=0/pitch=0 cameras (exact trig →
  bit-exact hash); rotated cameras assert structure, not hashes. The GUI's
  sticky `_raster_slow` painter fallback can trip DURING the set_model fly-in
  on slow boxes (xvfb!) — tests cancel the tween before asserting on the blit.
- The pick ray (`bim.screen_ray`) is the algebraic INVERSE of
  `project_points` — never inherit its `depth <= _EPS` clamp (a hit with
  t <= 0 is a miss) and never re-derive a second camera model. Möller-
  Trumbore must test `|det|` (two-sided): `det > eps` silently makes half
  the walls unpickable depending on orbit side. Section-clip-manufactured
  endpoints are NOT vertices — `clip_segment_box`'s cut flags exclude them
  from vertex snap (edge snap reaches them honestly); a box set exactly to
  `model.bounds()` must be a no-op (inclusive eps, bitwise-kept endpoints).
  The measure tape reads `bim.measure3d` = `fieldpro.deltas` — THE single
  delta source; viewer x=E, y=N, so deltas args are (y, x, z) order.
- Clash-Lite: `pipewright.run_z` is the ONE pipe-z source (viewer + clash;
  it returns the INVERT = pipe bottom — the capsule axis lifts +r).  The
  ignore-below threshold applies to OVERLAP, never raw distance.  The
  ternary-search box distance works because sd of a convex set along an
  affine segment is convex — NEVER reuse it on a union of boxes.  Pipe
  endpoints within `MERGE_TOL_FT` (0.05 ft) node-merge and fall under the
  adjacency exclusion (connected runs never clash at their fitting) — test
  scenes must offset run ends by MORE than that or they silently stop
  clashing.  Runs without inverts are excluded AND surfaced (skip note via
  `backcheck._RuleSkip`) — never guessed at z=0.
- drawdiff's line buckets have a theta=0/pi SEAM: direction flips there and
  rho NEGATES — the wrap probe unions `(nbins-1, -rb-1)` neighborhoods or
  near-horizontal lines randomly fail to group.  Members of a seam group
  project correctly because projection uses ENDPOINTS with the leader's
  direction, never the member's own u.  minipdf text is WinAnsi: there is
  NO Greek delta glyph — the redline's revision-delta tags are DRAWN
  triangles + a plain number.  Redline region markers are rectangles, not
  clouds (invariant #6 — clouded compare output needs owner sign-off).
- The review deck's single most important correctness rule: an accepted
  EDIT files per-glyph corrections ONLY when the edit length equals the
  glyph count — a length mismatch is a segmentation error, not a label
  (the cell↔char alignment is unknown); the text still flows to overrides
  and audit.  Promote the NORMALIZED `ng.cell` the classifier saw, never
  display crops (un-normalized features poison the kNN).  The machine
  repairs (index/lexicon/grammar snaps) are LIFTED to 0.95 — above τ_hi —
  so a pure mid-band review filter would hide exactly the tokens where
  the machine overrode the pixels; `_REVIEW_REPAIRS` queues them.
  `classify.default_ensemble()` is a process singleton — the deck holds
  it, so promotions reach the next OCR run without re-applying a profile.
- Resolution statuses are keyed by zero-filled RFI numbers (matching core's
  `zfill(3)`); `ResolutionStore.seed_from_records` never downgrades an
  existing status.
- Wire-format coordinate order differs per dialect (PNEZD/LandXML are
  N-first, the GSI fieldbook is E-first — WI 81 = Easting, DXF group 10 =
  X = Easting) and a swapped N/E imports without any error, mirrored about
  the N=E diagonal.  The order lives ONLY in `selvage.WRITER_ORDER`;
  every exporter calls `selvage.ordered()` — never inline it.
- `fieldpro.point_sigma`'s 1.5 mm target-centering default is SPECIFIED at
  a <= 1.5 m rod (adjusted 8' vial); the pole term charges only the tilt
  lever ABOVE that reference — that is what makes the brief's worked
  example land (~2.9 mm 1-sigma / ~0.22 in 95% for a 5" gun at 100 ft).
  Don't "fix" it to h*sin(vial/2) of the full height.
- Loft (draft.py) model space is decimal feet, y UP (= Fieldstitch world
  frame, E=x N=y); the GUI canvas flips y in its view transform only. Doors/
  windows are host-parametric (`pts=[]`, everything derives from host wall +
  `t`) — they ride wall moves for free, and deleting a wall CASCADES to its
  hosted doors/windows (remove() returns the full count). Paper-relative
  sizes (text, bubbles, dash patterns) convert via
  `model_ft = paper_in * scale_ratio / 12`.
- The Tracer's glyph-height scale (`tracer.components._median_glyph_h`) MUST
  exclude sub-despeckle speckle before taking the median. A speckled scan
  floods the box set with thousands of 1–2 px salt-and-pepper components; a raw
  median collapses `glyph_h` toward the noise height (~1 px), and the
  size-gate scaling (esp. the elongation gate `long_side > 4·glyph_h AND
  aspect > 8`, the one meant to PROTECT `I 1 l - . ' "`) then deletes every
  thin glyph as "linework". This masqueraded as an ~11% "segmentation" residual
  on degraded photocopies — it was 100% dropped thin glyphs (0 substitutions).
  Fixed in v4.7.1; `filter_glyphs`/`read_image` pass `dpi` through so the floor
  scales. The eval's speckle tier (`test_tracer_eval.py`, ≤2%) is the guard —
  keep it. Genuine degraded residual is now gen-3 double-weld copies +
  sub-legible text (OCR_PLAN §8), not thin-glyph loss — single-weld touching
  glyphs read clean since the P5 lattice (v4.17.0).
- The P5 word lattice (`segment.word_spans`/`_lattice_spans`) fails SILENTLY
  into the per-box path when no complete boundary path exists, so bugs there
  masquerade as "the lattice didn't help". Two hard-won rules: (1) the word
  crop is MASKED to the word's own component boxes — stray speckle (already
  rejected by `filter_glyphs`) otherwise blocks the no-ink free connectors,
  voids every path, and rides into a span's full-height ink trim (a speck
  above the dash read '-' as '); (2) the over-width confidence discount
  (OVERWIDE_LAMBDA past SEG_W_HI) models the weld-MASQUERADE band (dilated
  welds measure 0.84-0.92, Hershey welds ≤0.80) — a whole-box reading ≥
  SEG_SURE_CONF (0.95) is a genuine wide glyph and must never be discounted
  (the discount alone shredded a degraded conf-1.00 '0' into two '1's).
  Score WER only through `eval._charset_spaced`: `only_charset` on a spaced
  string strips the spaces (space is outside CHARSET), collapsing the page
  to ONE token and pinning WER at a constant 0%-or-100%.
- The Shuttle (minipdf reader/writer) laws, each bought with a debugging
  session: parsed PDF names CARRY their leading slash (`Name("/Root")`) —
  the first build stripped it and every trailer lookup silently missed;
  `/Parent` on page-like dicts is CUT on import and any ref to a dict with
  `/Kids` becomes null (one forgotten cut imports the whole source file
  behind every page); copied stream RAW bytes are never re-encoded (that is
  what makes untouched pages pixel-identical for free); every write runs a
  STRICT self-re-parse (recovery disabled) before bytes land — fitz/pypdf
  silently rebuild broken xrefs and hide writer bugs; `np.rot90` of an
  upright raster is NOT pixel-equal to rendering the /Rotate'd page (glyph
  antialiasing doesn't commute with rotation), and fitz redraws text-annot
  ICONS viewer-upright — compare same-orientation renders, `annots=False`
  where icons interfere.  Cross-backend renders can differ by a few AA
  pixels at some dpis (90/240) while byte-identical at 72/150/300/360 with
  identical texttrace glyph origins — assert parity at 150 dpi, geometry by
  texttrace, never chase the renderer's cache trivia.
- Draw-In (ifclite) import law: a wall's 'Axis' representation can carry
  RepresentationType 'SweptSolid', so the body-selection FALLBACK must
  exclude identifiers 'Axis'/'FootPrint' or it imports stick figures (hit
  in the zero-usable acceptance test).  Rectangle profiles are CENTERED on
  their 2D Position (±half-dims).  The unit scale applies ONCE, to final
  world vertices — scaling profile dims and placement translations
  separately double-scales, and directions are unitless.  IFC axes map to
  bim x/y/z with NO flip.
- minipdf's `Canvas.showPage()` has REPORTLAB semantics: it *ends* the page and
  the next page materializes lazily on the first draw, so the pervasive
  trailing `showPage(); save()` idiom never adds a blank page — and the default
  font is Helvetica-12, reset per page. An eager-append showPage masqueraded as
  "3 tests need re-baselining" during the cutover; it was one engine bug. The
  semantics probe in `tests/test_minipdf_parity.py` and the 1/1/2-page cases
  are the guard. Text must be WinAnsi single-byte (em dash 0x97, middot 0xB7):
  the ONE shared encoder in `minipdf/encoding.py` feeds both `string_width` and
  drawing — never add a second encode path or measurement and ink can diverge
  (box geometry drifts and `verify.py` FAILs).
- `layout.py` measures text through `minipdf.metrics.string_width`, which is
  held equal to the historical reportlab metrics to ~1e-13 by
  `tests/test_minipdf.py`'s oracle-parity test (kerning OFF). Changing width
  tables re-clips every header and moves every box — don't.
- The Cut Ticket census keys on EXPLICIT fixture tags only — never scrape
  tag-shaped text: `swatchbook.canonical_tag("P1")` -> "P-1" and the tag
  regex both collide exactly with sheet references ("SEE 2/P-1") and
  callout bubbles ("A-501"), so scraping fabricates fixtures.  Reconcile
  field ownership is law: machine facts (counts/sources/stencil/flags)
  always refresh, human fields (callouts/prefix/notes/status) are never
  touched, orphans tombstone (missing_from_model) and are never
  auto-deleted.  `DraftModel.remove()` takes a LIST of ids — a bare id
  string iterates its characters and silently removes nothing.
- The Swatchbook's stamp is its own APPROVED submittal standard —
  RGB(0.80,0.05,0.05), white fill, Helvetica-Bold 10.5, exact rect math —
  deliberately close kin to but DISTINCT from the RFI note-box law
  (invariant #2 governs note boxes; deviating from either gets the
  deliverable rejected).  Rotated manufacturer sheets are re-rendered via
  `show_pdf_page` onto fresh unrotated pages so the stamp lands in the
  VISUAL top-right with zero rotation math — never stamp in-place on a
  /Rotate page.  The renderer's save stamps a /Producer and a RANDOM /ID:
  every packet re-serializes through the Shuttle for metadata-clean
  deterministic bytes.  Double-stamping is a submittal rejection — the
  never-restamp guard refuses any component carrying tag-shaped text in
  page 1's top-right corner region; and a gap is NEVER silently
  substituted (ambiguous alias resolution returns None on purpose).
- Story Pole laws: a dimension string pairs with the nearest segment whose
  MIDDLE band contains the text's projection — ticks and extension lines
  sit near the ends, so the band test alone rejects them.  A single-bezier
  quarter arc yields too few circle-fit samples at 3 t-values — sample 5
  per curve or small doors vanish.  The `= 1'-0"` tail of a scale note
  parses as a legit dimension and lands in the outlier list as debris —
  tokens preceded by "=" are skipped.  PASS requires TWO evidence
  families (dimension self-agreement + doors or note): a half-size print
  is perfectly self-consistent, so self-agreement alone must refuse.
- Reed Count laws: principal-axis canonicalization is UNSTABLE for
  symmetric shapes (a square's axis is arbitrary) — pose search is a brute
  24-rotation x flip enumeration, and raw grid IoU is brittle to one-cell
  sampling drift, so scoring is a dilated-grid soft-F1.  Near-identical
  conventions (mop sink vs single-bowl sink: concentric rectangles both)
  must surface as AMBIGUOUS, never silently picked.  A water heater is
  just a circle — text-labeled stencils count ONLY with their label word
  inside the cluster bbox.  Size sanity vs the stencil footprint is the
  strongest false-positive killer (north arrows match wh at 0.85) and is
  why the Reed Count REQUIRES a verified scale (Story Pole or human cal).
- Chalk Mark laws: many manufacturer sheets are FILLABLE FORMS whose
  checkboxes exist only as widget annotations — `show_pdf_page` embeds
  page content only, so `build_packet` BAKES annots+widgets into the
  content first or those checkboxes silently vanish from the delivered
  packet (and from `get_drawings`).  Checkbox-row membership is the box's
  vertical CENTER inside the text band, never rect intersection — stacked
  option checkboxes sit ~10 pt apart and a ±2 pt band grazes the next
  row's box by a fraction of a point, fabricating 2-box refusals on clean
  rows.  Model matching is word-exact on the normalized join (up to 3
  words) — substring matching lets Z100 swallow Z1000.  The gates ignore
  boxless occurrences (headers/running text carry the model name on
  nearly every sheet); marking gates are one row, one box, pixel-empty —
  a skipped mark ALWAYS logs its reason and count.
- Drag-drop (gui/dnd.py) routes by REGISTRY + GEOMETRY, not widget stacking:
  the smallest viewable registered widget containing the drop point wins, the
  toplevel's own registration is the overlay's window-level enter/leave hook +
  fallback target, and every drop fires the leave hooks first (OLE sends Drop
  INSTEAD of a final DragLeave — the overlay must still hide). Callbacks are
  deferred `after(20)` past the OS drop handshake. In dnd_win32, every COM
  callback/vtable/object ref is PINNED in _KEEPALIVE for the window's lifetime
  — a garbage-collected WINFUNCTYPE callback is a use-after-free hard crash,
  invisible until a real drag on a real desktop. Never synthesize a real OS
  drag in tests; feed the Router directly (test_gui_construct.check_dnd).

## Summaries

`summarize.py` produces the note bodies deterministically and offline
(`OfflineSummarizer.summarize(rec)`, hooked into `pipeline.run` via the
`summarizer=` parameter). When Claude itself runs this engine (see
`skill/rfi-overlay/SKILL.md`), Claude should write the cliff notes directly
via the same summarizer hook.
