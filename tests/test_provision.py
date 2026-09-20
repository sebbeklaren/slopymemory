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
    def can_provision(self): return True
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


def test_plan_init_refuses_when_cannot_provision(tmp_home, tmp_path):
    """Finding 1: can_provision()-false refusal must be tested."""
    repo = tmp_path / "proj"; repo.mkdir()
    class NoCan:
        def database_exists(self, n): return False
        def can_provision(self): return False
    with pytest.raises(provision.InitRefused, match="slopymem_template"):
        provision.plan_init(repo, None, "coding", NoCan())
    with pytest.raises(provision.InitRefused, match="SETUP.md#postgres"):
        provision.plan_init(repo, None, "coding", NoCan())


def test_plan_init_refuses_unknown_dialect_with_anchor(tmp_home, tmp_path):
    """Finding 3: unknown dialect refusal must include anchor."""
    repo = tmp_path / "proj"; repo.mkdir()
    pg = FakePostgres()
    with pytest.raises(provision.InitRefused, match="SETUP.md#registry"):
        provision.plan_init(repo, None, "unknown", pg)


def test_slug_refuses_empty_name_with_anchor():
    """Finding 3: _slug refusal must include anchor."""
    with pytest.raises(provision.InitRefused, match="SETUP.md#registry"):
        provision._slug("---")
    with pytest.raises(provision.InitRefused, match="letter or digit"):
        provision._slug("---")


def test_system_postgres_create_database_branches(monkeypatch):
    """Finding 2: test all three branches of create_database without real Postgres."""
    calls = []
    psql_calls = []

    class StubResult:
        def __init__(self, stdout): self.stdout = stdout
        returncode = 0

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return StubResult("")

    pg = provision.SystemPostgres()

    # Branch (a): template exists
    calls.clear()
    psql_calls.clear()
    monkeypatch.setattr(provision.subprocess, "run", fake_run)
    monkeypatch.setattr(pg, "template_exists", lambda: True)
    monkeypatch.setattr(pg, "is_superuser", lambda: False)
    pg.create_database("test_a")
    assert calls == [["createdb", "-T", "slopymem_template", "test_a"]]
    assert len(psql_calls) == 0

    # Branch (b): no template, superuser
    calls.clear()
    monkeypatch.setattr(provision.subprocess, "run", fake_run)
    monkeypatch.setattr(pg, "template_exists", lambda: False)
    monkeypatch.setattr(pg, "is_superuser", lambda: True)
    def fake_psql(sql, db="postgres"):
        psql_calls.append((sql, db))
        return ""
    monkeypatch.setattr(pg, "_psql", fake_psql)
    pg.create_database("test_b")
    assert calls == [["createdb", "test_b"]]
    assert ("create extension if not exists vector", "test_b") in psql_calls

    # Branch (c): neither template nor superuser
    calls.clear()
    monkeypatch.setattr(provision.subprocess, "run", fake_run)
    monkeypatch.setattr(pg, "template_exists", lambda: False)
    monkeypatch.setattr(pg, "is_superuser", lambda: False)
    with pytest.raises(provision.InitRefused, match="superuser or the template"):
        pg.create_database("test_c")
    assert len(calls) == 0  # No createdb called


def test_system_postgres_ident_guards_unsafe_names(monkeypatch):
    """Finding 4: _ident must guard against unsafe names before subprocess."""
    monkeypatch.setattr(provision.subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("should not call subprocess")))

    pg = provision.SystemPostgres()
    with pytest.raises(ValueError, match="unsafe database name"):
        pg.database_exists("bad'name")


@pytest.mark.pg
def test_system_postgres_creates_and_drops_a_scratch_database():
    import os, subprocess
    name = f"slopymem_test_{os.getpid()}"
    pg = provision.SystemPostgres()
    if not pg.can_provision():
        pytest.skip("no slopymem_template and not a superuser — " + provision.TEMPLATE_HINT)
    assert pg.template_exists() or pg.is_superuser()
    assert pg.has_pgvector()
    assert not pg.database_exists(name)
    pg.create_database(name)
    try:
        assert pg.database_exists(name)
    finally:
        subprocess.run(["dropdb", name], check=True)


def test_system_postgres_reports_the_tools_stderr_and_the_anchor(broken_pg_tools, monkeypatch):
    """A failing psql/createdb must surface ITS reason (the stderr), not 'exit status 1', and name the anchor."""
    pg = provision.SystemPostgres()
    with pytest.raises(provision.InitRefused) as e:
        pg.database_exists("x_memory")
    assert "FATAL: boom" in str(e.value) and "SETUP.md#postgres" in str(e.value) and "psql" in str(e.value)
    monkeypatch.setattr(pg, "template_exists", lambda: True)
    with pytest.raises(provision.InitRefused) as e:
        pg.create_database("x_memory")
    assert "FATAL: boom" in str(e.value) and "SETUP.md#postgres" in str(e.value) and "createdb" in str(e.value)
    with pytest.raises(provision.InitRefused) as e:
        pg.drop_database("x_memory")
    assert "FATAL: boom" in str(e.value) and "SETUP.md#postgres" in str(e.value)


def test_system_postgres_names_a_missing_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(provision.InitRefused) as e:
        provision.SystemPostgres().database_exists("x_memory")
    assert "psql not found" in str(e.value) and "SETUP.md#postgres" in str(e.value)


def _psql_answering(*, createdb: str, superuser: str = "f", template: str = "1"):
    def fake_psql(sql, db="postgres"):
        if "server_version" in sql: return "17.0"
        if "rolcreatedb" in sql: return createdb
        if "rolsuper" in sql: return superuser
        if "datistemplate" in sql: return template
        return "1"
    return fake_psql


def test_can_provision_needs_createdb_and_the_hint_says_so(monkeypatch):
    """A role with the template but neither SUPERUSER nor CREATEDB cannot run `createdb -T`; the predicate
    the doctor and the installer build on must say so before createdb fails."""
    pg = provision.SystemPostgres()
    monkeypatch.setattr(pg, "_psql", _psql_answering(createdb="f"))
    assert pg.can_provision() is False
    assert "CREATEDB" in provision.TEMPLATE_HINT
    assert "role can create databases: no" in pg.describe()
    monkeypatch.setattr(pg, "_psql", _psql_answering(createdb="t"))
    assert pg.can_provision() is True
    assert "role can create databases: yes" in pg.describe()


def test_database_size_is_a_number_or_none_never_a_crash(broken_pg_tools, monkeypatch):
    pg = provision.SystemPostgres()
    assert pg.database_size("x_memory") is None                  # psql fails: None, the caller says "unknown"
    monkeypatch.setattr(pg, "_psql", lambda sql, db="postgres": "123456")
    assert pg.database_size("x_memory") == 123456
    with pytest.raises(ValueError):
        pg.database_size("bad'name")
