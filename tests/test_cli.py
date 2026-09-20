import io, sys
import pytest
from slopymemory import cli, provision
from slopymemory.registry import Registry
from slopymemory.store import Store


class FakePg:
    def __init__(self): self.created = []; self.dropped = []; self.sizes = {}
    def database_exists(self, n): return n in self.created
    def database_size(self, n): return self.sizes.get(n)
    def create_database(self, n): self.created.append(n)
    def drop_database(self, n): self.dropped.append(n)
    def has_pgvector(self): return True
    def describe(self): return "fake"
    def can_provision(self): return True


@pytest.fixture
def fake_pg(monkeypatch):
    pg = FakePg(); monkeypatch.setattr(cli, "postgres", lambda: pg); return pg


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
    monkeypatch.setattr(cli, "postgres", lambda: CreateFails())
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
