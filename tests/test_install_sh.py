"""`install.sh` driven end to end with a fake `uv`, a fake Python and a fake `slopymem` on PATH: the six steps, the
flag parsing, and the verdict after the doctor (the closing line and the exit status). Nothing real is installed;
the fake uv "syncs" by dropping the fake tools into the venv path the script chose."""
import os, stat, subprocess
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "install.sh"

FAKE_UV = '''#!/bin/sh
echo "fake uv $*" >> "$FAKE_LOG"
case "$1 $2" in
  "--version ") echo "uv 0.0.0-fake";;
  "python find") echo "$FAKE_PY";;
  "sync "*) mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"
            cp "$FAKE_PY" "$UV_PROJECT_ENVIRONMENT/bin/python"; cp "$FAKE_SLOPYMEM" "$UV_PROJECT_ENVIRONMENT/bin/slopymem"
            echo "fake sync: $*";;
  *) echo "fake uv: unexpected $*" >&2; exit 1;;
esac
'''
FAKE_PY = '''#!/bin/sh
# the preflight and the import check are `python -c …`: both succeed quietly here, logged with their arguments
echo "fake python $*" >> "$FAKE_LOG"
exit 0
'''
FAKE_SLOPYMEM = '''#!/bin/sh
echo "fake slopymem $*" >> "$FAKE_LOG"
case "$1" in
  doctor) cat "$FAKE_DOCTOR"; grep -q '^FAIL' "$FAKE_DOCTOR" && exit 1 || exit 0;;
  *) exit 0;;
esac
'''
OK_DOCTOR = "ok   python  fine\nok   harnesses  Claude Code: registered\ndoctor: all checks passed\n"
HARNESS_FAIL = "ok   python  fine\nFAIL harnesses    Claude Code: NOT registered  — see SETUP.md#harnesses\ndoctor: 1 check(s) failed\n"
TWO_FAILS = "FAIL postgres     psql not found — see SETUP.md#postgres\nFAIL harnesses    NOT registered — see SETUP.md#harnesses\ndoctor: 2 check(s) failed\n"


@pytest.fixture
def fakes(tmp_path):
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    for name, text in (("uv", FAKE_UV), ("fakepy", FAKE_PY), ("fakeslopymem", FAKE_SLOPYMEM)):
        f = bin_dir / name; f.write_text(text); f.chmod(f.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.log"; log.write_text("")
    doctor = tmp_path / "doctor.txt"
    def run(*args, doctor_text=OK_DOCTOR):
        doctor.write_text(doctor_text)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "SLOPYMEM_HOME": str(tmp_path / "home"),
               "FAKE_PY": str(bin_dir / "fakepy"), "FAKE_SLOPYMEM": str(bin_dir / "fakeslopymem"),
               "FAKE_DOCTOR": str(doctor), "FAKE_LOG": str(log)}
        env.pop("VIRTUAL_ENV", None)
        p = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, cwd=ROOT, timeout=60)
        return p.returncode, p.stdout + p.stderr, log.read_text()
    return run


def test_the_script_parses():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_six_steps_in_order_and_done_when_the_doctor_is_clean(fakes, tmp_path):
    rc, out, calls = fakes("--yes")
    assert rc == 0, out
    steps = [l for l in out.splitlines() if l.startswith("== ")]
    assert [s[:6] for s in steps] == ["== 1/6", "== 2/6", "== 3/6", "== 4/6", "== 5/6", "== 6/6"]
    assert "fake sync: sync --frozen --no-dev --no-editable --reinstall-package slopymemory" in out
    assert "install-postgres --yes" in calls and "install-model --yes" in calls and "register --detected --yes" in calls and "doctor" in calls
    assert out.rstrip().endswith("Done. Open any project and ask your agent to set up memory.")
    assert (tmp_path / "home" / "venv" / "bin" / "slopymem").exists()


def test_no_harness_skips_step_5_and_a_lone_harnesses_fail_is_still_a_success(fakes):
    rc, out, calls = fakes("--yes", "--postgres=embedded", "--no-harness", doctor_text=HARNESS_FAIL)
    assert rc == 0, out
    assert "register" not in calls and "install-postgres --postgres embedded --yes" in calls
    assert "skipped (--no-harness)" in out and "expected with --no-harness" in out and "Done." not in out


def test_a_doctor_fail_is_the_verdict_not_done(fakes):
    rc, out, _ = fakes("--yes", doctor_text=HARNESS_FAIL)                     # harnesses failed on a run that registered them
    assert rc == 1 and "Installed, but the doctor found 1 problem(s) above" in out and "Done." not in out
    rc, out, _ = fakes("--yes", "--no-harness", doctor_text=TWO_FAILS)          # a second FAIL beside the expected one
    assert rc == 1 and "found 2 problem(s)" in out and "expected with --no-harness" not in out
    assert "The command is" in out                                              # said in every case: the install did happen


def test_a_doctor_that_does_not_reach_its_verdict_is_a_failure(fakes):
    rc, out, _ = fakes("--yes", doctor_text="ok   python  fine\n")            # no `doctor:` line: the run was cut short
    assert rc == 1 and "did not run to its verdict" in out and "Done." not in out


def test_flags_are_checked_before_anything_runs(fakes):
    rc, out, calls = fakes("--postgres")
    assert rc == 2 and "needs a value" in out and calls == ""
    rc, out, calls = fakes("--postgres", "sqlite")
    assert rc == 2 and "system or embedded" in out and calls == ""
    rc, out, calls = fakes("--bogus")
    assert rc == 2 and "unknown flag" in out
    rc, out, calls = fakes("--help")
    assert rc == 0 and "--no-harness" in out and "set -euo" not in out and calls == ""


def test_the_preflight_runs_on_the_bare_interpreter_before_the_sync(fakes):
    """The size question is asked of the interpreter uv found (`uv python find --system`), with -B and the checkout's
    src on the path, BEFORE the sync creates the venv; the import check runs on the venv's python after it."""
    rc, out, calls = fakes("--yes")
    assert rc == 0 and "Python 3.13:" in out
    lines = calls.splitlines()
    find = next(i for i, l in enumerate(lines) if l.startswith("fake uv python find --system 3.13"))
    pre = next(i for i, l in enumerate(lines) if l.startswith("fake python -B -c import sys; sys.path.insert(0, 'src'); from slopymemory.install_steps import preflight"))
    sync = next(i for i, l in enumerate(lines) if l.startswith("fake uv sync --frozen"))
    imp = next(i for i, l in enumerate(lines) if "import slopymemory, agent_memory" in l)
    assert find < pre < sync < imp
