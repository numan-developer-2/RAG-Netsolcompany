# NETSOL RAG Runbook

## Start Services

From the project root:

```powershell
.\scripts\start_all.ps1
```

The launcher builds the frontend automatically when no production build exists.

Backend runs at:

```text
http://127.0.0.1:8000
```

Frontend runs at:

```text
http://127.0.0.1:3000
```

## Start Individually

Backend:

```powershell
.\scripts\start_backend.ps1
```

Frontend:

```powershell
.\scripts\start_frontend.ps1
```

## Verify

Backend health:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Frontend proxy health:

```powershell
Invoke-RestMethod http://127.0.0.1:3000/api/rag/health
```

Frontend checks:

```powershell
cd frontend
npm run typecheck
npm run build
```

Backend compile check:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m py_compile api.py config.py graph.py ingest.py nodes.py smoke_test_backend.py state.py
```

Backend smoke test:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" smoke_test_backend.py
```

## Common Issues

### Port 8000 Already In Use

If `start_backend.ps1` says the backend is already running, use the existing server:

```text
http://127.0.0.1:8000
```

To inspect the process:

```powershell
Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8000 -State Listen
```

### Wrong Python Interpreter

If you see:

```text
ModuleNotFoundError: No module named 'langgraph'
```

you are likely using a different Python installation. Use:

```powershell
.\scripts\start_backend.ps1
```

or the explicit Python 3.12 path shown below.

### GitHub Does Not Include Local Data

The repository intentionally does not upload:

```text
.env
chroma_db/
bm25_index.pkl
netsol_scraped_data/
frontend/node_modules/
frontend/.next/
```

These are secrets, generated indexes, raw scraped data, or reproducible build artifacts.

## Notes

The backend script prefers Python 3.12 from:

```text
%LOCALAPPDATA%\Programs\Python\Python312\python.exe
```

This avoids using a different Python installation that may not have the RAG dependencies installed.
