from pathlib import Path
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
