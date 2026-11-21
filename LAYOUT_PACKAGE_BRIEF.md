# Build brief: one-click layout package for Planloom

You are working in the Planloom repository. Read this whole brief first. Then read `CLAUDE.md`, the naming registry in `HANDOFF.md`, and the modules listed in section 3 before you write any code.

Every invariant in `CLAUDE.md` stays in force: offline always, no new dependencies beyond PyMuPDF and numpy, never cover linework, verification must pass before delivery, and no company, project, person or vendor names anywhere in code, tests, fixtures, docs or commit messages. Where this brief and `CLAUDE.md` disagree, `CLAUDE.md` wins and you stop and ask the owner.

## 1. What the owner wants

A layout person drags a plan set into Planloom, picks a hat, flips a few switches, presses one button, and gets field ready sheets, point files, an open items list and a plain report of what was checked. It runs fully offline with no cloud service and no questions asked mid-run unless a gate truly cannot decide.

Two hats, shown as two large buttons at the top of the tab. Both can be on at once.

- **Concrete hat.** Wall, column, slab edge, slab opening and shear wall end layout for a concrete subcontractor, checked against every other drawing in the drop.
- **Surveyor hat.** Control point request sheets, setup plans and check forms.

The program must never print something it has not checked. It reports what lines up as well as what does not.

## 2. Why this brief is strict

This workflow was run by hand on a real job. The sheets went out with errors that a first review pass did not catch, because that pass checked the wording and the point files but never compared the drawn plans with the source sheets. Every one of those errors is a required regression case in section 9. The rules in sections 4 to 7 exist so those errors cannot ship, not merely so they are less likely.

## 3. Build on what exists. Do not duplicate it.

- `sheets.py`: plan-set index (page to sheet number, geometry).
- `setscale.py` (the Story Pole): witnessed scale.
- `fieldstitch.py`: points, layers, numbering, statuses, witness points, world frame, PNEZD CSV, DXF R12, the export kits.
- `fieldpro.py`: tolerance classes with their basis and disclaimer, Stitch Codes, as-staked ledger, `station_geometry` setup advisor.
- `harvest.py`: gridiron, wall corners and the other pure generators. New point generators go here and stay pure (proposals only).
- `selvage.py`: wire formats and CAD layer-name rules.
- `backcheck.py`: the deterministic rule framework. New comparison rules follow its pattern.
- `drawdiff.py` (the Slipsheet) and `align.py`: vector revision diff and raster alignment.
- `tracer/`: OCR for text that is not in the PDF text layer.
- `layout.py`: the measured-empty rectangle finder. Every piece of text this feature places on a plan goes through it.
- `minipdf/`: all PDF output. Use the existing Plates title block.
- `verify.py` and `pipeline.py`: the pattern for "verification must pass before anything is delivered".
- `gui/`: tab pattern, theme, nav sections, the drag-and-drop backend. `tests/run_all.py`: every new test is registered there.

Geometry, polygon clipping, an ASCII DXF reader, connected components and morphology are written in house with numpy. No new packages.

## 4. The visibility gate (the vision library)

Proposed name: **The Sightline**. Register it in `HANDOFF.md` under the registry rules before use.

**The problem.** A PDF page holds vector shapes that are not visible on the plotted sheet: fills limited by a clip path, lines hidden under a later fill or mask, and superseded revision geometry left in the file. A reader that trusts raw vectors will draw and measure things that are not on the sheet. No vector reader in this repository checks for this today.

**Requirements.**

1. A GUI-free module with one job. Given a page and extracted vector elements, return for each element its visible part and a visibility score, measured against a render of the same page (150 dpi minimum, 300 dpi for small elements).
2. **Fills.** Apply clip paths from the extended drawing data. Then confirm against the render. If the visible area is under 95 percent of the vector area, replace the vector shape with the traced visible polygon and record both areas. An angled element is always a polygon, never its bounding box.
3. **Strokes.** Sample along every segment. A segment counts as visible only when a pixel of its own stroke class lies within 2 px at 80 percent or more of the samples. Under 50 percent it is hidden: drop it and record it. In between, split it where visibility changes.
4. **Reverse check.** Pixels of a tracked fill class that no accepted element covers are reported as regions ("seen but not read").
5. **Annotations over geometry.** A revision cloud, tag or leader drawn across an element is not a difference. Classify it as obscured and list it separately.
6. Every vector reader takes geometry through the gate: the new plan reader, and behind a default-on option the Slipsheet, the Story Pole, the Backcheck, `extrude.py` and `reedcount.py`. Their existing tests must still pass.
7. The gate saves per page what it dropped, what it clipped and what it could not read. The run report prints those lists.

## 5. Reading the drawings

1. **Intake.** Classify every dropped file and page: structural framing or slab plan per level, architectural plan per level, general notes, wall schedule, shop drawings, RFI responses, CAD background (ASCII DXF only; for any other CAD format show a plain message asking for DXF). Pages that cannot be classified are listed as unknown. Never guess.
2. **Scale and grid.** Scale from the Story Pole. Find gridlines and bubbles, build the grid with names and offsets, and check the spacing against the printed grid dimensions. When a CAD background exists, check the grid against it. A mismatch over 1/4 in. blocks that level.
3. **Concrete.** Walls, rectangular columns, round columns and other solid concrete, from the wall fill class through the gate. Do not hardcode a fill color. Learn it from the sheet and ask once, with picture crops, if two classes are plausible. Read openings in walls, wall marks, thickness as drawn and schedule thickness.
4. **Slab.** Slab edge from heavy linework through the gate. Slab openings, including ones whose cross is dashed. Curves with radius and center.
5. **Shear wall ends.** Read the schedule: marks, minimum length, end bars, ties and the zone length detail. On site these are called boundary zones or jamb bars. Where the schedule and the detail disagree, raise an open item. Do not pick one.
6. **Openings by others.** Penetrations noted on the plan, and mechanical openings from shop drawing overlays. Overlay locations are approximate and are labeled approximate everywhere.
7. **General notes.** Capture notes that govern layout (tolerance standard, reinforcing at openings, required surveys) with the exact quote, sheet, note number and heading. Find the heading by position: the nearest heading above the note in the same column.
8. **Architect dimension strings.** Read each string with its two ends. An end matches a structural line within 1 1/2 in. Classify each string as same (within 1/2 in.), different, or landing on one line only.
9. **Provenance.** Every element keeps its source file, page, sheet, revision text from the title block, extraction method (vector, traced, OCR) and confidence. An element taken from a different source than its neighbors, for example a wall added by an answered RFI and drawn from the CAD background, carries that on every output.

Not built in this round unless the owner asks: elevations, slab steps and depressions, curbs and pads, embeds and hold-downs. The report must say these were not laid out. It must not stay silent about them.

## 6. Cross-checks and the open items register

**Cross-checks.**

- Structure against CAD background: a face comparison along every wall, and a corner to corner test for every wall and column corner and every round column center. Match is 1/4 in. Flag is over 1/2 in. Keep results per element.
- Structure against architect strings: for every shear wall end and jamb, record whether the architect has a dimension string on it, ties the opening centerline only, or has nothing.
- Level against level: walls that do not sit on a wall below.
- Structure against schedule: thickness, and length against the minimum.
- Structure against overlays: openings through walls and slabs that the structural plan does not mark, and any opening inside a tied end zone.
- Revision against revision when two issues are in the drop, using the Slipsheet. Items inside a clouded area record the cloud number.

An element with only one source is labeled single source on plates, in point descriptions and in the report.

**Open items register** (proposed name: **Red Tags**).

- One register per project, stored in the project file.
- Item numbers are stable per level. They are never reused and never renumbered. New items append.
- Each item has: category (answer needed before layout, confirm before forming, answered), short label, what is wrong, what is needed, location boxes, proof record ids, status, and for answered items the RFI number and quote.
- Wording comes from the evidence, with singular and plural forms.
- A hold policy maps categories to point statuses.
- When a shear wall end, jamb or boundary zone has no dimension to the concrete, the program writes a short neutral RFI draft. It never sends anything.

## 7. Proof records: nothing prints without evidence

Proposed name: **The Paper Trail**.

1. No sentence, number, count or reference is printed on a plate, legend, point note or report unless it comes from a proof record: check id, scope (what was compared with what, and which elements), result, counts, source quotes with sheet, heading and note number.
2. Statement templates are bound to check ids. The words "all", "none", "every" and "no" are available only when the scope covers every element of that class and the count supports it. Otherwise the template prints the counts, for example "12 of 34".
3. A coverage statement lists what was not compared.
4. Every reference printed anywhere (sheet, item, detail, RFI) must resolve to something that exists in this run. One unresolved reference blocks delivery.
5. Legend examples are pulled from the produced data. Nothing on a legend is typed by hand.
6. A status is printed only from a field the user set. The program never states that an RFI is being written or sent. The default wording is "RFI needed".
7. Tolerances come from the job's tolerance classes in Fieldpro with their basis field. With no basis entered, the plate prints the existing "verify against project spec" disclaimer. Never hardcode a standard, a section number or a value from memory.
8. Text lint on every output: no names except project settings fields, American spelling, no how-to wording ("step 1", "click", "how to use"), and no long dashes in plates or reports from this feature. This does not change the approved note style in invariant 2.
9. File metadata on every output comes from project settings only. No tool or library names.

## 8. Outputs

**Plates** (PDF through `minipdf`, existing Plates title block, stored page scale for viewer measuring tools). Per level: wall plan, open items, masonry when the CAD background has it, slab edges, shear wall ends, architect dimensions to check. Plus one legend plate and an index.

- Every list row is also printed beside its element on the plan, in a small note block with a thin leader, placed with the measured-empty finder. If there is no free spot within reach, the row stays in the list only and the run report counts it. Text never covers linework, dimensions or other notes.
- Phone booklet option: one item per letter-size page, a crop of the plan with its text.
- Look: a plotted drawing. Monochrome plan, one accent color for open items, tables with a header band of 4472C4 with white bold text and banded rows of D9E1F2, short notes, no tutorial text. Stamped "FOR REVIEW" until the owner flips Issue.
- One-sheet proof for owner sign-off before the full set is built.

**Points.**

- New Harvest generators: wall and column corners at face of concrete (polygons, not just segments), round column centers, shear wall end zone ends, slab edge corners, radius points, slab opening corners. Gridiron already covers grid intersections.
- Proposals go into a Fieldstitch job, with layers for grid, before the pour, after the pour, needs a dimension, and hold.
- Names are stable and unique. Each description carries stage, Stitch Code, element id and one note: hold with item number, check with item number, no matching corner in the second source, dimensioned by the architect, no dimension to the concrete, or per answered RFI with its number.
- Frame: the CAD background's own origin and declared units when one exists, else a grid based local frame. Real-world coordinates only when the user supplies control with coordinates. Every plate and point file states which frame it uses.
- Export through the existing kits, plus one DXF background per level using the Selvage layer-name rules. Every point in the CSV has a matching point in the DXF. That is tested.
- An instrument range is never printed as a fact. The user setting is called "working range".

**Surveyor hat.**

- Control request plate per level: requested control locations on structure that will still be there later, each with id, ties to two gridlines and coordinates in the declared frame. Wording stays neutral: "requested control", never a claim about how any crew works.
- Setup plan plate, built on `fieldpro.station_geometry`. Every layout point must be inside the working range, no farther out than the farthest control that setup uses, and seen within the existing triangle rule. Lines of sight are blocked by walls and columns standing at the chosen construction stage. Points with no valid setup are flagged, never hidden.
- Check forms: the as-staked ledger that exists, and a slab elevation survey form only when a general note calls for one, with the quote from its proof record.

## 9. Regression cases. All are required tests.

Build every fixture synthetically with `minipdf` or PyMuPDF. No real project documents go in the repository.

1. **Clipped fill.** A gray rectangle clipped to a diagonal band. The reader must return the band. The earlier run drew a solid 7 ft by 12 ft block where the sheet shows a thin angled wall.
2. **Hidden superseded linework.** Heavy lines in the content stream covered by a later white fill, with a revision cloud beside them. They must be dropped, the slab outline must not include them, and an open item must be raised there.
3. **Edge with no plotted line.** A slab edge run whose heavy line is gone and only a light line remains. It is an open item, not a confident edge.
4. **Overstated statement.** 12 of 34 ends have an architect string. The printed sentence must carry those counts. A "no dimension given" sentence must be impossible.
5. **Coverage wider than the check.** A "matches within 1/2 in." line may print only for the element classes the corner test actually covered, and must name what was not compared.
6. **Wrong note heading.** Two headings in one column. The note must be cited under the heading directly above it.
7. **Invented legend example.** Every example string on the legend must exist verbatim in the produced point file.
8. **Dead reference.** A plate that refers to a sheet or item that does not exist in the run must block delivery.
9. **Mixed provenance.** One wall from the CAD background among walls from the structural plan. The notes must say so.
10. **Renumbering.** Add an item after a first run. Earlier item numbers must not change.
11. **Round column off by 1 in.** against the second source must be flagged. The earlier run compared only rectangular columns.
12. **Annotation over geometry.** A revision cloud across a column must not be reported as missing concrete.
13. **Text and metadata lint.** Outputs with a tool name in metadata, a long dash, British spelling or how-to wording must fail.
14. **Round trip.** CSV, DXF and job hold the same points with the same names and coordinates.
15. **Rotated page.** Cases 1 and 2 again on a page with /Rotate 90.

## 10. The tab, the switches and the command line

- New tab in Field Management, beside Fieldstitch. Proposed name for the run: **The Batterboard**. Proposed name for the role presets: **Hats**.
- Top to bottom: drop zone, a table of what was detected (file, what it is, level, revision), the two hat buttons, the switches, one Run button, a stage list with pass, flag and block lights, and an Open Output Folder button.
- Switches: levels, stage (before the pour, after the pour, both), outputs (plates, phone booklet, point kits, legend, RFI drafts, run report), include masonry, include overlays, hold policy, working range. Presets are saved per project.
- A switch for something that is not built is disabled and labeled "not built yet". No placeholder may look finished.
- Command line twin with a JSON options file. Exit code is nonzero when the run is blocked.

## 11. Gates and the run report

Stages: Intake, Sightline, Read, Cross-check, Register, Points, Plates, Self-check, Deliver.

Self-check is automatic and fully recorded:

1. **Picture check.** Each produced plan plate is compared with the render of its source sheet: concrete drawn against fill on the sheet, slab edge and openings against strokes on the sheet. This is the check that was missing.
2. Round trip of point files. Names unique. Frame declared.
3. Reference resolution, proof coverage for every printed statement, text lint, metadata.
4. Counts on the legend and summaries equal the data.
5. Key numbers (grid offsets, a sample of corner coordinates, zone lengths) are recomputed by a second code path and compared.

Result is PASS, PASS WITH FLAGS (open items exist), or BLOCKED (a self-check failed). A blocked run writes nothing to the deliverables folder.

The run report, as PDF and text, says in plain words: what was checked against what, with counts; what was found; what lines up; what was not checked and why; which elements are single source; and what hidden geometry was dropped. It never says "verified" without naming the second source.

## 12. Order of work

0. Read. Write `BATTERBOARD_PLAN.md` in the style of the other plan files. Propose names. Get owner sign-off.
1. The visibility gate, the retrofit of existing readers, and cases 1, 2, 3, 12 and 15.
2. The plan reader on synthetic fixtures, then on one real set the owner supplies locally. That set is never committed.
3. Cross-checks, the register and proof records. Cases 4 to 11.
4. Points and kits. Case 14.
5. Plates, on-plan notes and the legend. One-sheet proof for sign-off.
6. Surveyor hat.
7. Tab, switches, presets and the command line.
8. Self-check gate and run report. Case 13. Full regression. Windows build and smoke run.

End every phase with a short status in three parts: what was built, how it was tested, and what was not verified.

## 13. Delivery

The owner cannot compile. Leave him something a double-click runs.

- Keep `build_windows.bat` working.
- Add `run_planloom.bat`. On first run it sets up a local environment from `requirements.txt`, then launches the GUI. After that it just launches.
- If you are on Windows, run the build and the smoke test yourself and report the path of the executable and the result.
- Ask the owner before adding an automated Windows build that attaches the executables to a release.

## 14. Rules for you

- If the documents do not settle something, flag it. Never fill it in.
- Never weaken a gate or a threshold to make a test pass. Stop and ask.
- Do not claim anything works unless a test or a run showed it.
- Ask before changing any existing behavior, file format or style constant.
- No names anywhere, as invariant 7 requires. That includes this job's history: write the regression cases as generic geometry.

## 15. Done means

- All 15 regression cases pass inside `python tests/run_all.py`, along with every existing test.
- A run on the owner's real set ends in PASS or PASS WITH FLAGS, and the picture check reports no difference that is not on the open items list.
- Every statement on every plate traces to a proof record.
- The Windows executables build, start offline and complete a run.
