# tests/test_save_concepts.py
"""Concept-primary save: place the memory's typed CONCEPTS on save. A warm save with
`save_concepts` places+dedups+links each concept to THIS memory (concepts_placed in the reply);
without `save_concepts` the save is byte-identical to the current warm save (no concepts_placed key).
Mirrors the retrieve side that already takes query_concepts. @pytest.mark.embed — real nomic (concept
lighting is 768-cosine); fixtures mirror tests/test_concept_primary_retrieve.py."""
import pytest

from agent_memory.spaces.live_store_state import LiveStore
from agent_memory.spaces import coordinate_sources as cs


@pytest.fixture(scope="module")
def embedder():
    from agent_memory.embed.nomic import NomicEmbedder
    return NomicEmbedder()


# A small varied BACKGROUND corpus for the semantic projector. (The brief's illustrative
# ["a","b","c","d"] is below UMAP's spectral-init floor at n_components=3 — it raises
# "k >= N"; a handful of distinct sentences fits cleanly. The corpus contents are immaterial
# to what these tests assert — they only need a WARM projector so save() takes the warm path.)
_BG = ["combat is heavy and deliberate", "the economy is scarce on purpose",
       "story is the spine of the game", "friendly fire disables shots to teammates",
       "the hero ship grows with the player", "missiles open the engagement at long range",
       "a fleet engagement in open space", "power economy caps generator output"]


@pytest.mark.embed
def test_warm_save_places_concepts(conn, embedder):
    store = LiveStore(embedder, projector=cs.fit_semantic(embedder, _BG))
    out = store.save(conn, "Combat opener = long-range missile alpha", session_key="s1",
                     save_concepts=[["function", "missile opener"], ["function", "fleet engagement"]])
    assert out["status"] == "saved"
    assert out.get("concepts_placed") == 2
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM m3_concept_link WHERE memory_id = %s", (out["memory_id"],))
        assert cur.fetchone()[0] == 2          # both concepts linked to THIS memory


@pytest.mark.embed
def test_save_without_concepts_unchanged(conn, embedder):
    store = LiveStore(embedder, projector=cs.fit_semantic(embedder, _BG))
    out = store.save(conn, "some memory", session_key="s2")
    assert out["status"] == "saved"
    assert "concepts_placed" not in out or out["concepts_placed"] == 0
