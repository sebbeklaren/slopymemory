# src/agent_memory/spaces/wave.py
"""Synapse wave: spread activation from proximity-seed nodes along synapses (undirected,
within + across spaces), attenuating per hop x the synapse's retrieval_strength, with a hard
hop cap + activation cutoff (mandatory — ACT-R-style spreading blows up on dense graphs)."""
from agent_memory.spaces import store as m3


def synapse_wave(conn, seeds: dict[str, float], *, hop_cap: int, attenuation: float,
                 cutoff: float, now=None, max_relax: bool = False, recorder=None) -> dict[str, float]:
    """Spread activation from seed nodes along synapses (undirected, type-blind), attenuating per
    hop x the synapse's retrieval strength. `now` threads decay-aware effective strength (Phase 3).

    max_relax=False (default): sum-over-paths (Phase 2 byte-identical).
    max_relax=True: BELLMAN-FORD RELAXATION for safe hop>=2 traversal on a connected graph —
      activation[dst] = max(activation[dst], spread); a node may be IMPROVED across hops; the
      frontier enqueues a node only when its activation improves; bounded by hop_cap iterations.
      NOT a visit-once visited-set: a strong long path correctly overtakes a weak short one. Kills
      the A->B->A 2-cycle (a seed's returning path is strictly more attenuated than its own value
      -> never improves it) and dense-subgraph multi-path inflation (a node gets its strongest
      single path, not the sum of redundant paths).
    `recorder` (default None): observe-only trace sink; when set, on_edge/on_hop_end are called
      with already-computed values (no behavior change)."""
    activation: dict[str, float] = dict(seeds)
    frontier: dict[str, float] = dict(seeds)
    for _hop in range(hop_cap):
        nxt: dict[str, float] = {}
        for nid, act in frontier.items():
            for dst, retr in m3.outgoing(conn, nid, now=now):
                # Per-edge push, NO /degree. Burst strength is the single constant `attenuation`;
                # footprint scales with DENSITY (more neighbours -> more reach) at the SAME per-edge
                # push — breadth, not loudness. Do NOT normalize by neighbour count. Guarded by
                # tests/test_burst_density_invariant.py.
                spread = act * attenuation * retr
                if spread < cutoff:
                    if recorder is not None:
                        recorder.on_edge(_hop + 1, nid, dst, spread, "cutoff")
                    continue
                if max_relax:
                    if spread > activation.get(dst, 0.0):          # relax: improve only
                        activation[dst] = spread
                        nxt[dst] = max(nxt.get(dst, 0.0), spread)   # propagate the improved value
                        _status = "accepted"
                    else:
                        _status = "not_improved"
                else:
                    activation[dst] = activation.get(dst, 0.0) + spread
                    nxt[dst] = max(nxt.get(dst, 0.0), spread)      # carry the strongest path onward
                    _status = "accepted"
                if recorder is not None:
                    recorder.on_edge(_hop + 1, nid, dst, spread, _status)
        if recorder is not None:
            recorder.on_hop_end(_hop + 1, activation)
        frontier = nxt
        if not frontier:
            break
    return activation
