"""minipdf — Planloom's from-scratch, dependency-free PDF writer.

Retires ``reportlab``: the app only ever generates PDFs from non-embedded
base-14 text, rectangles/lines, flat RGB fills and simple tables, which is a
small, fully-documented slice of ISO 32000-1 this package emits directly (pure
Python + stdlib ``zlib``/``hashlib`` only — no third-party dependency, matching
the offline-and-from-scratch policy that already retired the OCR engine).

Staged like OCR_PLAN: P1 is the foundation — WinAnsi text encoding and
reportlab-exact Core-14 metrics (this module set); later phases add the object
serializer, a reportlab-``canvas``-shaped façade, and the flow/table engine, all
behind a pixel-diff parity gate against the reportlab oracle before cutover.  See
MINIPDF_PLAN.md.
"""
from __future__ import annotations

from . import canvas, colors, content, document, encoding, metrics
from .canvas import Canvas
from .colors import Color, HexColor, black, white
from .content import Content, fmt_num
from .document import Document, Page
from .metrics import string_width, stringWidth, char_width, FONTS

__all__ = [
    "canvas", "colors", "content", "document", "encoding", "metrics",
    "Canvas", "Color", "HexColor", "black", "white",
    "Content", "Document", "Page", "fmt_num",
    "string_width", "stringWidth", "char_width", "FONTS",
]
