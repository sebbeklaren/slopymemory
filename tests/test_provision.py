from pathlib import Path
import pytest
from slopymemory import provision
from slopymemory.registry import Registry
from slopymemory.store import Store


class FakePostgres:
    def __init__(self, existing=()): self.existing = set(existing); self.created = []
    def database_exists(self, n): return n in self.existing
    def create_database(self, n): self.existing.add(n); self.created.append(n)
    def has_pgvector(self): return True
    def describe(self): return "fake"


def test_plan_then_apply_creates_everything_once(tmp_home, tmp_path):
    repo = tmp_path / "proj"; repo.mkdir()
    pg = FakePostgres()
    plan = provision.plan_init(repo, None, "coding", pg)
    assert plan.store.name == "proj" and plan.store.database == "proj_memory" and plan.creates_database
    assert plan.store.port >= 8780
    st = provision.apply_init(plan, pg)
    assert pg.created == ["proj_memory"]
    assert Store.exists("proj") and Registry.load().resolve(repo) == "proj"
    assert (st.path() / "store.toml").exists()


def test_init_refuses_a_directory_that_already_resolves(tmp_home, tmp_path):
    repo = tmp_path / "proj"; repo.mkdir(); pg = FakePostgres()
    provision.apply_init(provision.plan_init(repo, None, "coding", pg), pg)
    with pytest.raises(provision.InitRefused, match="already resolves to proj"):
        provision.plan_init(repo / "sub", "other", "coding", pg)


def test_init_refuses_home_root_and_an_ancestor_of_a_linked_path(tmp_home, tmp_path, monkeypatch):
    # A launcher spawned outside a project (ChatGPT Desktop's Work mode, a shell in ~) must never provision
    # there: a store linked to ~ would become every project's store by longest-prefix matching.
    monkeypatch.setenv("HOME", str(tmp_path / "homedir")); (tmp_path / "homedir").mkdir()
    pg = FakePostgres()
    with pytest.raises(provision.InitRefused, match="choose a project directory"):
        provision.plan_init(Path.home(), None, "coding", pg)
    with pytest.raises(provision.InitRefused, match="choose a project directory"):
        provision.plan_init(Path("/"), "root", "coding", pg)
    deep = tmp_path / "work" / "proj"; deep.mkdir(parents=True)
    provision.apply_init(provision.plan_init(deep, None, "coding", pg), pg)
    with pytest.raises(provision.InitRefused, match="ancestor of .*proj"):
        provision.plan_init(tmp_path / "work", "work", "coding", pg)


def test_init_refuses_a_name_in_use_and_a_foreign_database(tmp_home, tmp_path):
    a = tmp_path / "a"; a.mkdir(); b = tmp_path / "b"; b.mkdir()
    pg = FakePostgres(existing={"taken_memory"})
    provision.apply_init(provision.plan_init(a, "x", "coding", pg), pg)
    with pytest.raises(provision.InitRefused, match="name x is in use"):
        provision.plan_init(b, "x", "coding", pg)
    with pytest.raises(provision.InitRefused, match="database taken_memory exists"):
        provision.plan_init(b, "taken", "coding", pg)


@pytest.mark.pg
def test_system_postgres_creates_and_drops_a_scratch_database():
    import os, subprocess
    name = f"slopymem_test_{os.getpid()}"
    pg = provision.SystemPostgres()
    assert pg.has_pgvector()
    assert not pg.database_exists(name)
    pg.create_database(name)
    try:
        assert pg.database_exists(name)
    finally:
        subprocess.run(["dropdb", name], check=True)
