# NETSOL RAG Backend

This folder contains the working NETSOL RAG backend.

## Current Backend Status

- Vector DB: ChromaDB
- Web chunks: `25,029`
- PDF chunks: `10,651`
- Metadata records: `6,315`
- BM25 chunks: `35,680`
- Embeddings: local `sentence-transformers/all-MiniLM-L6-v2`
- LLM: OpenRouter `google/gemini-2.5-flash`

Gemini embeddings were not used for final ingestion because the API quota was exhausted. Local embeddings keep Step 1 complete and repeatable.

## Local Data Artifacts

The backend expects these generated artifacts to exist locally:

```text
chroma_db/
bm25_index.pkl
netsol_scraped_data/
```

They are intentionally ignored by Git because they are large generated files:

- `netsol_scraped_data/`: about `1.64 GB`
- `chroma_db/`: about `956 MB`
- `bm25_index.pkl`: about `218 MB`

If these files are missing on a fresh clone, restore them from the local data backup or rerun `ingest.py` after placing the scraped JSONL files in `netsol_scraped_data/`.

## Answer Quality Controls

The backend is tuned for short company-focused answers. These settings live in `.env` / `config.py`:

```text
SIMPLE_CONTEXT_CHUNKS=3
COMPLEX_CONTEXT_CHUNKS=5
CONTEXT_CHARS_PER_CHUNK=900
ANSWER_MAX_WORDS=90
GENERATION_MAX_TOKENS=384
```

`nodes.py` also applies backend-side cleanup so simple company questions do not return long paragraphs or irrelevant old technical details.

## Run Backend

Recommended:

```powershell
.\scripts\start_backend.ps1
```

Or run with the Python 3.12 interpreter that has the RAG dependencies:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m uvicorn api:app --host 127.0.0.1 --port 8000
```

Do not use plain `python` on this machine unless it points to Python 3.12. In the current shell, `python` may resolve to `D:\FlaskPython\python.exe`, which does not have `langgraph` and the other backend dependencies installed.

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Smoke test:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" smoke_test_backend.py
```

## API Endpoints

- `GET /health`
- `GET /stats`
- `POST /query`
- `POST /query/stream`
- `GET /sources/{chunk_id}`

Example query:

```powershell
$body = @{ query = "What is LeasePak?"; persona = "general"; chat_history = @() } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/query -Method Post -ContentType "application/json" -Body $body
```

## Important Files

- `ingest.py` builds ChromaDB collections and BM25.
- `nodes.py` contains LangGraph node logic.
- `graph.py` wires the LangGraph pipeline.
- `api.py` exposes the FastAPI server.
- `chroma_db/` contains the vector database.
- `bm25_index.pkl` contains the keyword index.
- `query_logs.jsonl` records query metadata.
- `llm_errors.jsonl` records LLM failures if they happen.

## Restart Notes

If the server is already running, stop it first:

```powershell
Stop-Process -Id <PID>
```

Find the PID:

```powershell
Get-CimInstance Win32_Process -Filter "name = 'python.exe'" |
  Where-Object { $_.CommandLine -like '*uvicorn api:app*' } |
  Select-Object ProcessId,CommandLine
```
