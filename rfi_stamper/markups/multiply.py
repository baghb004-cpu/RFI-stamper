"""Multiply a markup into offset copies (linear run or grid)."""
from __future__ import annotations

import uuid
from datetime import datetime

from .model import Markup


def _copy_at(markup: Markup, dx: float, dy: float) -> Markup:
    m = markup.translated(dx, dy)          # fresh id, shifted points
    m.id = uuid.uuid4().hex
    m.created = datetime.now().isoformat(timespec="seconds")
    m.status = "none"
    m.status_history = []
    return m


def multiply(markup: Markup, copies: int, dx: float, dy: float,
             rows: int = 0, cols: int = 0) -> list:
    """Return new markups offset from the original.

    Linear mode (rows==0 or cols==0): 'copies' markups, i-th at (i*dx, i*dy).
    Grid mode (rows>0 and cols>0): rows x cols with spacing (dx, dy); cell
    (0,0) is the original and is skipped; 'copies' is ignored.
    Copies get fresh id/created and reset status; measure values are copied
    as-is (recomputing is the caller's job).
    """
    if rows > 0 and cols > 0:
        return [_copy_at(markup, c * dx, r * dy)
                for r in range(rows) for c in range(cols) if (r, c) != (0, 0)]
    if copies < 1:
        raise ValueError("copies must be >= 1")
    return [_copy_at(markup, i * dx, i * dy) for i in range(1, copies + 1)]
