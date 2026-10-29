param(
  [Parameter(Mandatory = $true)]
  [string]$Media,

  [string]$Model = 'medium',
  [string]$Language = 'ru',
  [string]$OutputDir = 'outputs',
  [string]$Device = 'cuda',
  [string]$ComputeType = 'float16',
  [string]$OutputPrefix = '',
  [switch]$KeepExtractedAudio
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'

function Get-DefaultOutputPrefix([string]$Path) {
  $fullPath = [System.IO.Path]::GetFullPath($Path).ToLowerInvariant()
  $sha = [System.Security.Cryptography.SHA1]::Create()
  try {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($fullPath)
    $hashBytes = $sha.ComputeHash($bytes)
    $hash = -join ($hashBytes | ForEach-Object { $_.ToString('x2') })
  } finally {
    $sha.Dispose()
  }
  $stem = [System.IO.Path]::GetFileNameWithoutExtension($Path)
  $safeStem = ($stem -replace '\s+', '_') -replace '[^\w-]', '_'
  if (-not $safeStem.Trim('_')) {
    $safeStem = 'media'
  }
  return ($hash.Substring(0, 8) + '_' + $safeStem)
}

if (-not (Test-Path -LiteralPath $Media)) {
  throw "Media file was not found: $Media"
}

if (-not $OutputPrefix.Trim()) {
  $OutputPrefix = Get-DefaultOutputPrefix $Media
}

$videoExtensions = @('.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v', '.wmv', '.flv')
$extension = [System.IO.Path]::GetExtension($Media).ToLowerInvariant()
$whisperInput = $Media
$extractedAudio = ''

if ($videoExtensions -contains $extension) {
  New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
  $extractedAudio = Join-Path $OutputDir ($OutputPrefix + '.extracted.wav')
  Write-Output "Extracting audio: $extractedAudio"
  & ffmpeg -hide_banner -nostdin -y -i $Media -vn -ac 1 -ar 16000 -c:a pcm_s16le $extractedAudio
  if ($LASTEXITCODE -ne 0) {
    throw "ffmpeg audio extraction failed with exit code $LASTEXITCODE"
  }
  $whisperInput = $extractedAudio
}

& .\run_whisper.ps1 -Audio $whisperInput -Model $Model -Language $Language -OutputDir $OutputDir -Device $Device -ComputeType $ComputeType -OutputPrefix $OutputPrefix
if ($LASTEXITCODE -ne 0) {
  throw "Whisper failed with exit code $LASTEXITCODE"
}

if ($extractedAudio -and -not $KeepExtractedAudio) {
  Remove-Item -LiteralPath $extractedAudio -Force -ErrorAction SilentlyContinue
}

$txtPath = Join-Path $OutputDir ($OutputPrefix + '.txt')
$srtPath = Join-Path $OutputDir ($OutputPrefix + '.srt')
Write-Output "txt=$((Resolve-Path -LiteralPath $txtPath).Path)"
Write-Output "srt=$((Resolve-Path -LiteralPath $srtPath).Path)"
if ($extractedAudio -and $KeepExtractedAudio) {
  Write-Output "extracted_audio=$((Resolve-Path -LiteralPath $extractedAudio).Path)"
}
