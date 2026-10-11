"""ChromaDB persistent client and the paper-passage collection.

ChromaDB is imported lazily so the API boots even when the vector
dependency is not installed; a clear error is raised only when vector
search is actually used.
"""
from functools import lru_cache

from app.core.config import CHROMA_DIR, settings


def get_chroma_client():
    try:
        import chromadb
    except ImportError as exc:
        raise RuntimeError(
            "chromadb is not installed. Install it with: pip install chromadb"
        ) from exc
    return chromadb.PersistentClient(path=str(CHROMA_DIR))


@lru_cache(maxsize=1)
def get_passages_collection():
    """Singleton handle to the persistent `paper_passages` collection."""
    client = get_chroma_client()
    return client.get_or_create_collection(
        name=settings.chroma_collection_name,
        metadata={"hnsw:space": "cosine"},
    )
