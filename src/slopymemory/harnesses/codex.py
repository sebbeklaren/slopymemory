import shutil, tomllib
from pathlib import Path
from . import Harness


def _cfg() -> Path:
    return Path.home() / ".codex" / "config.toml"


def _registered(launcher: str) -> bool:
    if not _cfg().exists():
        return False
    d = tomllib.loads(_cfg().read_text())
    return any(v.get("command") == launcher for v in d.get("mcp_servers", {}).values())


HARNESS = Harness(
    id="codex", name="Codex",
    detect=lambda: shutil.which("codex") is not None,
    registered=_registered,
    register_cmd=lambda launcher: ["codex", "mcp", "add", "memory", "--", launcher],
    config_hint="~/.codex/config.toml → [mcp_servers.memory] command = \"<launcher>\" (shared by the CLI, the IDE extension and ChatGPT Desktop's Codex mode)",
    instructions_file=lambda: Path.home() / ".codex" / "AGENTS.md",
)
