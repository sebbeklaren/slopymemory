from pathlib import Path
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
