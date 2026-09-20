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
    return Path(os.environ.get("SLOPYMEM_PYTHON", home() / "venv" / "bin" / "python"))
