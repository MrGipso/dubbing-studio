param(
  [Parameter(Mandatory = $true)]
  [string]$Audio,
  [string]$Model = 'medium',
  [string]$Language = 'auto',
  [string]$OutputDir = 'outputs',
  [string]$Device = 'cuda',
  [string]$ComputeType = 'float16',
  [string]$OutputPrefix = ''
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'
$argsList = @(
  '.\transcribe.py',
  $Audio,
  '--model', $Model,
  '--language', $Language,
  '--device', $Device,
  '--compute-type', $ComputeType,
  '--output-dir', $OutputDir
)
if ($OutputPrefix.Trim()) {
  $argsList += @('--output-prefix', $OutputPrefix)
}
.\.venv\Scripts\python.exe @argsList
exit $LASTEXITCODE
