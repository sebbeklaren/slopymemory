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
    hm.turn_on("claude-code", choose=lambda p, c: "restore")
    assert settings(home).read_text() == original               # not a fresh file holding only our key


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
    real_write_atomic = hm.paths.write_atomic
    seen_record_first = []

    def spy(path, text):
        if path == settings(home):
            # by the time the settings write is attempted, the record must already be on disk
            recorded = hm.STATE_FILE().exists() and "claude-code" in json.loads(hm.STATE_FILE().read_text())
            seen_record_first.append(recorded)
            raise OSError("disk full")
        return real_write_atomic(path, text)

    monkeypatch.setattr(hm.paths, "write_atomic", spy)
    with pytest.raises(hm.HarnessMemoryError, match="SETUP.md#harness-memory"):
        hm.turn_off("claude-code")

    assert seen_record_first == [True]                              # record-first ordering, proven
    assert not hm.STATE_FILE().exists() or "claude-code" not in json.loads(hm.STATE_FILE().read_text())
    assert not settings(home).exists()                              # the settings file itself was never touched


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
