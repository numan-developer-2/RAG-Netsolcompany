$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $projectRoot "frontend"

Set-Location -LiteralPath $frontendRoot
if (-not (Test-Path -LiteralPath ".next\BUILD_ID")) {
  npm.cmd run build
}
npm.cmd run start
