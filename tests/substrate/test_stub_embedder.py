# tests/test_stub_embedder.py
import numpy as np
from agent_memory.embed.stub import StubEmbedder


def test_dim_and_version():
    e = StubEmbedder(dim=768)
    assert e.dim == 768
    assert e.model_version == "stub-v1"


def test_deterministic_and_unit_norm():
    e = StubEmbedder(dim=768)
    v1 = e.embed_document("list comprehension")
    v2 = e.embed_document("list comprehension")
    assert np.allclose(v1, v2)                         # deterministic
    assert abs(np.linalg.norm(v1) - 1.0) < 1e-6        # unit vector


def test_same_text_doc_and_query_match():
    e = StubEmbedder(dim=768)
    assert np.allclose(e.embed_document("x"), e.embed_query("x"))


def test_different_texts_near_orthogonal():
    e = StubEmbedder(dim=768)
    a = e.embed_document("list comprehension")
    b = e.embed_document("photosynthesis in plants")
    assert abs(float(np.dot(a, b))) < 0.2              # ~uncorrelated in high dim (expected |cos| ~ 1/sqrt(768) ~ 0.036)
