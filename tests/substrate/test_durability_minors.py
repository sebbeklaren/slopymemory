"""Smaller durability guarantees: a connection closed when the schema step fails, passwords hidden in
any letter case, a deferred fit that is not reported as a healthy database, a partial drain still reported, a
rejected save told apart from a database that is down."""
import dataclasses

import psycopg
import pytest

import agent_memory.durability as du
from agent_memory.mcp import server
from tests_support_fakes import srv, down, up  # noqa: F401  (fixture)


def test_a_failed_schema_step_closes_the_connection(monkeypatch):
    closed = []

    class C:
        def close(self):
            closed.append(True)
    monkeypatch.setattr(server.psycopg, "connect", lambda *a, **k: C())
    monkeypatch.setattr(server, "register_vector", lambda c: None)
    monkeypatch.setattr(server, "_state", {"schema_ready": False, "failing": False})

    def boom(conn):
        raise psycopg.errors.InsufficientPrivilege("no")
    monkeypatch.setattr(server, "_ensure_schema", boom)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        server._connect()
    assert closed == [True]


def test_a_password_is_hidden_in_any_letter_case():
    assert "s3cret" not in du.clean_error(RuntimeError("could not connect: PASSWORD=" + "s3cret host=x"))


def test_a_rejected_save_is_not_worded_as_a_database_that_is_down(srv, monkeypatch):
    def reject(*a, **k):
        raise psycopg.errors.CharacterNotInRepertoire("invalid byte sequence")
    monkeypatch.setattr(server.handlers, "memory_save", reject)
    out = server.memory_save(tenant="t", text="bad bytes")
    assert out["status"] == "held" and "rejected this save" in out["message"] and "database is back" not in out["message"]


def test_a_partial_drain_is_still_reported(srv, conn, monkeypatch):
    down(monkeypatch)
    server.memory_save(tenant="t", text="one")
    server.memory_save(tenant="t", text="two")
    up(monkeypatch)
    real, calls = server.handlers.memory_save, []

    def second_fails(conn, store, tenant, sk, text, *a, **k):
        calls.append(text)
        if len(calls) == 2:
            raise psycopg.OperationalError("connection lost mid-drain")
        return real(conn, store, tenant, sk, text, *a, **k)
    monkeypatch.setattr(server.handlers, "memory_save", second_fails)
    first = server.memory_retrieve(tenant="t", query="q")             # drains "one", loses the connection on "two"
    assert first["status"] == "unavailable" and first["recovered"]["written"] == 1   # reported at once, not lost
    monkeypatch.setattr(server.handlers, "memory_save", real)
    second = server.memory_retrieve(tenant="t", query="q")
    assert second["recovered"]["written"] == 1 and du.held_paths() == []


def test_a_deferred_fit_marks_the_database_failing(srv, conn, monkeypatch):
    from agent_memory.spaces.live_store_state import LiveStore
    from tests_support_fakes import Emb, stub_fit
    import agent_memory.spaces.live_store_state as ls
    cold = LiveStore(Emb(), mode="bootstrap", bootstrap_n=1, fit_fn=stub_fit)
    monkeypatch.setattr(server, "_store", cold)

    def boom(*a, **k):
        raise psycopg.errors.AdminShutdown("fit failed")
    monkeypatch.setattr(ls, "save_memory", boom)
    out = server.memory_save(tenant="t", text="first")
    assert out["status"] == "buffered" and out.get("fit_deferred")
    assert du.read_status(du.state_dir())["state"] == "failing"



def test_a_fault_before_the_save_itself_is_the_database_down_not_a_rejection(srv, monkeypatch):
    """A missing vector extension, lost privileges or a read-only database fail in the connection or schema step,
    before this record is written: that is the database, and it is said so and marked failing."""
    def no_vector(conn):
        raise psycopg.ProgrammingError("vector type not found in the database")
    monkeypatch.setattr(server, "register_vector", no_vector)
    out = server.memory_save(tenant="t", text="x")
    assert out["status"] == "held" and "database is back" in out["message"]
    assert du.read_status(du.state_dir())["state"] == "failing"


def test_a_held_save_after_a_partial_drain_carries_what_was_written(srv, conn, monkeypatch):
    down(monkeypatch)
    server.memory_save(tenant="t", text="one")
    server.memory_save(tenant="t", text="two")
    up(monkeypatch)
    real, calls = server.handlers.memory_save, []

    def second_fails(conn, store, tenant, sk, text, *a, **k):
        calls.append(text)
        if len(calls) == 2:
            raise psycopg.OperationalError("connection lost mid-drain")
        return real(conn, store, tenant, sk, text, *a, **k)
    monkeypatch.setattr(server.handlers, "memory_save", second_fails)
    out = server.memory_save(tenant="t", text="three")                 # drains "one", loses the connection on "two"
    assert out["status"] == "held" and out["recovered"]["written"] == 1
