import json
import subprocess
import time

import pytest

from slopymemory import updates


@pytest.fixture
def home(tmp_home):
    tmp_home.mkdir(parents=True, exist_ok=True)
    return tmp_home


def _remote(tmp_path, tags):
    r = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(r)], check=True)
    w = tmp_path / "w"
    subprocess.run(["git", "clone", "-q", str(r), str(w)], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q",
                    "--allow-empty", "-m", "x"], check=True)
    for t in tags:
        subprocess.run(["git", "-C", str(w), "tag", t], check=True)
    subprocess.run(["git", "-C", str(w), "push", "-q", "--tags", "origin", "HEAD"], check=True)
    return str(r)


def test_install_record_round_trips_and_the_check_can_be_switched(home, tmp_path):
    updates.write_install(tmp_path, ["--no-harness"], True, "https://example.invalid/x.git")
    info = updates.read_install()
    assert info["source"] == str(tmp_path) and info["flags"] == ["--no-harness"] and info["update_check"] is True
    assert info["version"] == updates.installed()
    updates.set_check(False)
    assert updates.read_install()["update_check"] is False


def test_latest_ignores_tags_that_are_not_versions():
    assert updates.latest_of(["v0.3", "v0.4.0", "v1.0-rc1", "latest", "v0.10.1", "v0.9"]) == "0.10.1"
    assert updates.latest_of(["nope"]) is None


def test_refresh_reads_the_remote_and_caches_it(home, tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "installed", lambda: "0.4.0")
    updates.write_install(tmp_path, [], True, _remote(tmp_path, ["v0.4.0", "v0.5.0"]))
    c = updates.refresh()
    assert c["latest"] == "0.5.0" and c["error"] is None
    assert updates.newer() == "0.5.0"
    assert updates.notice() == "slopymemory v0.5.0 is available (installed v0.4.0) — tell the user: run `slopymem update`."


def test_a_fresh_cache_is_not_checked_again(home, tmp_path):
    updates.write_install(tmp_path, [], True, "https://example.invalid/x.git")
    updates.cache_file().write_text(json.dumps({"checked": time.time(), "latest": "0.4.0", "error": None}))
    calls = []
    updates.refresh(run=lambda *a, **k: calls.append(a))
    assert calls == []


def test_a_corrupt_cache_is_treated_as_stale(home, tmp_path):
    updates.write_install(tmp_path, [], True, "https://example.invalid/x.git")
    updates.cache_file().write_text("{torn")
    calls = []

    def fake(cmd, **k):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="abc\trefs/tags/v9.9.9\n", stderr="")
    assert updates.refresh(run=fake)["latest"] == "9.9.9" and len(calls) == 1


def test_no_git_is_recorded_not_raised(home, tmp_path):
    updates.write_install(tmp_path, [], True, "https://example.invalid/x.git")

    def missing(cmd, **k):
        raise FileNotFoundError("git")
    c = updates.refresh(run=missing)
    assert c["error"] and "git" in c["error"] and updates.notice() is None


def test_a_timeout_is_recorded_not_raised(home, tmp_path):
    updates.write_install(tmp_path, [], True, "https://example.invalid/x.git")

    def slow(cmd, **k):
        raise subprocess.TimeoutExpired(cmd, 3)
    assert "timed out" in updates.refresh(run=slow)["error"]


def test_the_check_is_off_or_unrecorded_means_no_call_and_no_notice(home, tmp_path):
    calls = []
    updates.refresh(run=lambda *a, **k: calls.append(a))                  # no install.toml at all
    updates.write_install(tmp_path, [], False, "https://example.invalid/x.git")
    updates.refresh(run=lambda *a, **k: calls.append(a))
    assert calls == [] and updates.notice() is None


def test_no_notice_when_installed_is_newer(home, tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "installed", lambda: "0.6.0")
    updates.write_install(tmp_path, [], True, "https://example.invalid/x.git")
    updates.cache_file().write_text(json.dumps({"checked": time.time(), "latest": "0.5.0", "error": None}))
    assert updates.newer() is None and updates.notice() is None


def test_refresh_counts_only_v_tags(home, tmp_path):
    updates.write_install(tmp_path, [], True, "https://example.invalid/x.git")

    def fake(cmd, **k):
        return subprocess.CompletedProcess(cmd, 0, stdout="a\trefs/tags/2026.09\nb\trefs/tags/v0.5.0\n", stderr="")
    assert updates.refresh(run=fake)["latest"] == "0.5.0"


def test_the_check_can_never_prompt_on_a_terminal(home, tmp_path):
    updates.write_install(tmp_path, [], True, "git@example.invalid:x.git")
    seen = {}

    def spy(cmd, **k):
        seen.update(k)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    updates.refresh(run=spy)
    assert seen["stdin"] == subprocess.DEVNULL and seen["start_new_session"] is True
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0" and "BatchMode=yes" in seen["env"]["GIT_SSH_COMMAND"]
