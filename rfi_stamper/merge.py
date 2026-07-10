"""PDF combine / split / rotate engine (the Shuttle).

Pure page-level surgery: no rendering, no content rewriting.  Page boxes,
annotations and other page-level resources travel with each page untouched;
rotation is applied via the page /Rotate entry only.

The PDF I/O backend is selectable by the ``PLOOM_PDF_IO`` env var: the
default is Planloom's own from-scratch reader/writer (``mini``, the
Shuttle); ``PLOOM_PDF_IO=pypdf`` opts back into the retired library as a
dev-box parity oracle (no longer a shipped dependency) — the same
retirement pattern as reportlab.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass


def _io():
    """(ReaderClass, WriterClass) for the selected PDF I/O backend."""
    if os.environ.get("PLOOM_PDF_IO", "mini").lower() == "pypdf":
        from pypdf import PdfReader, PdfWriter
        return PdfReader, PdfWriter
    from .minipdf.io import Reader, Writer
    return Reader, Writer

# re.ASCII: only 0-9 count as digits (unicode digits like "١" or "１" are
# rejected rather than silently accepted).
_TERM = re.compile(r"^(?:(\d+)|(\d+)\s*-\s*(\d+)|(\d+)\s*-|-\s*(\d+))$", re.ASCII)


@dataclass
class MergeItem:
    path: str
    pages: str = ""        # page-range spec, "" = all pages
    rotation: int = 0      # extra rotation per page: 0/90/180/270
    bookmark: str = ""     # outline title; "" -> file stem


def _open(path: str):
    """Open a reader, transparently unlocking blank-password encryption.

    Password-protected inputs otherwise fail deep in the backend when pages
    are touched; here we try the empty password (the common "owner-locked,
    no user password" case) and, failing that, raise a clean ``ValueError``
    the caller can surface to the user."""
    Reader, _ = _io()
    r = Reader(path)
    if r.is_encrypted:
        try:
            ok = r.decrypt("")          # PasswordType.NOT_DECRYPTED == 0
        except Exception:
            ok = 0
        if not ok:
            raise ValueError(
                f"{os.path.basename(path)} is password-protected; unlock it first")
    return r


def pdf_page_count(path: str) -> int:
    return len(_open(path).pages)


def parse_page_range(spec: str, n_pages: int) -> list[int]:
    """Parse a 1-based page-range spec into an ordered page list.

    Grammar: comma-separated terms; term = N | N-M | N- (to end) | -M (from 1).
    Duplicates allowed, order preserved.  "" or "all" -> every page.
    """
    s = spec.strip()
    if not s or s.lower() == "all":
        return list(range(1, n_pages + 1))
    out: list[int] = []
    for raw in s.split(","):
        term = raw.strip()
        m = _TERM.match(term)
        if not m:
            raise ValueError(f"bad page range term {term!r} (use N, N-M, N- or -M)")
        single, lo, hi, from_, to = m.groups()
        if single:
            a = b = int(single)
        elif lo:
            a, b = int(lo), int(hi)
        elif from_:
            a, b = int(from_), n_pages
        else:
            a, b = 1, int(to)
        if a < 1 or b < 1 or a > n_pages or b > n_pages:
            raise ValueError(
                f"page range {term!r} outside document (has {n_pages} page(s))")
        if a > b:
            raise ValueError(f"page range {term!r} is reversed ({a} > {b})")
        out.extend(range(a, b + 1))
    return out


def _check_rotation(rotation: int) -> int:
    if rotation % 90 != 0:
        raise ValueError(f"rotation must be a multiple of 90, got {rotation}")
    return rotation % 360


def _atomic_write(writer, out_path: str) -> None:
    """Write beside out_path, fsync, then atomically replace: a killed process
    or crash can never leave a truncated PDF at the final path."""
    # Deliver clean, reproducible bytes: the mini writer structurally cannot
    # emit /Info; the pypdf ORACLE path would stamp a /Producer, so the
    # metadata dance survives only for it (same policy as stamp.stamp_pdf —
    # an NDA posture leaks no tool names or wall-clock dates).
    try:
        writer.metadata = None
    except Exception:                        # older pypdf without the setter
        pass
    tmp = out_path + ".part"
    with open(tmp, "wb") as f:
        writer.write(f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out_path)


def merge_pdfs(items: list[MergeItem], out_path: str, bookmarks: bool = True,
               log=print) -> dict:
    """Append selected pages of each item, in order, into one PDF."""
    if not items:
        raise ValueError("no input files given")
    _, Writer = _io()
    writer = Writer()
    total = 0
    marks: list[tuple[str, int]] = []   # (title, 0-based first page in output)
    for it in items:
        rot = _check_rotation(it.rotation)
        reader = _open(it.path)
        nums = parse_page_range(it.pages, len(reader.pages))
        if not nums:
            log(f"  !! {os.path.basename(it.path)}: no pages selected, skipped")
            continue
        marks.append((it.bookmark or os.path.splitext(os.path.basename(it.path))[0],
                      total))
        for n in nums:
            page = writer.add_page(reader.pages[n - 1])
            if rot:
                page.rotate(rot)
        total += len(nums)
        log(f"  + {os.path.basename(it.path)}: {len(nums)} page(s)"
            + (f", rotated {rot}°" if rot else ""))
    if total == 0:
        raise ValueError("no pages selected across all inputs")
    if bookmarks:
        for title, first in marks:
            writer.add_outline_item(title, first)
    _atomic_write(writer, out_path)
    log(f"  wrote {out_path} ({total} pages from {len(items)} files)")
    return {"files": len(items), "pages": total, "out_path": out_path}


def split_pdf(path: str, out_dir: str, ranges: str = "", every: int = 0,
              prefix: str = "", log=print) -> list[str]:
    """Split one PDF into parts, by explicit ranges or fixed-size chunks."""
    if bool(ranges.strip()) == bool(every):
        raise ValueError("give exactly one of ranges= or every=")
    if every < 0:
        raise ValueError("every must be a positive page count")
    reader = _open(path)
    n = len(reader.pages)
    if ranges.strip():
        parts = [parse_page_range(r, n) for r in ranges.split(";") if r.strip()]
        if not parts:
            raise ValueError("ranges spec selected nothing")
    else:
        parts = [list(range(i, min(i + every, n + 1))) for i in range(1, n + 1, every)]
    _, Writer = _io()
    stem = prefix or os.path.splitext(os.path.basename(path))[0]
    os.makedirs(out_dir, exist_ok=True)
    paths: list[str] = []
    for i, nums in enumerate(parts, 1):
        writer = Writer()
        for pn in nums:
            writer.add_page(reader.pages[pn - 1])
        out = os.path.join(out_dir, f"{stem}_part{i:02d}.pdf")
        _atomic_write(writer, out)
        paths.append(out)
        log(f"  wrote {os.path.basename(out)} ({len(nums)} page(s))")
    return paths


def rotate_pdf(path: str, out_path: str, rotation: int, pages: str = "",
               log=print) -> None:
    """Rotate the listed pages (default all) by rotation degrees (multiple of 90)."""
    rot = _check_rotation(rotation)
    reader = _open(path)
    targets = set(parse_page_range(pages, len(reader.pages)))
    _, Writer = _io()
    writer = Writer()
    for i, page in enumerate(reader.pages, 1):
        added = writer.add_page(page)
        if rot and i in targets:
            added.rotate(rot)
    _atomic_write(writer, out_path)
    log(f"  wrote {out_path} ({len(targets)} page(s) rotated {rot}°)")
