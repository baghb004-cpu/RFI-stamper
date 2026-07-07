# HANDOFF.md — session continuity for Planloom

Read this + CLAUDE.md first in any new session. This file records the naming
registry, the in-flight round, and the roadmap distilled from the product
owner's feature briefs, so work can resume mid-stream without re-asking.

## Current state (start of "Fieldstitch round")

- Product: **Planloom** v3.0.0, offline construction workspace; Python package
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
| **Daybook** (roadmap) | progress journal: measurements, comments, photo refs | the foreman's daybook |
| **Crewpass** (roadmap) | offline seat/device ledger + user reports | a crew's gate pass |

**Vendor-name policy (hard rule, from the owner):** never name third-party
companies or products (survey-tablet vendors, CAD/BIM authoring tools, PDF
competitors) in code, UI, comments, docs, or history. Describe compatibility
by FORMAT, generically: "PNEZD CSV consumed by robotic-total-station field
tablets", "XLSX + DXF consumed by grid layout tablets", "layer conventions
familiar from leading CAD/BIM authoring tools". This extends invariant #7.

## This round (in flight): Fieldstitch + the 60,000% visual pass

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

## Roadmap (owner's briefs, distilled — not yet built)

- **Daybook**: progress journal entries (date, crew, measurements, comments,
  photo file refs) in the project store + PDF report; pairs with Reporting.
  (Owner brief mentioned 360° panoramic views — treat as photo refs first;
  true pano viewing is a stretch goal.)
- **Crewpass**: offline seat ledger (assign users, move a seat between
  devices, usage report PDF) — a local JSON registry, no server.
- **Estimating/takeoff**: quantity takeoff from measure/count markups →
  priced CSV (unit costs from a local price file). Natural extension of the
  markups layer.
- **Scan/point-cloud viewing, machine control, GNSS**: out of scope for an
  offline tkinter app — do not attempt; note in docs if asked.
- **BIM importers**: floor-plan extrusion from a plan PDF into the 3D model;
  richer OBJ/level support; Strata driving BIM system visibility (partially
  done via systems legend).
- **Digital plans from paper**: already served by OCR + markup; surface the
  story in docs/UI copy.

## Session pickup checklist

1. `python3.12 tests/run_all.py` (use `xvfb-run -a` for the GUI test) — must
   be all green before and after your changes.
2. `grep -ri` for banned names (vendor/company/person) before every commit.
3. Engine modules get a plain-python test script in `tests/`; GUI behavior
   goes into `tests/test_gui_construct.py`.
4. Docs to keep current: README.md (user-facing), CLAUDE.md (invariants,
   repo map, gotchas), this file (state + naming + roadmap).
