"""The embedded Postgres: the embedded-postgres wheel's binaries run a cluster under the home's `pg/` for a
machine without a system Postgres. The `pg`-marked tests start a REAL cluster in the test home and stop it in
`finally` — a leaked postmaster is a failure of the test, not of the machine."""
import subprocess
from pathlib import Path
import pytest
from slopymemory import embedded_pg, provision
from slopymemory.store import Store


def _postmasters_for(pgdata: Path) -> list[str]:
    out = subprocess.run(["pgrep", "-af", str(pgdata)], capture_output=True, text=True).stdout
    return [l for l in out.splitlines() if "bin/postgres" in l]


@pytest.mark.pg
def test_embedded_server_starts_once_survives_reentry_and_provisions_pgvector(tmp_home):
    epg = embedded_pg.EmbeddedPostgres(tmp_home / "pg")
    assert not epg.is_running() and epg.database_size("x") is None      # down: None, and no start as a side effect
    assert not (tmp_home / "pg").exists()
    try:
        epg.ensure_running()
        assert epg.is_running()
        assert (tmp_home / "pg" / "postmaster.pid").exists()
        assert (tmp_home / "pg" / ".s.PGSQL.5432").is_socket()          # the socket lives IN the data dir
        epg.ensure_running()                                             # idempotent: no second start
        assert epg.has_pgvector()
        assert epg.can_provision()
        epg.create_database("slopymem_epg_test")
        assert epg.database_exists("slopymem_epg_test")
        assert epg.database_size("slopymem_epg_test") > 0
        d = epg.describe()
        assert "vector" in d and "running" in d and str(tmp_home / "pg") in d
        # The product: the DSN a store's server is given reaches the database, and pgvector is installed in it.
        import psycopg
        with psycopg.connect(embedded_pg.uri(tmp_home / "pg", "slopymem_epg_test")) as c:
            assert c.execute("select extversion from pg_extension where extname = 'vector'").fetchone() is not None
            assert c.execute("show listen_addresses").fetchone() == ("",)    # no TCP port, ever
        epg.drop_database("slopymem_epg_test")
        assert not epg.database_exists("slopymem_epg_test")
        with pytest.raises(ValueError, match="unsafe database name"):
            epg.database_exists("bad'name")
    finally:
        stopped = epg.stop()
    assert stopped is True
    assert not epg.is_running()
    assert epg.stop() is False                                           # nothing running: False, not an error
    assert _postmasters_for(tmp_home / "pg") == []
    assert (tmp_home / "pg" / "PG_VERSION").exists()                     # the data survives a stop


def test_ensure_running_refuses_a_socket_path_too_long_for_unix_sockets_without_starting(tmp_path):
    deep = tmp_path / ("d" * 120)
    with pytest.raises(provision.InitRefused) as e:
        embedded_pg.EmbeddedPostgres(deep / "pg").ensure_running()
    assert "SLOPYMEM_HOME" in str(e.value) and "SETUP.md#postgres" in str(e.value)
    assert not (deep / "pg").exists()


def test_uri_is_the_unix_socket_dsn_of_the_data_dir(tmp_path):
    pgdata = tmp_path / "pg"
    assert embedded_pg.uri(pgdata, "e_memory") == f"host={pgdata} port=5432 user=postgres dbname=e_memory"
    assert embedded_pg.uri(pgdata, "e_memory").startswith(embedded_pg.uri_prefix(pgdata))
    assert not embedded_pg.uri(tmp_path / "other", "e_memory").startswith(embedded_pg.uri_prefix(pgdata))


def test_a_store_on_embedded_postgres_gets_the_embedded_uri(tmp_home):
    st = Store(name="e", dialect="coding", port=8790, database="e_memory", postgres="embedded")
    assert st.server_env()["DATABASE_URL"].startswith(embedded_pg.uri_prefix(tmp_home / "pg"))
    assert st.server_env()["DATABASE_URL"] == embedded_pg.uri(tmp_home / "pg", "e_memory")
    sys_store = Store(name="s", dialect="coding", port=8791, database="s_memory", postgres="system")
    assert sys_store.server_env()["DATABASE_URL"] == "postgresql:///s_memory"


def test_availability_is_the_wheels_binaries_and_its_pgvector(monkeypatch):
    assert embedded_pg.unavailable_reason() is None and embedded_pg.available()      # this venv has the wheel
    monkeypatch.setattr(embedded_pg, "BIN", None)
    monkeypatch.setattr(embedded_pg, "IMPORT_ERROR", ImportError("No module named 'embedded_postgres'"))
    reason = embedded_pg.unavailable_reason()
    assert not embedded_pg.available() and "embedded_postgres" in reason and "install" in reason
    with pytest.raises(provision.InitRefused, match="SETUP.md#postgres"):
        embedded_pg.EmbeddedPostgres(Path("/nonexistent/pg")).ensure_running()


def test_has_pgvector_and_describe_answer_without_a_running_server(tmp_home):
    epg = embedded_pg.EmbeddedPostgres(tmp_home / "pg")
    assert epg.has_pgvector() is True                # the wheel ships the extension: no server needed to know
    assert epg.can_provision() is True
    d = epg.describe()
    assert "not running" in d and "PostgreSQL" in d and str(tmp_home / "pg") in d


class NoPg:                       # a system Postgres that cannot provision
    def can_provision(self): return False
    def describe(self): return "no system Postgres"


class Pg:
    def can_provision(self): return True


class Broken:                     # psql missing or the server down: the check itself refuses
    def can_provision(self): raise provision.InitRefused("psql not found — install the PostgreSQL client tools — see SETUP.md#postgres")


def test_init_on_a_machine_without_system_postgres_offers_embedded(tmp_home):
    choice = provision.choose_backend(system=NoPg(), embedded_available=True, requested=None)
    assert choice.backend == "embedded"
    assert "system Postgres" in choice.note and "SETUP.md#postgres" in choice.note


def test_choose_backend_prefers_an_existing_embedded_data_dir_then_a_working_system_postgres(tmp_home):
    assert provision.choose_backend(Pg(), True, None) == ("system", None)
    (tmp_home / "pg").mkdir(parents=True)
    assert provision.choose_backend(Pg(), True, None) == ("embedded", None)       # the user already chose it
    assert provision.choose_backend(Pg(), False, None) == ("system", None)        # ...unless the wheel is gone


def test_choose_backend_carries_the_system_failure_as_the_note(tmp_home):
    c = provision.choose_backend(Broken(), True, None)
    assert c.backend == "embedded" and "psql not found" in c.note


def test_choose_backend_honours_the_request_or_refuses_with_the_hint(tmp_home):
    assert provision.choose_backend(NoPg(), True, "embedded") == ("embedded", None)
    with pytest.raises(provision.InitRefused, match="slopymem_template"):
        provision.choose_backend(NoPg(), True, "system")
    with pytest.raises(provision.InitRefused, match="psql not found"):
        provision.choose_backend(Broken(), True, "system")
    with pytest.raises(provision.InitRefused, match="SETUP.md#postgres"):
        provision.choose_backend(Pg(), False, "embedded")
    with pytest.raises(provision.InitRefused, match="system | embedded"):
        provision.choose_backend(Pg(), True, "sqlite")


def test_choose_backend_refuses_when_neither_can_naming_both(tmp_home):
    with pytest.raises(provision.InitRefused) as e:
        provision.choose_backend(Broken(), False, None)
    assert "psql not found" in str(e.value) and "embedded" in str(e.value) and "SETUP.md#postgres" in str(e.value)
    with pytest.raises(provision.InitRefused, match="slopymem_template"):
        provision.choose_backend(NoPg(), False, None)
