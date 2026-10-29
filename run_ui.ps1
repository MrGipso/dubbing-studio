param(
  [int]$Port = 8768,
  [string]$HostName = '127.0.0.1'
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
  $python = 'python'
}

& $python .\pipeline_ui.py --host $HostName --port $Port
