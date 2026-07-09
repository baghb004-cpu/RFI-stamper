# -*- mode: python ; coding: utf-8 -*-
# Builds two one-file executables:
#   Planloom         windowed GUI (double-click)
#   planloom-cli     console tool for scripting / batch use
# Build on the target OS (run build_windows.bat on Windows to get .exe files).

from PyInstaller.utils.hooks import collect_data_files

try:
    tkdnd_datas = collect_data_files("tkinterdnd2")
except Exception:               # tkinterdnd2 not installed: DnD degrades to Browse
    tkdnd_datas = []

# the Tracer's trained OCR model rides along so the built-in OCR works with
# no retraining (and no external engine) in the frozen build
_tracer_model = [("rfi_stamper/tracer/model.npz", "rfi_stamper/tracer")]

a_gui = Analysis(
    ["launch_gui.py"],
    pathex=["."],
    datas=tkdnd_datas + [("assets/planloom.png", "assets")] + _tracer_model,
    hiddenimports=["tkinterdnd2"],   # optional drag-and-drop; warning-only if absent
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
    datas=tkdnd_datas + _tracer_model,
    hiddenimports=["tkinterdnd2"],
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
