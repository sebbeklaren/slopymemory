"""The weekly compatibility check's own logic, on fixed data — no network, no harness."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import compat_check as cc  # noqa: E402


def _rec(versions, contracts):
    return {"when": "2026-09-28T00:00:00+00:00", "versions": versions, "contracts": contracts}


def test_the_first_record_has_nothing_to_compare():
    assert cc.diff(None, _rec({"claude-code": "2.1.283"}, {})) == ["first record: nothing to compare yet"]


def test_diff_names_changed_versions_and_contracts_that_changed_state():
    prev = _rec({"claude-code": "2.1.283", "codex": "0.156.1"}, {"claude-code": "passed", "codex": "passed"})
    cur = _rec({"claude-code": "2.1.290", "codex": "0.156.1"}, {"claude-code": "failed", "codex": "passed"})
    out = cc.diff(prev, cur)
    assert "claude-code: 2.1.283 -> 2.1.290" in out and "contract claude-code: passed -> failed" in out
    assert not any("codex" in line for line in out)


def test_a_harness_gone_since_the_last_record_is_said():
    out = cc.diff(_rec({"codex": "0.156.1"}, {}), _rec({"codex": None}, {}))
    assert out == ["codex: not installed now (was 0.156.1)"]


def test_release_notes_keep_only_new_releases_that_touch_what_we_rely_on():
    releases = [
        {"tag_name": "v2.1.290", "published_at": "2026-10-02T00:00:00Z", "body": "Fixed a crash.\nMCP servers now reload on config change."},
        {"tag_name": "v2.1.289", "published_at": "2026-10-01T00:00:00Z", "body": "Faster startup."},
        {"tag_name": "v2.1.200", "published_at": "2026-08-01T00:00:00Z", "body": "mcp changes long ago"},
    ]

    def fake(cmd, **k):
        return subprocess.CompletedProcess(cmd, 0, stdout="\n".join(json.dumps(r) for r in releases), stderr="")
    out = cc.release_notes("2026-09-28T00:00:00+00:00", repos={"claude-code": "o/r"}, run=fake)
    assert out == ["claude-code v2.1.290: MCP servers now reload on config change."]


def test_release_notes_without_gh_are_skipped_and_said():
    def missing(cmd, **k):
        raise FileNotFoundError("gh")
    assert cc.release_notes("2026-09-28T00:00:00+00:00", repos={"x": "o/r"}, run=missing) == \
        ["release notes skipped: gh is not installed"]


def test_versions_of_a_missing_cli_are_none():
    def fake(cmd, **k):
        if cmd[0] == "codex":
            raise FileNotFoundError("codex")
        return subprocess.CompletedProcess(cmd, 0, stdout="2.1.283 (Claude Code)\n", stderr="")
    v = cc.harness_versions(clis={"claude-code": ["claude", "--version"], "codex": ["codex", "--version"]}, run=fake)
    assert v == {"claude-code": "2.1.283 (Claude Code)", "codex": None}


def test_contract_results_are_read_from_pytest_output():
    out = ("PASSED tests/contract/test_harness_contracts.py::test_register_is_seen_by_the_harness_and_unregister_removes_it[claude-code]\n"
           "FAILED tests/contract/test_harness_contracts.py::test_register_is_seen_by_the_harness_and_unregister_removes_it[codex] - x\n"
           "SKIPPED [1] tests/contract/test_harness_contracts.py:45: pi is not installed here\n")
    assert cc.parse_contracts(out) == {"claude-code": "passed", "codex": "failed"}


def test_dependabot_watches_the_lock_and_the_actions_weekly():
    import yaml
    cfg = yaml.safe_load((Path(__file__).parent.parent / ".github" / "dependabot.yml").read_text())
    eco = {u["package-ecosystem"]: u for u in cfg["updates"]}
    assert cfg["version"] == 2 and set(eco) == {"uv", "github-actions"}
    assert all(u["schedule"]["interval"] == "weekly" for u in eco.values())


def test_the_compatibility_page_names_every_harness_the_contracts_cover():
    page = (Path(__file__).parent.parent / "docs" / "COMPATIBILITY.md").read_text()
    for name in ("Claude Code", "Codex", "Harnesses configured by hand", "MCP Python SDK", "SLOPYMEM_CONTRACT=1", "compat_check.py"):
        assert name in page


def test_contracts_that_could_not_run_are_a_failure_not_a_pass():
    res = cc.run_contracts(installed=["claude-code"], run=lambda cmd, env: (2, "", "No module named pytest\n"))
    assert res == {"claude-code": "could not run: No module named pytest"}


def test_an_installed_harness_with_no_result_is_a_failure():
    out = "PASSED tests/contract/x.py::test_y[codex]\n"
    res = cc.run_contracts(installed=["claude-code", "codex"], run=lambda cmd, env: (0, out, ""))
    assert res == {"codex": "passed", "claude-code": "no result"}


def test_a_hung_contract_run_is_a_failure_and_raises_nothing():
    def hang(cmd, env):
        raise subprocess.TimeoutExpired(cmd, 900)
    assert cc.run_contracts(installed=["codex"], run=hang) == {"codex": "timed out after 900 s"}


def test_the_contract_run_has_no_terminal_and_its_own_process_group(monkeypatch):
    seen = {}

    class P:
        returncode = 0
        def communicate(self, timeout=None):
            return "", ""
    def popen(cmd, **k):
        seen.update(k)
        return P()
    monkeypatch.setattr(cc.subprocess, "Popen", popen)
    cc._run_pytest(["x"], {})
    assert seen["stdin"] == subprocess.DEVNULL and seen["start_new_session"] is True


def test_dependabot_leaves_the_major_versions_that_are_planned_migrations():
    """A major version of the MCP SDK or of the embedding stack is a migration (v2 moves the server modules; a new
    embedder library can change every stored vector) — planned and proven, never a weekly pull request."""
    import yaml
    cfg = yaml.safe_load((Path(__file__).parent.parent / ".github" / "dependabot.yml").read_text())
    uv = next(u for u in cfg["updates"] if u["package-ecosystem"] == "uv")
    ignored = {i["dependency-name"]: i.get("update-types") for i in uv.get("ignore", [])}
    for name in ("mcp", "sentence-transformers", "transformers", "torch"):
        assert ignored.get(name) == ["version-update:semver-major"], name


def test_dependabot_updates_only_within_the_ranges_pyproject_pins():
    """The ranges in pyproject.toml are deliberate (the embedding stack is pinned so stored vectors stay the same):
    Dependabot may move the lock inside them, never widen them — widening a pin is a planned change."""
    import yaml
    cfg = yaml.safe_load((Path(__file__).parent.parent / ".github" / "dependabot.yml").read_text())
    uv = next(u for u in cfg["updates"] if u["package-ecosystem"] == "uv")
    assert uv["versioning-strategy"] == "lockfile-only"


def test_release_notes_read_every_page_and_survive_malformed_output():
    pages = []

    def fake(cmd, **k):
        pages.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="{not json", stderr="")
    out = cc.release_notes("2026-09-28T00:00:00+00:00", repos={"x": "o/r"}, run=fake)
    assert "--paginate" in pages[0] and out == ["x: release notes not read (output was not a list of releases)"]


def test_the_keywords_catch_configuration_and_settings_words():
    assert cc.KEYWORDS.search("Configuration files are now reloaded") and cc.KEYWORDS.search("new setting")


def test_a_record_of_the_wrong_shape_is_skipped(tmp_path):
    (tmp_path / "a-older.json").write_text(json.dumps(_rec({"codex": "1"}, {})))
    (tmp_path / "b-newer.json").write_text("[1, 2]")
    assert cc.latest_record(tmp_path)["versions"] == {"codex": "1"}


def test_the_release_window_starts_at_the_last_record_whose_releases_were_read():
    prev = {"when": "2026-09-28T00:00:00+00:00", "releases_read": False, "since": "2026-09-01T00:00:00+00:00"}
    assert cc.release_window(prev, "2026-10-01T00:00:00+00:00") == "2026-09-01T00:00:00+00:00"
    assert cc.release_window({"when": "2026-09-28T00:00:00+00:00", "releases_read": True}, "x") == "2026-09-28T00:00:00+00:00"
    assert cc.release_window({}, "2026-10-01T00:00:00+00:00") == "2026-10-01T00:00:00+00:00"
