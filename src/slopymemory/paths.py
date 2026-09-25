"""Where slopymemory keeps its state. Everything lives under one root: `SLOPYMEM_HOME` if set
(tests point it at a temp dir), else the parent of the venv this interpreter runs from when that parent is a
slopymemory home, else `~/.slopymemory`. Nothing is ever written into a project."""
from __future__ import annotations
import os
import sys
from pathlib import Path
from typing import Callable

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


def write_atomic_with_mode(target: Path, text: str, mode: int | None, *,
                            replace: Callable[[Path, Path], None] | None = None) -> None:
    """`write_atomic`, but for a caller that must preserve an EXACT permission mode across the write (a
    settings or instructions file a user may have locked down to 0600). `mode=None` means `target` does
    not exist yet, so the temp file gets the ordinary umask-reduced default for a new file — the same
    thing `write_atomic` would produce.

    Two things a plain "write .tmp, chmod, rename" sequence gets wrong, fixed here:

    - A `.tmp` left over from an earlier crash or kill carries ITS OWN mode. Opening it with plain
      O_CREAT reuses that mode (the mode argument to `os.open` only applies when the file is actually
      created), so a leftover 0644 temp file would land its mode on the target through the rename. The
      leftover is removed first, and the temp file is always created fresh.
    - `mode` is applied with `os.fchmod` on the open file descriptor BEFORE any content is written, not
      with a `chmod` call after the rename — `fchmod` is not reduced by the umask (unlike the mode
      argument to `os.open`), so the exact mode is never briefly the umask's wider one, and there is no
      window after the file holds content where a crash would leave it at the wrong mode.

    A failure at any step (opening, writing, or the rename) removes the temp file and leaves `target`
    completely untouched — never a stray `.tmp`, never a partial write.

    `replace` lets a caller substitute its own rename step (for testing, or to keep a bookkeeping hook
    around the rename); left out, `os.replace` is looked up fresh at call time, so patching `os.replace`
    itself is enough to inject a failure.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.unlink(missing_ok=True)
    do_replace = replace if replace is not None else os.replace
    open_mode = 0o600 if mode is not None else 0o666
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, open_mode)
    try:
        try:
            if mode is not None:
                os.fchmod(fd, mode)
        except BaseException:
            os.close(fd)
            raise
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        do_replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
