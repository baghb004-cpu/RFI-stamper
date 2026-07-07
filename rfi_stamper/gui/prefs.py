"""User preferences persisted to ~/.rfi_stamper/prefs.json (local disk only)."""
from __future__ import annotations

import json
import os

PREFS_DIR = os.path.join(os.path.expanduser("~"), ".rfi_stamper")
PREFS_PATH = os.path.join(PREFS_DIR, "prefs.json")

DEFAULTS = {
    "theme": "light",
    "author": os.environ.get("USERNAME") or os.environ.get("USER") or "",
    "offline_guard": True,
    "last_dir": "",
    "invert_pdf_in_dark": False,
    "tips": True,
}


def load() -> dict:
    prefs = dict(DEFAULTS)
    try:
        with open(PREFS_PATH, encoding="utf-8") as f:
            prefs.update(json.load(f))
    except Exception:   # noqa: BLE001 -- missing/corrupt prefs -> defaults
        pass
    return prefs


def save(prefs: dict) -> None:
    try:
        os.makedirs(PREFS_DIR, exist_ok=True)
        with open(PREFS_PATH, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2)
    except Exception:   # noqa: BLE001 -- prefs are convenience, never fatal
        pass
