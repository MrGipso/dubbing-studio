@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start_UI.ps1"
if errorlevel 1 (
  echo.
  echo Failed to start Dubbing Studio. Run Setup.bat first.
  pause
)
