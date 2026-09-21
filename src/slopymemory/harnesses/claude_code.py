import json, shutil
from pathlib import Path
from . import Harness, servers_table


def _cfg() -> Path:
    return Path.home() / ".claude.json"


def _registered(launcher: str) -> bool:
    return any(v.get("command") == launcher for v in servers_table(_cfg(), json.loads, "mcpServers").values())


HARNESS = Harness(
    id="claude-code", name="Claude Code",
    detect=lambda: shutil.which("claude") is not None,
    registered=_registered,
    register_cmd=lambda launcher: ["claude", "mcp", "add", "--scope", "user", "--transport", "stdio", "memory", "--", launcher],
    unregister_cmd=lambda: ["claude", "mcp", "remove", "--scope", "user", "memory"],
    config_hint="~/.claude.json → top-level mcpServers.memory = {\"type\":\"stdio\",\"command\":\"<launcher>\"}",
    instructions_file=lambda: Path.home() / ".claude" / "CLAUDE.md",
)
