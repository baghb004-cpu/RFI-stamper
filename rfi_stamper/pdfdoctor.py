"""Offline PDF repair / optimization engine (fitz + the Shuttle).

Diagnose the problems that plague plan-set PDFs and apply safe, fully offline
fixes: decrypt owner-locked files, rebuild damaged cross-reference tables,
downsample oversized raster images, strip identifying metadata, remove
embedded JavaScript / attachments, bake page rotation into content, flatten
annotations, and rasterize / re-render pages.

No file is ever mutated in place.  Every write goes to ``out_path + ".part"``,
is flushed and ``os.fsync``-ed, then atomically ``os.replace``-d onto the
destination, so a killed process or crash can never leave a truncated PDF at
the final path.

Fully offline by policy: no sockets, no telemetry, no external services -- the
documents handled here are routinely NDA-covered.  OCR is intentionally *not*
implemented here; it lives in a sibling module.  The ``ocr`` fix key produced
by :func:`diagnose` is a routing hint for that module, not a call into it.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

import fitz

from .minipdf.parse import read_pdf as _read_pdf


# ------------------------------------------------------------- thresholds ---

OVERSIZE_BYTES = 8 * 1024 * 1024                # flag files larger than 8 MB
HEAVY_DPI = 150                                 # image "heavy" reference dpi
IMG_HEAVY_DPI = HEAVY_DPI * 1.25               # flag images above ~188 dpi
_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

# a JavaScript key whose value was neutralized to null is dead; ignore it so
# a scrubbed file no longer reports as carrying JavaScript.
_DEAD_JS = re.compile(r"/JavaScript\s*null\b")


@dataclass
class Issue:
    key: str          # encrypted|broken|oversize|no_text|rotation|javascript|
                      # embedded_files|metadata|annotations|forms|linearize
    severity: str     # "high" | "medium" | "low"
    title: str
    detail: str
    fixable: bool
    fix: str          # name of the fix fn to call ("unlock", "repair", ...)


# ----------------------------------------------------------- write helpers ---

def _fsync_path(path: str) -> None:
    """Force the just-written file's bytes to stable storage."""
    with open(path, "rb+") as f:
        f.flush()
        os.fsync(f.fileno())


def _atomic_save(doc, out_path: str, **opts) -> None:
    """Save ``doc`` beside out_path, fsync, then atomically replace.  A crash
    can never leave a half-written PDF at the final path; the input file is
    untouched because we always target a distinct ``out_path``."""
    tmp = out_path + ".part"
    try:
        doc.save(tmp, **opts)
        _fsync_path(tmp)
        os.replace(tmp, out_path)
    except Exception:
        _quiet_unlink(tmp)
        raise


def _atomic_copy(src_path: str, out_path: str) -> None:
    """Copy an already-written PDF onto out_path atomically."""
    tmp = out_path + ".part"
    try:
        with open(src_path, "rb") as r, open(tmp, "wb") as w:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                w.write(chunk)
            w.flush()
            os.fsync(w.fileno())
        os.replace(tmp, out_path)
    except Exception:
        _quiet_unlink(tmp)
        raise


def _quiet_unlink(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _open(path: str, log=print):
    """Open tolerantly and best-effort authenticate an empty password.
    Returns the document (fitz auto-repairs a damaged file on open)."""
    doc = fitz.open(path)
    if doc.needs_pass:
        doc.authenticate("")
    return doc


# ---------------------------------------------------------------- probes ---

def _rotations(doc) -> list[int]:
    out = []
    for pno in range(len(doc)):
        try:
            out.append(int(doc[pno].rotation) % 360)
        except Exception:
            out.append(0)
    return out


def _heavy_images(doc) -> int:
    """Count images whose stored resolution well exceeds ~150 dpi as placed."""
    n = 0
    for pno in range(len(doc)):
        page = doc[pno]
        try:
            infos = page.get_image_info(xrefs=True)
        except Exception:
            continue
        for info in infos:
            bb = fitz.Rect(info.get("bbox", (0, 0, 0, 0)))
            w_in = bb.width / 72.0
            px = int(info.get("width", 0) or 0)
            if w_in > 0.1 and px and px / w_in > IMG_HEAVY_DPI:
                n += 1
    return n


def _image_only_pages(doc) -> int:
    """Pages carrying images but no extractable text (scanned / flattened)."""
    n = 0
    for pno in range(len(doc)):
        page = doc[pno]
        try:
            txt = page.get_text("text").strip()
        except Exception:
            txt = ""
        if txt:
            continue
        try:
            if page.get_images(full=False):
                n += 1
        except Exception:
            pass
    return n


def _has_javascript(doc) -> bool:
    """True if any object carries a live JavaScript action or name tree.  The
    token ``/JavaScript`` appears both in ``/S /JavaScript`` actions and in the
    ``/Names /JavaScript`` tree; matching it alone avoids the false positives a
    bare ``/JS`` scan would hit.  Dead keys neutralized to ``null`` (by
    :func:`remove_javascript`) are ignored."""
    try:
        n = doc.xref_length()
    except Exception:
        return False
    for xref in range(1, n):
        try:
            obj = doc.xref_object(xref, compressed=True)
        except Exception:
            continue
        if "/JavaScript" in _DEAD_JS.sub("", obj):
            return True
    return False


def _metadata_fields(doc) -> list[str]:
    fields = []
    meta = doc.metadata or {}
    for k in ("title", "author", "subject", "keywords", "creator", "producer"):
        if meta.get(k):
            fields.append(k)
    try:
        if doc.get_xml_metadata():
            fields.append("xmp")
    except Exception:
        pass
    return fields


def _widget_count(doc) -> int:
    n = 0
    for pno in range(len(doc)):
        try:
            for _ in doc[pno].widgets():
                n += 1
        except Exception:
            pass
    return n


def _annot_count(doc) -> int:
    n = 0
    for pno in range(len(doc)):
        try:
            for _ in doc[pno].annots():
                n += 1
        except Exception:
            pass
    return n


def _embedded_count(doc) -> int:
    try:
        return int(doc.embfile_count())
    except Exception:
        return 0


def _is_linearized(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return b"/Linearized" in f.read(4096)
    except Exception:
        return False


# ------------------------------------------------------------- diagnosis ---

def is_encrypted(path: str) -> bool:
    """True if the file has any encryption applied -- including owner-only
    locks that carry an empty user password (fitz silently opens those, so we
    cross-check the trailer with the Shuttle's own reader -- the trailer's
    /Encrypt never lies)."""
    try:
        if _read_pdf(path).is_encrypted:
            return True
    except Exception:
        pass
    try:
        doc = fitz.open(path)
    except Exception:
        return False
    try:
        if doc.needs_pass or bool(getattr(doc, "is_encrypted", False)):
            return True
        return bool((doc.metadata or {}).get("encryption"))
    finally:
        doc.close()


def diagnose(path: str, log=print) -> list[Issue]:
    """Inspect ``path`` and return the issues found, sorted high -> low.

    Never raises: a file that only opens in repair mode -- or not at all --
    comes back as a ``broken`` issue rather than an exception."""
    issues: list[Issue] = []
    try:
        doc = fitz.open(path)
    except Exception as e:
        return [Issue("broken", "high", "Cannot open PDF",
                      f"File could not be opened even in repair mode: {e}",
                      False, "")]
    try:
        if getattr(doc, "is_repaired", False):
            issues.append(Issue(
                "broken", "high", "Damaged structure",
                "The cross-reference table or object structure is broken; the "
                "file opened only in repair mode.", True, "repair"))

        if is_encrypted(path):
            needs = bool(doc.needs_pass)
            issues.append(Issue(
                "encrypted", "high", "Encrypted / permission-locked",
                ("Requires a password to open." if needs else
                 "Owner-locked (restricts printing/copy/edit) with no open "
                 "password."), True, "unlock"))

        size = os.path.getsize(path)
        heavy = _heavy_images(doc)
        if size > OVERSIZE_BYTES or heavy:
            detail = f"File is {size / 1e6:.1f} MB."
            if heavy:
                detail += (f" {heavy} image(s) exceed ~{HEAVY_DPI} dpi effective "
                           "resolution and can be downsampled.")
            issues.append(Issue("oversize", "medium", "Oversized / heavy images",
                                detail, True, "compress"))

        img_only = _image_only_pages(doc)
        if img_only:
            issues.append(Issue(
                "no_text", "medium", "No searchable text",
                f"{img_only} page(s) are image-only with no extractable text "
                "(scanned?). OCR would make them searchable.", True, "ocr"))

        rots = _rotations(doc)
        nonzero = [r for r in rots if r % 360 != 0]
        if nonzero:
            detail = (f"{len(nonzero)} of {len(rots)} page(s) carry a /Rotate "
                      "flag")
            if len(set(rots)) > 1:
                detail += " (mixed orientations)"
            detail += ". Some viewers mishandle /Rotate."
            issues.append(Issue("rotation", "low", "Nonzero page rotation",
                                detail, True, "normalize_rotation"))

        if _has_javascript(doc):
            issues.append(Issue(
                "javascript", "high", "Embedded JavaScript",
                "The document contains JavaScript actions -- a common vector "
                "for auto-run behavior.", True, "remove_javascript"))

        n_emb = _embedded_count(doc)
        if n_emb:
            issues.append(Issue(
                "embedded_files", "medium", "Embedded attachments",
                f"{n_emb} embedded file(s)/attachment(s) travel inside this "
                "PDF.", True, "remove_embedded_files"))

        md = _metadata_fields(doc)
        if md:
            issues.append(Issue(
                "metadata", "low", "Identifying metadata",
                "Document carries " + ", ".join(md) + ".", True,
                "strip_metadata"))

        n_forms = _widget_count(doc)
        if n_forms:
            issues.append(Issue(
                "forms", "low", "Interactive form fields",
                f"{n_forms} interactive form field(s); flatten to bake current "
                "values into the page.", True, "flatten_annotations"))

        n_annot = _annot_count(doc)
        if n_annot:
            issues.append(Issue(
                "annotations", "low", "Annotations present",
                f"{n_annot} annotation(s) (comments / markups) present.", True,
                "flatten_annotations"))

        if not _is_linearized(path):
            issues.append(Issue(
                "linearize", "low", "Not linearized",
                "Not linearized (no fast web view); can be optimized for "
                "streaming.", True, "linearize"))
    finally:
        doc.close()

    issues.sort(key=lambda it: _SEVERITY_ORDER.get(it.severity, 3))
    for it in issues:
        log(f"  [{it.severity:6}] {it.key}: {it.title}")
    return issues


# ----------------------------------------------------------------- fixes ---

def unlock(path: str, out_path: str, password: str = "", log=print) -> bool:
    """Write a decrypted copy of ``path`` to ``out_path``.

    Tries the empty owner/user password first (owner-locked files with no open
    password are the common case), then the supplied ``password``.  Returns
    True if an unlocked copy was written, False if the file could not be opened
    even with the password."""
    try:
        doc = fitz.open(path)
    except Exception as e:
        log(f"  unlock: cannot open ({e})")
        return False
    try:
        if doc.needs_pass:
            if not (doc.authenticate("") or (password and doc.authenticate(password))):
                log("  unlock: authentication failed")
                return False
        _atomic_save(doc, out_path, encryption=fitz.PDF_ENCRYPT_NONE,
                     garbage=4, deflate=True)
        log(f"  unlock: wrote decrypted copy -> {out_path}")
        return True
    finally:
        doc.close()


def repair(path: str, out_path: str, log=print) -> dict:
    """Rebuild the xref / clean the object structure (garbage=4, deflate)."""
    doc = _open(path, log)
    try:
        was = bool(getattr(doc, "is_repaired", False))
        _atomic_save(doc, out_path, clean=True, garbage=4, deflate=True)
    finally:
        doc.close()
    note = "rebuilt xref and cleaned object structure"
    if was:
        note += " (file was auto-repaired on open)"
    log(f"  repair: {note}")
    return {"out_path": out_path, "note": note}


def compress(path: str, out_path: str, image_dpi: int = 150,
             deflate: bool = True, log=print) -> dict:
    """Downsample overlarge raster images to ~``image_dpi`` and recompress.

    Only image XObjects are touched (via ``replace_image`` keyed by xref);
    vector/text content is left byte-for-byte intact."""
    before = os.path.getsize(path)
    doc = _open(path, log)
    try:
        biggest: dict[int, tuple[float, float]] = {}
        for pno in range(len(doc)):
            try:
                infos = doc[pno].get_image_info(xrefs=True)
            except Exception:
                continue
            for info in infos:
                xref = int(info.get("xref", 0) or 0)
                if not xref:
                    continue
                bb = fitz.Rect(info.get("bbox", (0, 0, 0, 0)))
                w, h = biggest.get(xref, (0.0, 0.0))
                biggest[xref] = (max(w, bb.width), max(h, bb.height))

        changed = 0
        for xref, (wpt, hpt) in biggest.items():
            if wpt <= 0 or hpt <= 0:
                continue
            try:
                pix = fitz.Pixmap(doc, xref)
            except Exception:
                continue
            need_w = max(1, int(round(wpt / 72.0 * image_dpi)))
            need_h = max(1, int(round(hpt / 72.0 * image_dpi)))
            if pix.width <= need_w * 1.10:      # already at/under target
                continue
            try:
                if pix.alpha:
                    pix = fitz.Pixmap(pix, 0)   # drop alpha before rescale
                scaled = fitz.Pixmap(pix, need_w, need_h, None)
            except Exception:
                continue
            for pno in range(len(doc)):         # replace_image is global by
                try:                            # xref; any owning page works
                    doc[pno].replace_image(xref, pixmap=scaled)
                    changed += 1
                    break
                except Exception:
                    continue

        _atomic_save(doc, out_path, garbage=4, deflate=bool(deflate),
                     deflate_images=True)
    finally:
        doc.close()
    after = os.path.getsize(out_path)
    ratio = (after / before) if before else 1.0
    log(f"  compress: {before} -> {after} bytes ({ratio:.2f}), "
        f"{changed} image(s) downsampled to ~{image_dpi} dpi")
    return {"before": before, "after": after, "ratio": ratio,
            "out_path": out_path}


def flatten_annotations(path: str, out_path: str, log=print) -> dict:
    """Bake annotations and form widgets into the page content stream."""
    doc = _open(path, log)
    try:
        if hasattr(doc, "bake"):
            try:
                doc.bake(annots=True, widgets=True)
                note = "baked annotations and widgets into page content"
            except Exception as e:
                note = f"bake failed ({e}); saved without flattening"
        else:
            note = "bake() unavailable in this build; saved without flattening"
        _atomic_save(doc, out_path, garbage=4, deflate=True)
    finally:
        doc.close()
    log(f"  flatten_annotations: {note}")
    return {"out_path": out_path, "note": note}


def _render_pdf(path: str, out_path: str, dpi: int, log) -> int:
    """Render every page (as displayed, honoring /Rotate) to an image and
    rebuild a PDF whose pages are those images at the SAME point size."""
    src = _open(path, log)
    out = fitz.open()
    try:
        for pno in range(len(src)):
            page = src[pno]
            rect = page.rect                    # viewer size (rotation-aware)
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            npage = out.new_page(width=rect.width, height=rect.height)
            npage.insert_image(npage.rect, pixmap=pix)
        pages = len(out)
        _atomic_save(out, out_path, garbage=4, deflate=True)
    finally:
        out.close()
        src.close()
    return pages


def rasterize(path: str, out_path: str, dpi: int = 200, log=print) -> dict:
    """Flatten to image / reverse-OCR: replace every page with its render at
    ``dpi``, preserving page point-size.  Removes all live text, vectors,
    JavaScript, forms and annotations in one pass."""
    pages = _render_pdf(path, out_path, dpi, log)
    log(f"  rasterize: {pages} page(s) at {dpi} dpi -> {out_path}")
    return {"out_path": out_path, "dpi": dpi, "pages": pages}


def upscale(path: str, out_path: str, dpi: int = 300, log=print) -> dict:
    """Produce a higher-resolution raster copy (render at ``dpi``, re-embed)."""
    pages = _render_pdf(path, out_path, dpi, log)
    log(f"  upscale: {pages} page(s) re-rendered at {dpi} dpi -> {out_path}")
    return {"out_path": out_path, "dpi": dpi, "pages": pages}


def linearize(path: str, out_path: str, log=print) -> dict:
    """Linearize (fast web view).  Some MuPDF builds have dropped writing
    linearized PDFs; if so, fall back to a clean optimized save and say so."""
    doc = _open(path, log)
    try:
        note = "linearized (fast web view)"
        try:
            _atomic_save(doc, out_path, linear=True, garbage=4, deflate=True)
        except Exception as e:
            note = (f"linearization unsupported by this build ({e}); "
                    "wrote a clean optimized copy instead")
            _atomic_save(doc, out_path, garbage=4, deflate=True)
    finally:
        doc.close()
    log(f"  linearize: {note}")
    return {"out_path": out_path, "note": note}


def strip_metadata(path: str, out_path: str, log=print) -> dict:
    """Remove the document info dict and XMP packet (author, producer,
    keywords, creation tool, ...) -- NDA hygiene."""
    doc = _open(path, log)
    try:
        removed = _metadata_fields(doc)
        doc.set_metadata({})                    # empty dict clears info dict
        try:
            if doc.get_xml_metadata():
                doc.del_xml_metadata()
        except Exception:
            pass
        _atomic_save(doc, out_path, garbage=4, deflate=True)
    finally:
        doc.close()
    log(f"  strip_metadata: removed {removed or 'nothing'}")
    return {"out_path": out_path, "removed": removed}


def remove_javascript(path: str, out_path: str, log=print) -> dict:
    """Strip embedded JavaScript actions / name trees.

    ``scrub`` neutralizes the action code; we then null the catalog / page
    action hooks (``/OpenAction``, ``/AA``) and the ``/Names /JavaScript`` name
    tree so the now-orphaned action objects are dropped by ``garbage=4``,
    leaving no live JavaScript.  Embedded files under ``/Names`` are kept."""
    doc = _open(path, log)
    try:
        had = _has_javascript(doc)
        try:
            doc.scrub(attached_files=False, clean_pages=False,
                      embedded_files=False, hidden_text=False, javascript=True,
                      metadata=False, redactions=False, remove_links=False,
                      reset_fields=False, reset_responses=False,
                      thumbnails=False, xml_metadata=False)
        except Exception as e:
            log(f"  remove_javascript: scrub failed ({e})")
        try:
            cat = doc.pdf_catalog()
            for key in ("OpenAction", "AA"):
                v = doc.xref_get_key(cat, key)
                if v and v[0] != "null":
                    doc.xref_set_key(cat, key, "null")
            nk = doc.xref_get_key(cat, "Names")
            if nk and nk[0] == "xref":
                nx = int(nk[1].split()[0])
                if "JavaScript" in doc.xref_get_keys(nx):
                    doc.xref_set_key(nx, "JavaScript", "null")
            for pno in range(len(doc)):
                pr = doc[pno].xref
                if "AA" in doc.xref_get_keys(pr):
                    doc.xref_set_key(pr, "AA", "null")
        except Exception as e:
            log(f"  remove_javascript: hook cleanup skipped ({e})")
        _atomic_save(doc, out_path, garbage=4, deflate=True, clean=True)
    finally:
        doc.close()
    log(f"  remove_javascript: {'removed JavaScript' if had else 'none present'}")
    return {"out_path": out_path, "removed": had}


def remove_embedded_files(path: str, out_path: str, log=print) -> dict:
    """Delete every embedded file / attachment."""
    doc = _open(path, log)
    try:
        try:
            names = list(doc.embfile_names())
        except Exception:
            names = []
        for nm in names:
            try:
                doc.embfile_del(nm)
            except Exception:
                pass
        _atomic_save(doc, out_path, garbage=4, deflate=True)
    finally:
        doc.close()
    log(f"  remove_embedded_files: removed {names or 'nothing'}")
    return {"out_path": out_path, "removed": names}


def normalize_rotation(path: str, out_path: str, log=print) -> dict:
    """Bake any /Rotate into page content so every page has rotation 0 and
    looks identical in every viewer.  Vector content is preserved (each page
    is placed as a Form XObject, not rasterized).

    ``show_pdf_page`` copies a page's ``/Contents`` but NOT its ``/Annots``, so
    any annotations or form widgets (a reviewer's markup, a filled field) would
    be silently dropped.  We first ``bake`` those appearances into the content
    stream -- still as vector Form XObjects, not rasterized -- so nothing
    visible is lost when the rotation is normalized.

    Navigation ``/Link`` annotations have no appearance (bake skips them), so we
    re-attach them to the new upright page explicitly, mapping each link rect
    from unrotated media space back to viewer space via ``derotation_matrix``
    (the new page is rotation 0, so its viewer space == the old viewer space).
    This keeps auto-hyperlinked sheet jumps working after normalization."""
    src = _open(path, log)
    # capture links before bake/rebuild (bake can drop them)
    links_per_page = []
    for pno in range(len(src)):
        page = src[pno]
        try:
            derot = page.derotation_matrix
            links_per_page.append([
                {**lk, "from": fitz.Rect(lk["from"]) * derot}
                for lk in page.get_links()])
        except Exception:
            links_per_page.append([])
    if hasattr(src, "bake"):
        try:
            src.bake(annots=True, widgets=True)  # fold markups into content so
        except Exception as e:                   # show_pdf_page carries them
            log(f"  normalize_rotation: bake skipped ({e})")
    out = fitz.open()
    try:
        rotated = 0
        for pno in range(len(src)):
            page = src[pno]
            if page.rotation % 360:
                rotated += 1
            rect = page.rect                    # already the displayed size
            npage = out.new_page(width=rect.width, height=rect.height)
            npage.show_pdf_page(npage.rect, src, pno)
        # re-attach links in a second pass, once EVERY page exists — a GoTo
        # link to a not-yet-created page raises "bad page number", so this
        # can't be done inside the build loop
        kept_links = 0
        for pno, links in enumerate(links_per_page):
            for lk in links:
                try:
                    out[pno].insert_link(lk)
                    kept_links += 1
                except Exception:
                    pass
        pages = len(out)
        _atomic_save(out, out_path, garbage=4, deflate=True)
    finally:
        out.close()
        src.close()
    log(f"  normalize_rotation: baked rotation on {rotated} page(s)"
        + (f", preserved {kept_links} link(s)" if kept_links else ""))
    return {"out_path": out_path, "normalized": rotated, "pages": pages}


# --------------------------------------------------------------- pipeline ---

def verify_safe(orig_path: str, fixed_path: str, sample_pages=None,
                dpi: int = 50) -> tuple[bool, str]:
    """Guard a fix against damage.

    Checks that the page count is preserved and that no sampled page LOST its
    content: a page that rendered non-blank in the original must still render
    non-blank in the fixed copy.  A legitimately-blank page (spacer, blank
    middle sheet) that was blank in the original stays acceptable -- only a
    non-blank -> blank transition is a failure.  (Rasterized output keeps the
    same page count, so the count check holds for it too; only text is
    intentionally lost, which this does not require.)  Returns
    ``(ok, message)``."""
    try:
        o = fitz.open(orig_path)
    except Exception as e:
        return False, f"cannot open original: {e}"
    try:
        f = fitz.open(fixed_path)
    except Exception as e:
        o.close()
        return False, f"cannot open fixed copy: {e}"
    try:
        if o.needs_pass:
            o.authenticate("")
        if f.needs_pass:
            f.authenticate("")
        n_o, n_f = len(o), len(f)
        if n_f == 0:
            return False, "fixed copy has no pages"
        if n_f != n_o:
            return False, f"page count changed {n_o} -> {n_f}"
        if sample_pages is None:
            idx = sorted({0, n_f // 2, n_f - 1})
        else:
            idx = [p for p in sample_pages if 0 <= p < n_f]
        for p in idx:
            try:
                pix_o = o[p].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY,
                                        alpha=False)
                pix_f = f[p].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY,
                                        alpha=False)
            except Exception as e:
                return False, f"page {p + 1} did not render: {e}"
            s_o, s_f = pix_o.samples, pix_f.samples
            if not s_f:
                return False, f"page {p + 1} rendered empty"
            # Content-loss only: fail when a page that carried ink in the
            # original went entirely near-white in the fixed copy.  Pages
            # that were already blank are left alone.
            if s_o and min(s_o) < 250 <= min(s_f):
                return False, f"page {p + 1} went blank (content lost)"
        return True, f"ok: {n_f} page(s), {len(idx)} sampled, no content lost"
    finally:
        o.close()
        f.close()


def auto_fix(path: str, out_path: str, do_compress: bool = False,
             log=print) -> dict:
    """One-touch safe pipeline: unlock (if encrypted) -> repair (if broken) ->
    strip metadata -> compress (optional).  Each stage is chained through a
    temp file and verified with :func:`verify_safe` against the original; a
    stage that fails verification is skipped and the last known-good stage is
    kept.  The final good stage is atomically placed at ``out_path``."""
    quiet = lambda *a, **k: None                                # noqa: E731
    issues_before = len(diagnose(path, log=quiet))

    tmp_dir = os.path.dirname(os.path.abspath(out_path)) or "."
    base = os.path.basename(out_path) or "out.pdf"
    temps: list[str] = []

    def _stage_path(tag: str) -> str:
        p = os.path.join(tmp_dir, f".{base}.{tag}.stage")
        temps.append(p)
        return p

    stage = path
    actions: list[str] = []
    safe = True

    def _try(tag: str, fn) -> None:
        nonlocal stage
        cand = _stage_path(tag)
        try:
            fn(stage, cand)
        except Exception as e:
            log(f"  auto_fix: {tag} error ({e}); skipped")
            return
        ok, msg = verify_safe(path, cand)
        if ok:
            actions.append(tag)
            stage = cand
        else:
            log(f"  auto_fix: {tag} rejected by verify_safe ({msg}); skipped")

    try:
        if is_encrypted(stage):
            def _do_unlock(src, dst):
                if not unlock(src, dst, log=log):
                    raise RuntimeError("could not decrypt")
            _try("unlock", _do_unlock)

        try:
            probe = fitz.open(stage)
            broken = bool(getattr(probe, "is_repaired", False))
            probe.close()
        except Exception:
            broken = True
        if broken:
            _try("repair", lambda s, d: repair(s, d, log=log))

        _try("strip_metadata", lambda s, d: strip_metadata(s, d, log=log))

        if do_compress:
            _try("compress", lambda s, d: compress(s, d, log=log))

        ok, msg = verify_safe(path, stage)
        safe = ok
        if not ok:
            log(f"  auto_fix: final stage failed verify ({msg}); "
                "writing last known-good")
        _atomic_copy(stage, out_path)
    finally:
        for t in temps:                         # out_path is a separate copy
            _quiet_unlink(t)

    issues_after = len(diagnose(out_path, log=quiet))
    log(f"  auto_fix: actions={actions} safe={safe} "
        f"issues {issues_before} -> {issues_after}")
    return {"actions": actions, "out_path": out_path,
            "issues_before": issues_before, "issues_after": issues_after,
            "safe": safe}
