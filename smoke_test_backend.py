"""Smoke-test the NETSOL RAG backend.

Run after starting the API server:
    python smoke_test_backend.py
"""

from __future__ import annotations

import json
import sys
from typing import Any

import requests


BASE_URL = "http://127.0.0.1:8000"


def print_json(label: str, data: Any) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(data, indent=2, ensure_ascii=False)[:2000])


def assert_ok(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    health = requests.get(f"{BASE_URL}/health", timeout=30)
    assert_ok(health.ok, f"/health failed: {health.status_code} {health.text}")
    health_json = health.json()
    print_json("health", health_json)
    assert_ok(health_json["web_chunks"] > 0, "web collection is empty")
    assert_ok(health_json["pdf_chunks"] > 0, "pdf collection is empty")
    assert_ok(health_json["bm25_ready"], "BM25 index missing")

    query_payload = {
        "query": "What is LeasePak?",
        "persona": "general",
        "chat_history": [],
    }
    query = requests.post(f"{BASE_URL}/query", json=query_payload, timeout=180)
    assert_ok(query.ok, f"/query failed: {query.status_code} {query.text}")
    query_json = query.json()
    print_json("query", {
        "confidence": query_json.get("confidence"),
        "verified": query_json.get("verified"),
        "chunks_retrieved": query_json.get("chunks_retrieved"),
        "sources": query_json.get("sources", [])[:3],
        "answer_preview": query_json.get("answer", "")[:500],
    })
    assert_ok(query_json.get("chunks_retrieved", 0) > 0, "query retrieved no chunks")
    assert_ok(query_json.get("answer"), "query returned empty answer")

    stream = requests.post(
        f"{BASE_URL}/query/stream",
        json={"query": "Who is NETSOL CEO?", "persona": "general", "chat_history": []},
        stream=True,
        timeout=180,
    )
    assert_ok(stream.ok, f"/query/stream failed: {stream.status_code} {stream.text}")
    stages = []
    token_count = 0
    complete = False
    for line in stream.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        data = json.loads(line[6:])
        stage = data.get("stage")
        if stage not in stages:
            stages.append(stage)
        if stage == "token":
            token_count += 1
        if stage == "complete":
            complete = True
            break
    print_json("stream", {"stages": stages, "token_count": token_count, "complete": complete})
    assert_ok(complete, "stream never completed")
    assert_ok(token_count > 0, "stream emitted no tokens")

    print("\nBackend smoke test passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\nBackend smoke test failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
