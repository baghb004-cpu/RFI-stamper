# HANDOFF.md — session continuity for Planloom

Read this + CLAUDE.md first in any new session. This file records the naming
registry, the in-flight round, and the roadmap distilled from the product
owner's feature briefs, so work can resume mid-stream without re-asking.

## Current state (rolling — see the newest Round note below for detail)

- Product: **Planloom** v5.1.0, offline construction workspace; Python package
  keeps the historical name `rfi_stamper`. Seven sections behind an animated
  nav: Home, Field Management, Project Management, Plans & BIM, Reporting,
  App Integrations, Ground Truth. Runtime deps: **pymupdf + numpy + stdlib**
  (pypdf retired at v5.0.0) — the OCR (Tracer, with a human review deck and
  a touching-glyph lattice), PDF writer AND reader/merger (minipdf + the
  Shuttle), drag-drop, voice, KB, 3D raster, clash, vector-diff, CPM,
  IFC-import and cut-sheet-submittal engines are all Planloom's own.
- 61 green test scripts via `python3.12 tests/run_all.py` (GUI needs xvfb).
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
| **The Slipsheet** | vector drawing-revision diff + redline PDF (drawdiff.py) | slip-sheeting two vellums on a light table — the reviewer's oldest compare |
| **The Tautline** | CPM scheduler over the project store (cpm.py) | the taut-line hitch; the critical path is the one chain with no slack |
| **The Draw-In** | IFC/STEP building-model import (ifclite.py) | drawing-in: threading prepared warp — someone else's work — into your own loom |
| **The Shuttle** | from-scratch PDF reader/merger (minipdf parse/graph/pagemerge/io) | the loom piece that carries the thread back and forth — as this carries pages between documents |
| **The Swatchbook** | plumbing cut-sheet submittal builder (swatchbook.py) | a tailor's swatchbook is the bound book of cloth samples handed over for approval — a submittal packet is exactly that: product samples bound for the architect's sign-off |
| **The Cut Ticket** | model-driven pull list feeding the Swatchbook (cutticket.py) | the garment-trade production order that travels with a cut of cloth telling the shop what to make — the order the drawing writes for the cut sheets |
| **The Chalk Mark** | certainty-gated model-number checkbox marking inside Swatchbook builds (swatchbook.py) | a tailor's chalk mark tells the shop exactly where to cut — one small, deliberate mark, never a guess |
| **The Story Pole** | dimension-anchored witnessed autoscale (setscale.py) | the carpenter's rod marked with known lengths, used to transfer and VERIFY measurements — never trusted from one mark alone |
| **The Reed Count** | fixture-symbol recognition + auto-count (reedcount.py) | the reed count is the loom's dents-per-inch — THE density count of the trade, now counting fixtures per sheet |

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

## Round 26 (SHIPPED, v4.11.0): BUILDOUT Phase B — the BIM z-buffer rasterizer

Shaded mode gained per-pixel depth ("True depth"), fixing the painter's one
correctness hole: interpenetrating faces (a pipe through a wall, two
crossing walls) now resolve exactly, per pixel.

- **raster.py (new, GUI-free)**: numpy z-buffer over `bim.Face` polygons.
  Fan triangulation; camera space via the new public `bim.basis` (NEVER
  through `project_points` — its depth clamp is the painter's crutch and
  smears behind-camera triangles); Sutherland-Hodgman near-plane clip
  (perspective; the room the walker stands in stays visible), then the
  SAME viewport formulas as `project_points` so canvas overlays land on
  identical pixels. Pineda edge functions at pixel centers, inclusive >=0
  fill + strict-> depth ties in fixed draw order (no cracks, deterministic
  winner), depth as 1/z under perspective / camera-z under ortho,
  two-sided fill (open wall quads are unoriented — no backface culling,
  ever), per-triangle affine-evaluation fill loop (the measured-fastest
  shape; the fully-vectorized scatter variant degrades with bbox area —
  SKIPPED). `outline_mask` = fid/depth-discontinuity silhouettes
  (`soft_from` keeps the ground grid from outlining itself). Shading is
  single-source now: `lambert_bucket`/`shade`/`mix_rgb` ARE the painter
  formula; bim3d's painter path imports them (`_mix` delegates,
  `_LIGHT`/`_hex_rgb` removed).
- **gui/bim3d.py**: "True depth" checkbutton, default ON at fx quality
  "full" only (the old-hardware promise keeps reduced/off on the painter),
  always user-toggleable. The raster branch blits ONE PhotoImage (P6 PPM,
  reference pinned) beneath all canvas overlays — wireframe, sheet planes,
  pins, chips, measure, HUD stay canvas items, so click routing is
  untouched. Drags render at half resolution with hexagon capless pipe
  prisms, pixel-doubled up, refined on release (reuses `_lod`). Honest
  fallbacks with a hint note: > 6k triangles -> painter this model; still
  over SLOW_FRAME at half-res -> sticky `_raster_slow` until re-toggle or
  a new model. The ground grid becomes thin ground-plane quads in raster
  mode (correctly occluded by the building — an upgrade); painter mode
  keeps the stippled canvas grid via the shared `_grid_lattice`.
- **Tests**: `tests/test_raster.py` — hand-computed pixel-center coverage,
  crack-free fan diagonal, two-sided walls, an interpenetrating-X scene
  where the z-buffer is right AND a centroid-painter emulation is provably
  wrong, glancing-quad-vs-near-post 1/z guard, first-drawn z-tie
  determinism, clipper units (1->1 and 2->2 triangles) + no-smear +
  behind-camera-renders-empty, shade parity against an independent replica
  of the historical mix formula, outline-mask behavior, a golden sha256 at
  a yaw=0/pitch=0 ortho camera (exact trig -> stable hash; `PLOOM_REGOLD=1`
  re-mints), and a perf tripwire. test_bim's GUI portion: blit exists, no
  painter polygons in raster mode, overlays above the image, chip clicks
  route through it, toggle-off restores the painter. Gotcha pinned: the
  set_model fly-in must be cancelled before asserting on the blit — its
  slow xvfb frames legitimately trip the sticky fallback.
- SKIP list held (no textures/gouraud/MSAA, no side-plane frustum clip, no
  top-left fill rule, no scatter rasterizer, no per-pixel hidden-line
  wireframe, no threads/C extensions). 53 suites green twice. NEXT: Phase
  C — section box + 3D picking + measure-in-3D (the `fid` buffer is
  already the picking substrate).

## Round 27 (SHIPPED, v4.12.0): BUILDOUT Phase C — section box, picking, 3D measure

The viewer became an interrogation tool: cut the model open, pick real
geometry, read surveying numbers off the tape.

- **bim.py interrogation kernel (GUI-free)**: `screen_ray` — the exact
  algebraic inverse of `project_points` (ortho: origin varies per pixel,
  dir = fwd; perspective: the reverse; never inherits the depth clamp —
  t <= 0 is a miss). `ray_triangles` — Möller-Trumbore vectorized over
  (T,3,3), TWO-SIDED (`|det|`; `det > eps` silently makes half the walls
  unpickable). `fan_tris`, `clip_segment_box` — Liang-Barsky 3D returning
  the one surviving sub-segment + CUT flags (a clip-manufactured endpoint
  is not a real vertex), untouched endpoints bitwise-kept.
  `clip_poly_box` — Sutherland-Hodgman against the 6 half-spaces,
  inclusive eps (geometry ON a box plane survives; box at bounds is a
  no-op), sliver output filtered. `measure3d` — thin adapter over
  `fieldpro.deltas` (THE single delta source; viewer x=E y=N so args go
  (y, x, z)), adding SD, signed VD and pipe slope in inches/ft.
- **Section box (gui/bim3d)**: "Section" checkbutton -> box at padded
  model bounds; 12 dashed accent edges + 6 face-center square handles
  (tag `boxface:k`); dragging a handle converts screen motion to an axis
  move via px-per-world-unit projection (end-on handles dim and refuse —
  no divide-by-near-zero flings); double-click a handle resets that
  plane; last-moved plane glows (the Horizon-Slice idiom — the honest
  substitute for caps, which open quads/prisms cannot have). Everything
  obeys the box: segments (Liang-Barsky), model faces (S-H), pipe prisms
  (centerline clipped then re-extruded — never S-H 10 prism faces),
  sheet planes/pins (centroid in/out). Clipping is view-independent and
  CACHED (key: model, box, hidden systems, slice) so orbiting re-renders
  from the cache — the zero-idle promise holds. Composes with the
  Horizon Slice, which keeps its documented centroid-cull behavior
  (owner-approved cheap rule — deliberately not unified).
- **Picking**: `_pick` = vertex (12 px) > edge (6 px) > face (ray,
  front-most t), replacing endpoint-only `_snap_endpoint`. Candidates are
  the same visible/section-clipped/drawn geometry the frame renders; cut
  endpoints are excluded from vertex snap (edge snap reaches them,
  honestly); pipes pick on their centerline (drawn/exaggerated) and
  report TRUE geometry; faces only when shaded (visible = pickable).
  Picks through geometry — the wireframe-viewer norm, said in the hint.
- **Measure-in-3D**: same two-click shell, now with snap markers
  (square = vertex, diamond = edge, circle = face), a live rubber band
  (bound to <Motion> only while measuring — no after loops), and a
  two-line readout: `SD/HD/VD` (feet-inches) + `ΔN/ΔE` (decimal feet,
  matching the Fieldstitch HUD) + azimuth + pipe slope. All numbers from
  `bim.measure3d` = `fieldpro.deltas`, so the tape agrees with the
  As-Staked Ledger to the last digit by construction.
- **Tests**: headless — ray round-trip (seeded grid x cameras, both
  projections, < 1e-6 x depth), M-T vs a scalar reference on seeded rays
  + winding/parallel/behind cases, both clippers (bitwise-inside,
  cut-flag, on-plane survival, diamond-vs-box analytic area 92, sliver
  filter), box-at-bounds invariance over the whole demo building,
  measure3d parity with fieldpro.deltas to the last bit + azimuth 90 due
  east + 1/8"-per-ft slope. GUI — 6 handles, full-box no-op on drawn
  scene, handle drag moves the plane and drops geometry, vertex pick
  exact, cut endpoint demoted to edge, double-click plane reset, face
  pick on the wall plane, vertex-over-face priority, rubber band,
  SD/HD/VD/az label, Esc clears. Construct-test label assertion updated
  (ΔZ -> SD/HD/VD).
- SKIP list held (no caps, no rotated/multiple boxes, no measurement
  chains/angles/areas, no persistent 3D annotations, no occlusion-aware
  snapping, no midpoint/intersection snaps, no S-H for markers). 53
  suites green twice. NEXT: Phase D — clash-lite through the Backcheck
  (needs this phase's picking/highlight substrate).

## Round 28 (SHIPPED, v4.13.0): BUILDOUT Phase D — Clash-Lite

Deterministic MEP interference through the Backcheck, with the industry
coordination vocabulary and a hard zero-false-positive contract.

- **clash.py (new, GUI-free, stdlib math only)**: capsule-vs-capsule =
  the closed-form segment-segment closest distance (all degeneracies;
  parallel picks s=0 — same answer every run); capsule-vs-wall-box =
  exact signed distance to the box minimized by fixed-iteration ternary
  search (sd of a convex set along an affine segment is convex — ONE
  search replaces a page of Voronoi case analysis; never valid on a
  union of boxes).  Pipe capsules ride the NEW `pipewright.run_z` (the
  z-profile factored out of `to_bim` — one source, the viewer and the
  checker can never disagree), lifted +r (run_z is the invert = pipe
  bottom).  Runs with no invert are excluded AND counted — never guessed
  at z=0.  Taxonomy: hard (ignore-below 0.5" on OVERLAP, basis stated),
  clearance (opt-in knob, default 0 = off), penetration (a transverse
  wall crossing is SUPPOSED to happen — sleeve, not clash; a degree-1
  stub ending in a wall = fixture rough-in, demoted here too),
  concealed/wontfit (lengthwise in the wall: verify-cavity info vs
  physically-impossible major), duplicate (same-system near-coaxial —
  subsumes the hard spam).  False-positive discipline: network-adjacency
  exclusion (runs joined at a fitting never clash there; NOTE endpoints
  within MERGE_TOL_FT 0.05 ft node-merge — test scenes must offset
  more), inflated-AABB broad phase (flat i<j double loop — right at this
  scale; the escape hatch past ~2k segments is the floor-cell hash, not
  SAP).  Clustering: one ClashGroup per (kind, unordered pair) with
  count + worst overlap/witness; severity() escalates hard to blocker at
  overlap >= half the smaller diameter; pins() emits C1, C2, ...
  severity-colored for the viewer.
- **backcheck.py**: `_RuleSkip` exception — a rule can now register an
  honest "can't evaluate" skip note (distinct from a rule ERROR);
  `_Ctx.clash()` computes groups once, raises _RuleSkip when the model's
  pipes carry no inverts.  Five rules: GEO-CLASH-HARD (major/blocker),
  GEO-CLASH-CLEAR (minor, knob-gated), GEO-CLASH-DUP (info),
  GEO-PIPE-IN-WALL (major wontfit / info concealed), and **STD-SLEEVE
  GRADUATED from SKIPPED_RULES to a real rule** (Clash-Lite is exactly
  the MEP-vs-structure data its skip reason demanded) — it stays
  honestly skipped for PDF sources only (no pipe model there).  Details
  carry systems + trade-fraction overlap (fmt_dia_in), feet-inches
  location + Z (fmt_ftin), nearest grid intersection when grids exist;
  the pipe always reads first.
- **GUI**: `tab_draft.send_to_bim` runs clash after building the 3D
  model and hands pins to `_loft_to_3d(model, pins)` — pins render with
  the EXISTING stem/halo/label machinery, zero new viewer code; an
  empty send clears stale pins; the toast reports the pin count.
- **Tests**: tests/test_clash.py (16 checks) — kernel units (seg_seg
  incl. parallel determinism, sd_box signs, ternary boundary/plateau),
  run_z parity between capsules and to_bim, analytic overlap (2.8" on
  the crossing-mains case) + blocker escalation, ignore-below, CLEAN
  MODEL -> ZERO, full wall taxonomy, duplicate subsumption (and
  cross-system stays hard), per-pair clustering + worst-hit + pins +
  rerun determinism, Backcheck lane end-to-end (grid ref in details,
  graduation asserted), the no-elevation skip note, registry pins,
  clearance knob, perf tripwire.  test_backcheck's clean-loft
  assertion updated (STD-SLEEVE now neither checked nor skipped on a
  pipe-less loft); construct test asserts the pins bridge + clearing.
- SKIP list held (no 4D, no per-discipline clearance tables, no slab
  data source — the box math is slab-ready but Loft pipes carry no
  risers, so slab boxes would be dead code; no mesh/BVH clash, no
  cross-source clash, no clash-management workflow, no insulation
  radius default, no self-clash).  54 suites green twice.  NEXT:
  Phase E — vector drawing diff (addendum redline).

## Round 29 (SHIPPED, v4.14.0): BUILDOUT Phase E — the Slipsheet (vector diff)

Revision compare on the VECTORS: what linework changed between two
issues of a sheet, clustered into numbered change regions, written as a
deterministic redline PDF.

- **drawdiff.py (new, GUI-free)**: the whole diff is 1-D interval
  algebra per infinite line — segments from both revisions land in
  (theta, rho) buckets (rho measured from page CENTER; 3x3 union-find
  over cells, with the theta=0/pi SEAM probe where direction flips and
  rho NEGATES — miss it and horizontal-ish lines randomly fail to
  group), each bucket's segments become intervals along the leader's
  direction, collinear chains merge (GAP_TOL 0.75 pt — below dash gaps
  so dashed linetypes stay dashed), and added/removed are interval
  differences.  Splits, merges, extensions and partial erasures all
  fall out of ONE code path: the classic false diff (a line re-exported
  as two touching pieces) merges back and diffs to nothing BY
  CONSTRUCTION.  Registration first (the whole game): align.auto_align
  applied rotate-about-center-then-shift when score >= 0.35, or a
  caller AlignResult; low-confidence warns "sheets may not correspond".
  Word layer (text is invisible to get_drawings; a changed dimension
  is the worst silent miss): get_text("words") through rotation_matrix
  (the sheets.py trap), NFC + nbsp-normalized, (text, 2-pt-grid) multi-
  set diff.  Region clustering on a 24-pt grid, one issue per region,
  ordered by change magnitude.  extract_segments reused (min_len 1.5,
  cap raised to 20000 — a one-sided cap MANUFACTURES diffs; cap-hit
  warns).  Raster pages surface extract_segments' honest ValueError.
- **redline_pdf**: unchanged linework gray 0.78 (context), REMOVED
  dashed house-red (the demolition-plan convention), ADDED solid blue
  (align.py's exact overlay blue — raster and vector compares speak one
  color language), change regions boxed DASHED-RED with a DRAWN
  revision-delta triangle + number (WinAnsi carries no Greek delta
  glyph — minipdf would print "?").  NOTE: region markers are
  rectangles, NOT revision clouds — invariant #6 reserves cloud shapes
  and clouded compare output needs the owner's explicit sign-off;
  flip to `markups.cloud_path_points` outlines only after that
  sign-off.  Legend + totals + alignment note + warnings on the page.
  minipdf bytes are deterministic (test-pinned).
- **GUI**: "Vector diff PDF…" button in the Compare tab (run_bg,
  honest failure toast, warnings to the log); uses the tab's manual
  alignment when one is set, else auto.
- **Tests** (tests/test_drawdiff.py, 11 checks): exact echo, counted
  edits incl. a move, THE collinear split/merge case + 0.5 pt gap
  variant + mirror (all zero), extension length, rigid transform
  (rotation-sign pinned end to end + translation through the REAL
  auto_align), 3-corner clustering + delta-ordering, word layer,
  /Rotate 90 parity, deterministic renderer bytes + legend + tag
  numbers, honest failures (raster ValueError, size-mismatch warning),
  full-report determinism.
- SKIP list held (no bezier canonicalization — chord diffs are a
  documented limitation; no scale recovery on size mismatch; no
  multi-sheet auto-pairing — single page-pair like the raster compare;
  no hatch suppression — a re-hatched area IS a change, the pt-length
  totals let the reviewer recognize restyling).  55 suites green
  twice.  NEXT: Phase F — the CPM scheduler.

## Round 30 (SHIPPED, v4.15.0): BUILDOUT Phase F — the Tautline (CPM)

Textbook precedence-diagram critical-path scheduling over the EXISTING
project store — zero schema migration.

- **cpm.py (new, stdlib-only, read-only)**: `ScheduleItem.depends`
  already carried prerequisite ids; an optional `+N`/`-N` suffix is an
  FS lag in workdays (bare id = lag 0 — backward compatible).  Workday
  calendar (weekend mask, holidays SKIPPED as per-project data entry):
  `to_index` = workdays strictly before d from the anchor (works for a
  weekend anchor), `from_index` its inverse; durations = inclusive
  workday count of [start, end], clamped to 1 with a warning for
  weekend-only rows.  THE convention, stated once: ES/EF are MORNING
  indices — an activity occupies s..s+dur-1, EF = ES+dur, finish DATE =
  from_index(EF-1) (the classic CPM off-by-one lives here).  Forward
  pass takes max(pred EF+lag, entered-start-as-SNET, 0) — without the
  SNET term the computed schedule contradicts the user's own bars;
  backward pass, TF = LS-ES, FF = min(succ ES - lag) - EF.  Kahn topo
  sort; a dependency CYCLE refuses the whole analysis and NAMES the
  loop by title — never a hang, never silent link-dropping.  Dirty
  data (hand-edited JSON): junk dates and dangling preds skip with
  per-row warnings.  Deterministic: stored order breaks all ties.
- **Gantt (gui/tab_field.ScheduleView)**: analyze() runs ONCE in
  refresh(), stashed; `_draw_at` only reads it (the fx scheduler calls
  _draw_at per animation frame — per-frame CPM would violate the
  zero-idle rule).  Critical bars: theme error red, heavier outline
  (sweep-in untouched).  Total float: hollow dashed tail from the bar
  end to the late-finish date (omitted at TF 0); the chart window
  extends to the computed project finish so tails never clip.  One
  muted caption ("critical path red · dashed tail = total float") and
  one muted note line for cycles/warnings — never a modal.  The
  existing TODAY line stays the data-date line.
- **Tests** (tests/test_cpm.py): the hand-computed textbook network —
  A(3), B(4), C(2)<-A, D(5)<-A,B, E(4)<-C+1, F(3)<-D, G(2)<-E,F —
  asserted CELL BY CELL (the C->E lag splits TF from FF: C has TF 2
  FF 0, E has FF 2), critical path B-D-F-G, project finish across
  weekends; calendar round-trips; 2- and 3-node cycles named; dirty
  rows warned and skipped without sinking the rest; SNET recompute
  (C moved to workday 5 goes critical); lag parsing (ids keep their
  hyphens); negative-lag clamp; determinism.  Construct test: Gantt
  with a real project draws critbar + floatbar items, no CPM call
  from inside _draw_at.
- SKIP list held (no SS/FF link types — FS is ~90% of construction
  logic; no holiday calendars; no resource leveling; no write-back —
  an explicit "reschedule" action is the only thing that should ever
  write dates, and it does not exist yet; no logic-arrow overlay).
  56 suites green twice.  NEXT: Phase G — the OCR correction-review
  GUI.

## Round 31 (SHIPPED, v4.16.0): BUILDOUT Phase G — the OCR review deck

Human-in-the-loop confirmation of uncertain OCR reads — the
professional verification-station shape, wired into the machinery
profile.py already promised.

- **Engine taps (transparent no-ops by default)**:
  `read_image(review_sink=)` appends a `ReviewItem` per QUEUE-WORTHY
  read — the mid-band (τ_lo <= conf < τ_hi) plus every machine repair
  the corrector CHANGED (`_REVIEW_REPAIRS`: index/lexicon/grammar
  snaps — those were lifted to 0.95, above τ_hi, so a pure mid-band
  filter would hide exactly the tokens where the machine overrode the
  pixels).  Each item carries raw + corrected text, conf, why, and the
  per-glyph NORMALIZED 28x28 cells (the only thing safe to promote).
  `write_searchable(review_sink=, overrides=)` stamps pages into the
  sink and honors `{(page, bbox): text}` overrides just before
  insert_text (empty text = read rejected) — the deck re-runs the
  writer with accepted texts, never in-place PDF surgery, and the
  existing pixel-diff verify re-proves the raster untouched.
  `tracer.ocr_pdf` / `ocr.ocr_pdf` thread both through.  Sink=None is
  byte-identical to before (test-pinned).
- **gui/review_deck.py (new)**: Toplevel deck.  One Treeview of DATA
  rows (never a widget/PhotoImage per row — detail-pane-only rendering
  IS the tk virtual list); detail pane = integer-zoomed word crop from
  the page raster + per-glyph cell strip (char + conf, mid-band
  tinted) + editable Entry.  Keyboard-first: Enter accept, Tab skip
  (returns "break" or tk traversal eats it), Shift+Tab back,
  Ctrl+Enter batch-accept above a spinbox threshold (audit-tagged
  "batch"), Esc close (confirms when undecided remain).  THE
  correctness rule: an accepted EDIT files per-glyph corrections only
  when edit length == glyph count — a mismatch is a segmentation
  error, not a label; the text still flows to overrides + audit.
  Corrections are PENDING until the explicit "Promote N corrections…"
  button (Corrections.promote into the process-singleton ensemble —
  the same object the next OCR run uses), which then offers "save as
  firm font profile" (~/.planloom/fontprofiles/<label>.npz; producer
  metadata is often NDA-stripped, so the label is user-typed).
  "Apply N accepted…" re-runs the writer with overrides under run_bg.
  Append-only JSONL audit (~/.planloom/tracer_reviews.jsonl, one
  record per decision, atomic on close).
- **tab_pdftools**: `_run` gained an `after=` success hook; the OCR
  action collects the sink and lights "Review uncertain reads (N)";
  `open_review` builds the deck with a rerun closure that repeats
  `ocr.ocr_pdf` with the same lexicon config + overrides.
- **Tests** (tests/test_review.py, engine + xvfb deck halves): tap
  no-op byte-identity + mid-band predicate; end-to-end sink through
  ocr_pdf (a rendered "PLUMBNG" snaps to "PLUMBING", queued as a
  machine repair with the page stamped); overrides replace/reject +
  deterministic (page, bbox) keys across runs; the human gate
  (record does NOT train, promote does, FontProfile save/load/apply
  round trip on FRESH load_ensemble() instances — never the
  singleton); deck: rows, detail images, accept/skip/batch handlers
  called directly, the alignment fence (mismatched edit files zero
  glyph corrections), overrides dict, apply-through-rerun, audit
  JSONL (4 records, atomic, redirected out of $HOME).
- SKIP list held (no re-segmentation UI — text-edit only; no per-row
  thumbnails; no auto-promotion or background retraining; no undo
  stack, multi-doc sessions, or reviewer accounts; no τ threshold
  editing from the deck; dropped `< τ_lo` reads stay a count, not a
  tray).  57 suites green twice.  NEXT: Phase H — Tracer P5, the
  touching-glyph residual (the review data now exists to feed it).

## Round 32 (SHIPPED, v4.17.0): BUILDOUT Phase H — Tracer P5, the touching-glyph lattice

The touching/broken-glyph residual OCR_PLAN §8 names, taken head-on:
one split+merge recombination lattice per word, scored by classifier
confidence + a char bigram prior.  Touching-tier CER 3.38% → **0.00%**
(WER was a constant "100%" — partly a metric artifact — now 0.00%);
clean/speckle stay 0.00%; sheets 100%.

- **segment.py — the P5 lattice**: `_lattice_spans` is a Viterbi over
  (boundary, last char): candidate segments are batch-classified once
  (never per-segment in the loop), channel term `α·ln p` **weighted by
  width** (per-unit-evidence: a confident single misread of a weld can't
  win just by having fewer terms), `(1−α)·ln P(c|c')` bigram transitions,
  `^`/`$` anchors, deterministic tie-breaks.  FREE CONNECTORS (no-ink
  single-step boundary pairs) carry state across ordinary letter gaps —
  without them the word lattice has NO complete path and silently falls
  back per-box.  `candidate_cuts` got the ascending drop-fall variant
  (baseline welds) + a stroke-width neck test.  `dp_recombine` rides the
  same lattice (whole box always competes via `always`); `word_spans`
  (new) runs one lattice per word with per-box cuts, mergeable-gap fences
  (MERGE_GAP_FACTOR, MERGE_MIN_H — marks are not merge fodder), and a
  fast path so clean pages never pay for it.
- **Over-width discount, honestly bounded**: a weld reads as a CONFIDENT
  single char (dilated welds measured 0.84–0.92, Hershey welds ≤ 0.80), so
  beyond SEG_W_HI that confidence is discounted (OVERWIDE_LAMBDA) — but a
  whole reading ≥ SEG_SURE_CONF (0.95) is a genuine wide glyph (M/W/0/Q
  measure ≥ 0.95) and is NEVER discounted: the discount alone was
  shredding a degraded '0' (conf 1.00) into two '1's.
- **Masked word crop**: the word lattice's crop contains ONLY the word's
  own component boxes.  Stray speckle (already rejected by
  filter_glyphs) otherwise blocks the free connectors — voiding the
  lattice into the per-box fallback — and rides into a span's
  full-height ink trim (a speck above the dash read '-' as ').
- **lexicon.py — the bigram prior**: 44×44 ln P(cur|prev) with `^`/`$`
  anchor row/col, add-k over the lexicon words + domain shape strings
  (sheet/dimension patterns), cached; `centered=True` subtracts row
  entropy (length-neutral, for reliable lines), floored at −2.5 so an
  unseen bigram can't single-handedly veto a correct split.
- **read_image — word re-tokenization**: toner dilation shrinks
  inter-word gaps below Wong's rule and fuses words with no weld at all;
  after the lattice, Wong's rule + an outlier test (RETOK_*) over the
  FINAL spans re-opens real spaces (never before a trailing mark).
- **eval.py**: WER was scored on `only_charset` of the SPACED string —
  which strips spaces, collapsing the page to one token (a constant
  0%-or-100% artifact); `_charset_spaced` restricts per-token instead.
- **test_tracer_eval.py**: clean bar tightened to **== 0** (deterministic;
  any lattice graze fails loudly), touching ≤ 2% + WER < 50% (hard bars),
  new gen-3 double-weld tier (≤ 20%, must be > 0 — the suite always
  tracks a real residual; measures ~16%), `test_p5_units` (snapped-H
  merge, trailing-period fence, determinism).
- SKIP list held: no word-level language model beyond char bigrams, no
  beam search (Viterbi is exact here), no per-font lattice retuning, no
  touching-glyph handling in the P1 `split_touching` stub (kept only for
  API compat), gen-3 stays a tracked tier — not a target.  57 suites
  green twice.  NEXT: Phase I — IFC-lite import (bo_E.md).

## Round 33 (SHIPPED, v4.18.0): BUILDOUT Phase I — the Draw-In (IFC import)

IFC building-model exchange files (STEP / ISO 10303-21) import as
walls/slabs/columns straight into the 3D viewer — `ifclite.py`, one
module, stdlib + numpy, zero new deps.  The partial-importer contract:
never crash on unknown entities, coverage stats instead of silence.

- **Parser**: two-phase lazy — pass 1 is one string/comment-aware O(n)
  scan indexing `{id: (TYPE, args_pos)}` (strings legally contain
  `;()#,` — split-on-semicolon corrupts the index; the head match is
  ANCHORED at the record start so a `#5=IFCX(` inside a string can't
  false-index); pass 2 is a memoized recursive-descent arg parser
  (`$ * ints trailing-dot/E-005 reals strings refs enums nested lists
  typed-values binary`).  Only the product closure is ever parsed —
  unknown entities and forward references are free.  Full STEP string
  escapes (`'' \\ \\S\\ \\PA\\ \\X\\ \\X2\\ \\X4\\` + tolerant raw-UTF-8
  re-decode).  `.ifczip` sniffed by zip magic, never extension.
- **Units first**: SI-prefix table + conversion-based units (FOOT/INCH
  via IfcMeasureWithUnit); missing unit block → metres + a loud warning.
  The scale applies ONCE to final world vertices (target unit: decimal
  feet, the Fieldstitch/Loft frame; `target_unit="m"` accepted).
- **Placement**: IfcAxis2Placement3D with Gram-Schmidt (exporters emit
  non-perpendicular RefDirections; without it geometry shears), memoized
  IfcLocalPlacement chains with cycle guard, non-identity
  WorldCoordinateSystem composed (attr 4 — attr 3 is Precision).
- **Geometry**: IfcExtrudedAreaSolid over rectangle (CENTERED on its 2D
  Position), arbitrary-closed polyline (repeated closing point dropped),
  circle (16-gon) and IFC4 indexed-polycurve (line segments; arcs skip
  honestly) profiles; one level of IfcMappedItem indirection
  (RepresentationMap ∘ CartesianTransformationOperator3D, uniform scale
  only).  'Body' representation selected explicitly — the fallback
  EXCLUDES 'Axis'/'FootPrint' identifiers even when swept-typed (an Axis
  rep typed SweptSolid imports stick figures otherwise; caught by the
  zero-usable acceptance test).  bim mapping mirrors load_obj: Faces per
  ring + shared-edge-deduped wireframe Segments, system colors walls/
  slabs/columns for the Strata legend.
- **The report contract** (keys frozen in the test): schema, unit_scale,
  target_unit, imported, skipped `(id, class, reason)`, unsupported_counts,
  storeys, warnings — every candidate lands in imported or skipped, the
  two sum to the candidate count, a malformed product becomes a skip
  reason (never a crash), zero imports raises ValueError WITH the skip
  summary.  Viewer: "Open IFC…" beside Open OBJ; the coverage report
  shows in an info dialog after every load — a partial import must never
  look like a full one.
- Tests (`tests/test_ifclite.py`, 90 checks): exact feet vertices, IFC2X3
  == IFC4 geometry, rotation/nested-placement/tilted-axis math, the unit
  matrix (metre == FOOT file), L-slab, coverage contract, grammar
  torture, zero-usable error, mapped item, 16-gon column, polycurve + arc
  skip, ifczip, storeys, determinism; construct test drives
  `viewer.load_ifc` end to end and asserts the coverage dialog.
- SKIP list held (booleans/openings — walls import without door holes;
  BReps/tessellations; curved geometry; materials/styles; psets beyond
  the free Name; georeferencing; non-uniform transforms; writing IFC —
  ever).  58 suites green twice.  NEXT: Phase J — the pypdf retirement
  (bo_F.md), v5.0.0.

## Round 34 (SHIPPED, v5.0.0): BUILDOUT Phase J — the Shuttle (pypdf retired)

The reader + page-surgery half of PDF, from scratch — pypdf leaves the
runtime the way reportlab and Tesseract did (staged, oracle-tested,
demoted to a dev-box parity oracle behind `PLOOM_PDF_IO=pypdf`).
**Runtime deps are now pymupdf + numpy + stdlib.**  The BUILDOUT campaign
(A–J) is COMPLETE.

- **minipdf/parse.py — the lenient reader**: string/comment-aware record
  scanner; recursive-descent parser for the 8 object types (two-token
  lookahead for `12 0 R`, `#xx` names, octal/EOL string escapes, odd-hex
  padding, lenient numbers); classic xref parsed TOKENWISE (19/21-byte
  rows survive; a 1-byte offset shift is absorbed silently), xref streams
  (W/Index, PNG predictors incl. Paeth), object streams (cached whole on
  first touch — never O(n²) inflate), /Prev chains newest-first with
  first-seen-wins + hybrid /XRefStm before /Prev; /Length verified then
  endstream-scanned; junk-prefix offset retry; full-file scan-rebuild
  recovery (LAST definition wins) with a `repaired` flag; page tree by
  /Kids walk (never /Type or /Count) with inheritance, box normalization,
  crop∩media.  `strict=True` disables ALL recovery — the self-check mode.
- **minipdf/graph.py — importer + writer**: deep page import with the two
  load-bearing cuts (/Parent on page-like dicts; refs to /Kids nodes →
  null; page refs → pagemap or null — a nulled /Dest is a dead link every
  viewer tolerates), memo-before-recurse (cycle-safe), stream RAW bytes
  never re-encoded (untouched pages pixel-identical by construction);
  serializer with sorted keys, hex strings, fmt_num, classic xref,
  content-hash /ID, structurally NO /Info; outline chain writer; every
  `write()` strict-self-re-parses before a byte lands.
- **minipdf/pagemerge.py — the compositor**: /Contents becomes
  [q] + originals + [Q q CTM cm overlay Q]; the four closed-form CTMs
  (NOT pypdf's Transformation algebra — its translate is a device-space
  post-add; the 180°-flip bug lives down that road); overlay fonts
  imported under fresh /PLFn keys with the rename applied to OUR ops
  only; page /Resources copied-on-write.  Cross-backend: pixel-identical
  at 150 dpi, texttrace-identical glyph origins (a few AA pixels differ
  at 90/240 dpi — renderer cache trivia, documented in CLAUDE.md).
- **Cutovers**: merge.py / stamp.py / reports.py switch on PLOOM_PDF_IO
  (default `mini`); pdfdoctor.is_encrypted cross-checks the trailer via
  the Shuttle (the trailer never lies; fitz hides owner-locks);
  encryption stays out of the codebase — `decrypt("")` re-saves through
  fitz (blank/owner passwords open transparently; a user password raises
  today's exact ValueError).
- **tests/test_pdfio.py** (161 checks): corpus parity across classic/
  objstm/incremental containers (fitz always, pypdf when importable);
  8-fixture byte-surgery quirk battery with exact `repaired` flags;
  parser + Flate/predictor unit vectors; the four CTMs; writer
  determinism + strict self-check + no-/Info; merge backend A/B pixel
  parity; encryption behavior; retirement proof (no module-level pypdf
  import anywhere in the runtime + requirements clean).  12 test files
  swept off pypdf (fixture building via the mini writer; outline asserts
  via fitz.get_toc).  Whole suite proven green with pypdf import-blocked.
- SKIP list held: no in-house crypto ever; no decoders beyond Flate+PNG
  predictors (page content is never decoded); no content-stream parsing
  of plan pages; no linearization; no incremental/xref-stream writing;
  flat /Kids; PDF 2.0 beyond BOM handling refused.  59 suites green
  twice.  The feature campaign is complete — next work is owner-directed.

## Round 35 (SHIPPED, v5.1.0): the Swatchbook — cut-sheet submittal builder

Owner-supplied feature kit (SPEC + manifest + golden acceptance set +
reference recipes): the plumbing cut-sheet submittal builder.  One stamped
PDF per fixture tag — clean manufacturer sheets merged in spec-paragraph
order, the tag stamped top-right on EVERY page, named per the office 0-49
numbering standard, gaps documented in a build log.  The standards are an
APPROVED deliverable format verified against an accepted reference
package — never improvise on them.

- **swatchbook.py**: the exact approved stamp (10 pt corner margins,
  16 pt tall, `max(text_width+12, 40)` wide, 0.9 pt outline in
  RGB(0.80, 0.05, 0.05), white fill — never solid red — Helvetica-Bold
  10.5 centered; kin to but DISTINCT from the RFI note-box law);
  `build_packet` re-renders each source page onto a fresh unrotated page
  (`show_pdf_page` — rotated manufacturer sheets stamp in the VISUAL
  top-right with zero rotation math; mixed page sizes preserved, never
  normalized), then re-serializes through the Shuttle (drops the
  renderer's /Producer + random /ID → metadata-clean deterministic
  bytes); never-restamp guard (tag-shaped text in page 1's top-right
  150×40 pt → refuse: double-stamping is a rejection); the manifest-
  indexed `Library` (sha256 verified on load, mismatches refused loudly;
  alias resolution case/punct-blind with unique-series-prefix tolerance —
  ambiguity is a GAP, never a silent substitute); manual `import_pdf`
  (the rep-request path, clean-check + manifest append); `build_all`
  (gaps never block a packet, insertion positions recorded, engineer
  flags carried, `00-BUILD-LOG.md` in the approved format, gap fillers
  insert at their recorded positions); first-run kit copy into
  ~/.planloom/cutsheet_library so imports can append.
- **Deliberate scope decisions**: the spec's optional online fetch
  module is NOT built as code — offline invariant #1 outranks a
  default-off module; `source_url` fields stay as provenance DATA and
  wanted products surface as "request from rep / import manually".
  Reference-project identifying names were neutralized when staging the
  kit (invariant #7); building-product manufacturer names live in DATA
  files only (the feature's subject matter), never in code identifiers.
  pymupdf pinned to the 1.28 line per the kit's verified behavior.
- **Data staged**: `rfi_stamper/data/cutsheet_library/` (manifest with 43
  components + 4 wanted, neutralized reference recipes: 19 packets, 3 gap
  fillers) — bundled into both frozen exes via the .spec datas list;
  `tests/golden_cutsheets/` (the 19 approved packets + frozen page counts
  + neutralized build log).  The 43-sheet `seed_library/` of clean
  manufacturer PDFs is INSTALLED (v5.1.1, owner-supplied kit zip): the
  full acceptance runs live — T1 rebuilds all 19 reference packets with
  exact filename + page-count matches against golden, T4 sha256-sweeps
  every sheet, T3 re-verifies the rotated-page stamp on the rebuild, T7
  pins gap-filler positions page-by-page.
- **GUI**: the Swatchbook panel in Project Management (beside
  Submittals): fixture form (tag + 0-49 category + component callouts
  with live library resolution, loud red GAP labels, reorder = merge
  order), reference-project loader, clean-sheet-checked import, Build
  All → packets + build log via run_bg.
- **tests/test_swatchbook.py** (209 checks): T5 exact stamp geometry incl.
  the tag TEXT color, T6 never-restamp (incl. a golden packet as input
  AND a foreign media-coords stamp on a /Rotate page — text extraction
  reports unrotated coordinates, so the guard checks the derotated visual
  corner too), T2 every page of every golden packet, T3 visual top-right
  on rotated pages (synthetic + golden), library
  resolve/sha/import/install/sync, gap handling + log format + filler
  positions + byte determinism + metadata-clean output; T1/T4/T7 run
  against the real kit when the seed library is installed (the
  reportlab-oracle gate pattern) and then also assert filler POSITION
  (filled build == unfilled build with one page inserted at the recorded
  index) and per-page stamps.  Construct test drives the panel end to
  end on a synthetic library.
- **Adversarially reviewed before shipping** (three-lens agent fan-out,
  every finding refute-verified; 20 confirmed, all fixed): discontinued-
  model callouts now resolve as LOUD substitutions (`resolve_ex` note →
  amber form label + engineer flag; substitution aliases excluded from
  prefix matching — never a silent substitute), `ensure_user_library`
  SYNCS instead of first-run-only (the seed kit landing after a user dir
  exists installs instead of being stranded; manual imports untouched),
  gap fillers handle dict-shaped components and clear the gap note they
  fill (announced as a flag; bad insert_after appends LOUDLY), one
  booklet twice with two ranges keeps both (per-occurrence spans), the
  fixture form keeps original callouts and re-resolves at build (an
  imported sheet fills its gap with no re-typing) plus a "@ 4-6"
  page-range syntax, `import_pdf` refuses over an unreadable manifest
  (no clobber), JSON nulls in manifest entries can't poison resolution,
  multi-brand manufacturer strings resolve per word, the build no longer
  touches tk from the run_bg worker, the rows list is themed, and the
  staged manifest dropped its dead fetch-module browser-UA keys.  Known
  accepted cost: the first library touch lazily copies/hashes the kit on
  the tk thread once (~0.2 s).

## Round 36 (SHIPPED, v5.2.0): the Cut Ticket — the model writes the pull list

Owner request: drop a fixture tag / model piece into the drawing and a
manifest builds in the background toward the cut sheets, updating on
save.  Designed off an 8-agent research pass (Loft model, save plumbing,
Pipewright overlap, Swatchbook contract, project store, industry norms,
diff semantics, test plan); the owner confirmed all four recommendations
(save-trigger + live tally · project-store persistence · auto-feed as
proposals · the name).

- **Tagging**: fixtures carry an optional ``tag`` prop ("WC-1") — a Tag
  field on the fixture tool bar and in Traits; the tag renders under the
  symbol on canvas/plate/DXF/PNG via one render_ops op.  EXPLICIT TAGS
  ONLY: tag-shaped text is never scraped (the pattern collides exactly
  with sheet refs like 2/P-1 and callout bubbles like A-501 — decoys are
  pinned in the tests).  Untagged fixtures are counted and surfaced
  ("N untagged"), never given invented tags.  The Tally gained a live
  "Tagged" counter.
- **cutticket.py**: pure ``census(model)`` (keyed by canonical tag,
  ordered (prefix, tag), deterministic, mutation-free — never dirties the
  model or pushes undo); stencil→0-49 prefix table that NEVER guesses
  (cleanout/structure stencils surface "needs category" — a wrong prefix
  on a submittal is a rejection); same tag on two stencils = loud
  conflict.  ``sync_project`` reconciles into the project store's new
  ``pull_list`` records (PullItem) with strict field ownership: machine
  facts (count, per-drawing sources, stencil, flags) refresh; HUMAN
  fields (callouts, prefix/category override, notes, status) are never
  touched; a tag that leaves the model is TOMBSTONED (missing_from_model,
  kept until a human deletes it; re-placing revives); several drawings
  merge per-source; write-if-changed (no store churn on a no-op save).
- **Trigger**: LoftTab.save() syncs after a successful model save (a sync
  problem never blocks the save — the status line reports it); no timed
  autosave was added (owner call — explicit save is the trigger).
- **Auto-feed**: ``to_packets`` turns pull rows into Swatchbook proposal
  packets (callouts ride along and re-resolve at build; no callouts =
  loud gap; tombstones carry a MISSING FROM MODEL flag; needs-category
  rows surface separately, never force-prefixed).  The Swatchbook panel
  refreshes from the store on project load and on tab entry; hand-entered
  fixtures win tag collisions; entering callouts on a model-sourced tag
  writes them BACK to the store row (human-owned) so they survive
  restarts and re-censuses.  PDFs still build ONLY via Build All.
- **Tests**: tests/test_cutticket.py (37 checks — census/decoys/purity/
  category-honesty/conflicts/reconcile/tombstone-revive/two-drawing
  merge/write-if-changed/packets/render+roundtrip) + a construct block
  driving tag → place → save → store → auto-fed proposal → tombstone,
  asserting no PDF ever builds without the explicit action.
- Gotcha bought here: ``DraftModel.remove()`` takes a LIST of ids — a
  bare id string iterates its characters and silently removes nothing.

## Round 37 (SHIPPED, v5.3.0): the Chalk Mark — checkbox marking on cut sheets

SETSCAN Phase 4 (the owner's direct "can it check the box next to the
model number?" — yes).  During a Swatchbook build the component's model
designations (id + alias base forms) are searched on its OWN pages and
the empty checkbox in that row gets a red X.  Marks a LEGAL SUBMITTAL,
so the certainty contract is the strictest in the module; built with the
standing defaults (red X · report-only until the owner flips it on ·
vector-first).

- **Engine** (swatchbook.py): ``_visual_boxes`` (3.5–15 pt near-square
  vector candidates merged transitively — one drawn checkbox is routinely
  several overlapping paths), ``_model_spans`` (word-exact NORMALIZED
  matching, joins up to 3 adjacent words so "CX 300" finds CX-300;
  substring matching would let Z100 swallow Z1000), ``_box_is_empty``
  (28 %-inset interior render), ``_chalk_component`` (the gates).
  Gates: boxless occurrences (titles, running text) are ignored; then
  exactly ONE checkbox row, holding exactly ONE box, which must be
  pixel-empty — anything else skips into the build log with the count
  ("2 boxes in the model's row — marked none; check by hand").  Modes
  off / report / mark; engine default off, GUI default report; entries
  ride ``build_all`` results and a "## Chalk marks" log section.
- **The bake (delivered-fidelity fix beyond chalk)**: many manufacturer
  sheets are fillable forms whose checkboxes exist ONLY as widget
  annotations — ``show_pdf_page`` embeds page content only, so packets
  were shipping with those checkboxes INVISIBLE.  ``build_packet`` now
  bakes annotation + widget appearances into the content first
  (interactivity dropped — correct for a submittal; the visual sheet
  ships complete).  Golden page counts/stamps unchanged.
- **Row membership** is the box's vertical CENTER inside the text band
  (±2 pt): plain rect intersection grazed the next option row's box
  (stacked checkbox columns sit ~10 pt apart) and fabricated 2-box
  refusals on rows that were actually clean.
- **Real-kit truth** (report over the 19 reference packets, 41 entries):
  one clean would-mark (a trap-primer series option row); option grids
  refuse with counts, pre-inked boxes are left alone, header-only model
  text is ignored.  Mark mode: pixel diff confined to that one box,
  every other packet byte-identical.
- **Tests**: tests/test_chalkmark.py (25 checks — synthetic spec sheets:
  mark containment at 150 dpi + determinism, report=zero byte changes,
  refusals for 2-box/2-row/pre-checked/absent/boxless, word-join +
  no-substring-bleed + idempotence, build_all integration + log section,
  seed-gated reference report) + test_swatchbook widget-bake regression
  (353 checks total).

## Round 38 (SHIPPED, v5.4.0): the Story Pole — witnessed autoscale

SETSCAN Phase 1.  Derives each sheet's true scale (pt per real foot)
from its own dimensions and accepts it only when independent witnesses
agree — the owner's "100% absolutely certain" ask, including the named
door-opening cross-check.

- **Engine** (setscale.py): dimension strings (ft-in grammar via
  ``draft.parse_ftin``, word-joined up to 3) pair with their dimension
  line — the nearest segment whose MIDDLE band contains the text's
  projection (ticks/extension lines sit near the ends and self-reject).
  Each pair is one pt/ft hypothesis.  The certainty contract: >=5
  witnesses within ±0.5 % of the median (outliers NAMED with their
  implied ratio — a mistyped dimension is found, not averaged in), PLUS
  an independent second family of evidence: door swings (Kåsa circle fit
  over curve samples + hinge-anchored leaf line; leaf must land on a
  standard size 2'-0"…4'-0" in 2" steps) or an agreeing title-block
  scale note (arch + engineering forms).  A DISAGREEING note refuses
  with the exact ratio — the printed-half-size set is caught, not
  mismeasured.  Self-agreement alone refuses (a reduced print is
  self-consistent too).  Per-sheet verdicts (``set_verdicts``), never
  inherited; ambiguous multi-note sheets surface both labels.
- **GUI**: "Auto scale — the Story Pole…" in the markup tab's scale
  menu → run_bg over the whole set → verdict table (page / verdict /
  scale / evidence) with per-sheet detail (witnesses, named outliers,
  door checks); Apply calibrates ONLY the PASS pages into the existing
  per-sheet ScaleCal memory — REFUSED sheets keep whatever the human
  set.
- **Tests**: tests/test_storypole.py (26 checks — Loft plate at a known
  ladder scale verifies to the exact pt/ft with doors at 36"/32" and an
  agreeing note; poisoned dimension outvoted AND named; half-size print
  refused with "0.500x"; blank/thin/uncorroborated/off-standard-door
  refusals; doors corroborate without a note; a full circle is never a
  door; engineering note form; determinism) + a construct block driving
  the dialog to an applied calibration.

## Round 39 (SHIPPED, v5.5.0): the Reed Count — fixture symbols counted

SETSCAN Phase 2.  Counts plumbing fixtures on vector sheets by matching
linework clusters against a symbol library seeded from the Loft's own
stencils (convention-only descriptions, invariant #7).

- **Engine** (reedcount.py): primitives from get_drawings (lines, curve
  samples, rects, quads) → strip long linework (> 6.5 ft real) and door
  swings (the Story Pole's arc detector, reused) → proximity clusters
  (3" real gap) → 220-point resample → 24-rotation × flip pose search
  scored by dilated-grid soft-F1 against stencil signatures.  Principal-
  axis canonicalization was tried and REJECTED: a square's axis is
  arbitrary (see gotcha).  Requires a verified pt/ft (Story Pole or
  human cal) and refuses without one.
- **Honesty gates**: size sanity vs the stencil's real footprint (±35 %)
  is a hard gate — north arrows match the water-heater circle at 0.85
  and die on size; near-identical conventions surface as AMBIGUOUS with
  both names (mop vs single-bowl sink), never silently picked;
  text-labeled stencils (WH, CO) count only with their label word in the
  cluster bbox; every exclusion is counted (long linework, door swings,
  size rejections); unmatched fixture-sized clusters land in the unknown
  tray with their nearest miss named.
- **Human-gated learning**: `make_symbol` turns a labeled tray cluster
  into a custom library entry (real-inch points about the centroid),
  persisted in ~/.planloom `reed_symbols` — the review-deck precedent.
- **GUI**: "Count fixtures — the Reed Count…" beside the Story Pole in
  the markup tab's scale menu — refuses without a verified ft scale;
  results dialog: counts table, filter tallies, unknown tray with
  reasons, label-an-unknown flow, recount.
- **Tests**: tests/test_reedcount.py (17 checks — exact counts through
  the real plate pipeline incl. rotated/flipped/45° placements; decoy
  never counts; size gate carries reasons; ambiguity named; label gate;
  custom-symbol learning; no-scale refusal; determinism) + a construct
  block driving Story Pole → Reed Count to a counts tree.

## Roadmap (still open)

- **SETSCAN_PLAN.md (owner-requested, staged; Phases 4+1+2 SHIPPED
  v5.3.0/v5.4.0/v5.5.0)** — remaining: the Cut Ticket set-scan (fixture
  tags + the legend-sheet schedule table harvested into preliminary
  pull-list rows with pre-filled callouts — proposals only).
- **Owner-confirmed next campaign (2026-07-10)** — four recommendations
  locked by the owner: (1) the training mode — hands-on click-to-advance
  steps with a per-step "Show me" animated fallback; (2) Training Center
  + first-run prompt, per-section courses with progress tracking; (3)
  full-app memory palette (one consistent low-saturation anchor hue per
  section, calm neutrals, accent only where action is needed); (4) the
  Fieldstitch staged workflow board (Job → Set Up → Points → Stake/QA →
  Export as big touch-friendly stage tiles) + station-setup geometry
  advisor (good/bad triangle check, re-check-backsight reminders) + XLSX
  coordinates in the robotic-total-station export kit.  Inspiration
  seed: an uploaded RTS training deck — structure and pedagogy only
  (lesson roadmaps, "You Try It!" checkpoints, correct-vs-incorrect
  reviews), never its content or vendor names.  Also owner-requested:
  a dead-simple first-run connect wizard (folders, formats, kits).
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
