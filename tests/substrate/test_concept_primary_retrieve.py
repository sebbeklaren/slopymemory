"""The concept-primary activation field (`retrieve_concept_primary`).

ONE field: query-concept proximity seeds (768-COSINE, per concept-space) -> synapse wave
(max_relax) -> precision-weighted per-memory sum (aggregate_votes) -> rank. Uses the
`conn` fixture and a real NomicEmbedder,
so the query-concept<->memory-concept cosine is genuine (cos~1.0 to its own label, clearly separated
from a distinct label). embed-marked -> excluded from the fast suite (byte-identical), run explicitly.

Two memories, one distinct single concept each: the query-concept "friendly fire" is cosine-nearest
to mem_ff's own concept (cos 1.0) and far from mem_eco's "power economy" (cos ~0.56), so proximity
puts mem_ff on top. Memory text is inserted into m3_memory (place_text_concepts only places/links
concepts) so the enrichment payload is non-None.
"""
import pytest

from agent_memory.spaces import concepts
import agent_memory.spaces.word_vote as wv


@pytest.fixture(scope="module")
def embedder():
    from agent_memory.embed.nomic import NomicEmbedder
    return NomicEmbedder()


def _mem(conn, memory_id, text):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO m3_memory (memory_id, text) VALUES (%s, %s)", (memory_id, text))


@pytest.mark.embed
def test_field_ranks_the_proximate_memory_first(conn, embedder):
    _mem(conn, "mem_ff", "friendly fire disables shots to teammates")
    _mem(conn, "mem_eco", "power economy caps generator output")
    concepts.place_text_concepts(conn, "mem_ff", [("function", "friendly fire")], embedder)
    concepts.place_text_concepts(conn, "mem_eco", [("function", "power economy")], embedder)
    out = wv.retrieve_concept_primary(conn, embedder, [("function", "friendly fire")], k=2)
    assert out and out[0]["memory_id"] == "mem_ff"   # proximity puts the matching memory on top
    assert out[0]["text"] is not None                # whole text is the payload
