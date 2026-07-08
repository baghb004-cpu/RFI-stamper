# HANDOFF.md — session continuity for Planloom

Read this + CLAUDE.md first in any new session. This file records the naming
registry, the in-flight round, and the roadmap distilled from the product
owner's feature briefs, so work can resume mid-stream without re-asking.

## Current state (start of "Fieldstitch round")

- Product: **Planloom** v3.1.0, offline construction workspace; Python package
  keeps the historical name `rfi_stamper`. Seven sections behind an animated
  nav: Home, Field Management, Project Management, Plans & BIM, Reporting,
  App Integrations, Ground Truth.
- 18 green test scripts via `python tests/run_all.py` (GUI needs xvfb).
- Branch `claude/rfi-stamper-improvements-tw1e2c`; never push elsewhere.
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

- **fieldwire.py (new)**: LandXML 1.2 CgPoints export/import (N E [Z]
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
  fieldwire.WRITER_ORDER (see the CLAUDE.md gotcha). All ASCII CRLF
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
- tests/test_fieldwire.py + tests/test_harvest.py: 371 asserts + 43
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
