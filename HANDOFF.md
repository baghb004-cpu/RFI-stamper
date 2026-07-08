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
