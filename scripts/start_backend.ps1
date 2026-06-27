$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$preferredPython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
$python = if (Test-Path -LiteralPath $preferredPython) { $preferredPython } else { "python" }

Set-Location -LiteralPath $projectRoot

$existing = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -First 1

if ($existing) {
  try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 10
    Write-Host "Backend is already running on http://127.0.0.1:8000 (PID $($existing.OwningProcess))."
    Write-Host "Health: $($health.status), web=$($health.web_chunks), pdf=$($health.pdf_chunks), model=$($health.model)"
    return
  } catch {
    Write-Host "Port 8000 is already in use by PID $($existing.OwningProcess), but it did not respond to /health."
    Write-Host "Stop it first if you want to start this backend:"
    Write-Host "Stop-Process -Id $($existing.OwningProcess) -Force"
    return
  }
}

& $python -m uvicorn api:app --host 127.0.0.1 --port 8000
