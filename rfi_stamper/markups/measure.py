"""Measurement math: scale calibration, lengths/areas, caption formatting."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from fractions import Fraction


@dataclass
class ScaleCal:
    real_per_pt: float = 1.0
    unit: str = "ft"

    @classmethod
    def calibrate(cls, p0, p1, real_len: float, unit: str) -> "ScaleCal":
        d = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        if d <= 0:
            raise ValueError("calibration points coincide")
        if real_len <= 0:
            raise ValueError("real length must be positive")
        return cls(real_per_pt=real_len / d, unit=unit)

    def to_dict(self) -> dict:
        return {"real_per_pt": self.real_per_pt, "unit": self.unit}

    @classmethod
    def from_dict(cls, d: dict) -> "ScaleCal":
        return cls(real_per_pt=d.get("real_per_pt", 1.0), unit=d.get("unit", "ft"))


def length(points, cal: ScaleCal) -> float:
    """Straight distance, exactly first point to last point."""
    (x0, y0), (x1, y1) = points[0], points[-1]
    return math.hypot(x1 - x0, y1 - y0) * cal.real_per_pt


def polylength(points, cal: ScaleCal) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1])
               for a, b in zip(points, points[1:])) * cal.real_per_pt


def area(points, cal: ScaleCal) -> float:
    """Shoelace area, absolute, in real units squared."""
    s = 0.0
    n = len(points)
    for i in range(n):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return abs(s) / 2.0 * cal.real_per_pt ** 2


def _ft_in(value: float) -> str:
    """Decimal feet -> feet'-inches" to the nearest 1/16 (e.g. 12'-3 1/2")."""
    sign = "-" if value < 0 else ""
    sixteenths = round(abs(value) * 12 * 16)
    feet, rem = divmod(sixteenths, 12 * 16)
    inches, frac16 = divmod(rem, 16)
    s = f"{sign}{feet}'-{inches}"
    if frac16:
        f = Fraction(frac16, 16)
        s += f" {f.numerator}/{f.denominator}"
    return s + '"'


def fmt_value(value: float, unit: str, kind: str = "length") -> str:
    if kind == "count":
        return str(int(round(value)))
    if kind == "area":
        if unit in ("ft", "ft-in"):
            return f"{value:,.1f} sf"
        return f"{value:,.2f} {unit}²".strip()
    if unit == "ft-in":
        return _ft_in(value)
    return f"{value:,.2f} {unit}".strip()


def compute(markup, cal: ScaleCal) -> float:
    """Dispatch by markup.type; returns the value, sets nothing."""
    t = markup.type
    if t == "measure_length":
        return length(markup.points, cal)
    if t == "measure_polylength":
        return polylength(markup.points, cal)
    if t == "measure_area":
        return area(markup.points, cal)
    if t == "count":
        return float(len(markup.points))
    return 0.0


_MEASURES = ("measure_length", "measure_polylength", "measure_area")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def caption_for(markup, cal: ScaleCal | None = None, template: str = "") -> str:
    """Resolve the caption for a markup; unknown placeholders stay literal."""
    tpl = markup.caption_template or template
    if not tpl:
        if markup.type in _MEASURES:
            tpl = "{value}"
        elif markup.type in ("count", "text"):
            tpl = "{text}"
        else:
            return ""
    try:
        if cal is not None:
            raw = compute(markup, cal)
            unit = cal.unit
        else:
            raw = markup.measure_value
            unit = markup.measure_unit
    except Exception:
        raw, unit = 0.0, ""
    kind = ("area" if markup.type == "measure_area"
            else "count" if markup.type == "count" else "length")
    values = {
        "value": fmt_value(raw, unit, kind), "raw": raw, "unit": unit,
        "subject": markup.subject, "comment": markup.comment,
        "text": markup.text, "page": markup.page, "status": markup.status,
        "type": markup.type, "author": markup.author,
    }

    def repl(m):
        k = m.group(1)
        return str(values[k]) if k in values else m.group(0)

    try:
        return _PLACEHOLDER.sub(repl, tpl)
    except Exception:
        return tpl
