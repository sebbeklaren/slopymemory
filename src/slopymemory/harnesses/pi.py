import json
from pathlib import Path
from . import Harness, entry_named


def _cfg() -> Path:
    return Path.home() / ".pi" / "agent" / "mcp.json"


def _registered_as(launcher: str) -> str | None:
    return entry_named(_cfg(), json.loads, "mcpServers", launcher)


def _registered(launcher: str) -> bool:
    return _registered_as(launcher) is not None


HARNESS = Harness(
    id="pi", name="pi (pi-mcp-adapter)",
    detect=lambda: (Path.home() / ".pi").exists(),
    registered=_registered,
    registered_as=_registered_as,
    register_cmd=None,   # UNTESTED on this machine: the adapter's JSON is edited by hand until a pi install verifies it
    config_hint="~/.pi/agent/mcp.json → mcpServers.slopymemory = {\"command\":\"<launcher>\"} (pi-mcp-adapter); the pi-mcp extension is HTTP-only",
    instructions_file=None,
)
