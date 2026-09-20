import json
from pathlib import Path
from . import Harness, read_config


def _cfg() -> Path:
    return Path.home() / ".pi" / "agent" / "mcp.json"


def _registered(launcher: str) -> bool:
    d = read_config(_cfg(), json.loads)
    if d is None:
        return False
    servers = d.get("mcpServers", {})
    if not isinstance(servers, dict):
        return False
    return any(v.get("command") == launcher for v in servers.values())


HARNESS = Harness(
    id="pi", name="pi (pi-mcp-adapter)",
    detect=lambda: (Path.home() / ".pi").exists(),
    registered=_registered,
    register_cmd=None,   # UNTESTED on this machine: the adapter's JSON is edited by hand until a pi install verifies it
    config_hint="~/.pi/agent/mcp.json → mcpServers.memory = {\"command\":\"<launcher>\"} (pi-mcp-adapter); the pi-mcp extension is HTTP-only",
    instructions_file=None,
)
