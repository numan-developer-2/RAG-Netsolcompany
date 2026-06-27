"""LangGraph wiring and public query runner for NETSOL RAG."""

from __future__ import annotations

import time

from langgraph.graph import END, StateGraph

from nodes import (
    generator_node,
    hallucination_guard,
    hybrid_retriever,
    log_query,
    multi_hop_retriever,
    query_analyzer,
    reranker_node,
    response_formatter,
)
from state import RAGState


def route_complexity(state: RAGState) -> str:
    return "multi_hop" if state.get("complexity") == "multi_hop" else "rerank"


def route_guard(state: RAGState) -> str:
    action = state.get("hallucination_action", "USE_AS_IS")
    if action == "REGENERATE" and int(state.get("retry_count", 0) or 0) < 2:
        return "retry"
    return "format"


graph_builder = StateGraph(RAGState)
graph_builder.add_node("query_analyzer", query_analyzer)
graph_builder.add_node("hybrid_retriever", hybrid_retriever)
graph_builder.add_node("multi_hop_retriever", multi_hop_retriever)
graph_builder.add_node("reranker", reranker_node)
graph_builder.add_node("generator", generator_node)
graph_builder.add_node("hallucination_guard", hallucination_guard)
graph_builder.add_node("response_formatter", response_formatter)

graph_builder.set_entry_point("query_analyzer")
graph_builder.add_edge("query_analyzer", "hybrid_retriever")
graph_builder.add_conditional_edges(
    "hybrid_retriever",
    route_complexity,
    {"multi_hop": "multi_hop_retriever", "rerank": "reranker"},
)
graph_builder.add_edge("multi_hop_retriever", "reranker")
graph_builder.add_edge("reranker", "generator")
graph_builder.add_edge("generator", "hallucination_guard")
graph_builder.add_conditional_edges(
    "hallucination_guard",
    route_guard,
    {"retry": "hybrid_retriever", "format": "response_formatter"},
)
graph_builder.add_edge("response_formatter", END)

rag_graph = graph_builder.compile()


def initial_state(query: str, persona: str = "general", chat_history: list | None = None) -> RAGState:
    return {
        "query": query,
        "persona": persona or "general",
        "chat_history": chat_history or [],
        "rewritten_queries": [],
        "pdf_priority": False,
        "time_sensitive": False,
        "metadata_filters": {},
        "retrieved_chunks": [],
        "reranked_chunks": [],
        "draft_answer": "",
        "sources_used": [],
        "confidence": 0.0,
        "answer_type": "",
        "hallucination_verdict": "",
        "hallucination_action": "",
        "retry_count": 0,
        "final_response": {},
        "processing_time": 0.0,
        "error": None,
    }


def run_query(query: str, persona: str = "general", chat_history: list | None = None) -> dict:
    start = time.time()
    state = initial_state(query=query, persona=persona, chat_history=chat_history)
    try:
        output = rag_graph.invoke(state)
        final = output.get("final_response", {})
        final["processing_time"] = round(time.time() - start, 2)
        output["processing_time"] = final["processing_time"]
        log_query(output)
        return final
    except Exception as exc:
        return {
            "answer": "I could not complete the query because the backend pipeline hit an error.",
            "sources": [],
            "confidence": 0.0,
            "confidence_label": "Low",
            "persona": persona or "general",
            "intent": "error",
            "route": "error",
            "chunks_retrieved": 0,
            "chunks_used": 0,
            "answer_type": "error",
            "verified": "ERROR",
            "processing_time": round(time.time() - start, 2),
            "error": str(exc),
        }
