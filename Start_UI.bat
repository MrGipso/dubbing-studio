@echo off
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

if not exist ".venv\Scripts\python.exe" (
  echo The environment is not set up yet. Run Setup.bat first.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" pipeline_ui.py --open %*
if errorlevel 1 (
  echo.
  echo Dubbing Studio stopped with an error. If the port is busy, close the other window or run: Start_UI.bat --port 8770
  pause
)
