"""Word-layer concept-vote retrieval. The vote is CROSS-SPACE CONVERGENCE: saturating
per space (steep — first concept dominates) + across-space combine, activation-weighted. NOT
TF-IDF: same-space piling saturates, each new space adds fully. No distinct-space multiplier.

The across-space combine defaults to a plain sum but can carry a STEEP GENUINE-DEPTH weight
(depth_steepness): each space's within-space vote is scaled by (space_best_act ** depth_steepness)
before summing, so weakly-matched / burst-reached spaces (low activation) sink toward 0 while
genuine strong spaces keep ~full weight. depth_steepness=0.0 (default) => act**0 == 1 => plain sum."""
from collections import defaultdict
from types import MappingProxyType
from typing import Mapping, NamedTuple

from agent_memory.config import settings


def aggregate_votes(concept_hits, *, decay=None, depth_steepness=None) -> dict[str, float]:
    """{memory_id: vote}. Per (memory, space): sort contributions (activation*link_strength) desc,
    combine sub-additively c0 + decay*c1 + decay^2*c2 + ... (steep saturation => distinct-space
    dominance). Across spaces: total = Σ_space (space_best_act ** depth_steepness) * within_vote(space),
    where space_best_act = the max ACTIVATION (raw act, NOT act*ls) among that memory's hits in that
    space. activation already encodes burst attenuation, so depth_steepness>0 drives weakly/burst-lit
    spaces toward 0 (genuine DEPTH survives, weakly-matched space-BREADTH sinks).

    decay in [0,1): 0 = max-only, ->1 = full sum (TF-IDF, the anti-pattern). Default
    settings.m3_word_saturation_decay (0.25). depth_steepness>=0. Default settings.m3_word_depth_steepness
    (0.0) => act**0 == 1 => EXACT plain-sum (byte-identical backward-compat no-op)."""
    decay = settings.m3_word_saturation_decay if decay is None else decay
    depth_steepness = settings.m3_word_depth_steepness if depth_steepness is None else depth_steepness
    if not (0 <= decay < 1):                                    # decay>=1 IS the TF-IDF full-sum
        raise ValueError(f"decay must be in [0,1), got {decay}")  # anti-pattern; decay<0 subtracts
    per: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    best_act: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for mem, space, act, ls in concept_hits:
        per[mem][space].append(act * ls)
        if act > best_act[mem][space]:                         # max RAW activation per (mem, space)
            best_act[mem][space] = act
    votes: dict[str, float] = {}
    for mem, spaces in per.items():
        total = 0.0
        for space, contribs in spaces.items():
            contribs.sort(reverse=True)
            within = sum(c * (decay ** i) for i, c in enumerate(contribs))
            total += (best_act[mem][space] ** depth_steepness) * within  # steep genuine-depth weight
        votes[mem] = total                                     # depth_steepness=0 => weight==1 => plain sum
    return votes


def _concept_hits(conn, activation):
    """activation: {concept_node_id: act}. Join m3_concept_link + the node's concept-space name ->
    yield (memory_id, base_space, activation, link_strength) tuples for aggregate_votes. base_space
    is the INVERSE of concepts.CONCEPT_SPACES (concept-space name -> the base space it mirrors)."""
    if not activation:
        return []
    from agent_memory.spaces.concepts import CONCEPT_SPACES
    inv = {v: k for k, v in CONCEPT_SPACES.items()}
    ids = list(activation)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT l.concept_node::text, l.memory_id, s.name, l.strength FROM m3_concept_link l "
            "JOIN m3_node n ON n.id = l.concept_node JOIN m3_space s ON s.id = n.space_id "
            "WHERE l.concept_node::text = ANY(%s)", (ids,))
        return [(mem, inv[cspace], activation[cn], float(ls)) for cn, mem, cspace, ls in cur.fetchall()]


def _cosine_nearest_concepts(conn, concept_space, qvec, k):
    """Top-k concept nodes in a concept-space by 768-cosine to qvec (pgvector `<=>` = cosine
    distance; similarity = 1 - distance). Filtered to kind='concept' + non-null concept_embedding
    (the concept-space holds only concepts; belt-and-suspenders). Returns [(node_id, cosine)]."""
    import numpy as np
    q = np.asarray(qvec, dtype="float32")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT n.id::text, 1 - (n.concept_embedding <=> %s) AS cos FROM m3_node n "
            "JOIN m3_space s ON s.id = n.space_id "
            "WHERE s.name = %s AND n.kind = 'concept' AND n.concept_embedding IS NOT NULL "
            "ORDER BY n.concept_embedding <=> %s LIMIT %s", (q, concept_space, q, k))
        return [(r[0], float(r[1])) for r in cur.fetchall()]


def _concept_primary_seeds(conn, embedder, query_concepts, per_space_k):
    """Concept-primary proximity seeding (COSINE distance — chosen deliberately, NOT a silent
    vanilla-vector-search default). Each extracted query-concept lights its cosine-nearest
    memory-concepts IN ITS OWN concept-space; the seed activation is the cosine similarity
    (proximity kernel K, identity form). Query_concepts: [(base_space, label)]. Nodes lit
    by more than one query-concept take the MAX. This replaces retrieve_word_vote's whole-query cosine."""
    from agent_memory.spaces.concepts import CONCEPT_SPACES
    seeds: dict[str, float] = {}
    for base_space, label in query_concepts:
        cspace = CONCEPT_SPACES[base_space]
        norm = " ".join(label.strip().lower().split())
        qvec = embedder.embed_document(norm)
        for nid, cos in _cosine_nearest_concepts(conn, cspace, qvec, per_space_k):
            seeds[nid] = max(seeds.get(nid, 0.0), float(cos))
    return seeds


class GradientReading(NamedTuple):
    """What retrieval already computes and used to discard (a read-only seam).

    NAMING IS LOAD-BEARING — "the field" is banned as ambiguous:
      * `gradient` — NODE-level `{node_id: activation}`. `activation = proximity x synapse-strength`,
        attenuated per hop, POST-CUTOFF. This is the input a later binding pass would consume
        (binding is node-to-node).
      * `votes`    — MEMORY-level `{memory_id: score}`. An AGGREGATION *of* the gradient, not the
        gradient. This is what threshold-surfacing reads.

    Both are read-only views over COPIES: a consumer cannot mutate what retrieval computed, so
    "reading cannot alter it" is enforced rather than asserted (the line being guarded is
    anything that alters a memory's ACTIVATION VALUE). Deliberately carries NO derived quantity —
    no surprise/novelty/error — those belong to a later layer and this seam must not shape them."""
    gradient: Mapping[str, float]
    votes: Mapping[str, float]


def _reading(activation: dict, votes: dict) -> GradientReading:
    return GradientReading(gradient=MappingProxyType(dict(activation)),
                           votes=MappingProxyType(dict(votes)))


def retrieve_concept_primary(conn, embedder, query_concepts, k, *, per_space_k=None, decay=None,
                             hop_cap=None, return_gradient=False) -> list[dict]:
    """Concept-primary activation field (one-field retrieval over concepts; supersedes the whole-question
    two-wave/RRF path). COSINE distance. Proximity wave: query-concepts light cosine-nearest
    memory-concepts. Synapse wave: propagate along concept-links x retrieval_strength x alpha per hop
    (max_relax HARDCODED — clique density => breadth not loudness), radius = hop_cap. Precision-weighted
    per-memory sum across spaces (aggregate_votes, depth_steepness) = specificity gradient. Returns
    [{memory_id, score, text}] desc. NOTE: measured on the development corpus, the synapse wave was
    marginal (a present-but-inert seam for far-bridge reach + Bjork decay); proximity dominates."""
    from agent_memory.spaces.wave import synapse_wave
    from agent_memory.spaces.live_store import _memory_texts
    per_space_k = settings.m3_word_query_concepts if per_space_k is None else per_space_k
    hop_cap = settings.m3_hop_cap if hop_cap is None else hop_cap
    seeds = _concept_primary_seeds(conn, embedder, query_concepts, per_space_k)
    activation = synapse_wave(conn, seeds, hop_cap=hop_cap, attenuation=settings.m3_attenuation,
                              cutoff=settings.m3_activation_cutoff, max_relax=True)
    votes = aggregate_votes(sorted(_concept_hits(conn, activation)), decay=decay)
    ranked = sorted(votes.items(), key=lambda t: (-t[1], t[0]))[:k]
    texts = _memory_texts(conn, [m for m, _ in ranked])
    results = [{"memory_id": m, "score": round(s, 4), "text": texts.get(m)} for m, s in ranked]
    # SEAM (additive): `ranked` above is already truncated to k; `activation` and `votes` are the
    # un-truncated objects this function used to discard. Returning them changes nothing about them.
    return (results, _reading(activation, votes)) if return_gradient else results
