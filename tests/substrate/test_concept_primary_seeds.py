"""_concept_primary_seeds — query-concept cosine seeding, union-MAX across concepts.

Load-bearing behavior: a concept node lit by TWO different query-concepts takes the MAX of the two
cosines (NOT overwritten by whichever query-concept is processed last). The DB cosine is monkeypatched
so the union-max seeding logic is exercised deterministically without a live pgvector."""
import pytest

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


def _spy(monkeypatch, near):
    """Record every (concept-space, label) the seeding looks up, and every label it embeds."""
    looked, embedded, last = [], [], [""]

    class Emb:
        def embed_document(self, s):
            embedded.append(s); last[0] = s
            return [0.0] * 768

    def fake_nearest(conn, cspace, qvec, k):
        looked.append((cspace, last[0]))
        return near.get((cspace, last[0]), [])

    monkeypatch.setattr(wv, "_cosine_nearest_concepts", fake_nearest)
    return Emb(), looked, embedded


def _seed_spaces(monkeypatch, value):
    import dataclasses
    monkeypatch.setattr(wv, "settings", dataclasses.replace(wv.settings, m3_concept_seed_spaces=value))


def test_all_spaces_seeding_reaches_a_concept_filed_in_the_other_space(monkeypatch):
    """An agent names "button styling" as a function concept; the memory filed it as semantic. With seeding in
    every concept space the semantic node is lit too, and the label is embedded once, not once per space."""
    emb, looked, embedded = _spy(monkeypatch, {("semantic_c", "button styling"): [("S", 0.8)]})
    _seed_spaces(monkeypatch, "all")
    seeds = wv._concept_primary_seeds(None, emb, [("function", "button styling")], per_space_k=2)
    assert seeds == {"S": 0.8}
    assert {c for c, _ in looked} == {"semantic_c", "function_c", "feeling_c", "fiction_c", "player_facing_c"}
    assert embedded == ["button styling"]


def test_own_space_seeding_stays_in_the_named_space(monkeypatch):
    """The default: a function concept seeds the function space only — the other space's node stays dark."""
    emb, looked, _ = _spy(monkeypatch, {("semantic_c", "button styling"): [("S", 0.8)]})
    _seed_spaces(monkeypatch, "own")
    assert wv._concept_primary_seeds(None, emb, [("function", "button styling")], per_space_k=2) == {}
    assert looked == [("function_c", "button styling")]


def test_all_spaces_seeding_takes_the_max_across_spaces_and_labels(monkeypatch):
    emb, _, _ = _spy(monkeypatch, {("semantic_c", "a"): [("N", 0.4)], ("function_c", "b"): [("N", 0.9)]})
    _seed_spaces(monkeypatch, "all")
    assert wv._concept_primary_seeds(None, emb, [("semantic", "a"), ("semantic", "b")], per_space_k=2) == {"N": 0.9}


def test_an_unknown_seed_spaces_value_is_refused(monkeypatch):
    emb, _, _ = _spy(monkeypatch, {})
    _seed_spaces(monkeypatch, "some")
    with pytest.raises(ValueError, match="m3_concept_seed_spaces"):
        wv._concept_primary_seeds(None, emb, [("function", "x")], per_space_k=2)
