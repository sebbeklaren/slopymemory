"""The repository's own gates: the scanner's author check and the tracked git hooks that run it. The hooks live in
the repository and call only its own scanner, so a clone enforces the same checks as CI without reaching outside."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCANNER = ROOT / "scripts" / "public_scan_ci.py"
HOOKS = ROOT / ".githooks"


def _author(email: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCANNER), "--author", email], capture_output=True, text=True)


def test_author_mode_refuses_a_private_mailbox():
    r = _author("someone@" + "gmail.com")          # assembled: the scanner refuses the shape as text, in tests too
    assert r.returncode == 1 and "private" in r.stdout


def test_author_mode_accepts_the_noreply_address():
    r = _author("12345+someone@users.noreply.github.com")
    assert r.returncode == 0, r.stdout


def test_author_mode_refuses_an_empty_email():
    r = _author("")
    assert r.returncode == 1


def test_the_hooks_are_tracked_executable_and_use_only_the_repositorys_scanner():
    for name in ("pre-commit", "pre-push"):
        hook = HOOKS / name
        assert hook.is_file() and os.access(hook, os.X_OK), name
        text = hook.read_text()
        assert "scripts/public_scan_ci.py" in text, name
        assert "/home/" not in text and "/Users/" not in text, name
