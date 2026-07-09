# HANDOFF.md — session continuity for Planloom

Read this + CLAUDE.md first in any new session. This file records the naming
registry, the in-flight round, and the roadmap distilled from the product
owner's feature briefs, so work can resume mid-stream without re-asking.

## Current state (rolling — see the newest Round note below for detail)

- Product: **Planloom** v4.10.0, offline construction workspace; Python package
  keeps the historical name `rfi_stamper`. Seven sections behind an animated
  nav: Home, Field Management, Project Management, Plans & BIM, Reporting,
  App Integrations, Ground Truth. Runtime deps: pymupdf + pypdf + numpy +
  stdlib — the OCR (Tracer), PDF writer (minipdf), drag-drop, voice and KB
  engines are all Planloom's own.
- 52 green test scripts via `python3.12 tests/run_all.py` (GUI needs xvfb).
- Branch `claude/planloom-session-resume-p10twg`; never push elsewhere.
- All invariants in CLAUDE.md hold — offline-always is #1.

## Naming registry (unique, untraceable, meaningful — USE THESE)

| Name | What it is | Why the name |
|---|---|---|
| **Planloom** | the product | a loom weaves threads into sheets; we weave answers into plan sheets |
| **Ground Truth** | analytics section | surveying term: verified on-site reality |
| **Fieldstitch** | layout-points studio (this round) | stitches the design into the field; points = stitches |
| **Bowline Kit** | export bundle: PNEZD CSV + DXF | the rigger's most trusted knot; the robotic-total-station tablet workflow |
| **Clovehitch Kit** | export bundle: XLSX + DXF | the stake-tying knot; the grid-layout tablet workflow |
| **Full Spool** | export bundle: everything (CSV+XLSX+DXF+job JSON) | empty the spool |
| **Sheetbend Kit** | export bundle: LandXML + PNEZD CSV | the knot that joins two different ropes — office suites + modern controllers |
| **Marlinspike Kit** | export bundle: GSI + SP-record fieldbook (.rw5) | the rigger's fieldbook spike; the classic fixed-width/record collectors |
| **Harvest** | model-to-points generators (proposals only) | harvesting stakeable points out of the model |
| **Gridiron** | the grid-intersection generator | the gridiron; NOT "building control points" (originality fence) |
| **Stride rules** | per-trade hanger/support spacing table | named for the walk stride; each row cites its basis |
| **Strata** | layer manager (visibility, color override, lock, filter) | geological layers |
| **Horizon Slice** | 3D elevation view-range clip (animated section cut) | slicing the model at a horizon |
| **Warp-Up** | animated boot splash | a loom's warp threads + warming up |
| **Daybook** | progress journal: measurements, comments, photo refs | the foreman's daybook |
| **Crewpass** | offline seat/device ledger + user reports | a crew's gate pass |
| **The Loft** | 2D drafting mode (draw plans from scratch) | the mold loft: the floor where full-size lines are drawn before anything is built |
| **Plies** | drafting layers | plywood plies |
| **Plumbline** | snap/ortho precision system | the plumb line: construction's oldest truth reference |
| **Stencils** | fixture/symbol library | plastic drafting templates |
| **Plates** | exported sheets with title block | drawings in old books are "plates" |
| **Binder** | Loft's left tree (plies/stencils/plates) | construction docs live in binders |
| **Traits** | Loft's right properties panel | plain word, no baggage |
| **Tally** | live takeoff readout in the Loft | tally counter |
| **Heartwood** | the knowledge core: SQLite KB + from-scratch meaning search | the dense center wood of a tree — the heart, engine and soul |
| **The Old Hand** | global Q&A drawer persona over Heartwood (Ctrl+/) | the worker who's seen it all and cites where he read it |
| **Journeyman** | the SAME semantic layer in the owner's other repo (JS) | between apprentice and master; knows the words of the trade |
| **The Backcheck** | the instant peer checker (deterministic design-review rules) | the senior's red-pen back-check before a drawing is issued |
| **Holler** | hands-free voice control for any external app (Phase H, in flight) | job-site "give a holler"; command your tools without touching them |
| **The Caller** | Holler's spoken-measurement → text grammar | the crew member who calls the cuts |
| **Trips / Placards / Fetches / Runs** | Holler command kinds (shortcut / text insert / open target / macro) | a trip fires a tool; a placard is exact posted text; a fetch retrieves; a run is a keystroke sequence |
| **The Songbook / The Ticker** | Holler's command dictionary / live counter tape | the book of what it knows; the running tally |
| **The Selvage** | the wire-format dialects module (LandXML / GSI / SP-record fieldbook / DXF attribute tier + the ONE coordinate-order writer table) | the loom's self-finished edge — the woven boundary where Planloom's weave meets the field instruments without fraying |

**Vendor-name policy (hard rule, from the owner):** never name third-party
companies or products (survey-tablet vendors, CAD/BIM authoring tools, PDF
competitors) in code, UI, comments, docs, or history. Describe compatibility
by FORMAT, generically: "PNEZD CSV consumed by robotic-total-station field
tablets", "XLSX + DXF consumed by grid layout tablets", "layer conventions
familiar from leading CAD/BIM authoring tools". This extends invariant #7.

## Last completed round: Fieldstitch + the visual pass (SHIPPED)

All items below are BUILT, tested (19 suites green) and pushed:

1. **`rfi_stamper/fieldstitch.py`** (engine, GUI-free, tested):
   - `PointLayer(name, color, visible, locked, category)`;
     `LayoutPoint(id, num, prefix, suffix, page, x, y, elev, desc, category,
     layer, created)` — full number composes `f"{prefix}{num}{suffix}"`.
   - `LayoutJob` — sidecar `<pdf>.stitch.json` (atomic writes), numbering
     state (next number, pad width), basepoint/rotation/units + scale
     (reuses `markups.measure.ScaleCal`), `to_world(pt) -> (N, E, Z)`.
   - Exporters: `export_csv_pnezd`, `export_xlsx` (hand-rolled minimal OOXML
     zip — no new deps), `export_dxf` (R12 ASCII: LAYER table with ACI
     colors, POINT + TEXT entities per layout point), `export_job_json`,
     `import_csv` (round-trip). Kits: `export_kit(job, dir, kit)` with
     `bowline` (CSV+DXF), `clovehitch` (XLSX+DXF), `fullspool`.
2. **Fieldstitch studio** (GUI, sub-tab in Plans & BIM): PDF or blank-grid
   canvas, place-point tool with live next-number preview, glowing point
   markers + labels, crosshair + coordinate HUD, Strata panel, point table
   with layer/category filter, prefix/suffix/pad/start controls, basepoint +
   rotation + scale, export kit dialog, CSV import, select/move/delete/
   renumber. Points persist automatically.
3. **3D/visual blitz**: Warp-Up boot splash (quality-gated, click-to-skip);
   Horizon Slice slider in the BIM viewer (animated clip); Fieldstitch points
   rendered as 3D pins on their sheet planes in the BIM viewer; camera fly-in
   on model load. All through `gui/fx.py`'s scheduler — zero idle CPU stands.
4. Tests for the engine + studio construct/export; screenshots; README/CLAUDE
   updates; commit + push.

## Round 2 (SHIPPED): Daybook · Extrude · Reckoner · Crewpass

- **daybook.py + Field Management → Daybook panel**: journal entries with
  photo refs and measurements, PDF log via transmittal.
- **extrude.py + Plans & BIM → "⌂ From plan…"**: vector linework of the
  Fieldstitch plan extruded into 3D walls (multi-floor) in the SAME world
  frame as the layout points — pins land inside the real building. Requires
  the Fieldstitch scale; vector plans only (raster -> ValueError).
- **reckoner.py + Project Management → Reckoner panel**: takeoff from markup
  counts/lengths/areas (per-page scale sidecar respected), priced by a local
  CSV price book (Reckoner name = "to reckon"), CSV + PDF exports.
- **crewpass.py + Tools → Crewpass dialog**: offline seat ledger
  (assign/transfer/release + report PDF), local JSON at ~/.planloom.

## Round 3 (SHIPPED): Lookout · Strata↔3D · hardening

- **gui/pano.py "Lookout"**: offline 360° panorama viewer — pure-numpy
  equirect→perspective reprojection (direction-verified in tests/test_pano.py),
  drag look, FOV wheel zoom, flat photos shown fitted. Daybook entries
  double-click into it.
- **BIM legend = Strata toggles**: click a system chip to hide/show that
  system in 3D (segments carry .system; filter composes with Horizon Slice).
- **Adversarial review round 2**: two agents swept every GUI file written in
  rounds 4–6 (fieldstitch studio, bim3d additions, plansbim wiring, pano,
  daybook/reckoner/crewpass panels, app/nav/crud/truth) — confirmed bugs
  fixed in place with construct-test regressions.

## Round 4 (SHIPPED): finish & merge

- VERIFIED stamp-slam celebration on a passing run (fx, quality-gated,
  click-dismiss); Home hero gains a slowly orbiting wireframe building
  (full-quality only, stops when unmapped); Gantt weekend shading.
- App icon: assets/planloom.ico + .png, wired into both exe targets in
  rfi_stamper.spec (datas bundle the png; gui/app.resource_path resolves
  sys._MEIPASS for frozen builds) and the window iconphoto.
- v3.1.0. Branch merged to `main` — GitHub default may still point at the
  work branch; switch in repo Settings → Branches if needed.

## Round 5 (SHIPPED, v3.2.0): The Loft — original drafting mode

Owner brief: a drawing mode that FEELS familiar to anyone who drafts in big
CAD/BIM suites but is a visibly original design (no ribbon, no cloned UI).
Built from an 8-agent industry-standards research pass + implementation:

- **`rfi_stamper/draft.py`** (engine): DraftModel (feet, y-up = Fieldstitch
  world frame), entity kinds wall/door/window/fixture/line/grid/room/text/
  dim/callout, Plies (layers w/ weight+linetype+halftone+lock), Plumbline
  snap engine (end/mid/intersection/perp/grid + ortho), real standards
  numbers (scale ladder, 3/32"-1/8"-3/16" text, pen-weight mm ladder, NCS-ish
  dash patterns, real wall thicknesses + fixture plan dims), undo×1000,
  atomic `.loft.json`, exports: Plate PDF (title block, north arrow, scale
  bar, auto-fit), DXF R12, PNG; bridges: takeoff_lines→Reckoner,
  to_bim→extrude/BIM viewer, grid_points→Fieldstitch.
- **`gui/tab_draft.py`** ("The Loft" tab in Plans & BIM): tool spool +
  per-tool options bar, Binder tree, Traits panel w/ live Tally (CountUp),
  drafting canvas (wheel zoom-at-cursor, middle-drag pan, snap glyphs, live
  temp dims, rubber band, window/crossing box select, Esc chain, single-key
  shorthand, Shift=ortho, Space rotate/flip), weave/ring placement
  flourishes (quality-gated), DnD `.loft.json`, recents kind "loft".

## Round 6 (SHIPPED): Heartwood + the Old Hand — the bible of Planloom

Owner brief: the trade AI from the other repo must live IN Planloom,
reachable from every tab, restricted to the trades, self-learning, with the
KB as "the heart, engine and soul — the instruction book, the bible."

- **rfi_stamper/heartwood/** — Python port of the Journeyman (bit-verified
  hashing parity with the JS): pure-Python BM25 store, random-indexing
  vectors trained on the KB, 135-pair trade thesaurus seed + miner with a
  human gate, hybrid meaning search + confidence gate (honest refusals),
  TextRank digest, number-locked restate; ingest: TradeForge db import,
  PDFs (fitz), text, answered-RFI capture; two-lane self-learning (auto
  usage/mining signals; factual notes land UNVERIFIED until trusted).
  Store: ~/.planloom/heartwood.db. tests/test_heartwood.py: 125 asserts.
- **gui/oldhand.py** — the Old Hand drawer: slides in over ANY workspace
  (status-bar button + Ctrl+/ + palette + Tools menu), cited answer blocks,
  unverified shop-note labeling, confidence bars, click-a-passage feedback
  (mark_used), related-term chips, Teach dialog, Manage dialog (imports,
  approvals, rebuild). Answered RFI scans auto-weave in as unverified notes.
- TradeForge side shipped separately on its repo branch
  `claude/tradeforge-journeyman` (154 selftest checks green).

## Round 7 (SHIPPED, v3.4.0): Fieldstitch Pro A1 — the QA loop

Phase A1 of ROADMAP.md, built from the 8-agent point-layout research brief
(the brief lives at the session task output; its content is condensed into
the implementation):

- **fieldstitch.py extended** (backward compatible): point kinds
  (CONTROL/DESIGN/STAKED/CHECK) + status lifecycle (PENDING→STAKED→
  VERIFIED/REJECTED, ISO-dated, bulk seeding never downgrades), label
  cap 16 + charset validation, number Spools per layer (control 1-99 …,
  quarantine 90000+, tombstoned retirees, mint never rewinds), witness/
  offset points (host-parametric, cascade, one-side-per-layer lint),
  wire-CSV export options (PNEZD/PENZD order, 3/4 decimals, code column,
  # header, .tag.txt sidecar w/ frame hash + checksum), import upgrades
  (sniffing, null-Z sentinels, collision policies, advisory validators).
- **fieldpro.py (new)**: 19 tolerance classes w/ basis strings ("verify
  against project spec"), Stitch Codes library (+ the 8 utility paint
  colors), delta math (TIGHT/SNUG/LOOSE on unrounded values, cut/fill),
  as-staked pairing ladder (id/block/desc/proximity/manual — '1' can
  never match '1001'), commit w/ never-downgrade, QAStore sidecar
  (.fieldqa.json), check-shot brackets, As-Staked Ledger PDF + _qa.csv,
  walking-route sort (NN + 2-opt, order only).
- **GUI**: QA bar (stitch code + kind on placement, Witness, Walk order,
  Wire CSV dialog w/ live first-3-lines preview, As-staked review dialog
  w/ human confirmation + frame-mismatch warning, Ledger PDF, QA CSV);
  pins now shape-code status (control=triangle, witness=tethered hollow,
  staked=square, verified=green tick square, rejected=orange X) —
  color-blind-safe verdict trio #009E73/#D55E00/#E69F00; table ST chips.
- tests/test_fieldstitch_pro.py: 243 checks; construct test drives the
  whole loop. Old sidecars load unchanged.
- Deferred to A2: LandXML/GSI/SP-record wire formats, DXF attribute
  blocks, Harvest generators, two-feet/CSF/Helmert coordinate upgrades,
  error-budget preflight, stake packages, Field Mode + gun profiles.

## Round 8 (SHIPPED): Fieldstitch Pro A2 — wire formats + Harvest (engine)

Brief sections 2, 3.2-3.6, 4, 5.5, 6.4; engine only, GUI next.

- **selvage.py (new)**: LandXML 1.2 CgPoints export/import (N E [Z]
  INSIDE the element, Units Imperial/Metric — linearUnit says WHICH foot,
  state proposed/existing <-> kind, namespace-agnostic import); GSI-8/16
  fieldbook (exact word map, feet digit 1 / meter digit 0, E-N-Z order,
  whole-file GSI-16 auto-switch on >8-digit data or ids — never mixed);
  SP-record fieldbook (JB/MO headers, UN0|UN1|UN2 encodes the foot, SF =
  CSF, null-EL excluded w/ warning unless EL0.000 opt-in, import scans
  ONLY SP lines and ignores all observation records); DXF attribute-block
  tier (LAYPT block, PT/ELEV/DESC ATTDEFs, INSERT+ATTRIBx3+SEQEND per
  point alongside the plain POINT, CAD layer-name rules enforced at
  creation via add_cad_layer). Coordinate order centralized in
  selvage.WRITER_ORDER (see the CLAUDE.md gotcha). All ASCII CRLF
  no-BOM atomic. Kits: sheetbend (landxml+csv), marlinspike (gsi+sp).
- **harvest.py (new)**: PURE generators returning proposal dicts
  ({n,e|x,y, elev, z_ref, name, desc, code, layer, provenance{gen,key,
  rule,params}, witness?}) — gridiron (explicit line runs or the Loft
  bridge), wall_corners (+inset, witness spec), along_line (stride w/
  remainder center|end, divide-N, insets, sloped-Z interpolation),
  offset_line (signed side, O/S lath grammar), bolt_cage (children
  -A/-B/-C/-D, BOLT_GROUP_NOTE), line_intersections (dedupe 1/16 in,
  extend toggle), reharvest_diff (unchanged/drifted w/ dN dE/orphaned —
  never auto-deleted/new). STRIDE_RULES per-trade size->spacing ladders.
- **fieldpro.py additions**: FT_INTL/FT_US + convert_units (exact meters
  only), unit_shift_tripwire (block > 0.05 ft), elevation_factor/
  combined_scale_factor/grid_to_ground/ground_to_grid/set_job_csf,
  fit_from_control (2-pt exact + Helmert LSQ, residuals + RMS,
  azimuth_of_plan_north = (360 - rot) % 360 — unit-tested), apply_fit,
  dms/format_dms, tape_check (agree/foot/csf/gross bands), point_sigma
  (verified vs the brief's worked example) + budget_check, StationLog +
  make_station + QAStore.stations/station()/session_uids, export_package
  (csv + _qa.csv + json + dxf + one-page _sheet.pdf manifest w/ fitz
  thumbnail, control table, layer legend, checkbox route, CSF statement,
  check-shot ritual). LayoutJob grew survey_anchor/csf/csf_origin/
  csf_parts (sidecar extras, lean).
- fieldstitch: apply_import_rows extracted from import_csv (all wire
  importers share collision policy; rows may carry kind/code — CONTROL
  imports locked); KITS + export_kit dispatch (test_fieldstitch.py kit-set
  assertion updated accordingly).
- tests/test_selvage.py + tests/test_harvest.py: 371 asserts + 43
  expected-error checks. All 39 suites green.
- Deferred to the next round (GUI): Harvest drawer (ghost pins + commit
  lever), story-pole band filter, Field Mode, gun profiles, residual
  arrows on-plan, Two-Ties/Setup cards, cut-sheet/stake-strip variants,
  JobXML import subset (3.6), as-staked XLSX ledger mirror.

## Round 9 (SHIPPED, v3.6.0): Pipewright — the piping engine (ROADMAP Phase B)

- **pipewright.py**: pipe runs as Loft "pipe" entities (flow = first→last
  vertex; system san/vent/storm/dcw/dhw/gas w/ per-system plies + line
  conventions — vents dashed, gas phantom), network graph, deterministic
  fitting derivation (elbow45/90 by angle bands, tee/santee/wye/combo by
  drainage rules + notes, reducers, p-traps/closet flanges at fixtures),
  command APIs shaped for the Weaver (cap_open_ends / replace_fitting /
  slope_run w/ downstream invert propagation / resize_run — each one
  snapshot = one undo), check() findings (slope-min per diameter "verify
  against project code", open ends, drainage crosses, downstream
  reduction; never silent fixes), takeoff → Reckoner, to_bim (sloped runs
  at their inverts, Strata-toggle systems).
- **Loft GUI**: Pipe tool (P) with system/size options, Slope run… (accepts
  "1/8, 98.5"), Cap open ends, Check ✓ findings dialog (double-click jumps
  to the run), pipe Traits (system/dia/slope/IE), size labels + IE
  annotations render on-plan, tally + To-3D include pipes.
- tests/test_pipewright.py: 241 checks (fall math pinned: 1/8"/ft over
  22'-6" = exactly 0'-2 13/16"). 40 suites green.
- Known flake (once, unreproduced): construct test failed one run_all pass
  then passed twice — watch under the next round's runs.

## Round 10 (SHIPPED, v3.7.0): the Weaver — type to the board, it draws
## (ROADMAP Phase C1)

- **weaver.py**: from-scratch command parser — fixed verb table (the
  Corral: no eval, mutations only through draft/pipewright APIs), verb
  frames w/ slot filling, ONE clarifying question at a time (ask/answer
  pending flow the GUI round-trips opaquely), target references (this/
  selected, "the wc" nearest-fixture, "the main" largest-dia run, "the
  open ends", grid addresses B-2, bare coordinates, entity ids, "here"),
  size/slope/system/fixture-slang lexicons, plain-words say strings in
  feet-inches, honest refusals naming the 3 closest verbs, one undo per
  command, optional Heartwood lane-1 learning (phrase memory + PROPOSED
  synonyms, never auto-approved). tests/test_weaver.py: 126 phrasings
  incl. the owner's verbatim: 'run 4" sanitary from the wc to the main at
  1/8 per foot' / 'slope this run at 1/4' / 'cap the open ends' /
  'replace that wye with a combo'.
- **Loft GUI**: the Weave bar (bottom of the board; "/" focuses it):
  Enter runs the command with context {selection, last click}; asks render
  as "? question [options]" answered in the same box (Esc clears);
  ✓ say echoed + toasted, ⚠ warnings appended, ✋ refusals; results
  selected + Traits refreshed. App wires the Old Hand's Heartwood store
  into the Weaver for phrasing memory.
- Construct test drives the flagship command end to end (draw-by-typing,
  ask→answer placement, refusal safety, cap, single-undo revert).
- Next: C2 Squawk Box (voice deck feeding the same bar), then Phase D.

## Round 11 (SHIPPED, v3.8.0): the Squawk Box — speaker-trained voice deck
## (ROADMAP Phase C2)

- **squawk.py**: winmm wave-in capture via ctypes (HAS_CAPTURE honest on
  non-Windows; Recorder w/ rotating buffers + live level), WAV store,
  from-scratch DSP (MFCC: pre-emphasis/mel filterbank/DCT-II/CMS;
  trim_silence) + banded vectorized DTW matcher; Deck (takes per phrase,
  deck.json, cached templates, fail-closed confident(): score < 1.8 AND
  gap > 0.4, per-call overridable); 22 suggested day-one phrases.
- **gui/squawk_deck.py**: device picker + level meter (poll only while
  recording), hold-to-talk button + F9, confident match fires the Weave
  bar, otherwise "did you mean…" buttons (never auto-fires); training
  pane (record takes, play, delete). Loft Weave bar gains 🎙 Squawk….
- tests/test_squawk.py: 120 checks on synthesized audio. 42 suites green.
- HONEST: the winmm call path itself is untested until the owner smokes
  it on real Windows hardware + mic; thresholds calibrated on synthetic
  tones (per-call overrides exist for field tuning); "zoom fit" phrase
  awaits a Weaver zoom verb (refuses honestly). Construct flake seen once
  more, unreproduced across 5 runs — xvfb race suspected, keep watching.

## Round 12 (SHIPPED, v3.9.0): the 3D uplift (ROADMAP Phase D)

- **bim.py additive**: Face dataclass + Model.faces, Segment.radius,
  wall_faces/tube_faces/exaggerate_z; extrude/draft to_bim gained
  faces=True opt-in (segment lists byte-identical either way — pinned by
  tests/test_bim_faces.py); pipewright.to_bim sets pipe radius.
- **bim3d.py**: Shaded mode (flat shading by face normal, painter-sorted,
  under the wireframe; default ON at quality "full" only), pipe runs as
  octagonal solids + "slope ×N" slider (1-10, render-time z-stretch,
  model never mutated), Walk mode (eye 5'-6", WASD/arrows fixed steps,
  drag turns, Esc restores the orbit camera, HUD position chip),
  NE/NW/SE/SW iso presets (one fx tween), depth-cued fading (6 cached
  buckets, off at quality "off"), 3D Measure (two endpoint snaps ->
  ft-in + ΔZ tape). Horizon Slice + Strata cull faces by centroid z
  (documented approximation). No free-running loops; zero idle CPU kept.
- Trimmed deliberately: no per-frame polygon clipping at the slice, no
  walk collision (per ROADMAP), wheel no-op in walk, wireframe always on
  top of faces. 43 suites green twice (+ the rare xvfb construct flake
  seen once, unreproduced x5).

## Round 13 (SHIPPED, v4.0.0): Weaver v2 — rooms by conversation (Phase E)

- Room macro: '<W> by <D> <noun> [at anchor] [with fixtures]' → 4 chained
  walls, hosted 3'-0" door, auto-numbered room tag, fixtures on the wall
  opposite the door (1'-6" end + 3'-0" o.c., documented constants), ONE
  undo, itemized say. Unknown fixture word refuses BEFORE any mutation.
- Multi-turn memory on the model (_weaver_memory): "make it 14 wide" /
  "move it 2' north" / "add another lav" / "delete that"; reshape
  recomputes the layout from the stored frame; no memory → honest ask.
- View verbs → result "view" key ({fit|in|out|goto, point}) handled
  append-only in the GUI weave(); closes the Squawk "zoom fit" gap.
- Questions answered while drafting: slope minimums deterministically
  from pipewright.MIN_SLOPE; anything else via Heartwood ask() with
  citations (unverified flagged) or the Old Hand referral.
- Pattern macros lane-2: save_macro(name) → UNVERIFIED origin-"macro"
  note; replay fires ONLY once trusted in the Old Hand Manage screen.
- tests/test_weaver.py: 168 phrasings total. 43 suites green.

## Round 14 (SHIPPED, v4.1.0): Corral hardening (Phase F — ROADMAP COMPLETE)

- **heartwood/corral.py**: LIMITS + compact() (feedback prune, orphan
  sweep, in-doc dedupe, per-doc caps, vocab prune at compact AND load,
  VACUUM; one transaction; note content never touched), provenance()
  (every learned item w/ origin; seeds disable-not-delete via tombstone),
  purge(), snapshot()/restore() (JSON learning bundle, statuses exact,
  pending never travels, idempotent), gauges() + growth series (8
  snapshots in hw_meta).
- **GUI**: Old Hand Manage gains Provenance tree + Purge + Compact now +
  Export/Import learning; Ground Truth gains a Heartwood card row (KB
  size, passages + growth sparkline, unverified queue, 7-day asks) that
  only renders when a store exists.
- **Red-team proven (tests/test_corral.py, 181 asserts)**: a hostile PDF
  ("ignore all previous instructions", fake commands, poisoned synonym,
  fake citations) ingests as data only — an 8-command Weaver session is
  BYTE-IDENTICAL with and without the poisoned store; hostile notes stay
  unverified and macro replay refuses until trusted; miner proposals
  never affect search until approved; caps/dedupe/round-trip all hold.
- 44 suites green. ROADMAP phases A-F: ALL SHIPPED (v3.4.0 → v4.1.0).

## Round 15 (SHIPPED, v4.2.0): the Backcheck — instant peer checker (Phase G)

- **backcheck.py**: 25 deterministic rules in 6 categories (data /
  ambiguity / geometry / standards / lessons / dfx) over PDF / Loft /
  Pipewright / DXF / OBJ; every Finding carries code + severity + detail +
  suggestion + the RULE that produced it; Report.sort/by_category/
  by_severity; markup bridge findings_to_markups (severity-colored cloud +
  comment callout) + write_markup_pdf (real annotations via
  markups.apply_to_pdf) + loft_finding_points; Heartwood lessons lane
  (record_lesson unverified/human-gated, lessons_from_heartwood trusted →
  LES-REPEAT). Honest SKIP list surfaced in stats.skipped: STD-HOLE-GDT,
  STD-SLEEVE, DFX-DRAFT-ANGLE (need a mechanical solid part model — a 2D
  plan/BIM app has none). tests/test_backcheck.py: 123 asserts, incl. a
  tidy room with ZERO findings (no false positives).
- **GUI gui/tab_backcheck.py** (Plans & BIM → Backcheck tab): run on the
  Loft / open plan / a file; severity + category filters; findings tree;
  jump-to-location (PDF → Plan Viewing page; Loft → select + center +
  flourish); "Write markups on the plan…" (real cloud+callout PDF);
  "Mark on the Loft" (Q-BACK ply text marks, delete-ply to clear); "Log
  as lesson"; "Not checked…" shows the honest skip list. Loft toolbar
  "Backcheck ✓" button; palette commands; app wires the Heartwood path.
- 45 suites green. HONEST BOUNDARY (docs + UI): native proprietary CAD/BIM
  containers are closed — export to PDF/DXF; structured Loft/pipe drafts
  give the strongest checks.

## Round 16 (SHIPPED, v4.3.0): Holler — hands-free voice control (Phase H)

- **holler.py**: a system-wide voice→keystroke layer for ANY app. **The
  Caller** — spoken-measurement/shape grammar → formatted text (owner
  examples verbatim: "one hundred five feet six and seven eighths" →
  105'-6 7/8"; "L two and one half by two and one half by one quarter" →
  L2 1/2x2 1/2x1/4), 7 format PROFILES. **Songbook** — Trips/Placards/
  Fetches/Runs, JSON + CSV round-trip (open in a spreadsheet). **Sender**
  — user32 SendInput via ctypes (HAS_SEND honest; DRY intent-recording on
  non-Windows; intent tuples ("char",..)/("key",..)/("down/up",mod)/
  ("wait",s)). **Router** Holler.dispatch (Songbook-then-Caller
  precedence). **Ticker** — history + command/keystrokes-saved counters.
  tests/test_holler.py: 227 headless checks.
- **GUI gui/holler_deck.py**: a floating always-on-top companion (global —
  status-bar ⟟ Holler button + palette + Tools menu): dimension-format
  picker, a type/say box (works with or without a mic), the Ticker tape +
  counters, the Songbook table editor (add/edit/delete, import CSV, open as
  spreadsheet), and 🎙 Voice… which opens the Squawk Box recognizer trained
  on the Holler deck. Reuses the Squawk Box as the ear; on non-Windows
  every keystroke is a labeled [preview].
- 46 suites green. HONEST: the winmm-sibling user32 SendInput path is
  Windows-only and UNTESTED on the build box (dry intent lists are the
  contract) — smoke real keystrokes on Windows before relying on it;
  opener defaults to dry off-Windows; URL Fetch targets opt-in + flagged.
- ROADMAP phases A-H ALL SHIPPED (v3.4.0 → v4.3.0).

## Round 17 (SHIPPED, v4.4.0): the Tracer P1 — from-scratch OCR scaffold
## (ROADMAP Phase I / OCR_PLAN P1)

- **rfi_stamper/tracer/** (new package, pure numpy + fitz, offline, no new
  deps): render (gray + polarity + cap-height upscale + too_small flag),
  binarize (Otsu + integral-image Sauvola + flatness router), deskew
  (quadrant + fine), linework (vectorized long-run strip), components
  (run-based vectorized 8-conn union-find CC — 24 MP in 0.38 s + §5
  geometric glyph gates), segment (lines/words/broken-merge; touching-split
  is a P2 stub), normalize (area-average → CoM 28×28), fonts (fitz base-14
  Helvetica+Courier synthetic prototypes, cached), classify (NCC cosine +
  margin + aspect tie-break; 43/43 self-classify), searchable (rebuild as
  /Rotate 0, invisible render_mode=3 runs, atomic, pixel-diff verified).
  Drop-in-compatible public API (available/info/needs_ocr/read_words/
  read_image/ocr_page_text/ocr_pdf + compat aliases). CHARSET = A-Z 0-9
  - . " ' / # & (43 classes). tests/test_tracer.py: 188 checks.
- **ocr.py and test_ocr.py UNTOUCHED** — the Tesseract path stays green;
  the Tracer runs beside it (P4 swaps ocr.py to a facade + removes
  Tesseract, only after the eval harness proves parity).
- **GUI**: PDF Tools gains "🔎 Make searchable (built-in)" → tracer.ocr_pdf
  (always available, no install) + a palette command; the existing
  Tesseract button is unchanged.
- 47 suites green. HONEST P1 limits (deferred, documented in OCR_PLAN §8):
  touching/broken glyphs, linework-fused text, sub-legible scans, thin
  marks (hyphen/period vs apostrophe), lowercase/hand fonts — the P2
  gradient-MLP+kNN ensemble and P3 lexicon/grammar/number-lock. NEXT: P2.

## Round 18 (SHIPPED, v4.5.0): the Tracer P2 — MLP/kNN ensemble + training
## (OCR_PLAN P2)

- **tracer/ upgraded**: features.py (8-dir gradient NCFE 512-D → PCA 142-D
  + 2 structural = 144-D), synth.py (Kanungo/Baird corpus: Helvetica +
  Courier + Hershey Type A/B × 0°/15°, 240/class ≈ 10.3k exemplars, 3
  severity tiers, seeded), fonts.py (public-domain Hershey single-stroke),
  classify.py (from-scratch numpy MLP 144→256→43, ~48k params, ~11 s
  train, **99.5% held-out**; kNN store; NCC bank; ensemble
  0.55·MLP+0.30·NCC+0.15·kNN + topology gate + temperature/reliability-bin
  calibrated confidence), segment.py (drop-fall touching-split + DP
  recombination), eval.py (CER/WER + auto-labeled real set via
  get_text("words")).
- **model.npz** (636 KB) trained once + committed; loaded lazily (runtime
  never retrains). Added to **rfi_stamper.spec** datas (both exes) so the
  frozen Windows build ships the trained model.
- Measured: **auto-labeled real-set CER 0.00%** on a 237-char clean
  300-dpi Helvetica uppercase paragraph (P2 green bar ≤2%); ensemble beats
  NCC-alone on a degraded set (0.91 vs 0.55); "P-101" now reads WITH the
  hyphen mark.
- ocr.py / test_ocr.py / GUI untouched; Tesseract path green. 48 suites
  green (new: test_tracer_p2.py, 130 checks). HONEST remaining: P3
  (lexicon/grammar/number-lock + sheet-index/dimension cross-checks for
  real-word errors like 0/O, S-101 vs S-107; degraded-photocopy CER
  ~6–9%); P4 (wire eval into run_all, remove Tesseract after parity). NEXT: P3.

## Round 19 (SHIPPED, v4.6.0): the Tracer P3 — lexicon/grammar/number-lock
## (OCR_PLAN P3)

- **tracer/lexicon.py** (new post-correction stage, gated behind optional
  kwargs so P2 behavior is byte-identical when no context): field routing
  (sheet/dim/word/num/mark), sheet-number cross-check against the document's
  OWN index (harvest_sheet_hints via core.SHEET_TOKEN — free
  self-supervision; a smudged S-1O1 snaps to the real S-101), dimension
  grammar validate/repair via holler.parse_dimension/format_ftin,
  confusion-weighted SymSpell word-snap to a trade lexicon (+ optional
  Heartwood terms) with char-3-gram back-off, garbage rejection (τ_lo/τ_hi).
- **NUMBER-LOCK** reuses heartwood.restate.number_multiset: digit strings
  never dictionary-corrupted, corrections never change the numeric multiset
  (a scanned 8' can never become 6'); the ONE sanctioned exception is a
  sheet-index snap to a token genuinely in the set. Proven in tests.
- **tracer/profile.py**: two-lane self-learning (auto-lane verified tokens →
  kNN store, capped/provenance-tagged; human-gated Corrections.promote) +
  per-firm FontProfile save/load (kNN sidecar keyed by producer).
- Measured: sheet-number field accuracy raw 95.0% → **corrected 100.0%** on
  a 100-sample degraded set w/ index cross-check (≥99% bar cleared).
  model.npz reused unchanged (confusion field already present; 3-grams built
  at runtime).
- **GUI**: PDF Tools "built-in OCR" now passes lexicon.Lexicon.default(), so
  ocr_pdf auto-harvests the set's sheet index and cross-checks every read
  (verified end-to-end: scanned S-101 corrected via the doc's own index).
- ocr.py/test_ocr.py/searchable writer untouched; Tesseract green. 49 suites
  green (new: test_tracer_p3.py, 85 checks). NEXT: P4 — wire the CER/WER eval
  harness into run_all, prove parity, REMOVE Tesseract.

## Round 20 (SHIPPED, v4.7.0): the Tracer P4 — Tesseract RETIRED (OCR_PLAN P4)
## == the from-scratch OCR goal, COMPLETE. Planloom now has ZERO external
## binaries.

- **tests/test_tracer_eval.py** (wired into run_all): CER/WER/field-accuracy
  harness proving the Tracer's bar — clean auto-labeled real set **CER 0.00%**
  (n=237, assert ≤2%), sheet-number field accuracy **100%** raw-90%→corrected
  via index cross-check (assert ≥99%), degraded photocopy tier CER 11.39%
  reported (loose ≤15% ceiling — honest residual, segmentation-dominated per
  OCR_PLAN §8). Added eval.align_pairs (Levenshtein backtrace) for the
  confusion/domain sub-metric.
- **ocr.py is now a thin FACADE over the Tracer** (279→104 lines):
  needs_ocr/ocr_pdf(P3 context on by default)/ocr_page_text delegate;
  tesseract_available()→True, tesseract_info()→builtin, OcrUnavailable kept as
  never-raised shims for import compat. Tesseract internals DELETED
  (_discover_tessdata/_SMOKE_CACHE/_require_tessdata/_KNOWN_TESSDATA/
  _fitz_can_ocr/pdfocr_tobytes/TESSDATA_PREFIX/shutil.which). test_ocr.py
  rewritten to the behavioral contract (proves the from-scratch engine).
- **GUI**: PDF Tools collapsed to one "🔍 Make searchable (OCR)" button (built
  in, no install messaging); tracer_ocr kept as an alias. __main__ ocr command
  de-gated. requirements.txt/README cleaned (no external OCR binary).
- Verified independently: 50 suites green, construct green, model.npz still
  bundled in the spec, ocr.ocr_pdf reads a scanned S-101 via the set's own
  index end to end. NO external OCR binary in any live code/dep/build.
- **OCR_PLAN P1→P4 COMPLETE (v4.4.0→v4.7.0). ROADMAP Phase I DONE.** Honest
  residual for the record: degraded-photocopy CER ~11% (word-segmentation
  bleed), real-word/real-number errors the index/grammar can't catch (by
  design — number-lock refuses to guess).

## Round 21 (SHIPPED, v4.7.1): the Tracer — the "11% degraded residual" was a
## BUG, not a limit; speckled scans now read clean

- **Root cause found.** The documented ~11% degraded-photocopy CER was NOT
  segmentation bleed "by design" — it was a latent bug. A speckled/noisy scan
  floods the connected-component set with thousands of 1–2 px salt-and-pepper
  blobs; `tracer.components._median_glyph_h` took a raw median over ALL boxes,
  which collapsed `glyph_h` toward the noise height (~1 px). The size gates
  scale by `glyph_h`, so the elongation gate — the one whose comment says it
  "protects I 1 l - ' " /" — then deleted every thin glyph as elongated
  linework. Measured: the 11.39% eval residual was **27 deletions, 0
  substitutions, 0 insertions** — 18 `I`, 5 `-` (sheet-number separators!), 4
  `.` — the classifier was never wrong; the glyphs never reached it.
- **The fix — ONE source change (verified).** `_median_glyph_h(boxes, dpi=300)`
  now excludes sub-despeckle boxes (the same area/side floor `filter_glyphs`
  already despeckles) BEFORE the median, so speckle can't vote on the
  glyph-height scale; byte-identical on a clean render (no sub-floor box).
  `filter_glyphs`/`read_image` thread `dpi` through so the floor scales.
  **Degraded prose CER 11.39% → 0.00%** across the whole blur×noise×salt sweep;
  the thin glyphs (incl. every hyphen/period) come back. Clean CER stays 0.00%,
  sheet-number field accuracy stays 100%.
- **Test made deterministic.** `test_tracer_eval.py`'s sheet-accuracy tier
  seeded its degradations with `hash()` (per-process randomized) — a flaky ≥99%
  assertion that could pass or fail run-to-run; now a deterministic per-sample
  seed. (This flakiness, not the glyph fix, was what first read 96.7%.)
- **A tempting second change was REJECTED by adversarial review.** The glyph fix
  recovers a decimal sheet's dot, which can then misread (`E-1.10` → `E-1P10`);
  a loosened `_sheet_shaped` would route that to the index snap and recover it.
  But a skeptic probe proved the loosening also drags non-sheet tokens (`A-4X8`,
  `A-40X`, `D-101A`) into the number-lock-EXEMPT sheet-snap path, letting the
  index rewrite their digits (`A-4X8` → `A-401`, {4,8}→{4,0,1}) — a NUMBER-LOCK
  violation (OCR_PLAN §4 / CLAUDE.md: "a scanned 8' can never become 6'"). Since
  the deterministic sheet accuracy is already 100% WITHOUT it, and production
  already promotes a bottom-right `E-1P10` to sheet via the region prior, the
  loosening bought nothing and broke an invariant — so it was dropped.
  `_sheet_shaped` is unchanged; a new P3 guard asserts `A-4X8` stays verbatim.
- **Eval redesigned to stay honest.** `test_tracer_eval.py`'s single degraded
  tier split into two: a **speckle-robustness guard** (blur+noise+salt asserted
  ≤ 2% — the regression guard against the glyph-height collapse ever returning)
  and an **honest touching-glyph tier** (heavy toner spread welds neighbors,
  CER 3.38%, real substitutions — the genuine OCR_PLAN §8 residual, loose ≤ 15%
  ceiling). New unit guards: `test_tracer.py` proves `_median_glyph_h` ignores
  speckle and that the collapsed height WOULD delete an `I` (P1 188→191);
  `test_tracer_p3.py` proves a stray-letter numeric body (`A-4X8`) is not
  mis-snapped to a sheet — number-lock stays fail-closed (85→86).
- 50 suites green (python3.12). Source touched: `tracer/components.py` +
  `tracer/__init__.py` only (`tracer/lexicon.py` reverted to unchanged).
  Supersedes Round 20's "honest residual ~11%" note: the remaining degraded
  residual is touching/broken glyphs + sub-legible small text, not thin-glyph
  loss.

## Round 21 (SHIPPED, v4.8.0): the mini-pdf writer — reportlab RETIRED
## == every PDF Planloom emits is now generated by its own from-scratch engine.

Owner goal: **everything from scratch** — retire reportlab (and, next,
tkinterdnd2) the way the Tracer retired Tesseract. Research + full build plan
in **MINIPDF_PLAN.md** (8-agent pass, per-track dossiers as appendices).

- **`rfi_stamper/minipdf/`** — the engine: `encoding` (WinAnsi str→bytes — em
  dash 0x97, middot 0xB7 — + PDF literal/hex/name escaping, shared by measure
  AND draw so they can never diverge); `metrics` (`string_width` equal to the
  reportlab oracle to |Δ|≤2.3e-13, no kerning; vendored checksum-guarded
  Core-14 widths + 256-entry WinAnsi table in `_metrics_data`); `content`
  (operator builder, y-up, deterministic numerics); `document` (byte-exact
  classic xref, content-hash `/ID`, NO metadata — fixes reportlab's
  timestamped-/Producer NDA leak); `canvas` (reportlab-`canvas` façade incl.
  Bézier circle/arc/ellipse, clipPath, dashes, duck-typed Colors, reportlab
  page semantics: lazy page after `showPage()`, Helvetica-12 default font);
  `colors`, `pagesizes`; `flow` (from-scratch platypus slice: ParagraphStyle/
  Paragraph/Spacer/HRFlowable/TableStyle/Table with header-repeating
  pagination + `SimpleDocTemplate` whose footer hook knows the real page total
  — no canvas-snapshot trick).
- **Proven parity where it must be exact:** stamp overlays pixel-IDENTICAL to
  reportlab under fitz (max |Δ|=0 at 90 AND 300 dpi) and the full stamp+verify
  pipeline green on rot-0 + /Rotate 90; Loft plates 0 px over the 25-gray
  verify threshold; `layout.py` now measures via `minipdf.metrics` so box
  geometry is byte-identical. Tables/forms/reports are the new engine's own
  clean layout (not pixel-cloned platypus; not verify-gated) — valid,
  paginated, header-repeating, Page-X-of-Y, qpdf-clean, metadata-free.
- **The retirement:** transmittal/reports/fieldpro are minipdf-only;
  stamp/draft default to minipdf with `PLOOM_PDF_ENGINE=reportlab` as the
  dev-box parity-oracle opt-in; test fixtures build their fake plans with
  `minipdf.Canvas`; reportlab is OUT of requirements.txt and excluded in both
  PyInstaller Analysis blocks; a meta-path-blocker proof imports every module
  and emits stamp/table/form PDFs with reportlab unavailable. The parity tests
  skip cleanly when reportlab is absent.
- **The flip needed ZERO test re-baselining** — the three "expected" failures
  were one real engine bug (page semantics), not output drift. Also fixed en
  route: empty (header-only) tables crashed the line painter; pypdf's /Info is
  dropped from delivered stamped PDFs (metadata-clean + byte-reproducible).
- 52 suites green (new: test_minipdf.py, test_minipdf_parity.py). NEXT (Track
  B): from-scratch ctypes drag-drop, retire tkinterdnd2.

## Round 22 (SHIPPED, v4.9.0): from-scratch drag-and-drop — tkinterdnd2 RETIRED
## == MINIPDF_PLAN Track B; the last third-party GUI extension is gone.

- **gui/dnd.py rebuilt as a two-layer design.** A pure, platform-neutral
  **Router** per toplevel: targets register via the UNCHANGED public surface
  (`HAS_DND`, `DND_FILES`, `make_root`, `parse_drop_paths`,
  `enable_drop(widget, cb, exts=, on_enter=, on_leave=)`); the backend feeds
  window-level screen-coordinate events (enter/move/leave/drop) and the router
  synthesizes per-target hover enter/leave as the cursor crosses widgets,
  routes a drop to the smallest viewable registered widget containing the
  point (toplevel registration = window-level hooks + fallback target), runs
  the ext filter (dirs always pass), fires leave-hooks on every drop (OLE
  sends Drop INSTEAD of a final DragLeave) and defers callbacks `after(20)`
  past the OS drop handshake.
- **gui/dnd_win32.py** — the native half, pure ctypes against ole32/shell32/
  kernel32/user32: a full 7-slot OLE `IDropTarget` vtable (QI/AddRef/Release/
  DragEnter/DragOver/DragLeave/Drop), CF_HDROP via IDataObject::GetData +
  DragQueryFileW (wide), QueryGetData-driven DROPEFFECT_COPY/NONE cursor,
  registration on the top-level frame HWND (OleInitialize on the Tk STA
  thread; the OS walks up from the child under the cursor), every COM ref
  PINNED for the window's lifetime, RevokeDragDrop on destroy, all Tk work
  bounced out of the COM callbacks with `after`. HAS_NATIVE honest (the
  HAS_SEND pattern): module imports everywhere, activates only on the
  platform the exes ship for; elsewhere HAS_DND stays False and every target
  advertises click-to-browse exactly as before.
- **overlay.py** now uses the façade only (no raw tkdnd calls): the toplevel
  registration IS the overlay hook; its full-window canvas is purely visual
  because the router routes by registry+geometry, not stacking.
- tkinterdnd2 OUT of requirements.txt; spec drops its collect_data_files +
  hiddenimports and excludes it (with reportlab) from both exes. widgets.py
  DropZone unchanged (the façade signature is identical).
- Tests: `test_gui_construct.check_dnd` drives the seam with synthetic
  backend events under xvfb — routing, hover synthesis, smallest-target win,
  ext filter, root fallback, refused-empty-drop, brace-quoted path parsing,
  dnd_win32 import + honest attach()→False off-Windows. (A real OS drag can't
  be synthesized headlessly — the research was explicit — so the OLE half
  needs the usual real-Windows smoke alongside Squawk/Holler's mic smoke:
  drag files from a file manager onto the exe's window, watch the overlay +
  DropZone hover + routed drop.)
- 52 suites green twice; scrub clean. **MINIPDF_PLAN Tracks A+B COMPLETE:
  Planloom's runtime is now pymupdf + pypdf + numpy + stdlib — every engine
  the app ships (OCR, PDF writer, drag-drop, voice, KB) is its own.**

**Deferred follow-up (tracked): decimal-sheet dot-misread.** The v4.7.1
noise-robust glyph-height fix now KEEPS a degraded decimal sheet's dot (e.g.
E-1.10) instead of dropping it; when it misreads as a non-confusable letter
(E-1P10) the index cross-check does not recover it (the naive loose-snap that
would was reverted in v4.7.1 for violating number-lock — it truncated real
suffixes like D-101A and changed digits like A-4X8→A-401). The **number-lock-safe
fix** is a loose sheet-route committed ONLY on a same-length substitution that
preserves the digit multiset (recovers E-1P10→E-1.10; blocks D-101A and A-4X8);
worth an adversarial pass before shipping. `test_tracer_p3` / `test_tracer_eval`
sheet-accuracy tests are now DETERMINISTICALLY seeded (were `hash()`-flaky) and
100% on their fixed sample.

## Round 23 (SHIPPED, v4.9.1): the airtight pass — 3-agent audit, bloat purge,
## missed-gap fixes

Owner ask: "analyze the whole code base, remove code bloat, see what we
missed — airtight before we move on." Three parallel read-only audits (dead
code / new-code correctness / cross-cutting consistency) + a ruff sweep,
findings hand-verified (multiline-import ref-counts) then fixed:

- **Real bugs found & fixed:** (1) dnd_win32 had NO ctypes restype/argtypes —
  64-bit pointer truncation meant every native drop returned zero paths on
  the shipped x64 target (invisible from POSIX CI); explicit Win64-safe
  prototypes + a tymed guard now. (2) merge._atomic_write leaked
  "/Producer (pypdf)" into merge/split/rotate + project-snapshot deliverables
  — scrubbed at the one choke point, asserted in test_merge. (3) flow.Table
  could force a too-tall first row into the bottom margin — split() now
  defers ([self]) and build() retries on a fresh frame; Paragraphs SPLIT
  across pages instead of drawing below the margin. (4) the Home tab's
  "Drop anything" zone routed to a no-op (set_router never wired) — wired to
  app.route_paths. (5) canvas guards: showPage with an open saveState and
  restoreState underflow now raise like the retired library; fmt_num(bool)
  can't emit "True". (6) packaging: heartwood/thesaurus_seed.json now rides
  in BOTH exe datas (frozen builds silently shipped an EMPTY thesaurus);
  seed load + Loft entity-drop on load now warn instead of silent loss.
- **Router lifecycle:** destroyed widgets/toplevels prune their entries;
  enable_drop answers False inside secondary toplevels (no false "live DnD").
- **Bloat purge (~350 lines):** fieldpro's dead engine-namespace threading +
  constant-false thumbnail block + dead constants; one shared fsutil
  atomic-write (4 byte-identical copies removed); ONE greedy word-wrap
  (layout.wrap = flow.wrap_text; reports shim inlined); dead facade members
  (grays/setPageSize/getpdfdata/text_lines/…), dead pagesizes, TableSpec,
  witnesses_of, viewer.reload, DND_FILES sentinel, tracer/holler/backcheck
  dead constants + write-only attrs, ~25 unused imports (package + tests).
- **Docs truth pass:** HANDOFF "current state" un-staled (was v3.1.0/18
  scripts); README suite count; CLAUDE.md ocr.py line + missing map entries
  (squawk/weaver/fsutil/pano/tab_fieldstitch); four "(reportlab)" docstrings;
  SKILL.md no longer pip-installs the retired library; ROADMAP Phases J/K.
- Un-gated the two minipdf tests that needed no oracle (the shipped
  reportlab-free config now runs the pipeline-verify + metadata-clean
  guards). New engine tests: table defer, paragraph split, canvas guards.
- Consciously KEPT (owner call, documented): the test-only public API tail
  (fieldpro coordinate math, the Selvage importers, etc. — HANDOFF-documented
  surface), stamp/draft's PLOOM_PDF_ENGINE oracle branches, tracer compat
  aliases, submittal's deliberate lazy-import guard.
- 52 suites green twice after every batch; scrub clean.

## Round 24 (SHIPPED, v4.9.2): module rename — the Selvage

The wire-format module's old filename collided with a third-party
construction-software product name (invariant #7 / the vendor-name policy —
owner caught it). Renamed everywhere in one pass: the module is now
**rfi_stamper/selvage.py — the Selvage** (the loom's self-finished edge,
where the weave meets the field instruments), its suite is
tests/test_selvage.py, and every import/doc/gotcha reference follows. The
public API is unchanged (WRITER_ORDER/ordered(), export_landxml/export_gsi/
export_sp/export_dxf_blocks, importers); only the module name moved. A
case-insensitive scrub confirms zero occurrences of the old name in the
tree. NOTE: the old name does persist in pre-rename git HISTORY (commits/
diffs); rewriting pushed history is destructive and was deliberately not
done — say the word if you want a history rewrite instead.

## Round 25 (SHIPPED, v4.10.0): BUILDOUT Phase A — raster images in minipdf

First phase of BUILDOUT_PLAN.md (the ten-phase from-scratch campaign; research
dossiers ride as its appendices). The writer gained the minimal ISO 32000
image slice — no codec was written:

- **minipdf/images.py**: `jpeg_info` reads width/height/components straight
  from the SOF frame header (SOF0/1/2; FF-fill + RSTn/TEM handled; CMYK,
  12-bit and non-JPEG refused loudly); `make_image` classifies JPEG
  bytes/path -> /DCTDecode PASSTHROUGH (file bytes ARE the stream) vs a
  fitz-pixmap duck-type (alpha==0, n in (1,3), stride repack guard) ->
  /FlateDecode of the raw samples (fixed zlib level; fitz and PDF image
  space are both top-row-first so samples go in UNTOUCHED).
- **document.py**: `_use_image` registry keyed by content sha256 (the same
  pixels never embed twice, across pages), image objects numbered after
  fonts, shared resources dict gains /XObject.
- **content.draw_image**: `q  w 0 0 h x y cm  /ImN Do  Q` — unit-square
  image space, balanced q..Q so the matrix never leaks.
- **canvas.drawImage** replaces the NotImplementedError guard (default size =
  intrinsic pixels as points; routed through the lazy `_c` page property).
- **fieldpro stake sheet**: the plan-thumbnail band is BACK — fitz renders
  the busiest page at 0.75x, the pixmap embeds directly (no PNG detour),
  layer-colored point pins draw on top; absent/raster plans keep the honest
  "(no plan thumbnail…)" fallback.
- Tests (test_minipdf.test_images + parity update): SOF units incl.
  refusals, Flate quadrant round-trip with the top-left-red ROW-ORDER guard,
  DCT byte-identity passthrough, dedup (three draws -> ONE /Subtype /Image),
  double-build byte determinism, alpha/PNG/zero-size refusals, live
  thumbnail + honest fallback both asserted end to end.
- SKIP list held (no PNG parser, no CMYK/alpha/EXIF/inline images). 52
  suites green twice. NEXT: Phase B — the BIM z-buffer rasterizer.

## Roadmap (still open)

- **Scan/point-cloud viewing, machine control, GNSS**: out of scope for an
  offline tkinter app — do not attempt; note in docs if asked.
- **Richer extrusion**: door/window gap detection, per-layer wall heights.
- **Digital plans from paper**: served by OCR + markup; keep surfacing it.
- **Lookout stereo/tiles**: very large (>30MP) panos could downsample-first.

## Session pickup checklist

1. `python3.12 tests/run_all.py` (use `xvfb-run -a` for the GUI test) — must
   be all green before and after your changes.
2. `grep -ri` for banned names (vendor/company/person) before every commit.
3. Engine modules get a plain-python test script in `tests/`; GUI behavior
   goes into `tests/test_gui_construct.py`.
4. Docs to keep current: README.md (user-facing), CLAUDE.md (invariants,
   repo map, gotchas), this file (state + naming + roadmap).
