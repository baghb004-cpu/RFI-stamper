"""Core-14 text metrics — the drop-in for ``reportlab.pdfbase.pdfmetrics``.

``layout.py`` decides where a note header truncates by measuring text width, so
this function is a HARD parity invariant, not cosmetics: if a width differs from
reportlab's by a fraction of a point, header line-breaks and box heights shift,
red text can graze linework, and ``verify.py`` FAILs the page.  Parity is exact
by construction — width is summed per glyph from the same canonical Adobe AFM
values reportlab ships (1000-unit em), through the same WinAnsi byte mapping as
the writer draws, with **no kerning** (reportlab's ``stringWidth`` default).
``tests/test_minipdf.py`` re-proves it against the reportlab oracle over a corpus
that includes this app's non-ASCII glyphs.
"""
from __future__ import annotations

from . import encoding
from ._metrics_data import WIDTHS, WINANSI

#: fonts whose metrics we carry (the 12 Latin standard-14 faces).
FONTS = tuple(WIDTHS.keys())

# A few tolerant aliases so callers can pass the obvious spellings.
_ALIASES = {
    "helvetica": "Helvetica",
    "helvetica-bold": "Helvetica-Bold",
    "helvetica-oblique": "Helvetica-Oblique",
    "helvetica-boldoblique": "Helvetica-BoldOblique",
    "times": "Times-Roman",
    "times-roman": "Times-Roman",
    "courier": "Courier",
}


def _canon_font(font: str) -> str:
    if font in WIDTHS:
        return font
    canon = _ALIASES.get(font.lower())
    if canon is None:
        raise KeyError(f"unknown base-14 font {font!r}; known: {', '.join(FONTS)}")
    return canon


def _fallback_width(table: dict) -> int:
    return table.get("question", 0)


def char_width(ch: str, font: str) -> int:
    """Advance width (1000-em units) of one character in ``font``."""
    table = WIDTHS[_canon_font(font)]
    glyph = WINANSI[encoding.to_byte(ch)]
    w = table.get(glyph)
    return w if w is not None else _fallback_width(table)


def string_width(text: str, font: str, size: float) -> float:
    """Rendered width of ``text`` in points — reportlab.stringWidth-exact.

    ``size`` is the font size in points; the sum of per-glyph advances (no
    kerning) scaled by ``size/1000``.
    """
    table = WIDTHS[_canon_font(font)]
    fb = _fallback_width(table)
    total = 0
    for ch in text:
        glyph = WINANSI[encoding.to_byte(ch)]
        w = table.get(glyph)
        total += w if w is not None else fb
    return total * size / 1000.0


# reportlab-compatible spelling so call sites read the same.
stringWidth = string_width
