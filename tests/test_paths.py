from pathlib import Path
from slopymemory import paths


def test_home_follows_env(tmp_home):
    assert paths.home() == tmp_home
    assert paths.registry_file() == tmp_home / "registry.toml"
    assert paths.stores_dir() == tmp_home / "stores"


def test_home_defaults_to_dot_slopymemory(monkeypatch):
    monkeypatch.delenv("SLOPYMEM_HOME", raising=False)
    assert paths.home() == Path.home() / ".slopymemory"
