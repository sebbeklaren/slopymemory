from pathlib import Path
from unittest.mock import MagicMock, patch
from slopymemory import checks
from slopymemory.store import Store


IDS = ["python", "package", "postgres", "model", "registry", "servers", "harnesses", "space", "logs", "secrets", "local-paths"]


def test_every_check_has_an_id_symptom_verify_and_fix():
    assert [c.id for c in checks.CHECKS] == IDS
    for c in checks.CHECKS:
        assert c.symptom and c.verify and c.command and c.fix


def test_registry_check_finds_a_store_without_a_state_dir_or_a_shared_port(tmp_home):
    Store(name="a", dialect="coding", port=8780, database="a_db", postgres="system").save()
    Store(name="b", dialect="coding", port=8780, database="b_db", postgres="system").save()
    f = checks.by_id("registry").run()
    assert f.ok is False and "port 8780" in f.detail


def test_setup_md_is_generated_from_the_same_list():
    text = checks.render_setup("# head\n")
    assert text.startswith("# head")
    for i in IDS:
        assert f"## {i}" in text


def test_postgres_check_with_fake_pg_ok_but_no_provision(tmp_home):
    """postgres check with pgvector present, template missing, not superuser -> pass with note"""
    fake_pg = MagicMock()
    fake_pg.has_pgvector.return_value = True
    fake_pg.database_exists.return_value = True
    fake_pg.template_exists.return_value = False
    fake_pg.is_superuser.return_value = False
    fake_pg.can_provision.return_value = False

    with patch("slopymemory.checks.SystemPostgres", return_value=fake_pg):
        f = checks.by_id("postgres").run()

    assert f.ok is True
    assert "slopymem_template" in f.detail


def test_postgres_check_with_psql_missing_and_no_embedded_wheel_fails(tmp_home, monkeypatch):
    """No store yet, psql not found, and no embedded Postgres to fall back on: nothing can provision -> FAIL."""
    monkeypatch.setattr(checks.embedded_pg, "unavailable_reason", lambda: "the embedded-postgres wheel is not installed")
    with patch("slopymemory.checks.SystemPostgres", side_effect=FileNotFoundError("psql")):
        f = checks.by_id("postgres").run()
    assert f.ok is False
    assert "psql failed" in f.detail and "embedded" in f.detail and "not installed" in f.detail


def test_postgres_check_without_system_postgres_passes_when_the_embedded_one_can_serve_new_stores(tmp_home):
    """The machine this backend exists for: no psql at all, no store yet -> ok, and the detail says new stores
    go to the embedded Postgres."""
    with patch("slopymemory.checks.SystemPostgres", side_effect=FileNotFoundError("psql")):
        f = checks.by_id("postgres").run()
    assert f.ok is True
    assert "embedded" in f.detail and "new stores" in f.detail and "psql" in f.detail


def test_space_check_measures_data_not_venv(tmp_home, monkeypatch):
    """space check measures data only, reports venv separately"""
    from slopymemory import paths

    # Create data and venv files
    (paths.stores_dir()).mkdir(parents=True, exist_ok=True)
    (paths.stores_dir() / "store.txt").write_bytes(b"x" * 10)
    (paths.home() / "venv" / "big.bin").parent.mkdir(parents=True, exist_ok=True)
    (paths.home() / "venv" / "big.bin").write_bytes(b"x" * (2 * 1024 * 1024))  # 2 MB venv

    # Set a 1 MB warning threshold (data is only 10 bytes, so it won't warn)
    monkeypatch.setattr(checks, "WARN_TOTAL_GB", 1_000_000 / 2**30)

    f = checks.by_id("space").run()

    # Space check should pass (data is tiny), and detail should show install (venv) separately
    assert f.ok is True
    assert "install (venv)" in f.detail


def test_postgres_check_reports_psqls_own_reason(tmp_home, broken_pg_tools):
    Store(name="a", dialect="coding", port=8780, database="a_db", postgres="system").save()   # a store depends on it
    f = checks.by_id("postgres").run()
    assert f.ok is False and "FATAL: boom" in f.detail and "SETUP.md#postgres" in f.detail and "a" in f.detail


def test_postgres_check_reports_whether_the_role_can_create_databases(tmp_home):
    fake_pg = MagicMock()
    fake_pg.has_pgvector.return_value = True
    fake_pg.template_exists.return_value = True
    fake_pg.is_superuser.return_value = False
    fake_pg.can_create_databases.return_value = False
    fake_pg.can_provision.return_value = False
    with patch("slopymemory.checks.SystemPostgres", return_value=fake_pg):
        f = checks.by_id("postgres").run()
    assert f.ok is True and "role can create databases: no" in f.detail and "CREATEDB" in f.detail


def test_registry_check_fails_on_a_malformed_store_or_registry_file(tmp_home):
    Store(name="a", dialect="coding", port=8780, database="a_db", postgres="system").save()
    bad = tmp_home / "stores" / "bad"; bad.mkdir()
    (bad / "store.toml").write_text("not toml ][")
    f = checks.by_id("registry").run()
    assert f.ok is False and "bad" in f.detail and "SETUP.md#registry" in f.detail
    (tmp_home / "registry.toml").write_text("link = [ { path = ")
    f = checks.by_id("registry").run()
    assert f.ok is False and "registry.toml" in f.detail


def test_servers_check_fails_when_ss_is_missing(tmp_home, tmp_path, monkeypatch):
    Store(name="a", dialect="coding", port=8780, database="a_db", postgres="system").save()
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    f = checks.by_id("servers").run()
    assert f.ok is False and "ss not found" in f.detail and "iproute2" in f.detail


def test_logs_check_fails_on_an_unreadable_log_and_reports_error_lines_without_failing(tmp_home):
    """An unreadable log used to read as 'no error lines' — the check's purpose inverted. Error lines
    found are informational (ok stays True); a log that cannot be read is a failed check."""
    from slopymemory import paths
    a = Store(name="a", dialect="coding", port=8780, database="a_db", postgres="system"); a.save()
    paths.logs_dir().mkdir(parents=True)
    a.log_file().write_text("INFO fine\nERROR something broke\nINFO after\n")
    f = checks.by_id("logs").run()
    assert f.ok is True and "ERROR something broke" in f.detail
    b = Store(name="b", dialect="coding", port=8781, database="b_db", postgres="system"); b.save()
    b.log_file().mkdir()                                  # a directory where the log should be: unreadable
    f = checks.by_id("logs").run()
    assert f.ok is False and "b: log unreadable" in f.detail and "ERROR something broke" in f.detail
    assert "unreadable log fails" in checks.by_id("logs").verify


def test_logs_check_reads_only_the_last_64_kb(tmp_home):
    from slopymemory import paths
    a = Store(name="a", dialect="coding", port=8780, database="a_db", postgres="system"); a.save()
    paths.logs_dir().mkdir(parents=True)
    filler = ("INFO " + "x" * 95 + "\n") * 1000           # ~100 KB between the two error lines
    a.log_file().write_text("ERROR early one\n" + filler + "ERROR late one\nINFO end\n")
    f = checks.by_id("logs").run()
    assert f.ok is True and "ERROR late one" in f.detail and "1 error line" in f.detail and "early" not in f.detail


def test_space_check_follows_the_paths_each_stores_env_names(tmp_home, tmp_path, monkeypatch):
    st = Store(name="a", dialect="coding", port=8780, database="a_db", postgres="system")
    elsewhere = tmp_path / "elsewhere"; elsewhere.mkdir()
    (elsewhere / "buf.jsonl").write_bytes(b"x" * (3 * 2**20))
    st.env = {"AM_M3_BUFFER_PATH": str(elsewhere / "buf.jsonl")}; st.save()
    monkeypatch.setattr(checks, "WARN_TOTAL_GB", 2 * 2**20 / 2**30)          # warn above 2 MB
    f = checks.by_id("space").run()
    assert f.ok is False and "store data is 3 MB" in f.detail
    monkeypatch.setattr(checks, "WARN_TOTAL_GB", 1.0)
    f = checks.by_id("space").run()
    assert f.ok is True and "3 MB of store data" in f.detail


def test_package_check_verifies_the_vendored_substrate_not_a_separate_distribution(monkeypatch):
    """The substrate ships inside the slopymemory distribution; there is no `agent-memory` package to look up."""
    def version(name):
        if name == "slopymemory": return "0.2.0"
        raise checks.md.PackageNotFoundError(name)
    monkeypatch.setattr(checks.md, "version", version)
    f = checks._package()
    assert f.ok and "slopymemory 0.2.0" in f.detail and "agent_memory" in f.detail


def test_package_check_fails_when_slopymemory_is_not_installed(monkeypatch):
    def version(name): raise checks.md.PackageNotFoundError(name)
    monkeypatch.setattr(checks.md, "version", version)
    f = checks._package()
    assert not f.ok and "not installed" in f.detail


class FakeEpg:
    """The embedded backend as the check sees it."""
    running = True
    pgvector = True
    databases: set = {"e_db"}
    size = 46 * 2**20

    def __init__(self, pgdata): self.pgdata = pgdata
    def is_running(self): return self.running
    def has_pgvector(self): return self.pgvector
    def database_exists(self, n): return n in self.databases
    def data_size(self): return self.size


def _system_ok():
    fake = MagicMock()
    fake.has_pgvector.return_value = True
    fake.database_exists.return_value = True
    fake.template_exists.return_value = True
    fake.is_superuser.return_value = False
    fake.can_create_databases.return_value = True
    fake.can_provision.return_value = True
    return fake


def test_postgres_check_reports_both_backends_when_both_are_in_use(tmp_home, monkeypatch):
    Store(name="s", dialect="coding", port=8780, database="s_db", postgres="system").save()
    Store(name="e", dialect="coding", port=8781, database="e_db", postgres="embedded").save()
    (tmp_home / "pg").mkdir(parents=True)
    monkeypatch.setattr(checks, "EmbeddedPostgres", FakeEpg)
    with patch("slopymemory.checks.SystemPostgres", return_value=_system_ok()):
        f = checks.by_id("postgres").run()
    assert f.ok is True
    assert "embedded: running" in f.detail and "pgvector" in f.detail and "46 MB" in f.detail and "1 store" in f.detail
    assert "system: reachable" in f.detail


def test_postgres_check_fails_naming_an_embedded_store_whose_database_is_missing(tmp_home, monkeypatch):
    Store(name="e", dialect="coding", port=8781, database="e_db", postgres="embedded").save()
    Store(name="f", dialect="coding", port=8782, database="f_db", postgres="embedded").save()
    (tmp_home / "pg").mkdir(parents=True)
    monkeypatch.setattr(checks, "EmbeddedPostgres", FakeEpg)
    f = checks.by_id("postgres").run()
    assert f.ok is False and "f: database f_db missing" in f.detail and "SETUP.md#postgres" in f.detail
    assert "system" not in f.detail                  # no store uses it: not looked at, not reported


def test_postgres_check_reports_an_embedded_postgres_that_is_down_without_starting_it(tmp_home, monkeypatch):
    Store(name="e", dialect="coding", port=8781, database="e_db", postgres="embedded").save()
    (tmp_home / "pg").mkdir(parents=True)

    class Down(FakeEpg):
        running = False
        def database_exists(self, n): raise AssertionError("the doctor must not start the embedded Postgres")
    monkeypatch.setattr(checks, "EmbeddedPostgres", Down)
    f = checks.by_id("postgres").run()
    assert f.ok is True and "embedded: not running" in f.detail and "not verified" in f.detail


def test_postgres_check_fails_when_an_embedded_stores_data_dir_or_wheel_is_gone(tmp_home, monkeypatch):
    Store(name="e", dialect="coding", port=8781, database="e_db", postgres="embedded").save()
    monkeypatch.setattr(checks, "EmbeddedPostgres", FakeEpg)
    f = checks.by_id("postgres").run()                                        # no data dir at all
    assert f.ok is False and "e" in f.detail and str(tmp_home / "pg") in f.detail and "SETUP.md#postgres" in f.detail
    (tmp_home / "pg").mkdir(parents=True)
    monkeypatch.setattr(checks.embedded_pg, "unavailable_reason", lambda: "the embedded-postgres wheel is not installed")
    f = checks.by_id("postgres").run()
    assert f.ok is False and "not installed" in f.detail and "e" in f.detail


def test_postgres_check_reports_an_embedded_data_dir_nobody_uses_yet(tmp_home, monkeypatch):
    """The dir exists (a first init chose it) but no store is on it yet: reported, not failed."""
    (tmp_home / "pg").mkdir(parents=True)
    monkeypatch.setattr(checks, "EmbeddedPostgres", FakeEpg)
    f = checks.by_id("postgres").run()
    assert f.ok is True and "embedded: running" in f.detail and "0 store" in f.detail


def _log_record(t, **kw):
    import json
    return json.dumps({"tool": "memory_save", "t": t, **kw}) + "\n"


def test_secrets_check_counts_refused_saves_per_store_from_the_invocation_log(tmp_home):
    """Refusals are informational (ok stays True): the guard doing its job is not a broken machine. The count,
    the kinds and the first/last refusal times are what the user needs; the texts were never logged."""
    a = Store(name="a", dialect="coding", port=8780, database="a_db", postgres="system"); a.save()
    log = Path(a.server_env()["AM_MCP_INVOCATION_LOG"]); log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(_log_record(915278400.0, text="fine", status="saved")
                   + _log_record(915364800.0, refused="secret", kind="github_token", status="refused")
                   + "not json at all\n"
                   + _log_record(915451200.0, refused="secret", kind="high_entropy_token", status="refused"))
    b = Store(name="b", dialect="coding", port=8781, database="b_db", postgres="system"); b.save()   # no log yet
    f = checks.by_id("secrets").run()
    assert f.ok is True
    assert "a: 2 save(s) refused as secrets" in f.detail and "github_token" in f.detail and "high_entropy_token" in f.detail
    assert "since 1999-01-02" in f.detail and "first 1999-01-03" in f.detail and "last 1999-01-04" in f.detail
    assert "b:" not in f.detail


def test_secrets_check_is_quiet_when_nothing_was_refused_and_fails_on_an_unreadable_log(tmp_home):
    a = Store(name="a", dialect="coding", port=8780, database="a_db", postgres="system"); a.save()
    log = Path(a.server_env()["AM_MCP_INVOCATION_LOG"]); log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(_log_record(915278400.0, text="fine", status="saved"))
    f = checks.by_id("secrets").run()
    assert f.ok is True and "no save refused as a secret" in f.detail
    log.unlink(); log.mkdir()                                   # a directory where the log should be: unreadable
    f = checks.by_id("secrets").run()
    assert f.ok is False and "a: invocation log unreadable" in f.detail
    assert "scan-store" in checks.by_id("secrets").verify and "scan-store" in checks.by_id("secrets").command


# --- model: BOTH pinned snapshot DIRECTORIES must be in the cache, with their files — nothing else is consulted ---

WEIGHTS_REPO, CODE_REPO = "nomic-ai/nomic-embed-text-v1.5", "nomic-ai/nomic-bert-2048"
AUTO_MAP = {"AutoConfig": f"{CODE_REPO}--configuration_hf_nomic_bert.NomicBertConfig",
            "AutoModel": f"{CODE_REPO}--modeling_hf_nomic_bert.NomicBertModel"}


def _hub(tmp_path, monkeypatch, weights_rev=None, code_rev=None, weight_files=None, code_files=("configuration_hf_nomic_bert.py", "modeling_hf_nomic_bert.py")):
    """A scratch hub cache holding exactly what is asked for: snapshot dirs by commit, no refs/ anywhere."""
    import json, shutil
    from slopymemory import install_steps
    hub = tmp_path / "hub"
    shutil.rmtree(hub, ignore_errors=True)                  # each scenario starts from an empty cache
    monkeypatch.setattr(install_steps, "hub_cache", lambda: hub)
    if weights_rev:
        d = hub / f"models--{WEIGHTS_REPO.replace('/', '--')}" / "snapshots" / weights_rev
        for name in (install_steps.WEIGHT_FILES if weight_files is None else weight_files):
            (d / name).parent.mkdir(parents=True, exist_ok=True); (d / name).write_text("x")
        (d / "config.json").parent.mkdir(parents=True, exist_ok=True)
        (d / "config.json").write_text(json.dumps({"auto_map": AUTO_MAP}))
    if code_rev:
        d = hub / f"models--{CODE_REPO.replace('/', '--')}" / "snapshots" / code_rev
        d.mkdir(parents=True, exist_ok=True)
        for name in code_files:
            (d / name).write_text("# code")
    return hub


def test_model_check_is_ok_only_with_both_pinned_snapshot_directories(tmp_path, monkeypatch):
    from agent_memory.config import settings
    pin, code = settings.embed_revision, settings.embed_code_revision
    other = "0123456789abcdef0123456789abcdef01234567"
    _hub(tmp_path, monkeypatch)
    f = checks.by_id("model").run()
    assert not f.ok and "slopymem install-model" in f.detail
    _hub(tmp_path, monkeypatch, weights_rev=pin, code_rev=code)
    f = checks.by_id("model").run()
    assert f.ok and pin[:12] in f.detail and code[:12] in f.detail and "nomic-bert-2048" in f.detail
    assert not list((tmp_path / "hub").glob("models--*/refs")), "the check must not need a refs/main"
    _hub(tmp_path, monkeypatch, weights_rev=other, code_rev=code)
    f = checks.by_id("model").run()
    assert not f.ok and "migration" in f.detail and pin[:12] in f.detail and other[:12] in f.detail
    _hub(tmp_path, monkeypatch, weights_rev=pin, code_rev=code)
    (tmp_path / "hub" / f"models--{WEIGHTS_REPO.replace('/', '--')}" / "snapshots" / other).mkdir()
    assert checks.by_id("model").run().ok               # the pin is there; another snapshot beside it is no failure


def test_model_check_fails_on_an_incomplete_snapshot_or_missing_code(tmp_path, monkeypatch):
    """A snapshot directory without its files (an interrupted download) is not the model; the code repository at
    its pin, with the module files the config names, is as necessary as the weights."""
    from agent_memory.config import settings
    pin, code = settings.embed_revision, settings.embed_code_revision
    _hub(tmp_path, monkeypatch, weights_rev=pin, code_rev=code, weight_files=("config.json", "tokenizer.json"))
    f = checks.by_id("model").run()
    assert not f.ok and "model.safetensors" in f.detail and "slopymem install-model" in f.detail
    _hub(tmp_path, monkeypatch, weights_rev=pin)                                       # no code snapshot at all
    f = checks.by_id("model").run()
    assert not f.ok and "nomic-bert-2048" in f.detail and code[:12] in f.detail and "slopymem install-model" in f.detail
    _hub(tmp_path, monkeypatch, weights_rev=pin, code_rev=code, code_files=("configuration_hf_nomic_bert.py",))
    f = checks.by_id("model").run()
    assert not f.ok and "modeling_hf_nomic_bert.py" in f.detail
