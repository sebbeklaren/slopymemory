"""`slopymem status --json`: what a harness needs to know before it offers memory, read without starting a server or
creating anything — the installed version, the launcher, and whether a folder has a store."""
import json
from pathlib import Path

import pytest

from slopymemory import cli, updates
from slopymemory.registry import Registry
from slopymemory.store import Store


def _tree(root: Path) -> set:
    return {p.relative_to(root) for p in root.rglob("*")} if root.exists() else set()


def _status(capsys, *args) -> tuple[int, dict | None, str]:
    code = cli.main(["status", "--json", *args])
    out, err = capsys.readouterr()
    return code, (json.loads(out) if out.strip() else None), err


def _linked(tmp_path: Path, port: int) -> Path:
    repo = tmp_path / "repo"; (repo / "src").mkdir(parents=True)
    Store(name="repo", dialect="coding", port=port, database="x", postgres="system").save()
    r = Registry.load(); r.link(repo, "repo"); r.save()
    return repo


def test_a_linked_folder_reports_its_store(tmp_home, tmp_path, capsys, free_port):
    repo = _linked(tmp_path, free_port)
    code, out, _ = _status(capsys, "--project", str(repo))
    assert code == 0
    assert out["schema"] == 1 and out["version"] == updates.installed()
    assert out["launcher"].endswith("slopymem-mcp") and out["home"] == str(tmp_home)
    assert out["project"] == {"path": str(repo.resolve()), "store": "repo", "running": False}


def test_a_subfolder_reports_the_linked_projects_store(tmp_home, tmp_path, capsys, free_port):
    repo = _linked(tmp_path, free_port)
    _, out, _ = _status(capsys, "--project", str(repo / "src"))
    assert out["project"]["store"] == "repo"


def test_a_folder_without_a_store_reports_null(tmp_home, tmp_path, capsys, free_port):
    _linked(tmp_path, free_port)
    other = tmp_path / "other"; other.mkdir()
    _, out, _ = _status(capsys, "--project", str(other))
    assert out["project"] == {"path": str(other.resolve()), "store": None, "running": False}


def test_a_fresh_install_with_no_registry_reports_null_and_creates_nothing(tmp_home, tmp_path, capsys):
    proj = tmp_path / "proj"; proj.mkdir()
    before = _tree(tmp_path)
    code, out, _ = _status(capsys, "--project", str(proj))
    assert code == 0 and out["project"]["store"] is None
    assert _tree(tmp_path) == before


def test_without_project_the_current_folder_is_reported(tmp_home, tmp_path, capsys, free_port, monkeypatch):
    repo = _linked(tmp_path, free_port)
    monkeypatch.chdir(repo)
    _, out, _ = _status(capsys)
    assert out["project"]["store"] == "repo"


@pytest.mark.parametrize("kind", ["relative", "missing"])
def test_a_bad_project_is_refused_like_the_launcher_refuses_it(tmp_home, tmp_path, capsys, kind):
    value = {"relative": "repo", "missing": str(tmp_path / "nope")}[kind]
    code, out, err = _status(capsys, "--project", value)
    assert code == 2 and out is None and "--project" in err and value in err


def test_a_broken_registry_fails_with_its_message_not_a_traceback(tmp_home, tmp_path, capsys):
    tmp_home.mkdir(parents=True, exist_ok=True)
    (tmp_home / "registry.toml").write_text("link = [ { path = ")
    proj = tmp_path / "proj"; proj.mkdir()
    code, out, err = _status(capsys, "--project", str(proj))
    assert code == 1 and out is None and "registry.toml" in err
