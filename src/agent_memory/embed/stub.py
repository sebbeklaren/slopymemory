# src/agent_memory/embed/stub.py
import hashlib
import numpy as np
from agent_memory.config import settings


class StubEmbedder:
    """Deterministic hash-based embedder for plumbing/CI tests. Never used by the quality eval."""

    model_version = "stub-v1"

    def __init__(self, dim: int | None = None):
        self.dim: int = dim if dim is not None else settings.embed_dim

    def _vec(self, text: str) -> np.ndarray:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        seed = int.from_bytes(digest[:8], "little")
        rng = np.random.default_rng(seed)
        v = rng.standard_normal(self.dim).astype("float32")
        norm = np.linalg.norm(v)
        if norm == 0.0:
            raise ValueError("stub embedding has zero norm; cannot normalize")
        return v / norm

    def embed_document(self, text: str) -> np.ndarray:
        return self._vec(text)

    def embed_query(self, text: str) -> np.ndarray:
        return self._vec(text)
