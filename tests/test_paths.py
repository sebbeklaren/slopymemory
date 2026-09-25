import os
import stat
import sys
from pathlib import Path
import pytest
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


# --- write_atomic_with_mode: exact mode preserved regardless of the umask or a leftover temp file ------------------

def test_write_atomic_with_mode_keeps_the_exact_mode_under_a_widening_umask(tmp_path):
    old = os.umask(0o022)
    try:
        for mode in (0o600, 0o640, 0o664):
            target = tmp_path / f"f{oct(mode)}"
            target.write_text("before")
            target.chmod(mode)
            paths.write_atomic_with_mode(target, "after", mode)
            assert stat.S_IMODE(target.stat().st_mode) == mode
            assert target.read_text() == "after"
            assert not target.with_name(target.name + ".tmp").exists()
    finally:
        os.umask(old)


def test_write_atomic_with_mode_ignores_a_stale_tmp_files_own_mode(tmp_path):
    """A `.tmp` left over from a crashed or killed write must never donate ITS mode to the target: the
    helper must create the temp file fresh, not reuse whatever mode a leftover one already has."""
    target = tmp_path / "f"
    target.write_text("before")
    target.chmod(0o600)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text("leftover from an earlier crash")
    tmp.chmod(0o644)

    paths.write_atomic_with_mode(target, "after", 0o600)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert target.read_text() == "after"
    assert not tmp.exists()


def test_write_atomic_with_mode_with_none_gets_the_ordinary_new_file_default(tmp_path):
    old = os.umask(0o022)
    try:
        target = tmp_path / "new"
        paths.write_atomic_with_mode(target, "hello", None)
        assert target.read_text() == "hello"
        assert stat.S_IMODE(target.stat().st_mode) == 0o644     # 0o666 & ~umask, same as write_atomic
    finally:
        os.umask(old)


def test_write_atomic_with_mode_removes_the_temp_file_and_leaves_the_target_untouched_on_failure(tmp_path):
    target = tmp_path / "f"
    target.write_text("before")
    target.chmod(0o600)

    def boom(tmp, dest):
        raise OSError("disk full")

    with pytest.raises(OSError):
        paths.write_atomic_with_mode(target, "after", 0o600, replace=boom)
    assert target.read_text() == "before"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not target.with_name(target.name + ".tmp").exists()


def test_write_atomic_with_mode_default_replace_is_looked_up_dynamically(tmp_path, monkeypatch):
    """No explicit `replace=` is passed: the helper must call whatever `os.replace` resolves to AT CALL
    TIME, not a copy captured when the module was imported — so a caller can inject a failure by
    patching `os.replace` itself, the way harnesses.py's callers rely on."""
    target = tmp_path / "f"
    target.write_text("before")
    target.chmod(0o600)

    def boom(tmp, dest):
        raise OSError("disk full")

    monkeypatch.setattr(paths.os, "replace", boom)
    with pytest.raises(OSError):
        paths.write_atomic_with_mode(target, "after", 0o600)
    assert target.read_text() == "before"
