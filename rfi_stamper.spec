# -*- mode: python ; coding: utf-8 -*-
# Builds two one-file executables:
#   RFI-Stamper      windowed GUI (double-click)
#   rfi-stamp-cli    console tool for scripting / batch use
# Build on the target OS (run build_windows.bat on Windows to get .exe files).

from PyInstaller.utils.hooks import collect_data_files

try:
    tkdnd_datas = collect_data_files("tkinterdnd2")
except Exception:               # tkinterdnd2 not installed: DnD degrades to Browse
    tkdnd_datas = []

a_gui = Analysis(
    ["launch_gui.py"],
    pathex=["."],
    datas=tkdnd_datas,
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
    name="RFI-Stamper",
    console=False,
    upx=False,
)

a_cli = Analysis(
    ["launch_cli.py"],
    pathex=["."],
    datas=tkdnd_datas,
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
    name="rfi-stamp-cli",
    console=True,
    upx=False,
)
