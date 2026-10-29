param(
  [Parameter(Mandatory = $true)]
  [string]$InputPath,

  [string]$Output = '',
  [string]$Mode = 'soft',
  [double]$MinPause = 0.15,
  [double]$FadeMs = 40,
  [string]$Preset = 'custom',
  [double]$Amount = 55,
  [switch]$Deesser,
  [switch]$Eq,
  [switch]$Compressor,
  [switch]$Saturation,
  [switch]$Limiter,
  [switch]$Loudness,
  [switch]$Room,
  [double]$DeesserFreq = 5200,
  [double]$DeesserStrength = 55,
  [double]$EqMudDb = -1.8,
  [double]$EqClarityDb = 2.2,
  [double]$EqAirDb = 1.1,
  [double]$CompThreshold = -20,
  [double]$CompRatio = 3.0,
  [double]$CompMakeup = 2.2,
  [double]$SatDrive = 45,
  [double]$SatMix = 28,
  [double]$LimiterCeiling = 0.95,
  [double]$LoudnessTarget = -16,
  [double]$RoomMix = 10,
  [switch]$Normalize
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
  $python = 'python'
}

$argsList = @(
  '.\clean_wav.py',
  '--input', $InputPath,
  '--mode', $Mode,
  '--min-pause', $MinPause,
  '--fade-ms', $FadeMs,
  '--preset', $Preset,
  '--amount', $Amount,
  '--deesser-freq', $DeesserFreq,
  '--deesser-strength', $DeesserStrength,
  '--eq-mud-db', $EqMudDb,
  '--eq-clarity-db', $EqClarityDb,
  '--eq-air-db', $EqAirDb,
  '--comp-threshold', $CompThreshold,
  '--comp-ratio', $CompRatio,
  '--comp-makeup', $CompMakeup,
  '--sat-drive', $SatDrive,
  '--sat-mix', $SatMix,
  '--limiter-ceiling', $LimiterCeiling,
  '--loudness-target', $LoudnessTarget,
  '--room-mix', $RoomMix
)
if ($Output.Trim()) {
  $argsList += @('--output', $Output)
}
if ($Normalize) {
  $argsList += '--normalize'
}
if ($Deesser) {
  $argsList += '--deesser'
}
if ($Eq) {
  $argsList += '--eq'
}
if ($Compressor) {
  $argsList += '--compressor'
}
if ($Saturation) {
  $argsList += '--saturation'
}
if ($Limiter) {
  $argsList += '--limiter'
}
if ($Loudness) {
  $argsList += '--loudness'
}
if ($Room) {
  $argsList += '--room'
}

& $python @argsList
exit $LASTEXITCODE
