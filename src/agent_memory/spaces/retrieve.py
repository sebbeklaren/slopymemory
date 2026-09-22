# src/agent_memory/spaces/retrieve.py
"""Substrate retrieval: proximity signal + synapse burst. `retrieve_proximity` returns the nearest
semantic nodes (proximity-only). `retrieve_seeded` is the dual-wave core — the signal lands by
proximity in each space of a seed-coordinate map, then the synapse burst spreads. `retrieve` +
`seed_query_coords` are the substrate query path (semantic-only v1; the seed-map is the facet
extension point). `retrieve_dual_wave` is the legacy projectors-based adapter (byte-identical, kept
for the mechanism tests)."""
import math
from datetime import datetime, timezone

from agent_memory.config import settings
from agent_memory.spaces import coordinate_sources as cs
from agent_memory.spaces import store as m3
from agent_memory.spaces import negative_link as nl
from agent_memory.spaces.wave import synapse_wave


def retrieve_proximity(conn, embedder, projector, query: str, k: int) -> list[str]:
    qcoord = projector.project(embedder.embed_query(query))
    return [nid for nid, _ in m3.nearest_in_space(conn, "semantic", qcoord, k)]


def retrieve_seeded(conn, seed_coords: dict[str, tuple[float, float, float]], k: int,
                    hop_cap: int | None = None, attenuation: float | None = None,
                    *, m3_node_accessibility_on: bool = False,
                    m3_synapse_two_strength_on: bool = False,
                    m3_positive_link_on: bool = False,
                    m3_cross_memory_on: bool | None = None,
                    m3_supersession_downweight_on: bool = False,
                    downweight_beta: float | None = None,
                    return_activation: bool = False,
                    query_label: str = "",
                    now: datetime | None = None,
                    recorder=None):
    """Substrate dual-wave from a per-space seed-coordinate map. The signal lands by proximity in
    every space present in seed_coords (the EXTENSION POINT: v1 callers pass {"semantic": coord};
    facets later add project/episodic keys with no signature change), then the synapse burst spreads
    (intra-constellation + cross-memory note_related). Ranks by accumulated activation. The mechanism
    flags + recorder behave exactly as before (this is the body extracted from retrieve_dual_wave).
    m3_supersession_downweight_on: relevance-demotion seed down-weight (seeds[nid]*=exp(-beta*strength)
    for nodes with active incoming negative_links); reuses neg_downweight_beta; default-off = byte-identical."""
    hop_cap = settings.m3_hop_cap if hop_cap is None else hop_cap
    attenuation = settings.m3_attenuation if attenuation is None else attenuation
    # max-relax burst resolves from settings when the caller omits it (default True): dense
    # same-thread cliques spread breadth, not loudness. Legacy retrieve_dual_wave still passes an
    # explicit False, keeping the mechanism tests byte-identical in sum-mode.
    m3_cross_memory_on = settings.m3_cross_memory_on if m3_cross_memory_on is None else m3_cross_memory_on
    if (m3_node_accessibility_on or m3_synapse_two_strength_on) and now is None:
        now = datetime.now(timezone.utc)

    # Multi-space retrieval: FACET-space seed contributions (every space that is not 'semantic') are scaled by
    # m3_facet_seed_weight; semantic is always full weight. Default 1.0 -> byte-identical (1.0*x == x).
    # weight 0.0 -> those spaces contribute nothing (skipped entirely: THE CONTROL is byte-exact, no
    # zero-valued seeds leaking into the wave/ranking). No signature change — the weight is a config
    # knob and the multi-space seed map is the existing extension point.
    facet_weight = settings.m3_facet_seed_weight
    seeds: dict[str, float] = {}
    for space, qcoord in seed_coords.items():
        w = 1.0 if space == "semantic" else facet_weight
        if w == 0.0:                                   # a zero-weight facet space seeds nothing
            continue
        for nid, dist in m3.nearest_in_space(conn, space, qcoord, k):
            prox = w * (1.0 / (1.0 + dist))
            if m3_node_accessibility_on:
                prox *= m3.effective_accessibility(conn, nid, now=now)
            seeds[nid] = max(seeds.get(nid, 0.0), prox)

    # Relevance-demotion axis (supersession signal): a superseded seed's retrieval-time contribution
    # is down-weighted by exp(-beta*strength) — bounded (demote, not erase) and STATIC (sustains while
    # the link lives). Distinct from disuse-forgetting (the synapse axis). Modulates the contribution,
    # NOT the coordinate. Default-off = byte-identical.
    if m3_supersession_downweight_on and seeds:
        beta = settings.neg_downweight_beta if downweight_beta is None else downweight_beta
        for nid, st in nl.target_strengths(conn, list(seeds)).items():
            seeds[nid] *= math.exp(-beta * st)

    if recorder is not None:
        recorder.begin_query(query_label)
        recorder.on_seed(seeds)

    activation = synapse_wave(conn, seeds, hop_cap=hop_cap, attenuation=attenuation,
                              cutoff=settings.m3_activation_cutoff,
                              now=(now if m3_synapse_two_strength_on else None),
                              max_relax=m3_cross_memory_on,
                              recorder=recorder)

    ranked = [nid for nid, _ in sorted(activation.items(), key=lambda t: (-t[1], t[0]))[:k]]

    if recorder is not None:
        recorder.on_ranking(sorted(activation.items(), key=lambda t: (-t[1], t[0]))[:k])

    if m3_node_accessibility_on:
        m3.reinforce_node_accessibility(
            conn, ranked, now=now,
            lambda_=settings.m3_acc_decay_lambda,
            sigma=settings.m3_acc_storage_bump,
            rho=settings.m3_acc_retrieval_bump,
        )
    if m3_synapse_two_strength_on:
        m3.reinforce_synapses_in(
            conn, ranked, now=now,
            lambda_=settings.m3_syn_decay_lambda,
            sigma=settings.m3_syn_storage_bump,
            rho=settings.m3_syn_retrieval_bump,
            amplify_cited=m3_positive_link_on,
            amplify=settings.m3_pos_amplify,
            recorder=recorder,
        )
    return (ranked, activation) if return_activation else ranked


def retrieve_dual_wave(conn, embedder, projectors: dict, query: str, k: int,
                       hop_cap: int | None = None, attenuation: float | None = None,
                       *, m3_node_accessibility_on: bool = False,
                       m3_synapse_two_strength_on: bool = False,
                       m3_positive_link_on: bool = False,
                       m3_cross_memory_on: bool = False,
                       return_activation: bool = False,
                       now: datetime | None = None,
                       recorder=None):
    """LEGACY adapter (kept byte-identical for the existing mechanism tests that drive it): build the
    per-space seed coords by projecting the query embedding through each projector, then delegate to
    retrieve_seeded. The corrected-substrate path uses retrieve_seeded / retrieve directly."""
    qvec = embedder.embed_query(query)
    seed_coords = {space: projector.project(qvec) for space, projector in projectors.items()}
    return retrieve_seeded(
        conn, seed_coords, k, hop_cap=hop_cap, attenuation=attenuation,
        m3_node_accessibility_on=m3_node_accessibility_on,
        m3_synapse_two_strength_on=m3_synapse_two_strength_on,
        m3_positive_link_on=m3_positive_link_on,
        m3_cross_memory_on=m3_cross_memory_on,
        return_activation=return_activation, query_label=query, now=now, recorder=recorder)


def seed_query_coords(sem_projector, embedder, query: str, *, facets: dict | None = None,
                      epoch_range: tuple[float, float] | None = None,
                      qvec=None) -> dict[str, tuple[float, float, float]]:
    """Build the per-space seed-coords map. Always seeds SEMANTIC (from the query text, via
    embed_query). FACETS (explicit, caller-supplied) seed the other spaces by the SAME rule — a
    query that carries that space's coordinate source: facets={"project": <code-path>} ->
    project_coord(scope) (pure); facets={"episodic": <datetime>} -> episodic_coord(when, epoch_range)
    (needs epoch_range from place_corpus().epoch_range). facets=None -> semantic-only, byte-identical.
    Unknown facet keys raise (catches typos — facets is explicit).
    qvec: an optional precomputed embed_query(query) vector to REUSE — the B1 path embeds
    ONCE and shares the vector between this semantic seed and the facet projector transforms (nomic
    CPU latency on the hot path — embed once). None -> embed here (byte-identical for existing
    callers)."""
    seeds = {"semantic": sem_projector.project(embedder.embed_query(query) if qvec is None else qvec)}
    if facets:
        unknown = set(facets) - {"project", "episodic"}
        if unknown:
            raise ValueError(f"unknown facet keys {sorted(unknown)} (expected 'project' and/or 'episodic')")
        if "project" in facets:
            seeds["project"] = cs.project_coord(facets["project"])
        if "episodic" in facets:
            if epoch_range is None:
                raise ValueError("episodic facet requires epoch_range (place_corpus().epoch_range)")
            seeds["episodic"] = cs.episodic_coord(facets["episodic"], epoch_range)
    return seeds


def retrieve(conn, sem_projector, embedder, query: str, k: int, *, facets: dict | None = None,
             epoch_range: tuple[float, float] | None = None, **kwargs):
    """Convenience substrate retrieval: seed semantic from the query (+ any facets), then dual-wave.
    kwargs pass through to retrieve_seeded (hop_cap, flags, return_activation, recorder, now)."""
    return retrieve_seeded(conn, seed_query_coords(sem_projector, embedder, query, facets=facets,
                                                   epoch_range=epoch_range), k, query_label=query, **kwargs)
