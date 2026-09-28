import subprocess
from pathlib import Path

import pytest

from slopymemory import cli, updates


def _git(*a, cwd=None):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


def _clone_with_tags(tmp_path, *tags):
    remote, work = tmp_path / "r.git", tmp_path / "src"
    _git("init", "-q", "--bare", str(remote))
    _git("clone", "-q", str(remote), str(work))
    (work / "CHANGELOG.md").write_text("# Changelog\n\n## 0.4.0\n\n- the current one\n")
    _git("add", "CHANGELOG.md", cwd=work)
    _git("-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "-m", "a", cwd=work)
    _git("push", "-q", "origin", "HEAD", cwd=work)
    return remote, work


def _publish(tmp_path, remote, version):
    other = tmp_path / f"o{version}"
    _git("clone", "-q", str(remote), str(other))
    cl = other / "CHANGELOG.md"
    cl.write_text(cl.read_text().replace("# Changelog\n\n", f"# Changelog\n\n## {version}\n\n- a newer one\n\n"))
    _git("-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qam", version, cwd=other)
    _git("tag", f"v{version}", cwd=other)
    _git("push", "-q", "--tags", "origin", "HEAD", cwd=other)


@pytest.fixture
def installed(tmp_home, tmp_path, monkeypatch):
    tmp_home.mkdir(parents=True, exist_ok=True)
    remote, work = _clone_with_tags(tmp_path)
    updates.write_install(work, ["--no-harness"], True, str(remote))
    monkeypatch.setattr(updates, "installed", lambda: "0.4.0")
    calls = []
    monkeypatch.setattr(cli, "run_installer", lambda source, flags: calls.append((source, flags)) or 0)
    monkeypatch.setattr(cli, "running_stores", lambda: [])
    return remote, work, calls


def test_update_when_current_says_so_and_runs_nothing(installed, capsys):
    _, _, calls = installed
    assert cli.main(["update", "--yes"]) == 0
    assert "already the newest" in capsys.readouterr().out and calls == []


def test_update_pulls_prints_the_changes_and_reruns_the_installer_with_the_recorded_flags(installed, tmp_path, capsys):
    remote, work, calls = installed
    _publish(tmp_path, remote, "0.5.0")
    assert cli.main(["update", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "0.5.0" in out and "a newer one" in out and "the current one" not in out
    assert calls == [(work, ["--no-harness"])]


def test_update_refuses_a_clone_with_local_changes(installed, tmp_path, capsys):
    remote, work, calls = installed
    _publish(tmp_path, remote, "0.5.0")
    (work / "CHANGELOG.md").write_text("edited locally\n")
    assert cli.main(["update", "--yes"]) == 1
    assert "CHANGELOG.md" in capsys.readouterr().err and calls == []


def test_update_with_the_clone_gone_prints_the_manual_path(installed, capsys):
    import shutil
    remote, work, calls = installed
    shutil.rmtree(work)
    assert cli.main(["update", "--yes"]) == 1
    err = capsys.readouterr().err
    assert str(remote) in err and "./install.sh" in err and "SETUP.md#update" in err


def test_update_without_an_install_record_names_the_manual_path(tmp_home, capsys):
    tmp_home.mkdir(parents=True, exist_ok=True)
    assert cli.main(["update"]) == 1
    assert "git pull" in capsys.readouterr().err


def test_update_without_git_names_the_manual_path(installed, monkeypatch, capsys):
    monkeypatch.setenv("PATH", "/nonexistent")
    assert cli.main(["update", "--yes"]) == 1
    assert "SETUP.md#update" in capsys.readouterr().err


def test_update_offers_restarts_and_a_declined_one_says_so(installed, tmp_path, monkeypatch, capsys):
    remote, work, calls = installed
    _publish(tmp_path, remote, "0.5.0")
    from slopymemory.store import Store
    st = Store(name="a", dialect="coding", port=8780, database="a_db", postgres="system")
    monkeypatch.setattr(cli, "running_stores", lambda: [st])
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("n\n"))
    restarted = []
    monkeypatch.setattr(cli, "_restart", lambda s: restarted.append(s.name))
    assert cli.main(["update"]) == 0
    assert restarted == [] and "keeps the old code" in capsys.readouterr().out


def test_update_check_off_and_on(installed):
    assert cli.main(["update", "--check", "off"]) == 0 and updates.read_install()["update_check"] is False
    assert cli.main(["update", "--check", "on"]) == 0 and updates.read_install()["update_check"] is True


def test_record_install_writes_the_record_with_the_clones_remote(tmp_home, tmp_path):
    tmp_home.mkdir(parents=True, exist_ok=True)
    remote, work = _clone_with_tags(tmp_path)
    assert cli.main(["record-install", "--source", str(work), "--flags=--no-harness", "--update-check", "yes"]) == 0
    info = updates.read_install()
    assert info["remote"] == str(remote) and info["flags"] == ["--no-harness"] and info["update_check"] is True
