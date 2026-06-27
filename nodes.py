"""Step 2 LangGraph nodes for the NETSOL RAG backend."""

from __future__ import annotations

import json
import pickle
import re
import time
from pathlib import Path
from typing import Any

import chromadb
import requests
from google import genai
from google.genai import types

from config import (
    BM25_INDEX,
    CHROMA_PATH,
    COLLECTION_META,
    COLLECTION_PDF,
    COLLECTION_WEB,
    ANSWER_MAX_WORDS,
    COMPLEX_CONTEXT_CHUNKS,
    CONTEXT_CHARS_PER_CHUNK,
    CROSS_ENCODER_MODEL,
    EMBEDDING_PROVIDER,
    EMBED_MODEL,
    FRONTEND_ORIGIN,
    GENERATION_MAX_TOKENS,
    GOOGLE_API_KEY,
    LLM_MODEL,
    LLM_PROVIDER,
    LLM_ERROR_LOGS,
    LOCAL_EMBED_MODEL,
    MAX_RETRIES,
    MIN_CHUNK_WORDS,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODEL,
    QUERY_LOGS,
    SIMPLE_CONTEXT_CHUNKS,
    TOP_K_RERANK,
    TOP_K_RETRIEVE,
    USE_CROSS_ENCODER,
)
from state import RAGState


chroma = chromadb.PersistentClient(path=CHROMA_PATH)
col_web = chroma.get_collection(COLLECTION_WEB)
col_pdf = chroma.get_collection(COLLECTION_PDF)
col_meta = chroma.get_collection(COLLECTION_META)

with Path(BM25_INDEX).open("rb") as f:
    _bm25_data = pickle.load(f)
bm25_engine = _bm25_data["bm25"]
bm25_chunks = _bm25_data["chunks"]

_gemini_client: genai.Client | None = None
_local_embedder: Any | None = None
_reranker: Any | None = None


ANALYZER_SYSTEM = """
You are a query analysis expert for the Netsol Technologies knowledge base.
Netsol makes enterprise software: LeasePak, Appex Now, NTFC, NFS Ascent
for asset finance, leasing, fleet management, and fintech.
Corpus: 13.7M words, web pages, investor documents, PDFs, case studies, blogs.

Analyze the user query and return ONLY valid JSON:
{
  "intent": "product_info|financial|technical|competitive|general",
  "complexity": "simple|complex|multi_hop",
  "route": "web_only|pdf_only|hybrid|competitive_mode",
  "persona": "executive|developer|sales|general",
  "rewritten_queries": ["semantic variant", "keyword variant", "broad variant"],
  "pdf_priority": true,
  "time_sensitive": false,
  "metadata_filters": {"source_type": "", "min_date": ""}
}

Rules:
- financial query OR keywords revenue, report, annual, quarterly, growth: pdf_priority true, route hybrid
- latest, recent, current: time_sensitive true
- competitor names: competitive_mode
- if query needs multiple document types: multi_hop
- expand acronyms and products in rewritten queries
"""


GENERATOR_SYSTEM = """
You are NETSOL RAG, a concise company-information assistant.
Answer using ONLY the provided context chunks.

Rules:
1. Be brief by default: 1 short paragraph or 2-4 bullets.
2. Maximum 90 words unless the user explicitly asks for detail, comparison, or analysis.
3. Start with the direct answer. Do not write introductions like "Based on the context".
4. Include only facts needed to answer the question. Do not add old technical/platform details unless asked.
5. Cite factual claims inline using [Source: URL] or [PDF: filename, p.N].
6. If context is weak, say "I found limited information on this in the indexed NETSOL corpus."
7. No markdown tables unless the user asks to compare.
8. Match the requested persona, but keep the answer concise.

Return ONLY valid JSON:
{
  "answer": "concise answer with inline citations",
  "sources_used": ["url or pdf:filename:pagenum"],
  "confidence": 0.75,
  "confidence_label": "High|Medium|Low",
  "answer_type": "factual|analytical|comparative|insufficient"
}
"""


GUARD_SYSTEM = """
You are a fact-verification specialist.
Verify that factual claims in the answer are supported by the context chunks.

Return ONLY valid JSON:
{
  "verdict": "PASS|FAIL|PARTIAL",
  "unsupported_claims": [],
  "flagged_count": 0,
  "action": "USE_AS_IS|REMOVE_FLAGGED|REGENERATE|ADD_CAVEAT"
}

Rules:
- PASS means action USE_AS_IS
- more than 2 unsupported claims means action REGENERATE
- 1 unsupported claim means action ADD_CAVEAT or REMOVE_FLAGGED
"""


def get_gemini_client() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        if not GOOGLE_API_KEY or GOOGLE_API_KEY == "your_gemini_api_key_here":
            raise RuntimeError("GOOGLE_API_KEY missing")
        _gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
    return _gemini_client


def get_local_embedder() -> Any:
    global _local_embedder
    if _local_embedder is None:
        from sentence_transformers import SentenceTransformer

        _local_embedder = SentenceTransformer(LOCAL_EMBED_MODEL)
    return _local_embedder


def get_reranker() -> Any | None:
    global _reranker
    if not USE_CROSS_ENCODER:
        return None
    if _reranker is None:
        from sentence_transformers import CrossEncoder

        _reranker = CrossEncoder(CROSS_ENCODER_MODEL)
    return _reranker


def embed_query(text: str) -> list[float]:
    """Embed queries using the same provider used for Chroma ingestion."""
    if EMBEDDING_PROVIDER == "local":
        vector = get_local_embedder().encode(
            [text],
            normalize_embeddings=True,
            show_progress_bar=False,
        )[0]
        return vector.tolist()

    response = get_gemini_client().models.embed_content(
        model=EMBED_MODEL,
        contents=[text],
        config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
    )
    return response.embeddings[0].values


def extract_json(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty JSON response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(1))


def llm_json(
    system: str,
    user: str,
    fallback: Any | None = None,
    max_tokens: int = 768,
) -> Any:
    """Call configured LLM and parse JSON, returning fallback on failure."""
    try:
        if LLM_PROVIDER == "openrouter" and OPENROUTER_API_KEY:
            last_exc: Exception | None = None
            for attempt in range(2):
                try:
                    response = requests.post(
                        f"{OPENROUTER_BASE_URL.rstrip('/')}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                            "Content-Type": "application/json",
                            "HTTP-Referer": FRONTEND_ORIGIN,
                            "X-Title": "NETSOL RAG",
                        },
                        json={
                            "model": OPENROUTER_MODEL,
                            "messages": [
                                {"role": "system", "content": system},
                                {"role": "user", "content": user},
                            ],
                            "temperature": 0.1,
                            "max_tokens": max_tokens,
                            "response_format": {"type": "json_object"},
                        },
                        timeout=75,
                    )
                    response.raise_for_status()
                    content = response.json()["choices"][0]["message"]["content"]
                    return extract_json(content)
                except Exception as exc:
                    last_exc = exc
                    if attempt == 0:
                        time.sleep(1.0)
            if last_exc:
                raise last_exc

        response = get_gemini_client().models.generate_content(
            model=LLM_MODEL,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                temperature=0.1,
                max_output_tokens=max_tokens,
            ),
        )
        return extract_json(response.text)
    except Exception as exc:
        log_llm_error(exc, system, user)
        if fallback is not None:
            return fallback
        raise


def log_llm_error(exc: Exception, system: str, user: str) -> None:
    try:
        record = {
            "timestamp": time.time(),
            "provider": LLM_PROVIDER,
            "model": OPENROUTER_MODEL if LLM_PROVIDER == "openrouter" else LLM_MODEL,
            "error_type": type(exc).__name__,
            "error": str(exc)[:1000],
            "system_preview": system[:120],
            "user_preview": user[:240],
        }
        with Path(LLM_ERROR_LOGS).open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def default_analysis(query: str, persona: str = "general") -> dict[str, Any]:
    q = query.lower()
    financial = any(word in q for word in ["revenue", "annual", "quarter", "financial", "growth", "report"])
    technical = any(word in q for word in ["api", "integration", "architecture", "technical", "cloud"])
    competitive = any(word in q for word in ["competitor", "compare", "salesforce", "oracle", "fiserv"])
    product = any(word in q for word in ["leasepak", "appex", "nfs ascent", "ntfc", "transcend", "flex"])
    latest = any(word in q for word in ["latest", "recent", "current", "new"])
    intent = (
        "financial" if financial else
        "technical" if technical else
        "competitive" if competitive else
        "product_info" if product else
        "general"
    )
    route = "competitive_mode" if competitive else "hybrid" if financial or technical else "web_only"
    return {
        "intent": intent,
        "complexity": "multi_hop" if financial or competitive else "simple",
        "route": route,
        "persona": persona or "general",
        "rewritten_queries": [
            query,
            f"NETSOL {query}",
            f"Netsol Technologies {query} asset finance leasing software",
        ],
        "pdf_priority": financial,
        "time_sensitive": latest,
        "metadata_filters": {},
    }


def source_label(chunk: dict[str, Any]) -> str:
    if chunk.get("source_type") == "pdf":
        return f"pdf:{chunk.get('pdf_name', '')}:p{chunk.get('page_number', 0)}"
    return str(chunk.get("source_url", ""))


def citation(chunk: dict[str, Any]) -> str:
    if chunk.get("source_type") == "pdf":
        return f"[PDF: {chunk.get('pdf_name', '')}, p.{chunk.get('page_number', 0)}]"
    return f"[Source: {chunk.get('source_url', '')}]"


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "in", "is", "it", "of", "on", "or", "that", "the", "this", "to", "what",
    "when", "where", "which", "who", "why", "with",
}


def query_terms(query: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9-]{2,}", query.lower())
        if token not in STOPWORDS
    }


def candidate_sentences(text: str) -> list[str]:
    clean = " ".join(str(text).split())
    clean = re.sub(r"\b(MARKET WIRE|PRNewswire|GlobeNewswire)\b", "", clean, flags=re.I)
    parts = re.split(r"(?<=[.!?])\s+", clean)
    sentences = []
    for part in parts:
        sentence = part.strip(" -")
        words = sentence.split()
        if not 8 <= len(words) <= 42:
            continue
        if re.match(r"^[A-Z][a-z]+ \d{1,2}, \d{4}\b", sentence):
            continue
        if sentence.count("--") > 1:
            continue
        sentences.append(sentence)
    return sentences


def fallback_points(query: str, chunks: list[dict[str, Any]], limit: int = 4) -> list[tuple[str, dict[str, Any]]]:
    terms = query_terms(query)
    scored: list[tuple[float, str, dict[str, Any]]] = []
    seen: set[str] = set()

    for chunk in chunks:
        for sentence in candidate_sentences(chunk.get("text", "")):
            normalized = sentence.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            sentence_terms = set(re.findall(r"[a-z0-9][a-z0-9-]{2,}", normalized))
            overlap = len(terms & sentence_terms)
            product_bonus = 1.5 if any(term in normalized for term in terms) else 0.0
            definition_bonus = 1.0 if re.search(r"\b(is|provides|offers|designed|system|platform|solution)\b", normalized) else 0.0
            score = overlap * 2.0 + product_bonus + definition_bonus + float(chunk.get("rrf_score", 0.0))
            if score > 1.0:
                scored.append((score, sentence, chunk))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [(sentence, chunk) for _, sentence, chunk in scored[:limit]]


def score_sentence(query: str, sentence: str) -> float:
    terms = query_terms(query)
    normalized = sentence.lower()
    sentence_terms = set(re.findall(r"[a-z0-9][a-z0-9-]{2,}", normalized))
    overlap = len(terms & sentence_terms)
    definition_bonus = 1.0 if re.search(
        r"\b(is|are|provides|offers|designed|system|platform|solution|company|founded|headquartered)\b",
        normalized,
    ) else 0.0
    return overlap * 2.0 + definition_bonus


def context_snippet(query: str, text: str, max_chars: int = CONTEXT_CHARS_PER_CHUNK) -> str:
    """Return the most query-relevant sentences from a chunk within a small budget."""
    sentences = candidate_sentences(text)
    if not sentences:
        return " ".join(str(text).split())[:max_chars]

    ranked = sorted(
        sentences,
        key=lambda sentence: score_sentence(query, sentence),
        reverse=True,
    )
    selected: list[str] = []
    used = 0
    for sentence in ranked:
        if score_sentence(query, sentence) <= 0 and selected:
            continue
        needed = len(sentence) + 1
        if used + needed > max_chars:
            continue
        selected.append(sentence)
        used += needed
        if len(selected) >= 4:
            break

    if not selected:
        return " ".join(str(text).split())[:max_chars]
    return " ".join(selected)[:max_chars]


def answer_word_limit(state: RAGState) -> int:
    explicit_detail = any(
        token in state.get("query", "").lower()
        for token in ["detail", "detailed", "explain", "compare", "analysis", "summarize"]
    )
    if explicit_detail or state.get("complexity") in {"complex", "multi_hop"}:
        return max(ANSWER_MAX_WORDS, 180)
    return ANSWER_MAX_WORDS


def enforce_answer_length(answer: str, max_words: int) -> str:
    words = answer.split()
    if len(words) <= max_words:
        return answer.strip()

    units: list[str] = []
    for line in answer.splitlines():
        stripped = line.strip()
        if stripped.startswith(("-", "*", "1.", "2.", "3.", "4.")):
            units.append(stripped)
        else:
            units.extend(part.strip() for part in re.split(r"(?<=[.!?])\s+", stripped) if part.strip())

    kept: list[str] = []
    used = 0
    for unit in units:
        unit_words = unit.split()
        if kept and used + len(unit_words) > max_words:
            break
        kept.append(unit)
        used += len(unit_words)

    if kept:
        shortened = "\n".join(kept)
    else:
        shortened = " ".join(words[:max_words]).rstrip(" ,;:")
    return shortened.strip() + "\n\nNote: Answer shortened for concision; open sources for more detail."


def technical_detail_requested(query: str) -> bool:
    return any(
        token in query.lower()
        for token in [
            "api", "architecture", "database", "databases", "technical", "integration",
            "operating system", "oracle", "sybase", "linux", "hp-ux", "solaris",
            "configured", "configuration",
        ]
    )


def remove_unrequested_technical_detail(answer: str, query: str) -> str:
    if technical_detail_requested(query):
        return answer

    noisy_terms = [
        "operating system", "operating systems", "database", "databases", "hp-ux",
        "sun/solaris", "solaris", "sybase", "oracle", "linux", "configured for",
    ]
    units = re.split(r"(?<=[.!?])\s+", answer.strip())
    kept = [
        unit for unit in units
        if not any(term in unit.lower() for term in noisy_terms)
    ]
    return " ".join(kept).strip() if kept else answer


def source_to_citation(source: str) -> str:
    if not source:
        return ""
    pdf_match = re.match(r"(?i)(?:pdf:\s*)?([^:\[]+\.pdf)(?::p?\.?(\d+))?", source.strip())
    if pdf_match:
        page = pdf_match.group(2)
        page_text = f", p.{page}" if page else ""
        return f"[PDF: {pdf_match.group(1).strip()}{page_text}]"

    url_match = re.search(r"https?://[^\s\])]+", source)
    if url_match:
        return f"[Source: {url_match.group(0)}]"
    return ""


def ensure_inline_citation(answer: str, sources: list[str], chunks: list[dict[str, Any]]) -> str:
    if re.search(r"\[(?:Source|PDF):", answer):
        return answer

    citation_text = ""
    for source in sources:
        citation_text = source_to_citation(str(source))
        if citation_text:
            break
    if not citation_text and chunks:
        citation_text = citation(chunks[0])

    if not citation_text:
        return answer

    lines = answer.splitlines()
    for index, line in enumerate(lines):
        if line.strip():
            lines[index] = line.rstrip() + f" {citation_text}"
            return "\n".join(lines)
    return answer


def polish_answer(state: RAGState, answer: str, sources: list[str]) -> str:
    polished = remove_unrequested_technical_detail(answer, state.get("query", ""))
    polished = enforce_answer_length(polished, answer_word_limit(state))
    return ensure_inline_citation(polished, sources, state.get("reranked_chunks", []))


def normalize_vector_result(
    doc: str,
    meta: dict[str, Any],
    distance: float,
    source_type: str,
    rank: int,
    score_multiplier: float = 1.0,
) -> dict[str, Any]:
    score = 1.0 / (60 + rank)
    score *= 1.0 + max(0.0, 1.0 - float(distance or 1.0))
    score *= 0.7 + 0.3 * float(meta.get("recency_score", 0.7) or 0.7)
    score *= score_multiplier
    return {
        "chunk_id": meta.get("chunk_id", ""),
        "text": doc,
        "source_url": meta.get("source_url", ""),
        "page_title": meta.get("page_title", ""),
        "source_type": meta.get("source_type", source_type),
        "pdf_name": meta.get("pdf_name", ""),
        "page_number": meta.get("page_number", 0),
        "rrf_score": score,
        "collection": source_type,
    }


def query_analyzer(state: RAGState) -> RAGState:
    fallback = default_analysis(state["query"], state.get("persona", "general"))
    result = llm_json(ANALYZER_SYSTEM, state["query"], fallback=fallback, max_tokens=512)
    if not isinstance(result, dict):
        result = fallback
    result.setdefault("persona", state.get("persona", "general"))
    if not result.get("rewritten_queries"):
        result["rewritten_queries"] = fallback["rewritten_queries"]
    return {**state, **result}


def hybrid_retriever(state: RAGState) -> RAGState:
    route = state.get("route", "hybrid")
    pdf_priority = bool(state.get("pdf_priority", False))
    variants = state.get("rewritten_queries") or [state["query"]]
    all_results: dict[str, dict[str, Any]] = {}

    collections: list[tuple[str, Any]] = []
    if route in {"web_only", "hybrid", "competitive_mode"}:
        collections.append(("web_page", col_web))
    if route in {"pdf_only", "hybrid"} or pdf_priority:
        collections.append(("pdf", col_pdf))
    if not collections:
        collections = [("web_page", col_web), ("pdf", col_pdf)]

    for variant in variants[:3]:
        q_emb = embed_query(variant)
        for source_type, collection in collections:
            boost = 1.4 if source_type == "pdf" and pdf_priority else 1.0
            res = collection.query(
                query_embeddings=[q_emb],
                n_results=12,
                include=["documents", "metadatas", "distances"],
            )
            docs = res.get("documents", [[]])[0]
            metas = res.get("metadatas", [[]])[0]
            distances = res.get("distances", [[]])[0]
            for rank, (doc, meta, distance) in enumerate(zip(docs, metas, distances), start=1):
                item = normalize_vector_result(doc, meta, distance, source_type, rank, boost)
                cid = item["chunk_id"]
                if not cid:
                    continue
                if cid in all_results:
                    all_results[cid]["rrf_score"] += item["rrf_score"]
                else:
                    all_results[cid] = item

    tokens = state["query"].lower().split()
    bm25_scores = bm25_engine.get_scores(tokens)
    max_bm25 = float(max(bm25_scores)) if len(bm25_scores) else 0.0
    top_idxs = sorted(range(len(bm25_scores)), key=lambda i: bm25_scores[i], reverse=True)[:20]
    for rank, idx in enumerate(top_idxs, start=1):
        score = float(bm25_scores[idx])
        if score <= 0:
            continue
        chunk = bm25_chunks[idx]
        cid = chunk.get("chunk_id", str(idx))
        bm25_rrf = (1.0 / (60 + rank)) + ((score / max_bm25) * 0.03 if max_bm25 else 0.0)
        item = {
            "chunk_id": cid,
            "text": chunk.get("text", ""),
            "source_url": chunk.get("source_url", ""),
            "page_title": chunk.get("page_title", ""),
            "source_type": chunk.get("source_type", ""),
            "pdf_name": chunk.get("pdf_name", ""),
            "page_number": chunk.get("page_number", 0),
            "rrf_score": bm25_rrf,
            "collection": "bm25",
        }
        if cid in all_results:
            all_results[cid]["rrf_score"] += bm25_rrf
        else:
            all_results[cid] = item

    clean = [
        item for item in all_results.values()
        if len(str(item.get("text", "")).split()) >= MIN_CHUNK_WORDS
    ]
    clean.sort(key=lambda item: item.get("rrf_score", 0.0), reverse=True)
    return {**state, "retrieved_chunks": clean[:TOP_K_RETRIEVE]}


def multi_hop_retriever(state: RAGState) -> RAGState:
    if state.get("complexity") != "multi_hop" or not state.get("retrieved_chunks"):
        return state

    context = " ".join(chunk["text"][:300] for chunk in state["retrieved_chunks"][:5])
    fallback_entities = re.findall(r"\b[A-Z][A-Za-z0-9&.-]{2,}\b", state["query"] + " " + context)[:3]
    entities = llm_json(
        "Extract key entities. Return ONLY JSON: {\"entities\":[\"...\"]}",
        f"Query: {state['query']}\nContext: {context}",
        fallback={"entities": fallback_entities},
        max_tokens=256,
    )
    if isinstance(entities, dict):
        entities = entities.get("entities", [])
    if not isinstance(entities, list):
        entities = fallback_entities

    existing = {chunk["chunk_id"] for chunk in state["retrieved_chunks"]}
    extra: list[dict[str, Any]] = []
    for entity in entities[:3]:
        q_emb = embed_query(str(entity))
        for source_type, collection in [("web_page", col_web), ("pdf", col_pdf)]:
            res = collection.query(
                query_embeddings=[q_emb],
                n_results=4,
                include=["documents", "metadatas", "distances"],
            )
            for rank, (doc, meta, distance) in enumerate(
                zip(res["documents"][0], res["metadatas"][0], res["distances"][0]),
                start=1,
            ):
                item = normalize_vector_result(doc, meta, distance, source_type, rank)
                cid = item["chunk_id"]
                if cid and cid not in existing:
                    existing.add(cid)
                    extra.append(item)

    combined = state["retrieved_chunks"] + extra
    combined.sort(key=lambda item: item.get("rrf_score", 0.0), reverse=True)
    return {**state, "retrieved_chunks": combined[:TOP_K_RETRIEVE]}


def reranker_node(state: RAGState) -> RAGState:
    chunks = state.get("retrieved_chunks", [])
    if not chunks:
        return {**state, "reranked_chunks": []}

    try:
        reranker = get_reranker()
        if reranker is None:
            raise RuntimeError("cross encoder disabled")
        pairs = [(state["query"], chunk["text"][:1024]) for chunk in chunks]
        scores = reranker.predict(pairs)
        ranked = sorted(zip(scores, chunks), key=lambda item: float(item[0]), reverse=True)
        top = [chunk for _, chunk in ranked[:TOP_K_RERANK]]
    except Exception:
        top = sorted(chunks, key=lambda chunk: chunk.get("rrf_score", 0.0), reverse=True)[:TOP_K_RERANK]
    return {**state, "reranked_chunks": top}


def fallback_answer(state: RAGState) -> dict[str, Any]:
    chunks = state.get("reranked_chunks", [])[:5]
    if not chunks:
        return {
            "answer": "I found limited information on this in the indexed NETSOL corpus.",
            "sources_used": [],
            "confidence": 0.2,
            "confidence_label": "Low",
            "answer_type": "insufficient",
        }

    points = fallback_points(state.get("query", ""), chunks)
    sources = []
    if points:
        lines = [
            "I found supporting information in the indexed NETSOL corpus. Key points:"
        ]
        for sentence, chunk in points:
            lines.append(f"- {sentence} {citation(chunk)}")
            sources.append(source_label(chunk))
    else:
        lines = [
            "I found limited information on this in the indexed NETSOL corpus. The closest retrieved sources are:"
        ]
        for chunk in chunks[:2]:
            title = chunk.get("page_title") or chunk.get("pdf_name") or "Retrieved source"
            lines.append(f"- {title} {citation(chunk)}")
            sources.append(source_label(chunk))

    sources = list(dict.fromkeys(sources))
    confidence = min(0.72, 0.4 + 0.08 * len(sources))
    return {
        "answer": "\n".join(lines),
        "sources_used": sources,
        "confidence": confidence,
        "confidence_label": "Medium" if confidence >= 0.5 else "Low",
        "answer_type": "factual",
    }


def generator_node(state: RAGState) -> RAGState:
    limit = COMPLEX_CONTEXT_CHUNKS if state.get("complexity") in {"complex", "multi_hop"} else SIMPLE_CONTEXT_CHUNKS
    chunks = state.get("reranked_chunks", [])[:limit]
    context_parts = []
    for i, chunk in enumerate(chunks, start=1):
        snippet = context_snippet(state["query"], chunk.get("text", ""))
        context_parts.append(f"--- Chunk {i} {citation(chunk)} ---\n{snippet}")
    history = "\n".join(
        f"{msg.get('role', 'user')}: {msg.get('content', '')}"
        for msg in state.get("chat_history", [])[-3:]
    )
    user_msg = (
        f"PERSONA: {state.get('persona', 'general')}\n"
        f"HISTORY:\n{history}\n"
        f"QUESTION: {state['query']}\n"
        f"CONTEXT:\n{chr(10).join(context_parts)}"
    )
    result = llm_json(
        GENERATOR_SYSTEM,
        user_msg,
        fallback=fallback_answer(state),
        max_tokens=GENERATION_MAX_TOKENS,
    )
    if not isinstance(result, dict):
        result = fallback_answer(state)

    sources = result.get("sources_used", [])
    if not isinstance(sources, list):
        sources = [str(sources)] if sources else []
    answer = polish_answer(state, str(result.get("answer", "")), sources)
    return {
        **state,
        "draft_answer": answer,
        "sources_used": sources,
        "confidence": float(result.get("confidence", 0.5) or 0.5),
        "answer_type": result.get("answer_type", "factual"),
    }


def hallucination_guard(state: RAGState) -> RAGState:
    retry = int(state.get("retry_count", 0) or 0)
    if retry >= MAX_RETRIES:
        caveat = "\n\nNote: Some details could not be fully verified against available sources."
        return {
            **state,
            "draft_answer": state.get("draft_answer", "") + caveat,
            "hallucination_verdict": "PARTIAL",
            "hallucination_action": "ADD_CAVEAT",
        }

    context = "\n".join(chunk["text"][:800] for chunk in state.get("reranked_chunks", []))
    result = llm_json(
        GUARD_SYSTEM,
        f"ANSWER:\n{state.get('draft_answer', '')}\n\nCONTEXT:\n{context}",
        fallback={"verdict": "PASS", "flagged_count": 0, "action": "USE_AS_IS"},
        max_tokens=512,
    )
    if not isinstance(result, dict):
        result = {"verdict": "PASS", "flagged_count": 0, "action": "USE_AS_IS"}
    action = result.get("action", "USE_AS_IS")
    verdict = result.get("verdict", "PASS")
    flagged_count = int(result.get("flagged_count", 0) or 0)
    if verdict == "FAIL" and action == "USE_AS_IS":
        action = "ADD_CAVEAT"
        verdict = "PARTIAL"
    if action == "ADD_CAVEAT" and "available sources" not in state.get("draft_answer", ""):
        state = {
            **state,
            "draft_answer": state.get("draft_answer", "")
            + "\n\nNote: Some details should be treated as source-bound to the retrieved context.",
            "confidence": min(float(state.get("confidence", 0.5) or 0.5), 0.7),
        }
    if flagged_count > 2 and retry < MAX_RETRIES - 1:
        action = "REGENERATE"
        verdict = "FAIL"
    elif flagged_count > 2:
        action = "ADD_CAVEAT"
        verdict = "PARTIAL"
        state = {
            **state,
            "draft_answer": state.get("draft_answer", "")
            + "\n\nNote: Some generated details could not be fully verified against the retrieved context.",
            "confidence": min(float(state.get("confidence", 0.5) or 0.5), 0.6),
        }
    return {
        **state,
        "hallucination_verdict": verdict,
        "hallucination_action": action,
        "retry_count": retry + (1 if action == "REGENERATE" else 0),
    }


def response_formatter(state: RAGState) -> RAGState:
    confidence = float(state.get("confidence", 0.5) or 0.5)
    confidence = max(0.0, min(1.0, confidence))
    label = "High" if confidence >= 0.75 else "Medium" if confidence >= 0.5 else "Low"
    sources = state.get("sources_used", [])
    answer = polish_answer(state, str(state.get("draft_answer", "")), sources)
    final = {
        "answer": answer,
        "sources": sources,
        "confidence": round(confidence, 2),
        "confidence_label": label,
        "persona": state.get("persona", "general"),
        "intent": state.get("intent", "general"),
        "route": state.get("route", "hybrid"),
        "chunks_retrieved": len(state.get("retrieved_chunks", [])),
        "chunks_used": len(state.get("reranked_chunks", [])),
        "answer_type": state.get("answer_type", "factual"),
        "verified": state.get("hallucination_verdict", "UNKNOWN"),
    }
    return {**state, "final_response": final}


def log_query(state: RAGState) -> None:
    try:
        record = {
            "timestamp": time.time(),
            "query": state.get("query"),
            "route": state.get("route"),
            "intent": state.get("intent"),
            "confidence": state.get("confidence"),
            "chunks_retrieved": len(state.get("retrieved_chunks", [])),
            "processing_time": state.get("processing_time"),
        }
        with Path(QUERY_LOGS).open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass
