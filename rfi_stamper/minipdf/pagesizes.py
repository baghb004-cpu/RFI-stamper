"""Page sizes in points — the drop-in for ``reportlab.lib.pagesizes``."""
from __future__ import annotations

letter = (612.0, 792.0)
A4 = (595.2755905511812, 841.8897637795277)
legal = (612.0, 1008.0)
TABLOID = (792.0, 1224.0)


def landscape(size):
    """Swap to landscape (wider than tall)."""
    w, h = size
    return (h, w) if w < h else (w, h)


def portrait(size):
    """Swap to portrait (taller than wide)."""
    w, h = size
    return (w, h) if w < h else (h, w)
