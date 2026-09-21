# src/agent_memory/embed/nomic.py
import numpy as np
from agent_memory.config import settings


class NomicEmbedder:
    """Real local embedder: nomic-embed-text-v1.5 via sentence-transformers, in-process, CPU.
    Nomic models require task prefixes: 'search_document: ' for stored text, 'search_query: ' for queries."""

    model_version = "nomic-embed-text-v1.5"

    def __init__(self):
        from sentence_transformers import SentenceTransformer
        self.dim = settings.embed_dim
        self._model = SentenceTransformer(settings.embed_model, revision=settings.embed_revision, trust_remote_code=True, device="cpu")

    def _embed(self, text: str, prefix: str) -> np.ndarray:
        v = self._model.encode([f"{prefix}{text}"], normalize_embeddings=True)[0]
        return np.asarray(v, dtype="float32")

    def embed_document(self, text: str) -> np.ndarray:
        return self._embed(text, "search_document: ")

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed(text, "search_query: ")
