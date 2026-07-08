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

## Round 5 (IN FLIGHT): The Loft — original drafting mode

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
