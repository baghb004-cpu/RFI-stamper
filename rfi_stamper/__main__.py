"""RFI Stamper CLI.  No arguments -> GUI.  Subcommands for scripting:

  rfi-stamper stamp -p plans.pdf -r rfi_folder -o out.pdf
  rfi-stamper stamp -p plans.pdf -r a.pdf b.pdf --scan-only mapping.csv
  rfi-stamper stamp -p plans.pdf -r rfi_folder --map mapping.csv
  rfi-stamper merge a.pdf b.pdf c.pdf -o combined.pdf
  rfi-stamper split big.pdf --every 1 -d out_dir
  rfi-stamper split big.pdf --ranges "1-3; 4-10; 11-" -d out_dir
  rfi-stamper compare old.pdf new.pdf -o overlay.pdf
  rfi-stamper gui

The legacy flag style (rfi-stamper -p plans.pdf -r rfis/) still works and
means `stamp`.  Everything runs fully offline.
"""
from __future__ import annotations

import argparse
import os
import sys

SUBCOMMANDS = {"stamp", "merge", "split", "compare", "gui"}


def build_stamp(sub):
    ap = sub.add_parser("stamp", help="overlay RFI cliff notes onto a plan set")
    ap.add_argument("-p", "--plans", required=True, help="plan set PDF")
    ap.add_argument("-r", "--rfis", nargs="+", required=True,
                    help="RFI files and/or folders")
    ap.add_argument("-o", "--out", help="output PDF (default: <plans>_RFI_overlay.pdf)")
    ap.add_argument("--scan-only", metavar="CSV",
                    help="only detect sheets + map RFIs; write mapping CSV and exit")
    ap.add_argument("--map", metavar="CSV", help="use an edited mapping CSV")
    ap.add_argument("--dpi", type=int, default=90,
                    help="analysis render DPI (default 90)")


def build_merge(sub):
    ap = sub.add_parser("merge", help="combine PDFs into one")
    ap.add_argument("files", nargs="+", help="input PDFs, in order")
    ap.add_argument("-o", "--out", required=True, help="output PDF")
    ap.add_argument("--pages", nargs="*", default=[],
                    help="per-file page ranges, aligned with inputs "
                         "(use 'all' to skip one), e.g. --pages 1-3 all 2-")
    ap.add_argument("--no-bookmarks", action="store_true",
                    help="skip the per-file outline entries")


def build_split(sub):
    ap = sub.add_parser("split", help="split a PDF into pieces")
    ap.add_argument("file", help="input PDF")
    ap.add_argument("-d", "--dir", default=".", help="output folder")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--every", type=int, help="chunk size in pages (1 = per page)")
    g.add_argument("--ranges", help="semicolon-separated ranges, one file each")


def build_compare(sub):
    ap = sub.add_parser("compare", help="auto-align two revisions and write a "
                                        "color overlay PDF")
    ap.add_argument("base", help="old revision PDF")
    ap.add_argument("overlay", help="new revision PDF")
    ap.add_argument("-o", "--out", help="output PDF (default: <base>_compare.pdf)")
    ap.add_argument("--pages", nargs=2, type=int, default=(1, 1),
                    metavar=("BASE_PG", "OVERLAY_PG"))
    ap.add_argument("--no-align", action="store_true",
                    help="overlay as-is, skip auto-registration")
    ap.add_argument("--no-rotation", action="store_true",
                    help="translation-only alignment (faster)")


def build_parser():
    ap = argparse.ArgumentParser(
        prog="rfi-stamper",
        description="Offline plan toolkit: stamp RFI notes, combine/split PDFs, "
                    "compare revisions. No network access, ever.")
    sub = ap.add_subparsers(dest="cmd")
    build_stamp(sub)
    build_merge(sub)
    build_split(sub)
    build_compare(sub)
    sub.add_parser("gui", help="launch the desktop app (default with no args)")
    return ap


def cmd_stamp(args) -> int:
    from . import pipeline
    from .summarize import OfflineSummarizer
    index, rows = pipeline.scan(args.plans, args.rfis)
    if args.scan_only:
        pipeline.rows_to_csv(index, rows, args.scan_only)
        print(f"mapping written to {args.scan_only} — edit the 'sheets' column, "
              "then re-run with --map")
        return 0
    if args.map:
        pipeline.apply_csv(index, rows, args.map)
    rep = pipeline.run(args.plans, out_path=args.out, rows=rows, index=index,
                       summarizer=OfflineSummarizer(), dpi=args.dpi)
    return 0 if rep.verify_ok else 1


def cmd_merge(args) -> int:
    from . import merge
    items = []
    for i, f in enumerate(args.files):
        spec = args.pages[i] if i < len(args.pages) else ""
        items.append(merge.MergeItem(
            path=f, pages="" if spec.lower() in ("", "all") else spec,
            bookmark=os.path.splitext(os.path.basename(f))[0]))
    res = merge.merge_pdfs(items, args.out, bookmarks=not args.no_bookmarks)
    print(f"combined {res['files']} file(s) -> {res['pages']} pages: "
          f"{res['out_path']}")
    return 0


def cmd_split(args) -> int:
    from . import merge
    out = merge.split_pdf(args.file, args.dir, ranges=args.ranges or "",
                          every=args.every or 0)
    for p in out:
        print(p)
    return 0


def cmd_compare(args) -> int:
    from . import align
    res = None
    if not args.no_align:
        res = align.auto_align(args.base, args.overlay,
                               base_page=args.pages[0],
                               overlay_page=args.pages[1],
                               try_rotation=not args.no_rotation)
        print(f"aligned: dx {res.dx:+.1f}pt dy {res.dy:+.1f}pt "
              f"rot {res.rotation:+.2f}° confidence {res.score:.2f}")
    out = args.out or os.path.splitext(args.base)[0] + "_compare.pdf"
    align.make_comparison_pdf(args.base, args.overlay, out,
                              base_page=args.pages[0],
                              overlay_page=args.pages[1], align=res)
    print(f"overlay written: {out}")
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0] == "gui" or "--gui" in argv:
        from . import gui
        gui.main()
        return 0
    if argv[0] not in SUBCOMMANDS and argv[0] not in ("-h", "--help"):
        # legacy flag style: rfi-stamper -p plans.pdf -r rfis/ [...]
        argv = ["stamp"] + argv
    args = build_parser().parse_args(argv)
    return {"stamp": cmd_stamp, "merge": cmd_merge,
            "split": cmd_split, "compare": cmd_compare}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
