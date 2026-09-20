import datetime as dt
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


def test_a_malformed_store_toml_is_a_named_config_error_not_a_crash(tmp_home):
    from slopymemory.paths import ConfigError
    from slopymemory.store import all_stores, store_problems
    mk("good").save()
    bad = tmp_home / "stores" / "bad"; bad.mkdir(parents=True)
    (bad / "store.toml").write_text('name = "bad"\ndialect = "coding"\nport = 8781\npostgres = "system"\ncreated = 2026-09-20\n')  # no database
    with pytest.raises(ConfigError) as e:
        Store.load("bad")
    assert "database" in str(e.value) and "SETUP.md#registry" in str(e.value) and str(bad / "store.toml") in str(e.value)
    (bad / "store.toml").write_text("this is not toml ][")
    with pytest.raises(ConfigError, match="SETUP.md#registry"):
        Store.load("bad")
    odd = tmp_home / "stores" / "odd"; odd.mkdir()
    (odd / "store.toml").write_text('name = "odd"\ndialect = "poetry"\nport = 8782\ndatabase = "odd_db"\npostgres = "system"\ncreated = 2026-09-20\n')
    with pytest.raises(ConfigError, match="dialect"):
        Store.load("odd")
    dash = tmp_home / "stores" / "dash"; dash.mkdir()            # a database name outside [a-z0-9_]: list would
    (dash / "store.toml").write_text('name = "dash"\ndialect = "coding"\nport = 8783\ndatabase = "bad-name"\npostgres = "system"\ncreated = 2026-09-20\n')
    with pytest.raises(ConfigError, match="database"):           # otherwise crash on pg_database_size's identifier guard
        Store.load("dash")
    assert [s.name for s in all_stores()] == ["good"]          # the good store is still served
    problems = store_problems()
    assert len(problems) == 3 and all("SETUP.md#registry" in p for p in problems)
    assert any("bad" in p for p in problems) and any("odd" in p for p in problems) and any("dash" in p for p in problems)


def test_save_is_atomic_no_tmp_file_remains(tmp_home):
    s = mk(); s.save()
    assert Store.load("a") == s
    assert [p.name for p in (tmp_home / "stores" / "a").iterdir()] == ["store.toml"]


def test_state_size_counts_the_files_the_server_env_names_once_each(tmp_home, tmp_path):
    """Adopted stores keep their files where the [env] table says; the size must follow those paths, and a
    file named both by a dir and by itself is counted once."""
    s = mk(); s.save()
    elsewhere = tmp_path / "elsewhere"; (elsewhere / "proj").mkdir(parents=True)
    (elsewhere / "buf.jsonl").write_bytes(b"x" * 3000)
    (elsewhere / "proj" / "p.npy").write_bytes(b"y" * 5000)
    (s.path() / "local.bin").write_bytes(b"z" * 2000)
    s.env = {"AM_M3_BUFFER_PATH": str(elsewhere / "buf.jsonl"), "AM_M3_FACET_PROJECTOR_DIR": str(elsewhere / "proj"),
             "AM_MCP_INVOCATION_LOG": str(s.path() / "local.bin")}     # inside the state dir: not double-counted
    s.save()
    assert Store.load("a").state_size() == 3000 + 5000 + 2000 + s.toml_file().stat().st_size
