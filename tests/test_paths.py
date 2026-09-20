from pathlib import Path
from slopymemory import paths


def test_home_follows_env(tmp_home):
    assert paths.home() == tmp_home
    assert paths.registry_file() == tmp_home / "registry.toml"
    assert paths.stores_dir() == tmp_home / "stores"


def test_home_defaults_to_dot_slopymemory(monkeypatch):
    monkeypatch.delenv("SLOPYMEM_HOME", raising=False)
    assert paths.home() == Path.home() / ".slopymemory"


def test_venv_python_override_expands_a_tilde(monkeypatch):
    monkeypatch.setenv("SLOPYMEM_PYTHON", "~/somewhere/bin/python")
    assert paths.venv_python() == Path.home() / "somewhere" / "bin" / "python"


def test_logs_dir_is_under_home(tmp_home):
    assert paths.logs_dir() == tmp_home / "logs"
