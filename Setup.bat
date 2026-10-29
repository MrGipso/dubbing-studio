@echo off
setlocal
cd /d "%~dp0"

rem One-time setup of the main environment (Whisper + UI).
rem TTS and separation engines are installed later from Settings, each into its own folder.

where uv >nul 2>nul
if errorlevel 1 (
  echo uv not found. Install it once and run Setup again:
  echo   winget install --id=astral-sh.uv -e
  echo   or see https://docs.astral.sh/uv/getting-started/installation/
  pause
  exit /b 1
)

echo Creating the main environment (.venv) ...
uv sync
if errorlevel 1 (
  echo uv sync failed.
  pause
  exit /b 1
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo.
  echo ffmpeg not found in PATH. It is needed for video, mixing and phrase time-stretch:
  echo   winget install --id=Gyan.FFmpeg -e
)

echo.
echo Done. Start the app with Start_UI.bat, then open Settings.
pause
