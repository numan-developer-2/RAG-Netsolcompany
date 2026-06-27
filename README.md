# RAG-Netsolcompany

Professional Retrieval-Augmented Generation (RAG) system for querying a NETSOL Technologies company knowledge base. The project includes a FastAPI/LangGraph backend, ChromaDB + BM25 retrieval, Gemini/OpenRouter LLM generation, and a modern Next.js frontend.

## Project Goal

This system answers company-related questions from a scraped NETSOL corpus using source-grounded retrieval. It is designed for short, factual answers with citations instead of long generic LLM responses.

Example questions:

- What is LeasePak?
- What does NETSOL do?
- Summarize NETSOL financial performance from available reports.
- Compare LeasePak and NFS Ascent using the indexed corpus.

## Current Dataset Status

The local dataset and generated indexes are intentionally not committed to GitHub because they are large generated artifacts.

| Artifact | Local path | Status | Size |
| --- | --- | --- | --- |
| Scraped NETSOL data | `netsol_scraped_data/` | Local only | ~1.64 GB |
| Scraped files | `netsol_scraped_data/` | Local only | 13,665 files |
| Main RAG chunks | `netsol_scraped_data/rag_chunks.jsonl` | Local only | ~131 MB |
| Page corpus records | `netsol_scraped_data/rag_corpus.jsonl` | Local only | ~109 MB |
| PDF chunks | `netsol_scraped_data/rag_pdf_chunks.jsonl` | Local only | ~24.6 MB |
| Chroma vector DB | `chroma_db/` | Local only | ~956 MB |
| BM25 keyword index | `bm25_index.pkl` | Local only | ~218 MB |

Indexed collection counts:

- Web chunks: `25,029`
- PDF chunks: `10,651`
- Metadata records: `6,315`

These artifacts are ignored through `.gitignore` so the repository remains lightweight and safe to clone.

## Architecture

```text
User question
  -> Next.js frontend
  -> FastAPI API
  -> LangGraph RAG pipeline
  -> Query analysis
  -> Hybrid retrieval: ChromaDB vector search + BM25 keyword search
  -> Reranking / top evidence selection
  -> Gemini/OpenRouter answer generation
  -> Hallucination guard
  -> Concise sourced response
```

## Tech Stack

Backend:

- Python
- FastAPI
- LangGraph
- ChromaDB
- BM25 (`rank-bm25`)
- Sentence Transformers local embeddings
- Gemini through OpenRouter

Frontend:

- Next.js
- React
- TypeScript
- Framer Motion
- Lucide icons

Retrieval/LLM configuration:

- Embeddings: `sentence-transformers/all-MiniLM-L6-v2`
- LLM provider: OpenRouter
- LLM model: `google/gemini-2.5-flash`
- Vector DB: ChromaDB
- Keyword search: BM25

## Repository Contents

Important files:

- `api.py` - FastAPI server and streaming endpoints
- `graph.py` - LangGraph wiring
- `nodes.py` - query analysis, retrieval, generation, validation, formatting
- `ingest.py` - ingestion pipeline for ChromaDB and BM25
- `config.py` - central backend settings
- `state.py` - LangGraph state schema
- `frontend/` - Next.js RAG interface
- `scripts/` - startup scripts for backend/frontend
- `BACKEND_README.md` - backend-specific details
- `RUNBOOK.md` - local run and verification commands

Included project reference files:

- `Netsol_RAG_Backend_MasterPrompt.pdf`
- `Netsol_RAG_Backend_MasterPrompt.extracted.txt`
- `Netsol_RAG_MasterDoc.docx`

These reference documents are small and kept in the repository because they describe the original backend/RAG requirements.

## Files Not Uploaded

The following are intentionally not committed:

```text
.env
.vscode/
.agents/
chroma_db/
bm25_index.pkl
netsol_scraped_data/
frontend/node_modules/
frontend/.next/
frontend/tsconfig.tsbuildinfo
query_logs.jsonl
llm_errors.jsonl
```

Reasons:

- `.env` contains API keys and must never be pushed.
- `chroma_db/`, `bm25_index.pkl`, and `netsol_scraped_data/` are large generated artifacts.
- `node_modules/` and `.next/` are reproducible build/dependency folders.
- logs and cache files are runtime-only.

## Setup

1. Install backend dependencies:

```powershell
pip install -r requirements.txt
```

2. Create environment file:

```powershell
Copy-Item .env.example .env
```

3. Add required keys in `.env`:

```text
GOOGLE_API_KEY=your_gemini_api_key_here
OPENROUTER_API_KEY=your_openrouter_api_key_here
```

4. Install frontend dependencies:

```powershell
cd frontend
npm install
```

## Run Locally

Start backend from the project root:

```powershell
.\scripts\start_backend.ps1
```

Start frontend:

```powershell
cd frontend
npm run dev
```

Open:

```text
http://127.0.0.1:3000
```

Backend health:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Frontend proxy health:

```powershell
Invoke-RestMethod http://127.0.0.1:3000/api/rag/health
```

## Backend Notes

On this Windows machine, the correct backend Python is usually:

```text
%LOCALAPPDATA%\Programs\Python\Python312\python.exe
```

Use `.\scripts\start_backend.ps1` instead of plain `python -m uvicorn ...` because another Python installation may not have `langgraph` and other RAG dependencies installed.

## Answer Quality Improvements

The generation pipeline is tuned for concise company answers:

- small context window for simple questions
- query-relevant sentence extraction before generation
- lower generation token budget
- backend answer length guard
- inline source citation enforcement
- unrequested technical/platform details removed from simple company answers

This prevents Gemini from producing long paragraphs when the user needs a short factual answer.

## Verification

Useful checks:

```powershell
python -m py_compile api.py config.py graph.py ingest.py nodes.py smoke_test_backend.py state.py
```

```powershell
cd frontend
npm run typecheck
npm run build
```

Backend smoke test:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" smoke_test_backend.py
```

## GitHub Hygiene

Before pushing, confirm ignored files are not staged:

```powershell
git status --short --ignored
```

Expected ignored local artifacts include `.env`, `chroma_db/`, `bm25_index.pkl`, `netsol_scraped_data/`, `frontend/node_modules/`, and `frontend/.next/`.
