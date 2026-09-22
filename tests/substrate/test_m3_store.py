# tests/test_m3_store.py
from agent_memory.spaces import store as m3


def test_apply_schema_and_seed_fifteen_spaces(conn):
    # Six harness-domain spaces + the four additive content-facet spaces + the five
    # additive word-layer concept-spaces (ids 11-15). The base-10 assertions are
    # unchanged (the concept-spaces are additive; current retrieval never touches them).
    m3.apply_schema(conn)
    m3.seed_spaces(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT name, source FROM m3_space ORDER BY id")
        rows = dict(cur.fetchall())
    assert set(rows) == {"semantic", "episodic", "procedural", "outcome", "project", "social",
                         "function", "feeling", "fiction", "player_facing",
                         "semantic_c", "function_c", "feeling_c", "fiction_c", "player_facing_c"}
    assert rows["semantic"] == "project_embedding"
    assert rows["project"] == "code_location"
    assert rows["social"] == "project_embedding"
    assert rows["function"] == "facet_embedding"
    assert rows["player_facing"] == "facet_embedding"
    # the concept-spaces are TYPE-TAGS; source='concept_embedding' is informational.
    for cs in ("semantic_c", "function_c", "feeling_c", "fiction_c", "player_facing_c"):
        assert rows[cs] == "concept_embedding"
    m3.seed_spaces(conn)  # idempotent
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM m3_space")
        assert cur.fetchone()[0] == 15
