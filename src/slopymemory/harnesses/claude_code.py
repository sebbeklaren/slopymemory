import json, shutil
from pathlib import Path
from . import Harness, read_config


def _cfg() -> Path:
    return Path.home() / ".claude.json"


def _registered(launcher: str) -> bool:
    d = read_config(_cfg(), json.loads)
    if d is None:
        return False
    servers = d.get("mcpServers", {})
    if not isinstance(servers, dict):
        return False
    return any(v.get("command") == launcher for v in servers.values())


HARNESS = Harness(
    id="claude-code", name="Claude Code",
    detect=lambda: shutil.which("claude") is not None,
    registered=_registered,
    register_cmd=lambda launcher: ["claude", "mcp", "add", "--scope", "user", "--transport", "stdio", "memory", "--", launcher],
    config_hint="~/.claude.json → top-level mcpServers.memory = {\"type\":\"stdio\",\"command\":\"<launcher>\"}",
    instructions_file=lambda: Path.home() / ".claude" / "CLAUDE.md",
)
