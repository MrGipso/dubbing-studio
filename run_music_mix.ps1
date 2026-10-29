param(
  [Parameter(Mandatory = $true)]
  [string]$VoiceAudio,

  [Parameter(Mandatory = $true)]
  [string]$MusicAudio,

  [string]$Output = '',
  [double]$MusicVolumeDb = -18.0,
  [string]$Ffmpeg = 'ffmpeg'
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'

if (-not (Test-Path -LiteralPath $VoiceAudio)) {
  throw "Voice audio was not found: $VoiceAudio"
}
if (-not (Test-Path -LiteralPath $MusicAudio)) {
  throw "Music audio was not found: $MusicAudio"
}

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
  $python = 'python'
}

$argsList = @(
  '.\mix_music.py',
  '--voice-audio', $VoiceAudio,
  '--music-audio', $MusicAudio,
  '--music-volume-db', $MusicVolumeDb,
  '--ffmpeg', $Ffmpeg
)

if ($Output.Trim()) {
  $argsList += @('--output', $Output)
}

& $python @argsList
exit $LASTEXITCODE
