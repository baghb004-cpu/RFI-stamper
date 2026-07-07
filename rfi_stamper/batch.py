"""Batch stamping: run the RFI-overlay pipeline over many plan sets at once.

Stamps a whole pile of plan-set PDFs against the *same* collection of RFI
files in a single call.  Each plan is scanned, mapped, stamped, and pixel-diff
verified independently; a failure on any one plan (unreadable file, no clear
space, verification fault) is captured and reported without aborting the rest
of the batch.  Fully offline: this module adds no I/O beyond the pipeline it
delegates to and imports no networking.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from . import pipeline
from .summarize import OfflineSummarizer

_SUFFIX = "_RFI_overlay.pdf"


@dataclass
class BatchItem:
    """Outcome of stamping one plan set.

    ``report`` holds the :class:`pipeline.Report` on success, or ``None`` when
    the plan raised before a report could be produced (``error`` is then set).
    """

    plan_path: str
    out_path: str = ""
    verify_ok: bool = False
    error: str = ""
    report: object = None       # the pipeline.Report, or None on error


def _out_path_for(plan_path: str, out_dir: str | None) -> str:
    """Destination overlay path: ``<out_dir or plan dir>/<stem>_RFI_overlay.pdf``."""
    stem = os.path.splitext(os.path.basename(plan_path))[0]
    folder = out_dir if out_dir else os.path.dirname(os.path.abspath(plan_path))
    return os.path.join(folder, stem + _SUFFIX)


def batch_stamp(plan_paths, rfi_paths, out_dir: str | None = None,
                summarizer=None, dpi: int = 90, log=print,
                progress=None) -> list[BatchItem]:
    """Stamp every plan in ``plan_paths`` against the shared ``rfi_paths``.

    Each plan is run through :func:`pipeline.run` independently and written to
    ``<out_dir or plan's dir>/<plan stem>_RFI_overlay.pdf``.  Any exception from
    a single plan is trapped and recorded in that plan's :class:`BatchItem`
    (``error`` set, ``report`` left ``None``); the batch always continues.

    ``progress``, if given, is called ``progress(i, n, plan_path)`` with a
    zero-based index ``i`` before each plan is processed.  When ``summarizer``
    is ``None`` a fresh :class:`~rfi_stamper.summarize.OfflineSummarizer` is
    used.  Returns one :class:`BatchItem` per plan, in the input order.
    """
    if summarizer is None:
        summarizer = OfflineSummarizer()

    plans = list(plan_paths)
    n = len(plans)
    items: list[BatchItem] = []

    for i, plan_path in enumerate(plans):
        if progress is not None:
            try:
                progress(i, n, plan_path)
            except Exception:                       # noqa: BLE001 -- never abort on a UI callback
                pass

        out_path = _out_path_for(plan_path, out_dir)
        item = BatchItem(plan_path=plan_path, out_path=out_path)
        try:
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
            report = pipeline.run(plan_path, rfi_paths=rfi_paths,
                                  out_path=out_path, summarizer=summarizer,
                                  dpi=dpi, log=log)
            item.report = report
            item.out_path = report.out_path or out_path
            item.verify_ok = bool(report.verify_ok)
            if not item.verify_ok:
                item.error = "verification failed"
        except Exception as e:                      # noqa: BLE001 -- one bad plan must not sink the batch
            item.error = f"{type(e).__name__}: {e}"
            item.verify_ok = False
            item.report = None
            log(f"  !! {os.path.basename(plan_path)}: {item.error}")
        items.append(item)

    return items


def batch_summary(items: list[BatchItem]) -> dict:
    """Tally a batch: total plans, how many produced a report (passed), how
    many failed, and how many verified clean (``verify_ok``)."""
    total = len(items)
    passed = sum(1 for it in items if not it.error)
    verified = sum(1 for it in items if it.verify_ok)
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "verified": verified,
    }
