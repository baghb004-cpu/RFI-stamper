"""Tool Chest: reusable markup presets persisted to a user JSON file."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .model import Markup, Style


@dataclass
class ToolPreset:
    name: str
    type: str
    style: Style = field(default_factory=Style)
    subject: str = ""
    caption_template: str = ""
    text: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.type, "style": self.style.to_dict(),
                "subject": self.subject, "caption_template": self.caption_template,
                "text": self.text}

    @classmethod
    def from_dict(cls, d: dict) -> "ToolPreset":
        return cls(name=d["name"], type=d["type"],
                   style=Style.from_dict(d.get("style", {})),
                   subject=d.get("subject", ""),
                   caption_template=d.get("caption_template", ""),
                   text=d.get("text", ""))


DEFAULTS = (
    ToolPreset("Revision Cloud", "cloud", Style(color="#D01414", width=2.0)),
    ToolPreset("RFI Callout", "callout", Style(color="#D01414", width=1.5),
               subject="RFI", text="RFI"),
    ToolPreset("Punch Item", "cloud", Style(color="#D01414", width=1.5),
               subject="Punch"),
    ToolPreset("Field Verify", "text", Style(color="#D01414", font_size=12.0),
               subject="Field Verify", text="FIELD VERIFY"),
    ToolPreset("Dimension Check", "ellipse", Style(color="#1450D0", width=1.5),
               subject="Dimension Check"),
    ToolPreset("Highlight", "highlighter", Style(color="#F5D400", width=6.0,
               opacity=0.35)),
    ToolPreset("Count Dot", "count", Style(color="#D01414"),
               caption_template="{text}"),
    ToolPreset("Area Takeoff", "measure_area",
               Style(color="#108030", fill="#B0E0B0", width=1.0, opacity=0.6),
               subject="Area", caption_template="{subject}: {value}"),
    # standard construction stamps (text notes; place, then move as needed)
    ToolPreset("Stamp: HOLD", "text",
               Style(color="#D01414", font_size=16.0), subject="Hold",
               text="HOLD"),
    ToolPreset("Stamp: AS-BUILT", "text",
               Style(color="#108030", font_size=14.0), subject="As-Built",
               text="AS-BUILT"),
    ToolPreset("Stamp: REVISED", "text",
               Style(color="#D01414", font_size=14.0), subject="Revised",
               text="REVISED"),
    ToolPreset("Stamp: NOT IN CONTRACT", "text",
               Style(color="#B45309", font_size=12.0), subject="NIC",
               text="NOT IN CONTRACT"),
    ToolPreset("Stamp: BY OTHERS", "text",
               Style(color="#B45309", font_size=12.0), subject="By Others",
               text="BY OTHERS"),
    ToolPreset("Stamp: VERIFY IN FIELD", "text",
               Style(color="#1450D0", font_size=12.0), subject="VIF",
               text="VERIFY IN FIELD"),
    ToolPreset("Punch Dot (numbered)", "count",
               Style(color="#D01414"), subject="Punch",
               text="P", caption_template="{text}"),
    ToolPreset("Length Check", "measure_length",
               Style(color="#1450D0", width=1.2),
               subject="Dim", caption_template="{value}"),
)


class ToolChest:
    DEFAULT_PATH = os.path.join("~", ".planloom", "toolchest.json")

    def __init__(self, path: str | None = None):
        self.path = os.path.expanduser(path or self.DEFAULT_PATH)
        self.presets: list[ToolPreset] = []
        if os.path.exists(self.path):
            self.load()
        else:
            self.presets = [ToolPreset.from_dict(p.to_dict()) for p in DEFAULTS]

    def add(self, preset: ToolPreset):
        self.presets.append(preset)

    def remove(self, name: str):
        self.presets = [p for p in self.presets if p.name != name]

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        data = {"version": 1, "presets": [p.to_dict() for p in self.presets]}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)

    def load(self):
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        self.presets = [ToolPreset.from_dict(d) for d in data.get("presets", [])]

    def search(self, query: str) -> list:
        q = query.lower()
        return [p for p in self.presets
                if q in p.name.lower() or q in p.type.lower()
                or q in p.subject.lower()]

    def make_markup(self, preset: ToolPreset, page: int, points) -> Markup:
        return Markup.new(page, preset.type, points,
                          style=Style.from_dict(preset.style.to_dict()),
                          subject=preset.subject,
                          caption_template=preset.caption_template,
                          text=preset.text)
