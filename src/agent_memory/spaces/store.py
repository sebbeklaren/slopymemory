# src/agent_memory/spaces/store.py
from importlib import resources
from itertools import combinations
from datetime import datetime as _dt

import numpy as np
import psycopg

from agent_memory import strength as _strength
from agent_memory.spaces import negative_link as _nl
from agent_memory.spaces import positive_link as _pl

_SPACES = [  # (id, name, source) — the six harness-domain spaces + the four Phase-A content-facet spaces
    (1, "semantic", "project_embedding"),
    (2, "episodic", "recency"),
    (3, "procedural", "project_embedding"),
    (4, "outcome", "outcome"),
    (5, "project", "code_location"),
    (6, "social", "project_embedding"),
    # Multi-space Phase A: content-facet spaces (additive ids; each coord = an embedding of the
    # space's own facet text — see m3_facet + the placement pass). seed_spaces is ON CONFLICT DO
    # NOTHING, so appending these is idempotent on already-seeded DBs.
    (7, "function", "facet_embedding"),       # the mechanic/rule itself
    (8, "feeling", "facet_embedding"),        # tone / intended player emotion
    (9, "fiction", "facet_embedding"),        # narrative / world meaning
    (10, "player_facing", "facet_embedding"), # what the player does/sees/controls
    # Word-layer: concept-spaces mirror semantic + the 4 content spaces via their own
    # projectors. Concepts placed here NEVER mix with memory nodes -> current retrieval untouched.
    # These are TYPE-TAGS (concept nodes store their OWN 768 concept_embedding for cosine lighting;
    # they are not 3D-placed); source='concept_embedding' is informational. seed_spaces is ON
    # CONFLICT DO NOTHING, so appending ids 11-15 is idempotent on already-seeded DBs.
    (11, "semantic_c", "concept_embedding"),
    (12, "function_c", "concept_embedding"),
    (13, "feeling_c", "concept_embedding"),
    (14, "fiction_c", "concept_embedding"),
    (15, "player_facing_c", "concept_embedding"),
]


def apply_schema(conn: psycopg.Connection) -> None:
    sql = (resources.files("agent_memory.spaces") / "schema.sql").read_text()
    with conn.cursor() as cur:
        cur.execute(sql)


def seed_spaces(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO m3_space (id, name, source) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
            _SPACES,
        )


_SPACE_ID = {name: sid for sid, name, _ in _SPACES}


def place_node(conn: psycopg.Connection, space: str, label: str, kind: str,
               coord: tuple[float, float, float], *, layer: str = "everyday",
               source_ref: str | None = None) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO m3_node (space_id, label, kind, coord, layer, source_ref) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id::text",
            (_SPACE_ID[space], label, kind, np.asarray(coord, dtype="float32"), layer, source_ref),
        )
        return cur.fetchone()[0]


def bind_constellation(conn: psycopg.Connection, node_ids: list[str], *, type: str = "assoc") -> None:
    """Link a memory's aspect-nodes with static synapses (retrieval_strength defaults to 1.0;
    two-strength OFF in Phase 2). One undirected edge per pair (stored once; `outgoing` reads
    both directions)."""
    pairs = list(combinations(sorted(set(node_ids)), 2))
    if not pairs:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO m3_synapse (src_node, dst_node, type) VALUES (%s, %s, %s) "
            "ON CONFLICT (src_node, dst_node, type) DO NOTHING",
            [(s, d, type) for s, d in pairs],
        )


def note_related(conn: psycopg.Connection, node_a: str, node_b: str, *, type: str = "related") -> None:
    """Spawn one UNDIRECTED cross-memory synapse between two nodes (typically from different
    memories). Twin of bind_constellation — a symmetric association, NOT a directed link. Default
    two-strength (storage=retrieval=1.0 via column defaults) so it slots into the existing
    gate/amplify/decay machinery unchanged. `type` is provenance only (the wave is type-blind);
    'related' distinguishes cross-memory edges from intra-constellation 'assoc' for diagnostics.
    Canonical-ordered pair + ON CONFLICT DO NOTHING so (a,b) and (b,a) collapse to one row."""
    src, dst = sorted((node_a, node_b))
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO m3_synapse (src_node, dst_node, type) VALUES (%s, %s, %s) "
            "ON CONFLICT (src_node, dst_node, type) DO NOTHING",
            (src, dst, type),
        )


def outgoing(conn: psycopg.Connection, node_id: str, *, now: _dt | None = None) -> list[tuple[str, float]]:
    """Synapse neighbours of a node (undirected): the other endpoint + the synapse's retrieval
    strength. When `now` is provided, applies the synapse-side decay formula
    `retrieval * exp(-(lambda / storage) * delta_days)` using `last_activated_at` (Phase 3
    decay-aware spread). Default `now=None` returns the raw retrieval_strength (Phase 2 byte-
    identical behavior). Used by synapse_wave."""
    with conn.cursor() as cur:
        if now is None:
            cur.execute(
                "SELECT dst_node::text, retrieval_strength FROM m3_synapse WHERE src_node = %s "
                "UNION ALL "
                "SELECT src_node::text, retrieval_strength FROM m3_synapse WHERE dst_node = %s",
                (node_id, node_id))
            return [(r[0], float(r[1])) for r in cur.fetchall()]
        # decay-aware
        cur.execute(
            "SELECT dst_node::text, retrieval_strength, storage_strength, last_activated_at "
            "FROM m3_synapse WHERE src_node = %s "
            "UNION ALL "
            "SELECT src_node::text, retrieval_strength, storage_strength, last_activated_at "
            "FROM m3_synapse WHERE dst_node = %s",
            (node_id, node_id))
        from agent_memory.config import settings
        return [(r[0], _strength.effective_retrieval(float(r[1]), float(r[2]), r[3], now,
                                                     settings.m3_syn_decay_lambda))
                for r in cur.fetchall()]


def nearest_in_space(conn: psycopg.Connection, space: str, qcoord: tuple[float, float, float],
                     k: int) -> list[tuple[str, float]]:
    """Exact-fast 3D nearest neighbours in one space by L2 distance (pgvector `<->`)."""
    q = np.asarray(qcoord, dtype="float32")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id::text, coord <-> %s AS dist FROM m3_node WHERE space_id = %s "
            "ORDER BY coord <-> %s LIMIT %s",
            (q, _SPACE_ID[space], q, k),
        )
        return [(r[0], float(r[1])) for r in cur.fetchall()]


def effective_accessibility(conn: psycopg.Connection, node_id: str, *, now: _dt) -> float:
    """Decay-aware accessibility for a single node, computed at `now`. Mirrors v1's
    effective_retrieval formula: retrieval * exp(-(lambda / storage) * delta_days). Reads the
    Phase-3 columns added to m3_node (storage_strength, retrieval_strength, last_activated_at)."""
    with conn.cursor() as cur:
        cur.execute("SELECT retrieval_strength, storage_strength, last_activated_at "
                    "FROM m3_node WHERE id = %s", (node_id,))
        retr, stor, last_act = cur.fetchone()
    from agent_memory.config import settings
    return _strength.effective_retrieval(float(retr), float(stor), last_act, now, settings.m3_acc_decay_lambda)


def reinforce_node_accessibility(conn: psycopg.Connection, node_ids: list[str], *, now: _dt,
                                 lambda_: float, sigma: float, rho: float) -> None:
    """Coupled reinforcement of per-node accessibility (mirrors v1 nodes.reinforce), UNCONDITIONAL
    gate on incoming m3_negative_link: a node with an active incoming link is excluded from
    reinforcement. Caller picks the right node-id set — i.e. the final top-k of retrieve_dual_wave
    (reach-only spread nodes do NOT bump)."""
    if not node_ids:
        return
    gated = _nl.suppressed_in(conn, node_ids)
    clean = [nid for nid in node_ids if nid not in gated]
    if not clean:
        return
    with conn.cursor() as cur:
        cur.execute("SELECT id::text, storage_strength, retrieval_strength, last_activated_at "
                    "FROM m3_node WHERE id::text = ANY(%s)", (clean,))
        rows = cur.fetchall()
        for nid, stor, retr, last_act in rows:
            new_s, new_r = _strength.reinforce_coupled(float(stor), float(retr), last_act, now,
                                                       lambda_, sigma, rho)
            cur.execute("UPDATE m3_node SET storage_strength=%s, retrieval_strength=%s, "
                        "last_activated_at=%s WHERE id::text=%s", (new_s, new_r, now, nid))


def reinforce_synapses_in(conn: psycopg.Connection, top_k_ids: list[str], *, now: _dt,
                          lambda_: float, sigma: float, rho: float,
                          amplify_cited: bool = False, amplify: float = 1.0,
                          recorder=None) -> None:
    """Hebbian, both-endpoints-clean: bump only synapses where BOTH endpoints are in
    top_k_ids AND NEITHER endpoint has an active incoming m3_negative_link.

    Phase 3+ positive arm (amplify_cited=True): within those clean synapses, a synapse with
    EITHER endpoint cited (active m3_positive_link) gets an amplified bump (sigma*amplify, rho
    clamped to <=1.0 so retrieval stays <=1.0). Ordered predicate per synapse:
      negative on either endpoint -> skip (gated nodes already excluded from `clean`; gate beats cite)
      else cited on either endpoint -> amplified sigma/rho
      else -> normal sigma/rho
    amplify_cited=False is byte-identical to the Phase 3 behavior
    (tests/test_phase3plus_amplify.py::test_amplify_cited_false_is_phase3_identical)."""
    if len(top_k_ids) < 2:
        return
    gated = _nl.suppressed_in(conn, top_k_ids)
    clean = [nid for nid in top_k_ids if nid not in gated]
    if len(clean) < 2:
        return
    cited = _pl.cited_in(conn, clean) if amplify_cited else set()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, src_node::text, dst_node::text, storage_strength, retrieval_strength, "
            "       last_activated_at FROM m3_synapse "
            "WHERE src_node::text = ANY(%s) AND dst_node::text = ANY(%s)",
            (clean, clean))
        rows = cur.fetchall()
        for sid, src, dst, stor, retr, last_act in rows:
            if cited and (src in cited or dst in cited):
                s_eff, r_eff = sigma * amplify, min(rho * amplify, 1.0)
            else:
                s_eff, r_eff = sigma, rho
            new_s, new_r = _strength.reinforce_coupled(float(stor), float(retr), last_act, now,
                                                       lambda_, s_eff, r_eff)
            cur.execute("UPDATE m3_synapse SET storage_strength=%s, retrieval_strength=%s, "
                        "last_activated_at=%s WHERE id=%s", (new_s, new_r, now, sid))
            if recorder is not None:
                decision = "amplified" if (cited and (src in cited or dst in cited)) else "normal"
                recorder.on_bump(str(sid), (src, dst), decision, (float(stor), new_s), (float(retr), new_r))
