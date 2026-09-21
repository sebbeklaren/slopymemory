"""Where slopymemory keeps its state. Everything lives under one root: `SLOPYMEM_HOME` if set
(tests point it at a temp dir), else the parent of the venv this interpreter runs from when that parent is a
slopymemory home, else `~/.slopymemory`. Nothing is ever written into a project."""
from __future__ import annotations
import os
import sys
from pathlib import Path

DEFAULT_HOME = Path.home() / ".slopymemory"


def home() -> Path:
    return home_and_why()[0]


def home_and_why() -> tuple[Path, str]:
    """The root, and where it came from (the doctor prints both). `SLOPYMEM_HOME` when set. Otherwise the layout
    decides: `slopymem` and `slopymem-mcp` always run from `<home>/venv/bin`, and a launcher registered in a
    harness — or a fresh shell — carries no variable, so an install put elsewhere (`SLOPYMEM_HOME=/data/mem
    ./install.sh`) would otherwise split silently into a second tree under `~/.slopymemory`. The venv's parent is
    the home when it is `~/.slopymemory` itself or holds a registry or a stores dir; a `venv` under anything else
    (someone's own project venv) is not. Else `~/.slopymemory`."""
    told = os.environ.get("SLOPYMEM_HOME")
    if told:
        return Path(told).expanduser(), "SLOPYMEM_HOME"
    prefix = Path(sys.prefix)
    if prefix.name == "venv":
        parent = prefix.parent
        if parent == DEFAULT_HOME or (parent / "registry.toml").is_file() or (parent / "stores").is_dir():
            return parent, f"the parent of the venv this interpreter runs from, {prefix}; SLOPYMEM_HOME is unset"
    return DEFAULT_HOME, "the default; SLOPYMEM_HOME is unset and this interpreter is not under a <home>/venv"


def registry_file() -> Path:
    return home() / "registry.toml"


def stores_dir() -> Path:
    return home() / "stores"


def logs_dir() -> Path:
    return home() / "logs"


def embedded_pg_dir() -> Path:
    """The embedded Postgres cluster (PGDATA, its log, its unix socket) — the ONE place its location is defined:
    the server, the doctor, `init` and the space check all ask here."""
    return home() / "pg"


def venv_python() -> Path:
    """The interpreter the store servers run under (the slopymemory venv)."""
    return Path(os.environ.get("SLOPYMEM_PYTHON", home() / "venv" / "bin" / "python")).expanduser()


class ConfigError(RuntimeError):
    """A registry or store file that cannot be read as what it must be. Named, anchored, never a traceback:
    SETUP.md tells users to hand-edit these files, so a typo is an expected failure."""


def write_atomic(f: Path, text: str) -> None:
    """Write `<file>.tmp` and rename it over the file, so a crash mid-write never leaves a half-written
    registry or store.toml for every launcher on the machine to trip over."""
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, f)
