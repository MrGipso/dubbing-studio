param(
  [int]$Port = 8768,
  [string]$HostName = '127.0.0.1'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$url = "http://${HostName}:$Port"

function Get-UiProcessOnPort {
  param([int]$Port)

  $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $connection) {
    return $null
  }

  $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($connection.OwningProcess)" -ErrorAction SilentlyContinue
  if ($process -and $process.CommandLine -like '*pipeline_ui.py*') {
    return $process
  }

  if ($process) {
    throw "Port $Port is already used by another process: $($process.CommandLine)"
  }
  throw "Port $Port is already used by PID $($connection.OwningProcess)."
}

function Wait-Ui {
  param([string]$Url)

  for ($i = 0; $i -lt 40; $i++) {
    try {
      Invoke-RestMethod -Uri "$Url/api/state" -TimeoutSec 1 | Out-Null
      return $true
    } catch {
      Start-Sleep -Milliseconds 250
    }
  }
  return $false
}

Set-Location $root
$env:PYTHONIOENCODING = 'utf-8'

$existing = Get-UiProcessOnPort -Port $Port
if (-not $existing) {
  $python = Join-Path $root '.venv\Scripts\python.exe'
  if (-not (Test-Path -LiteralPath $python)) {
    $python = 'python'
  }

  Start-Process `
    -FilePath $python `
    -ArgumentList @('pipeline_ui.py', '--host', $HostName, '--port', $Port) `
    -WorkingDirectory $root `
    -WindowStyle Hidden | Out-Null

  if (-not (Wait-Ui -Url $url)) {
    throw "UI server did not start on $url"
  }
}

Start-Process $url
