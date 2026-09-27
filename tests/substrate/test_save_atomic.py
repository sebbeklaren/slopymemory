"""One save is one transaction: a failure part-way leaves no row of that memory and the session map as it was."""
import psycopg
import pytest

import agent_memory.spaces.live_store_state as ls
from tests_support_fakes import warm_store


def _rows(conn, mid):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM m3_memory WHERE memory_id = %s", (mid,))
        m = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM m3_node WHERE source_ref = %s", (mid,))
        return m, cur.fetchone()[0]


def test_a_failure_after_the_memory_row_leaves_nothing_and_the_session_map_unchanged(conn, monkeypatch):
    s = warm_store()
    def boom(*a, **k):
        raise psycopg.errors.DiskFull("simulated")
    monkeypatch.setattr(ls, "place_text_concepts", boom)
    with pytest.raises(psycopg.errors.DiskFull):
        s.save(conn, "a decision", session_key="s1", memory_id="m1", save_concepts=[["function", "x"]])
    assert _rows(conn, "m1") == (0, 0)
    assert s._session_nodes == {}


def test_a_successful_save_joins_the_session_after_commit(conn):
    s = warm_store()
    s.save(conn, "first", session_key="s1", memory_id="a")
    s.save(conn, "second", session_key="s1", memory_id="b")
    assert len(s._session_nodes["s1"]) == 2 and _rows(conn, "b")[0] == 1


def test_supersedes_accepts_a_target_named_in_known_ids_and_defers_the_link(conn):
    s = warm_store()
    out = s.save(conn, "fix", session_key="s", memory_id="new", supersedes="held-target", known_ids={"held-target"})
    assert out["status"] == "saved" and out["supersedes_pending"] == "held-target"
