import io, sys
from pathlib import Path
import pytest
from slopymemory import cli, provision
from slopymemory.registry import Registry
from slopymemory.store import Store


class FakePg:
    def __init__(self): self.created = []
    def database_exists(self, n): return False
    def create_database(self, n): self.created.append(n)
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


def test_remove_asks_twice_and_refuses_yes(tmp_home, tmp_path, fake_pg, monkeypatch):
    a = tmp_path / "a"; a.mkdir(); monkeypatch.chdir(a); run(["init", "--yes"])
    code, out = run(["remove", "a", "--yes"])
    assert code == 2 and "refuses --yes" in out
    code, out = run(["remove", "a"], stdin="y\nn\n")
    assert code == 1 and Store.exists("a")
    code, out = run(["remove", "a"], stdin="y\na\n")     # the second question wants the name typed
    assert code == 0 and not Store.exists("a") and Registry.load().resolve(a) is None
