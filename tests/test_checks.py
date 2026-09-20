from pathlib import Path
from unittest.mock import MagicMock, patch
from slopymemory import checks
from slopymemory.store import Store


IDS = ["python", "package", "postgres", "model", "registry", "servers", "harnesses", "space", "logs", "local-paths"]


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


def test_postgres_check_with_psql_missing(tmp_home):
    """postgres check when psql is not found -> failure with right message"""
    with patch("slopymemory.checks.SystemPostgres", side_effect=FileNotFoundError("psql")):
        f = checks.by_id("postgres").run()

    assert f.ok is False
    assert "psql failed" in f.detail


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
    f = checks.by_id("postgres").run()
    assert f.ok is False and "FATAL: boom" in f.detail and "SETUP.md#postgres" in f.detail


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
    from slopymemory import paths
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
