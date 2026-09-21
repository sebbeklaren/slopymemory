import subprocess
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
    assert OLDER_LINE.startswith(harnesses.OFFER_LINE_PREFIX) and OLDER_LINE != harnesses.OFFER_LINE


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
