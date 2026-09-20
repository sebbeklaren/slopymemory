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
