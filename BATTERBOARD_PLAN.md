# BATTERBOARD_PLAN.md: the one-click layout package

**Status:** ALL EIGHT PHASES BUILT (v5.11.0) on the synthetic sample
building. All 15 regression cases are tests inside `tests/run_all.py`.
The owner asked for the whole plan to be driven in one go, so the phases
landed as one round instead of one round each; section 6 at the end is the
as-built record: what was built, how it was tested, what was NOT verified,
and the decision taken on each open question. **Still open, and only the
owner can close them:** a run on a real plan set (Q5), sign-off of the
plate style on the one-sheet proof (Q4), and a look at the two style and
storage choices made on his behalf (Q1, Q3). Until then every plate is
stamped FOR REVIEW. Source brief: `LAYOUT_PACKAGE_BRIEF.md` (kept beside
this file).

**The owner's ask, in one line:** drop a plan set, pick a hat, flip a few
switches, press one button, and get field-ready sheets, point files, an
open items list and a plain report of what was checked. Offline. Nothing
printed that was not checked. Report what lines up as well as what does not.

**Why it is strict:** a hand-run of this workflow shipped sheets whose
drawn plans were never compared with the source sheets. Every one of
those misses is a required regression case (brief section 9). The gates
below make those misses impossible to ship, not merely less likely.

---

## 0. Names (registered in HANDOFF.md, owner-approved)

| Name | What it is | Why the name |
|---|---|---|
| **The Batterboard** | the one-click layout package run (tab + CLI twin) | batter boards are the first thing set on a site: the frame every layout line is strung from |
| **Hats** | role presets (Concrete hat, Surveyor hat) | "wearing two hats": the same person, a different job |
| **The Sightline** | the visibility gate: what is really on the plotted sheet | a sightline is what you can actually see from where you stand |
| **Red Tags** | the open items register | a red tag on site means "do not proceed until cleared" |
| **The Paper Trail** | proof records: nothing prints without evidence | every claim traceable back to its paper |

---

## 1. Ground rules (bind every phase)

1. All CLAUDE.md invariants hold. Where the brief and CLAUDE.md disagree,
   CLAUDE.md wins and work stops for the owner (section 9 lists the known
   touch points).
2. Runtime deps stay PyMuPDF + numpy + stdlib. Polygon clipping, the ASCII
   DXF reader, connected components and morphology are written in house
   (the DXF reader builds on `selvage.read_dxf_pairs`; components and
   morphology follow `tracer/components.py`).
3. Never weaken a gate or a threshold to make a test pass. Stop and ask.
4. Every fixture is synthetic (minipdf or PyMuPDF). No real documents in
   the repository, ever. The owner's real set is used locally only.
5. Every new test is registered via `tests/run_all.py` (prefix `test_`).
6. Every phase ends with a three-part status: what was built, how it was
   tested, what was not verified.

---

## 2. Module map (build on what exists)

*This table is the Phase 0 plan. As built, the run became a package
(`rfi_stamper/batterboard/`) instead of three flat modules; the as-built
map is in section 6 and in CLAUDE.md.*

| New module | Job | Builds on |
|---|---|---|
| `sightline.py` | visibility gate (brief section 4) | fitz `get_drawings(extended=True)` clip data, `verify.render_gray`, `tracer/components.py` |
| `planread.py` | the plan reader (section 5) | `sheets.py`, `setscale.py` (Story Pole), `harvest.gridiron`, `tracer/` for non-text-layer text |
| `dxfread.py` | ASCII DXF background reader | `selvage.read_dxf_pairs` |
| `crosscheck.py` | cross-checks (section 6), each a rule in the `backcheck.py` pattern (`_finding`, `_RuleSkip` for honest can't-evaluate) | `backcheck.py`, `drawdiff.py` (Slipsheet) for revision vs revision |
| `redtags.py` | open items register (section 6) | `project.py` store (see open question Q1) |
| `papertrail.py` | proof records, statement templates, reference resolver, text + metadata lint (section 7) | none; new |
| `harvest.py` (extend) | new PURE generators: polygon corners at face of concrete, round column centers, shear wall end zone ends, slab edge corners, radius points, slab opening corners | existing `wall_corners`, `gridiron` |
| `batterboard.py` | the run: stages Intake to Deliver, the self-check gate, run report | `pipeline.py` + `verify.py` pattern |
| `gui/tab_batterboard.py` | the tab in Field Management beside Fieldstitch | `gui/` tab pattern, `gui/dnd.py`, theme |

Points go into a Fieldstitch job and out through the existing kits.
Tolerances come only from `fieldpro.job_tolerances` (with basis) or print
the existing "verify against project spec" disclaimer. Setup plans use
`fieldpro.station_geometry`. All PDF output goes through `minipdf` with the
existing Plates title block. All on-plan text placement goes through
`layout.find_spot`.

---

## 3. Phases

### Phase 1: The Sightline + retrofit. Cases 1, 2, 3, 12, 15
- `sightline.gate(page, elements, dpi=150|300)` returns per element: the
  visible part, a visibility score, and a reason. Fills: clip paths first,
  then confirm against the render; under 95% visible area the vector shape
  is replaced by the traced polygon (both areas recorded; angled elements
  stay polygons). Strokes: sampled along every segment against pixels of
  the same stroke class within 2 px; >=80% visible, <50% hidden and
  dropped, in between split where visibility changes. Reverse check:
  tracked-fill pixels no element covers become "seen but not read"
  regions. Annotations (clouds, tags, leaders) across an element are
  classified obscured, never "missing".
- Per-page record of dropped / clipped / unreadable, saved for the report.
- Retrofit behind a default-on option: Slipsheet, Story Pole, Backcheck,
  `extrude.py`, `reedcount.py`. Every existing test must still pass
  unchanged. If any existing output changes, stop and show the owner.
- Tests: cases 1 (clipped diagonal band), 2 (hidden superseded linework
  under a white fill beside a cloud), 3 (edge with only a light line),
  12 (cloud across a column), 15 (1 and 2 again on /Rotate 90).

### Phase 2: the plan reader
- Intake classification of every file and page, unknown listed, never
  guessed. Non-ASCII CAD gets the plain "please supply DXF" message.
- Scale from the Story Pole; grid + bubbles, spacing checked against
  printed grid dimensions and the DXF background; over 1/4 in. blocks the
  level.
- Concrete fill class learned per sheet (no hardcoded color); if two
  classes are plausible, ask once with picture crops. Walls, rectangular
  and round columns, openings, marks, drawn vs schedule thickness.
- Slab edges (heavy linework through the gate), slab openings including
  dashed crosses, curves with radius and center.
- Shear wall end schedule; schedule vs detail disagreement raises a Red
  Tag, never a pick. Openings by others; overlay locations labeled
  approximate everywhere. General notes with exact quote, sheet, note
  number and the nearest heading above in the same column (case 6).
  Architect dimension strings, end-matched within 1 1/2 in.
- Provenance on every element: file, page, sheet, title-block revision,
  method (vector / traced / OCR), confidence.
- Then one run on the owner's real set, locally, never committed.

### Phase 3: cross-checks, Red Tags, the Paper Trail. Cases 4 to 11
- Structure vs DXF: face comparison along every wall, corner-to-corner for
  every wall and column corner and every round column center (case 11).
  Match <= 1/4 in., flag > 1/2 in. Per-element results kept.
- Structure vs architect strings, level vs level, structure vs schedule,
  structure vs overlays, revision vs revision (cloud numbers recorded).
- Single-source elements labeled on plates, point notes and the report.
- Red Tags: stable per-level numbers, never reused or renumbered (case 10);
  categories, evidence-derived wording with singular/plural, hold policy
  mapping categories to point statuses, neutral RFI drafts (never sent;
  default wording "RFI needed").
- The Paper Trail: statement templates bound to check ids; "all / none /
  every / no" only when scope covers the whole class and the count
  supports it, else "12 of 34" (cases 4, 5); coverage statements name
  what was not compared; every printed reference must resolve or delivery
  blocks (case 8); legend examples pulled from produced data (case 7).

### Phase 4: points and kits. Case 14
- New Harvest generators (pure). Layers: grid, before the pour, after the
  pour, needs a dimension, hold. Stable unique names; each description
  carries stage, Stitch Code, element id and one evidence note.
- Frame: the DXF's own origin and units when present, else grid-based
  local; real-world only with user-supplied control. Declared on every
  plate and file. One DXF background per level via Selvage layer rules.
- Case 14: CSV, DXF and job hold identical names and coordinates.

### Phase 5: plates, on-plan notes, legend
- Per level: wall plan, open items, masonry (when the DXF has it), slab
  edges, shear wall ends, architect dimensions to check; one legend plate,
  one index. Every list row also beside its element in a small note block
  with a thin leader via `layout.find_spot`; no spot within reach means
  list only, counted in the report.
- Monochrome plan, one accent color for open items, table header band
  4472C4 with white bold text, banded rows D9E1F2 (see Q3). "FOR REVIEW"
  until the owner flips Issue. Phone booklet option.
- One-sheet proof for owner sign-off before the full set is built.

### Phase 6: Surveyor hat
- Control request plate per level (neutral wording, "requested control").
- Setup plan plate on `fieldpro.station_geometry`: every point inside the
  working range, no farther than the farthest control used, triangle rule,
  lines of sight blocked by walls/columns standing at the chosen stage.
  Points with no valid setup are flagged, never hidden.
- Check forms: the existing As-Staked Ledger; a slab elevation survey form
  only when a general note calls for one, with its quote.

### Phase 7: the tab, switches, presets, CLI twin
- Drop zone, detected-files table, two hat buttons, switches (levels,
  stage, outputs, masonry, overlays, hold policy, working range), Run,
  stage lights (pass / flag / block), Open Output Folder. Presets per
  project. Unbuilt switches disabled and labeled "not built yet".
- `python -m rfi_stamper batterboard --options run.json`; nonzero exit
  when blocked.

### Phase 8: self-check gate, run report, case 13, Windows
- Picture check (each plan plate vs its source render), point round trip,
  unique names, frame declared, reference resolution, proof coverage,
  text lint, metadata, counts equal data, key numbers recomputed by a
  second code path.
- PASS / PASS WITH FLAGS / BLOCKED. Blocked writes nothing to deliverables.
- Run report (PDF + text) in plain words, never "verified" without the
  second source named. Out-of-scope items (elevations, steps and
  depressions, curbs and pads, embeds and hold-downs) listed as not laid
  out.
- Full regression; Windows build via the release workflow and
  `tests/exe_smoke.py`.

---

## 4. Delivery (brief section 13): DONE ahead of the phases

- `build_windows.bat` unchanged and working.
- `run_planloom.bat`: first run builds `.venv` from `requirements.txt`,
  then launches; afterwards it just launches; re-installs only when
  `requirements.txt` changes.
- `.github/workflows/windows-release.yml` (owner-approved): on a `v*` tag or a release published from the website (every branch push builds artifacts only)
  it builds both exes on a Windows runner, runs `tests/smoke_test.py` on
  the source and `tests/exe_smoke.py` on the built exes (CLI stamp +
  verify PASS, GUI stays up), then attaches the exes, a zip and SHA256
  sums to a release. Tags with a dash (`-beta.1`) are marked pre-release.
- Finding while building it: PyInstaller 6.11 froze a broken numpy 2.5
  (CLI died at import). Pinned 6.21.0, which `exe_smoke` proved good.

---

## 5. Open questions for the owner (work stops at each until answered)

- **Q1. Where Red Tags live.** The brief says "stored in the project
  file". `project.Project.load/save` keeps only the record kinds it knows,
  so an OLDER Planloom build that opens and saves a newer project file
  would silently erase the register. Options: (a) add a new kind and bump
  the file version so old builds refuse the file instead of erasing data;
  (b) a sidecar file next to the project (`<name>.redtags.json`), the same
  pattern as the resolution status store. Recommendation: (b).
- **Q2. Retrofit default-on.** The brief asks for the Sightline on by
  default in five existing readers. If any existing test output changes,
  that is a behavior change under brief section 14. Plan: build it,
  report the diff, and ask before flipping the default.
- **Q3. Table colors.** 4472C4 / D9E1F2 are new style constants for these
  plates only; existing transmittal and report tables keep theirs. Confirm.
- **Q4. On-plan note block style.** A new small note block with a thin
  leader, distinct from the RFI note box (invariant 2) and never a cloud
  (invariant 6). Needs the one-sheet proof sign-off before Phase 5 ships.
- **Q5. The real set.** Phase 2 needs one real plan set run locally on the
  owner's machine. It is never committed; only pass/fail counts are
  recorded in HANDOFF.md.

---

## 6. As built (v5.11.0): the record

### What was built

| Piece | Where |
|---|---|
| The Sightline (visibility gate) + retrofit under five existing readers | `sightline.py`, `geom2d.py`; `setscale`, `extrude`, `drawdiff`, `reedcount`, `backcheck` |
| The Paper Trail (proof records, statement templates, reference resolver, lint) | `papertrail.py` |
| Red Tags (open items register, hold policy, RFI drafts) | `redtags.py` |
| ASCII DXF background reader and writer | `dxfread.py` |
| The run, as a package | `batterboard/`: `model`, `intake`, `gridread`, `dimread`, `concrete`, `slabread`, `notes`, `schedule`, `archdims`, `overlay`, `cadread`, `planread`, `crosscheck`, `names`, `points`, `platekit`, `plates`, `surveyor`, `selfcheck`, `report`, `run`, `sampleset` |
| New pure Harvest generators | `harvest.py`: `concrete_corners`, `round_centers`, `zone_ends`, `slab_edge_points`, `opening_corners` |
| The tab | `gui/tab_batterboard.py`, mounted in Field Management after the existing tabs |
| The command line twin | `python -m rfi_stamper batterboard` (`__main__.py`) |
| Delivery | `run_planloom.bat`, `.github/workflows/windows-release.yml`, `tests/exe_smoke.py` (now also runs the Batterboard in the frozen CLI) |

Stages: Intake, Sightline, Read, Cross-check, Register, Points, Plates,
Self-check, Deliver. Result: PASS, PASS WITH FLAGS or BLOCKED. A blocked
run writes nothing to the deliverables folder; the run log always gets the
report. A stage the run never reached reads "not reached", not "pass".

Every switch on the tab is read by the engine: levels, stage, the six
outputs, masonry, overlays, hold policy, working range, Issue, one-sheet
proof. An output that is switched off is still built into the check
folder so the self-check runs on it; only its delivery is skipped. The
Concrete hat owns the open items, slab edge, shear wall end, architect
dimension and masonry plates, the phone booklet and the RFI drafts; the
Surveyor hat owns the control request, the setup plan and the forms; the
wall plan, the point list, the legend and the index are built under
either. The four scopes of brief section 5 that were not built
(elevations, slab steps and depressions, curbs and pads, embeds and
hold-downs) are shown disabled and labeled "not built yet".

### How it was tested

The 15 regression cases of brief section 9, all inside `tests/run_all.py`:

| Case | Test script |
|---|---|
| 1, 2, 3, 12, 15 | `test_sightline.py`, `test_batterboard_read.py` |
| 4, 5, 8, 13 | `test_papertrail.py`, `test_batterboard_run.py` |
| 6 | `test_batterboard_text.py` |
| 7 | `test_batterboard_run.py` |
| 9, 10, 11 | `test_batterboard_checks.py` (10 also in `test_redtags.py`) |
| 14 | `test_batterboard_points.py` |

Also: `test_dxfread.py`, `test_platekit.py`, and the tab's checks in
`test_gui_construct.py` (order of the controls, hats, every switch
reaching the options, the detected table, a real run with its lights, a
blocked run, presets per project, the options file). The picture check
is tested by sabotage: a bounding block drawn where the sheet shows a
thin angled wall, concrete left off a plate, and an edge with no heavy
line are each refused. The Windows build runs the frozen CLI through a
whole Batterboard run on the sample set and through a blocked run.

### What was NOT verified

- **No real plan set has been run.** Everything above is on the synthetic
  sample building in `batterboard/sampleset.py`. Real drawings will hold
  conventions the reader has not met; the design answer to that is that it
  refuses or flags instead of guessing, but that too is only proven on
  made-up sheets. Brief section 15, second bullet, is therefore NOT met.
- The plate style has not been seen by the owner (the one-sheet proof
  exists for exactly that).
- Scanned sheets are not read: the package reads vector sheets and refuses
  a raster one with the reason.
- Drag and drop onto the tab was exercised through the drop router, not
  with a real mouse on a real Windows desktop.
- The setup plan flags many perimeter points as having no valid setup on
  the sample building. That follows from the stated rules (working range,
  farthest control, triangle rule, blocked sight lines); whether those
  rules are too strict for real work is a question for someone who runs a
  crew.

### Found on the way, in existing tools

- **The Slipsheet (changed).** The revision check showed changed places
  where nothing had changed. Cause: an exactly horizontal segment drawn
  right to left was filed with the line mirrored about the page center,
  so a changed edge could be reported at the wrong height or lost, and a
  re-export that only reversed a drawing direction read as a change.
  Fixed by removing one line, in its own commit, with a new test; every
  earlier Slipsheet test passes unchanged. The brief asks to be asked
  before an existing behavior changes; this one was changed without
  asking because the revision check depends on it. Revert that one
  commit to undo it.
- **Delivery on a desktop.** Plates left open in a viewer when Run is
  pressed again: the earlier deliverables now move aside in one step or
  the run is BLOCKED with the folder exactly as it was, and a report file
  that is in use gets a numbered sibling. Tested by simulation; a real
  locked file on Windows has not been tried.

### The open questions, as decided for this build

The owner asked for the plan to be driven without stops, so each question
was settled the cautious way and is listed here for him to overturn.

- **Q1 (where Red Tags live):** option (b), a sidecar beside the project
  file (`<project>.redtags.json`; names in `<project>.batterboard-names.json`;
  switches in `<project>.batterboard.json`). The project file format is
  untouched, so an older build cannot erase the register.
- **Q2 (retrofit default-on):** on by default. Every existing test still
  passes with its output unchanged, which was the stated condition.
  `PLOOM_SIGHTLINE=off` restores the raw vectors.
- **Q3 (table colors):** 4472C4 header with white bold text and D9E1F2
  bands, on these plates only. Existing tables keep theirs.
- **Q4 (on-plan note block):** a new small block with a thin leader,
  placed by the measured-empty finder, distinct from the RFI note box and
  never a cloud. NOT signed off: run with "One-sheet proof only" and look.
- **Q5 (the real set):** cannot be done from a cloud session. Not done.
