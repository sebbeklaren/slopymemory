import json
from pathlib import Path
from . import Harness


def _cfg() -> Path:
    return Path.home() / ".pi" / "agent" / "mcp.json"


def _registered(launcher: str) -> bool:
    if not _cfg().exists():
        return False
    d = json.loads(_cfg().read_text())
    return any(v.get("command") == launcher for v in d.get("mcpServers", {}).values())


HARNESS = Harness(
    id="pi", name="pi (pi-mcp-adapter)",
    detect=lambda: (Path.home() / ".pi").exists(),
    registered=_registered,
    register_cmd=None,   # UNTESTED on this machine: the adapter's JSON is edited by hand until a pi install verifies it
    config_hint="~/.pi/agent/mcp.json → mcpServers.memory = {\"command\":\"<launcher>\"} (pi-mcp-adapter); the pi-mcp extension is HTTP-only",
    instructions_file=None,
)
