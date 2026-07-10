# SETSCAN_PLAN.md — reading the drawing set (scale · symbols · tags · marks)

**Status:** COMPLETE — all four phases SHIPPED: the Chalk Mark v5.3.0,
the Story Pole v5.4.0, the Reed Count v5.5.0, the Cut Ticket set-scan
v5.6.0. Same discipline that shipped the Tracer
(OCR_PLAN), minipdf (MINIPDF_PLAN) and the BUILDOUT campaign: research →
staged build behind tests → prove → ship, one phase = one round = one
version. README documents only shipped features and is updated as each
phase lands.

**The owner's ask (verbatim intent, decomposed):**
1. Find a dimension on a PDF drawing and use it to auto-scale the set — but
   be *certain*: cross-check the derived scale against known points in the
   project (door openings were the named example) before trusting it, and
   handle the other sheets of the set.
2. Auto-count fixtures, heavy plumbing emphasis — a database of what a
   toilet / sink / mop sink *looks like* as drawn under common drafting
   conventions, so the program counts a shape when it sees it.
3. Scan a whole set for plumbing fixture tags and produce a **preliminary
   fixture schedule**, editable to project specifications later — baked
   into the cut-sheet pipeline (the Cut Ticket → the Swatchbook).
4. During the cut-sheet build, find the specified model number on the
   manufacturer sheet and check / X the checkbox next to it.

---

## 0. Ground rules (bind every phase)

1. Fully offline; pure numpy + fitz + existing engines. No new deps.
2. Invariant #7: symbol conventions are described **convention-only** —
   never vendor/authoring-tool names in code, comments or docs.
3. **Certainty is a verdict, not a boolean.** Every automatic conclusion
   (a scale, a count, a checked box) ships with its evidence and its
   refusals; ambiguity is surfaced, never resolved silently. The human
   gate stays where it is: nothing builds or marks a deliverable without
   an explicit user action.
4. Every phase: plain-python tests with exact assertions (synthetic
   fixtures from the Loft's own plate/DXF exporters give KNOWN-scale,
   KNOWN-count drawings for free), full suite green twice, scrub, docs,
   version bump, commit + push.

---

## Phase 1 — the Story Pole: dimension-anchored autoscale, witnessed
**SHIPPED v5.4.0** *(module `rfi_stamper/setscale.py` — a story pole is the
carpenter's rod marked with known lengths, used to transfer and VERIFY
measurements.  Built to this plan; grid-module witness deferred as planned
(optional corroborator); scanned-set reading stays a Tracer stretch goal.)*

**Goal:** derive each sheet's true scale (pt per real foot) from its own
dimensions, and accept it only when independent witnesses agree.

- **Harvest hypotheses (vector-first):** dimension strings via
  `fitz get_text("words")` matched by the existing ft-in grammar
  (`draft.parse_ftin`); pair each with its dimension line (nearby parallel
  segment + extension ticks — the Slipsheet's line extraction already
  buckets segments). `text_value / segment_length` = one scale hypothesis.
- **The certainty contract (all gates must pass, else REFUSE):**
  1. ≥ N independent dimension witnesses (default 5) within ±0.5% of the
     median hypothesis; outliers reported by name (a mistyped dimension on
     the drawing is *found*, not averaged in).
  2. **Door-opening corroboration** (the owner's example): door swing arcs
     (quarter-circle + leaf) located in the linework; opening widths must
     land on standard leaf sizes (2'-0"…4'-0" in 2" steps) within
     tolerance for ≥ M doors.
  3. **Title-block scale note cross-check** when present ("1/8" = 1'-0""):
     measured scale must agree — a disagreement is the classic
     printed-half-size set, and the verdict says so with the exact ratio
     instead of silently mis-measuring.
  4. Grid-module witness (optional corroborator, never sole evidence).
- **Verdict object:** `{pt_per_ft, witnesses[], outliers[], door_checks[],
  note_check, PASS|REFUSED(reason)}`. The GUI shows the witness table; a
  PASS can be applied to the markup measure calibration (per-sheet scale
  memory already exists), Fieldstitch cal, and Reckoner takeoff.
- **Cross-sheet:** every sheet verifies independently (enlarged plans at
  1/4" beside 1/8" floor plans are normal); a set-level report lists each
  sheet's verdict. Scale is never inherited blindly.
- **Scope fence:** vector sheets first; scanned sets read dimensions via
  the Tracer + raster linework as a stretch stage (honest SKIP if thin).
- **Tests:** Loft `plate_pdf` output at known scale → exact pt/ft asserts;
  a poisoned dimension gets outvoted and NAMED; a half-size print REFUSES
  with the ratio; a no-dimension sheet refuses; door widths verify.

## Phase 2 — the Reed Count: fixture-symbol recognition + auto-count
**SHIPPED v5.5.0** *(module `rfi_stamper/reedcount.py` — the reed count is
the loom's dents-per-inch: THE density count of the trade.  Built to this
plan; the library seeds from the Loft STENCILS directly (no separate
data/symbol_library/ needed) and user-labeled symbols persist in
~/.planloom.  Two additions the plan didn't foresee: near-identical
conventions surface as AMBIGUOUS, and text-labeled symbols require their
label word.)*

**Goal:** count plumbing fixtures on vector sheets by matching linework
clusters against a symbol library.

- **Symbol library:** normalized geometric signatures (line/arc primitive
  sets, translation/rotation/scale/reflection-invariant) for WC (tank +
  flush-valve forms), lavatory, urinal, sinks (single/double/mop), floor
  drain, floor sink, water heater, drinking fountain, shower, tub —
  seeded from Planloom's own Loft STENCILS plus parametric variants of
  the common drafting-convention families (convention-only descriptions).
- **Matching:** filter walls/grids/long linework (extrude.py already
  does this cut); cluster remaining fixture-scale linework; normalize the
  cluster; nearest-signature match with a confidence score. **The Story
  Pole verdict gates size sanity:** with a verified scale, a WC candidate
  must footprint ~19"×28" real — the single strongest false-positive
  killer, and the reason Phase 2 depends on Phase 1.
- **Honesty:** per-sheet counts with locations + confidence; *unmatched
  fixture-sized clusters* land in an "unknown symbols" tray — the user can
  label one, and the labeled shape joins the library (human-gated, the
  review-deck precedent). Counts are proposals — they feed Phase 3, never
  a deliverable directly.
- **Tests:** Loft-generated plans with known stencil placements → exact
  counts; rotated/mirrored placements; a decoy (chair-sized rectangle)
  never counts; unknown-cluster tray fills honestly.

## Phase 3 — the Cut Ticket reads the set: tags → preliminary schedule
**SHIPPED v5.6.0** *(extends `cutticket.py` — new source lane
`origin="set-scan"` beside the existing Loft-model lane; the per-source
reconcile merges lanes exactly as planned.)*

**Goal:** scan a whole plan set for fixture tags and emit the preliminary
fixture schedule as Cut Ticket rows the Swatchbook auto-feeds.

- **Tag harvesting, context-gated** (the Loft lesson holds: naked
  tag-shaped text is a false-positive trap — `P-1` is also a plumbing
  sheet number):
  1. strongest: a tag token adjacent to / leadered to a Phase-2 recognized
     symbol (tag + shape corroborate each other);
  2. the **fixture schedule table itself**: detect the schedule header row
     on legend sheets, parse rows into tag + description + the schedule
     paragraph — which pre-fills the Swatchbook **callouts** (manufacturer
     + model strings resolve against the cut-sheet library's aliases);
  3. hard rejects: sheet-reference forms (`2/P-1`, `SEE P-101`), any token
     matching the set's own sheet-number index (sheets.py already builds
     it), title-block text.
- **Output:** pull-list rows `{tag, count (symbol-derived where available),
  description, callout candidates, source sheets}` as PROPOSALS —
  reconciled per-source, human-owned fields untouched, tombstones on
  re-scan, exactly like the model lane. The Swatchbook shows them with
  loud provenance; the user edits to project standards; Build All stays
  the only path to PDFs.
- **Tests:** synthetic legend sheet (minipdf table) + plan sheets → exact
  schedule rows incl. parsed callouts; sheet-ref decoys rejected; re-scan
  reconcile preserves human edits; count column matches Phase 2.

## Phase 4 — the Chalk Mark: model-number checkbox marking on cut sheets
**SHIPPED v5.3.0** *(extends `swatchbook.py` — a tailor's chalk mark tells
the shop exactly where to cut; shipped first, independent of Phases 1–3.
Built to this plan with the standing defaults: red X, report-only GUI
default, vector-first. Bonus fidelity fix shipped with it: form-widget
checkboxes are now BAKED into packet content — `show_pdf_page` was
silently dropping them from delivered packets. See HANDOFF Round 37.)*

**Goal (the owner's direct question — yes, it is possible):** during a
packet build, find the specified model number on the manufacturer sheet
and mark the checkbox beside it.

- **Locate:** the component's resolved callout/model string searched in
  the sheet's text layer (fitz words; alias-normalized matching — the
  machinery exists); image-only sheets go through the Tracer (stretch).
- **Find the box:** small empty square candidates (vector rects or box
  glyphs, ~4–14 pt) within the matched line's row band.
- **Mark:** a red X (or slash — owner's call) inside the box bounds, same
  approved submittal red, drawn in the same pass as the corner tag stamp,
  before the Shuttle re-serialize.
- **The certainty contract (marks a legal submittal — strictest gates):**
  mark ONLY when the model string matches exactly once, exactly one box
  candidate sits in its row band, and the box is pixel-EMPTY; anything
  else is skipped and written to the build log ("model found, 2 candidate
  boxes — marked none; check by hand"). Pixel-diff regression in tests:
  the ONLY rendered change is inside the box bounds (verify.py
  discipline). Idempotent: a marked box is never re-marked.
- **Suffix/option grids** (base model + checkable option suffixes): v1
  marks the BASE model row only and reports option suffixes for hand
  checking — parsing option matrices is fabrication risk, not scope.
- **Modes:** off / report-only / mark (default report-only for the first
  build of a project, then the user flips it — proposals-first, like
  everything else).
- **Tests:** synthetic spec sheets (minipdf) with checkbox rows → exact
  mark placement + pixel containment; ambiguity refuses loudly; golden
  Swatchbook acceptance (T1–T7) unchanged with marking OFF.

---

## Suggested order and versions

| Phase | Name | Version | Depends on |
|---|---|---|---|
| 4 | the Chalk Mark (checkbox marking) — **SHIPPED** | v5.3.0 | Swatchbook (shipped) |
| 1 | the Story Pole (witnessed autoscale) — **SHIPPED** | v5.4.0 | — |
| 2 | the Reed Count (symbol counting) — **SHIPPED** | v5.5.0 | 1 (size sanity) |
| 3 | the Cut Ticket set-scan (tags → schedule) — **SHIPPED** | v5.6.0 | 2 (symbol context), Swatchbook |

Phase 4 first: it completes the cut-sheet workflow the owner is actively
using, is self-contained, and is the cheapest win. 1→2→3 build on each
other by design (scale gates symbols; symbols gate tags).

## Open questions (owner)

1. **Chalk Mark style:** X, slash, or checkmark inside the box? And is
   report-only-first-then-enable the right default?
2. **Scanned/raster sets** in v1 scope for the Story Pole / Reed Count, or
   vector-first with raster as a later stage (recommended)?
3. **Symbol-library learning:** OK that user-labeled unknown symbols join
   the library (human-gated, per-firm, like the Tracer font profiles)?
4. **Names:** the Story Pole / the Reed Count / the Chalk Mark — bless or
   rename before their phases ship (registry rows added at ship time).
