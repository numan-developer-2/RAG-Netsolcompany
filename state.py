"""LangGraph state schema for the NETSOL RAG backend."""

from typing import Any, Optional, TypedDict


class RAGState(TypedDict, total=False):
    # Input
    query: str
    persona: str
    chat_history: list[dict[str, Any]]

    # Query analysis
    intent: str
    complexity: str
    route: str
    rewritten_queries: list[str]
    pdf_priority: bool
    time_sensitive: bool
    metadata_filters: dict[str, Any]

    # Retrieval and reranking
    retrieved_chunks: list[dict[str, Any]]
    reranked_chunks: list[dict[str, Any]]

    # Generation
    draft_answer: str
    sources_used: list[str]
    confidence: float
    answer_type: str

    # Validation
    hallucination_verdict: str
    hallucination_action: str
    retry_count: int

    # Final output
    final_response: dict[str, Any]
    processing_time: float
    error: Optional[str]
