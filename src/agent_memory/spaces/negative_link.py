# src/agent_memory/spaces/negative_link.py
"""m3 parallel to the 768 negative_link relation. v1 wires only the supersession source.
`kind`/`scope` are provenance — gating logic in retrieve_dual_wave branches on LINK PRESENCE only,
never on them. FK-references m3_node (NOT the 768 nodes table)."""
import psycopg


def note_supersession(conn: psycopg.Connection, source_id: str, target_id: str, *, bump: float = 1.0) -> None:
    """Record/strengthen 'source supersedes target' (B -> A) on m3. Idempotent on
    (source, target, kind): a repeat bumps strength (confidence-by-repetition)."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO m3_negative_link (source_id, target_id, kind, scope, strength) "
            "VALUES (%s, %s, 'supersession', 'objective', %s) "
            "ON CONFLICT (source_id, target_id, kind) "
            "DO UPDATE SET strength = m3_negative_link.strength + EXCLUDED.strength, updated_at = now()",
            (source_id, target_id, bump),
        )


def suppressed_in(conn: psycopg.Connection, node_ids: list[str]) -> set[str]:
    """Pool-scoped: {nid for nid in node_ids if nid has an active incoming negative_link}.
    Used by the retrieve_dual_wave gate — both for the per-node accessibility bump check and
    for the synapse Hebbian both-endpoints-clean check (called once per top-k, cheap)."""
    if not node_ids:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT target_id::text FROM m3_negative_link "
            "WHERE strength > 0 AND target_id::text = ANY(%s)",
            (list(node_ids),),
        )
        return {r[0] for r in cur.fetchall()}


def target_strengths(conn: psycopg.Connection, node_ids: list[str]) -> dict[str, float]:
    """Pool-scoped magnitude: {nid: sum of active incoming negative_link strengths} for nids that
    have any. `suppressed_in` gives PRESENCE; the supersession seed down-weight needs the MAGNITUDE
    (multiply-superseded -> stronger demotion). Only nids with strength > 0 appear."""
    if not node_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT target_id::text, COALESCE(SUM(strength), 0.0) FROM m3_negative_link "
            "WHERE strength > 0 AND target_id::text = ANY(%s) GROUP BY target_id",
            (list(node_ids),),
        )
        return {r[0]: float(r[1]) for r in cur.fetchall()}


def note_supersession_for_aspects(conn: psycopg.Connection,
                                  by_node_ids_by_space: dict[str, str],
                                  stale_node_ids_by_space: dict[str, str],
                                  *, bump: float = 1.0) -> None:
    """Per-aspect supersession expansion: given a fact-level pair B → A as two
    {space: node_id} maps (the constellations), emit one negative_link row per SHARED space
    (semantic-B → semantic-A AND project-B → project-A, etc.). Spaces present on only one side
    are skipped (the gold's responsibility to keep aspect-shapes aligned where the supersession is
    actually meant to apply)."""
    shared = set(by_node_ids_by_space) & set(stale_node_ids_by_space)
    for sp in shared:
        note_supersession(conn, by_node_ids_by_space[sp], stale_node_ids_by_space[sp], bump=bump)
