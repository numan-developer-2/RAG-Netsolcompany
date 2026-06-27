# NETSOL RAG System

Source-grounded Retrieval-Augmented Generation project for querying the NETSOL company corpus.

## Stack

- Backend: FastAPI, LangGraph, ChromaDB, BM25
- LLM: OpenRouter Gemini (`google/gemini-2.5-flash`)
- Embeddings: `sentence-transformers/all-MiniLM-L6-v2`
- Frontend: Next.js, React, Framer Motion

## Run

From the project root:

```powershell
.\scripts\start_backend.ps1
```

In another terminal:

```powershell
cd frontend
npm run dev
```

Open:

```text
http://127.0.0.1:3000
```

## Notes

- Copy `.env.example` to `.env` and add API keys before running LLM-backed queries.
- Large generated artifacts are intentionally not committed:
  - `chroma_db/`
  - `bm25_index.pkl`
  - `netsol_scraped_data/`
  - `frontend/node_modules/`
  - `frontend/.next/`

See `BACKEND_README.md` and `RUNBOOK.md` for backend details and operational commands.
