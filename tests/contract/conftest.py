"""Every contract test runs in a throwaway home — enforced here for the whole directory, so a new test cannot forget
it. Claude Code honours CLAUDE_CONFIG_DIR over HOME and Codex honours CODEX_HOME; the launcher a harness starts for
its health check finds its slopymemory home from SLOPYMEM_HOME. All of them are pointed inside the throwaway home."""
import os
import pwd
import sys
from pathlib import Path

import pytest


def _real_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()


def guard(throwaway: Path) -> None:
    """Refuse before any harness command unless every place a harness or the launcher could write resolves inside
    the throwaway home."""
    t = throwaway.resolve()
    home = Path.home().resolve()
    if home == _real_home() or home != t:
        raise RuntimeError(f"refusing: HOME is {home}, not the throwaway home {throwaway}")
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        raise RuntimeError("refusing: CLAUDE_CONFIG_DIR is set — Claude Code would write there, not in the throwaway home")
    for var in ("CODEX_HOME", "SLOPYMEM_HOME"):
        p = Path(os.environ.get(var, "/")).resolve()
        if not p.is_relative_to(t):
            raise RuntimeError(f"refusing: {var} is {p}, outside the throwaway home {throwaway}")


@pytest.fixture(autouse=True)
def throwaway(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("SLOPYMEM_HOME", str(home / ".slopymemory"))
    monkeypatch.setenv("SLOPYMEM_PYTHON", sys.executable)      # launcher_path() still names a real launcher file
    guard(home)
    return home
