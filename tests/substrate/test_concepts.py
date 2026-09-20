# tests/test_concepts.py
"""Word-layer save-side primitives. Teeth for the two load-bearing behaviors:
  1. DEDUP  — the SAME (normalized label, concept-space) across two memories is ONE shared
     concept node with TWO m3_concept_link rows (this shared node is what makes
     texts-sharing-a-concept related at retrieval time).
  2. CLIQUE — a text's concepts are mutually wired via note_related(type='concept'):
     3 concepts -> C(3,2)=3 'concept' synapses (co-occurrence within a text).
Fast suite: DB-only, StubEmbedder (no real model) -> NOT marked embed/extract."""
from agent_memory.embed.stub import StubEmbedder
from agent_memory.spaces import concepts


def _concept_node_count(conn, concept_space):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM m3_node n JOIN m3_space s ON s.id = n.space_id "
                    "WHERE s.name = %s AND n.kind = 'concept'", (concept_space,))
        return cur.fetchone()[0]


def _link_count(conn, concept_node):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM m3_concept_link WHERE concept_node = %s", (concept_node,))
        return cur.fetchone()[0]


def test_same_concept_dedups_to_one_node(conn):
    """Same (normalized label, space) across two texts = ONE concept node, TWO concept->text links.
    Also proves the dedup key is the NORMALIZED label (case + surrounding whitespace collapse)."""
    emb = StubEmbedder(dim=768)

    n1 = concepts.place_or_get_concept(conn, "function", "cooperation", emb)
    concepts.link_concept_to_memory(conn, n1, "m1")

    # Different surface form -> same normalized key -> must reuse the SAME node.
    n2 = concepts.place_or_get_concept(conn, "function", "  COOPERATION  ", emb)
    concepts.link_concept_to_memory(conn, n2, "m2")

    assert n1 == n2, "differently-cased/whitespaced same concept must dedup to one node"
    assert _concept_node_count(conn, "function_c") == 1, "exactly one concept node in function_c"
    assert _link_count(conn, n1) == 2, "one shared node, two concept->text links (m1 and m2)"

    # The stored node is normalized + carries its own 768 concept_embedding (not null).
    with conn.cursor() as cur:
        cur.execute("SELECT label, (concept_embedding IS NOT NULL) FROM m3_node WHERE id = %s", (n1,))
        label, has_emb = cur.fetchone()
    assert label == "cooperation"
    assert has_emb, "place_or_get_concept must store the label's 768 embedding"

    # Re-save is idempotent: re-place + re-link m1 must not spawn a node or a duplicate link row.
    again = concepts.place_or_get_concept(conn, "function", "cooperation", emb)
    concepts.link_concept_to_memory(conn, again, "m1")
    assert again == n1
    assert _concept_node_count(conn, "function_c") == 1
    assert _link_count(conn, n1) == 2

    # Same label but a DIFFERENT concept-space is a DIFFERENT concept node (space is part of the key).
    other = concepts.place_or_get_concept(conn, "feeling", "cooperation", emb)
    assert other != n1
    assert _concept_node_count(conn, "feeling_c") == 1


def test_text_concepts_form_a_clique(conn):
    """A text's concepts are mutually wired (co-occurrence) via note_related(type='concept').
    3 concepts -> C(3,2)=3 'concept' synapses among exactly those nodes; each linked to the memory."""
    emb = StubEmbedder(dim=768)
    typed = [("function", "cooperation"), ("feeling", "tension"), ("fiction", "the heist")]

    nodes = concepts.place_text_concepts(conn, "m1", typed, emb)
    assert len(nodes) == 3
    assert len(set(nodes)) == 3

    with conn.cursor() as cur:
        # C(3,2) = 3 concept synapses, all among the returned nodes.
        cur.execute("SELECT count(*) FROM m3_synapse WHERE type = 'concept' "
                    "AND src_node::text = ANY(%s) AND dst_node::text = ANY(%s)", (nodes, nodes))
        assert cur.fetchone()[0] == 3, "3 concepts must form a C(3,2)=3 co-occurrence clique"

        # No stray concept synapses outside the returned node set.
        cur.execute("SELECT count(*) FROM m3_synapse WHERE type = 'concept'")
        assert cur.fetchone()[0] == 3

        # Each concept links to the memory.
        cur.execute("SELECT count(*) FROM m3_concept_link WHERE memory_id = 'm1'")
        assert cur.fetchone()[0] == 3

    # Re-saving the same text is idempotent: no duplicate synapses (note_related ON CONFLICT DO NOTHING).
    concepts.place_text_concepts(conn, "m1", typed, emb)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM m3_synapse WHERE type = 'concept'")
        assert cur.fetchone()[0] == 3
