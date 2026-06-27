"""FastAPI server for the NETSOL RAG backend."""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from config import (
    BM25_INDEX,
    EMBEDDING_PROVIDER,
    FRONTEND_ORIGIN,
    LLM_MODEL,
    LLM_PROVIDER,
    LOCAL_EMBED_MODEL,
    OPENROUTER_MODEL,
    PDF_CHUNKS_JSONL,
    backend_artifact_status,
)
from graph import run_query
from nodes import col_meta, col_pdf, col_web


ALLOWED_ORIGINS = [
    origin
    for origin in {
        FRONTEND_ORIGIN,
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    }
    if origin
]

app = FastAPI(title="Netsol RAG API", version="2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class QueryRequest(BaseModel):
    query: str
    persona: str = "general"
    chat_history: list[dict[str, Any]] = Field(default_factory=list)


@app.post("/query")
async def query_endpoint(req: QueryRequest) -> dict:
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query is required")
    result = run_query(req.query, req.persona, req.chat_history)
    if result.get("answer_type") == "error":
        raise HTTPException(status_code=500, detail=result)
    return result


@app.post("/query/stream")
async def stream_endpoint(req: QueryRequest) -> StreamingResponse:
    async def generate():
        if not req.query.strip():
            yield f"data: {json.dumps({'error': 'query is required'})}\n\n"
            return

        try:
            yield f"data: {json.dumps({'stage': 'analyzing'})}\n\n"
            result = run_query(req.query, req.persona, req.chat_history)
            if result.get("answer_type") == "error":
                yield f"data: {json.dumps({'error': result.get('answer', 'RAG backend failed')})}\n\n"
                return

            yield f"data: {json.dumps({'stage': 'streaming'})}\n\n"
            answer = result.get("answer", "")
            if answer:
                yield f"data: {json.dumps({'stage': 'token', 'token': answer})}\n\n"
            yield f"data: {json.dumps({'stage': 'complete', 'result': result})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/health")
async def health() -> dict:
    artifacts = backend_artifact_status()
    return {
        "status": "ok",
        "web_chunks": col_web.count(),
        "pdf_chunks": col_pdf.count(),
        "metadata_records": col_meta.count(),
        "bm25_ready": artifacts["bm25_index"]["exists"],
        "pdf_cache_ready": artifacts["pdf_chunks"]["exists"],
        "embedding_provider": EMBEDDING_PROVIDER,
        "embedding_model": LOCAL_EMBED_MODEL if EMBEDDING_PROVIDER == "local" else "gemini",
        "llm_provider": LLM_PROVIDER,
        "model": OPENROUTER_MODEL if LLM_PROVIDER == "openrouter" else LLM_MODEL,
    }


@app.get("/stats")
async def stats() -> dict:
    return {
        "collections": {
            "web": col_web.count(),
            "pdf": col_pdf.count(),
            "metadata": col_meta.count(),
        },
        "artifacts": backend_artifact_status(),
        "bm25_index": BM25_INDEX,
        "pdf_chunks_jsonl": PDF_CHUNKS_JSONL,
        "llm_provider": LLM_PROVIDER,
        "llm_model": OPENROUTER_MODEL if LLM_PROVIDER == "openrouter" else LLM_MODEL,
        "embedding_provider": EMBEDDING_PROVIDER,
        "embedding_model": LOCAL_EMBED_MODEL if EMBEDDING_PROVIDER == "local" else "gemini",
    }


@app.get("/sources/{chunk_id}")
async def source_lookup(chunk_id: str) -> dict:
    for collection_name, collection in [
        ("web", col_web),
        ("pdf", col_pdf),
        ("metadata", col_meta),
    ]:
        result = collection.get(
            ids=[chunk_id],
            include=["documents", "metadatas"],
        )
        if result.get("ids"):
            return {
                "collection": collection_name,
                "chunk_id": result["ids"][0],
                "text": result["documents"][0],
                "metadata": result["metadatas"][0],
            }
    raise HTTPException(status_code=404, detail="chunk not found")
