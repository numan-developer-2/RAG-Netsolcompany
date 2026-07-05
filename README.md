# RAG-Netsolcompany

An end-to-end Retrieval-Augmented Generation (RAG) system built for querying a NETSOL Technologies knowledge base with grounded, concise, source-backed answers.

This project demonstrates a complete RAG workflow: large-scale web/PDF corpus preparation, hybrid retrieval, vector indexing, keyword search, LangGraph orchestration, LLM answer generation, validation, API serving, and a modern frontend experience.

It is structured as a portfolio-ready, production-style implementation: modular backend code, clear startup scripts, safe repository hygiene, documented operational checks, and a client-facing interface.

## Why This Project Matters

Most basic chatbot demos send a user question directly to an LLM. This project solves a more realistic business problem: answering company-specific questions from a private or scraped knowledge base while keeping responses accurate, concise, and traceable to sources.

The system is designed for:

- company knowledge assistants
- investor-relations document search
- product documentation Q&A
- sales/research enablement
- internal enterprise search
- source-grounded executive summaries

## What This RAG System Can Do

Example questions:

- What is LeasePak?
- What does NETSOL do?
- Which products are mentioned in the NETSOL corpus?
- Summarize available financial information from reports.
- Compare LeasePak and NFS Ascent using retrieved evidence.
- Answer from PDF filings and web pages with citations.

The backend is optimized to avoid generic, overly long LLM responses. Simple company questions return short factual answers with citations, while complex questions can use more context.

## Key Features

| Area | Implementation |
| --- | --- |
| Data ingestion | JSONL corpus loading, chunk filtering, metadata preservation |
| Vector search | ChromaDB persistent collections |
| Keyword search | BM25 lexical retrieval |
| Hybrid retrieval | Vector + BM25 result fusion |
| PDF support | Separate PDF chunks and source/page citations |
| Orchestration | LangGraph node-based RAG pipeline |
| LLM generation | Gemini via OpenRouter |
| Embeddings | Local Sentence Transformers embeddings |
| Validation | Hallucination guard and confidence metadata |
| API | FastAPI endpoints plus SSE streaming |
| Frontend | Next.js interface with responsive RAG workspace |
| Repository hygiene | Secrets, vector DB, scraped data, and build artifacts excluded from GitHub |

## RAG Pipeline

```text
User question
  -> FastAPI /query or /query/stream
  -> LangGraph state pipeline
  -> Query analysis
  -> Query rewriting
  -> Hybrid retrieval
      - ChromaDB semantic search
      - BM25 keyword search
  -> Multi-hop expansion when needed
  -> Reranking / top evidence selection
  -> Context compression
  -> Gemini answer generation
  -> Hallucination guard
  -> Final response formatting
  -> Frontend answer with sources and confidence
```

## LangGraph Nodes

The backend pipeline is organized into clear RAG stages:

- Query analyzer: detects intent, complexity, route, PDF priority, and query variants.
- Hybrid retriever: combines semantic vector search with BM25 search.
- Multi-hop retriever: expands complex questions using extracted entities.
- Reranker: selects the most relevant evidence passages.
- Generator: sends compressed source context to the LLM.
- Hallucination guard: validates answer support against retrieved context.
- Response formatter: returns concise final output with metadata.

## Dataset and Index Status

The project was built around a large NETSOL scraped corpus. The generated data/index files are intentionally kept local because they are too large for normal GitHub storage.

| Artifact | Local path | Size / Count |
| --- | --- | --- |
| Scraped NETSOL data | `netsol_scraped_data/` | ~1.64 GB |
| Scraped files | `netsol_scraped_data/` | 13,665 files |
| Main RAG chunks | `netsol_scraped_data/rag_chunks.jsonl` | ~131 MB |
| Page corpus records | `netsol_scraped_data/rag_corpus.jsonl` | ~109 MB |
| PDF chunks | `netsol_scraped_data/rag_pdf_chunks.jsonl` | ~24.6 MB |
| ChromaDB vector index | `chroma_db/` | ~956 MB |
| BM25 index | `bm25_index.pkl` | ~218 MB |

Indexed collection counts:

- Web chunks: `25,029`
- PDF chunks: `10,651`
- Metadata records: `6,315`

## Tech Stack

Backend:

- Python
- FastAPI
- LangGraph
- ChromaDB
- BM25 (`rank-bm25`)
- Sentence Transformers
- Gemini through OpenRouter
- Server-Sent Events streaming

Frontend:

- Next.js
- React
- TypeScript
- Framer Motion
- Lucide React icons

Models and retrieval:

- Embedding model: `sentence-transformers/all-MiniLM-L6-v2`
- LLM provider: OpenRouter
- LLM model: `google/gemini-2.5-flash`
- Vector database: ChromaDB
- Keyword retrieval: BM25

## Answer Quality Engineering

The system includes practical controls to make LLM answers useful in a business setting:

- concise answer prompt
- query-relevant context compression
- small context window for simple questions
- larger context window for analytical questions
- low generation token budget
- backend answer length guard
- source citation enforcement
- hallucination guard
- confidence scoring
- route and intent metadata
- removal of unrequested legacy technical details for simple company answers

Important config values:

```text
SIMPLE_CONTEXT_CHUNKS=3
COMPLEX_CONTEXT_CHUNKS=5
CONTEXT_CHARS_PER_CHUNK=900
ANSWER_MAX_WORDS=90
GENERATION_MAX_TOKENS=384
```

## API Endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/health` | Backend and index health |
| GET | `/stats` | Collection/artifact statistics |
| POST | `/query` | Standard RAG query |
| POST | `/query/stream` | Streaming RAG response |
| GET | `/sources/{chunk_id}` | Inspect source chunk text/metadata |

Example query:

```powershell
$body = @{
  query = "What is LeasePak?"
  persona = "general"
  chat_history = @()
} | ConvertTo-Json

Invoke-RestMethod http://127.0.0.1:8000/query -Method Post -ContentType "application/json" -Body $body
```

## Frontend Experience

The Next.js frontend provides a clean RAG workspace:

- persona selector
- suggested prompts
- health/status panel
- streaming answer state
- citations and sources
- confidence/verification metadata
- responsive desktop/mobile layout
- professional light UI with smooth transitions

## Repository Structure

```text
.
├── api.py
├── config.py
├── graph.py
├── ingest.py
├── nodes.py
├── state.py
├── smoke_test_backend.py
├── requirements.txt
├── RUNBOOK.md
├── scripts/
│   ├── start_backend.ps1
│   ├── start_frontend.ps1
│   └── start_all.ps1
└── frontend/
    ├── app/
    ├── package.json
    └── tsconfig.json
```

## Local-Only Files Not Uploaded

These files are intentionally ignored:

```text
.env
.vscode/
.agents/
BACKEND_COMPLETION_AUDIT.md
BACKEND_README.md
Netsol_RAG_Backend_MasterPrompt.extracted.txt
Netsol_RAG_Backend_MasterPrompt.pdf
Netsol_RAG_MasterDoc.docx
chroma_db/
bm25_index.pkl
netsol_scraped_data/
frontend/node_modules/
frontend/.next/
frontend/tsconfig.tsbuildinfo
query_logs.jsonl
llm_errors.jsonl
```

Why:

- `.env` contains private API keys.
- `chroma_db/`, `bm25_index.pkl`, and `netsol_scraped_data/` are large generated artifacts.
- `frontend/node_modules/` and `frontend/.next/` are reproducible.
- internal prompt/audit documents are kept local only.

## Setup

Install backend dependencies:

```powershell
pip install -r requirements.txt
```

Create environment file:

```powershell
Copy-Item .env.example .env
```

Add API keys:

```text
GOOGLE_API_KEY=your_gemini_api_key_here
OPENROUTER_API_KEY=your_openrouter_api_key_here
```

Install frontend dependencies:

```powershell
cd frontend
npm install
```

## Run Locally

Start backend:

```powershell
.\scripts\start_backend.ps1
```

Start frontend:

```powershell
cd frontend
npm run dev
```

Open the app:

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

## Verification

Backend compile check:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m py_compile api.py config.py graph.py ingest.py nodes.py smoke_test_backend.py state.py
```

Frontend checks:

```powershell
cd frontend
npm run typecheck
npm run build
```

Backend smoke test:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" smoke_test_backend.py
```

## Engineering Highlights

This project demonstrates:

- practical RAG backend architecture
- LangGraph orchestration
- hybrid semantic + lexical retrieval
- source-grounded generation
- local embedding fallback when API quota is limited
- clean API boundaries
- SSE response streaming
- RAG answer quality tuning
- frontend integration through a proxy route
- repository safety for secrets and large generated artifacts

## Notes for Reviewers

The codebase is complete and runnable when the local data/index artifacts are available. The public GitHub repository intentionally contains only source code, setup files, frontend code, and operational docs. Large generated indexes and private API keys are excluded for security and repository hygiene.
