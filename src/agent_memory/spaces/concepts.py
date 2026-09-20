# src/agent_memory/spaces/concepts.py
"""Word-layer save-side primitives: dedup/place concept nodes in the concept-spaces, link
concept->text (m3_concept_link), wire the per-text co-occurrence clique (note_related type='concept').
Population-level: a concept is ONE shared node keyed by (normalized label, concept-space) — that
shared node is what makes texts-sharing-a-concept related. Concept lighting is 768-cosine;
this module only places/dedups/links/wires. Concepts are NOT 3D-placed (dummy coord)."""
from itertools import combinations

from agent_memory.spaces import store as m3

CONCEPT_SPACES = {"semantic": "semantic_c", "function": "function_c", "feeling": "feeling_c",
                  "fiction": "fiction_c", "player_facing": "player_facing_c"}


def _find_concept(conn, concept_space, norm_label):
    with conn.cursor() as cur:
        cur.execute("SELECT n.id::text FROM m3_node n JOIN m3_space s ON s.id=n.space_id "
                    "WHERE s.name=%s AND n.kind='concept' AND n.label=%s LIMIT 1",
                    (concept_space, norm_label))
        r = cur.fetchone()
        return r[0] if r else None


_DUMMY_COORD = (0.0, 0.0, 0.0)   # concepts are NOT 3D-placed under (B); lighting is 768-cosine


def place_or_get_concept(conn, base_space, label, embedder) -> str:
    """Dedup by (normalized label, concept-space). Reuse the existing node or place a new one:
    embed the label -> store its 768 vector in concept_embedding (dummy 3D coord). No projector."""
    import numpy as np
    cspace = CONCEPT_SPACES[base_space]
    norm = " ".join(label.strip().lower().split())
    existing = _find_concept(conn, cspace, norm)
    if existing:
        return existing
    nid = m3.place_node(conn, cspace, label=norm, kind="concept", coord=_DUMMY_COORD, source_ref=norm)
    emb = np.asarray(embedder.embed_document(norm), dtype="float32")
    with conn.cursor() as cur:
        cur.execute("UPDATE m3_node SET concept_embedding = %s WHERE id = %s", (emb, nid))
    return nid


def link_concept_to_memory(conn, concept_node, memory_id, strength=1.0) -> None:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO m3_concept_link (concept_node, memory_id, strength) VALUES (%s,%s,%s) "
                    "ON CONFLICT (concept_node, memory_id) DO NOTHING", (concept_node, memory_id, strength))


def wire_concept_clique(conn, concept_nodes) -> None:
    for a, b in combinations(sorted(set(concept_nodes)), 2):
        m3.note_related(conn, a, b, type="concept")     # burst runs max-relax -> no inflation


def place_text_concepts(conn, memory_id, typed_concepts, embedder) -> list[str]:
    """typed_concepts: list of (base_space, label). Place/dedup each (768-cosine layer), link->memory,
    wire the co-occurrence clique."""
    nodes = []
    for base_space, label in typed_concepts:
        nid = place_or_get_concept(conn, base_space, label, embedder)
        link_concept_to_memory(conn, nid, memory_id)
        nodes.append(nid)
    wire_concept_clique(conn, nodes)
    return nodes
