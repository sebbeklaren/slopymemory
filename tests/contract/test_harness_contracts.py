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


def _real_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()


def guard(throwaway: Path) -> None:
    """Refuse before any harness command unless HOME is the throwaway directory: these tests register and remove
    MCP servers, and must never do it to the user's own harness configuration."""
    home = Path.home().resolve()
    if home == _real_home() or home != throwaway.resolve():
        raise RuntimeError(f"refusing: HOME is {home}, not the throwaway home {throwaway}")


@pytest.fixture
def throwaway(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    guard(home)
    return home


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


def test_the_guard_refuses_the_real_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(_real_home()))
    with pytest.raises(RuntimeError, match="refusing"):
        guard(tmp_path)
