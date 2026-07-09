"""Accuracy harness for the Tracer — CER/WER + auto-labeled real ground truth.

Two metrics and one free-ground-truth generator (OCR_PLAN §6 test plan):

* :func:`cer` / :func:`wer` — pure-Python Levenshtein edit distance normalized
  by the reference length; no external library.
* :func:`auto_label_set` — rasterize the **vector** text pages the app itself
  renders and pair each raster with ``fitz.get_text("words")`` (text AND
  boxes).  Because the truth comes from the very pages we rasterize, this yields
  unlimited labeled real lettering at zero human cost and with no leakage — the
  P2 clean-scan bar is scored here.
* :func:`confusion` — a 43×43 ``(true, pred)`` count matrix, plus
  :func:`domain_error` weighting sheet-number / dimension characters (digits and
  the technical marks) above prose, per OCR_PLAN §6's domain sub-metric.

Scoring folds case and whitespace the way the app's own OCR tests do, and (for
the character metric) restricts to the uppercase CHARSET the engine models, so
the number reflects recognition rather than the vector layer's punctuation.
"""
from __future__ import annotations

import numpy as np
import fitz

from .fonts import CHARSET


# --------------------------------------------------------------------------- #
#  Edit-distance metrics                                                       #
# --------------------------------------------------------------------------- #

def _levenshtein(a, b) -> int:
    """Classic O(len(a)·len(b)) DP edit distance over two sequences."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        ai = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ai == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[lb]


def cer(ref: str, hyp: str) -> float:
    """Character error rate = edit distance / max(1, len(ref))."""
    return _levenshtein(list(ref), list(hyp)) / max(1, len(ref))


def wer(ref: str, hyp: str) -> float:
    """Word error rate = token edit distance / max(1, #ref tokens)."""
    ra, rb = ref.split(), hyp.split()
    return _levenshtein(ra, rb) / max(1, len(ra))


# --------------------------------------------------------------------------- #
#  Text normalization for scoring                                             #
# --------------------------------------------------------------------------- #

def only_charset(s: str) -> str:
    """Uppercase and keep only characters the engine models (drop the rest)."""
    keep = set(CHARSET)
    return "".join(c for c in s.upper() if c in keep)


def words_to_text(words, spaced: bool = True) -> str:
    """Join word tuples ``(x0,y0,x1,y1,text,...)`` in reading order.

    Sorted top-to-bottom then left-to-right with a line tolerance, so both the
    vector truth and the OCR hypothesis are linearized the same way.
    """
    if not words:
        return ""
    hs = [w[3] - w[1] for w in words]
    tol = 0.6 * (np.median(hs) if hs else 8.0)
    ws = sorted(words, key=lambda w: (round(w[1] / max(1.0, tol)), w[0]))
    toks = [w[4] for w in ws if str(w[4]).strip()]
    return (" ".join(toks) if spaced else "".join(toks))


# --------------------------------------------------------------------------- #
#  Auto-labeled real ground truth (vector pages → raster + truth words)        #
# --------------------------------------------------------------------------- #

def _gray_of(page, dpi: int):
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(
        pix.height, pix.width).copy()


def auto_label_set(pages, dpi: int = 300):
    """``[(gray_uint8, truth_words), ...]`` from vector text pages.

    ``pages`` may be a ``fitz.Document`` or a list of ``fitz.Page``.  Each
    page is rasterized at ``dpi`` and its ``get_text("words")`` is scaled to the
    same pixel frame, giving ``truth_words = [(x0,y0,x1,y1,text), ...]`` (pixels)
    — real ground truth with matching boxes, zero human cost, no leakage.
    """
    if isinstance(pages, fitz.Document):
        pages = [pages[i] for i in range(pages.page_count)]
    scale = dpi / 72.0
    out = []
    for page in pages:
        gray = _gray_of(page, dpi)
        truth = []
        for w in page.get_text("words"):
            x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
            truth.append((x0 * scale, y0 * scale, x1 * scale, y1 * scale, text))
        out.append((gray, truth))
    return out


def score_page(gray, truth_words, dpi: int = 300, spaced: bool = False):
    """OCR ``gray`` and score against ``truth_words`` → ``{"cer","wer",...}``.

    ``spaced=False`` scores the character metric on whitespace-stripped,
    CHARSET-restricted strings (recognition accuracy, the P2 focus); the word
    metric always uses the spaced linearization.
    """
    from . import read_image
    hyp_words = [(x0, y0, x1, y1, t)
                 for (x0, y0, x1, y1, t, _s) in read_image(gray, dpi=dpi)]
    ref_c = only_charset(words_to_text(truth_words, spaced=False))
    hyp_c = only_charset(words_to_text(hyp_words, spaced=False))
    ref_w = only_charset(words_to_text(truth_words, spaced=True))
    hyp_w = only_charset(words_to_text(hyp_words, spaced=True))
    return {"cer": cer(ref_c, hyp_c), "wer": wer(ref_w, hyp_w),
            "ref": ref_c, "hyp": hyp_c, "n_ref": len(ref_c)}


# --------------------------------------------------------------------------- #
#  Confusion matrix + domain sub-metric                                        #
# --------------------------------------------------------------------------- #

def confusion(pairs) -> np.ndarray:
    """43×43 ``(true, pred)`` count matrix over ``[(true_char, pred_char)]``."""
    idx = {c: i for i, c in enumerate(CHARSET)}
    M = np.zeros((len(CHARSET), len(CHARSET)), np.int64)
    for t, p in pairs:
        if t in idx and p in idx:
            M[idx[t], idx[p]] += 1
    return M


_STRUCT = set("0123456789-./#'\"")     # sheet-number / dimension characters


def domain_error(pairs, struct_weight: float = 3.0) -> float:
    """Weighted char-error rate charging structural-token errors ``×weight``.

    A wrong digit or dimension mark (a sheet number or feet-inches) costs more
    than a wrong prose letter — the OCR_PLAN §6 domain sub-metric.
    """
    num = den = 0.0
    for t, p in pairs:
        w = struct_weight if t in _STRUCT else 1.0
        den += w
        if t != p:
            num += w
    return num / den if den else 0.0
