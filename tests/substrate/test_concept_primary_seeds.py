"""_concept_primary_seeds — query-concept cosine seeding, union-MAX across concepts.

Load-bearing behavior: a concept node lit by TWO different query-concepts takes the MAX of the two
cosines (NOT overwritten by whichever query-concept is processed last). The DB cosine is monkeypatched
so the union-max seeding logic is exercised deterministically without a live pgvector."""
import agent_memory.spaces.word_vote as wv


def test_seeds_union_max_across_query_concepts(monkeypatch):
    # Two query-concepts in the SAME base_space 'function' (-> concept-space 'function_c').
    # Concept node "A" is near BOTH query-concepts; its seed must be MAX(0.9, 0.7) == 0.9.
    fake = {("function_c", "friendly fire"): [("A", 0.9), ("B", 0.5)],
            ("function_c", "tactics"):       [("A", 0.7), ("C", 0.6)]}
    last_label = [""]

    class Emb:
        def embed_document(self, s):
            last_label[0] = s
            return [0.0] * 768

    def fake_nearest(conn, cspace, qvec, k):
        return fake[(cspace, last_label[0])]

    monkeypatch.setattr(wv, "_cosine_nearest_concepts", fake_nearest)

    seeds = wv._concept_primary_seeds(
        None, Emb(),
        [("function", "friendly fire"), ("function", "tactics")],
        per_space_k=2,
    )
    assert seeds["A"] == 0.9   # MAX(0.9, 0.7), NOT overwritten to 0.7 by the later query-concept
    assert seeds["B"] == 0.5
    assert seeds["C"] == 0.6
