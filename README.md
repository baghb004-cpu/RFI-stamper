# RFI Stamper — offline plan toolkit

A desktop toolkit for construction drawings that runs **100% offline**:

* **Stamp RFIs** — overlay RFI cliff-note boxes onto the matching sheets of a
  plan-set PDF, any trade, any firm's RFI format, with pixel-diff verification
  that nothing on the drawings was covered.
* **Combine PDFs** — merge, reorder, extract page ranges, rotate, split, with
  a bookmark per source file.
* **Markup & Measure** — pen, highlighter, lines, arrows, rectangles,
  ellipses, revision clouds, callouts, text, images; calibrated length /
  polylength / area / count measurements with custom captions; a searchable
  Markups List with statuses; a searchable Tool Chest of reusable presets;
  Multiply for offset copies and grids; undo/redo; dark mode.
* **Compare / Overlay** — Auto Align two revisions (deterministic image
  registration: translation + rotation) and view or export a color overlay
  (red = removed, blue = added, dark = unchanged).
* **PDF Tools** — one-touch, background fixes for the common PDF problems that
  trip up other editors: **Auto-Fix** (unlock + repair + strip hidden data,
  verified safe), unlock password/owner-locked files, repair broken/corrupt
  structure, compress (image downsample), **OCR** to a searchable layer,
  **Auto-Hyperlink** every sheet reference, flatten annotations, flatten to
  image (rasterize / "reverse-OCR"), upscale, web-optimize (linearize), strip
  metadata, normalize rotation, remove embedded JavaScript/attachments. Every
  operation writes a **new** file — your original is never touched — and a
  Diagnose button lists what's wrong with a dropped PDF and fixes each issue.
* **Auto-Hyperlink** — drop or open a plan set and the tool finds every sheet
  reference (P-101, A-5.02, "3/A-501") throughout the document and adds
  **native GoTo links** to the referenced page, plus a sheet-index outline in
  the bookmarks panel. These are standard PDF link annotations, so the jumps
  work in **any** viewer — Bluebeam, Acrobat, Preview, and open-source readers
  alike. Click P-101 anywhere, land on sheet P-101.
* **RFI & submittal logs** — generate a clean paginated RFI log PDF (cover
  sheet) from any stamp run, or parse a submittal register into a submittal
  log PDF.
* **Batch** — stamp many plan sets against one RFI pile in a single run.

Drag-and-drop works everywhere a file can go: plan sets, RFI piles, combine
lists, compare slots, images onto drawings — and dragging a file anywhere
over the window turns the whole app into one giant labeled drop target,
routed to the right tool. Every drop zone also works by click-to-browse.

The interface is built around big type and open space: a Home dashboard with
action cards and recent files, an RFI dashboard with at-a-glance stat tiles
after every scan, a sheet navigator with page thumbnails and detected sheet
numbers (jump to "P-2.01", not "page 37"), toast notifications, a busy
spinner, a zoom badge, and F11 fullscreen. All animation is timer-based tk —
no render loops, near-zero idle CPU.

## Privacy and NDA safety

This tool is built for documents you are not allowed to leak.

* **No network code exists in the app.** There is no update check, telemetry,
  crash reporter, cloud sync, or AI API call. RFI note summaries are produced
  by a deterministic, offline text summarizer.
* **Offline guard.** On top of having no network code, the app installs a
  process-wide kill-switch at startup that blocks *any* outbound socket
  connection (defense-in-depth, visible in the status bar as
  `● OFFLINE — network blocked`). If some future dependency ever tried to
  phone home, it would get an `OfflineError` instead of a connection.
* **Local files only.** Markups save to a JSON sidecar next to the PDF;
  preferences and the Tool Chest live in `~/.rfi_stamper/`. Nothing is
  written anywhere else.

## Install & run

Python 3.10+ with Tk. Then:

    pip install -r requirements.txt
    python -m rfi_stamper            # GUI
    python tests/run_all.py          # full test suite

`tkinterdnd2` enables OS drag-and-drop; without it the app still works with
Browse buttons. **OCR is optional and offline**: if the free
[Tesseract OCR](https://github.com/tesseract-ocr/tesseract) engine is
installed on the machine, the OCR button and `ocr` command light up; if it
isn't, every other feature still works and the app never phones home either
way. Nothing else needs an external binary.

### Windows executables

PyInstaller does not cross-compile, so build on Windows: double-click
`build_windows.bat` once. It produces `dist\RFI-Stamper.exe` (GUI) and
`dist\rfi-stamp-cli.exe` — self-contained files you can copy to anyone in the
office; end users do not need Python. On ARM-based Windows machines, a native
ARM Python produces a native ARM executable the same way.

## Using the RFI stamper

GUI: pick the plan set, drop in RFI files or a folder, **1 Scan & map**,
review the mapping table (double-click a Sheets cell to correct it — this
review step is the human safeguard on the automated mapping), then
**2 Stamp & verify**. The output PDF and a `_report.txt` land next to the
plan set.

What a run does: reads every sheet and detects the sheet number from the
title block (P-10.10, M-2.01, A-553 — discipline-agnostic), parses each RFI
file for its number, title, question, answer, and drawing references, maps
each RFI to the sheets it names, finds measured-empty white space on each
target sheet, and draws the note there: thin red-outlined box, white fill,
red text, bold `RFI ### — SUBJECT` header, one-to-two-line Q/A body, multiple
RFIs stacked per sheet. It never covers linework: a spot only qualifies if a
padded window around the box contains zero content pixels, and after stamping
it renders every page and pixel-diffs against the original — the run only
reports PASS if the sole change on every stamped page is the box itself and
untouched pages are identical. Anything unplaceable, and any RFI with no
sheet reference, goes to a clearly-labeled appendix page instead of being
forced onto a drawing.

Input formats: ordinary RFI PDFs (one or many RFIs per file), the zip-style
export packages some document-controls systems produce with a `.pdf`
extension, and raw text dumps. Answered copies of earlier RFIs found inside
later packages automatically backfill the earlier record's answer.

## Command line

    rfi-stamper stamp -p plans.pdf -r rfi_folder --scan-only mapping.csv
    (edit the sheets column in Excel if needed)
    rfi-stamper stamp -p plans.pdf -r rfi_folder --map mapping.csv -o out.pdf
    rfi-stamper merge a.pdf b.pdf c.pdf -o combined.pdf --pages 1-3 all 2-
    rfi-stamper split big.pdf --every 1 -d pages/
    rfi-stamper compare old_rev.pdf new_rev.pdf -o overlay.pdf
    rfi-stamper doctor plans.pdf --action auto        # unlock+repair+strip meta
    rfi-stamper doctor plans.pdf --action diagnose    # list problems
    rfi-stamper ocr scanned.pdf -o searchable.pdf     # offline Tesseract
    rfi-stamper hyperlink plans.pdf -o linked.pdf     # cross-link sheet refs
    rfi-stamper log -p plans.pdf -r rfi_folder -o RFI_log.pdf
    rfi-stamper batch -p setA.pdf setB.pdf -r rfi_folder -d out/
    rfi-stamper submittal register.pdf -o submittal_log.pdf

The mapping CSV's `via` column tells you how each match was made: `planref`
(labeled Plan Ref / Drawing Number line — high confidence), `body` (sheet
token elsewhere in the RFI text — worth a glance), `manual`, or `unmatched`.
Tokens that only appear inside attachment listings are reported in
`attachment_refs` but never auto-mapped. CLI exit code is 0 only when
verification passes.

## Feature notes

* **Multiply** (Ctrl+M): offset copies of any markup or measurement — linear
  runs or full grids — for scaling up counts, building forms, and grids.
* **Custom captions**: give a measurement a caption template such as
  `{subject}: {value}` and the resolved caption is displayed on the drawing
  and in the Markups List. Placeholders: `{value} {unit} {subject} {comment}
  {text} {page} {status}`.
* **Statuses by keyboard**: select markups, `Alt+1..5` assigns
  none/accepted/rejected/completed/cancelled. The CSV export can include the
  full status history or the latest status only.
* **Scale presets**: pick a standard architectural or metric scale
  (1/16"–3" = 1'-0", 1:50–1:500) from the scale menu, or calibrate from any
  two points of a known dimension — every measurement gets a real-world
  caption either way.
* **Auto-numbered counts**: with auto-numbering on, count dots label
  themselves from the Label field — `P` becomes P-001, P-002, … — a ready-made
  punch list you can export to CSV.
* **Construction stamps** ship in the Tool Chest: HOLD, AS-BUILT, REVISED,
  NOT IN CONTRACT, BY OTHERS, VERIFY IN FIELD, numbered punch dots.
* **Tool Chest search**: type in the box above the presets; save any current
  tool + style as a new preset.
* **Command palette** (Ctrl+K): fuzzy-search every feature, tab, tool, and
  preference — feature discovery without digging through menus.
* **Dark mode** (Ctrl+D), plus optional PDF color inversion in the markup
  view for late-night sheet reading.
* **Auto Align** is deterministic FFT-based image registration — no AI, no
  cloud — so the same inputs always align the same way.

## Honest limits

Sheet-number detection assumes the number appears as text near the
bottom-right title block; pure-image (scanned) plan sets won't index without
OCR. RFIs whose only references are to another trade's sheets correctly won't
match — load that trade's set instead, or add the sheet by hand in the review
step. Offline summaries are serviceable cliff notes, not judgment calls; a
human edit closes that gap. The verification pass is the backstop for
everything else: if it says FAIL, don't issue the sheet. The Compare overlay
is raster-based (it compares what prints, not vector objects), and the
markup editor is a drawing-review tool, not a full CAD annotator — image
markups preview as placeholders on canvas and embed for real when you apply
to PDF.

Out of scope by design (this tool is offline-first): cloud collaboration /
studio sessions, mobile apps, and organization-wide user administration.
3D navigation, PDF form filling, and partial pen-stroke erasing are not
implemented (delete + redraw covers the last one).
