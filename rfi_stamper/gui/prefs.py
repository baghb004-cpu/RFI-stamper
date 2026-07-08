"""User preferences persisted to ~/.planloom/prefs.json (local disk only).

Older installs kept prefs in ~/.rfi_stamper; migrated once, transparently.
"""
from __future__ import annotations

import json
import os
import shutil

PREFS_DIR = os.path.join(os.path.expanduser("~"), ".planloom")
LEGACY_DIR = os.path.join(os.path.expanduser("~"), ".rfi_stamper")
PREFS_PATH = os.path.join(PREFS_DIR, "prefs.json")

DEFAULTS = {
    "theme": "dark",
    "author": os.environ.get("USERNAME") or os.environ.get("USER") or "",
    "offline_guard": True,
    "last_dir": "",
    "invert_pdf_in_dark": False,
    "tips": True,
    "recent": [],
    "effects": "auto",       # auto / full / reduced / off — 2026 machines get
                             # the full treatment, older hardware degrades
    "last_project": "",
}


def _migrate() -> None:
    if os.path.isdir(PREFS_DIR) or not os.path.isdir(LEGACY_DIR):
        return
    try:
        shutil.copytree(LEGACY_DIR, PREFS_DIR)
    except Exception:   # noqa: BLE001 -- migration is best-effort
        pass


def load() -> dict:
    _migrate()
    prefs = dict(DEFAULTS)
    try:
        with open(PREFS_PATH, encoding="utf-8") as f:
            prefs.update(json.load(f))
    except Exception:   # noqa: BLE001 -- missing/corrupt prefs -> defaults
        pass
    # sanitize hand-editable / corruptible fields so a bad prefs.json can
    # never crash GUI startup ------------------------------------------------
    raw = prefs.get("recent")
    prefs["recent"] = ([r for r in raw if isinstance(r, dict)]
                       if isinstance(raw, list) else [])
    if prefs.get("effects") not in ("auto", "full", "reduced", "off"):
        prefs["effects"] = "auto"
    return prefs


def save(prefs: dict) -> None:
    try:
        os.makedirs(PREFS_DIR, exist_ok=True)
        with open(PREFS_PATH, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2)
    except Exception:   # noqa: BLE001 -- prefs are convenience, never fatal
        pass
