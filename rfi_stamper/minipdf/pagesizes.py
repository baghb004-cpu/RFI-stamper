"""Page sizes in points — the drop-in for ``reportlab.lib.pagesizes``."""
from __future__ import annotations

letter = (612.0, 792.0)
A4 = (595.2755905511812, 841.8897637795277)


def landscape(size):
    """Swap to landscape (wider than tall)."""
    w, h = size
    return (h, w) if w < h else (w, h)
