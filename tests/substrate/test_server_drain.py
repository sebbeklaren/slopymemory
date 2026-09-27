"""Held saves are written in order, through the same save path, as soon as the database answers; an already-committed
record is not placed twice; a failure that is not the connection is kept in held-failed/ and the rest go on."""
import psycopg

import agent_memory.durability as du
from agent_memory.mcp import server
from tests_support_fakes import srv, down, up  # noqa: F401  (fixture)


def _rows(conn, text):
    with conn.cursor() as cur:
        cur.execute("SELECT memory_id FROM m3_memory WHERE text = %s ORDER BY created_at", (text,))
        return [r[0] for r in cur.fetchall()]


def test_held_saves_are_written_in_order_when_the_database_answers(srv, conn, monkeypatch):
    down(monkeypatch)
    a = server.memory_save(tenant="t", text="first held")["memory_id"]
    b = server.memory_save(tenant="t", text="second held")["memory_id"]
    up(monkeypatch)
    out = server.memory_save(tenant="t", text="after")
    assert out["status"] == "saved"
    assert out["recovered"] == {"written": 2, "failed": 0, "failed_errors": []}
    assert _rows(conn, "first held") == [a] and _rows(conn, "second held") == [b]
    assert du.held_paths() == []
    assert "recovered" not in server.memory_save(tenant="t", text="later")      # reported once


def test_drain_releases_a_record_already_committed(srv, conn, monkeypatch):
    down(monkeypatch)
    mid = server.memory_save(tenant="t", text="committed then crashed")["memory_id"]
    up(monkeypatch)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO m3_memory (memory_id, text) VALUES (%s, 'committed then crashed')", (mid,))
    server.memory_retrieve(tenant="t", query="q")
    assert _rows(conn, "committed then crashed") == [mid] and du.held_paths() == []


def test_a_non_connection_failure_moves_the_record_to_failed_and_the_rest_drain(srv, conn, monkeypatch):
    down(monkeypatch)
    server.memory_save(tenant="t", text="poison")
    server.memory_save(tenant="t", text="fine")
    up(monkeypatch)
    real = server.handlers.memory_save

    def poison(conn, store, tenant, sk, text, *a, **k):
        if text == "poison":
            raise psycopg.errors.CheckViolation("simulated bad row")
        return real(conn, store, tenant, sk, text, *a, **k)

    monkeypatch.setattr(server.handlers, "memory_save", poison)
    out = server.memory_retrieve(tenant="t", query="q")
    assert out["recovered"]["written"] == 1 and out["recovered"]["failed"] == 1
    assert "CheckViolation" in out["recovered"]["failed_errors"][0]
    assert du.counts() == (0, 1) and _rows(conn, "fine")


def test_a_connection_error_stops_the_drain_and_keeps_order(srv, monkeypatch):
    down(monkeypatch)
    for t in ("one", "two", "three"):
        server.memory_save(tenant="t", text=t)
    order = [du.load(p)["text"] for p in du.held_paths()]
    server.memory_retrieve(tenant="t", query="q")                    # still down
    assert [du.load(p)["text"] for p in du.held_paths()] == order == ["one", "two", "three"]


def test_drain_moves_an_unreadable_record_to_failed(srv, monkeypatch):
    held = du.state_dir() / "held"
    held.mkdir(parents=True, exist_ok=True)
    (held / "00000000000000000001-bad.json").write_text("{torn")
    (held / "00000000000000000002-x.tmp").write_text("partial")
    server.memory_retrieve(tenant="t", query="q")
    assert du.counts() == (0, 1)
    assert (held / "00000000000000000002-x.tmp").exists()               # a .tmp is never a record


def test_a_held_correction_of_a_held_memory_is_linked_after_both_drain(srv, conn, monkeypatch):
    down(monkeypatch)
    old = server.memory_save(tenant="t", text="the port is 8780")["memory_id"]
    server.memory_save(tenant="t", text="the port is 8781", supersedes=old)
    up(monkeypatch)
    server.memory_retrieve(tenant="t", query="q")
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM m3_memory_supersession WHERE stale_memory_id = %s", (old,))
        assert cur.fetchone()[0] == 1
