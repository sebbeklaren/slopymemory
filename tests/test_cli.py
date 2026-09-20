import io, sys
from pathlib import Path
import pytest
from slopymemory import cli, provision
from slopymemory.registry import Registry
from slopymemory.store import Store


class FakePg:
    def __init__(self): self.created = []; self.dropped = []
    def database_exists(self, n): return n in self.created
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
