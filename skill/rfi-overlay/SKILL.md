---
name: rfi-overlay
description: Stamp RFI cliff-note summaries onto the matching sheets of a construction plan-set PDF (any trade - plumbing, mechanical, electrical, architectural). Use this skill whenever the user wants to overlay, stamp, annotate, or mark up drawings/plans/sheets with RFI notes, summaries, or responses; mentions "RFI overlay", "stamp RFIs", "put RFI notes on my drawings", or "cliff notes on the sheets"; or hands over a plan set plus a batch of RFI files and wants them cross-referenced onto the drawings. Also use it when they ask to map RFIs to sheets, even without stamping. Handles ordinary RFI PDFs, multi-RFI PDFs, zip-style document-controls export packages, and raw text dumps.
---

# RFI Overlay

Stamps compact red note boxes ("RFI ### — SUBJECT" header + 1–2 line question/
answer body) into measured-empty white space on the sheets each RFI references.
The engine guarantees the hard parts; Claude supplies the judgment.

## Engine location

This skill lives inside the RFI Stamper repository at
`<repo>/skill/rfi-overlay/`; the engine is the `rfi_stamper` package two
directories up. Bootstrap:

```python
import os, sys
repo = os.path.abspath(os.path.join(os.path.dirname(SKILL_DIR), "..", ".."))
sys.path.insert(0, repo)          # SKILL_DIR = this skill's directory
from rfi_stamper import pipeline
```

Install deps if missing:
`pip install pymupdf pypdf numpy --break-system-packages`.

## Division of labor

The scripts (deterministic, verified): sheet-number detection from title
blocks, RFI parsing, reference extraction, empty-rectangle placement that never
covers linework, rotation-correct stamping, and a pixel-diff verification pass
over every page. Claude (judgment): reading the RFIs properly, writing tight
cliff notes, reviewing the sheet mapping with the user, and deciding what to do
with unmatched RFIs.

## Workflow

1. **Verify inputs on disk first** (`ls` the upload dir). Plan set = one PDF;
   RFIs = files or a folder.

2. **Scan and map**:

   ```python
   index, rows = pipeline.scan(plan_pdf, [rfi_folder_or_files])
   ```

   `index.pages` gives page→sheet detection — sanity-check it against the
   drawing index or ask the user if any page came back as `PAGE-n`
   (scanned/no-text sheets need OCR or manual numbers). Each row has
   `.record` (number, title, question, answer, has_answer, refs) and
   `.pages`/`.via` (`planref` = labeled reference, high confidence; `body` =
   token found in the text, glance at it; `unmatched` = no sheet found).

3. **Review the mapping with the user** before stamping: show a compact table
   of RFI → sheets → answered, flag `body` matches and unmatched RFIs, and ask
   about anything Claude is unsure of after reading the RFI text (Claude
   should actually read `record.question`/`record.answer` — cheap and catches
   parser misses). Edit `row.pages` directly (page numbers via
   `index.match("P-1.02")`) per the user's calls.

4. **Write the cliff notes yourself** — this is the quality edge over the
   standalone tool. Read each RFI and write one ≤240-char note in the form
   `Q: <question essence> A: <answer/direction>` (or ending `Resp: not in
   file.` when unanswered). Supply them through the summarizer hook; anything
   omitted falls back to the offline extractive summarizer:

   ```python
   class Notes:
       def __init__(self, d): self.d = d          # rfi number -> note text
       def summarize(self, rec): return self.d.get(rec.number)

   rep = pipeline.run(plan_pdf, out_path=out_pdf, rows=rows, index=index,
                      summarizer=Notes({"044": "Q: ... A: ..."}))
   ```

5. **Trust the verifier, and say so.** `rep.verify_ok` must be True before
   delivering — it means the only rendered change on every stamped page is
   the note box, nothing pre-existing sat under any box, and untouched pages
   are pixel-identical. If it fails, do not deliver; inspect the
   `*_report.txt` written next to the output. Unplaceable notes and unmatched
   RFIs land on a labeled appendix page at the end — tell the user what's on
   it.

6. If the user wants a style proof first, run once with `rows` filtered to a
   single sheet's RFIs, get sign-off, then run the full set.

## Notes

- Cross-trade refs (an A- or C- sheet cited in a plumbing RFI) correctly
  won't match — offer to stamp that trade's set too if the user has it.
- Answered copies of earlier RFIs inside later packages auto-backfill the
  earlier record's answer; still spot-check `has_answer` against the log.
- Privacy: the engine is fully offline (no network calls); keep it that way
  when driving it — do not add cloud summarization on NDA-covered documents.
- The standalone GUI/exe version of this engine lives in the repository root
  (`build_windows.bat` builds it on Windows).
