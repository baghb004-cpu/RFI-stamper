# -*- mode: python ; coding: utf-8 -*-
# Builds two one-file executables:
#   Planloom         windowed GUI (double-click)
#   planloom-cli     console tool for scripting / batch use
# Build on the target OS (run build_windows.bat on Windows to get .exe files).

# the Tracer's trained OCR model rides along so the built-in OCR works with
# no retraining (and no external engine) in the frozen build; the Heartwood
# trade-thesaurus seed likewise (loaded __file__-relative — without it the
# frozen exe would silently ship an EMPTY thesaurus and meaning search
# degrades with no error).  The Swatchbook's cut-sheet library kit
# (manifest + reference recipes + seed sheets, all __file__-relative) rides
# the same way — without it the frozen exe would open with an empty
# component library and every callout would read as a GAP.
_tracer_model = [
    ("rfi_stamper/tracer/model.npz", "rfi_stamper/tracer"),
    ("rfi_stamper/heartwood/thesaurus_seed.json", "rfi_stamper/heartwood"),
    ("rfi_stamper/data/cutsheet_library", "rfi_stamper/data/cutsheet_library"),
]

# Drag-and-drop is Planloom's own ctypes OLE backend (gui/dnd_win32.py) and PDF
# generation is the built-in minipdf engine — the retired tkinterdnd2 and
# reportlab must never ride into the exe even if a dev box has them installed.
_excludes = ["reportlab", "tkinterdnd2"]

a_gui = Analysis(
    ["launch_gui.py"],
    pathex=["."],
    datas=[("assets/planloom.png", "assets")] + _tracer_model,
    excludes=_excludes,
    noarchive=False,
)
pyz_gui = PYZ(a_gui.pure)
exe_gui = EXE(
    pyz_gui,
    a_gui.scripts,
    a_gui.binaries,
    a_gui.datas,
    [],
    name="Planloom",
    console=False,
    upx=False,
    icon="assets/planloom.ico",
)

a_cli = Analysis(
    ["launch_cli.py"],
    pathex=["."],
    datas=_tracer_model,
    excludes=_excludes,              # retired libs; built-ins replace both
    noarchive=False,
)
pyz_cli = PYZ(a_cli.pure)
exe_cli = EXE(
    pyz_cli,
    a_cli.scripts,
    a_cli.binaries,
    a_cli.datas,
    [],
    name="planloom-cli",
    console=True,
    upx=False,
    icon="assets/planloom.ico",
)
