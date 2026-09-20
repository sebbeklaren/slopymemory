import shutil, tomllib
from pathlib import Path
from . import Harness, servers_table


def _cfg() -> Path:
    return Path.home() / ".codex" / "config.toml"


def _registered(launcher: str) -> bool:
    return any(v.get("command") == launcher for v in servers_table(_cfg(), tomllib.loads, "mcp_servers").values())


HARNESS = Harness(
    id="codex", name="Codex",
    detect=lambda: shutil.which("codex") is not None,
    registered=_registered,
    register_cmd=lambda launcher: ["codex", "mcp", "add", "memory", "--", launcher],
    config_hint="~/.codex/config.toml → [mcp_servers.memory] command = \"<launcher>\" (shared by the CLI, the IDE extension and ChatGPT Desktop's Codex mode)",
    instructions_file=lambda: Path.home() / ".codex" / "AGENTS.md",
)
