"""The Batterboard: the one-click layout package (BATTERBOARD_PLAN.md).

A layout person drops a plan set, picks a hat, flips a few switches and
presses one button.  Out come field-ready plates, point files, an open
items list and a plain report of what was checked.  Nothing prints that
was not checked; a blocked run writes nothing to the deliverables folder.

Stages: Intake, Sightline, Read, Cross-check, Register, Points, Plates,
Self-check, Deliver.  GUI-free; the tab and the CLI twin both call
:func:`rfi_stamper.batterboard.run.run`.

Fully offline: PyMuPDF + numpy + stdlib only.
"""
