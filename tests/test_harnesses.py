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
