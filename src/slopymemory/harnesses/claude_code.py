import json, os, shutil
from pathlib import Path
from . import Harness, SERVER_NAME, entry_named


def config_dir() -> Path:
    """Where Claude Code keeps CLAUDE.md and settings.json: $CLAUDE_CONFIG_DIR when set, else ~/.claude."""
    d = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(d) if d else Path.home() / ".claude"        # taken literally, as Claude Code takes it (no ~ expansion)


def _cfg() -> Path:
    """The MCP table's file, where Claude Code reads it: a legacy `.config.json` in its config directory wins when it
    exists; else $CLAUDE_CONFIG_DIR/.claude.json when the variable is set, else ~/.claude.json."""
    legacy = config_dir() / ".config.json"
    if legacy.exists():
        return legacy
    d = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(d) / ".claude.json" if d else Path.home() / ".claude.json"


def _registered_as(launcher: str) -> str | None:
    return entry_named(_cfg(), json.loads, "mcpServers", launcher)


def _registered(launcher: str) -> bool:
    return _registered_as(launcher) is not None


HARNESS = Harness(
    id="claude-code", name="Claude Code",
    detect=lambda: shutil.which("claude") is not None,
    registered=_registered,
    register_cmd=lambda launcher: ["claude", "mcp", "add", "--scope", "user", "--transport", "stdio", SERVER_NAME, "--", launcher],
    registered_as=_registered_as,
    unregister_cmd=lambda name: ["claude", "mcp", "remove", "--scope", "user", name],
    config_hint="~/.claude.json ($CLAUDE_CONFIG_DIR/.claude.json when set; a legacy .config.json in the config directory wins when it exists) → top-level mcpServers.slopymemory = {\"type\":\"stdio\",\"command\":\"<launcher>\"}",
    instructions_file=lambda: config_dir() / "CLAUDE.md",
)
