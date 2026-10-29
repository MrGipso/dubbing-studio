# One-time setup of the main environment (Whisper + UI).
# TTS engines are installed later from the "Engines" tab, each into its own folder.

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host 'uv not found. Install it once and run Setup again:' -ForegroundColor Yellow
  Write-Host '  winget install --id=astral-sh.uv -e'
  Write-Host '  (or see https://docs.astral.sh/uv/getting-started/installation/)'
  exit 1
}

Write-Host 'Creating the main environment (.venv) ...'
uv sync
if ($LASTEXITCODE -ne 0) {
  throw "uv sync failed with exit code $LASTEXITCODE"
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Host ''
  Write-Host 'ffmpeg not found in PATH. It is needed for video files, music mix and phrase time-stretch:' -ForegroundColor Yellow
  Write-Host '  winget install --id=Gyan.FFmpeg -e'
}

Write-Host ''
Write-Host 'Done. Start the app with Start_UI.bat, then open the "Engines" tab.' -ForegroundColor Green
