"""Where slopymemory keeps its state. Everything lives under one root: `SLOPYMEM_HOME` if set
(tests point it at a temp dir), else `~/.slopymemory`. Nothing is ever written into a project."""
from __future__ import annotations
import os
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("SLOPYMEM_HOME", Path.home() / ".slopymemory")).expanduser()


def registry_file() -> Path:
    return home() / "registry.toml"


def stores_dir() -> Path:
    return home() / "stores"


def logs_dir() -> Path:
    return home() / "logs"


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
