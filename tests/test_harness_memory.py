import json
import stat
import pytest
from slopymemory import harness_memory as hm


@pytest.fixture
def home(tmp_path, tmp_home, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude").mkdir()
    return tmp_path


def settings(home):
    return home / ".claude" / "settings.json"


def test_off_adds_the_key_and_on_restores_the_file_byte_for_byte(home):
    original = '{\n  "permissions": {"allow": ["Bash(ls)"]},\n  "theme": "dark"\n}\n'
    settings(home).write_text(original)
    hm.turn_off("claude-code")
    assert json.loads(settings(home).read_text())["autoMemoryEnabled"] is False
    assert hm.status()["claude-code"] == "off (slopymemory)"
    hm.turn_on("claude-code", choose=lambda p, c: pytest.fail("no conflict expected"))
    assert settings(home).read_text() == original
    assert hm.status()["claude-code"] == "on"


def test_off_with_no_settings_file_creates_it_and_on_removes_it(home):
    hm.turn_off("claude-code")
    assert json.loads(settings(home).read_text()) == {"autoMemoryEnabled": False}
    hm.turn_on("claude-code", choose=lambda p, c: pytest.fail("no conflict expected"))
    assert not settings(home).exists()


def test_a_true_value_is_restored_as_true(home):
    settings(home).write_text('{"autoMemoryEnabled": true}')
    hm.turn_off("claude-code"); hm.turn_on("claude-code", choose=lambda p, c: pytest.fail("no conflict"))
    assert json.loads(settings(home).read_text()) == {"autoMemoryEnabled": True}


def test_already_off_by_the_user_is_left_off_both_ways(home):
    settings(home).write_text('{"autoMemoryEnabled": false}')
    hm.turn_off("claude-code")
    assert hm.status()["claude-code"] == "off (slopymemory)"
    hm.turn_on("claude-code", choose=lambda p, c: pytest.fail("no conflict"))
    assert settings(home).read_text() == '{"autoMemoryEnabled": false}'     # the user's own choice, untouched


def test_on_keeps_other_keys_added_while_off(home):
    settings(home).write_text('{"theme": "dark"}')
    hm.turn_off("claude-code")
    d = json.loads(settings(home).read_text()); d["model"] = "x"; settings(home).write_text(json.dumps(d))
    hm.turn_on("claude-code", choose=lambda p, c: pytest.fail("no conflict"))
    assert json.loads(settings(home).read_text()) == {"theme": "dark", "model": "x"}   # our key gone, theirs kept


@pytest.mark.parametrize("now,expected_current", [('{"autoMemoryEnabled": true}', "true"), ('{}', "absent")])
def test_on_never_restores_over_a_newer_choice(home, now, expected_current):
    hm.turn_off("claude-code")
    settings(home).write_text(now)                                  # changed by the user or the harness while off
    asked = []
    out = hm.turn_on("claude-code", choose=lambda prior, current: asked.append((prior, current)) or "keep")
    assert asked == [("absent", expected_current)]                  # JSON spellings, not Python's True/False
    assert settings(home).read_text() == now and "kept" in out
    assert hm.status()["claude-code"] == "on"                       # the record is cleared either way


def test_on_restores_when_the_user_chooses_restore(home):
    settings(home).write_text('{"autoMemoryEnabled": true}')
    hm.turn_off("claude-code")
    settings(home).write_text('{"autoMemoryEnabled": true, "x": 1}')   # flipped back on, plus another key
    hm.turn_on("claude-code", choose=lambda p, c: "restore")
    assert json.loads(settings(home).read_text()) == {"autoMemoryEnabled": True, "x": 1}


@pytest.mark.parametrize("text", ["{not json", "[1, 2]"])
def test_off_refuses_an_unparseable_or_non_object_settings_file(home, text):
    settings(home).write_text(text)
    with pytest.raises(hm.HarnessMemoryError, match="SETUP.md#harness-memory"):
        hm.turn_off("claude-code")
    assert settings(home).read_text() == text


def test_on_without_a_record_refuses(home):
    settings(home).write_text('{"autoMemoryEnabled": false}')
    with pytest.raises(hm.HarnessMemoryError, match="no record"):
        hm.turn_on("claude-code", choose=lambda p, c: "restore")
    assert settings(home).read_text() == '{"autoMemoryEnabled": false}'


def test_off_twice_keeps_the_first_record(home):
    settings(home).write_text('{"autoMemoryEnabled": true}')
    hm.turn_off("claude-code"); hm.turn_off("claude-code")
    hm.turn_on("claude-code", choose=lambda p, c: pytest.fail("no conflict"))
    assert json.loads(settings(home).read_text()) == {"autoMemoryEnabled": True}


def test_status_reports_off_not_by_us(home):
    settings(home).write_text('{"autoMemoryEnabled": false}')
    assert hm.status()["claude-code"] == "off (not by slopymemory)"


def test_on_restores_the_whole_file_when_it_was_deleted_while_off(home):
    original = '{\n  "permissions": {"allow": ["Bash(ls)"]},\n  "theme": "dark",\n  "autoMemoryEnabled": true\n}\n'
    settings(home).write_text(original)
    hm.turn_off("claude-code")
    settings(home).unlink()                                    # the whole file, not just our key, went away
    out = hm.turn_on("claude-code", choose=lambda p, c: "restore")
    assert settings(home).read_text() == original               # not a fresh file holding only our key
    assert "recreated" in out


def test_on_leaves_no_file_when_none_existed_before_off_and_it_was_deleted_while_off(home):
    hm.turn_off("claude-code")
    settings(home).unlink()
    hm.turn_on("claude-code", choose=lambda p, c: "restore")
    assert not settings(home).exists()


def test_on_restores_original_bytes_when_the_file_was_only_reformatted_while_off(home):
    original = '{\n  "permissions": {"allow": ["Bash(ls)"]},\n  "theme": "dark"\n}\n'
    settings(home).write_text(original)
    hm.turn_off("claude-code")
    # same meaning, different whitespace/key order — as if the harness itself rewrote the file
    reformatted = '{"theme": "dark", "permissions": {"allow": ["Bash(ls)"]}, "autoMemoryEnabled": false}'
    settings(home).write_text(reformatted)
    hm.turn_on("claude-code", choose=lambda p, c: pytest.fail("no conflict expected"))
    assert settings(home).read_text() == original


def test_status_reports_on_changed_since_when_the_value_was_flipped_back_while_off(home):
    settings(home).write_text('{"autoMemoryEnabled": true}')
    hm.turn_off("claude-code")
    settings(home).write_text('{"autoMemoryEnabled": true}')       # switched back on while off, by hand or by Claude
    assert hm.status()["claude-code"] == "on (changed since slopymemory switched it off)"


def test_off_reapplies_when_the_value_changed_back_since_the_record_was_made(home):
    settings(home).write_text('{"autoMemoryEnabled": true}')
    hm.turn_off("claude-code")
    settings(home).write_text('{"autoMemoryEnabled": true}')       # switched back on while off
    out = hm.turn_off("claude-code")
    assert json.loads(settings(home).read_text())["autoMemoryEnabled"] is False
    assert "switched OFF again" in out
    # the restore target is still the ORIGINAL prior value, not the intervening one
    hm.turn_on("claude-code", choose=lambda p, c: pytest.fail("no conflict expected"))
    assert json.loads(settings(home).read_text()) == {"autoMemoryEnabled": True}


def test_off_saves_the_record_before_the_settings_write_and_cleans_up_if_it_fails(home, monkeypatch):
    def boom(tmp, target):
        # by the time the settings write is attempted, the record must already be on disk
        recorded = hm.STATE_FILE().exists() and "claude-code" in json.loads(hm.STATE_FILE().read_text())
        assert recorded, "the record must be saved before the settings write is attempted"
        raise OSError("disk full")

    monkeypatch.setattr(hm, "_replace", boom)
    with pytest.raises(hm.HarnessMemoryError, match="SETUP.md#harness-memory"):
        hm.turn_off("claude-code")

    assert not hm.STATE_FILE().exists() or "claude-code" not in json.loads(hm.STATE_FILE().read_text())
    assert not settings(home).exists()                              # the settings file itself was never touched


def test_on_raises_with_the_anchor_and_keeps_the_record_when_the_restore_write_fails(home, monkeypatch):
    settings(home).write_text('{"autoMemoryEnabled": true}')
    hm.turn_off("claude-code")

    real_replace = hm._replace
    calls = {"n": 0}

    def flaky(tmp, target):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        return real_replace(tmp, target)

    monkeypatch.setattr(hm, "_replace", flaky)
    with pytest.raises(hm.HarnessMemoryError, match="SETUP.md#harness-memory"):
        hm.turn_on("claude-code", choose=lambda p, c: pytest.fail("no conflict expected"))

    assert "claude-code" in json.loads(hm.STATE_FILE().read_text())     # the restore target survived the failure

    # a retry once whatever blocked the write clears succeeds
    hm.turn_on("claude-code", choose=lambda p, c: pytest.fail("no conflict expected"))
    assert json.loads(settings(home).read_text()) == {"autoMemoryEnabled": True}


def test_off_and_on_set_the_mode_on_the_temp_file_before_the_replace(home, monkeypatch):
    settings(home).write_text('{"theme": "dark"}')
    settings(home).chmod(0o600)

    seen_modes_at_replace = []
    real_replace = hm._replace

    def spy(tmp, target):
        # the temp file's mode must already be right BEFORE the rename makes it visible at `target`
        seen_modes_at_replace.append(stat.S_IMODE(tmp.stat().st_mode))
        return real_replace(tmp, target)

    monkeypatch.setattr(hm, "_replace", spy)
    hm.turn_off("claude-code")
    assert seen_modes_at_replace == [0o600]
    assert stat.S_IMODE(settings(home).stat().st_mode) == 0o600

    def raise_on_chmod(*a, **k):
        raise OSError("no permission to chmod")
    monkeypatch.setattr(hm.os, "chmod", raise_on_chmod)
    before = settings(home).read_text()
    with pytest.raises(hm.HarnessMemoryError, match="SETUP.md#harness-memory") as exc:
        hm.turn_on("claude-code", choose=lambda p, c: pytest.fail("no conflict expected"))
    assert "not changed" in str(exc.value)
    assert settings(home).read_text() == before                     # a chmod failure must never leave a half write


def test_off_and_on_write_through_a_symlinked_settings_file(home):
    real = home / "real-settings.json"
    original = '{"theme": "dark"}'
    real.write_text(original)
    settings(home).symlink_to(real)

    hm.turn_off("claude-code")
    assert settings(home).is_symlink()                              # the link itself is untouched
    assert json.loads(real.read_text())["autoMemoryEnabled"] is False   # the target carries the change

    hm.turn_on("claude-code", choose=lambda p, c: pytest.fail("no conflict expected"))
    assert settings(home).is_symlink()
    assert real.read_text() == original


def test_off_treats_an_integer_zero_as_different_from_json_false(home):
    hm.turn_off("claude-code")
    settings(home).write_text('{"autoMemoryEnabled": 0}')            # not JSON false, though Python's 0 == False
    asked = []
    hm.turn_on("claude-code", choose=lambda p, c: asked.append((p, c)) or "keep")
    assert asked                                                     # must be treated as a conflict, not silently "still off"


def test_off_and_on_preserve_the_settings_files_permission_mode(home):
    settings(home).write_text('{"theme": "dark"}')
    settings(home).chmod(0o600)
    hm.turn_off("claude-code")
    assert stat.S_IMODE(settings(home).stat().st_mode) == 0o600
    hm.turn_on("claude-code", choose=lambda p, c: pytest.fail("no conflict expected"))
    assert stat.S_IMODE(settings(home).stat().st_mode) == 0o600


class FakeCodex:
    def __init__(self, enabled: bool):
        self.enabled, self.calls = enabled, []

    def __call__(self, cmd, **kw):
        import subprocess
        self.calls.append(cmd)
        if cmd[1:3] == ["features", "list"]:
            line = f"memories                                 stable             {'true' if self.enabled else 'false'}\n"
            return subprocess.CompletedProcess(cmd, 0, stdout="apps  stable  true\n" + line, stderr="")
        if cmd[1:3] == ["features", "disable"]:
            self.enabled = False
        if cmd[1:3] == ["features", "enable"]:
            self.enabled = True
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


@pytest.fixture
def codex(home, monkeypatch):
    fake = FakeCodex(enabled=True)
    monkeypatch.setattr(hm.subprocess, "run", fake)
    monkeypatch.setattr(hm, "_codex_available", lambda: True)
    return fake


def test_codex_off_disables_and_on_re_enables(codex):
    hm.turn_off("codex")
    assert ["codex", "features", "disable", "memories"] in codex.calls and codex.enabled is False
    hm.turn_on("codex", choose=lambda p, c: pytest.fail("no conflict"))
    assert codex.enabled is True


def test_codex_already_off_is_left_off(codex):
    codex.enabled = False
    hm.turn_off("codex"); hm.turn_on("codex", choose=lambda p, c: pytest.fail("no conflict"))
    assert codex.enabled is False and ["codex", "features", "enable", "memories"] not in codex.calls


def test_codex_newer_choice_is_not_overridden(codex):
    hm.turn_off("codex"); codex.enabled = True                       # the user turned it back on themselves
    asked = []
    hm.turn_on("codex", choose=lambda p, c: asked.append((p, c)) or "keep")
    assert asked and codex.enabled is True


def test_codex_off_reapplies_when_it_was_turned_back_on_since_the_record_was_made(codex):
    hm.turn_off("codex")
    codex.enabled = True                                              # switched back on while off
    out = hm.turn_off("codex")
    assert codex.enabled is False and "again" in out
    # the restore target is still the ORIGINAL prior value (True), not the intervening flip
    hm.turn_on("codex", choose=lambda p, c: pytest.fail("no conflict expected"))
    assert codex.enabled is True


def test_codex_command_failure_is_reported(home, monkeypatch):
    import subprocess
    monkeypatch.setattr(hm, "_codex_available", lambda: True)
    def boom(cmd, **kw): raise subprocess.CalledProcessError(1, cmd, stderr="nope")
    monkeypatch.setattr(hm.subprocess, "run", boom)
    with pytest.raises(hm.HarnessMemoryError, match="SETUP.md#harness-memory"):
        hm.turn_off("codex")


def test_codex_off_rolls_back_the_record_if_the_disable_command_fails(home, monkeypatch):
    import subprocess
    monkeypatch.setattr(hm, "_codex_available", lambda: True)

    def flaky(cmd, **kw):
        if cmd[1:3] == ["features", "list"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="memories  stable  true\n", stderr="")
        raise subprocess.CalledProcessError(1, cmd, stderr="nope")

    monkeypatch.setattr(hm.subprocess, "run", flaky)
    with pytest.raises(hm.HarnessMemoryError, match="SETUP.md#harness-memory"):
        hm.turn_off("codex")
    assert not hm.STATE_FILE().exists() or "codex" not in json.loads(hm.STATE_FILE().read_text())


def test_codex_on_without_a_record_refuses(codex):
    with pytest.raises(hm.HarnessMemoryError, match="no record"):
        hm.turn_on("codex", choose=lambda p, c: "restore")
    assert codex.calls == []                                         # never even asked Codex its state


def test_status_omits_codex_when_not_available(home):
    assert "codex" not in hm.status()                                # the autouse fixture leaves it unavailable


def test_status_reports_codex_off_by_slopymemory(codex):
    hm.turn_off("codex")
    assert hm.status()["codex"] == "off (slopymemory)"


def test_status_reports_codex_on(codex):
    assert hm.status()["codex"] == "on"


def test_status_reports_codex_off_not_by_slopymemory(codex):
    codex.enabled = False
    assert hm.status()["codex"] == "off (not by slopymemory)"


def test_status_reports_codex_changed_since(codex):
    hm.turn_off("codex")
    codex.enabled = True                                              # switched back on while off
    assert hm.status()["codex"] == "on (changed since slopymemory switched it off)"


def test_status_reports_unknown_when_the_codex_command_fails(home, monkeypatch):
    import subprocess
    monkeypatch.setattr(hm, "_codex_available", lambda: True)
    def boom(cmd, **kw): raise subprocess.CalledProcessError(1, cmd, stderr="nope")
    monkeypatch.setattr(hm.subprocess, "run", boom)
    out = hm.status()
    assert out["codex"].startswith("unknown (") and "nope" in out["codex"]
