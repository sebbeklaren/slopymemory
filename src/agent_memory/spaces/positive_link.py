# src/agent_memory/spaces/positive_link.py
"""m3 positive_link: the cite-driven twin of negative_link. v1 wires only the explicit-cite source
(`note_used`). `kind`/`scope` are provenance — the retrieve_dual_wave bump phase branches on LINK
PRESENCE only, never on them. Unary (a cite names one node); FK-references m3_node."""
import psycopg


def note_used(conn: psycopg.Connection, node_id: str, *, bump: float = 1.0) -> None:
    """Record/strengthen 'node was used/cited'. Idempotent on (node_id, kind): a repeat bumps
    strength (confidence-by-repetition). Twin of note_supersession; the synapse-strength effect is
    separate, in retrieve_dual_wave's bump phase."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO m3_positive_link (node_id, kind, scope, strength) "
            "VALUES (%s, 'validation', 'objective', %s) "
            "ON CONFLICT (node_id, kind) "
            "DO UPDATE SET strength = m3_positive_link.strength + EXCLUDED.strength, updated_at = now()",
            (node_id, bump),
        )


def cited_in(conn: psycopg.Connection, node_ids: list[str]) -> set[str]:
    """Pool-scoped: {nid for nid in node_ids if nid has an active positive_link (strength > 0)}.
    Used by the bump-phase amplify check (mirror of negative_link.suppressed_in)."""
    if not node_ids:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT node_id::text FROM m3_positive_link "
            "WHERE strength > 0 AND node_id::text = ANY(%s)",
            (list(node_ids),),
        )
        return {r[0] for r in cur.fetchall()}
