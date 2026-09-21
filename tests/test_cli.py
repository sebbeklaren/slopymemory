import io, sys
import pytest
from slopymemory import cli, provision
from slopymemory.registry import Registry
from slopymemory.store import Store


class FakePg:
    def __init__(self): self.created = []; self.dropped = []; self.sizes = {}; self.asked = []; self.running = True
    def database_exists(self, n): return n in self.created
    def database_size(self, n): return self.sizes.get(n)
    def create_database(self, n): self.created.append(n)
    def drop_database(self, n): self.dropped.append(n)
    def has_pgvector(self): return True
    def describe(self): return "fake"
    def can_provision(self): return True
    def is_running(self): return self.running


@pytest.fixture
def fake_pg(monkeypatch):
    """One fake stands in for BOTH backends; `asked` records which one each command wanted."""
    pg = FakePg()
    monkeypatch.setattr(cli, "postgres", lambda backend="system": (pg.asked.append(backend), pg)[1])
    monkeypatch.setattr(cli.embedded_pg, "available", lambda: True)          # the wheel, whatever this box has
    return pg


def run(argv, stdin=""):
    sys.stdin = io.StringIO(stdin)
    out = io.StringIO(); err = io.StringIO()
    old = sys.stdout, sys.stderr; sys.stdout, sys.stderr = out, err
    try:
        code = cli.main(argv)
    finally:
        sys.stdout, sys.stderr = old
    return code, out.getvalue() + err.getvalue()


def test_init_prints_the_plan_and_asks_then_creates(tmp_home, tmp_path, fake_pg, monkeypatch):
    repo = tmp_path / "proj"; repo.mkdir(); monkeypatch.chdir(repo)
    code, out = run(["init"], stdin="n\n")
    assert code == 1 and "create database proj_memory" in out and fake_pg.created == []
    code, out = run(["init", "--yes"])
    assert code == 0 and fake_pg.created == ["proj_memory"] and Registry.load().resolve(repo) == "proj"
    code, out = run(["init", "--yes"])
    assert code == 1 and "already resolves to proj" in out and "SETUP.md#registry" in out


def test_link_unlink_list(tmp_home, tmp_path, fake_pg, monkeypatch):
    a = tmp_path / "a"; a.mkdir(); b = tmp_path / "b"; b.mkdir(); monkeypatch.chdir(a)
    assert run(["init", "--yes"])[0] == 0
    monkeypatch.chdir(b)
    assert run(["link", "a", "--yes"])[0] == 0
    assert Registry.load().resolve(b) == "a"
    code, out = run(["list"])
    assert code == 0 and "a" in out and str(b) in out and "down" in out
    assert run(["unlink", "--yes"])[0] == 0
    assert Registry.load().resolve(b) is None
    code, out = run(["link", "nope", "--yes"])
    assert code == 1 and "no store named nope" in out


def test_start_and_stop_report_and_refuse_bad_arguments(tmp_home, tmp_path, fake_pg, monkeypatch):
    a = tmp_path / "a"; a.mkdir(); monkeypatch.chdir(a); run(["init", "--yes"])
    code, out = run(["stop"])
    assert code == 1 and "SETUP.md#servers" in out
    code, out = run(["stop", "nope"])
    assert code == 1 and "no store named" in out and "SETUP.md#registry" in out
    code, out = run(["stop", "a", "--all"])
    assert code == 1 and "OR --all, not both" in out
    code, out = run(["stop", "a"])
    assert code == 0 and "was not running" in out


def test_remove_asks_twice_and_refuses_yes(tmp_home, tmp_path, fake_pg, monkeypatch):
    a = tmp_path / "a"; a.mkdir(); monkeypatch.chdir(a); run(["init", "--yes"])
    code, out = run(["remove", "a", "--yes"])
    assert code == 2 and "refuses --yes" in out
    code, out = run(["remove", "a"], stdin="y\nn\n")
    assert code == 1 and Store.exists("a")
    code, out = run(["remove", "a"], stdin="y\na\n")     # the second question wants the name typed
    assert code == 0 and not Store.exists("a") and Registry.load().resolve(a) is None and fake_pg.dropped == ["a_memory"]


def test_init_reports_a_failing_createdb_instead_of_a_traceback(tmp_home, tmp_path, monkeypatch):
    class CreateFails(FakePg):
        def create_database(self, n):
            raise provision.InitRefused("createdb: FATAL: boom — see SETUP.md#postgres")
    monkeypatch.setattr(cli, "postgres", lambda backend="system": CreateFails())
    repo = tmp_path / "proj"; repo.mkdir(); monkeypatch.chdir(repo)
    code, out = run(["init", "--yes"])
    assert code == 1 and "FATAL: boom" in out and "SETUP.md#postgres" in out
    assert not Store.exists("proj") and Registry.load().resolve(repo) is None


def test_remove_reports_a_failing_psql_instead_of_a_traceback(tmp_home, tmp_path, fake_pg, monkeypatch):
    a = tmp_path / "a"; a.mkdir(); monkeypatch.chdir(a); run(["init", "--yes"])
    def boom(n):
        raise provision.InitRefused("psql: FATAL: boom — see SETUP.md#postgres")
    monkeypatch.setattr(fake_pg, "database_exists", boom)
    code, out = run(["remove", "a"], stdin="y\na\n")
    assert code == 1 and "FATAL: boom" in out and "SETUP.md#postgres" in out and Store.exists("a")


def test_list_reports_a_malformed_store_and_still_lists_the_others(tmp_home, tmp_path, fake_pg, monkeypatch):
    a = tmp_path / "a"; a.mkdir(); monkeypatch.chdir(a); run(["init", "--yes"])
    bad = tmp_home / "stores" / "bad"; bad.mkdir()
    (bad / "store.toml").write_text("not toml ][")
    code, out = run(["list"])
    assert code == 0 and "a  coding" in out and "?? " in out and "bad" in out and "SETUP.md#registry" in out
    (tmp_home / "registry.toml").write_text("link = [ { path = ")
    code, out = run(["list"])
    assert code == 0 and "a  coding" in out and "registry.toml" in out and "SETUP.md#registry" in out
    code, out = run(["unlink", "--yes"])                 # any command that must read the registry: the message, exit 1
    assert code == 1 and "registry.toml" in out and "SETUP.md#registry" in out


def test_link_refuses_home_root_an_ancestor_and_a_missing_path(tmp_home, tmp_path, fake_pg, monkeypatch):
    """`link` writes the same registry entry `init` does, so it must refuse the same directories: a link
    at ~ or / would make every unlinked directory resolve to that store by longest prefix."""
    home = tmp_path / "homedir"; home.mkdir(); monkeypatch.setenv("HOME", str(home))
    a = tmp_path / "a"; a.mkdir(); monkeypatch.chdir(a); assert run(["init", "--yes"])[0] == 0
    monkeypatch.chdir(home)
    code, out = run(["link", "a", "--yes"])
    assert code == 1 and "choose a project directory" in out and "SETUP.md#registry" in out
    assert Registry.load().resolve(home) is None
    code, out = run(["link", "a", "--yes", "--path", "/"])
    assert code == 1 and "SETUP.md#registry" in out
    code, out = run(["link", "a", "--yes", "--path", str(tmp_path)])
    assert code == 1 and "ancestor" in out and "SETUP.md#registry" in out
    code, out = run(["link", "a", "--yes", "--path", str(tmp_path / "nope")])
    assert code == 1 and "not an existing directory" in out and "SETUP.md#registry" in out
    assert Registry.load().paths_of("a") == [a]


def test_store_name_arguments_are_validated_and_start_checks_the_store_exists(tmp_home, tmp_path, fake_pg):
    for cmd in (["start", "../x"], ["stop", "../x"], ["remove", "../x"], ["link", "../x", "--yes"]):
        code, out = run(cmd)
        assert code == 1 and "not a valid store name" in out and "SETUP.md#registry" in out, cmd
    code, out = run(["start", "nosuch"])
    assert code == 1 and "no store named nosuch" in out and "SETUP.md#registry" in out


def test_stop_and_remove_report_a_missing_ss(tmp_home, tmp_path, fake_pg, monkeypatch):
    a = tmp_path / "a"; a.mkdir(); monkeypatch.chdir(a); assert run(["init", "--yes"])[0] == 0
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    code, out = run(["stop", "a"])
    assert code == 1 and "ss not found" in out and "SETUP.md#servers" in out
    code, out = run(["remove", "a"], stdin="y\na\n")
    assert code == 1 and "ss not found" in out and "SETUP.md#servers" in out and Store.exists("a")


def test_list_reports_the_state_size_from_the_env_paths_and_the_database_size(tmp_home, tmp_path, fake_pg, monkeypatch):
    a = tmp_path / "a"; a.mkdir(); monkeypatch.chdir(a); assert run(["init", "--yes"])[0] == 0
    st = Store.load("a")
    elsewhere = tmp_path / "elsewhere"; elsewhere.mkdir()
    (elsewhere / "buf.jsonl").write_bytes(b"x" * 300 * 1024)
    st.env = {"AM_M3_BUFFER_PATH": str(elsewhere / "buf.jsonl")}; st.save()
    fake_pg.sizes = {"a_memory": 7 * 1024 * 1024}
    code, out = run(["list"])
    assert code == 0 and f"state {Store.load('a').state_size() // 1024} KB" in out and "state 300 KB" in out
    assert "db a_memory  db size 7168 KB" in out
    fake_pg.sizes = {}
    code, out = run(["list"])
    assert code == 0 and "db size unknown" in out and "SETUP.md#postgres" in out


def test_doctor_and_register_call_through(tmp_home, tmp_path, broken_pg_tools, monkeypatch):
    """`slopymem doctor` runs the check list and `slopymem register` the harness table directly — a broken
    import there must surface as the real ImportError, never as a placeholder message."""
    monkeypatch.setenv("HOME", str(tmp_path / "homedir")); (tmp_path / "homedir").mkdir()
    from slopymemory.checks import CHECKS
    code, out = run(["doctor"])
    assert code in (0, 1) and "doctor:" in out and all(c.id in out for c in CHECKS) and "no checks yet" not in out
    code, out = run(["register", "nosuch"])
    assert code == 1 and "unknown harness nosuch" in out and "no harness table" not in out


def test_init_takes_the_backend_and_the_plan_says_which(tmp_home, tmp_path, fake_pg, monkeypatch):
    repo = tmp_path / "proj"; repo.mkdir(); monkeypatch.chdir(repo)
    code, out = run(["init", "--postgres", "embedded", "--yes"])
    assert code == 0 and "on embedded Postgres" in out and Store.load("proj").postgres == "embedded"
    assert fake_pg.asked[-1] == "embedded" and fake_pg.created == ["proj_memory"]
    other = tmp_path / "other"; other.mkdir(); monkeypatch.chdir(other)
    code, out = run(["init", "--postgres", "system", "--yes"])
    assert code == 0 and "on system Postgres" in out and Store.load("other").postgres == "system"
    third = tmp_path / "third"; third.mkdir(); monkeypatch.chdir(third)
    code, out = run(["init", "--yes"])                        # no request: the system one can, so it is chosen
    assert code == 0 and Store.load("third").postgres == "system"
    with pytest.raises(SystemExit) as e:                  # argparse's own refusal of an unknown backend
        run(["init", "--postgres", "sqlite", "--yes"])
    assert e.value.code == 2


def test_init_without_a_usable_system_postgres_falls_back_to_embedded_and_says_why(tmp_home, tmp_path, fake_pg, monkeypatch):
    repo = tmp_path / "proj"; repo.mkdir(); monkeypatch.chdir(repo)
    monkeypatch.setattr(fake_pg, "can_provision", lambda: fake_pg.asked[-1] != "system")   # only the system one refuses
    code, out = run(["init"], stdin="n\n")
    assert code == 1 and "on embedded Postgres" in out and "system Postgres" in out and "SETUP.md#postgres" in out
    assert not Store.exists("proj")
    monkeypatch.setattr(cli.embedded_pg, "available", lambda: False)
    code, out = run(["init", "--yes"])
    assert code == 1 and "slopymem_template" in out and "embedded" in out and "SETUP.md#postgres" in out


def test_list_says_when_an_embedded_stores_size_is_unknown_because_its_postgres_is_down(tmp_home, tmp_path, fake_pg, monkeypatch):
    repo = tmp_path / "proj"; repo.mkdir(); monkeypatch.chdir(repo)
    assert run(["init", "--postgres", "embedded", "--yes"])[0] == 0
    fake_pg.running = False
    code, out = run(["list"])
    assert code == 0 and "db size unknown (embedded Postgres not running)" in out and "SETUP.md#postgres" not in out
    assert fake_pg.asked[-1] == "embedded"
    fake_pg.running = True; fake_pg.sizes = {"proj_memory": 2048}
    code, out = run(["list"])
    assert code == 0 and "db size 2 KB" in out


def test_remove_drops_the_database_on_the_stores_own_backend(tmp_home, tmp_path, fake_pg, monkeypatch):
    repo = tmp_path / "proj"; repo.mkdir(); monkeypatch.chdir(repo)
    assert run(["init", "--postgres", "embedded", "--yes"])[0] == 0
    fake_pg.asked.clear()
    code, out = run(["remove", "proj"], stdin="y\nproj\n")
    assert code == 0 and fake_pg.dropped == ["proj_memory"] and set(fake_pg.asked) == {"embedded"}


# --- scan-store: reports memories that look like secrets, never deletes ---

def test_scan_store_reports_hits_by_id_and_kind_and_never_deletes(tmp_home, tmp_path, fake_pg, monkeypatch):
    import datetime as dt
    repo = tmp_path / "proj"; repo.mkdir(); monkeypatch.chdir(repo)
    assert run(["init", "--yes"])[0] == 0
    asked = []
    rows = [("m1", dt.datetime(1999, 1, 2, 3, 4, tzinfo=dt.timezone.utc), "the decision was to keep the port"),
            ("m2", dt.datetime(1999, 1, 3, 4, 5, tzinfo=dt.timezone.utc), "deploy token ghp_" + "c" * 36)]
    monkeypatch.setattr(cli, "read_memories", lambda st: (asked.append(st.name), rows)[1])
    code, out = run(["scan-store", "proj"])
    assert code == 0 and asked == ["proj"]        # "never deletes" is proven on read_memories: one SELECT, a read-only session
    assert "m2  1999-01-03 04:05  github_token" in out and "m1" not in out     # only the hits are listed
    assert "1 of 2 memories look like they carry a secret" in out
    assert "review and remove by hand: slopymem shows, never deletes" in out and "SETUP.md#secrets" in out
    assert "ghp_" not in out                                              # the text itself is never printed


def test_scan_store_says_when_a_store_is_clean_and_refuses_bad_names(tmp_home, tmp_path, fake_pg, monkeypatch):
    repo = tmp_path / "proj"; repo.mkdir(); monkeypatch.chdir(repo)
    assert run(["init", "--yes"])[0] == 0
    monkeypatch.setattr(cli, "read_memories", lambda st: [("m1", None, "plain")])
    code, out = run(["scan-store", "proj"])
    assert code == 0 and "0 of 1 memories look like they carry a secret" in out
    code, out = run(["scan-store", "nope"])
    assert code == 1 and "no store named nope" in out and "SETUP.md#registry" in out
    code, out = run(["scan-store", "Bad Name"])
    assert code == 1 and "not a valid store name" in out


def test_scan_store_reports_an_unreachable_database_instead_of_a_traceback(tmp_home, tmp_path, fake_pg, monkeypatch):
    repo = tmp_path / "proj"; repo.mkdir(); monkeypatch.chdir(repo)
    assert run(["init", "--yes"])[0] == 0
    import psycopg
    def boom(st): raise psycopg.OperationalError("connection refused")
    monkeypatch.setattr(cli, "read_memories", boom)
    code, out = run(["scan-store", "proj"])
    assert code == 1 and "connection refused" in out and "SETUP.md#postgres" in out


def test_read_memories_uses_the_stores_own_dsn(tmp_home, tmp_path, fake_pg, monkeypatch):
    """The DSN is the one the server itself connects with (adopted stores override it in [env]), and the
    read is a plain SELECT on m3_memory — no write can be issued through this path."""
    repo = tmp_path / "proj"; repo.mkdir(); monkeypatch.chdir(repo)
    assert run(["init", "--yes"])[0] == 0
    st = Store.load("proj"); st.env = {"DATABASE_URL": "postgresql://someone@/elsewhere_memory"}; st.save()
    seen = {}
    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, sql, *a): seen["sql"] = sql
        def fetchall(self): return [("m1", None, "t")]
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def cursor(self): return Cur()
    conn = Conn()
    monkeypatch.setattr(cli.psycopg, "connect", lambda dsn, **kw: (seen.update(dsn=dsn, kw=kw), conn)[1])
    assert cli.read_memories(Store.load("proj")) == [("m1", None, "t")]
    assert seen["dsn"] == "postgresql://someone@/elsewhere_memory"
    assert seen["sql"].strip().upper().startswith("SELECT") and "m3_memory" in seen["sql"]
    assert conn.read_only is True                 # the session itself refuses a write, not only the string


def test_scan_store_names_an_embedded_postgres_that_is_down(tmp_home, tmp_path, fake_pg, monkeypatch):
    repo = tmp_path / "proj"; repo.mkdir(); monkeypatch.chdir(repo)
    assert run(["init", "--postgres", "embedded", "--yes"])[0] == 0
    import psycopg
    def boom(st): raise psycopg.OperationalError("connection refused")
    monkeypatch.setattr(cli, "read_memories", boom)
    fake_pg.running = False
    code, out = run(["scan-store", "proj"])
    assert code == 1 and "embedded Postgres is not running" in out and "slopymem start proj" in out and "SETUP.md#postgres" in out
