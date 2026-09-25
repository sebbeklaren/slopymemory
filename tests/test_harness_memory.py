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


def test_the_temp_file_is_created_at_the_target_mode_not_chmodded_after(home, monkeypatch):
    """The mode must be passed to the call that CREATES the temp file, not applied with a separate chmod
    once it already holds content — otherwise the content sits at the umask's mode for however long the
    two calls are apart."""
    settings(home).write_text('{"theme": "dark"}')
    settings(home).chmod(0o600)

    seen = []
    real_open = hm.os.open

    def spy_open(path, flags, mode=0o777):
        seen.append(mode)
        return real_open(path, flags, mode)

    monkeypatch.setattr(hm.os, "open", spy_open)
    monkeypatch.setattr(hm.os, "chmod", lambda *a, **k: pytest.fail("mode must not be set with a separate chmod"))
    hm.turn_off("claude-code")
    assert seen and seen[-1] == 0o600


def test_a_failed_restore_write_removes_the_temp_file_and_leaves_the_target_untouched(home, monkeypatch):
    settings(home).write_text('{"theme": "dark"}')
    settings(home).chmod(0o600)
    hm.turn_off("claude-code")
    before = settings(home).read_text()

    def boom(tmp, target):
        raise OSError("disk full")
    monkeypatch.setattr(hm, "_replace", boom)
    with pytest.raises(hm.HarnessMemoryError, match="SETUP.md#harness-memory") as exc:
        hm.turn_on("claude-code", choose=lambda p, c: pytest.fail("no conflict expected"))
    assert "not changed" in str(exc.value)
    assert settings(home).read_text() == before                     # a failed write must never leave a half write
    tmp = settings(home).parent / (settings(home).name + ".tmp")
    assert not tmp.exists()                                          # nothing stray left behind either


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
    codex.calls.clear()                                               # only count what turn_on itself does
    asked = []
    hm.turn_on("codex", choose=lambda p, c: asked.append((p, c)) or "keep")
    assert asked == [("true", "true")]                                # JSON spellings, not Python's True/True
    assert codex.enabled is True
    assert codex.calls == [["codex", "features", "list"]]             # "keep" ran no enable/disable command


def test_codex_off_reapplies_when_it_was_turned_back_on_since_the_record_was_made(codex):
    hm.turn_off("codex")
    codex.enabled = True                                              # switched back on while off
    out = hm.turn_off("codex")
    assert codex.enabled is False and "again" in out
    # the restore target is still the ORIGINAL prior value (True), not the intervening flip
    hm.turn_on("codex", choose=lambda p, c: pytest.fail("no conflict expected"))
    assert codex.enabled is True


def test_codex_off_twice_when_unchanged_reports_already_off(codex):
    hm.turn_off("codex")
    out = hm.turn_off("codex")
    assert "already off by slopymemory (unchanged)" in out
    hm.turn_on("codex", choose=lambda p, c: pytest.fail("no conflict expected"))
    assert codex.enabled is True


def test_codex_off_refuses_when_codex_is_not_on_path(home, monkeypatch):
    """The autouse fixture leaves `_codex_available` False by default — this must be a real
    guarantee for turn_off/turn_on themselves, not just for status(): no test (and no future CLI
    caller) can reach a real `subprocess.run(["codex", ...])` without opting codex in first."""
    import subprocess
    calls = []
    def spy(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(hm.subprocess, "run", spy)
    with pytest.raises(hm.HarnessMemoryError, match="SETUP.md#harness-memory") as exc:
        hm.turn_off("codex")
    assert calls == []                                                # never even tried to shell out
    assert "PATH" in str(exc.value)


def test_codex_on_refuses_when_codex_is_not_on_path(home, monkeypatch):
    import json as _json
    import subprocess
    calls = []
    def spy(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(hm.subprocess, "run", spy)
    hm.STATE_FILE().parent.mkdir(parents=True, exist_ok=True)
    hm.STATE_FILE().write_text(_json.dumps({"codex": {"prior": True, "wrote": False}}))
    with pytest.raises(hm.HarnessMemoryError, match="SETUP.md#harness-memory") as exc:
        hm.turn_on("codex", choose=lambda p, c: pytest.fail("no conflict expected"))
    assert calls == []
    assert "PATH" in str(exc.value)


def test_codex_on_raises_and_keeps_the_record_when_the_restore_command_fails(home, monkeypatch):
    import subprocess
    monkeypatch.setattr(hm, "_codex_available", lambda: True)
    state = {"enabled": True, "enable_calls": 0}

    def fake(cmd, **kw):
        if cmd[1:3] == ["features", "list"]:
            line = f"memories  stable  {'true' if state['enabled'] else 'false'}\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=line, stderr="")
        if cmd[1:3] == ["features", "disable"]:
            state["enabled"] = False
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[1:3] == ["features", "enable"]:
            state["enable_calls"] += 1
            if state["enable_calls"] == 1:
                raise subprocess.CalledProcessError(1, cmd, stderr="nope")
            state["enabled"] = True
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(hm.subprocess, "run", fake)

    hm.turn_off("codex")                                              # state["enabled"] now False, prior True recorded
    with pytest.raises(hm.HarnessMemoryError, match="SETUP.md#harness-memory"):
        hm.turn_on("codex", choose=lambda p, c: pytest.fail("no conflict expected"))

    recs = json.loads(hm.STATE_FILE().read_text())
    assert recs["codex"]["prior"] is True                             # the restore target survived the failure

    hm.turn_on("codex", choose=lambda p, c: pytest.fail("no conflict expected"))   # retry once it works
    assert state["enabled"] is True
    assert not hm.STATE_FILE().exists()                                # the last record cleared: no file left behind


def test_codex_other_os_error_is_reported(home, monkeypatch):
    monkeypatch.setattr(hm, "_codex_available", lambda: True)
    def boom(cmd, **kw): raise PermissionError("not executable")
    monkeypatch.setattr(hm.subprocess, "run", boom)
    with pytest.raises(hm.HarnessMemoryError, match="SETUP.md#harness-memory"):
        hm.turn_off("codex")
    assert hm.status()["codex"].startswith("unknown (")


def test_codex_command_timeout_is_reported(home, monkeypatch):
    import subprocess
    monkeypatch.setattr(hm, "_codex_available", lambda: True)
    seen_kwargs = []
    def hang(cmd, **kw):
        seen_kwargs.append(kw)
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout") or 30)
    monkeypatch.setattr(hm.subprocess, "run", hang)
    with pytest.raises(hm.HarnessMemoryError, match="SETUP.md#harness-memory"):
        hm.turn_off("codex")
    assert seen_kwargs and seen_kwargs[0].get("timeout")              # a timeout was actually passed
    assert hm.status()["codex"].startswith("unknown (")


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


def test_codex_calls_pass_stdin_devnull_so_a_prompting_build_never_stalls(home, monkeypatch):
    import subprocess
    monkeypatch.setattr(hm, "_codex_available", lambda: True)
    seen_kwargs = []

    def spy(cmd, **kw):
        seen_kwargs.append(kw)
        return subprocess.CompletedProcess(cmd, 0, stdout="memories  stable  true\n", stderr="")

    monkeypatch.setattr(hm.subprocess, "run", spy)
    hm.status()
    assert seen_kwargs and seen_kwargs[0].get("stdin") is subprocess.DEVNULL


# --- the last record cleared deletes the state file rather than leaving `{}` behind -------------------------------

def test_on_deletes_the_state_file_when_the_last_record_is_cleared(home):
    settings(home).write_text('{"autoMemoryEnabled": true}')
    hm.turn_off("claude-code")
    assert hm.STATE_FILE().exists()
    hm.turn_on("claude-code", choose=lambda p, c: pytest.fail("no conflict expected"))
    assert not hm.STATE_FILE().exists()                               # not left behind as an empty `{}`


def test_codex_on_deletes_the_state_file_when_the_last_record_is_cleared(codex):
    hm.turn_off("codex")
    assert hm.STATE_FILE().exists()
    hm.turn_on("codex", choose=lambda p, c: pytest.fail("no conflict expected"))
    assert not hm.STATE_FILE().exists()


# --- an already-false Claude Code setting: recorded without a rewrite, Codex's wording -----------------------

def test_off_on_an_already_false_setting_does_not_rewrite_the_file_and_matches_codexs_wording(home):
    original = '{"autoMemoryEnabled":false,"theme":"dark"}'
    settings(home).write_text(original)
    out = hm.turn_off("claude-code")
    assert "already off" in out and "recorded, nothing changed" in out    # Codex's own wording for this case
    assert settings(home).read_text() == original                        # off never rewrote/reformatted it
    assert hm.status()["claude-code"] == "off (slopymemory)"
    hm.turn_on("claude-code", choose=lambda p, c: pytest.fail("no conflict expected"))
    assert settings(home).read_text() == original                        # bytes unchanged through the whole round trip


# --- the record write that clears state AFTER a successful restore can itself fail — that must be said, not raw --

def test_on_reports_when_the_restore_succeeds_but_the_final_record_write_fails(home, monkeypatch):
    settings(home).write_text('{"autoMemoryEnabled": true}')
    hm.turn_off("claude-code")

    def boom(d):
        raise OSError("disk full")
    monkeypatch.setattr(hm, "_save_records", boom)

    with pytest.raises(hm.HarnessMemoryError, match="SETUP.md#harness-memory") as exc:
        hm.turn_on("claude-code", choose=lambda p, c: pytest.fail("no conflict expected"))

    msg = str(exc.value)
    assert "restored" in msg and "record" in msg and "could not" in msg
    # the setting itself WAS restored even though the record write failed
    assert json.loads(settings(home).read_text()) == {"autoMemoryEnabled": True}


def test_codex_on_reports_when_the_restore_succeeds_but_the_final_record_write_fails(codex, monkeypatch):
    hm.turn_off("codex")

    def boom(d):
        raise OSError("disk full")
    monkeypatch.setattr(hm, "_save_records", boom)

    with pytest.raises(hm.HarnessMemoryError, match="SETUP.md#harness-memory") as exc:
        hm.turn_on("codex", choose=lambda p, c: pytest.fail("no conflict expected"))

    msg = str(exc.value)
    assert "restored" in msg and "record" in msg and "could not" in msg
    assert codex.enabled is True                                          # codex ITSELF was restored


# --- has_record(): whether slopymemory holds a record, independent of whether current state can be read ----------

def test_has_record_true_only_while_a_record_exists(home):
    assert hm.has_record("claude-code") is False
    hm.turn_off("claude-code")
    assert hm.has_record("claude-code") is True
    hm.turn_on("claude-code", choose=lambda p, c: pytest.fail("no conflict expected"))
    assert hm.has_record("claude-code") is False
