"""Step 1 ingestion pipeline for the NETSOL RAG backend.

Builds three ChromaDB collections and a BM25 index:
- netsol_web_pages from netsol_scraped_data/rag_chunks.jsonl
- netsol_pdfs from page-aware PDF chunks generated from rag_corpus.jsonl refs
- netsol_metadata from netsol_scraped_data/rag_corpus.jsonl

Use:
    python ingest.py --dry-run
    python ingest.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import chromadb
import jsonlines
from google import genai
from google.genai import types
from pypdf import PdfReader
from rank_bm25 import BM25Okapi
from tqdm import tqdm

from config import (
    BATCH_SIZE,
    BM25_INDEX,
    CHROMA_PATH,
    CHUNK_JSONL,
    COLLECTION_META,
    COLLECTION_PDF,
    COLLECTION_WEB,
    CORPUS_JSONL,
    EMBEDDING_PROVIDER,
    EMBED_MODEL,
    GOOGLE_API_KEY,
    LOCAL_EMBED_MODEL,
    MIN_CHUNK_WORDS,
    PDF_CHUNKS_JSONL,
    validate_step0_paths,
)


SKIP_URLS = (
    "/tag/",
    "/category/",
    "/page/",
    "/feed/",
    "/wp-admin/",
    "#",
    "javascript:",
    "mailto:",
)
PDF_CHUNK_WORDS = 280
PDF_CHUNK_OVERLAP = 40

_gemini_client: genai.Client | None = None
_local_embedder: Any | None = None


def get_gemini_client() -> genai.Client:
    if not GOOGLE_API_KEY or GOOGLE_API_KEY == "your_gemini_api_key_here":
        raise RuntimeError("GOOGLE_API_KEY is missing in .env")

    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
    return _gemini_client


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed stored chunks with RETRIEVAL_DOCUMENT task type."""
    if not texts:
        return []
    if EMBEDDING_PROVIDER == "local":
        return embed_documents_local(texts)

    response = get_gemini_client().models.embed_content(
        model=EMBED_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
    )
    embeddings = [embedding.values for embedding in response.embeddings]
    if len(embeddings) != len(texts):
        if len(texts) == 1:
            raise RuntimeError("Gemini returned no embedding for a single input.")
        raise RuntimeError(
            f"Gemini returned {len(embeddings)} embeddings for {len(texts)} inputs. "
            "Reduce BATCH_SIZE or switch to a local embedding provider."
        )
    return embeddings


def get_local_embedder() -> Any:
    global _local_embedder
    if _local_embedder is None:
        from sentence_transformers import SentenceTransformer

        _local_embedder = SentenceTransformer(LOCAL_EMBED_MODEL)
    return _local_embedder


def embed_documents_local(texts: list[str]) -> list[list[float]]:
    """Embed stored chunks with a local sentence-transformers model."""
    model = get_local_embedder()
    vectors = model.encode(
        texts,
        batch_size=min(max(BATCH_SIZE, 1), 128),
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return vectors.tolist()


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def stable_id(value: str, length: int = 16) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:length]


def extract_date(value: str) -> str:
    """Return YYYY-MM-DD from URLs or filenames when available."""
    match = re.search(r"(20\d{2}|19\d{2})[-_/](\d{2})[-_/](\d{2})", value or "")
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    match = re.search(r"(20\d{2}|19\d{2})", value or "")
    if match:
        return f"{match.group(1)}-01-01"
    return ""


def recency_score(date_str: str) -> float:
    """Return a 0.5 old-to-1.0 recent score for recency boosting."""
    try:
        parsed = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        days_old = (datetime.now(timezone.utc) - parsed).days
        return round(max(0.5, 1.0 - (days_old / 365) * 0.5), 3)
    except Exception:
        return 0.7


def normalize_web_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    source_url = str(chunk.get("source_url") or chunk.get("url") or "")
    title = str(chunk.get("page_title") or chunk.get("title") or "")
    scraped_date = str(chunk.get("scraped_date") or extract_date(source_url))
    return {
        "chunk_id": str(chunk.get("chunk_id", "")),
        "document_id": str(chunk.get("document_id", "")),
        "chunk_index": int(chunk.get("chunk_index", 0) or 0),
        "source_type": "web_page",
        "source_url": source_url,
        "page_title": title,
        "parent_url": str(chunk.get("parent_url", "")),
        "breadcrumb": str(chunk.get("breadcrumb", "")),
        "host": str(chunk.get("host", "")),
        "level": int(chunk.get("level", 0) or 0),
        "scraped_date": scraped_date,
        "recency_score": recency_score(scraped_date),
        "word_count": int(chunk.get("word_count", 0) or 0),
        "content_category": str(chunk.get("content_category") or chunk.get("section") or ""),
        "text": clean_text(str(chunk.get("text", ""))),
    }


def should_skip(chunk: dict[str, Any]) -> bool:
    if int(chunk.get("word_count", 0) or 0) < MIN_CHUNK_WORDS:
        return True
    if len(str(chunk.get("text", "")).strip()) < 100:
        return True
    source_url = str(chunk.get("source_url") or chunk.get("url") or "")
    if any(pattern in source_url for pattern in SKIP_URLS):
        return True
    if not str(chunk.get("page_title") or chunk.get("title") or "").strip():
        return True
    return False


def chroma_metadata(chunk: dict[str, Any]) -> dict[str, str | int | float | bool]:
    """Build Chroma-safe metadata from normalized chunks."""
    source_type = str(chunk.get("source_type", "web_page"))
    meta: dict[str, str | int | float | bool] = {
        "chunk_id": str(chunk.get("chunk_id", "")),
        "source_type": source_type,
        "source_url": str(chunk.get("source_url", ""))[:500],
        "page_title": str(chunk.get("page_title", ""))[:300],
        "parent_url": str(chunk.get("parent_url", ""))[:500],
        "host": str(chunk.get("host", ""))[:120],
        "level": int(chunk.get("level", 0) or 0),
        "scraped_date": str(chunk.get("scraped_date", ""))[:40],
        "recency_score": float(chunk.get("recency_score", 0.7) or 0.7),
        "word_count": int(chunk.get("word_count", 0) or 0),
        "content_category": str(chunk.get("content_category", ""))[:120],
    }
    if source_type == "pdf":
        meta.update(
            {
                "pdf_name": str(chunk.get("pdf_name", ""))[:260],
                "page_number": int(chunk.get("page_number", 0) or 0),
                "section_heading": str(chunk.get("section_heading", ""))[:300],
                "doc_category": str(chunk.get("doc_category", ""))[:120],
                "local_path": str(chunk.get("local_path", ""))[:500],
            }
        )
    return meta


def iter_web_chunks(limit: int | None = None) -> Iterable[dict[str, Any]]:
    yielded = 0
    with jsonlines.open(CHUNK_JSONL) as reader:
        for raw in reader:
            chunk = normalize_web_chunk(raw)
            if should_skip(chunk):
                continue
            yield chunk
            yielded += 1
            if limit is not None and yielded >= limit:
                return


def iter_corpus_records(limit: int | None = None) -> Iterable[dict[str, Any]]:
    yielded = 0
    with jsonlines.open(CORPUS_JSONL) as reader:
        for rec in reader:
            if rec.get("title") and rec.get("url"):
                yield rec
                yielded += 1
                if limit is not None and yielded >= limit:
                    return


def iter_pdf_refs() -> Iterable[dict[str, Any]]:
    seen: set[str] = set()
    with jsonlines.open(CORPUS_JSONL) as reader:
        for rec in reader:
            for doc in rec.get("documents") or []:
                saved_path = str(doc.get("saved_path") or "")
                if not saved_path or saved_path in seen:
                    continue
                seen.add(saved_path)
                yield {
                    "saved_path": saved_path,
                    "source_url": str(doc.get("url") or ""),
                    "pdf_name": Path(saved_path).name,
                    "parent_url": str(rec.get("url") or ""),
                    "page_title": str(rec.get("title") or ""),
                    "section_heading": str(rec.get("title") or ""),
                    "doc_category": str(rec.get("section") or ""),
                    "host": str(rec.get("host") or ""),
                    "level": int(rec.get("level", 0) or 0),
                    "declared_page_count": int(doc.get("page_count", 0) or 0),
                }


def chunk_words(words: list[str], chunk_words_count: int, overlap: int) -> Iterable[tuple[int, int, str]]:
    start = 0
    total = len(words)
    while start < total:
        end = min(start + chunk_words_count, total)
        yield start, end, " ".join(words[start:end])
        if end >= total:
            break
        start = max(0, end - overlap)


def build_pdf_chunks_cache(force: bool = False, limit_files: int | None = None) -> int:
    """Generate page-aware PDF chunks because raw rag_chunks lacks PDF metadata."""
    out_path = Path(PDF_CHUNKS_JSONL)
    if out_path.exists() and not force:
        with out_path.open("r", encoding="utf-8") as f:
            return sum(1 for _ in f)

    refs = list(iter_pdf_refs())
    if limit_files is not None:
        refs = refs[:limit_files]

    written = 0
    errors = 0
    with jsonlines.open(out_path, mode="w") as writer:
        for ref in tqdm(refs, desc="PDF parse"):
            path = Path(ref["saved_path"])
            if not path.exists():
                errors += 1
                continue
            try:
                reader = PdfReader(str(path))
                pdf_date = extract_date(ref["source_url"] or path.name)
                for page_index, page in enumerate(reader.pages, start=1):
                    text = clean_text(page.extract_text() or "")
                    words = text.split()
                    if len(words) < MIN_CHUNK_WORDS:
                        continue
                    for chunk_index, start, end, chunk_text in (
                        (idx, start, end, body)
                        for idx, (start, end, body) in enumerate(
                            chunk_words(words, PDF_CHUNK_WORDS, PDF_CHUNK_OVERLAP)
                        )
                    ):
                        chunk_id = (
                            f"pdf_{stable_id(str(path))}_p{page_index:04d}_"
                            f"c{chunk_index:03d}"
                        )
                        writer.write(
                            {
                                "chunk_id": chunk_id,
                                "document_id": f"pdf_{stable_id(str(path))}",
                                "chunk_index": chunk_index,
                                "source_type": "pdf",
                                "source_url": ref["source_url"],
                                "page_title": ref["page_title"],
                                "parent_url": ref["parent_url"],
                                "host": ref["host"],
                                "level": ref["level"],
                                "scraped_date": pdf_date,
                                "recency_score": recency_score(pdf_date),
                                "word_start": start,
                                "word_end": end,
                                "word_count": len(chunk_text.split()),
                                "content_category": ref["doc_category"],
                                "pdf_name": ref["pdf_name"],
                                "page_number": page_index,
                                "section_heading": ref["section_heading"],
                                "doc_category": ref["doc_category"],
                                "local_path": str(path),
                                "text": chunk_text,
                            }
                        )
                        written += 1
            except Exception as exc:
                errors += 1
                print(f"PDF parse error: {path.name}: {exc}")

    print(f"PDF chunk cache written: {written} chunks ({errors} files skipped/failed)")
    return written


def iter_pdf_chunks(limit: int | None = None) -> Iterable[dict[str, Any]]:
    if not Path(PDF_CHUNKS_JSONL).exists():
        build_pdf_chunks_cache()

    yielded = 0
    with jsonlines.open(PDF_CHUNKS_JSONL) as reader:
        for chunk in reader:
            if should_skip(chunk):
                continue
            yield chunk
            yielded += 1
            if limit is not None and yielded >= limit:
                return


def upsert_embedded_batches(
    collection: chromadb.Collection,
    chunks: list[dict[str, Any]],
    desc: str,
) -> None:
    chunks = skip_existing_chunks(collection, chunks, desc)
    for i in tqdm(range(0, len(chunks), BATCH_SIZE), desc=desc):
        batch = chunks[i : i + BATCH_SIZE]
        texts = [chunk["text"] for chunk in batch]
        try:
            embeddings = embed_documents(texts)
            collection.upsert(
                ids=[chunk["chunk_id"] for chunk in batch],
                embeddings=embeddings,
                documents=texts,
                metadatas=[chroma_metadata(chunk) for chunk in batch],
            )
            time.sleep(0.05)
        except Exception as exc:
            if is_quota_error(exc):
                raise RuntimeError(
                    "Gemini embedding quota exhausted. Resume later with python ingest.py."
                ) from exc
            print(f"{desc} batch {i} failed: {exc}; retrying one by one")
            time.sleep(5)
            for chunk in batch:
                try:
                    embedding = embed_documents([chunk["text"]])
                    collection.upsert(
                        ids=[chunk["chunk_id"]],
                        embeddings=embedding,
                        documents=[chunk["text"]],
                        metadatas=[chroma_metadata(chunk)],
                    )
                    time.sleep(0.25)
                except Exception as inner_exc:
                    if is_quota_error(inner_exc):
                        raise RuntimeError(
                            "Gemini embedding quota exhausted. Resume later with python ingest.py."
                        ) from inner_exc
                    print(f"Skip {chunk['chunk_id']}: {inner_exc}")


def is_quota_error(exc: Exception) -> bool:
    message = str(exc)
    return "429" in message or "RESOURCE_EXHAUSTED" in message


def skip_existing_chunks(
    collection: chromadb.Collection,
    chunks: list[dict[str, Any]],
    desc: str,
) -> list[dict[str, Any]]:
    """Skip IDs already present in Chroma so interrupted ingestions resume."""
    if not chunks or collection.count() == 0:
        return chunks

    existing: set[str] = set()
    ids = [chunk["chunk_id"] for chunk in chunks]
    for i in range(0, len(ids), 5000):
        try:
            found = collection.get(ids=ids[i : i + 5000])
            existing.update(found.get("ids", []))
        except Exception:
            continue

    if existing:
        print(f"{desc}: skipping {len(existing)} existing chunks")
    return [chunk for chunk in chunks if chunk["chunk_id"] not in existing]


def get_collection(name: str) -> chromadb.Collection:
    chroma = chromadb.PersistentClient(path=CHROMA_PATH)
    return chroma.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )


def reset_collections() -> None:
    chroma = chromadb.PersistentClient(path=CHROMA_PATH)
    for name in (COLLECTION_WEB, COLLECTION_PDF, COLLECTION_META):
        try:
            chroma.delete_collection(name)
            print(f"Deleted collection: {name}")
        except Exception:
            pass


def ingest_web_pages(limit: int | None = None) -> int:
    collection = get_collection(COLLECTION_WEB)
    print(f"Existing web chunks: {collection.count()}")
    chunks = list(iter_web_chunks(limit=limit))
    print(f"Web chunks after filter: {len(chunks)}")
    upsert_embedded_batches(collection, chunks, "Web Pages")
    print(f"Web DONE. Total: {collection.count()}")
    return collection.count()


def ingest_pdfs(limit: int | None = None) -> int:
    collection = get_collection(COLLECTION_PDF)
    print(f"Existing PDF chunks: {collection.count()}")
    chunks = list(iter_pdf_chunks(limit=limit))
    print(f"PDF chunks after filter: {len(chunks)}")
    upsert_embedded_batches(collection, chunks, "PDFs")
    print(f"PDF DONE. Total: {collection.count()}")
    return collection.count()


def ingest_metadata(limit: int | None = None) -> int:
    collection = get_collection(COLLECTION_META)
    print(f"Existing metadata records: {collection.count()}")
    records = list(iter_corpus_records(limit=limit))
    print(f"Metadata records after filter: {len(records)}")

    chunks: list[dict[str, Any]] = []
    for rec in records:
        url = str(rec.get("url", ""))
        title = str(rec.get("title", ""))
        text = clean_text(f"{title} {url} {rec.get('section', '')}")
        chunks.append(
            {
                "chunk_id": f"meta_{stable_id(url or title)}",
                "source_type": "metadata",
                "source_url": url,
                "page_title": title,
                "parent_url": str(rec.get("parent_url", "")),
                "host": str(rec.get("host", "")),
                "level": int(rec.get("level", 0) or 0),
                "scraped_date": extract_date(url),
                "recency_score": recency_score(extract_date(url)),
                "word_count": max(len(text.split()), 1),
                "content_category": str(rec.get("section", "")),
                "text": text,
            }
        )

    upsert_embedded_batches(collection, chunks, "Metadata")
    print(f"Metadata DONE. Total: {collection.count()}")
    return collection.count()


def build_bm25_index(limit: int | None = None) -> int:
    all_chunks: list[dict[str, Any]] = []
    for chunk in iter_web_chunks(limit=limit):
        all_chunks.append(
            {
                "chunk_id": chunk["chunk_id"],
                "text": chunk["text"],
                "source_url": chunk["source_url"],
                "source_type": "web_page",
                "page_title": chunk["page_title"],
                "pdf_name": "",
                "page_number": 0,
            }
        )
    for chunk in iter_pdf_chunks(limit=limit):
        all_chunks.append(
            {
                "chunk_id": chunk["chunk_id"],
                "text": chunk["text"],
                "source_url": chunk["source_url"],
                "source_type": "pdf",
                "page_title": chunk["page_title"],
                "pdf_name": chunk.get("pdf_name", ""),
                "page_number": chunk.get("page_number", 0),
            }
        )

    tokenized = [chunk["text"].lower().split() for chunk in all_chunks]
    bm25 = BM25Okapi(tokenized)
    with Path(BM25_INDEX).open("wb") as f:
        pickle.dump({"bm25": bm25, "chunks": all_chunks}, f)
    print(f"BM25 index built: {len(all_chunks)} chunks saved")
    return len(all_chunks)


def dry_run() -> None:
    missing = validate_step0_paths()
    if missing:
        raise RuntimeError(f"Missing required Step 0 paths: {missing}")

    web_count = sum(1 for _ in iter_web_chunks())
    corpus_count = sum(1 for _ in iter_corpus_records())
    pdf_refs = list(iter_pdf_refs())
    existing_pdf_cache = Path(PDF_CHUNKS_JSONL).exists()
    pdf_cache_count = 0
    if existing_pdf_cache:
        with Path(PDF_CHUNKS_JSONL).open("r", encoding="utf-8") as f:
            pdf_cache_count = sum(1 for _ in f)

    print("=== STEP 1 DRY RUN ===")
    print(f"Web chunks ready: {web_count}")
    print(f"Metadata records ready: {corpus_count}")
    print(f"Unique PDF files referenced: {len(pdf_refs)}")
    print(f"PDF chunk cache exists: {existing_pdf_cache}")
    print(f"PDF chunk cache rows: {pdf_cache_count}")
    print(f"Chroma path: {CHROMA_PATH}")
    print(f"BM25 path: {BM25_INDEX}")
    print("No Gemini calls made.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NETSOL RAG Step 1 ingestion")
    parser.add_argument("--dry-run", action="store_true", help="Validate counts without API calls")
    parser.add_argument("--reset", action="store_true", help="Delete Chroma collections first")
    parser.add_argument("--force-pdf-cache", action="store_true", help="Rebuild PDF chunk cache")
    parser.add_argument("--prepare-pdf-chunks", action="store_true", help="Only build PDF chunk cache")
    parser.add_argument("--limit", type=int, default=None, help="Limit chunks/records for smoke tests")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.dry_run:
        dry_run()
        return

    if args.prepare_pdf_chunks:
        build_pdf_chunks_cache(force=args.force_pdf_cache, limit_files=args.limit)
        return

    if args.reset:
        reset_collections()

    print("=== STEP 1: NETSOL INGESTION PIPELINE ===")
    print("0/4 PDF chunk cache...")
    build_pdf_chunks_cache(force=args.force_pdf_cache, limit_files=args.limit)
    print("1/4 Web pages...")
    ingest_web_pages(limit=args.limit)
    print("2/4 PDFs...")
    ingest_pdfs(limit=args.limit)
    print("3/4 Metadata...")
    ingest_metadata(limit=args.limit)
    print("4/4 BM25 index...")
    build_bm25_index(limit=args.limit)
    print("=== INGESTION COMPLETE ===")
    print("Now run Step 2 after implementing nodes.py, graph.py, and api.py")


if __name__ == "__main__":
    main()
