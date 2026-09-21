import sys
from pathlib import Path
from slopymemory import paths


def test_home_follows_env(tmp_home):
    assert paths.home() == tmp_home
    assert paths.registry_file() == tmp_home / "registry.toml"
    assert paths.stores_dir() == tmp_home / "stores"


def test_home_defaults_to_dot_slopymemory(monkeypatch):
    monkeypatch.delenv("SLOPYMEM_HOME", raising=False)
    monkeypatch.setattr(sys, "prefix", "/usr")                       # an interpreter that is not under a <home>/venv
    assert paths.home() == Path.home() / ".slopymemory"
    home, why = paths.home_and_why()
    assert home == Path.home() / ".slopymemory" and "default" in why and "SLOPYMEM_HOME" in why


def test_home_without_the_variable_is_the_parent_of_the_venv_this_interpreter_runs_from(monkeypatch, tmp_path):
    """`slopymem` and `slopymem-mcp` always run from `<home>/venv/bin`, and a registered launcher or a fresh shell
    has no SLOPYMEM_HOME: the home is the venv's parent when that parent is a slopymemory home (a registry or a
    stores dir, or it is `~/.slopymemory` itself) — never a split install under `~/.slopymemory` beside it."""
    monkeypatch.delenv("SLOPYMEM_HOME", raising=False)
    h = tmp_path / "elsewhere"; (h / "venv").mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(h / "venv"))
    assert paths.home() == Path.home() / ".slopymemory"               # nothing under the parent yet: not a home
    (h / "registry.toml").write_text("")
    assert paths.home() == h and paths.registry_file() == h / "registry.toml"
    home, why = paths.home_and_why()
    assert home == h and "venv" in why and str(h / "venv") in why
    (h / "registry.toml").unlink(); (h / "stores").mkdir()
    assert paths.home() == h                                          # a stores dir is enough on its own
    monkeypatch.setattr(sys, "prefix", str(Path.home() / ".slopymemory" / "venv"))
    assert paths.home() == Path.home() / ".slopymemory"               # the standard layout, whatever the parent holds
    monkeypatch.setattr(sys, "prefix", str(h / "not-a-venv"))
    assert paths.home() == Path.home() / ".slopymemory"               # an unrelated prefix: the default


def test_the_variable_wins_over_the_interpreters_layout(monkeypatch, tmp_path):
    h = tmp_path / "elsewhere"; (h / "venv").mkdir(parents=True); (h / "registry.toml").write_text("")
    monkeypatch.setattr(sys, "prefix", str(h / "venv"))
    monkeypatch.setenv("SLOPYMEM_HOME", str(tmp_path / "told"))
    assert paths.home() == tmp_path / "told" and paths.home_and_why()[1] == "SLOPYMEM_HOME"


def test_venv_python_override_expands_a_tilde(monkeypatch):
    monkeypatch.setenv("SLOPYMEM_PYTHON", "~/somewhere/bin/python")
    assert paths.venv_python() == Path.home() / "somewhere" / "bin" / "python"


def test_logs_dir_is_under_home(tmp_home):
    assert paths.logs_dir() == tmp_home / "logs"
