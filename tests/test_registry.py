from pathlib import Path
import pytest
from slopymemory.registry import Registry, Link


def test_empty_when_no_file(tmp_home):
    r = Registry.load()
    assert r.links == []
    assert r.resolve(Path("/nowhere")) is None


def test_longest_prefix_wins_and_worktrees_resolve(tmp_home, tmp_path):
    repo = tmp_path / "repo"; (repo / ".claude" / "worktrees" / "w1").mkdir(parents=True)
    other = tmp_path / "repo" / "sub"; other.mkdir()
    r = Registry.load(); r.link(repo, "repo-store"); r.link(other, "sub-store"); r.save()
    r2 = Registry.load()
    assert r2.resolve(repo / ".claude" / "worktrees" / "w1") == "repo-store"
    assert r2.resolve(other) == "sub-store"
    assert r2.resolve(other / "deeper") == "sub-store"
    assert r2.resolve(tmp_path) is None


def test_link_refuses_a_linked_path_and_unlink_removes_it(tmp_home, tmp_path):
    r = Registry.load(); r.link(tmp_path, "a")
    with pytest.raises(ValueError, match="already linked to a"):
        r.link(tmp_path, "b")
    assert r.unlink(tmp_path) is True
    assert r.unlink(tmp_path) is False
    assert r.resolve(tmp_path) is None


def test_save_writes_toml_under_home(tmp_home, tmp_path):
    r = Registry.load(); r.link(tmp_path, "a"); r.save()
    text = (tmp_home / "registry.toml").read_text()
    assert "[[link]]" in text and 'store = "a"' in text
