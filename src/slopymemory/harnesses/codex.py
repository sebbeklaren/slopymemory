import os, shutil, tomllib
from pathlib import Path
from . import Harness, SERVER_NAME, entry_named


def home() -> Path:
    """Codex's home: $CODEX_HOME when set, else ~/.codex — where its config.toml and AGENTS.md live."""
    d = os.environ.get("CODEX_HOME")
    return Path(d).expanduser() if d else Path.home() / ".codex"


def _cfg() -> Path:
    return home() / "config.toml"


def _registered_as(launcher: str) -> str | None:
    return entry_named(_cfg(), tomllib.loads, "mcp_servers", launcher)


def _registered(launcher: str) -> bool:
    return _registered_as(launcher) is not None


HARNESS = Harness(
    id="codex", name="Codex",
    detect=lambda: shutil.which("codex") is not None,
    registered=_registered,
    register_cmd=lambda launcher: ["codex", "mcp", "add", SERVER_NAME, "--", launcher],
    registered_as=_registered_as,
    unregister_cmd=lambda name: ["codex", "mcp", "remove", name],
    config_hint="~/.codex/config.toml ($CODEX_HOME/config.toml when set) → [mcp_servers.slopymemory] command = \"<launcher>\" (shared by the CLI, the IDE extension and ChatGPT Desktop's Codex mode)",
    instructions_file=lambda: home() / "AGENTS.md",
)
