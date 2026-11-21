"""Stable names: element ids and point numbers that never change.

A layout crew loads a point file into a collector and stakes from it; a
later run must not hand the same name to a different place, and must not
rename a point that did not move.  So names are not derived from sort
order (one new wall would shift every number after it).  They are looked
up in an append-only register:

* an entry is ``(scope, kind, e, n) -> number``;
* a lookup matches an existing entry of the same scope and kind within a
  tolerance, else it appends with the next number of that scope + kind;
* numbers only grow, so a number is never reused, even after the thing it
  named is gone.  A thing that MOVES beyond the tolerance is a new thing
  with a new number (the crew is never handed a silent move).

The register is a small JSON sidecar next to the Red Tags register.
In-memory when no path is given (tests, the one-sheet proof).
"""
from __future__ import annotations

import json
import math
import os

from ..fsutil import atomic_write_bytes

_VERSION = 1
MATCH_FT = 0.02            # about 1/4 in.: the same place


class Names:
    def __init__(self, path: str | None = None):
        self.path = path
        self.entries: dict = {}      # "scope|kind" -> [[e, n, number], ...]
        self.counters: dict = {}     # "scope|kind" -> last number issued
        if path and os.path.exists(path):
            self.load()

    def number(self, scope: str, kind: str, e: float, n: float, *,
               start: int = 1, tol: float = MATCH_FT) -> int:
        key = f"{scope}|{kind}"
        rows = self.entries.setdefault(key, [])
        best = None
        for row in rows:
            d = math.hypot(row[0] - e, row[1] - n)
            if d <= tol and (best is None or d < best[0]):
                best = (d, row)
        if best is not None:
            return int(best[1][2])
        num = max(self.counters.get(key, start - 1), start - 1) + 1
        self.counters[key] = num
        rows.append([round(float(e), 4), round(float(n), 4), num])
        return num

    def to_dict(self) -> dict:
        return {"version": _VERSION, "entries": self.entries, "counters": self.counters}

    def save(self, path: str | None = None) -> None:
        p = path or self.path
        if not p:
            return
        atomic_write_bytes(json.dumps(self.to_dict(), indent=1, sort_keys=True).encode("utf-8"), p)

    def load(self, path: str | None = None) -> None:
        p = path or self.path
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise ValueError(f"the names register {os.path.basename(p)} cannot be read "
                             f"({e}); starting empty would rename every point, so the run "
                             "stops here") from None
        if data.get("version") != _VERSION:
            raise ValueError(f"unsupported names register version {data.get('version')!r}")
        self.entries = {k: [list(r) for r in v] for k, v in (data.get("entries") or {}).items()}
        self.counters = {k: int(v) for k, v in (data.get("counters") or {}).items()}
