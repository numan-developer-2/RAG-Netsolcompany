$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $projectRoot "frontend"
$preferredPython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
$python = if (Test-Path -LiteralPath $preferredPython) { $preferredPython } else { "python" }
$frontendBuild = Join-Path $frontendRoot ".next\BUILD_ID"

if (-not (Test-Path -LiteralPath $frontendBuild)) {
  Push-Location -LiteralPath $frontendRoot
  try {
    & npm.cmd run build
  }
  finally {
    Pop-Location
  }
}

Start-Process -FilePath $python `
  -ArgumentList "-m uvicorn api:app --host 127.0.0.1 --port 8000" `
  -WorkingDirectory $projectRoot `
  -RedirectStandardOutput (Join-Path $projectRoot "api_server.log") `
  -RedirectStandardError (Join-Path $projectRoot "api_server.err") `
  -WindowStyle Hidden

Start-Process -FilePath "npm.cmd" `
  -ArgumentList "run start" `
  -WorkingDirectory $frontendRoot `
  -RedirectStandardOutput (Join-Path $projectRoot "frontend_prod.log") `
  -RedirectStandardError (Join-Path $projectRoot "frontend_prod.err") `
  -WindowStyle Hidden

Write-Host "Backend:  http://127.0.0.1:8000"
Write-Host "Frontend: http://127.0.0.1:3000"
