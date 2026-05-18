"""Hermem Phase 2 - 语义召回模块"""

from .database import get_db, init_db, insert_chunk, get_chunk_count
from .embedding import get_embedding_cached, is_ollama_healthy, test_embedding
from .vectorstore import init_vectorstore, append_vectors, get_stats
from .retrieval import semantic_search, keyword_search, hybrid_search, search

__all__ = [
    "get_db", "init_db", "insert_chunk", "get_chunk_count",
    "get_embedding_cached", "is_ollama_healthy", "test_embedding",
    "init_vectorstore", "append_vectors", "get_stats",
    "semantic_search", "keyword_search", "hybrid_search", "search",
]
