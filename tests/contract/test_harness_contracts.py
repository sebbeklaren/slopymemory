"""Contract tests: slopymemory's registration against each installed harness's REAL command line, in a throwaway
home. They catch a harness changing the commands or config layout slopymemory relies on (docs/COMPATIBILITY.md)
before a user does. Opt-in — SLOPYMEM_CONTRACT=1 — because they run real harness CLIs; scripts/compat_check.py runs
them. A harness not installed here is skipped, and says so."""
import os
import pwd
import shutil
import subprocess
from pathlib import Path

import pytest

from slopymemory import harnesses
from slopymemory.harnesses import claude_code, codex

opt_in = pytest.mark.skipif(os.environ.get("SLOPYMEM_CONTRACT") != "1",
                            reason="contract tests run real harness CLIs: set SLOPYMEM_CONTRACT=1 (compat_check.py does)")

CASES = [(claude_code.HARNESS, "claude", ["claude", "mcp", "get", "slopymemory"]),
         (codex.HARNESS, "codex", ["codex", "mcp", "get", "slopymemory"])]


from conftest import guard, _real_home  # noqa: E402  (the directory's own fixture module)


@opt_in
@pytest.mark.parametrize("h,cli,show", CASES, ids=["claude-code", "codex"])
def test_register_is_seen_by_the_harness_and_unregister_removes_it(throwaway, h, cli, show):
    if shutil.which(cli) is None:
        pytest.skip(f"{cli} is not installed here")
    launcher = harnesses.launcher_path()
    assert harnesses.register(h.id, True, print_summary=False) == 0
    seen = subprocess.run(show, capture_output=True, text=True, timeout=120)
    assert seen.returncode == 0 and launcher in seen.stdout, seen.stdout + seen.stderr
    f = h.instructions_file()
    assert harnesses.TRIGGER_PREFIX in f.read_text()
    assert harnesses.unregister(h, launcher) == 0
    harnesses.remove_offer_line(f)
    gone = subprocess.run(show, capture_output=True, text=True, timeout=120)
    assert launcher not in gone.stdout
    assert harnesses.TRIGGER_PREFIX not in (f.read_text() if f.exists() else "")


@opt_in
@pytest.mark.parametrize("h,cli,show", CASES, ids=["claude-code", "codex"])
def test_a_config_kept_elsewhere_is_found_where_the_harness_keeps_it(throwaway, monkeypatch, h, cli, show):
    """CLAUDE_CONFIG_DIR and CODEX_HOME moved away from their defaults: slopymemory must read the same file the
    harness writes, or it cannot tell it is registered and cannot remove itself."""
    if shutil.which(cli) is None:
        pytest.skip(f"{cli} is not installed here")
    (throwaway / "cc").mkdir(); (throwaway / "cx").mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(throwaway / "cc"))
    monkeypatch.setenv("CODEX_HOME", str(throwaway / "cx"))
    guard(throwaway)
    launcher = harnesses.launcher_path()
    assert harnesses.register(h.id, True, print_summary=False) == 0
    assert launcher in subprocess.run(show, capture_output=True, text=True, timeout=120).stdout
    assert h.registered(launcher), f"slopymemory does not see its own entry in {h.name}'s moved config"
    assert harnesses.unregister(h, launcher) == 0
    assert launcher not in subprocess.run(show, capture_output=True, text=True, timeout=120).stdout


def test_the_guard_refuses_the_real_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(_real_home()))
    with pytest.raises(RuntimeError, match="refusing"):
        guard(tmp_path)


def test_the_guard_refuses_a_claude_config_dir(throwaway, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(_real_home() / ".claude"))
    with pytest.raises(RuntimeError, match="CLAUDE_CONFIG_DIR"):
        guard(throwaway)


def test_the_guard_refuses_a_codex_or_slopymemory_home_outside(throwaway, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(_real_home() / ".codex"))
    with pytest.raises(RuntimeError, match="CODEX_HOME"):
        guard(throwaway)


def test_the_launcher_a_health_check_starts_uses_the_throwaway_slopymemory_home(throwaway):
    from slopymemory import paths
    assert paths.home().resolve().is_relative_to(throwaway.resolve())
