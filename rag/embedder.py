"""
RAG embedder — Phase 2.
Generates embeddings for NCAIDSHP chunks via Ollama's /api/embed endpoint.
"""


def embed(text: str, model: str = "nomic-embed-text") -> list[float]:
    """Phase 2: return embedding vector for text."""
    raise NotImplementedError("Embedder not yet implemented — Phase 2")


def embed_batch(texts: list[str], model: str = "nomic-embed-text") -> list[list[float]]:
    """Phase 2: batch embed."""
    raise NotImplementedError("Phase 2")
