@echo off
REM One-click build of RFI-Stamper.exe + rfi-stamp-cli.exe (run this ON WINDOWS)
REM Works on x64 and Windows-on-ARM (native ARM Python builds a native exe).
where py >nul 2>nul || (echo Python not found. Install Python 3.10+ from python.org and tick "Add python.exe to PATH". & pause & exit /b 1)
py -m pip install --upgrade pip
py -m pip install -r requirements.txt pyinstaller
py -m PyInstaller rfi_stamper.spec --noconfirm
echo.
echo Done. Your executables are in the dist\ folder:
echo   dist\RFI-Stamper.exe     (double-click GUI)
echo   dist\rfi-stamp-cli.exe   (command line)
echo Copy them anywhere - they are self-contained and fully offline.
pause
