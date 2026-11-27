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
                               "config": "~/.h1/config.json", "instructions_file": str(tmp_path / "AGENT_RULES.md"),
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


# ---- review fixes: the plan must name everything the install does --------------------------------------------------

from slopymemory.harnesses import LEGACY_NAME, TRIGGER_PREFIX, trigger_line


def _named(tmp_path, name, *, line=None):
    f = tmp_path / "RULES.md"
    if line is not None:
        f.write_text(f"\n# slopymemory\n{line}\n")
    return Harness(id="h1", name="Harness One", detect=lambda: True, registered=lambda lp: True,
                   register_cmd=lambda lp: ["h1", "mcp", "add", "slopymemory", lp], config_hint="~/.h1/config.json",
                   instructions_file=lambda: f, registered_as=lambda lp: name,
                   unregister_cmd=lambda n: ["h1", "mcp", "remove", n])


def test_a_legacy_name_lists_the_rename_commands(tmp_path, quiet):
    h = _named(tmp_path, LEGACY_NAME, line=trigger_line(LEGACY_NAME))
    changes = _plan(tmp_path, [h])["harnesses"][0]["changes"]
    assert any(c.startswith("run: h1 mcp add slopymemory") for c in changes)
    assert "run: h1 mcp remove memory" in changes


def test_a_users_own_name_with_its_line_changes_nothing(tmp_path, quiet):
    h = _named(tmp_path, "mymem", line=trigger_line("mymem"))
    assert _plan(tmp_path, [h])["harnesses"][0]["changes"] == []


def test_a_stale_line_is_replaced_not_added(tmp_path, quiet):
    h = _named(tmp_path, "slopymemory", line=TRIGGER_PREFIX + " an older wording")
    assert _plan(tmp_path, [h])["harnesses"][0]["changes"] == [f"replace the slopymemory line in {tmp_path / 'RULES.md'}"]


def test_each_harness_names_its_config_and_instructions_file(tmp_path, quiet):
    e = _plan(tmp_path, [_harness(tmp_path)])["harnesses"][0]
    assert e["config"] == "~/.h1/config.json" and e["instructions_file"] == str(tmp_path / "AGENT_RULES.md")


def test_only_python_3_14_means_3_13_is_downloaded_and_its_links_written(tmp_path, quiet, monkeypatch):
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(tmp_path / "uvpy"))
    monkeypatch.setattr(install_plan, "_python_version", lambda exe: (3, 14))
    monkeypatch.setattr(install_plan.shutil, "which", lambda n: "/usr/bin/" + n if n in ("python3", "python3.14", "uv") else None)
    p = _plan(tmp_path, [])
    assert "python" in {d["what"] for d in p["downloads"]}
    assert str(tmp_path / "uvpy") in p["writes"] and str(Path.home() / ".local" / "bin") in p["writes"]


def test_a_3_13_named_python3_is_found(tmp_path, quiet, monkeypatch):
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(tmp_path / "uvpy"))
    monkeypatch.setattr(install_plan, "_python_version", lambda exe: (3, 13))
    monkeypatch.setattr(install_plan.shutil, "which", lambda n: "/usr/bin/" + n if n in ("python3", "uv") else None)
    assert "python" not in {d["what"] for d in _plan(tmp_path, [])["downloads"]}


def test_no_uv_names_the_installers_receipt_folder(tmp_path, quiet, monkeypatch):
    monkeypatch.setattr(install_plan.shutil, "which", lambda n: None if n == "uv" else "/usr/bin/" + n)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert str(Path.home() / ".config" / "uv") in _plan(tmp_path, [])["writes"]


def test_not_linux_and_no_curl_without_uv_are_blockers(tmp_path, quiet, monkeypatch):
    monkeypatch.setattr(install_plan.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(install_plan.shutil, "which", lambda n: None if n in ("uv", "curl") else "/usr/bin/" + n)
    blockers = " ".join(_plan(tmp_path, [])["blockers"])
    assert "Linux" in blockers and "curl" in blockers


def test_cached_weights_without_their_code_repo_still_download_the_code(tmp_path, quiet, monkeypatch):
    snap = tmp_path / "weights"; snap.mkdir()
    (snap / "config.json").write_text('{"auto_map": {"AutoModel": "org/code-repo--modeling.Model"}}')
    monkeypatch.setattr(install_steps, "weights_complete", lambda model, rev: snap)
    monkeypatch.setattr(install_steps, "local_snapshot", lambda repo, rev: None)
    what = {d["what"]: d for d in _plan(tmp_path, [])["downloads"]}
    assert "model" not in what and "org/code-repo" in what["model code"]["note"]


def test_the_hub_cache_fallback_honours_xdg_cache_home(tmp_path, monkeypatch):
    for v in ("HF_HUB_CACHE", "HF_HOME"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert install_steps.hub_cache_from_env() == tmp_path / "xdg" / "huggingface" / "hub"


def test_a_failure_while_planning_is_reported_as_json_not_a_traceback(tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise PermissionError("RULES.md: permission denied")
    monkeypatch.setattr(install_plan, "plan", boom)
    code = install_plan.main([str(tmp_path / "venv"), str(tmp_path), "1", "", ""])
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["schema"] == 1 and "permission denied" in out["blockers"][0]


def test_unknown_package_sizes_mark_the_total_incomplete(tmp_path, quiet):
    pk = next(d for d in _plan(tmp_path, [])["downloads"] if d["what"] == "packages")
    assert pk["unknown_size"] and pk["bytes_complete"] is False
