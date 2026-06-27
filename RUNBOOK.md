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

## Notes

The backend script prefers Python 3.12 from:

```text
%LOCALAPPDATA%\Programs\Python\Python312\python.exe
```

This avoids using a different Python installation that may not have the RAG dependencies installed.
