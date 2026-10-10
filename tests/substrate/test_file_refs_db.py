"""The anchor table: rows tied to their memory (deleted with it), shown back the way they were given."""
import psycopg
import pytest

from agent_memory.spaces import file_refs

H, G = "a" * 64, "b" * 64


def _memory(conn, mid, text="t", at="2026-01-01T00:00:00Z"):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO m3_memory (memory_id, text, created_at) VALUES (%s, %s, %s)", (mid, text, at))


def _ref(path, sha=H):
    return {"path": path, "sha256": sha, "line_start": None, "line_end": None}


def test_written_refs_come_back_as_given(conn):
    _memory(conn, "m1")
    refs = file_refs.normalize([{"path": "a.py", "sha256": H, "line": 4},
                                {"path": "b.py", "sha256": G, "lines": [1, 9]},
                                {"path": "c.py", "sha256": H}], arg="files", save=True)
    assert file_refs.write(conn, "m1", refs) == 3
    assert file_refs.refs_of(conn, ["m1", "nobody"]) == {"m1": [
        {"path": "a.py", "sha256": H, "line": 4},
        {"path": "b.py", "sha256": G, "lines": [1, 9]},
        {"path": "c.py", "sha256": H}]}


def test_write_of_nothing_writes_nothing(conn):
    assert file_refs.write(conn, "m1", None) == 0 and file_refs.write(conn, "m1", []) == 0


def test_anchored_finds_by_exact_path_newest_first(conn):
    _memory(conn, "old", "old text", "2026-01-01T00:00:00Z")
    _memory(conn, "new", "new text", "2026-02-01T00:00:00Z")
    _memory(conn, "other", "x", "2026-03-01T00:00:00Z")
    for mid, path in (("old", "src/a.py"), ("new", "src/a.py"), ("other", "src/ab.py")):
        file_refs.write(conn, mid, [_ref(path)])
    assert file_refs.anchored(conn, ["src/a.py"]) == [("new", "new text"), ("old", "old text")]
    assert file_refs.anchored(conn, []) == []


def test_a_memory_deleted_takes_its_anchors_with_it(conn):
    _memory(conn, "m1")
    file_refs.write(conn, "m1", [_ref("a")])
    with conn.cursor() as cur:
        cur.execute("DELETE FROM m3_memory WHERE memory_id = 'm1'")
        cur.execute("SELECT count(*) FROM m3_file_ref")
        assert cur.fetchone()[0] == 0


def test_the_table_refuses_a_hash_that_is_not_lower_hex(conn):
    _memory(conn, "m1")
    with pytest.raises(psycopg.errors.CheckViolation):
        file_refs.write(conn, "m1", [_ref("a", "A" * 64)])


def test_the_same_anchor_written_twice_is_kept_once(conn):
    """A warm-up replayed after a crash between its commit and clearing its log writes the anchors again."""
    _memory(conn, "m1")
    refs = [_ref("a.py"), {"path": "a.py", "sha256": H, "line_start": 3, "line_end": 3}]
    file_refs.write(conn, "m1", refs)
    file_refs.write(conn, "m1", refs)
    assert len(file_refs.refs_of(conn, ["m1"])["m1"]) == 2
