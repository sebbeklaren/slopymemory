import json, shutil
from pathlib import Path
from . import Harness, entry_named


def _cfg() -> Path:
    return Path.home() / ".claude.json"


def _registered_as(launcher: str) -> str | None:
    return entry_named(_cfg(), json.loads, "mcpServers", launcher)


def _registered(launcher: str) -> bool:
    return _registered_as(launcher) is not None


HARNESS = Harness(
    id="claude-code", name="Claude Code",
    detect=lambda: shutil.which("claude") is not None,
    registered=_registered,
    register_cmd=lambda launcher: ["claude", "mcp", "add", "--scope", "user", "--transport", "stdio", "memory", "--", launcher],
    registered_as=_registered_as,
    unregister_cmd=lambda name: ["claude", "mcp", "remove", "--scope", "user", name],
    config_hint="~/.claude.json → top-level mcpServers.memory = {\"type\":\"stdio\",\"command\":\"<launcher>\"}",
    instructions_file=lambda: Path.home() / ".claude" / "CLAUDE.md",
)
