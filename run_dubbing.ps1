param(
  [Parameter(Mandatory = $true)]
  [string]$Jsonl,

  [string]$Output = '',
  # Engine id from engines\ (omnivoice, chatterbox, openai_speech, ...).
  # Empty = the engine saved in the UI settings.
  [string]$Engine = '',
  [string]$Device = '',
  [double]$Speed = 1.0,
  [double]$TailRoom = 0.45,
  [switch]$AutoFit,
  [switch]$NaturalDuration,
  [double]$MaxAutoSpeed = 1.18,
  [string]$PostClean = 'off',
  [double]$PostFadeMs = 40,
  [double]$PostMinPause = 0.15,
  [switch]$PostNormalize,
  [switch]$KeepSegments
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
  $python = 'python'
}

$argsList = @(
  '.\dub.py',
  $Jsonl,
  '--speed', $Speed,
  '--tail-room', $TailRoom,
  '--max-auto-speed', $MaxAutoSpeed,
  '--post-clean', $PostClean,
  '--post-fade-ms', $PostFadeMs,
  '--post-min-pause', $PostMinPause
)
if ($Engine.Trim()) {
  $argsList += @('--engine', $Engine)
}
if ($Device.Trim()) {
  $argsList += @('--device', $Device)
}
if ($Output.Trim()) {
  $argsList += @('--output', $Output)
}
if ($AutoFit) {
  $argsList += '--auto-fit'
}
if ($NaturalDuration) {
  $argsList += '--natural-duration'
}
if ($KeepSegments) {
  $argsList += '--keep-segments'
}
if ($PostNormalize) {
  $argsList += '--post-normalize'
}

& $python @argsList
exit $LASTEXITCODE
