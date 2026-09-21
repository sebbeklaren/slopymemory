import shutil, tomllib
from pathlib import Path
from . import Harness, entry_named


def _cfg() -> Path:
    return Path.home() / ".codex" / "config.toml"


def _registered_as(launcher: str) -> str | None:
    return entry_named(_cfg(), tomllib.loads, "mcp_servers", launcher)


def _registered(launcher: str) -> bool:
    return _registered_as(launcher) is not None


HARNESS = Harness(
    id="codex", name="Codex",
    detect=lambda: shutil.which("codex") is not None,
    registered=_registered,
    register_cmd=lambda launcher: ["codex", "mcp", "add", "memory", "--", launcher],
    registered_as=_registered_as,
    unregister_cmd=lambda name: ["codex", "mcp", "remove", name],
    config_hint="~/.codex/config.toml → [mcp_servers.memory] command = \"<launcher>\" (shared by the CLI, the IDE extension and ChatGPT Desktop's Codex mode)",
    instructions_file=lambda: Path.home() / ".codex" / "AGENTS.md",
)
