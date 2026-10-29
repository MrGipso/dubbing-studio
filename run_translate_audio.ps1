param(
  [Parameter(Mandatory = $true)]
  [string]$Audio,

  [string]$TargetLanguage = 'English',
  [string]$SourceLanguage = 'Russian',
  [string]$WhisperModel = 'medium',
  [string]$WhisperLanguage = 'auto',
  [string]$WhisperDevice = 'cuda',
  [string]$WhisperComputeType = 'float16',
  [string]$OutputDir = 'outputs',
  [string]$OutputPrefix = '',
  # LLM translator: any preset id from config\llm_providers.json.
  # API keys come from env vars (DUB_LLM_API_KEY, OPENROUTER_API_KEY, ...)
  # or config\secrets.local.json, never from the command line.
  [string]$LlmProvider = 'lmstudio',
  [string]$LlmUrl = '',
  [string]$LlmModel = '',
  [int]$BatchSize = 8,
  [int]$LmContextLength = 8192,
  [string]$Style = 'Natural spoken dubbing, concise timing-preserving translation.',
  [switch]$NoMergePhrases,
  [double]$PhraseMaxGap = 0.8,
  [double]$PhraseMaxDuration = 12.0,
  [string]$TargetLanguageId = '',
  [string]$RefAudio = '',
  [string]$RefText = '',
  [switch]$SkipWhisper,
  [switch]$Resume,
  [switch]$NoAutoLoadModel,
  [switch]$KeepOtherModels,
  [switch]$UnloadModelWhenDone,
  [switch]$DryRun
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
    $safeStem = 'audio'
  }
  return ($hash.Substring(0, 8) + '_' + $safeStem)
}

if (-not $OutputPrefix.Trim()) {
  $OutputPrefix = Get-DefaultOutputPrefix $Audio
}

if (-not $SkipWhisper -and -not (Test-Path -LiteralPath $Audio)) {
  throw "Audio file was not found: $Audio"
}

if (-not $SkipWhisper) {
  & .\run_whisper.ps1 -Audio $Audio -Model $WhisperModel -Language $WhisperLanguage -OutputDir $OutputDir -Device $WhisperDevice -ComputeType $WhisperComputeType -OutputPrefix $OutputPrefix
  if ($LASTEXITCODE -ne 0) {
    throw "Whisper failed with exit code $LASTEXITCODE"
  }
}

$audioStem = [System.IO.Path]::GetFileNameWithoutExtension($Audio)
$srtPath = Join-Path $OutputDir ($OutputPrefix + '.srt')
if ($SkipWhisper -and -not (Test-Path -LiteralPath $srtPath)) {
  $legacySrtPath = Join-Path $OutputDir ($audioStem + '.srt')
  if (Test-Path -LiteralPath $legacySrtPath) {
    $srtPath = $legacySrtPath
  }
}
if (-not (Test-Path -LiteralPath $srtPath)) {
  throw "Whisper SRT was not found: $srtPath"
}

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
  $python = 'python'
}

$translateArgs = @(
  '.\translate_srt.py',
  $srtPath,
  '--target-language', $TargetLanguage,
  '--source-language', $SourceLanguage,
  '--llm-provider', $LlmProvider,
  '--output-dir', $OutputDir,
  '--batch-size', $BatchSize,
  '--lm-context-length', $LmContextLength,
  '--style', $Style,
  '--phrase-max-gap', $PhraseMaxGap,
  '--phrase-max-duration', $PhraseMaxDuration
)

if ($LlmUrl.Trim()) {
  $translateArgs += @('--llm-url', $LlmUrl)
}
if ($LlmModel.Trim()) {
  $translateArgs += @('--model', $LlmModel)
}
if ($NoAutoLoadModel) {
  $translateArgs += '--no-auto-load-model'
}
if ($KeepOtherModels) {
  $translateArgs += '--keep-other-models'
}
if ($UnloadModelWhenDone) {
  $translateArgs += '--unload-model-when-done'
}
if ($NoMergePhrases) {
  $translateArgs += '--no-merge-phrases'
}
if ($TargetLanguageId.Trim()) {
  $translateArgs += @('--target-language-id', $TargetLanguageId)
}
if ($RefAudio.Trim()) {
  $translateArgs += @('--ref-audio', $RefAudio)
}
if ($RefText.Trim()) {
  $translateArgs += @('--ref-text', $RefText)
}
if ($Resume) {
  $translateArgs += '--resume'
}
if ($DryRun) {
  $translateArgs += '--dry-run'
}

& $python @translateArgs
exit $LASTEXITCODE
