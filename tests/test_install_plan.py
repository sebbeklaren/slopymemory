"""`install.sh --plan --json`: everything an install would do, reported before it does anything — so a harness can
show a consent screen built only from what the installer itself says."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from slopymemory import install_plan, install_steps
from slopymemory.harnesses import Harness, OFFER_LINE

ROOT = Path(__file__).resolve().parent.parent


def _tree(root: Path) -> set:
    return {p.relative_to(root) for p in root.rglob("*")} if root.exists() else set()


def _harness(tmp_path, *, registered=False, line_present=False, detected=True) -> Harness:
    f = tmp_path / "AGENT_RULES.md"
    if line_present:
        f.write_text(f"\n# slopymemory\n{OFFER_LINE}\n")
    return Harness(id="h1", name="Harness One", detect=lambda: detected, registered=lambda lp: registered,
                   register_cmd=lambda lp: ["h1", "mcp", "add", "slopymemory", lp], config_hint="~/.h1/config.json",
                   instructions_file=lambda: f)


@pytest.fixture
def quiet(monkeypatch):
    """No network, a roomy disk, the system tool present: each test changes only what it is about."""
    monkeypatch.setattr(install_steps, "remote_size", lambda url: None)
    monkeypatch.setattr(install_steps, "free_bytes", lambda path: 100 * 2**30)
    monkeypatch.setattr(install_steps, "check_tools", lambda: (True, "ss found"))
    monkeypatch.setattr(install_steps, "weights_complete", lambda model, rev: None)


def _plan(tmp_path, known, **kw):
    home = tmp_path / "home"
    return install_plan.plan(home / "venv", home, known=known, cache=None, **kw)


def test_a_detected_unregistered_harness_lists_its_exact_command_and_the_line(tmp_path, quiet):
    h = _harness(tmp_path)
    p = _plan(tmp_path, [h])
    lp = str(tmp_path / "home" / "venv" / "bin" / "slopymem-mcp")
    assert p["harnesses"] == [{"id": "h1", "name": "Harness One", "registered": False,
                               "changes": [f"run: h1 mcp add slopymemory {lp}",
                                           f"add one line to {tmp_path / 'AGENT_RULES.md'}"]}]


def test_a_registered_harness_with_its_line_changes_nothing(tmp_path, quiet):
    p = _plan(tmp_path, [_harness(tmp_path, registered=True, line_present=True)])
    assert p["harnesses"][0]["registered"] is True and p["harnesses"][0]["changes"] == []


def test_an_undetected_harness_is_not_listed(tmp_path, quiet):
    assert _plan(tmp_path, [_harness(tmp_path, detected=False)])["harnesses"] == []


def test_no_harness_lists_no_harness_changes(tmp_path, quiet):
    assert _plan(tmp_path, [_harness(tmp_path)], harness=False)["harnesses"] == []


def test_the_plan_names_the_downloads_and_the_space(tmp_path, quiet):
    p = _plan(tmp_path, [])
    what = {d["what"]: d for d in p["downloads"]}
    assert what["packages"]["bytes"] > 0
    assert "model" in what and what["model"]["bytes"] is None and "0.5 GB" in what["model"]["note"]
    assert p["free_bytes_needed"] == install_steps.MIN_FREE_BYTES and p["free_bytes"] == 100 * 2**30
    assert p["blockers"] == [] and p["unattended_flag"] == "--yes" and p["schema"] == 1


def test_a_cached_model_is_not_a_download(tmp_path, quiet, monkeypatch):
    monkeypatch.setattr(install_steps, "weights_complete", lambda model, rev: tmp_path)
    assert "model" not in {d["what"] for d in _plan(tmp_path, [])["downloads"]}


def test_too_little_space_and_a_missing_tool_are_blockers(tmp_path, quiet, monkeypatch):
    monkeypatch.setattr(install_steps, "free_bytes", lambda path: 2**30)
    monkeypatch.setattr(install_steps, "check_tools", lambda: (False, "ss not found"))
    blockers = _plan(tmp_path, [])["blockers"]
    assert any("ss not found" in b for b in blockers) and any("GB" in b for b in blockers)


def test_the_postgres_choice_follows_the_flag(tmp_path, quiet):
    assert _plan(tmp_path, [], postgres="system")["postgres"]["choice"] == "system"
    emb = _plan(tmp_path, [], postgres="embedded")["postgres"]
    assert emb["choice"] == "embedded" and str(tmp_path / "home" / "pg") in emb["writes"]


def test_the_plan_writes_nothing(tmp_path, quiet):
    before = _tree(tmp_path)
    _plan(tmp_path, [_harness(tmp_path)])
    assert _tree(tmp_path) == before


@pytest.mark.skipif(sys.platform != "linux", reason="the installer is Linux only")
def test_install_sh_plan_json_prints_one_object_and_changes_nothing(tmp_path):
    home = tmp_path / "h"; home.mkdir()
    env = {**os.environ, "HOME": str(home), "SLOPYMEM_HOME": str(home / ".slopymemory"),
           "SLOPYMEM_PLAN_OFFLINE": "1"}
    before = _tree(home)
    r = subprocess.run(["bash", "install.sh", "--plan", "--json", "--no-harness"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    p = json.loads(r.stdout)
    assert p["schema"] == 1 and p["home"] == str(home / ".slopymemory") and p["harnesses"] == []
    assert _tree(home) == before


def test_the_plan_says_what_the_daily_update_check_sends(tmp_path, quiet):
    on = _plan(tmp_path, [])["update_check"]
    assert on["enabled"] is True and "git ls-remote" in on["note"]
    assert _plan(tmp_path, [], update_check=False)["update_check"]["enabled"] is False
