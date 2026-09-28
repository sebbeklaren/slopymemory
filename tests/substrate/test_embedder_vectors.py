"""Every memory was embedded with the pinned model under the locked stack. A dependency bump inside the allowed ranges
must leave the vectors the same, or stored coordinates drift from new queries. The reference vectors were taken with
torch 2.12.1 / transformers 5.9.0 / sentence-transformers 5.5.1; the same bits came out under torch 2.14.0 with
transformers 5.11.0 to 5.16.0 and sentence-transformers 6.1.0 (transformers 5.17 no longer loads the model's pinned
code). Compared with a small tolerance, not bit for bit: CPU kernels differ across instruction sets and machines.
The texts cover a short phrase, a sentence, a question, the long-sequence path, truncation, and Unicode/code/emoji.

Any change to the torch, transformers or sentence-transformers lines in uv.lock needs a local `pytest -m embed` run:
CI's unit job does not load the model."""
import json
from pathlib import Path

import numpy as np
import pytest

from embedder_reference_texts import TEXTS

REFERENCE = Path(__file__).parent / "embedder_reference.json"   # little-endian float32 vectors as hex, exact
TOLERANCE = 1e-5


@pytest.mark.embed
def test_the_embedder_gives_the_vectors_the_stores_were_built_with():
    from agent_memory.embed.nomic import NomicEmbedder
    e = NomicEmbedder()
    raw = json.loads(REFERENCE.read_text())
    ref = {side: [np.frombuffer(bytes.fromhex(h), dtype="<f4") for h in raw[side]] for side in raw}
    for i, text in enumerate(TEXTS):
        for side, embed in (("doc", e.embed_document), ("qry", e.embed_query)):
            diff = np.abs(np.asarray(embed(text), dtype=np.float32) - ref[side][i]).max()
            assert diff < TOLERANCE, f"{side} vector changed by {diff:.2e} for text {i} ({text[:40]!r})"
