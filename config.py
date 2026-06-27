"""Central settings for the NETSOL RAG backend.

Step 0 keeps configuration in one place so ingestion, retrieval, LangGraph,
and FastAPI use the same paths, model names, and pipeline limits.
"""

from pathlib import Path
import os

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


# API keys
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")


# Models
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "gemini").lower()
EMBED_MODEL = os.getenv("EMBED_MODEL", "gemini-embedding-2-preview")
LOCAL_EMBED_MODEL = os.getenv(
    "LOCAL_EMBED_MODEL",
    "sentence-transformers/all-MiniLM-L6-v2",
)
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.0-flash")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openrouter").lower()
CROSS_ENCODER_MODEL = os.getenv(
    "CROSS_ENCODER_MODEL",
    "cross-encoder/ms-marco-MiniLM-L-6-v2",
)
USE_CROSS_ENCODER = os.getenv("USE_CROSS_ENCODER", "false").lower() == "true"


# ChromaDB
CHROMA_PATH = str(BASE_DIR / "chroma_db")
COLLECTION_WEB = "netsol_web_pages"
COLLECTION_PDF = "netsol_pdfs"
COLLECTION_META = "netsol_metadata"


# Data
DATA_DIR = BASE_DIR / "netsol_scraped_data"
CHUNK_JSONL = str(DATA_DIR / "rag_chunks.jsonl")
CORPUS_JSONL = str(DATA_DIR / "rag_corpus.jsonl")
PDF_CHUNKS_JSONL = str(DATA_DIR / "rag_pdf_chunks.jsonl")
BM25_INDEX = str(BASE_DIR / "bm25_index.pkl")
QUERY_LOGS = str(BASE_DIR / "query_logs.jsonl")
LLM_ERROR_LOGS = str(BASE_DIR / "llm_errors.jsonl")


# Pipeline settings
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))
TOP_K_RETRIEVE = int(os.getenv("TOP_K_RETRIEVE", "20"))
TOP_K_RERANK = int(os.getenv("TOP_K_RERANK", "5"))
MIN_CHUNK_WORDS = int(os.getenv("MIN_CHUNK_WORDS", "40"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "2"))
SIMPLE_CONTEXT_CHUNKS = int(os.getenv("SIMPLE_CONTEXT_CHUNKS", "3"))
COMPLEX_CONTEXT_CHUNKS = int(os.getenv("COMPLEX_CONTEXT_CHUNKS", "5"))
CONTEXT_CHARS_PER_CHUNK = int(os.getenv("CONTEXT_CHARS_PER_CHUNK", "900"))
ANSWER_MAX_WORDS = int(os.getenv("ANSWER_MAX_WORDS", "90"))
GENERATION_MAX_TOKENS = int(os.getenv("GENERATION_MAX_TOKENS", "384"))


# API settings
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8000"))
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")


def validate_step0_paths() -> list[str]:
    """Return missing required data paths for a quick setup check."""
    required_paths = [Path(CHUNK_JSONL), Path(CORPUS_JSONL), DATA_DIR]
    return [str(path) for path in required_paths if not path.exists()]


def backend_artifact_status() -> dict:
    """Return file-level readiness for generated backend artifacts."""
    paths = {
        "chroma_path": Path(CHROMA_PATH),
        "bm25_index": Path(BM25_INDEX),
        "pdf_chunks": Path(PDF_CHUNKS_JSONL),
        "chunk_jsonl": Path(CHUNK_JSONL),
        "corpus_jsonl": Path(CORPUS_JSONL),
    }
    return {
        name: {
            "exists": path.exists(),
            "size": path.stat().st_size if path.exists() and path.is_file() else None,
            "path": str(path),
        }
        for name, path in paths.items()
    }
