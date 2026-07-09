# PLANLOOM — offline construction workspace

*A loom weaves threads into a sheet. Planloom weaves your project — RFIs,
answers, tasks, statuses — straight into the plan sheets.*

Planloom is a **100% offline** desktop workspace for construction teams,
built around one core promise: **a designer can pick up a stamped set and
instantly see each RFI's question, its answer, and whether the fix is done —
on the sheets themselves.**

## The core: RFI stamping with resolution tracking

The stamping engine maps every RFI to the sheets it references, writes a
compact red note (question + answer) into measured-empty white space, and
pixel-verifies that nothing on the drawing was covered. On top of that rides
the **resolution lifecycle**:

    OPEN → ANSWERED → IN WORK → FIXED → VERIFIED

* Every stamped note header carries its status (`RFI 044 — RELOCATE CO ·
  ANSWERED`), so the printed set itself says what's outstanding.
* The **Resolution Board** is a drag-and-drop kanban of every scanned RFI —
  drag a card from ANSWERED to IN WORK to FIXED as the work happens; the next
  stamp run weaves the new statuses into the sheets.
* The **Designer Pickup Sheet** is a one-click PDF: per sheet, every
  unverified item with a plain-English next step ("Incorporate answer; mark
  In Work" … "Field-verify, then mark Verified").

## The workspaces

| Section | What lives there |
|---|---|
| **⌂ Home** | Animated blueprint hero, project bar, section cards, recents, smart drop zone |
| **⛑ Field Management** | Task management, scheduling (animated Gantt), punch list, inspections |
| **▤ Project Management** | RFIs (stamping), Resolution Board, submittals, change orders, budget, document management (register + combine + PDF tools), specifications (CSI-parsed spec book) |
| **⬒ Plans & BIM** | Plan viewing & markup (measure, multiply, tool chest…), **The Loft** (draft a real plan from a blank sheet — walls, doors, fixtures, grids, dims), as-built drawings (auto-align compare + red-line flow), 3D BIM viewer with your 2D sheets placed at floor elevations — click a sheet in the model to open it |
| **◫ Reporting** | Project snapshot, RFI log, pickup sheet, submittal log; printable field forms (daily report, safety inspection, QC punch walk, RFI follow-up) — blank or filled |
| **⇌ App Integrations** | File-based bridges: task CSV import/export, punch/budget/CO CSVs, schedule → calendar (.ics), whole-project bundles, drop-folder scan. Offline by design — an "integration" is a local file another tool reads |
| **◎ Ground Truth** | Animated KPIs, gauges and sparklines over the live project data, plus a rules-based insight feed where every insight names the rule that produced it |

A **project** is one local `.ploom.json` file holding tasks, schedule, punch,
inspections, change orders, budget, documents and specs — portable, mergeable
(via bundles), never uploaded.

## The look

Planloom is themed on color theory, not flat gray: a warm drafting-paper
light mode, a deep blueprint-blue dark mode, and a distinct hue per workspace
so you always know where you are. Motion is everywhere but honest about
hardware:

* eased section transitions, an animated nav indicator, gradient headers with
  a drifting sheen, count-up KPIs, sweep-in Gantt bars, draw-in sparklines,
  arc gauges, an ambient animated blueprint backdrop, toasts, and a real-time
  **3D building viewer** — flat-shaded walls with painter's-algorithm depth
  (wireframe mode for older machines), pipe runs as solids with a **slope
  exaggeration slider** so 1/8"-per-foot is visible at building scale, a
  first-person **walk mode** at 5'-6" eye height, isometric presets,
  depth-cued fading, and a 3D measure tape that reads feet-and-inches with
  ΔZ — all pure canvas, no GPU required.
* **Adaptive quality**: a startup probe classifies the machine; new hardware
  gets the full treatment, older machines automatically drop to reduced
  motion, and everything can be forced to full/reduced/off in View →
  Animation quality. Every animation is timer-driven from one scheduler that
  goes fully idle when nothing moves — **zero idle CPU** on any machine.

## Privacy and NDA safety

* **No network code exists in the app**, and an **offline guard** blocks any
  outbound socket at the OS-call level (`● OFFLINE — network blocked` in the
  status bar). Documents, markups, and project data never leave the machine.
* PDF Tools includes **strip metadata** for scrubbing files before they go
  out the door.

## Fieldstitch — layout points for the field crew

Draw it in the office, stake it in the field, **without CAD**. Open a plan
PDF (or a blank grid), drop numbered layout points exactly where they belong
— prefix/suffix/auto-increment numbering, descriptions, categories, per-point
elevations — organized in **Strata** layers (visibility, color override,
lock, filter — conventions any CAD/BIM user already knows). Set a basepoint,
rotation, and scale, and every point gets real-world N/E/Z coordinates. Then
hand it to whichever tablet the crew carries:

* **Bowline Kit** — PNEZD CSV + DXF, the import pair for
  robotic-total-station field tablets
* **Clovehitch Kit** — XLSX (X/Y/Z, point number, prefix/suffix,
  description, category, layer) + DXF, the import pair for grid-layout
  tablets
* **Sheetbend Kit** — LandXML CgPoints + PNEZD CSV, for office survey
  suites and modern controllers
* **Marlinspike Kit** — GSI-8/16 fieldbook + SP-record fieldbook (.rw5),
  for the classic fixed-width and record-based collectors (widths and
  unit digits handled exactly; big state-plane coordinates auto-switch
  the whole file to the wide format)
* **Full Spool** — everything at once, plus the re-loadable job JSON

Every wire file is ASCII/CRLF/no-BOM with the frame hash embedded, every
export declares *which foot* (international vs US survey — exact
conversions through meters only), and a grid-to-ground **CSF** with its
scaling origin rides the job when you set one. Frames can be fit straight
from control pairs (2-point exact or least squares with per-pair
residuals), and an **error-budget preflight** colors any point whose 95%
shot budget exceeds its tolerance class before the crew ever leaves.
**Stake packages** bundle a day's work — route-ordered CSV, QA companion,
DXF (attribute-block tier), job JSON snapshot, and a one-page printable
manifest with the plan thumbnail, control table, and checkbox walk list.

And the loop closes: import the crew's **as-staked shots**, review the
pairing in a human-confirmation table, and Planloom judges every point
against its trade tolerance class (anchor bolts, embeds, sleeves, track —
19 editable presets with their code basis) — TIGHT / SNUG / LOOSE, cut/fill
derived, never typed. The **As-Staked Ledger PDF** prints the signed QA
deliverable: deltas grouped by instrument session, check-shot brackets,
datum and *which foot* declared, signature blocks. Control points live in a
locked number spool, witness stakes tether visibly to their parents, and a
one-tap **walk order** sorts the stake list by route without ever touching
a point number.

Points placed on sheets also show up as 3D pins on those sheets in the BIM
viewer, and the **Horizon Slice** control cuts the 3D model at any elevation
band — an animated section cut for coordination reviews; the systems legend
doubles as **Strata toggles** — click "domestic water" to hide or show that
whole system in 3D. Better still,
**⌂ From plan** extrudes the plan's own vector linework into the 3D model —
walls rise from your actual floor plan, in the same coordinate frame as the
layout points, so every pin lands inside the real building.

## The Loft — draft a plan from a blank sheet

The mold loft was the floor where full-size lines were drawn before anything
was built; **The Loft** (Plans & BIM) is that floor. It keeps the muscle
memory every drafter already has — wheel zoom at the cursor, middle-drag pan,
Esc chain, single-key tools, window-vs-crossing box selection, Shift for
ortho — inside a deliberately original board: a compact **tool spool** with a
per-tool options bar instead of a ribbon, the **Binder** tree on the left
(**Plies**, **Stencils**, **Plates**), and the **Traits** panel on the right
with a live **Tally** of wall footage and fixture counts.

Under the cursor, the **Plumbline** system snaps to endpoints, midpoints,
intersections, perpendiculars and grid lines — each with its own glyph — and
a live temporary dimension reads out feet-and-inches and angle while you
draw. The standards are real: the architectural scale ladder, 3/32" body
text, pen-weight ladder, center-line grids with lettered/numbered bubbles
(I and O skipped), architectural tick dimensions, and a stencil library with
true plan-size plumbing fixtures. Walls chain like a drafter draws, doors
hang on walls with swing and hand you can flip mid-placement, rooms tag and
auto-increment, and undo runs a thousand deep.

And the Loft runs pipe. Draw a run flow-wise with the **Pipe tool** —
sanitary, vent (dashed, as convention demands), storm, domestic hot/cold,
gas — and **Pipewright** derives every fitting deterministically from the
geometry and the system: elbows by angle, wye vs san-tee vs combo by
drainage rules, reducers where sizes meet, p-traps at fixtures. Tell it
**Slope run… `1/8, 98.5`** and invert elevations propagate down the
network, printed on the plan as `IE 98'-4 1/2"`. **Cap open ends** closes
every loose end in one command; **Check ✓** lists findings — under-minimum
slopes, drainage reductions, uncapped ends — and never silently "fixes"
anything. Every command is one undo. Pipe footage and fitting counts land
in the Tally, and **To 3D** carries the runs at their true inverts.

And you can simply *tell* the board what to draw. The **Weave bar** (press
`/`) takes trade English: `run 4" sanitary from the wc to the main at 1/8
per foot` — the **Weaver** finds your water closet, routes to the biggest
sanitary main, derives the fittings, slopes the run, and answers in plain
feet-and-inches: *"Ran 22'-6" of 4" sanitary at 1/8"/ft — IE drops
0'-2 13/16"."* `cap the open ends` · `replace that wye with a combo` ·
`slope this run at 1/4` · `add a drinking fountain` (it asks *"Where does
it go?"* — answer in the same box). Missing information gets ONE pointed
question, out-of-trade requests get an honest refusal, every command is a
single undo, and there is no AI guesswork anywhere in the chain: a
fixed verb table feeds deterministic drafting engines, so the Weaver can
only draw what it can justify. It even learns your phrasing through the
Heartwood — new field words you confirm become proposed synonyms, gated
by you.

And it holds a conversation. `draw a 12 by 10 restroom at B-2 with two
lavs, a wc and a floor drain` → four walls, a hung door, a numbered room
tag, and fixtures spaced along the back wall — one undo for the lot. Then
`make it 14 wide` reshapes it, `add another lav` extends the row, `zoom
fit` frames it, and `minimum slope for 4"?` gets answered mid-draft from
the piping tables (or from your Heartwood, with citations). Room layouts
you like can be saved as named macros — which only replay after you trust
them, because nothing self-taught ever fires without your sign-off.

Prefer to say it out loud? The **🎙 Squawk Box** (on the Weave bar) is a
mic/headset deck like a meeting app's — pick your input device, watch the
live level meter, hold the button (or F9) to talk. Recognition is built
entirely from scratch and trained by *you*: record each command phrase two
or three times and those recordings are the model, stored locally,
growable phrase by phrase. A confident match fires the Weave bar; anything
uncertain shows "did you mean…" and never auto-fires a drawing command.
It's a trained phrase deck, not open dictation — the honest trade for
running 100% offline with nothing embedded.

## Holler — hands-free control for any CAD

The **⟟ Holler** button opens a floating companion that types into
*whatever window has focus* — so you can drive your other CAD tools by
voice too. Say a measurement and **the Caller** types it formatted —
"one hundred five feet six and seven eighths" becomes `105'-6 7/8"`, and
"L two and one half by two and one half by one quarter" becomes
`L2 1/2x2 1/2x1/4` — with format profiles to match any program's expected
input. Say a tool word and a **Trip** fires its shortcut; say a phrase and
a **Placard** stamps the exact boilerplate; say a folder name and a
**Fetch** opens it; say a macro name and a **Run** plays the keystroke
sequence with real waits. The **Songbook** is your command dictionary —
edit it right in a spreadsheet — and the **Ticker** tapes what it heard,
what it did, and every keystroke it saved you. It complements your mouse
and keyboard; it never replaces them.

Honest boundaries, as always: the keystroke sender is a Windows OS call, so
on other platforms it runs in preview mode showing the exact keystrokes it
*would* send; recognition is speaker-trained (any language you record, no
speech-pack dependency); and Planloom's own process still opens zero
network sockets — opening a target is a local OS hand-off, with browser/URL
targets opt-in and clearly labeled.

A draft saves as one `.loft.json` file, and leaves the Loft three ways:

* **Plate PDF** — a titled, bordered sheet (title block, north arrow,
  graphic scale bar) at any standard sheet size, auto-fitting the scale
* **DXF** — R12 with your Plies as layers, importable by practically any
  CAD tool
* **Bridges** — extrude the draft into the 3D BIM viewer, send grid
  intersections to Fieldstitch as layout points, or export the Tally as a
  takeoff CSV for Reckoner

## The Old Hand — ask the trades, from anywhere

Hit **Ctrl+/** in any workspace (or the ⚘ button in the status bar) and the
**Old Hand** slides in: Planloom's offline trade brain. Ask a question in
your own words — "what size is the hot wire for a 20 amp circuit" — and it
answers from **Heartwood**, the knowledge core, with quoted passages and a
citation on every block. It was built from scratch (no cloud AI, no
downloaded models, no network — ever): meaning search comes from term
vectors trained on *your own* knowledge base plus a curated trade thesaurus,
so field words find code words. It summarizes across documents by picking
the central sentences verbatim, restates code-speak into plain words without
ever touching a number, and when it doesn't know, it says so — it can never
invent a code requirement.

And it learns. Every answered RFI you stamp is offered into the Heartwood;
daily use sharpens its ranking; you can teach it directly. But nothing
becomes gospel on its own: everything it learns lands as an *unverified shop
note*, clearly labeled in answers, until you trust it in the Manage screen.
Seed it in one click by importing an existing knowledge-base file, or feed
it PDFs and text.

The growth is fenced — **the Corral**. The Manage screen shows the
provenance of every learned item (purge any of it in one click), a
**Compact now** sweep keeps the store lean, and your accumulated learning
exports to a single file you can carry to another machine. Ground Truth
gauges the brain: knowledge-base size, growth sparkline, unverified queue,
weekly usage. And it's red-team tested: a document planted with hostile
instructions ingests as quoted data only — the test suite proves the
drawing agent behaves *bit-identically* with and without poisoned
knowledge, because knowledge is data and the verb table is fixed.

## The Backcheck — your instant peer checker

The senior reviewer's red-pen pass, automated. Point the **Backcheck**
(Plans & BIM) at the open plan or your Loft draft and in seconds it catches
what slips through the cracks — across six categories: **technical-data
inconsistencies** (duplicate sheet numbers, contradictory dimensions, blank
title blocks), **ambiguous or incomplete drawings** (dangling detail
references, vague "by others"/"verify in field" notes, missing scales,
unlabeled rooms), **geometry flaws** (sharp corners, unclosed walls,
overlaps, degenerate 3D, unsupported pipe spans), **non-conformance**
(sub-minimum lettering, missing invert callouts, untrapped fixtures,
slope minimums), **conflicts with lessons learned** (repeat issues matched
against your own trusted Heartwood lessons), and **DFX / constructability**
(thin walls, corridor pinch points, doors swinging into fixtures, rooms
with no door, dead-end mains).

Every finding names the rule that produced it and suggests the fix — no AI
guesswork, all deterministic. Then it writes them **directly onto the
design**: severity-colored revision clouds and comment callouts as real PDF
annotations, or text marks on a dedicated Q-BACK layer in the Loft (cleared
in one command). And it's honest about its limits — checks that need a
mechanical solid part (GD&T, hole callouts, injection-molding draft angles)
are listed under "Not checked, and why" rather than faked. Native
proprietary CAD/BIM files are closed formats; export to PDF or DXF and the
Backcheck reads them — your structured Loft and Pipewright drafts get the
deepest checks of all.

## Daybook, Reckoner, Crewpass

* **Daybook** (Field Management) — the foreman's daily journal: crew,
  weather, work performed, measurements, comments, photo references (paths
  only — nothing is copied or uploaded), and a one-click paginated PDF log.
  Double-click an entry to open its photos in **Lookout**, the built-in
  offline 360° viewer: 2:1 equirectangular site shots become drag-to-look-
  around panoramas (pure math, no cloud, no plugins); ordinary photos open
  fitted.
* **Reckoner** (Project Management) — quantity takeoff from the drawing
  markups: count dots, length runs and area takeoffs become quantities;
  point it at a local **price book CSV** and it produces a priced estimate,
  export as CSV or PDF. No cloud pricing, ever.
* **Crewpass** (Tools menu) — an offline seat ledger: assign users to
  devices, transfer seats, print the usage report. A local JSON file; no
  license server, no activation calls.

## Everything else in the box

Combine/split/rotate PDFs with bookmarks · one-touch PDF repair (unlock,
fix broken files, compress, flatten, rasterize, upscale, linearize,
de-JavaScript) with a damage-proof verify step · built-in offline OCR (the
Tracer — Planloom's own from-scratch engine, no external binary) ·
**auto-hyperlinking** — every sheet reference in a set becomes a native
clickable jump that works in any viewer, plus a bookmark index per sheet ·
revision compare with FFT auto-align · calibrated measurements with per-sheet
scale memory · auto-numbered punch dots · batch stamping · a submittal-log
parser · a command palette (Ctrl+K) that searches every feature.

## Install & run

Python 3.10+ with Tk, then:

    pip install -r requirements.txt
    python -m rfi_stamper            # the Planloom GUI
    python tests/run_all.py          # full test suite (18 scripts)

On Windows, double-click `build_windows.bat` once to produce
`dist\Planloom.exe` and `dist\planloom-cli.exe` — self-contained, offline,
no Python needed by end users, with the Planloom icon baked in (the spec
bundles `assets/` automatically). Works on x64 and Windows-on-ARM. (The
internal Python package keeps its original `rfi_stamper` name for API
stability; the product is Planloom.)

CLI (also answers to the legacy flag style):

    planloom-cli stamp -p plans.pdf -r rfi_folder -o out.pdf
    planloom-cli merge a.pdf b.pdf -o combined.pdf
    planloom-cli compare old.pdf new.pdf -o overlay.pdf
    planloom-cli doctor plans.pdf --action auto
    planloom-cli ocr scanned.pdf | hyperlink plans.pdf | log | batch | submittal

## Honest limits

Sheet detection needs text near the title block (scanned sets: run OCR
first). OCR is Planloom's own built-in engine (the Tracer) — no install, no
external binary; it reads title-block and large lettering well, with
small/degraded/linework-fused text an honest work-in-progress routed to
review. The BIM viewer is a wireframe walkthrough with sheet
placement, not an IFC authoring tool. Offline summaries are serviceable cliff
notes, not judgment calls. The verification pass is the backstop: if it says
FAIL, don't issue the sheet. Cloud collaboration and mobile apps are out of
scope by design — this tool exists to keep NDA-covered documents local.
