"""Content-stream builder — the ISO 32000-1 §8-9 operator subset we emit.

Vector + text only: path construction (m l re), painting (S f B n), clipping
(W n), graphics state (q Q cm w), DeviceRGB color (rg RG), and text objects
(BT/ET, Tf, Td, TL, T*, Tj).  No general engine — just the operators the app's
output needs, kept minimal so the bytes are easy to prove correct.

Coordinate space is PDF-native: origin bottom-left, +y up, 1 unit = 1/72".  A
path is built fully then painted exactly once; every ``q`` is balanced by ``Q``.
Numbers are formatted in the C locale with fixed precision and no scientific
notation so the output is deterministic (byte-reproducible for the pixel-diff
baseline).  Text is transcoded to WinAnsi and escaped by :mod:`encoding`.
"""
from __future__ import annotations

from . import encoding


def fmt_num(x: float) -> str:
    """Deterministic PDF numeric: fixed precision, no exponent, no '-0'."""
    if isinstance(x, bool):                # bool IS an int: True would emit
        x = int(x)                         # the token "True" — coerce to 1/0
    if isinstance(x, int):
        return str(x)
    if x != x or x in (float("inf"), float("-inf")):
        raise ValueError(f"non-finite PDF number: {x!r}")
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    if s in ("-0", ""):
        s = "0"
    return s


def _nums(*xs) -> bytes:
    return b" ".join(fmt_num(x).encode("ascii") for x in xs)


class Content:
    """Accumulates content-stream bytes; ``bytes(content)`` is the stream body.

    ``set_font`` resolves a base-14 name to a page resource key via the owning
    document's font registry, so the caller names fonts by their real name
    (``Helvetica-Bold``) and never sees ``/F1``.
    """

    def __init__(self, doc):
        self._doc = doc
        self._buf = bytearray()

    # -- raw / state -------------------------------------------------------- #
    def raw(self, line: bytes) -> "Content":
        self._buf += line + b"\n"
        return self

    def save(self) -> "Content":
        return self.raw(b"q")

    def restore(self) -> "Content":
        return self.raw(b"Q")

    def concat(self, a, b, c, d, e, f) -> "Content":
        return self.raw(_nums(a, b, c, d, e, f) + b" cm")

    def translate(self, tx, ty) -> "Content":
        return self.concat(1, 0, 0, 1, tx, ty)

    def line_width(self, w) -> "Content":
        return self.raw(_nums(w) + b" w")

    def set_dash(self, array, phase=0) -> "Content":
        arr = b"[" + b" ".join(fmt_num(v).encode("ascii") for v in array) + b"]"
        return self.raw(arr + b" " + fmt_num(phase).encode("ascii") + b" d")

    # -- color -------------------------------------------------------------- #
    def fill_rgb(self, r, g, b) -> "Content":
        return self.raw(_nums(r, g, b) + b" rg")

    def stroke_rgb(self, r, g, b) -> "Content":
        return self.raw(_nums(r, g, b) + b" RG")

    def fill_gray(self, g) -> "Content":
        return self.raw(_nums(g) + b" g")

    def stroke_gray(self, g) -> "Content":
        return self.raw(_nums(g) + b" G")

    # -- paths -------------------------------------------------------------- #
    def move_to(self, x, y) -> "Content":
        return self.raw(_nums(x, y) + b" m")

    def line_to(self, x, y) -> "Content":
        return self.raw(_nums(x, y) + b" l")

    def curve_to(self, x1, y1, x2, y2, x3, y3) -> "Content":
        return self.raw(_nums(x1, y1, x2, y2, x3, y3) + b" c")

    def rect(self, x, y, w, h) -> "Content":
        return self.raw(_nums(x, y, w, h) + b" re")

    def close(self) -> "Content":
        return self.raw(b"h")

    # -- painting ----------------------------------------------------------- #
    def stroke(self) -> "Content":
        return self.raw(b"S")

    def fill(self) -> "Content":
        return self.raw(b"f")

    def fill_stroke(self) -> "Content":
        return self.raw(b"B")

    def end_path(self) -> "Content":
        return self.raw(b"n")

    def clip(self) -> "Content":
        """Intersect the clip with the current path, then discard it (W n)."""
        return self.raw(b"W n")

    # -- text --------------------------------------------------------------- #
    def set_font(self, font: str, size: float) -> "Content":
        key = self._doc._use_font(font)
        return self.raw(encoding.pdf_name(key) + b" "
                        + fmt_num(size).encode("ascii") + b" Tf")

    def text(self, x, y, s: str, font: str = None, size: float = None) -> "Content":
        """Draw a single-line string with its baseline at (x, y)."""
        self.raw(b"BT")
        if font is not None:
            self.set_font(font, size)
        self.raw(_nums(x, y) + b" Td")
        self.raw(encoding.pdf_string(s) + b" Tj")
        self.raw(b"ET")
        return self

    def __bytes__(self) -> bytes:
        return bytes(self._buf)
