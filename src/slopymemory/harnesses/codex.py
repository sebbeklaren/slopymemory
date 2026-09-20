import shutil, tomllib
from pathlib import Path
from . import Harness, read_config


def _cfg() -> Path:
    return Path.home() / ".codex" / "config.toml"


def _registered(launcher: str) -> bool:
    d = read_config(_cfg(), tomllib.loads)
    if d is None:
        return False
    servers = d.get("mcp_servers", {})
    if not isinstance(servers, dict):
        return False
    return any(v.get("command") == launcher for v in servers.values())


HARNESS = Harness(
    id="codex", name="Codex",
    detect=lambda: shutil.which("codex") is not None,
    registered=_registered,
    register_cmd=lambda launcher: ["codex", "mcp", "add", "memory", "--", launcher],
    config_hint="~/.codex/config.toml → [mcp_servers.memory] command = \"<launcher>\" (shared by the CLI, the IDE extension and ChatGPT Desktop's Codex mode)",
    instructions_file=lambda: Path.home() / ".codex" / "AGENTS.md",
)
