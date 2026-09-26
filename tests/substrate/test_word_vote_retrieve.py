"""Word-layer retrieval: light concept nodes by 768-COSINE per concept-space, burst
(max-relax), follow concept->text links, aggregate (cross-space convergence), rank texts.

Deterministic embedder: each concept
label is a one-hot 768 vector at a fixed dim; the query is a weighted blend over dims, so
cos(query, label) = weight[dim] / |query| is exactly controlled. Concept nodes are placed with
their 768 concept_embedding via concepts.place_or_get_concept.

Two load-bearing behaviors:
  1. Convergence — a text whose concepts converge across MORE spaces outranks a 1-concept text.
  2. Clique / max-relax teeth — a concept-DENSE 6-clique must NOT dominate a genuinely
     cross-space-convergent text; self-carrying teeth: the SAME corpus under sum-mode
     (max_relax=False) inflates the clique so it DOES win.
Fast suite: DB-only, deterministic embedder (no real model) -> NOT marked embed/extract.
"""
import numpy as np

from agent_memory.config import settings
from agent_memory.spaces import concepts
from agent_memory.spaces import word_vote
from agent_memory.spaces.wave import synapse_wave
from agent_memory.spaces.word_vote import (
    _concept_hits,
    _cosine_nearest_concepts,
    aggregate_votes,
)


class _DimEmbedder:
    """Deterministic 768-d embedder for word-vote lighting. Each concept label is a one-hot at a
    fixed dim; the query is a weighted blend over dims. cos(query, one_hot[dim]) = weight[dim]/|q|."""

    def __init__(self, dims, weights):
        self.dims = dims          # normalized label -> dim index
        self.weights = weights    # dim index -> query weight

    def embed_document(self, text):
        v = np.zeros(768, dtype="float32")
        v[self.dims[text]] = 1.0
        return v

    def embed_query(self, text):
        v = np.zeros(768, dtype="float32")
        for d, w in self.weights.items():
            v[d] = w
        return v


def _mem(conn, memory_id, text):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO m3_memory (memory_id, text) VALUES (%s, %s)", (memory_id, text))


def _place(conn, emb, memory_id, typed, *, clique=True):
    """Place/link each (base_space, label) concept to memory_id; optionally wire the co-occurrence
    clique (the real save path always wires it)."""
    nodes = []
    for base_space, label in typed:
        n = concepts.place_or_get_concept(conn, base_space, label, emb)
        concepts.link_concept_to_memory(conn, n, memory_id)
        nodes.append(n)
    if clique:
        concepts.wire_concept_clique(conn, nodes)
    return nodes


def test_aggregate_votes_is_input_order_sensitive():
    """Justification for the stable-order sort above: aggregate_votes sums per-space contributions
    in INPUT-iteration order, and float addition is non-associative — so two orderings of the SAME
    cross-space hits can yield DIFFERENT floats (a near-tie could flip). Hence the caller must sort."""
    # The witness is chosen for the shipped depth_steepness default of 4.0:
    # under it the old (.1,.2,.3) contributions round to the SAME float in both
    # orders, so that input stopped exercising non-associativity. (.1,.2,.9) still does. The assert
    # itself is unchanged — the teeth are the inequality, not the numbers.
    a = aggregate_votes([("m", "function", 0.1, 1.0), ("m", "feeling", 0.2, 1.0), ("m", "fiction", 0.9, 1.0)])
    b = aggregate_votes([("m", "feeling", 0.2, 1.0), ("m", "fiction", 0.9, 1.0), ("m", "function", 0.1, 1.0)])
    assert a["m"] != b["m"]                   # differs at the last bit under the shipped default


# ================= the live path: retrieve_concept_primary =================
# Two invariants of the retrieval the product runs: concept density becomes breadth, not loudness; and
# the cross-space float sum does not depend on row order.


class _BlendEmbedder:
    """`_concept_primary_seeds` embeds each QUERY-CONCEPT LABEL with embed_document, so a query label
    can carry a BLEND: that is how the 2-vs-1 seed geometry below survives the move from
    whole-query cosine to per-concept cosine."""

    def __init__(self, dims, blends):
        self.dims, self.blends = dims, blends

    def embed_document(self, text):
        v = np.zeros(768, dtype="float32")
        if text in self.blends:
            for d, w in self.blends[text].items():
                v[d] = w
        else:
            v[self.dims[text]] = 1.0
        return v

    def embed_query(self, text):
        return self.embed_document(text)


def test_concept_primary_clique_does_not_inflate_under_max_relax(conn):
    """Teeth on the live path: a concept-DENSE memory must NOT out-rank genuine cross-space
    convergence. max_relax turns clique density into BREADTH, not loudness. Self-carrying RED teeth:
    the identical corpus under SUM-mode lets the clique inflate past the convergent memory."""
    dims = {"cvf": 0, "cve": 1, "c0": 10, "c1": 11, "c2": 12, "c3": 13, "c4": 14, "c5": 15}
    blends = {"qf": {0: 2.0, 10: 1.0, 11: 1.0, 12: 1.0},      # function-space query concept
              "qe": {1: 2.0, 13: 1.0, 14: 1.0, 15: 1.0}}      # feeling-space query concept
    emb = _BlendEmbedder(dims, blends)

    _mem(conn, "m_converge", "genuine convergence")
    _mem(conn, "m_clique", "dense clique")
    _place(conn, emb, "m_converge", [("function", "cvf"), ("feeling", "cve")])
    _place(conn, emb, "m_clique", [("function", "c0"), ("function", "c1"), ("function", "c2"),
                                   ("feeling", "c3"), ("feeling", "c4"), ("feeling", "c5")])
    qc = [["function", "qf"], ["feeling", "qe"]]

    got = word_vote.retrieve_concept_primary(conn, emb, qc, 5)
    ids = [r["memory_id"] for r in got]
    assert ids[0] == "m_converge", f"max-relax must keep convergence on top, got {ids}"

    # RED teeth: same corpus, same seeds, SUM-mode burst -> the 6-clique's mutual reinforcement
    # inflates it past the convergent memory. Proves the guard is non-vacuous.
    seeds = word_vote._concept_primary_seeds(conn, emb, qc, settings.m3_word_query_concepts)
    sum_act = synapse_wave(conn, seeds, hop_cap=settings.m3_hop_cap,
                           attenuation=settings.m3_attenuation, cutoff=settings.m3_activation_cutoff,
                           max_relax=False)
    sum_ranked = sorted(aggregate_votes(sorted(_concept_hits(conn, sum_act))).items(),
                        key=lambda t: (-t[1], t[0]))
    assert sum_ranked[0][0] == "m_clique", (
        f"sum-mode must let the clique inflate past convergence (teeth), got {sum_ranked}")


def test_concept_primary_feeds_aggregate_in_stable_order(conn, monkeypatch):
    """Determinism on the LIVE path: the cross-space float sum must not depend on DB row order, so
    the hits handed to aggregate_votes must be SORTED. Patch _concept_hits to return a known
    unsorted list and spy what aggregate_votes actually receives."""
    unsorted = [("m_b", "feeling", 0.2, 1.0), ("m_a", "function", 0.5, 1.0), ("m_a", "feeling", 0.3, 1.0)]
    monkeypatch.setattr(word_vote, "_concept_hits", lambda conn, act: list(unsorted))
    captured = {}
    real_aggregate = word_vote.aggregate_votes

    def spy(hits, **kw):
        captured["hits"] = list(hits)
        return real_aggregate(hits, **kw)

    monkeypatch.setattr(word_vote, "aggregate_votes", spy)
    word_vote.retrieve_concept_primary(conn, _BlendEmbedder({}, {}), [], 5)
    assert captured["hits"] == sorted(unsorted)
