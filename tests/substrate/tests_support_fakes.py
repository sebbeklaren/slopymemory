"""Shared stand-ins for the save-path tests: a deterministic embedder, a projector that places everything at the
origin, and a warm LiveStore built from them — no model, no UMAP."""
import numpy as np

from agent_memory.spaces.live_store_state import LiveStore


class Emb:
    def embed_document(self, text):
        v = np.zeros(768, dtype="float32")
        v[sum(map(ord, text)) % 768] = 1.0
        return v

    def embed_query(self, text):
        return self.embed_document(text)


class Proj:
    def project(self, vec):
        return (0.0, 0.0, 0.0)


def stub_fit(embedder, texts):
    return Proj()


def warm_store() -> LiveStore:
    return LiveStore(Emb(), mode="fixed", projector=Proj())
