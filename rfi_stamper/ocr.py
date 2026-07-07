"""Searchable OCR text layer for scanned plan-set PDFs (fully offline).

Adds an invisible, selectable text layer to image-only / scanned pages using
the locally installed Tesseract engine through PyMuPDF's built-in OCR
(`fitz.Pixmap.pdfocr_tobytes`).  No network is used or reachable: OCR runs
against the on-disk Tesseract language data only.

The input file is never mutated.  Output is written with the house atomic
pattern (write to ``out_path + ".part"``, flush + ``os.fsync``, then
``os.replace``) so a crash can never leave a truncated PDF at the final path.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field

import fitz


class OcrUnavailable(RuntimeError):
    """Raised when a usable local Tesseract engine cannot be located."""


# Minimum count of non-whitespace characters a page must expose through the
# normal text extractor before it is considered to already carry real text.
_MIN_CHARS = 12

# Directories Tesseract language data is commonly installed into.  Checked
# only as a fallback after the environment / PyMuPDF's own discovery.
_KNOWN_TESSDATA = (
    "/usr/share/tesseract-ocr/5/tessdata",
    "/usr/share/tesseract-ocr/4.00/tessdata",
    "/usr/share/tesseract-ocr/tessdata",
    "/usr/share/tessdata",
    "/usr/local/share/tessdata",
    "/opt/homebrew/share/tessdata",
)

_INSTALL_HINT = (
    "Tesseract OCR is not available. Install the Tesseract engine and the "
    "'eng' language data, then point TESSDATA_PREFIX at the folder that "
    "contains the *.traineddata files."
)

# Cache of {tessdata_dir -> bool} so the one-time engine smoke test (which
# shells out to Tesseract) is not repeated on every call.
_SMOKE_CACHE: dict[str, bool] = {}


@dataclass
class _Tessdata:
    """Best-effort description of the discovered Tesseract data folder."""

    path: str = ""
    langs: list[str] = field(default_factory=list)


def _has_traineddata(directory: str) -> bool:
    try:
        return any(name.endswith(".traineddata") for name in os.listdir(directory))
    except OSError:
        return False


def _list_langs(directory: str) -> list[str]:
    try:
        return sorted(
            name[: -len(".traineddata")]
            for name in os.listdir(directory)
            if name.endswith(".traineddata")
        )
    except OSError:
        return []


def _discover_tessdata() -> str | None:
    """Return a tessdata folder holding at least one ``*.traineddata`` file.

    Search order: ``TESSDATA_PREFIX`` (and its ``tessdata`` subfolder),
    PyMuPDF's own discovery, then the well-known install locations.  Returns
    ``None`` if nothing usable is found.
    """
    candidates: list[str] = []
    env = os.environ.get("TESSDATA_PREFIX")
    if env:
        candidates.append(env)
        candidates.append(os.path.join(env, "tessdata"))
    try:
        found = fitz.get_tessdata()
        if found:
            candidates.append(found)
    except Exception:
        pass
    candidates.extend(_KNOWN_TESSDATA)
    for cand in candidates:
        if cand and os.path.isdir(cand) and _has_traineddata(cand):
            return os.path.normpath(cand)
    return None


def _fitz_can_ocr(tessdata: str) -> bool:
    """Confirm PyMuPDF can actually run Tesseract against ``tessdata``.

    Renders a tiny pixmap and OCRs it once (result cached per folder).  Any
    failure — missing engine, unreadable language data — yields ``False``
    rather than raising.
    """
    if tessdata in _SMOKE_CACHE:
        return _SMOKE_CACHE[tessdata]
    ok = False
    try:
        doc = fitz.open()
        try:
            page = doc.new_page(width=120, height=60)
            page.insert_text((8, 38), "ok", fontsize=20)
            pix = page.get_pixmap(dpi=96)
            data = pix.pdfocr_tobytes(language="eng", tessdata=tessdata)
            ok = bool(data)
        finally:
            doc.close()
    except Exception:
        ok = False
    _SMOKE_CACHE[tessdata] = ok
    return ok


def _require_tessdata() -> str:
    """Return a usable tessdata folder or raise :class:`OcrUnavailable`.

    Also exports ``TESSDATA_PREFIX`` so the underlying engine resolves the
    same data even for code paths that ignore the explicit argument.
    """
    tessdata = _discover_tessdata()
    if not tessdata or not _fitz_can_ocr(tessdata):
        raise OcrUnavailable(_INSTALL_HINT)
    os.environ["TESSDATA_PREFIX"] = tessdata
    return tessdata


def _visible(text: str) -> str:
    """Collapse all whitespace away, leaving only the ink-bearing characters."""
    return "".join(text.split())


def _page_has_text(page, min_chars: int = _MIN_CHARS) -> bool:
    return len(_visible(page.get_text("text"))) >= min_chars


def _atomic_save_doc(doc, out_path: str) -> None:
    """Save ``doc`` beside ``out_path``, fsync, then atomically replace."""
    tmp = out_path + ".part"
    doc.save(tmp, garbage=3, deflate=True)
    fd = os.open(tmp, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, out_path)


def tesseract_available() -> bool:
    """True when a local Tesseract engine can be located AND PyMuPDF can use it.

    "Located" means either ``tesseract`` is on ``PATH`` or a known tessdata
    folder exists; "usable" means a one-shot OCR of a scratch pixmap succeeds.
    """
    tessdata = _discover_tessdata()
    located = bool(shutil.which("tesseract")) or bool(tessdata)
    if not located or not tessdata:
        return False
    return _fitz_can_ocr(tessdata)


def tesseract_info() -> dict:
    """Best-effort engine description; never raises.

    Returns ``{"available": bool, "path": str, "tessdata": str, "langs": [...]}``
    where ``path`` is the Tesseract binary (if on ``PATH``) and ``langs`` are
    the language codes with data present.
    """
    info = {"available": False, "path": "", "tessdata": "", "langs": []}
    try:
        info["path"] = shutil.which("tesseract") or ""
        tessdata = _discover_tessdata() or ""
        info["tessdata"] = tessdata
        if tessdata:
            info["langs"] = _list_langs(tessdata)
        info["available"] = tesseract_available()
    except Exception:
        pass
    return info


def needs_ocr(path: str, page_no: int | None = None, min_chars: int = 12) -> bool:
    """True when a page has essentially no extractable text (image-only/scanned).

    ``page_no`` is 1-based; when ``None`` the answer is True if ANY page in the
    document lacks real text.  A page qualifies as scanned when the normal text
    extractor yields fewer than ``min_chars`` non-whitespace characters.
    """
    with fitz.open(path) as doc:
        total = doc.page_count
        if page_no is None:
            indices = range(total)
        else:
            if page_no < 1 or page_no > total:
                raise ValueError(
                    f"page_no {page_no} outside document (has {total} page(s))")
            indices = [page_no - 1]
        for i in indices:
            if len(_visible(doc[i].get_text("text"))) < min_chars:
                return True
        return False


def ocr_pdf(path: str, out_path: str, dpi: int = 300, language: str = "eng",
            skip_text_pages: bool = True, log=print) -> dict:
    """Write a searchable copy of ``path`` to ``out_path``.

    Each page that already carries real text is copied through untouched when
    ``skip_text_pages`` is set; every other page is rendered to a pixmap at
    ``dpi`` and rebuilt as a searchable page (image + invisible OCR text).
    Page order and point-size are preserved.

    Raises :class:`OcrUnavailable` if Tesseract cannot be used.
    Returns ``{"pages_ocred": int, "pages_total": int, "out_path": str}``.
    """
    tessdata = _require_tessdata()
    src = fitz.open(path)
    try:
        total = src.page_count
        out = fitz.open()
        try:
            pages_ocred = 0
            for i in range(total):
                page = src[i]
                if skip_text_pages and _page_has_text(page):
                    out.insert_pdf(src, from_page=i, to_page=i)
                    log(f"  = page {i + 1}/{total}: has text, copied unchanged")
                    continue
                pix = page.get_pixmap(dpi=dpi)
                ocr_bytes = pix.pdfocr_tobytes(language=language, tessdata=tessdata)
                one = fitz.open("pdf", ocr_bytes)
                try:
                    out.insert_pdf(one)
                finally:
                    one.close()
                pages_ocred += 1
                log(f"  + page {i + 1}/{total}: OCR'd at {dpi} dpi")
            _atomic_save_doc(out, out_path)
        finally:
            out.close()
    finally:
        src.close()
    log(f"  wrote {out_path} ({pages_ocred}/{total} page(s) OCR'd)")
    return {"pages_ocred": pages_ocred, "pages_total": total, "out_path": out_path}


def ocr_page_text(path: str, page_no: int, dpi: int = 300,
                  language: str = "eng") -> str:
    """Return only the OCR-extracted text of one page (1-based ``page_no``).

    Raises :class:`OcrUnavailable` if Tesseract cannot be used.
    """
    tessdata = _require_tessdata()
    with fitz.open(path) as doc:
        total = doc.page_count
        if page_no < 1 or page_no > total:
            raise ValueError(
                f"page_no {page_no} outside document (has {total} page(s))")
        pix = doc[page_no - 1].get_pixmap(dpi=dpi)
    ocr_bytes = pix.pdfocr_tobytes(language=language, tessdata=tessdata)
    with fitz.open("pdf", ocr_bytes) as one:
        return one[0].get_text("text")
