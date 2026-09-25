import subprocess
import pytest
from slopymemory import harnesses
from slopymemory.harnesses import claude_code, codex


def test_known_table_has_the_three_and_a_generic_hint():
    assert [h.id for h in harnesses.KNOWN] == ["claude-code", "codex", "pi"]
    for h in harnesses.KNOWN:
        assert h.config_hint and h.offer_line


def test_claude_code_register_command_is_user_scope_stdio():
    cmd = claude_code.HARNESS.register_cmd("/x/slopymem-mcp")
    assert cmd[:4] == ["claude", "mcp", "add", "--scope"] and "user" in cmd and cmd[-1] == "/x/slopymem-mcp"


def test_codex_register_command():
    cmd = codex.HARNESS.register_cmd("/x/slopymem-mcp")
    assert cmd[:3] == ["codex", "mcp", "add"] and cmd[-1] == "/x/slopymem-mcp"


def test_registered_reads_the_config_files(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude.json").write_text('{"mcpServers": {"memory": {"command": "/x/slopymem-mcp"}}}')
    assert claude_code.HARNESS.registered("/x/slopymem-mcp") is True
    assert claude_code.HARNESS.registered("/other") is False
    (tmp_path / ".codex").mkdir(); (tmp_path / ".codex" / "config.toml").write_text('[mcp_servers.memory]\ncommand = "/x/slopymem-mcp"\n')
    assert codex.HARNESS.registered("/x/slopymem-mcp") is True


def test_malformed_config_files_return_false(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    # Malformed JSON in claude config
    (tmp_path / ".claude.json").write_text("{not json")
    assert claude_code.HARNESS.registered("/x/slopymem-mcp") is False
    captured = capsys.readouterr()
    assert "unreadable" in captured.err and "SETUP.md#harnesses" in captured.err
    # Non-dict mcp_servers in codex config
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text('mcp_servers = "not a dict"')
    assert codex.HARNESS.registered("/x/slopymem-mcp") is False


def test_register_catches_subprocess_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    # Mock subprocess.run to raise CalledProcessError
    def mock_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, "claude")
    monkeypatch.setattr(harnesses.subprocess, "run", mock_run)
    # Mock detect and registered to return True/False as needed
    harness = harnesses.KNOWN[0]  # claude-code
    monkeypatch.setattr(harness, "detect", lambda: True)
    monkeypatch.setattr(harness, "registered", lambda x: False)
    # Call register with yes=True to skip prompts
    rc = harnesses.register("claude-code", yes=True)
    assert rc == 1
    captured = capsys.readouterr()
    assert "registration failed" in captured.err and "SETUP.md#harnesses" in captured.err


def test_offer_line_not_appended_twice(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    # Create a claude config that shows as registered
    (tmp_path / ".claude.json").write_text('{"mcpServers": {"memory": {"command": "/x/slopymem-mcp"}}}')
    # Create instructions file that already contains the offer line
    (tmp_path / ".claude").mkdir()
    instructions = tmp_path / ".claude" / "CLAUDE.md"
    instructions.write_text(f"# Existing content\n\n# slopymemory\n{harnesses.OFFER_LINE}\n")
    # Mock subprocess.run to succeed
    def mock_run(cmd, **kwargs):
        pass
    monkeypatch.setattr(harnesses.subprocess, "run", mock_run)
    # Mock detect and registered to make registration flow proceed
    harness = harnesses.KNOWN[0]  # claude-code
    monkeypatch.setattr(harness, "detect", lambda: True)
    monkeypatch.setattr(harness, "registered", lambda x: False)
    # Call register with yes=True
    rc = harnesses.register("claude-code", yes=True)
    assert rc == 0
    # Verify file wasn't modified
    content = instructions.read_text()
    assert content.count(harnesses.OFFER_LINE) == 1
    captured = capsys.readouterr()
    assert "offer line already present" in captured.out


def test_registered_reads_a_non_dict_config_or_entry_as_not_registered_with_a_note(tmp_path, monkeypatch, capsys):
    """A config whose top level is not an object, or whose server entry is not one, is 'not registered' and
    a note on stderr — never a raise for run_all to turn into 'check crashed'."""
    from slopymemory.harnesses import pi
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude.json").write_text("[1, 2, 3]")
    assert claude_code.HARNESS.registered("/x/slopymem-mcp") is False
    assert "SETUP.md#harnesses" in capsys.readouterr().err
    (tmp_path / ".claude.json").write_text('{"mcpServers": {"memory": "not an object", "other": {"command": "/x/slopymem-mcp"}}}')
    assert claude_code.HARNESS.registered("/x/slopymem-mcp") is True      # the bad entry is skipped, the good one read
    assert "memory" in capsys.readouterr().err
    (tmp_path / ".codex").mkdir(); (tmp_path / ".codex" / "config.toml").write_text('[mcp_servers]\nmemory = "nope"\n')
    assert codex.HARNESS.registered("/x/slopymem-mcp") is False
    (tmp_path / ".pi" / "agent").mkdir(parents=True); (tmp_path / ".pi" / "agent" / "mcp.json").write_text('"just a string"')
    assert pi.HARNESS.registered("/x/slopymem-mcp") is False


# --- the offer line's second layer: the line is updated in place, never appended twice ---

def _registration_flow(tmp_path, monkeypatch, registered: bool):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude.json").write_text('{"mcpServers": {"memory": {"command": "/x/slopymem-mcp"}}}')
    (tmp_path / ".claude").mkdir(exist_ok=True)
    monkeypatch.setattr(harnesses.subprocess, "run", lambda cmd, **kw: None)
    harness = harnesses.KNOWN[0]  # claude-code
    monkeypatch.setattr(harness, "detect", lambda: True)
    monkeypatch.setattr(harness, "registered", lambda x: registered)
    return tmp_path / ".claude" / "CLAUDE.md"


OLDER_LINE = ("If the memory tools show only `memory_init`, tell the user this project has no memory yet "
              "and offer to set it up.")


def test_offer_line_carries_the_second_layer_and_a_stable_prefix():
    assert harnesses.OFFER_LINE.startswith(harnesses.OFFER_LINE_PREFIX)
    assert "Never save passwords, keys or tokens" in harnesses.OFFER_LINE
    assert any(OLDER_LINE.startswith(p) for p in harnesses.LEGACY_PREFIXES)
    assert harnesses.OFFER_LINE.startswith(harnesses.TRIGGER_PREFIX) and OLDER_LINE != harnesses.OFFER_LINE


def test_offer_line_replaces_an_older_version_in_place(tmp_path, monkeypatch, capsys):
    f = _registration_flow(tmp_path, monkeypatch, registered=False)
    f.write_text(f"# Mine\n\nkeep this\n\n# slopymemory\n{OLDER_LINE}\n\n# after\nand this\n")
    assert harnesses.register("claude-code", yes=True) == 0
    content = f.read_text()
    assert f"\n{OLDER_LINE}\n" not in content and content.count(harnesses.OFFER_LINE) == 1   # the old line is a prefix of the new
    assert content == f"# Mine\n\nkeep this\n\n# slopymemory\n{harnesses.OFFER_LINE}\n\n# after\nand this\n"
    assert "offer line updated" in capsys.readouterr().out


def test_offer_line_is_appended_once_when_absent(tmp_path, monkeypatch):
    f = _registration_flow(tmp_path, monkeypatch, registered=False)
    f.write_text("# Mine\n")
    assert harnesses.register("claude-code", yes=True) == 0
    assert f.read_text() == f"# Mine\n\n# slopymemory\n{harnesses.OFFER_LINE}\n"
    assert harnesses.register("claude-code", yes=True) == 0           # content-idempotent
    assert f.read_text().count(harnesses.OFFER_LINE) == 1


def test_register_on_an_already_registered_harness_still_updates_a_stale_offer_line(tmp_path, monkeypatch, capsys):
    """A re-run of `slopymem register` is how an existing machine gets the new line: the harness is already
    registered, so the mcp add is skipped, but a stale or missing offer line is still offered (and asked)."""
    f = _registration_flow(tmp_path, monkeypatch, registered=True)
    f.write_text(f"# slopymemory\n{OLDER_LINE}\n")
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("n\n"))
    assert harnesses.register("claude-code", yes=False) == 1            # declined: nothing changes, said so
    assert f.read_text() == f"# slopymemory\n{OLDER_LINE}\n"
    assert harnesses.register("claude-code", yes=True) == 0
    assert f.read_text() == f"# slopymemory\n{harnesses.OFFER_LINE}\n"
    out = capsys.readouterr().out
    assert "already registered" in out and "offer line updated" in out
    before = f.read_text()
    assert harnesses.register("claude-code", yes=True) == 0            # current line: byte-identical, no prompt
    assert f.read_text() == before


def test_ensure_offer_line_states(tmp_path):
    f = tmp_path / "AGENTS.md"
    assert harnesses.offer_line_state(f, "x line") == "absent"
    assert harnesses.ensure_offer_line(f, harnesses.OFFER_LINE) == "appended"
    assert harnesses.offer_line_state(f, harnesses.OFFER_LINE) == "present"
    f.write_text(f"{OLDER_LINE}\n")
    assert harnesses.offer_line_state(f, harnesses.OFFER_LINE) == "stale"
    assert harnesses.ensure_offer_line(f, harnesses.OFFER_LINE) == "replaced"
    assert f.read_text() == f"{harnesses.OFFER_LINE}\n"
    assert harnesses.ensure_offer_line(f, harnesses.OFFER_LINE) == "present"


def test_register_reports_an_unreadable_instructions_file_instead_of_a_traceback(tmp_path, monkeypatch, capsys):
    f = _registration_flow(tmp_path, monkeypatch, registered=True)
    f.mkdir()                                                   # a directory where the file should be
    assert harnesses.register("claude-code", yes=True) == 1
    err = capsys.readouterr().err
    assert "offer line" in err and str(f) in err and "SETUP.md#harnesses" in err


def test_ensure_offer_line_keeps_the_exact_mode_through_a_stale_tmp(tmp_path):
    f = tmp_path / "CLAUDE.md"
    f.write_text(f"# mine\n{OLDER_LINE}\n")
    f.chmod(0o600)
    tmp = tmp_path / "CLAUDE.md.tmp"
    tmp.write_text("leftover from an earlier crash")
    tmp.chmod(0o644)

    assert harnesses.ensure_offer_line(f, harnesses.OFFER_LINE) == "replaced"
    assert (f.stat().st_mode & 0o777) == 0o600
    assert not tmp.exists()
    assert f.read_text() == f"# mine\n{harnesses.OFFER_LINE}\n"


def test_ensure_offer_line_keeps_the_exact_mode_through_a_symlink(tmp_path):
    target = tmp_path / "dotfiles" / "claude.md"; target.parent.mkdir()
    target.write_text(f"# mine\n{OLDER_LINE}\n")
    target.chmod(0o600)
    f = tmp_path / "CLAUDE.md"; f.symlink_to(target)

    assert harnesses.ensure_offer_line(f, harnesses.OFFER_LINE) == "replaced"
    assert f.is_symlink()
    assert (target.stat().st_mode & 0o777) == 0o600
    assert target.read_text() == f"# mine\n{harnesses.OFFER_LINE}\n"


def test_remove_offer_line_keeps_the_exact_mode(tmp_path):
    f = tmp_path / "AGENTS.md"
    f.write_text(f"keep\n\n# slopymemory\n{OLDER_LINE}\n")
    f.chmod(0o600)

    assert harnesses.remove_offer_line(f) is True
    assert (f.stat().st_mode & 0o777) == 0o600
    assert f.read_text() == "keep\n"


def test_ensure_offer_line_failed_write_leaves_no_tmp_and_the_file_unchanged(tmp_path, monkeypatch):
    f = tmp_path / "CLAUDE.md"
    f.write_text(f"# mine\n{OLDER_LINE}\n")
    f.chmod(0o600)
    before = f.read_text()

    def boom(tmp, target):
        raise OSError("disk full")

    monkeypatch.setattr(harnesses.paths.os, "replace", boom)
    with pytest.raises(OSError):
        harnesses.ensure_offer_line(f, harnesses.OFFER_LINE)
    assert f.read_text() == before
    assert (f.stat().st_mode & 0o777) == 0o600
    assert not (tmp_path / "CLAUDE.md.tmp").exists()


def test_remove_offer_line_failed_write_leaves_no_tmp_and_the_file_unchanged(tmp_path, monkeypatch):
    f = tmp_path / "AGENTS.md"
    f.write_text(f"keep\n\n# slopymemory\n{OLDER_LINE}\n")
    f.chmod(0o600)
    before = f.read_text()

    def boom(tmp, target):
        raise OSError("disk full")

    monkeypatch.setattr(harnesses.paths.os, "replace", boom)
    with pytest.raises(OSError):
        harnesses.remove_offer_line(f)
    assert f.read_text() == before
    assert (f.stat().st_mode & 0o777) == 0o600
    assert not (tmp_path / "AGENTS.md.tmp").exists()


def test_a_stale_line_in_a_symlinked_instructions_file_is_replaced_through_the_link(tmp_path, monkeypatch):
    """Dotfile setups keep ~/.claude/CLAUDE.md as a symlink into a repo: the replacement must land in the TARGET and
    leave the link (and the target's mode) as they were — an atomic replace of the link path would sever it."""
    import os
    f = _registration_flow(tmp_path, monkeypatch, registered=True)
    target = tmp_path / "dotfiles" / "claude.md"; target.parent.mkdir()
    target.write_text(f"# mine\n{OLDER_LINE}\n"); target.chmod(0o600)
    f.symlink_to(target)
    assert harnesses.register("claude-code", yes=True) == 0
    assert f.is_symlink() and os.readlink(f) == str(target)                 # the link survives
    assert target.read_text() == f"# mine\n{harnesses.OFFER_LINE}\n"        # the target carries the new line
    assert (target.stat().st_mode & 0o777) == 0o600                          # and its mode


def test_fresh_registration_propagates_an_unwritable_instructions_file_as_a_failure(tmp_path, monkeypatch, capsys):
    f = _registration_flow(tmp_path, monkeypatch, registered=False)
    f.mkdir()                                                   # a directory where the file should be
    assert harnesses.register("claude-code", yes=True) == 1
    err = capsys.readouterr().err
    assert "offer line" in err and str(f) in err and "SETUP.md#harnesses" in err


# --- unregister removes OUR entry by its command, never a foreign entry named `memory` ---

def _configs(tmp_path, monkeypatch, claude: str, codex: str, pi_json: str):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude.json").write_text(claude)
    (tmp_path / ".codex").mkdir(); (tmp_path / ".codex" / "config.toml").write_text(codex)
    (tmp_path / ".pi" / "agent").mkdir(parents=True); (tmp_path / ".pi" / "agent" / "mcp.json").write_text(pi_json)


def test_registered_as_names_our_entry_whatever_it_is_called(tmp_path, monkeypatch):
    from slopymemory.harnesses import pi
    _configs(tmp_path, monkeypatch,
             claude='{"mcpServers": {"memory": {"command": "/someone/elses/server-memory"}, "mem2": {"command": "/x/slopymem-mcp"}}}',
             codex='[mcp_servers.memory]\ncommand = "/someone/elses"\n[mcp_servers.ours]\ncommand = "/x/slopymem-mcp"\n',
             pi_json='{"mcpServers": {"memory": {"command": "/x/slopymem-mcp"}}}')
    assert claude_code.HARNESS.registered_as("/x/slopymem-mcp") == "mem2" and claude_code.HARNESS.registered("/x/slopymem-mcp")
    assert claude_code.HARNESS.registered_as("/other") is None and not claude_code.HARNESS.registered("/other")
    assert codex.HARNESS.registered_as("/x/slopymem-mcp") == "ours"
    assert pi.HARNESS.registered_as("/x/slopymem-mcp") == "memory" and pi.HARNESS.unregister_cmd is None


def test_unregister_removes_the_entry_whose_command_is_ours_and_leaves_a_foreign_memory_alone(tmp_path, monkeypatch):
    _configs(tmp_path, monkeypatch,
             claude='{"mcpServers": {"memory": {"command": "/someone/elses/server-memory"}, "mem2": {"command": "/x/slopymem-mcp"}}}',
             codex='[mcp_servers.memory]\ncommand = "/someone/elses"\n[mcp_servers.ours]\ncommand = "/x/slopymem-mcp"\n',
             pi_json='{}')
    ran = []
    monkeypatch.setattr(harnesses.subprocess, "run", lambda cmd, **kw: ran.append(list(cmd)))
    assert harnesses.unregister(claude_code.HARNESS, "/x/slopymem-mcp") == 0
    assert harnesses.unregister(codex.HARNESS, "/x/slopymem-mcp") == 0
    assert ran == [["claude", "mcp", "remove", "--scope", "user", "mem2"], ["codex", "mcp", "remove", "ours"]]
    # only a foreign `memory` entry: nothing is ours, nothing runs
    (tmp_path / ".claude.json").write_text('{"mcpServers": {"memory": {"command": "/someone/elses/server-memory"}}}')
    (tmp_path / ".codex" / "config.toml").write_text('[mcp_servers.memory]\ncommand = "/someone/elses"\n')
    ran.clear()
    assert harnesses.unregister(claude_code.HARNESS, "/x/slopymem-mcp") == 0
    assert harnesses.unregister(codex.HARNESS, "/x/slopymem-mcp") == 0
    assert ran == []


def test_unregister_without_a_command_says_how_by_hand_and_fails(tmp_path, monkeypatch, capsys):
    from slopymemory.harnesses import pi
    _configs(tmp_path, monkeypatch, claude="{}", codex="", pi_json='{"mcpServers": {"memory": {"command": "/x/slopymem-mcp"}}}')
    assert harnesses.unregister(pi.HARNESS, "/x/slopymem-mcp") == 1
    err = capsys.readouterr().err
    assert "by hand" in err and "SETUP.md#harnesses" in err


# --- the registered name is `slopymemory`, never the generic `memory` other servers use ---

def test_register_commands_use_the_distinct_name():
    assert harnesses.SERVER_NAME == "slopymemory"
    for cmd in (claude_code.HARNESS.register_cmd("/x/slopymem-mcp"), codex.HARNESS.register_cmd("/x/slopymem-mcp")):
        assert "slopymemory" in cmd and "memory" not in cmd


def test_hints_name_the_distinct_name():
    for h in harnesses.KNOWN:
        assert "slopymemory" in h.config_hint and ".memory" not in h.config_hint


def _migration(tmp_path, monkeypatch, claude: str, fail_on=None):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude.json").write_text(claude)
    (tmp_path / ".claude").mkdir(exist_ok=True)
    (tmp_path / ".claude" / "CLAUDE.md").write_text(f"# slopymemory\n{harnesses.OFFER_LINE}\n")
    monkeypatch.setattr(harnesses, "launcher_path", lambda: "/x/slopymem-mcp")
    monkeypatch.setattr(claude_code.HARNESS, "detect", lambda: True)
    ran = []

    def run(cmd, **kw):
        ran.append(list(cmd))
        if fail_on and fail_on in cmd:
            raise subprocess.CalledProcessError(1, cmd[0])
    monkeypatch.setattr(harnesses.subprocess, "run", run)
    return ran


OURS_AS_MEMORY = '{"mcpServers": {"memory": {"command": "/x/slopymem-mcp"}}}'


def test_an_older_registration_under_memory_is_renamed_on_a_yes_add_first(tmp_path, monkeypatch, capsys):
    ran = _migration(tmp_path, monkeypatch, OURS_AS_MEMORY)
    assert harnesses.register("claude-code", yes=True) == 0
    assert ran == [claude_code.HARNESS.register_cmd("/x/slopymem-mcp"), ["claude", "mcp", "remove", "--scope", "user", "memory"]]
    out = capsys.readouterr().out
    assert "'memory'" in out and "mcp__slopymemory__" in out and "mcp__memory__" in out   # the allowlist consequence is said


def test_an_older_registration_is_left_alone_on_a_no(tmp_path, monkeypatch, capsys):
    ran = _migration(tmp_path, monkeypatch, OURS_AS_MEMORY)
    # A decline leaves the harness registered as `memory`, so the instructions file's already-current line
    # is the one naming THAT registration, not the default `slopymemory` one `_migration` seeds.
    (tmp_path / ".claude" / "CLAUDE.md").write_text(f"# slopymemory\n{harnesses.trigger_line('memory')}\n")
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("n\n"))
    assert harnesses.register("claude-code", yes=False) == 0
    assert ran == []
    assert "left" in capsys.readouterr().out


def test_a_failed_add_during_the_rename_changes_nothing_and_says_so(tmp_path, monkeypatch, capsys):
    ran = _migration(tmp_path, monkeypatch, OURS_AS_MEMORY, fail_on="add")
    assert harnesses.register("claude-code", yes=True) == 1
    assert ran == [claude_code.HARNESS.register_cmd("/x/slopymem-mcp")]         # the old entry was never removed
    err = capsys.readouterr().err
    assert "still registered as 'memory'" in err and "SETUP.md#harnesses" in err


def test_a_failed_remove_during_the_rename_says_both_names_are_registered(tmp_path, monkeypatch, capsys):
    _migration(tmp_path, monkeypatch, OURS_AS_MEMORY, fail_on="remove")
    assert harnesses.register("claude-code", yes=True) == 1
    err = capsys.readouterr().err
    assert "claude mcp remove --scope user memory" in err and "SETUP.md#harnesses" in err


def test_a_current_registration_asks_nothing(tmp_path, monkeypatch, capsys):
    ran = _migration(tmp_path, monkeypatch, '{"mcpServers": {"slopymemory": {"command": "/x/slopymem-mcp"}}}')
    assert harnesses.register("claude-code", yes=False) == 0
    assert ran == [] and "rename" not in capsys.readouterr().out


def test_a_foreign_memory_entry_is_byte_identical_after_register(tmp_path, monkeypatch):
    claude = '{"mcpServers": {"memory": {"command": "/someone/elses/server-memory"}}}'
    ran = _migration(tmp_path, monkeypatch, claude)
    assert harnesses.register("claude-code", yes=True) == 0
    assert ran == [claude_code.HARNESS.register_cmd("/x/slopymem-mcp")]         # a fresh add, no remove of anything
    assert (tmp_path / ".claude.json").read_text() == claude


def test_a_name_the_user_chose_is_never_offered_a_rename(tmp_path, monkeypatch, capsys):
    ran = _migration(tmp_path, monkeypatch, '{"mcpServers": {"mem2": {"command": "/x/slopymem-mcp"}}}')
    assert harnesses.register("claude-code", yes=True) == 0
    assert ran == [] and "rename" not in capsys.readouterr().out


# --- the trigger line: tells the agent when to use memory, naming the server as registered ---

LEGACY_LINE = ("If the memory tools show only `memory_init`, tell the user this project has no memory yet and offer "
               "to set it up. Never save passwords, keys or tokens to memory; save where they live.")


def test_trigger_line_carries_the_triggers_and_the_secret_rule():
    line = harnesses.trigger_line("slopymemory")
    assert line.startswith(harnesses.TRIGGER_PREFIX) and "`slopymemory`" in line
    for s in ("Before planning or building", "query_concepts", "weigh what comes back", "save each decision with its why",
              "Never save passwords, keys or tokens"):
        assert s in line
    assert harnesses.OFFER_LINE == line


def test_old_offer_line_is_replaced_by_the_trigger(tmp_path):
    f = tmp_path / "CLAUDE.md"
    f.write_text(f"# mine\n\n# slopymemory\n{LEGACY_LINE}\n\n# after\n")
    assert harnesses.offer_line_state(f, harnesses.OFFER_LINE) == "stale"
    assert harnesses.ensure_offer_line(f, harnesses.OFFER_LINE) == "replaced"
    assert f.read_text() == f"# mine\n\n# slopymemory\n{harnesses.OFFER_LINE}\n\n# after\n"
    assert harnesses.ensure_offer_line(f, harnesses.OFFER_LINE) == "present"     # never a second line
    assert f.read_text().count("slopymemory") == 2                                 # the heading and the one line


def test_remove_offer_line_takes_a_legacy_line_too(tmp_path):
    f = tmp_path / "AGENTS.md"
    f.write_text(f"keep\n\n# slopymemory\n{LEGACY_LINE}\n")
    assert harnesses.remove_offer_line(f) is True
    assert f.read_text() == "keep\n"                      # the existing removal keeps the final newline


def test_trigger_names_the_registered_name(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude.json").write_text('{"mcpServers": {"mem2": {"command": "/x/slopymem-mcp"}}}')
    (tmp_path / ".claude").mkdir()
    monkeypatch.setattr(harnesses, "launcher_path", lambda: "/x/slopymem-mcp")
    monkeypatch.setattr(claude_code.HARNESS, "detect", lambda: True)
    monkeypatch.setattr(harnesses.subprocess, "run", lambda cmd, **kw: None)
    assert harnesses.register("claude-code", yes=True) == 0
    assert harnesses.trigger_line("mem2") in (tmp_path / ".claude" / "CLAUDE.md").read_text()


# --- status() reports a harness-memory switch left off by slopymemory ---------------------------------------------

def test_status_reports_harness_memory_off_without_failing(tmp_path, tmp_home, monkeypatch):
    from slopymemory import harness_memory as hm
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    monkeypatch.setattr(harnesses, "detected", lambda: [])
    hm.turn_off("claude-code")
    f = harnesses.status()
    assert f.ok and "file memory is OFF" in f.detail and "projects without a store have no memory" in f.detail


def test_status_survives_an_os_error_reading_harness_memory_and_keeps_the_registration_rows(tmp_path, monkeypatch):
    """A registered harness's row must not be lost just because harness-memory's own state (a settings file
    with a permissions problem, say) raised something other than HarnessMemoryError."""
    from slopymemory import harness_memory as hm
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude.json").write_text('{"mcpServers": {"slopymemory": {"command": "/x/slopymem-mcp"}}}')
    monkeypatch.setattr(harnesses, "launcher_path", lambda: "/x/slopymem-mcp")
    monkeypatch.setattr(claude_code.HARNESS, "detect", lambda: True)
    monkeypatch.setattr(harnesses, "detected", lambda: [claude_code.HARNESS])

    def boom():
        raise OSError("permission denied")
    monkeypatch.setattr(hm, "status", boom)
    f = harnesses.status()
    assert f.ok and "Claude Code: registered" in f.detail
    assert "permission denied" in f.detail
    # the same prefix and anchor summary() already uses — never a bare str(e) pointing at the wrong section
    assert "harness memory state unreadable:" in f.detail
    assert "SETUP.md#harness-memory" in f.detail


def test_status_reports_the_changed_back_on_state_as_reported_not_failed(tmp_path, tmp_home, monkeypatch):
    """doctor is silent today on a record that outlives a user's own flip back on — summary() already makes
    it visible; status() should too, as a reported row, not a failure."""
    from slopymemory import harness_memory as hm
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude.json").write_text('{"mcpServers": {"slopymemory": {"command": "/x/slopymem-mcp"}}}')
    monkeypatch.setattr(harnesses, "launcher_path", lambda: "/x/slopymem-mcp")
    monkeypatch.setattr(claude_code.HARNESS, "detect", lambda: True)
    monkeypatch.setattr(harnesses, "detected", lambda: [claude_code.HARNESS])
    hm.turn_off("claude-code")
    (tmp_path / ".claude" / "settings.json").write_text('{"autoMemoryEnabled": true}')   # switched back on since
    f = harnesses.status()
    assert f.ok                                            # reported, not failed
    assert "changed since" in f.detail or "switched back on" in f.detail


# --- summary(): the first-run block, only lines that are true --------------------------------------------------

def test_summary_names_only_what_is_true(tmp_path, tmp_home, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude.json").write_text('{"mcpServers": {"slopymemory": {"command": "/x/slopymem-mcp"}}}')
    (tmp_path / ".claude" / "CLAUDE.md").write_text(f"# slopymemory\n{harnesses.OFFER_LINE}\n")
    monkeypatch.setattr(harnesses, "launcher_path", lambda: "/x/slopymem-mcp")
    monkeypatch.setattr(harnesses, "detected", lambda: [claude_code.HARNESS])
    lines = "\n".join(harnesses.summary())
    assert 'Registered as the MCP server "slopymemory" in: Claude Code' in lines
    assert "Codex" not in lines
    assert str(tmp_path / ".claude" / "CLAUDE.md") in lines
    assert "Your harness's own memory (Claude Code): unchanged" in lines
    assert "slopymem harness-memory off" in lines and "slopymem uninstall" in lines
    import re
    assert not re.search(r"\d[\d.,]*\s*k?\s*tokens", lines)                 # no token figure is printed


def test_summary_never_reports_a_stale_legacy_line_as_added(tmp_path, tmp_home, monkeypatch):
    """A file still carrying the LEGACY offer line (state 'stale') is not the current line — reporting it
    as 'Added one line to ... (tells the agent when to use memory)' would be false for every pre-existing
    install that runs `slopymem summary` before re-registering."""
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude.json").write_text('{"mcpServers": {"slopymemory": {"command": "/x/slopymem-mcp"}}}')
    claude_md = tmp_path / ".claude" / "CLAUDE.md"
    claude_md.write_text("If the memory tools show only an older line here\n")
    monkeypatch.setattr(harnesses, "launcher_path", lambda: "/x/slopymem-mcp")
    monkeypatch.setattr(harnesses, "detected", lambda: [claude_code.HARNESS])
    lines = harnesses.summary()
    added_lines = [l for l in lines if l.strip().startswith("Added one line to")]
    assert not any(str(claude_md) in l for l in added_lines)          # a stale line is never counted as "added"
    stale_lines = [l for l in lines if str(claude_md) in l]
    assert stale_lines and "register" in stale_lines[0] and "tells the agent when to use memory" not in stale_lines[0]


# --- the harness-memory part: one true line PER HARNESS, from its actual status() spelling --------------------
#
# A harness earns a line only if it is DETECTED, or slopymemory holds a RECORD for it (state "off
# (slopymemory)" or "on (changed since slopymemory switched it off)") — a record is true information even
# for a harness that's since been removed. hm.status() always checks claude-code whether or not Claude
# Code exists on this machine, so without this filter a clean-container install (nothing detected, no
# record) would get a line naming a harness that was never there.

def test_summary_says_nothing_about_a_harness_that_is_neither_detected_nor_on_record(tmp_path, tmp_home, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    monkeypatch.setattr(harnesses, "detected", lambda: [])
    lines = "\n".join(harnesses.summary())
    assert "Claude Code" not in lines
    assert "harness's own memory" not in lines and "harness's own file memory" not in lines
    assert "slopymem harness-memory off" not in lines


def test_summary_harness_memory_line_for_on(tmp_path, tmp_home, monkeypatch):
    """`on` (untouched): the unchanged/fallback line, named for this harness, and the off-hint (since a
    harness IS on) — shown because the harness is detected."""
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    monkeypatch.setattr(harnesses, "detected", lambda: [claude_code.HARNESS])
    lines = "\n".join(harnesses.summary())
    assert "Your harness's own memory (Claude Code): unchanged" in lines and "stays as a fallback" in lines
    assert "slopymem harness-memory off" in lines


def test_summary_harness_memory_line_for_off_by_slopymemory_shown_without_detection(tmp_path, tmp_home, monkeypatch):
    """A record is true information even when the harness is gone — shown though nothing is detected."""
    from slopymemory import harness_memory as hm
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    monkeypatch.setattr(harnesses, "detected", lambda: [])
    hm.turn_off("claude-code")
    lines = "\n".join(harnesses.summary())
    assert "file memory: OFF" in lines and "Claude Code" in lines and "slopymem harness-memory on" in lines
    assert "stays as a fallback" not in lines                    # off is never described as "unchanged"
    assert "slopymem harness-memory off" not in lines            # the off-hint: no harness here is actually "on"


def test_summary_harness_memory_line_for_changed_back_on(tmp_path, tmp_home, monkeypatch):
    from slopymemory import harness_memory as hm
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    monkeypatch.setattr(harnesses, "detected", lambda: [])
    hm.turn_off("claude-code")
    (tmp_path / ".claude" / "settings.json").write_text('{"autoMemoryEnabled": true}')   # switched back on since
    lines = "\n".join(harnesses.summary())
    assert "switched back on" in lines and "slopymem harness-memory on" in lines
    assert "unchanged" not in lines                               # not the "on" bucket — it changed since
    assert "slopymem harness-memory off" not in lines             # the off-hint: this state is not plain "on"


def test_summary_harness_memory_line_for_off_not_by_slopymemory(tmp_path, tmp_home, monkeypatch):
    """`off (not by slopymemory)` is not a slopymemory record, so it needs detection to be shown at all."""
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    monkeypatch.setattr(harnesses, "detected", lambda: [claude_code.HARNESS])
    (tmp_path / ".claude" / "settings.json").write_text('{"autoMemoryEnabled": false}')   # the user's own choice
    lines = "\n".join(harnesses.summary())
    assert "off by your own choice" in lines and "no fallback memory there" in lines
    assert "stays as a fallback" not in lines                     # an off harness must never be worded as a fallback
    assert "slopymem harness-memory off" not in lines             # no harness here is "on"


def test_summary_harness_memory_line_for_unknown_state(tmp_path, tmp_home, monkeypatch):
    """`unknown (...)` is not a slopymemory record either — Codex needs to be detected for its line to show."""
    from slopymemory import harness_memory as hm
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    monkeypatch.setattr(harnesses, "detected", lambda: [codex.HARNESS])
    monkeypatch.setattr(hm, "_codex_available", lambda: True)

    def boom(cmd, **kw):
        raise PermissionError("not executable")
    monkeypatch.setattr(hm.subprocess, "run", boom)
    lines = "\n".join(harnesses.summary())
    assert "state unknown" in lines and "not executable" in lines and "Codex" in lines


def test_summary_harness_memory_mixed_states_prints_both_true_lines(tmp_path, tmp_home, monkeypatch):
    """claude-code's line shows via its RECORD (undetected); codex's shows via DETECTION (no record)."""
    from slopymemory import harness_memory as hm
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    monkeypatch.setattr(harnesses, "detected", lambda: [codex.HARNESS])
    monkeypatch.setattr(hm, "_codex_available", lambda: True)
    monkeypatch.setattr(hm, "codex_memories_enabled", lambda: True)     # codex: untouched, "on"
    hm.turn_off("claude-code")                                          # claude-code: "off (slopymemory)"
    lines = "\n".join(harnesses.summary())
    assert "file memory: OFF" in lines and "Claude Code" in lines                       # claude-code's true line
    assert "Your harness's own memory (Codex): unchanged" in lines and "stays as a fallback" in lines  # codex's
    assert "slopymem harness-memory off" in lines                       # the hint: codex IS "on"


def test_summary_never_raises_when_harness_memory_state_is_unreadable(tmp_path, tmp_home, monkeypatch):
    from slopymemory import harness_memory as hm
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    monkeypatch.setattr(harnesses, "detected", lambda: [])

    def boom():
        raise OSError("permission denied")
    monkeypatch.setattr(hm, "status", boom)
    lines = "\n".join(harnesses.summary())
    assert "Harness memory state unreadable" in lines and "permission denied" in lines and "SETUP.md#harness-memory" in lines


def test_summary_does_not_double_the_anchor_on_a_harness_memory_error(tmp_path, tmp_home, monkeypatch):
    """`HarnessMemoryError` messages already end with their own SETUP.md anchor; the unreadable line must not
    append a second one."""
    from slopymemory import harness_memory as hm
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    monkeypatch.setattr(harnesses, "detected", lambda: [])
    hm.STATE_FILE().parent.mkdir(parents=True, exist_ok=True)
    hm.STATE_FILE().write_text("{not json")
    lines = "\n".join(harnesses.summary())
    assert lines.count("SETUP.md#harness-memory") == 1


def test_register_prints_the_summary_after_a_fresh_registration(tmp_path, monkeypatch, capsys):
    _registration_flow(tmp_path, monkeypatch, registered=False)
    assert harnesses.register("claude-code", yes=True) == 0
    out = capsys.readouterr().out
    assert "slopymemory is set up." in out


def test_register_prints_no_summary_when_nothing_changed(tmp_path, monkeypatch, capsys):
    f = _registration_flow(tmp_path, monkeypatch, registered=True)
    f.write_text(harnesses.OFFER_LINE + "\n")          # already the current line: nothing to update
    assert harnesses.register("claude-code", yes=True) == 0
    out = capsys.readouterr().out
    assert "already registered" in out
    assert "slopymemory is set up." not in out


def test_register_no_summary_flag_suppresses_the_block_even_when_changed(tmp_path, monkeypatch, capsys):
    _registration_flow(tmp_path, monkeypatch, registered=False)
    assert harnesses.register("claude-code", yes=True, print_summary=False) == 0
    out = capsys.readouterr().out
    assert "slopymemory is set up." not in out


# --- register_detected(): the block prints at most ONCE, after every harness, not once per harness ------------

def _two_fake_harnesses(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(harnesses.subprocess, "run", lambda cmd, **kw: ran.append(list(cmd)))
    a = harnesses.Harness(id="a", name="A", detect=lambda: True, registered=lambda lp: False, registered_as=lambda lp: None,
                           register_cmd=lambda lp: ["a-add", lp], unregister_cmd=lambda name: ["a-remove", name],
                           config_hint="a hint", instructions_file=lambda: tmp_path / "a.md")
    b = harnesses.Harness(id="b", name="B", detect=lambda: True, registered=lambda lp: False, registered_as=lambda lp: None,
                           register_cmd=lambda lp: ["b-add", lp], unregister_cmd=lambda name: ["b-remove", name],
                           config_hint="b hint", instructions_file=lambda: tmp_path / "b.md")
    monkeypatch.setattr(harnesses, "detected", lambda: [a, b])
    return ran


def test_register_detected_prints_the_summary_exactly_once_across_several_harnesses(tmp_path, tmp_home, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    ran = _two_fake_harnesses(tmp_path, monkeypatch)
    assert harnesses.register_detected(yes=True) == 0
    out = capsys.readouterr().out
    assert ran == [["a-add", harnesses.launcher_path()], ["b-add", harnesses.launcher_path()]]   # both really ran
    assert out.count("slopymemory is set up.") == 1
    assert "A:" in out and "B:" in out


def test_register_detected_no_summary_suppresses_the_block(tmp_path, tmp_home, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    _two_fake_harnesses(tmp_path, monkeypatch)
    assert harnesses.register_detected(yes=True, print_summary=False) == 0
    out = capsys.readouterr().out
    assert "slopymemory is set up." not in out
