@echo off
REM Double-click to run Planloom from the source folder.
REM First run: builds a private Python environment in .venv from
REM requirements.txt (needs Python 3.10+ and an internet connection once).
REM After that it just launches. Planloom itself never goes online.
setlocal
cd /d "%~dp0"
set "VENV=%~dp0.venv"

if exist "%VENV%\planloom_ready.txt" (
  fc /b "requirements.txt" "%VENV%\planloom_ready.txt" >nul 2>nul && goto launch
  echo requirements.txt changed - updating the environment...
)

where py >nul 2>nul || (
  echo Python 3.10 or newer is needed. Install it from python.org and tick
  echo "Add python.exe to PATH", then double-click this file again.
  pause
  exit /b 1
)

echo Setting up Planloom - one time only, this takes a few minutes...
if not exist "%VENV%\Scripts\python.exe" (
  py -3 -m venv "%VENV%" || goto fail
)
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip || goto fail
"%VENV%\Scripts\python.exe" -m pip install -r requirements.txt || goto fail
copy /y "requirements.txt" "%VENV%\planloom_ready.txt" >nul

:launch
start "" "%VENV%\Scripts\pythonw.exe" -m rfi_stamper
exit /b 0

:fail
echo.
echo Setup did not finish. Check the internet connection and try again.
echo Delete the .venv folder next to this file to start over.
pause
exit /b 1
