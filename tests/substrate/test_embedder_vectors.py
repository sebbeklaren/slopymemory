"""Every memory ever saved was embedded with the pinned model under the locked stack. A dependency bump inside the
allowed ranges must leave the vectors bit-identical, or every stored coordinate would drift from new queries. These
checksums were taken with torch 2.12.1 / transformers 5.9.0 / sentence-transformers 5.5.1 and verified identical
under torch 2.14.0 / transformers 5.11.0 / sentence-transformers 6.1.0 (transformers 5.12 removes a function the
model's pinned code calls, so the ceiling is below it)."""
import hashlib

import numpy as np
import pytest

EXPECTED = {
    "Destructive actions use an outlined button and a confirm dialog.":
        ("8ed75739b38860d669f6cc6a9d7de266165412cef72520e94ca5dd67978e807a",
         "c7aab346cadaac1dbed0c27a79f1220ab676930cc51673f8b5749e5967692e5c"),
    "button styling":
        ("92de4c1d4708d508dcde425e2e50a79cf43769d7d4d20d4bb6d12d72a80d4317",
         "68e9c9cd7e0530b9a78866d93f1d4610fb8fa3dd2f6e86abf8845e3568abb9f3"),
    "how long does a user stay logged in?":
        ("c078b1e34d4c7f7502089d7f931b74020ee39366fcd656dc1e56dc10f9232a64",
         "564d78c98313ddf5c42ff47eee25eb6f7e8e15be110f5478f6d965546081624b"),
}


def _sha(v) -> str:
    return hashlib.sha256(np.asarray(v, dtype=np.float32).tobytes()).hexdigest()


@pytest.mark.embed
def test_the_embedder_gives_the_same_bits_as_the_stores_were_built_with():
    from agent_memory.embed.nomic import NomicEmbedder
    e = NomicEmbedder()
    for text, (doc, qry) in EXPECTED.items():
        assert _sha(e.embed_document(text)) == doc, f"document vector changed for {text!r}"
        assert _sha(e.embed_query(text)) == qry, f"query vector changed for {text!r}"
