"""Minimal color objects — the drop-in for ``reportlab.lib.colors``.

The app only needs flat RGB (and grayscale) fills/strokes; plates are monochrome
black.  ``HexColor`` raises ``ValueError`` on malformed input, matching reportlab
(draft.py / fieldpro.py catch that).
"""
from __future__ import annotations


class Color:
    __slots__ = ("red", "green", "blue", "alpha")

    def __init__(self, red=0.0, green=0.0, blue=0.0, alpha=1.0):
        self.red, self.green, self.blue, self.alpha = red, green, blue, alpha

    def rgb(self):
        return (self.red, self.green, self.blue)

    def __eq__(self, other):
        return isinstance(other, Color) and self.rgb() == other.rgb()

    def __repr__(self):
        return f"Color({self.red},{self.green},{self.blue})"


def HexColor(val) -> Color:
    """Parse ``0xRRGGBB`` or ``"#RRGGBB"`` into a Color (ValueError if malformed)."""
    if isinstance(val, Color):
        return val
    if isinstance(val, int):
        n = val
    else:
        s = str(val).strip().lstrip("#")
        if len(s) != 6:
            raise ValueError(f"bad hex color {val!r}")
        n = int(s, 16)          # raises ValueError on non-hex
    return Color(((n >> 16) & 0xFF) / 255.0,
                 ((n >> 8) & 0xFF) / 255.0,
                 (n & 0xFF) / 255.0)


black = Color(0, 0, 0)
white = Color(1, 1, 1)
red = Color(1, 0, 0)
