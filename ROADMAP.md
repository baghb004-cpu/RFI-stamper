# ROADMAP.md — Planloom: the road to a drawing-driving trade brain

The owner's brief, in one line: **advanced 3D where it earns its keep, a
self-growing trades-only brain that cannot break out, and the ability to
type (or talk) to Planloom and have it draw — connect pipe and fittings,
slope runs at 1/8" or 1/4" per foot, replace a fitting, cap open ends.**
Everything from scratch. Nothing removed.

This file chunks that into shippable rounds. Each phase lands green
(`python tests/run_all.py`), scrubbed, committed, pushed. Names come from
the registry in HANDOFF.md; new names are declared here.

## New names (registry additions)

| Name | What it is | Why the name |
|---|---|---|
| **Pipewright** | the from-scratch piping domain engine (runs, fittings, slopes, inverts) | a wright is a maker — shipwright, millwright, pipewright |
| **The Weaver** | the drawing-driving agent: typed commands → drafted geometry | the one who works the loom |
| **The Corral** | the self-learning containment design | the brain grows inside the fence, never acts outside it |
| **Squawk Box** | mic/headset voice-command deck (speaker-trained, offline) | job-site slang for the intercom |

## The honest architecture (why this works offline, from scratch)

Talking-to-draw does NOT require a generative model. It decomposes into
three provable parts:

1. **Understanding** — a from-scratch command parser: a trade lexicon
   (verbs, objects, sizes, slopes, directions), a slot-filling grammar, and
   Heartwood's thesaurus + meaning vectors so *field words* land on
   canonical tokens ("crapper" → water closet, "fall" → slope). When a slot
   is missing, the Weaver asks back — a bounded clarifying question, not a
   guess.
2. **Deciding** — deterministic domain solvers. Fitting selection at a
   junction is geometry + system rules (angle → elbow 90/45; three-way on
   sanitary → wye/san-tee/combo by flow direction; open end → cap or
   cleanout). Slope is arithmetic (invert elevations propagated through the
   network). Code minimums are table lookups (e.g. small-bore sanitary
   wants 1/4"/ft, 3"+ allows 1/8"/ft) that WARN, never silently "fix".
3. **Acting** — existing, tested machinery: Loft entities with undo×1000,
   ghost previews before multi-entity commits, pixel-honest rendering,
   exports. Every command echoes back what it did in plain words and is
   one Ctrl+Z from gone.

The "AI" feel comes from 1 + Heartwood's learning; the trust comes from 2
and 3 being deterministic. It cannot draw something it cannot justify.

**Honesty note (talking):** open-vocabulary dictation from scratch is not
a promise worth making — the Weaver is TYPED chat first. But a
**speaker-trained voice-command deck IS buildable from scratch**, and it
ships as the **Squawk Box** (Phase C):

- **Device picker like a meeting app**: a settings dialog listing every
  input device (built-in mic, USB headset…) with a live level meter, a
  test-record/playback button, and a push-to-talk key. On Windows the
  capture layer is written from scratch against the OS wave-in interface
  via ctypes — no new packages, nothing embedded.
- **Recognition the from-scratch way**: classic signal processing — MFCC
  features (numpy DSP, written here) + dynamic-time-warping template
  matching. YOU train it: record each command phrase 2–3 times in your
  own voice ("cap open ends", "slope one eighth", digits for sizes), and
  those recordings ARE the model — stored locally, per user, growable
  phrase by phrase. This is the same self-learning ethos as Heartwood:
  the model is built from what the operator gives it.
- **Honest boundary**: it recognizes the phrases you trained (a growing
  deck of dozens, speaker-dependent, push-to-talk), not free-form speech.
  Anything unrecognized shows the closest matches and asks — never
  guesses a drawing command. Typed commands remain the full-power path;
  voice is the hands-busy shortcut.

## The Corral — self-ballooning without breakout (standing rules)

The brain may GROW from: uploads (PDFs/text/knowledge-base imports), what
is typed (commands, questions, teachings), what is asked (query phrasing,
click-to-reinforce feedback), and Planloom's own work (answered RFIs,
daybook notes). It may NEVER:

- touch the network (offline_guard stays default-on; the engine has no
  networking imports — enforced by tests);
- execute anything from KB content (knowledge is DATA; the parser maps to
  a fixed verb set; there is no eval, no plugins-from-content);
- promote facts on its own (lane 2 stays human-gated: unverified until
  trusted — the Apprentice rule);
- write files outside its store except through explicit user actions;
- grow unbounded (store caps + compaction: feedback log pruning, vector
  vocab cap, dedupe — with a Ground Truth gauge showing size/growth).

## Phase A — Fieldstitch Pro (research done, build next)

Point layout grows to field grade, per the 8-agent research pass:
- point TYPES (control / layout / as-staked / check) + status lifecycle
  (pending → staked → verified/rejected) with plan colors;
- tolerance classes per trade (anchor bolts, embeds, sleeves…) with
  numeric defaults; as-staked CSV import → design-vs-field ΔN/ΔE/ΔZ
  delta report with pass/fail — the "verify construction against design
  intent" loop, fully offline;
- export dialog options from the owner's brief: point order (N,E,H /
  E,N,H), output units incl. survey vs international foot, precision,
  duplicate-number check, code column, header prefix, delimiter;
- new exchange formats (an open XML survey format; a fixed-width
  field-book format) as new kits, named per the knot registry;
- auto point generation: wall corners (with corner offsets), points along
  a line at spacing / divide-N, offset from baseline, rectangular bolt
  arrays, line intersections — sourced from Loft geometry and vector plans;
- walking-route sort (nearest-neighbor) for stake lists; work packages.

Acceptance: engine tests for formats/deltas/generators; construct test
drives the new tools; docs + registry updated.

## Phase B — Pipewright v1: the piping engine (the hands)

New engine `rfi_stamper/pipewright.py` + Loft integration:
- **Data model**: pipe runs (polylines with system, diameter, material),
  nodes, derived fittings. Systems: sanitary, vent, storm, domestic CW/HW,
  gas. Loft gains a "pipe" tool (P) with system/size options; new plies
  (P-SAN, P-VENT, P-DCW, P-DHW…).
- **Auto-connect**: pipe ends snap-join within tolerance; fixture stencils
  gain outlet points so a WC/lav connects to the nearest compatible main;
  Manhattan + 45° routing between two picked points.
- **Fitting derivation**: every junction resolves to a fitting by geometry
  + system rules (elbow 90/45, tee, san-tee, wye, combo, coupling,
  reducer, p-trap at fixture outlets, cleanout, cap). Plan symbols drawn
  per drafting convention; fitting list feeds the Tally.
- **Slope solver**: `slope(run, 1/8"|1/4" per ft, from=invert)` computes
  invert elevations node-by-node through the connected network; IE
  annotations on the plan; violations of table minimums → warnings panel.
- **Edits**: replace fitting at a node; cap ALL open ends (one command);
  resize a run up/downstream; insert cleanouts at rule spacing.
- 3D: runs extrude as sloped solids into the BIM viewer (see Phase D).

Acceptance: pipewright test suite (junction resolution truth table, slope
math to 1/16", cap/replace/resize ops, takeoff counts); construct test
draws a small sanitary tree by tool; Tally shows pipe LF by size +
fitting counts.

## Phase C — The Weaver v1: typed commands drive the drawing (the voice)

New `rfi_stamper/weaver.py` + a command bar in the Loft (and a "weave:"
mode in the Old Hand drawer):
- **Lexicon + grammar**: ~20 verbs (run, connect, slope, cap, replace,
  draw, add, move, delete, dimension, label, zoom…), objects (walls,
  rooms, fixtures, grids, pipe systems, fitting types), qualifiers
  (sizes, slopes "1/8 per foot", directions, counts, references "from the
  water closet to the main", grid addresses "at B-2"). Reuses
  `draft.parse_ftin`; Heartwood synonyms resolve field words.
- **Slot filling + clarification**: missing size/system/target → ONE
  pointed question back ("Which open end — at the lav or at the main?").
- **Ghost + confirm**: multi-entity commands preview as a ghost overlay;
  "weave it" / Enter commits, Esc discards; single-entity commands apply
  immediately (undo covers everything).
- **Explain + learn**: every command echoes plain-words results ("Ran
  22'-6" of 4" sanitary at 1/8"/ft; added 2 wyes, 1 cleanout; IE drops
  0'-2 13/16""). Successful phrasing→intent pairs land in Heartwood lane 1
  (ranking memory); corrections teach the parser's synonym table via the
  human gate.
- **Squawk Box (voice)**: the mic/headset deck described above — device
  picker with level meter, push-to-talk, speaker-trained MFCC+DTW phrase
  recognition feeding the SAME parser as typed commands (voice is just
  another way to fill the command box). Windows capture via ctypes
  wave-in; the DSP/matcher fully unit-tested with synthesized WAV
  fixtures so the pipeline is proven even where CI has no microphone.
- Target commands from the brief, day one: `run 4" sanitary from the wc
  to the main at 1/8 per foot` · `slope this run at 1/4` · `cap the open
  ends` · `replace that wye with a combo` · plus draw/move/delete for
  walls, fixtures, grids, dims.

Acceptance: parser test corpus (≥80 phrasings incl. slang, ambiguity,
refusals for out-of-trade commands); construct test types commands and
asserts resulting geometry; every command undoable.

## Phase D — 3D uplift: advanced where it earns its keep (the eyes)

Canvas-only (no GPU), quality-tier honest, ambient-cost zero:
- filled, flat-shaded wall faces with painter's-algorithm depth sort
  (wireframe stays as a toggle and as the "reduced" tier);
- Pipewright runs as sloped solids, system-colored, with a slope
  exaggeration slider (1× to 10×) so 1/8"/ft is visible at building scale;
- first-person WALK mode (eye height 5'-6", arrow/WASD, collision-free)
  alongside orbit; isometric preset buttons (NE/NW/SE/SW);
- depth-cued line weight/fade, 3D measure tool (pick two points, get
  feet-inches + ΔZ), section-cut interplay with Horizon Slice preserved;
- Loft→3D and extrude→3D keep one world frame with Fieldstitch pins.

Acceptance: render correctness in construct test (face counts, cull
behavior, walk-mode camera math), zero idle CPU preserved, quality "off"
still fully usable.

## Phase E — The Weaver v2: draw the whole plan by conversation

- compound commands ("draw a 12 by 10 restroom at B-2 with two lavs, a
  wc and a floor drain, vent through the wall") → room macro: walls,
  door, fixtures placed at code-legal spacings, pipe stubs;
- pattern macros learned from the user's own drafts (lane 2, gated);
- multi-turn context ("make it 14 wide" edits the last room);
- Weaver reads the Heartwood: "slope limits for 2 inch?" answered inline
  with citations while drawing.

## Phase F — Corral hardening + growth visible

- store caps/compaction jobs + provenance browser (where every learned
  item came from, one click to purge);
- Ground Truth gauges: KB size, growth rate, unverified queue depth,
  answer hit-rate;
- learned-state export/import (one file, offline hand-off between
  machines);
- red-team test suite: prompt-injection-style content in uploaded PDFs
  must never alter behavior (knowledge is data; the verb set is fixed).

## Phase G — the Backcheck: the instant peer checker (owner brief, added
## after A-F shipped)

The senior's red-pen pass, automated: analyze the open plan set or Loft
draft, catch design issues before they slip through, and write the
findings as real markups and comments directly on the design. Six finding
categories (the owner's list, translated honestly to what an offline
from-scratch engine can PROVE — every rule deterministic, every finding
citing its rule):

1. **Inconsistencies in technical data** — title-block discrepancies
   across sheets (sheet-number duplicates/mismatches vs the detected
   index, date/scale drift), duplicate or CONTRADICTORY dimensions (exact
   on Loft drafts: two dims measuring the same points with different
   text), material keyword conflicts on a sheet.
2. **Ambiguous or incomplete drawings** — dangling detail/section
   references (callout points at a sheet that doesn't exist — the
   hyperlink engine already finds references), vague-note lexicon ("as
   required", "by others", "verify in field", "match existing", bare
   "typ."), unlabeled rooms/grids, views without scales, undimensioned
   rooms.
3. **Design flaws in geometry** — sharp inside corners (acute wall
   junctions), unclosed wall runs (near-miss endpoints), fixture/wall
   overlaps, slender unbraced wall runs, degenerate/floating 3D geometry
   (open edges, zero-length segments, orphan islands), unsupported pipe
   spans vs the per-trade stride rules.
4. **Non-conformance to standards** — text below the 3/32" minimum,
   missing tolerance classes on layout points, sloped runs without
   invert callouts, fixtures without traps, penetrations without
   sleeves, missing title-block fields, slope minimums (already in
   Pipewright, surfaced here).
5. **Conflicts with lessons learned** — the Heartwood lessons lane:
   findings the owner marks recurring become lesson notes (human-gated,
   like everything); the checker matches new sheets/drafts against
   trusted lessons and flags repeats, with the lesson cited.
6. **DFX / constructability** — thin wall segments, corridor pinch
   points, doors swinging into fixtures (clear-width math), rooms
   without doors, cleanout access spacing, dead-end mains.

Inputs: vector plan PDFs (primary), Loft drafts + Pipewright networks
(structured = strongest checks), DXF and OBJ (geometry checks). Native
proprietary CAD/BIM containers are closed formats — export to PDF/DXF as
usual; say so honestly in the UI.

Output: a findings panel (severity blocker/major/minor/info, category
filters, jump-to-location) and **Write markups** — findings land as real
PDF markup annotations (clouds + comments, severity-colored) via the
existing markups engine, or on a dedicated QA ply in the Loft, removable
in one command. Every finding: code, message, suggestion, and the rule
that produced it (the Ground Truth insight-feed promise, kept here too).

A (Fieldstitch Pro) → B (Pipewright) → C (Weaver v1 + Squawk Box) →
D (3D uplift) → E (Weaver v2) → F (Corral hardening). B before C because
the Weaver needs hands before a voice; D after C so the first wow is
functional, not cosmetic. Each phase is independently shippable.

## Phase I — the Tracer: from-scratch OCR (retire the Tesseract dependency)

Researched (8-agent pass) and PLANNED — full plan in **OCR_PLAN.md**. Replace
the optional Tesseract OCR path with a pure Python + numpy + fitz engine for
scanned/raster plan pages (vector pages keep their perfect existing text
path). Verdict: feasible for this narrow domain (near-fixed technical fonts,
uppercase, ~40–60 char classes, structured fields) — clean-scan CER ≤ 1–2%,
within a small delta of Tesseract, and BEATS it on structured tokens via the
app's own sheet-index + dimension grammar + trade lexicon. Ensemble classifier
(NCC template + kNN memory + numpy MLP over 8-direction gradient features),
synthetic training data from Hershey/base-14 fonts + Kanungo/Baird degradation,
two-lane human-gated self-learning (per-firm font profiles). Drop-in for
`ocr.py` via a new `ocr/` package. Staged P1→P4, Tesseract removed only in P4
after an eval harness proves parity. Honest SKIPs: text fused with linework,
sub-legible scans, hand fonts.

**Status: COMPLETE (v4.4.0 → v4.7.0).** Built as the `rfi_stamper/tracer/`
package (P1 scaffold+NCC v4.4.0, P2 gradient-MLP/kNN ensemble + synthetic
training v4.5.0, P3 lexicon/grammar/number-lock + sheet-index self-supervision
v4.6.0, P4 eval harness + Tesseract retired v4.7.0). Measured clean CER 0.00%,
sheet-number field accuracy 100%. ocr.py is now a thin facade over the Tracer;
NO external OCR binary anywhere — Planloom has zero external binaries. Full
plan + numbers in OCR_PLAN.md. **v4.7.1 hardening:** the documented ~11%
degraded-photocopy residual turned out to be a bug — speckle collapsed the
glyph-height scale and the size gates deleted thin glyphs (`I - .`); a
noise-robust median took the speckled-scan CER to ~0, leaving only the genuine
touching/broken-glyph + sub-legible residual.

## Phase H — Holler: hands-free voice control for ANY app (owner brief,
## added after Phase G)

A system-wide voice layer that types real keystrokes into whatever window
has focus — driving external CAD/BIM the way a caller reads cuts to the
crew. ENHANCES mouse+keyboard, never replaces them. Reuses the Squawk Box
recognizer (from-scratch MFCC+DTW, speaker-trained) as the ear; adds the
deterministic hands. Honest boundaries: the keystroke SENDER is OS-level
(Windows user32 SendInput via ctypes — sibling of the winmm capture;
dry-run + intent log on non-Windows, GUI says so); opening targets is a
local OS shell hand-off so Planloom's own process opens ZERO sockets and
the offline invariant holds; URL targets opt-in per row, clearly labeled.
Multi-language falls out free: speaker-trained = any language you record,
no speech-pack dependency.

Original names (whole feature = **Holler**):
- **The Caller** — spoken-measurement grammar → formatted text; composes
  any dimension from a small trained number-vocabulary; "one hundred five
  feet six and seven eighths" → `105'-6 7/8"`; shape mode → `L2 1/2x2
  1/2x1/4`; format profiles (arch hyphen/space/no-hyphen, decimal, mm,
  custom) to fit any CAD.
- **Trips** — spoken word fires a tool-shortcut chord (user maps their CAD).
- **Placards** — exact boilerplate text inserts, case/format preserved.
- **Fetches** — open a file/folder/app (opt-in URL) by phrase.
- **Runs** — keystroke macros with real waits between steps.
- **The Songbook** — the editable command dictionary (JSON + CSV round-trip).
- **The Ticker** — live heard/did preview, command counter, keystrokes saved.

Engine rfi_stamper/holler.py (Caller grammar pure/testable, Songbook model
+ persistence + CSV, keystroke Sender w/ HAS_SEND honest dry-run, Router
w/ Songbook-then-grammar precedence + reused confidence gate, Ticker).
GUI: a floating always-on-top Holler companion (global — status bar +
palette + from the Squawk Box), device picker + hands-free/push-to-talk,
Ticker tape, Songbook table editor + format-profile picker. Tests: Caller
corpus (100+ spoken→text incl owner examples verbatim), Songbook CSV
round-trip, Sender dry-run intent asserts, Router precedence, Ticker.

**Status: COMPLETE. All six phases shipped in order — A1 v3.4.0, A2
v3.5.0, B v3.6.0, C1 v3.7.0, C2 v3.8.0, D v3.9.0, E v4.0.0, F v4.1.0 —
each green across the full suite, scrubbed, and pushed to main. Deferred
polish items live in HANDOFF.md round notes (A-polish list, Weaver zoom
refinements, real-Windows mic smoke, threshold field-tuning).**


## Phase J — the mini-pdf writer: retire reportlab (owner directive:
## "everything from scratch")

**Status: COMPLETE (v4.8.0).** `rfi_stamper/minipdf/` — WinAnsi encoding,
Core-14 metrics (oracle-equal to ~1e-13), content-stream builder, byte-exact
classic-xref document (no metadata, content-hash /ID), reportlab-canvas
facade with reportlab page semantics, flow/table layout engine. Stamp
overlays pixel-identical to the retired library; plates within the verify
threshold; tables/forms/reports are the new engine's own clean layout.
reportlab OUT of requirements and excluded from both exes; dev boxes may
install it for the optional parity-oracle tests. Full plan + research
dossiers in MINIPDF_PLAN.md.

## Phase K — from-scratch drag-and-drop: retire tkinterdnd2

**Status: COMPLETE (v4.9.0).** `gui/dnd.py` is a pure per-toplevel drop
Router (hover synthesis, smallest-target routing, ext filter, overlay
fallback, deferred callbacks, destroy-pruning) fed by `gui/dnd_win32.py`,
a from-scratch ctypes OLE IDropTarget with explicit Win64-safe prototypes.
Click-to-browse remains the honest fallback everywhere the backend is not
live. Real-Windows drag smoke rides the standing Windows checklist.
