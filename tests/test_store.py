import datetime as dt
from pathlib import Path
import pytest
from slopymemory.store import Store, DIALECT_ENV, allocate_port, collisions


def mk(name="a", port=8780, db=None, dialect="coding"):
    return Store(name=name, dialect=dialect, port=port, database=db or f"{name}_memory",
                 postgres="system", env={}, created=dt.date(2026, 9, 20))


def test_roundtrip_and_paths(tmp_home):
    s = mk(); s.save()
    assert (tmp_home / "stores" / "a" / "store.toml").exists()
    t = Store.load("a")
    assert t == s and Store.exists("a") and not Store.exists("zzz")
    assert t.lock_file() == tmp_home / "stores" / "a" / "start.lock"
    assert t.log_file() == tmp_home / "logs" / "a.log"


def test_server_env_is_derived_from_the_state_dir_and_the_dialect(tmp_home):
    s = mk(); env = s.server_env()
    d = str(tmp_home / "stores" / "a")
    assert env["DATABASE_URL"] == "postgresql:///a_memory"          # system: peer auth, no role in the URL
    assert env["AM_MCP_PORT"] == "8780" and env["AM_MCP_HOST"] == "127.0.0.1"
    assert env["AM_M3_BUFFER_PATH"] == f"{d}/m3_buffer.jsonl"
    assert env["AM_M3_PROJECTOR_PATH"] == f"{d}/projector.pkl"
    assert env["AM_M3_FACET_PROJECTOR_DIR"] == f"{d}/multispace_projectors"
    assert env["AM_MCP_INVOCATION_LOG"] == f"{d}/mcp_invocations.jsonl"
    for k, v in DIALECT_ENV["coding"].items():
        assert env[k] == v
    s2 = mk(dialect="design")
    assert "AM_M3_RETRIEVAL_MODE" not in s2.server_env()   # design = the substrate's defaults


def test_explicit_env_overrides_win_for_adopted_stores(tmp_home):
    s = mk(); s.env = {"AM_M3_BUFFER_PATH": "/elsewhere/m3_buffer.jsonl", "DATABASE_URL": "postgresql://role@/x"}
    env = s.server_env()
    assert env["AM_M3_BUFFER_PATH"] == "/elsewhere/m3_buffer.jsonl"
    assert env["DATABASE_URL"] == "postgresql://role@/x"


def test_port_allocation_skips_taken(tmp_home):
    assert allocate_port({8780, 8781}) == 8782


def test_collisions_name_every_shared_thing(tmp_home):
    a = mk("a", 8780, "same_db"); b = mk("b", 8780, "same_db")
    msgs = collisions(b, [a])
    assert any("port 8780" in m for m in msgs) and any("database same_db" in m for m in msgs)
    assert collisions(mk("c", 8781, "c_db"), [a]) == []
